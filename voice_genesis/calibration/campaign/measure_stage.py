"""meter call stage: within-process 3 call + fresh-process 3 call
（IMPLEMENTATION_MAP_v1.md §6.4, 設計正本 §6, §8）。

`implementation_ref`（`candidates.registry.Candidate.implementation_ref`、
`<module>:<function>` 形式）を `importlib` で解決し `measure(signal, sr,
params) -> MeterOutput` として呼ぶ。within-process repeat はプロセス内で
直接呼ぶ（同一 bytes・同一 process — 設計正本 §6「同一 artifact 再読の
within-process repeat」）。fresh-process repeat は subprocess worker
`_measure_worker.py` を都度起動し、PCM ファイルを再読込してから呼ぶ。

**単一 writer 境界**（`provenance.py` 契約）: worker（subprocess）は結果を
stdout 経由で親プロセスへ返すのみで ledger には一切触れない。ledger
`meter_call` event の append は常に呼び出し元プロセス（本モジュールの
`run_measurement_for_instance`）が直列に行う。`[UNDERSPEC-CAL-D14]`
memo の「per-worker JSONL → 直列 append 集約」は、worker が中間 JSONL
ファイルを書いてから別途集約する 2 段構成を示唆するが、本実装は
`subprocess.run(capture_output=True)` の同期 stdout 捕捉で worker の結果を
直接受け取り、単一 writer（呼び出し元プロセス）がそのまま `Ledger.append()`
する 1 段構成を採る（中間ファイル I/O を経ずに同じ契約——「ledger に触れる
のは 1 writer のみ」——を満たす、より単純な実装）。`max_workers>1` の場合、
`ThreadPoolExecutor` で複数 fresh-process subprocess を並行起動する（各
thread は subprocess の完了待ちで I/O-bound であり GIL の制約を受けない。
ledger への append は future 完了順に main thread から直列に行う）。

**render bytes 検証**（finding #4）: `run_measurement_for_instance` は
measure する直前に render 済み PCM の実バイト列 sha256 を計算し、
`.sha256` sidecar と ledger 上の `render` event に pin された sha256 の
**両方**と照合する（`_verify_and_load_rendered_pcm`）。存在検査のみだった
旧実装は、render 後に何らかの経路で置き換えられた/破損した PCM を無言で
測定し得た — 一致しなければ `stale` ledger event を記帳した上で
`StaleRenderError` により fail-closed する（測定は一切行わない）。

**測定段の resume**（finding #10 巡 #9）: `run_measurement_for_instance` は
測定を始める前に、ledger 上に既に記帳済みの `meter_call` を
`(row_id, probe_index, candidate_id, repeat_kind, repeat_index)` キーで
再構成する（`_completed_meter_call_records`）。within 3 + fresh 3 が
ちょうど揃っていれば **再測定・再記帳せず**その結果を返す（二重追記の
禁止）。1 件も無ければ通常どおり測定する。それ以外（部分的にしか揃って
いない、または同一キーに複数件記帳されている）は `stop_event` ledger
event を記帳した上で `StaleMeasurementError` により fail-closed する
（測定・記帳は一切行わない — 中断状態からの自動再開は安全に決定できない
ため、明示的な運用判断に委ねる）。
"""

from __future__ import annotations

import importlib
import json
import math
import resource
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice_genesis.calibration.campaign import render_stage
from voice_genesis.calibration.campaign.caps import (
    CostCapExceededError,
    WorkerCpuSecondsInvalidError,
    charge_worker_attempts_before_raising,
    reported_cpu_seconds_or_none,
    save_cap_counters,
    validate_worker_cpu_seconds,
)
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.campaign.time_budget import SliceStatus, TimeBudget
from voice_genesis.calibration.candidates import adapter
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import Candidate
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.cost_caps import check as cost_caps_check
from voice_genesis.calibration.provenance import LedgerEntry
from voice_genesis.calibration.vocab import MissingReason

WITHIN_PROCESS_REPEATS = 3
FRESH_PROCESS_REPEATS = 3

#: `campaign.caps.CostCapExceededError` を本モジュール名前空間へ再公開する
#: （finding #1: render_stage/measure_stage で単一の cap 超過 error 型を
#: 共有する。既存呼び出し元は `measure_stage.CostCapExceededError` を参照
#: するため、import 元の変更後もこの属性名を保つ）。


def _process_cpu_seconds() -> float:
    """This process's own cumulative user+sys CPU seconds
    (`resource.getrusage(RUSAGE_SELF)`). Used by
    `run_measurement_for_instance` to charge the within-process 3 calls
    (round 14 finding #2) — those run serially in-process (never under
    `ThreadPoolExecutor`), so a before/after delta of this value is exact
    CPU time, not merely a wall-clock approximation of it."""
    ru_self = resource.getrusage(resource.RUSAGE_SELF)
    return ru_self.ru_utime + ru_self.ru_stime


def _children_cpu_seconds() -> float:
    """round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): this process's cumulative
    child user+sys CPU seconds (`resource.getrusage(RUSAGE_CHILDREN)`).
    Used as the parent-observed fallback for charging a fresh-process worker
    that failed post-spawn (timeout / nonzero exit / malformed JSON) and so
    never reported its own `cpu_seconds` — a before/after delta around one
    `subprocess.run()` call, taken by `_run_one_fresh_call`. On POSIX,
    `subprocess.run(timeout=...)` reaps the killed child (calls
    `process.wait()`) before re-raising `TimeoutExpired`, so this delta
    already reflects that child's accumulated CPU by the time it is taken."""
    ru_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru_children.ru_utime + ru_children.ru_stime


def resolve_measure_callable(implementation_ref: str) -> Callable[..., MeterOutput]:
    """`<module>:<function>` を解決する。`candidates.impl.<mod>` 形式は
    `voice_genesis.calibration.` を補って完全修飾する
    （`candidates.registry.Candidate.implementation_ref` docstring 参照）。
    M6 のような完全修飾 `voice_genesis.calibration.<mod>:<function>` 形式は
    そのまま解決する。"""
    module_path, sep, func_name = implementation_ref.partition(":")
    if not sep or not module_path or not func_name:
        raise ValueError(f"measure_stage: invalid implementation_ref {implementation_ref!r}")
    if module_path.startswith("candidates."):
        module_path = f"voice_genesis.calibration.{module_path}"
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


def pcm_bytes_to_signal(pcm_bytes: bytes, sr_hz: int) -> tuple[np.ndarray, int]:
    """render 済み PCM16 bytes を `[-1, 1]` 正規化 float64 signal へ変換する
    （`tests/test_generators.py` が使う `pcm.astype(np.float64) / 32767.0`
    規約と同一）。"""
    pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
    signal = pcm.astype(np.float64) / 32767.0
    return signal, sr_hz


def load_pcm_signal(pcm_path: str | Path, sr_hz: int) -> tuple[np.ndarray, int]:
    """render 済み PCM16 ファイルを読み、`[-1, 1]` 正規化 float64 signal へ
    変換する。fresh-process worker（`_measure_worker.py`）が自プロセス内で
    PCM を再読込する際に使う — within-process 経路は `run_measurement_for_instance`
    が事前に検証済みの bytes（`_verify_and_load_rendered_pcm`）を直接使い、
    本関数を経由しない。"""
    return pcm_bytes_to_signal(Path(pcm_path).read_bytes(), sr_hz)


class StaleRenderError(RuntimeError):
    """finding #4: render 済み PCM の実バイト列が `.sha256` sidecar または
    ledger に pin された `render` event の sha256 と一致しない（＝差し替え・
    破損・欠損）際の fail-closed error。測定は一切行わない。"""

    def __init__(self, row_id: str, probe_index: int, detail: str) -> None:
        self.row_id = row_id
        self.probe_index = probe_index
        super().__init__(
            f"measure_stage: stale render for row_id={row_id!r} probe_index={probe_index}: "
            f"{detail}"
        )


def _verify_and_load_rendered_pcm(
    campaign: FrozenCampaign, row_id: str, probe_index: int, sr_hz: int
) -> tuple[np.ndarray, int]:
    """finding #4: 測定の直前に render 済み PCM の実バイト列を読み、sha256 を
    計算して **`.sha256` sidecar と ledger に pin された `render` event の
    sha256 の両方**と照合する。一方でも欠落/不一致なら測定を一切行わず
    `StaleRenderError`（ledger `stale` event 付き）で fail-closed する
    （差し替えられた/破損した bytes を測定しない）。PCM ファイル自体が存在
    しない場合は（render が一度も行われていない、より基本的な状態）
    `FileNotFoundError` のまま送出する — こちらは「差し替え」ではなく
    「まだ render していない」なので既存呼び出し元の分岐と型を変えない。

    round 5 finding S4 (adopted, category ③, `[UNDERSPEC-CAL-D79]`): the
    PCM-vs-sidecar half of this check is now `render_stage._verify_pcm_
    sidecar()` — the same shared helper `render_stage._validate_skipped_
    resume_outcomes()` uses for its completing-invocation resume check, so
    the two can never again independently drift apart on which checks a
    "valid rendered PCM" requires (pre-fix, render_stage's own resume
    validator never read the sidecar at all)."""

    def _stale(detail: str) -> StaleRenderError:
        campaign.ledger.append(
            {
                "kind": "stale",
                "row_id": row_id,
                "probe_index": probe_index,
                "detail": detail,
            }
        )
        return StaleRenderError(row_id, probe_index, detail)

    # `_verify_pcm_sidecar()` raises `FileNotFoundError` uncaught (the more
    # basic "never rendered" state, distinct from "stale" — see its
    # docstring), matching this function's own pre-fix contract.
    pcm_bytes, actual_sha, detail = render_stage._verify_pcm_sidecar(campaign, row_id, probe_index)
    if detail is not None:
        raise _stale(detail)

    ledger_sha = render_stage._recorded_render_sha(campaign.ledger.entries, row_id, probe_index)
    if ledger_sha is None:
        raise _stale(
            f"no ledger 'render' event pinning sha256 for row_id={row_id!r} "
            f"probe_index={probe_index}"
        )
    if ledger_sha != actual_sha:
        raise _stale(
            f"pcm sha256={actual_sha!r} does not match ledger-pinned render "
            f"sha256={ledger_sha!r}"
        )

    return pcm_bytes_to_signal(pcm_bytes, sr_hz)


#: `[UNDERSPEC-CAL-D12]` `candidates.adapter.MeterOutput.values` は候補ごとに
#: 異なるフィールド集合を持つ（`candidates/adapter.py` docstring）。設計正本は
#: どのフィールドを selection/holdout criteria の主要スカラー値として使うか
#: を規定しないため、各 `algorithm_family`（`candidates.registry.Candidate.
#: algorithm_family`）が実際に返す `values` のキーを実装から機械的に転記した
#: 対応表を用いる（`candidates/impl/*.py` の `MeterOutput(values={...})` を
#: 直接参照）。formant 系（BURG_LPC/CEPSTRAL_POLES）は F1（`f1_hz`）を代表値
#: として採る（F2/F3 個別 gate は本 D2 infra の範囲外 — フルの multi-formant
#: margin 計算は E_use を formant index 別に持つ拡張が必要）。
PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY: Mapping[str, str] = {
    "B0_CURRENT_NACF_YIN": "f0_hz",
    "PYIN": "f0_hz",
    "B0_CURRENT_CEPSTRAL_CENTROID": "f1_est_hz",
    "CEPSTRAL_POLES": "f1_hz",
    "BURG_LPC": "f1_hz",
    "B0_CURRENT_HYBRID": "value",
    "HARMONIC_OLS": "tilt_db_per_oct",
    "HARMONIC_THEILSEN": "tilt_db_per_oct",
    "B0_CURRENT_HNR_APPROX": "hnr_db",
    "HNR_ACF": "hnr_db",
    "HARMONIC_RESIDUAL": "residual_fraction",
    "D4C_WORLD": "aperiodicity",
    "LOCAL_PROMINENCE": "center_hz",
    "WAVE_DISCONTINUITY": "magnitude",
    "SPECTRAL_FLUX": "magnitude",
}


#: round 27 ADOPT (1) (`[UNDERSPEC-CAL-D61]`): `algorithm_family` values whose
#: `measure()` reads an injected `f0_hz` from `params` (mechanically
#: transcribed from `candidates/impl/{aperiodicity,tilt_harmonic,
#: formant_cepstral}.py` — the only implementations that call
#: `params.get("f0_hz", ...)`; `F0_CONTROL` family candidates derive F0
#: themselves and are excluded). `run_measure_stage()` uses this set together
#: with `f0_unusable_instances` to make sure a candidate in this set is never
#: even called (never receives an injected `f0_hz` key at all) on an instance
#: whose selected-F0 aggregate failed the finite/strictly-positive guard in
#: `cli._build_f0_by_instance()` — see that function's docstring for the full
#: rationale (`formant_cepstral.py`'s own cutoff substitution on invalid F0
#: is why "not injected" alone is not enough; the call itself must not
#: happen).
F0_DEPENDENT_ALGORITHM_FAMILIES: frozenset[str] = frozenset(
    {
        "HARMONIC_OLS",
        "HARMONIC_THEILSEN",
        "HARMONIC_RESIDUAL",
        "D4C_WORLD",
        "CEPSTRAL_POLES",
    }
)


def primary_output_value(candidate: Candidate, output: MeterOutput) -> float | None:
    """`candidate.algorithm_family` の主要出力フィールド（`values` の 1 キー）
    を返す。missing/ineligible、または該当フィールド不在なら `None`。"""
    if output.missing_reason is not None or output.ineligible:
        return None
    field = PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY.get(candidate.algorithm_family)
    if field is None:
        return None
    value = output.values.get(field)
    return float(value) if value is not None else None


def usable_primary_instances(
    candidate: Candidate,
    records: Sequence[MeasurementRecord],
    *,
    within_fresh_tol: float = 0.0,
) -> frozenset[tuple[str, int]]:
    """UNDERSPEC-CAL-D76 ruling (4)（instance usability。`sweep_truth_
    investigation.md` Codex round 7 #1）: instance `(row_id, probe_index)`
    は、その候補について記録されている **全** record（repeat）が有限な
    primary 値を返し、かつ within 群と fresh 群がどちらも存在する場合は
    `candidates.adapter.within_fresh_process_mismatch()`（D67 一貫性規則）
    で不一致と判定されない場合にのみ usable とする。

    旧実装（`campaign/cli.py` `_run_c4` の直書き set comprehension）は
    instance の record を 1 件でも走査し、その 1 件の値が有限なら instance
    全体を usable とみなす existential collapse だった——6 repeat 中 1 件が
    たまたま有限であれば、残り 5 件が missing/不整合でも usable 側へ倒れて
    いた。本関数は record を `(row_id, probe_index)` でグループ化し、
    グループ内**全** record が有限・（within/fresh 両方揃っていれば）相互
    整合であることを要求する（1 件でも欠落/不整合なら instance 全体を
    不採用 — 呼び出し側は `OUTPUT_MISSING`（部分被覆）として扱う）。

    within `WITHIN_PROCESS_REPEATS` + fresh `FRESH_PROCESS_REPEATS` の
    閉集合が揃っているかどうか自体はここでは検査しない——実キャンペーンの
    ledger 記帳（`_completed_meter_call_records`/`StaleMeasurementError`）
    が「within 3 + fresh 3 がちょうど揃うか、測定を一切行わないか」を
    既に fail-closed で保証しているため、実測 record が 1 件でも
    `records` に現れる時点で完全な 6 repeat が揃っている（本関数が実際に
    塞ぐのは「揃った 6 repeat のうち一部だけが有限/整合」というケース）。
    軽量な単一 record/instance のテスト fixture（このモジュールの呼び出し
    元テスト群が広く使う慣用句）とも両立する。

    `algorithm_family` の主要出力 field が未知（`PRIMARY_OUTPUT_FIELD_BY_
    ALGORITHM_FAMILY` に無い）候補、または within/fresh の一方しか
    record が無い場合（軽量テスト fixture の典型形）は D67 一貫性検査を
    スキップする（比較対象が揃っていないため——`within_fresh_process_
    mismatch()` 自身は「一方が空なら不一致」という production 前提の
    `candidate_fail_filter_report()` 向け規約を持つが、本関数はその規約を
    継承しない）。
    """
    own = [r for r in records if r.candidate_id == candidate.candidate_id]
    by_instance: dict[tuple[str, int], list[MeasurementRecord]] = {}
    for r in own:
        by_instance.setdefault((r.row_id, r.probe_index), []).append(r)

    required_field = PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY.get(candidate.algorithm_family)

    usable: set[tuple[str, int]] = set()
    for key, group in by_instance.items():
        if not group:
            continue
        values_are_finite = True
        for r in group:
            value = primary_output_value(candidate, r.output)
            if value is None or not math.isfinite(value):
                values_are_finite = False
                break
        if not values_are_finite:
            continue
        if required_field is not None:
            within_values = [r.output.values for r in group if r.repeat_kind == "within"]
            fresh_values = [r.output.values for r in group if r.repeat_kind == "fresh"]
            if not within_values or not fresh_values:
                usable.add(key)
                continue  # no evidence to compare (abbreviated/lightweight record set)
            if adapter.within_fresh_process_mismatch(
                within_values, fresh_values, field_name=required_field, tol=within_fresh_tol
            ):
                continue  # D67: within/fresh disagree beyond tol -> not usable
        usable.add(key)
    return frozenset(usable)


#: round 26 ADOPT (1) (`[UNDERSPEC-CAL-D58]`): the 3 non-finite kinds a
#: `MeterOutput.values` field can take, sufficient (together with the
#: sanitized `None` standing in for the actual float) to losslessly
#: reconstruct the original `nan`/`inf`/`-inf` value — see
#: `meter_output_to_dict`/`meter_output_from_dict`.
def _nonfinite_kind(value: float) -> str:
    if math.isnan(value):
        return "nan"
    return "inf" if value > 0.0 else "-inf"


def meter_output_to_dict(output: MeterOutput) -> dict[str, object]:
    """round 26 ADOPT (1) (`[UNDERSPEC-CAL-D58]`) "Classify non-finite values
    before ledger serialization": a candidate that violates the adapter
    contract (`candidates/adapter.py` docstring: an unexplained field should
    be OMITTED, never returned as NaN/Inf) and returns a non-finite float in
    `output.values` without `missing_reason`/`ineligible` used to pass this
    conversion unchanged, carrying the raw `nan`/`inf`/`-inf` straight into
    the `meter_call` ledger payload — `canonical.canonical_json()` (which
    `provenance.Ledger.append()` uses for `entry_sha`) then rejects the FIRST
    such non-finite float with `ValueError` (`vgcal-canon/1` forbids
    NaN/Infinity), and because that happens inside `Ledger.append()`'s
    exclusive-lock write path (no bytes written on the raise) *before* this
    work unit's compute charge (`run_measurement_for_instance` charges
    AFTER its full `for record in records: ... ledger.append(...)` loop),
    the candidate's already-spent CPU (within-process + every fresh-process
    worker) went uncharged, no `meter_call` was durably recorded either, and
    every retry of that candidate/instance repeated the same wasted work.

    Fix: sanitize each non-finite entry of `values` to `None` here (valid
    JSON, passes `canonical_json` unchanged) and record which entries were
    sanitized — and which of the 3 non-finite kinds each was — in a sibling
    `nonfinite_kind` mapping (`{field_name: "nan" | "inf" | "-inf"}`, `None`
    when `values` has no non-finite entries so the payload shape for the
    common case is unchanged). This is enough for `meter_output_from_dict`
    to reconstruct the EXACT original float on read-back (a plain `None`
    could not distinguish `nan` from `inf` from `-inf`), so every downstream
    consumer that inspects `MeterOutput.values` — `candidates.adapter.
    unexplained_nonfinite()` (the fail filter this contract already
    designates for exactly this case), `is_finite_and_explained()`,
    `within_fresh_process_mismatch()`, `observables.two_stage_median()`/
    `u_rep()` (never coerce a non-finite repeat to 0 — see their own
    docstrings) — sees the identical `math.isnan`/`math.isinf` value it
    would have seen had `canonical_json` not existed, whether the record was
    freshly measured or reconstructed via `_completed_meter_call_records`
    resume. The `values`/`missing_reason`/`ineligible`/`ineligible_reason`
    keys and their meaning are otherwise unchanged — only the added
    `nonfinite_kind` key and `values`' use of `None` for a sanitized entry
    are new. `_measure_worker.py` calls this same function to build its
    stdout payload, so a NaN/Inf from a fresh-process worker is sanitized at
    the exact same boundary as one from a within-process call — both charge
    and record identically."""
    sanitized_values: dict[str, float | None] = {}
    nonfinite_kind: dict[str, str] = {}
    for key, value in output.values.items():
        if math.isfinite(value):
            sanitized_values[key] = value
        else:
            sanitized_values[key] = None
            nonfinite_kind[key] = _nonfinite_kind(value)
    return {
        "values": sanitized_values,
        "missing_reason": output.missing_reason.value if output.missing_reason is not None else None,
        "ineligible": output.ineligible,
        "ineligible_reason": output.ineligible_reason,
        "nonfinite_kind": nonfinite_kind or None,
    }


_NONFINITE_KIND_VALUES: Mapping[str, float] = {
    "nan": math.nan,
    "inf": math.inf,
    "-inf": -math.inf,
}


def meter_output_from_dict(d: Mapping[str, object]) -> MeterOutput:
    """round 26 ADOPT (1) (`[UNDERSPEC-CAL-D58]`): the inverse of
    `meter_output_to_dict`'s sanitization — a `values` entry of `None`
    (whether freshly parsed from a `meter_call`/fresh-worker payload or
    reconstructed on resume) is reconstructed back to the exact original
    `nan`/`inf`/`-inf` float using the paired `nonfinite_kind` entry, so
    `MeterOutput.values` is indistinguishable from what a freshly-measured
    candidate would have produced in-memory. A `None` entry with no matching
    `nonfinite_kind` (or an unrecognized kind string) is malformed — raises
    `ValueError` exactly like the pre-existing `float(v)` on a genuinely
    invalid entry did, so the existing `_run_one_fresh_call`/
    `_completed_meter_call_records` callers' fail-closed handling of a
    malformed payload is unchanged."""
    missing = d.get("missing_reason")
    values_raw = d.get("values") or {}
    if not isinstance(values_raw, Mapping):
        raise ValueError("measure_stage: MeterOutput payload 'values' must be an object")
    nonfinite_kind_raw = d.get("nonfinite_kind") or {}
    if not isinstance(nonfinite_kind_raw, Mapping):
        raise ValueError("measure_stage: MeterOutput payload 'nonfinite_kind' must be an object")
    values: dict[str, float] = {}
    for k, v in values_raw.items():
        key = str(k)
        if v is None:
            kind = nonfinite_kind_raw.get(key)
            if not isinstance(kind, str) or kind not in _NONFINITE_KIND_VALUES:
                raise ValueError(
                    f"measure_stage: MeterOutput payload has null values[{key!r}] "
                    f"without a matching nonfinite_kind entry (got {kind!r})"
                )
            values[key] = _NONFINITE_KIND_VALUES[kind]
        else:
            values[key] = float(v)
    ineligible_reason = d.get("ineligible_reason")
    return MeterOutput(
        values=values,
        missing_reason=MissingReason(missing) if isinstance(missing, str) else None,
        ineligible=bool(d.get("ineligible", False)),
        ineligible_reason=str(ineligible_reason) if isinstance(ineligible_reason, str) else None,
    )


@dataclass(frozen=True)
class MeasurementRecord:
    row_id: str
    probe_index: int
    candidate_id: str
    repeat_kind: str  # "within" | "fresh"
    repeat_index: int
    process_id: str
    output: MeterOutput


class StaleMeasurementError(RuntimeError):
    """finding #9: ledger 上の `meter_call` 記録が (row_id, probe_index,
    candidate_id) について部分的にしか揃っていない、または同一
    (repeat_kind, repeat_index) キーに複数件（重複・矛盾のいずれでも）
    記帳されている場合の fail-closed error。測定・記帳のいずれも一切行わ
    ない — 中断状態からの自動再開（欠けている分だけ測定して埋める）は、
    なぜ中断したかが分からない以上安全に決定できないため、明示的な運用
    判断（ledger 調査の上での手動復旧、または R1 の
    `--discard-partial-groups` による明示的な operator recovery）に委ねる。

    R1（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    `kind` は `"partial"`（一部の repeat key しか記帳されていない — 中断
    直後にありうる、`--discard-partial-groups` の対象）と `"duplicate"`
    （同一 repeat key に複数件 — 単一 writer 契約違反または矛盾する
    再測定。フラグの有無に関わらず常に fail-closed）を区別する。`kind ==
    "partial"` のとき `present_keys` はその時点で記帳済みだった
    `(repeat_kind, repeat_index)` キー集合（呼び出し元が discard event の
    `discarded_repeat_keys` を組み立てるのに使う）。

    Codex PR #345 round 6 finding #3 (adopted, category 3,
    `[UNDERSPEC-CAL-D79]`): `kind == "partial"` のとき
    `discarded_within_cpu_seconds` は present な記録（`by_key` の各キーに
    ちょうど 1 件ずつ）が共有する `within_cpu_seconds` 値（`_resolve_meter_
    group` が算出。同一 work unit の全記録は `run_measurement_for_instance`
    の単一計算から書かれるため本来同一値のはずだが、`max()` を取ることで
    型不正/欠落な個別レコードが混じっていても過大側に振れる — このモジュール
    既存の fail-closed 方向と同じ）。呼び出し元が `meter_call_group_
    discarded` event へそのまま転記し、`caps.cap_counters_from_ledger()` が
    それを exactly-once で課金する（詳細は同関数 docstring）。"""

    def __init__(
        self,
        row_id: str,
        probe_index: int,
        candidate_id: str,
        detail: str,
        *,
        kind: str,
        present_keys: frozenset[tuple[str, int]] = frozenset(),
        discarded_within_cpu_seconds: float = 0.0,
    ) -> None:
        self.row_id = row_id
        self.probe_index = probe_index
        self.candidate_id = candidate_id
        self.kind = kind
        self.present_keys = present_keys
        self.discarded_within_cpu_seconds = discarded_within_cpu_seconds
        super().__init__(
            f"measure_stage: stale meter_call state for row_id={row_id!r} "
            f"probe_index={probe_index} candidate_id={candidate_id!r}: {detail}"
        )


#: 1 instance × 1 candidate が完了したとみなす repeat キーの閉集合
#: （within `WITHIN_PROCESS_REPEATS` 件 + fresh `FRESH_PROCESS_REPEATS` 件）。
_EXPECTED_REPEAT_KEYS: frozenset[tuple[str, int]] = frozenset(
    {("within", i) for i in range(WITHIN_PROCESS_REPEATS)}
    | {("fresh", i) for i in range(FRESH_PROCESS_REPEATS)}
)

#: R1 の discard event（design memo `design_runner_robustness.md`,
#: `[UNDERSPEC-CAL-D79]`）が使う ledger `kind`。
METER_CALL_GROUP_DISCARDED_KIND = "meter_call_group_discarded"


def _partial_group_within_cpu_seconds(
    by_key: Mapping[tuple[str, int], Sequence[Mapping[str, object]]],
) -> float:
    """Codex PR #345 round 6 finding #3 (adopted, category 3,
    `[UNDERSPEC-CAL-D79]`): the shared `within_cpu_seconds` aggregate of a
    partial (not-yet-6-record) meter_call group, to be recorded on the
    `meter_call_group_discarded` event so a hard-killed process's within-
    process CPU is not lost (see `caps.cap_counters_from_ledger()`
    docstring for the exactly-once invariant). Every record of one work
    unit carries the *same* `within_cpu_seconds` value (computed once by
    `run_measurement_for_instance` before any of its 6 records are
    appended) — so this is a single shared aggregate, not a per-record
    quantity to sum (summing would multiply the same aggregate by however
    many of the 6 records happened to persist before the kill). `by_key`
    at the call site (post duplicate-check, pre-partial-raise) has exactly
    1 payload per present key, so `max()` over the validated per-record
    readings recovers that shared value while staying fail-closed
    (overcount-safe) against any individual record whose field is
    missing/non-finite/negative (0.0 contributed instead of aborting
    discard-recovery — the whole point of the operator-recovery escape
    hatch is robustness to whatever the interrupted process left behind)."""
    best = 0.0
    for entries in by_key.values():
        for payload in entries:
            value = payload.get("within_cpu_seconds")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not math.isfinite(value) or value < 0:
                continue
            if float(value) > best:
                best = float(value)
    return best


def _resolve_meter_group(
    by_key: Mapping[tuple[str, int], Sequence[Mapping[str, object]]],
    row_id: str,
    probe_index: int,
    candidate_id: str,
) -> list[MeasurementRecord] | None:
    """`by_key`（1 つの discard-epoch 内で観測された `(repeat_kind,
    repeat_index) -> [payload, ...]`）から `_completed_meter_call_records`/
    `MeterCallIndex.completed_records` 共通の判定ロジックを適用する。
    `MeterCallIndex`（R3）と素朴な 1 回スキャン（`_completed_meter_call_records`）
    の両方がこの同じ関数を経由することで両者の等価性を構造的に保証する。"""
    if not by_key:
        return None

    duplicate_keys = sorted(k for k, v in by_key.items() if len(v) > 1)
    if duplicate_keys:
        raise StaleMeasurementError(
            row_id,
            probe_index,
            candidate_id,
            f"duplicate ledger meter_call entries for repeat keys {duplicate_keys!r} "
            "(single-writer contract violated, or conflicting re-measurement)",
            kind="duplicate",
        )

    present_keys = frozenset(by_key.keys())
    if present_keys != _EXPECTED_REPEAT_KEYS:
        missing = sorted(_EXPECTED_REPEAT_KEYS - present_keys)
        unexpected = sorted(present_keys - _EXPECTED_REPEAT_KEYS)
        raise StaleMeasurementError(
            row_id,
            probe_index,
            candidate_id,
            f"incomplete meter_call state: missing={missing!r} unexpected={unexpected!r}",
            kind="partial",
            present_keys=present_keys,
            discarded_within_cpu_seconds=_partial_group_within_cpu_seconds(by_key),
        )

    records: list[MeasurementRecord] = []
    for (repeat_kind, repeat_index), entries in sorted(by_key.items()):
        payload = entries[0]
        process_id = (
            "within-process" if repeat_kind == "within" else f"fresh-process-{repeat_index}"
        )
        records.append(
            MeasurementRecord(
                row_id=row_id,
                probe_index=probe_index,
                candidate_id=candidate_id,
                repeat_kind=repeat_kind,
                repeat_index=repeat_index,
                process_id=process_id,
                output=meter_output_from_dict(payload),
            )
        )
    return records


class MeterCallIndex:
    """R3（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    ledger を 1 回だけ走査して `(row_id, probe_index, candidate_id)` ごとの
    `meter_call`/`meter_call_group_discarded` 状態をメモリ上に索引化する。

    `run_measure_stage()` は stage 呼び出しごとに `build()` で 1 回だけ全体を
    構築し、以降は `run_measurement_for_instance()` が `meter_call`/
    `meter_call_group_discarded` を記帳するたびに `observe_entry()` で
    その 1 件だけを反映する（`Ledger.entries` を再取得しない — このプロパティ
    は呼ぶたびに `self._entries` から新しい tuple を作り直すため、instance
    ごとに呼び直すと事実上 O(N) の rescan を繰り返すことになる。`append()`
    が返す `LedgerEntry` をそのまま渡すことで、1 stage 呼び出しあたり
    「初回の全走査 1 回 + 追記 1 件あたり O(1)」に抑える）。

    `completed_records()` は `_completed_meter_call_records()`（素朴な
    1 回スキャン版）と同じ判定ロジック（`_resolve_meter_group`）を共有し、
    かつ discard-reset ルール（R1: あるキーへの discard event 以降の
    `meter_call` のみを完全性・重複判定・scoring の対象とする — discard 前の
    記録は ledger 上に残るが、以降の完全性判定からは除外される）も同一に
    適用する。"""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, int, str], dict[tuple[str, int], list[Mapping[str, object]]]] = {}
        self._scanned = 0

    @classmethod
    def build(cls, ledger_entries: Sequence[LedgerEntry]) -> "MeterCallIndex":
        index = cls()
        index.update(ledger_entries)
        return index

    def observe_entry(self, entry: LedgerEntry) -> None:
        """`entry` 1 件だけを索引へ反映する（`Ledger.entries` を再取得しない
        増分更新経路 — `run_measurement_for_instance` が `campaign.ledger.
        append()` の戻り値をそのまま渡す）。"""
        payload = entry.payload
        if not isinstance(payload, Mapping):
            return
        kind = payload.get("kind")
        if kind == METER_CALL_GROUP_DISCARDED_KIND:
            outer_key = (
                payload.get("row_id"),
                payload.get("probe_index"),
                payload.get("candidate_id"),
            )
            # R1 reconstruction rule: discard event はそのキーの蓄積を
            # リセットする — discard 前の meter_call は ledger には残るが
            # （append-only）、以降の完全性・重複・scoring 判定からは除外
            # される。
            self._by_key.pop(outer_key, None)  # type: ignore[arg-type]
            return
        if kind != "meter_call":
            return
        outer_key = (payload.get("row_id"), payload.get("probe_index"), payload.get("candidate_id"))
        repeat_kind = payload.get("repeat_kind")
        repeat_index = payload.get("repeat_index")
        if repeat_kind not in ("within", "fresh") or not isinstance(repeat_index, int):
            return
        self._by_key.setdefault(outer_key, {}).setdefault(  # type: ignore[arg-type]
            (repeat_kind, repeat_index), []
        ).append(payload)

    def update(self, ledger_entries: Sequence[LedgerEntry]) -> None:
        """`ledger_entries` の末尾のうち、まだ索引化していない分だけを取り込む
        （初回 `build()` は `self._scanned == 0` のため全体を走査する）。"""
        for entry in ledger_entries[self._scanned :]:
            self.observe_entry(entry)
        self._scanned = len(ledger_entries)

    def completed_records(
        self, row_id: str, probe_index: int, candidate_id: str
    ) -> list[MeasurementRecord] | None:
        by_key = self._by_key.get((row_id, probe_index, candidate_id), {})
        return _resolve_meter_group(by_key, row_id, probe_index, candidate_id)

    def is_complete(self, row_id: str, probe_index: int, candidate_id: str) -> bool:
        """rehearsal 4 finding D (adopted, `[UNDERSPEC-CAL-D79]`): O(1)
        presence check — True iff `(row_id, probe_index, candidate_id)`
        already has a complete, non-duplicate within3+fresh3 `meter_call`
        group recorded (the same key-set criterion `completed_records()`
        uses), WITHOUT reconstructing a single `MeasurementRecord`
        (no `meter_output_from_dict()` call, no PCM read — this function
        touches only the small in-memory `dict[(repeat_kind, repeat_index),
        list[payload]]` this key already maps to).

        Codex PR #345 round 7 finding #2 (adopted, category ③,
        `[UNDERSPEC-CAL-D79]`): a duplicate-key group now raises
        `StaleMeasurementError(kind="duplicate")` immediately, the same as
        `completed_records()`/`_resolve_meter_group()` — it previously
        returned `False` here instead (treating the cell as merely
        "pending"), which let a resumed-slice caller's time-budget check
        (`run_measure_stage()`'s per-instance boundary check, evaluated
        strictly before any candidate is dispatched) exit with a clean
        `PARTIAL_SLICE` for an already-tiny/exhausted budget *before*
        `completed_records()`/`run_measurement_for_instance()` was ever
        reached for that cell — silently hiding a genuine single-writer
        contract violation behind an indefinitely repeatable "still
        pending" report instead of failing closed. Raising here instead
        means every caller — `run_measure_stage()`'s own `is_complete()`
        checks included — now surfaces the duplicate regardless of budget
        size; each such call site is responsible for its own `stop_event`
        ledger entry before letting the exception propagate, mirroring
        `run_measurement_for_instance()`'s existing duplicate handling."""
        by_key = self._by_key.get((row_id, probe_index, candidate_id))
        if not by_key:
            return False
        duplicate_keys = sorted(k for k, entries in by_key.items() if len(entries) > 1)
        if duplicate_keys:
            raise StaleMeasurementError(
                row_id,
                probe_index,
                candidate_id,
                f"duplicate ledger meter_call entries for repeat keys {duplicate_keys!r} "
                "(single-writer contract violated, or conflicting re-measurement)",
                kind="duplicate",
            )
        return frozenset(by_key.keys()) == _EXPECTED_REPEAT_KEYS


def _completed_meter_call_records(
    ledger_entries: Sequence[LedgerEntry],
    row_id: str,
    probe_index: int,
    candidate_id: str,
) -> list[MeasurementRecord] | None:
    """finding #9: ledger から (row_id, probe_index, candidate_id) の
    `meter_call` を `(repeat_kind, repeat_index)` キーで再構成する（1 回の
    素朴な全走査 — `MeterCallIndex.build(ledger_entries).completed_records(...)`
    の薄いラッパー。両者が同じ `_resolve_meter_group` を経由するため常に
    等価な結果を返す — R3 equivalence test 参照）。

    - 1 件も無ければ `None`（未着手 — 呼び出し元は通常どおり測定する）。
    - within `WITHIN_PROCESS_REPEATS` 件 + fresh `FRESH_PROCESS_REPEATS`
      件がちょうど 1 件ずつ揃っていれば、その内容から再構成した
      `MeasurementRecord` 列を返す（呼び出し元は再測定・再記帳しない —
      二重追記の禁止）。
    - それ以外（部分的にしか揃っていない、または同一キーに複数件記帳）は
      `StaleMeasurementError` を送出する（呼び出し元は ledger `stop_event`
      を記帳してから re-raise する。R1: `kind == "partial"` かつ
      `--discard-partial-groups` 指定時は discard event を記帳して
      フルグループを再測定する — `run_measurement_for_instance` 参照）。

    R1（design memo, `[UNDERSPEC-CAL-D79]`）: `kind ==
    METER_CALL_GROUP_DISCARDED_KIND` の event がある場合、そのキーについて
    その event **以降**に記帳された `meter_call` のみを対象とする
    （discard 前の記録は ledger には残るが、この関数の判定からは除外
    される — reconstruction rule）。
    """
    return MeterCallIndex.build(ledger_entries).completed_records(row_id, probe_index, candidate_id)


def _params_with_f0(candidate: Candidate, f0_hz: float | None) -> dict[str, object]:
    """F0 依存候補（D4C・harmonic-residual）へ選択済み F0 candidate の per-instance
    出力を注入する（memo §6.4: 「fixture の truth F0 ではなく c3a-f0-selection で
    選択された F0 candidate の実測出力を instance 単位で入力とする」）。"""
    params = dict(candidate.params_dict())
    if f0_hz is not None:
        params["f0_hz"] = float(f0_hz)
    return params


def run_within_process_calls(
    candidate: Candidate,
    signal: np.ndarray,
    sr: int,
    *,
    f0_hz: float | None,
    row_id: str,
    probe_index: int,
    repeats: int = WITHIN_PROCESS_REPEATS,
) -> list[MeasurementRecord]:
    """同一 process・同一 signal bytes 上で `repeats` 回直接呼ぶ。"""
    fn = resolve_measure_callable(candidate.implementation_ref)
    params = _params_with_f0(candidate, f0_hz)
    records: list[MeasurementRecord] = []
    for i in range(repeats):
        output = fn(signal, sr, params)
        records.append(
            MeasurementRecord(
                row_id=row_id,
                probe_index=probe_index,
                candidate_id=candidate.candidate_id,
                repeat_kind="within",
                repeat_index=i,
                process_id="within-process",
                output=output,
            )
        )
    return records


class _FreshWorkerFailure(RuntimeError):
    """round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): internal-only carrier.
    `_run_one_fresh_call` raises this instead of letting a post-spawn
    subprocess failure (timeout / nonzero exit / malformed JSON) propagate
    directly, so `run_fresh_process_calls` — which holds the
    ledger/`cap_counters`/`cost_caps` this needs to be charged against — can
    charge the attempted work (`caps.charge_worker_failure()`) before
    re-raising `cause` unchanged. Never crosses this module's public
    boundary (not in `__all__`, never returned/raised to callers of
    `run_fresh_process_calls`/`run_measurement_for_instance`)."""

    def __init__(self, failure_kind: str, compute: float, cause: BaseException) -> None:
        self.failure_kind = failure_kind
        self.compute = compute
        self.cause = cause
        super().__init__(f"measure_stage: fresh worker {failure_kind}: {cause}")


def _run_one_fresh_call(
    candidate_id: str, pcm_path: Path, sr: int, f0_hz: float | None, timeout_s: float
) -> tuple[MeterOutput, float]:
    """Returns `(output, cpu_seconds)`. `cpu_seconds` is the worker's own
    reported CPU time (round 14 finding #2) — validated here (fail-closed
    via `WorkerCpuSecondsInvalidError` on a missing/non-finite/negative
    value) so a caller running these concurrently under `ThreadPoolExecutor`
    sees the failure surface through `future.result()` exactly like any
    other error from this call.

    round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): a worker that times out,
    exits nonzero, or emits JSON that fails to parse raises `_FreshWorkerFailure`
    instead (caught and charged by `run_fresh_process_calls`) — carrying
    `failure_kind` and the compute to charge for the attempt: the worker's
    own reported `cpu_seconds` when a well-formed report is actually
    recoverable (a nonzero-exit worker's captured stdout, via
    `caps.reported_cpu_seconds_or_none()`), otherwise the parent-observed
    `RUSAGE_CHILDREN` delta around this call (`_children_cpu_seconds()`) — a
    timed-out worker never gets a report-recovery attempt (it was killed
    before it could reliably finish writing one; `caps.charge_worker_failure()`
    docstring).

    round 25 (`[UNDERSPEC-CAL-D57]`) finding "Charge parseable but invalid
    worker results": a worker that exits 0 with parseable JSON but an
    invalid `cpu_seconds` field, or a result shape `meter_output_from_dict()`
    cannot construct a `MeterOutput` from, now ALSO raises
    `_FreshWorkerFailure("malformed_output", ...)` instead of letting
    `WorkerCpuSecondsInvalidError`/`ValueError`/`TypeError` escape this
    function's `_FreshWorkerFailure` contract uncharged — the round 14
    finding #2 "stays uncharged" posture this supersedes meant such a worker
    could be retried indefinitely for free. The charged compute prefers the
    worker's own reported `cpu_seconds` when it validated fine (a
    result-shape failure AFTER a valid `cpu_seconds`) — only falling back to
    the `RUSAGE_CHILDREN` delta when `cpu_seconds` itself is the unusable
    field."""
    payload = {
        "candidate_id": candidate_id,
        "pcm_path": str(pcm_path),
        "sr_hz": sr,
        "f0_hz": f0_hz,
    }
    argv = [
        sys.executable,
        "-m",
        "voice_genesis.calibration.campaign._measure_worker",
        json.dumps(payload),
    ]
    children_t0 = _children_cpu_seconds()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, check=True)
    except subprocess.TimeoutExpired as exc:
        raise _FreshWorkerFailure(
            "timeout", _children_cpu_seconds() - children_t0, exc
        ) from exc
    except subprocess.CalledProcessError as exc:
        compute = reported_cpu_seconds_or_none(exc.stdout)
        if compute is None:
            compute = _children_cpu_seconds() - children_t0
        raise _FreshWorkerFailure("nonzero_exit", compute, exc) from exc
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise _FreshWorkerFailure(
            "malformed_output", _children_cpu_seconds() - children_t0, exc
        ) from exc
    if not isinstance(raw, Mapping):
        raise _FreshWorkerFailure(
            "malformed_output",
            _children_cpu_seconds() - children_t0,
            ValueError(f"measure_stage: fresh worker returned non-object JSON: {raw!r}"),
        )
    try:
        cpu_seconds = validate_worker_cpu_seconds(
            raw.get("cpu_seconds"),
            context=f"measure_stage: fresh-process worker for candidate_id={candidate_id!r}",
        )
    except WorkerCpuSecondsInvalidError as exc:
        # round 25 (`[UNDERSPEC-CAL-D57]`): cpu_seconds itself is the
        # unusable field here, so it cannot be the charge -- fall back to
        # the RUSAGE_CHILDREN delta.
        raise _FreshWorkerFailure(
            "malformed_output", _children_cpu_seconds() - children_t0, exc
        ) from exc
    try:
        output = meter_output_from_dict(raw)
    except (KeyError, TypeError, ValueError) as exc:
        # round 25 (`[UNDERSPEC-CAL-D57]`): cpu_seconds validated fine above
        # -- it is itself a usable figure, so charge it rather than the
        # coarser RUSAGE_CHILDREN delta.
        raise _FreshWorkerFailure("malformed_output", cpu_seconds, exc) from exc
    return output, cpu_seconds


def run_fresh_process_calls(
    candidate: Candidate,
    pcm_path: Path,
    sr: int,
    *,
    f0_hz: float | None,
    row_id: str,
    probe_index: int,
    campaign: FrozenCampaign,
    repeats: int = FRESH_PROCESS_REPEATS,
    timeout_s: float = 60.0,
    max_workers: int = 1,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> tuple[list[MeasurementRecord], float]:
    """`repeats` 回、subprocess worker (`_measure_worker.py`) を起動して測定
    する。`max_workers>1` なら `ThreadPoolExecutor` で並行起動する（結果は
    repeat_index 順に整列して返す — ledger への append 順を決定論的に保つ
    ため）。戻り値は `(records, cpu_seconds_total)` —
    `cpu_seconds_total` は各 worker が報告した `cpu_seconds` の合計
    （round 14 finding #2: 並行実行時の wall-clock 過小計上を避けるため、
    呼び出し元はこれを compute cap へ課金する。wall time ではない）。
    いずれかの worker が無効な `cpu_seconds` を報告した場合は
    `WorkerCpuSecondsInvalidError` を伝播する（測定結果ごと破棄 — fail
    closed）。

    round 24 ADOPT (1) (`[UNDERSPEC-CAL-D55]`): a worker that fails
    post-spawn (timeout / nonzero exit / malformed JSON — `_run_one_fresh_call`
    raising `_FreshWorkerFailure`) must not be free.

    round 25 (`[UNDERSPEC-CAL-D57]`) "Charge every worker attempt before
    propagating a repeat failure": this function now runs **every** attempt
    in the batch to completion regardless of `max_workers` — a sequential
    (`max_workers<=1`) repeat that fails no longer skips the remaining
    not-yet-started repeats, and a `ThreadPoolExecutor` batch (`max_workers>1`)
    no longer discards the results of futures the executor had already
    started (never cancelled either way) just because one future raised.
    Once every attempt's outcome is collected: if none failed, this is the
    ordinary success path (`cpu_seconds_total` is simply the sum, as
    before). If one or more failed, `caps.charge_worker_attempts_before_raising()`
    charges the WHOLE batch in one shot — every successful attempt's own
    `cpu_seconds` (its `MeasurementRecord` is discarded, never becomes a
    `meter_call` event, but the compute it already spent is charged via a
    `worker_attempts_discarded` ledger event) plus every failed attempt's
    charge (`worker_failed` ledger event each, same shape
    `charge_worker_failure()` used alone before this revision) — persists,
    cap-checks once, then re-raises the FIRST failed attempt's original
    cause (cap breach still takes priority via `CostCapExceededError`, same
    priority as every other charge-then-check call site in this package).
    `ThreadPoolExecutor` futures are read via `future.result()` from this
    function's own thread in submission order, so both the collection and
    the eventual charging stay single-threaded/deterministic."""
    outcomes: list[tuple[MeterOutput, float] | _FreshWorkerFailure] = []
    if max_workers <= 1:
        for _ in range(repeats):
            try:
                outcomes.append(
                    _run_one_fresh_call(candidate.candidate_id, pcm_path, sr, f0_hz, timeout_s)
                )
            except _FreshWorkerFailure as exc:
                outcomes.append(exc)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    _run_one_fresh_call, candidate.candidate_id, pcm_path, sr, f0_hz, timeout_s
                )
                for _ in range(repeats)
            ]
            for future in futures:
                try:
                    outcomes.append(future.result())
                except _FreshWorkerFailure as exc:
                    outcomes.append(exc)

    failures = [outcome for outcome in outcomes if isinstance(outcome, _FreshWorkerFailure)]
    if failures:
        successes = [
            cpu_seconds
            for outcome in outcomes
            if not isinstance(outcome, _FreshWorkerFailure)
            for _output, cpu_seconds in [outcome]
        ]
        charge_worker_attempts_before_raising(
            campaign.ledger,
            campaign.campaign_dir,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            stage="measure",
            row_id=row_id,
            probe_index=probe_index,
            candidate_id=candidate.candidate_id,
            successes=successes,
            failures=[(exc.failure_kind, exc.compute, exc.cause) for exc in failures],
        )

    # No failures reached this point (the branch above always raises) -- every
    # outcome is a real `(output, cpu_seconds)` pair.
    cpu_seconds_total = sum(cpu_seconds for _output, cpu_seconds in outcomes)  # type: ignore[misc]
    records = [
        MeasurementRecord(
            row_id=row_id,
            probe_index=probe_index,
            candidate_id=candidate.candidate_id,
            repeat_kind="fresh",
            repeat_index=i,
            process_id=f"fresh-process-{i}",
            output=output,
        )
        for i, (output, _cpu_seconds) in enumerate(outcomes)  # type: ignore[misc]
    ]
    return records, cpu_seconds_total


def run_measurement_for_instance(
    campaign: FrozenCampaign,
    candidate: Candidate,
    *,
    row_id: str,
    probe_index: int,
    sr_hz: int,
    f0_hz: float | None = None,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    max_workers: int = 1,
    discard_partial_groups: bool = False,
    stage: str = "unknown",
    meter_call_index: MeterCallIndex | None = None,
) -> list[MeasurementRecord]:
    """1 instance × 1 candidate = within 3 + fresh 3 の 6 call を実行し、
    ledger `meter_call` event を直列に記帳する。cap 超過を検出したら
    `stop_event` を記帳し `CostCapExceededError` で fail-closed する。

    測定前に render 済み PCM の実バイト列を `.sha256` sidecar + ledger の
    `render` event pin の両方と照合する（finding #4: 差し替えられた/破損した
    bytes を測定しない — `_verify_and_load_rendered_pcm` 参照）。不一致・
    pin 欠落は `StaleRenderError`、PCM ファイル自体が無ければ従来どおり
    `FileNotFoundError`。

    測定前に resume 判定も行う（finding #9）: この (row_id, probe_index,
    candidate) が既に ledger 上で within3+fresh3 ちょうど記帳済みなら、
    その結果をそのまま返し、再測定・再記帳は一切しない。部分的にしか
    揃っていない/同一キー重複があれば `stop_event` を記帳し
    `StaleMeasurementError` で fail-closed する（この場合も測定・記帳は
    一切行わない）。

    **compute 課金**（round 14 finding #2、round 16 finding #3 で改訂）:
    `--workers>1` では fresh-process 3 call が `ThreadPoolExecutor` で並行
    実行されるため、親プロセスの wall-clock 経過時間（`elapsed`）は実際に
    消費した CPU 秒数を過小計上する。fresh 側は各 worker が報告した
    `cpu_seconds`（`resource.getrusage` RUSAGE_SELF+RUSAGE_CHILDREN、
    `run_fresh_process_calls()` が合算して返す）を compute counter へ課金
    する。within 側は本関数自身の `resource.getrusage(RUSAGE_SELF)` 差分
    （within は並行化されず本プロセス内で直列実行されるため、この差分は
    正確な CPU 秒数そのもの）で `within_cpu_seconds` として引き続き測定
    するが、**compute counter へは課金しない**（round 16 finding #3、
    `[UNDERSPEC-CAL-D35]`）: within-process 3 call の CPU は `cli.py`
    `main()` が dispatch 全体の親プロセス CPU として既に
    `resource.getrusage(RUSAGE_SELF)` 差分で丸ごと課金・`stage_summary`
    event へ記帳している（round 15 finding #5, 1351736）ため、ここで
    さらに課金すると二重計上になる。`within_cpu_seconds` は ledger
    `meter_call` event へ informational として記帳する（`cpu_seconds`
    フィールドは従来どおり within+fresh の合計を保つ——`wall_seconds` と
    並ぶ informational な全体像であり、課金額そのものは `cap_counters.add()`
    へ渡す fresh 側の値のみ）。wall time はどちらの計上にも使わず、
    `wall_seconds` として ledger `meter_call` event へ informational にのみ
    記録する。fresh worker が無効な `cpu_seconds` を報告した場合は
    `WorkerCpuSecondsInvalidError` を stale/invalid work unit として
    `stop_event` 記帳の上 fail-closed する（`meter_call` 記帳は一切行わない
    —round 25 (`[UNDERSPEC-CAL-D57]`) 以降はこの attempt 自体は
    `worker_failed`/`malformed_output` として下記のとおり課金される。§下記参照）。

    **worker 失敗時の課金**（round 24 ADOPT (1) `[UNDERSPEC-CAL-D55]`、
    round 25 `[UNDERSPEC-CAL-D57]` で統一規則へ改訂）: fresh worker が
    起動後に timeout / nonzero exit / malformed JSON で失敗した場合
    （exit 0 かつ parseable JSON だが `cpu_seconds` 無効/結果形状不正——旧実装で
    無課金だった経路——も round 25 でこの扱いに合流した）は
    `run_fresh_process_calls()` が、同一 batch の**全** attempt（成功・失敗
    問わず全 `repeats` 回、`--workers>1` でも既に開始済みの future を
    途中キャンセルせず全員完走させる）の結果を集めてから、
    `caps.charge_worker_attempts_before_raising()` を経由して一括課金する:
    失敗した各 attempt は `worker_failed` event（`failure_kind` 別）、
    同一 batch 内で成功したが結果を破棄する attempt（他の repeat が失敗した
    ため）は `worker_attempts_discarded` event（`discarded_success_attempts`
    に各自の `cpu_seconds`）として記帳・課金・cap 再検査してから、
    batch 内で最初に失敗した attempt の元の例外を再送出する — 旧実装
    （round 24 時点）は失敗した 1 attempt のみを課金し、既に完了していた
    兄弟 attempt（sequential では先行する成功、concurrent では他の future）
    を無課金で破棄していた。

    **R1 — 明示的な operator recovery**（design memo
    `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）: `discard_
    partial_groups=True` かつ resume 判定が `kind == "partial"`（一部の
    repeat key しか記帳されていない — duplicate ではない）の
    `StaleMeasurementError` のとき、`stop_event`/re-raise の代わりに
    `meter_call_group_discarded` event を 1 件（`{row_id, probe_index,
    candidate_id, discarded_repeat_keys, discarded_count, reason:
    "operator_discard_partial_group_after_interrupt", stage}`）記帳してから
    フルグループ（within3+fresh3 全 6 call）を測定・記帳する（通常の
    未着手経路と同じ下り）。`kind == "duplicate"` は `discard_partial_groups`
    の有無に関わらず常に `stop_event`+re-raise する（duplicate は
    "operator が中断後に discard してよい" 対象ではない — 単一 writer
    契約違反や矛盾する再測定の兆候であり、手動調査に委ねる）。

    Codex PR #345 round 6 finding #3 (adopted, category 3,
    `[UNDERSPEC-CAL-D79]`): the discard event above also carries an
    additional field `discarded_within_cpu_seconds` (memo §6.5 lists the
    payload keys exhaustively as of round 5 — this is a new optional field
    added here, not yet reflected there) — `exc.discarded_within_cpu_
    seconds` (see `StaleMeasurementError`/`_partial_group_within_cpu_
    seconds`). Rationale: a process hard-killed (SIGKILL/OOM) mid-append
    never reaches `cli.py` `main()`'s `finally` block, so it never emits
    the `stage_summary`/`slice_summary` event that `within_cpu_seconds`
    normally rides on (round 16 finding #3 — within-process CPU is
    deliberately excluded from the per-`meter_call` compute charge on the
    assumption a parent-CPU summary event covers it). For a discarded
    partial group that assumption can be false, silently losing that CPU
    from the compute cap forever. See `caps.cap_counters_from_ledger()`
    docstring for the exactly-once charging invariant.

    **R3 — 1 stage 呼び出し 1 スキャン**: `meter_call_index` が渡されれば
    resume 判定・discard reset をそれ経由で行い（`campaign.ledger.entries`
    を再取得しない）、新規に記帳した `meter_call`/`meter_call_group_
    discarded` event も同じ index へ即座に反映する（`Ledger.append()` の
    戻り値をそのまま渡す — O(1) の増分更新）。`None`（既定）なら従来どおり
    `_completed_meter_call_records()` が `campaign.ledger.entries` を
    1 回スキャンする（`run_measurement_for_instance` を単体で呼ぶ既存の
    呼び出し元・テストの挙動は変わらない）。"""
    try:
        resumed = (
            meter_call_index.completed_records(row_id, probe_index, candidate.candidate_id)
            if meter_call_index is not None
            else _completed_meter_call_records(
                campaign.ledger.entries, row_id, probe_index, candidate.candidate_id
            )
        )
    except StaleMeasurementError as exc:
        if discard_partial_groups and exc.kind == "partial":
            discarded_repeat_keys = [list(k) for k in sorted(exc.present_keys)]
            discard_entry = campaign.ledger.append(
                {
                    "kind": METER_CALL_GROUP_DISCARDED_KIND,
                    "row_id": row_id,
                    "probe_index": probe_index,
                    "candidate_id": candidate.candidate_id,
                    "discarded_repeat_keys": discarded_repeat_keys,
                    "discarded_count": len(discarded_repeat_keys),
                    "reason": "operator_discard_partial_group_after_interrupt",
                    "stage": stage,
                    # round 6 finding #3: shared within-process CPU aggregate
                    # of the discarded group's present records (see
                    # `StaleMeasurementError`/`_partial_group_within_cpu_
                    # seconds` docstrings) — charged exactly once by
                    # `caps.cap_counters_from_ledger()`.
                    "discarded_within_cpu_seconds": exc.discarded_within_cpu_seconds,
                }
            )
            if meter_call_index is not None:
                meter_call_index.observe_entry(discard_entry)
            resumed = None
        else:
            campaign.ledger.append(
                {
                    "kind": "stop_event",
                    "reason": "STALE_MEASUREMENT_STATE",
                    "row_id": row_id,
                    "probe_index": probe_index,
                    "candidate_id": candidate.candidate_id,
                    "detail": str(exc),
                }
            )
            raise
    if resumed is not None:
        return resumed

    pcm_path = campaign.renders_dir / row_id / f"{probe_index}.pcm"
    signal, sr = _verify_and_load_rendered_pcm(campaign, row_id, probe_index, sr_hz)

    wall_t0 = time.perf_counter()
    within_cpu_t0 = _process_cpu_seconds()
    within = run_within_process_calls(
        candidate, signal, sr, f0_hz=f0_hz, row_id=row_id, probe_index=probe_index
    )
    within_cpu_seconds = _process_cpu_seconds() - within_cpu_t0
    try:
        fresh, fresh_cpu_seconds = run_fresh_process_calls(
            candidate,
            pcm_path,
            sr,
            f0_hz=f0_hz,
            row_id=row_id,
            probe_index=probe_index,
            campaign=campaign,
            max_workers=max_workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
        )
    except WorkerCpuSecondsInvalidError as exc:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": "INVALID_MEASURE_WORKER_CPU_SECONDS",
                "row_id": row_id,
                "probe_index": probe_index,
                "candidate_id": candidate.candidate_id,
                "detail": str(exc),
            }
        )
        raise
    wall_seconds = time.perf_counter() - wall_t0
    # round 16 finding #3 (`[UNDERSPEC-CAL-D35]`): `compute_seconds` here
    # stays the informational within+fresh total recorded on the
    # `cpu_seconds` ledger field below (unchanged meaning); the *charged*
    # compute (`cap_counters.add()` further down) uses `fresh_cpu_seconds`
    # alone — within-process CPU is already covered by the parent
    # RUSAGE_SELF stage charge (`cli.py` `main()`'s `stage_summary` event,
    # round 15 finding #5 / 1351736) and charging it again here would
    # double-count it against the frozen compute cap.
    compute_seconds = within_cpu_seconds + fresh_cpu_seconds

    records = within + fresh
    storage_bytes = 0
    for record in records:
        payload = {
            "kind": "meter_call",
            "row_id": row_id,
            "probe_index": probe_index,
            "candidate_id": candidate.candidate_id,
            "repeat_kind": record.repeat_kind,
            "repeat_index": record.repeat_index,
            # round 14 finding #2: instance x candidate work-unit aggregate
            # (same value on every one of the 6 records — this is a per-work-
            # unit figure, not a per-call one; matches the existing
            # per-work-unit granularity of `cap_counters.add()` below).
            "wall_seconds": wall_seconds,
            "cpu_seconds": compute_seconds,
            # round 16 finding #3 (`[UNDERSPEC-CAL-D35]`): the within-process
            # portion of `cpu_seconds` above, broken out purely as
            # informational provenance (not charged — see the compute
            # charging docstring paragraph above and the `cap_counters.add()`
            # call below, which uses `fresh_cpu_seconds` only).
            "within_cpu_seconds": within_cpu_seconds,
            **meter_output_to_dict(record.output),
        }
        # round 15 finding #3 (`[UNDERSPEC-CAL-D31]`): unlike `cpu_seconds`
        # above, this is the record's *own* individual serialized size (not
        # a repeated per-work-unit aggregate) — computed from the payload
        # before this field is added, so it does not self-reference its own
        # eventual size. `campaign.caps.cap_counters_from_ledger()` sums it
        # across all 6 records without dedup (genuinely additive per
        # record), reproducing the same total charged to `storage_used`
        # below.
        record_bytes = len(json.dumps(payload).encode("utf-8"))
        payload["storage_bytes"] = record_bytes
        appended_entry = campaign.ledger.append(payload)
        if meter_call_index is not None:
            meter_call_index.observe_entry(appended_entry)
        storage_bytes += record_bytes

    if cap_counters is not None:
        # round 13 finding #3: this measurement (1 instance x 1 candidate =
        # within3 + fresh3) is 1 budget work unit — same accounting rule and
        # granularity as render_stage.render_instance().
        budget_charge = cost_caps.budget_charge_per_work_unit() if cost_caps is not None else 0.0
        # round 16 finding #3 (`[UNDERSPEC-CAL-D35]`): fresh-worker CPU only
        # — `within_cpu_seconds` is deliberately excluded here (see the
        # docstring paragraph above); it is already charged once, in
        # `cli.py` `main()`'s parent RUSAGE_SELF `stage_summary` charge.
        cap_counters.add(compute=fresh_cpu_seconds, storage=storage_bytes, budget=budget_charge)
        # Persist immediately (finding #1: counters must survive across
        # subcommands) — before the breach check, so a unit's consumption is
        # never lost even when this same unit trips a fail-closed exit below.
        save_cap_counters(campaign.campaign_dir, cap_counters)
        if cost_caps is not None:
            decision = cost_caps_check(cap_counters, cost_caps)
            if decision is not None:
                campaign.ledger.append(decision.event_payload)
                raise CostCapExceededError(decision.detail)
    return records


def _instance_has_pending_candidate(
    row_id: str,
    probe_index: int,
    candidates: Sequence[Candidate],
    meter_call_index: MeterCallIndex,
    f0_unusable_instances: frozenset[tuple[str, int]],
) -> bool:
    """rehearsal 4 finding G helper: True iff at least one of `candidates`
    still needs measurement for this instance — i.e. is neither
    permanently resolved (the `f0_unusable_instances` /
    `F0_DEPENDENT_ALGORITHM_FAMILIES` skip `run_measure_stage()`'s own loop
    applies) nor already `MeterCallIndex.is_complete()` in the ledger.
    O(1) per candidate — index lookups only, no PCM read, no record
    reconstruction — so callers may cheaply evaluate this for every
    instance in a stage's full instance set, including ones a
    budget-bounded dispatch loop never reached this invocation.

    round 7 finding #2 (`[UNDERSPEC-CAL-D79]`): `MeterCallIndex.
    is_complete()` now raises `StaleMeasurementError(kind="duplicate")`
    instead of returning `False` for a duplicate-key cell — this function
    does not catch it, so it propagates unchanged to whichever
    `run_measure_stage()` call site invoked this helper (both wrap the
    call in `try`/`except StaleMeasurementError` to log a `stop_event`
    before re-raising; see `_append_stale_measurement_stop_event()`)."""
    for candidate in candidates:
        if (row_id, probe_index) in f0_unusable_instances and (
            candidate.algorithm_family in F0_DEPENDENT_ALGORITHM_FAMILIES
        ):
            continue
        if not meter_call_index.is_complete(row_id, probe_index, candidate.candidate_id):
            return True
    return False


def _append_stale_measurement_stop_event(
    campaign: FrozenCampaign, exc: StaleMeasurementError
) -> None:
    """Codex PR #345 round 7 finding #2 (adopted, category ③,
    `[UNDERSPEC-CAL-D79]`): shared `stop_event` shape for a
    `StaleMeasurementError` raised by `MeterCallIndex.is_complete()` (see
    its docstring) at one of `run_measure_stage()`'s own `is_complete()`
    call sites — same payload shape `run_measurement_for_instance()`'s
    pre-existing "duplicate"/"partial" branches already use, so a
    duplicate ledger state is always logged exactly once before the
    exception propagates out of `run_measure_stage()`. Deliberately not
    reused inside `run_measurement_for_instance()` itself: that function
    logs its own `stop_event` from `completed_records()`'s raise
    independently, and routing both through one shared call site risks
    double-logging the same failure key if a future change ever let both
    raise for the same cell within one call."""
    campaign.ledger.append(
        {
            "kind": "stop_event",
            "reason": "STALE_MEASUREMENT_STATE",
            "row_id": exc.row_id,
            "probe_index": exc.probe_index,
            "candidate_id": exc.candidate_id,
            "detail": str(exc),
        }
    )


def run_measure_stage(
    campaign: FrozenCampaign,
    instances: Sequence[tuple[str, int]],
    candidates: Sequence[Candidate],
    *,
    sr_by_row: Mapping[str, int],
    f0_by_instance: Mapping[tuple[str, int], float] | None = None,
    f0_unusable_instances: frozenset[tuple[str, int]] = frozenset(),
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    max_workers: int = 1,
    missing_reason: str = "F0_UNUSABLE",
    discard_partial_groups: bool = False,
    stage: str = "unknown",
    time_budget: TimeBudget | None = None,
) -> list[MeasurementRecord] | tuple[list[MeasurementRecord], SliceStatus]:
    """`instances × candidates` の全 work unit を決定論的順序（instance →
    candidate_id 昇順）で処理する。

    R3（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）:
    呼び出しの先頭で `MeterCallIndex` を 1 回だけ構築し（`campaign.ledger.
    entries` への唯一のアクセス）、以降は全 `run_measurement_for_instance()`
    呼び出しへ同じ index を渡す（各呼び出しは新規記帳 1 件ごとに index を
    O(1) で更新するのみで ledger を再スキャンしない）。`discard_partial_
    groups`/`stage`（R1）も素通しする。

    R2（`[UNDERSPEC-CAL-D79]`）: `time_budget` が渡されれば instance 境界
    （`(row_id, probe_index)` 1 件 = 全 candidate の測定が揃って初めて
    「完了」）で予算超過を検査し、超過していれば以降の instance を一切
    dispatch せず戻る（既に dispatch 済みの instance は最後まで完走する
    — `for candidate in candidates` の内側では検査しない）。この場合、
    戻り値は `(records, SliceStatus)` の 2-tuple になる（`time_budget`
    が `None`（既定）のときは従来どおり `records` 単体を返す — 呼び出し元
    の挙動・シグネチャは不変）。

    Codex PR #345 round 4 finding #3（adopted, category ③,
    `[UNDERSPEC-CAL-D79]`, `render_stage.run_render_stage()` と同一修正）:
    上記の予算境界検査は `_instance_has_pending_candidate()` で「真に
    pending な instance」と判定された場合のみ発動する。既に全 candidate が
    `is_complete()`（または F0-unusable による恒久解決）済みの instance は
    budget に関わらず内側の `for candidate` ループへ進む（そこでの処理は
    O(1) skip のみで dispatch は発生しない）。これにより、直前の呼び出しが
    最終 instance の測定完了後・stage 完了記帳前に中断され、かつ `Meter
    CallIndex.build()`（全 ledger スキャン）自体が time_budget を使い切る
    場合でも、完了済み instance の skip が budget 切れを理由にブロック
    されず、stage は完了/遷移へ進める。

    rehearsal 4 finding D/G（adopted, `[UNDERSPEC-CAL-D79]`。c3b parent CPU
    71.7s→78.9s→84.3s→88.3s の growth 実測）: 既に `MeterCallIndex.
    is_complete()` で完了済みと分かるセルは `run_measurement_for_instance()`
    を一切呼ばず（PCM 読込なし・`MeasurementRecord` 再構成なし）O(1) で
    skip する — 再構成が要る呼び出し（`time_budget=None` の単発実行、または
    slice が完走した completing invocation）だけ、skip したセル全体を
    ループ後の 1 パスでまとめて再構成する（`_rebuild_skipped_records`）。
    `instances_remaining` も `total_instances - instances_completed_this_run`
    ではなく、`is_complete()` を使った index ベースの 1 パス
    （`_instance_has_pending_candidate`）で「この呼び出し後の真の未完了数」
    を数え直す — budget が最初の instance の dispatch 前に尽きた場合でも
    （`instances_completed_this_run == 0`）、過去の呼び出しで完了済みの
    instance を無視した過大な `instances_remaining` を報告しない
    （非増加が保証される）。

    round 29 ADOPT (`[UNDERSPEC-CAL-D65]`): `missing_reason` names the
    `measurement_missing` event's `reason` field for every cell this call
    skips via `f0_unusable_instances` (default `"F0_UNUSABLE"`, the D61/D63/
    D64 per-instance-rejection reason). Callers pass `"F0_SELECTION_FAILED"`
    when `f0_unusable_instances` covers every instance because C3a itself
    recorded no F0 winner (`SELECTION_FAILED_CLOSED`) — a distinct cause from
    a per-instance non-finite/missing F0 aggregate, kept distinguishable in
    the ledger.

    round 27 ADOPT (1) (`[UNDERSPEC-CAL-D61]`): `f0_unusable_instances`
    names instances whose selected-F0 per-instance aggregate was rejected by
    `cli._build_f0_by_instance()`'s finite/strictly-positive guard. Any
    candidate whose `algorithm_family` is in `F0_DEPENDENT_ALGORITHM_FAMILIES`
    is skipped entirely (not called, no `MeasurementRecord` produced) for
    those instances — the same "absent, as if unmeasured" treatment
    `build_candidate_criteria()`/`candidate_fail_filter_report()` already
    give any instance this candidate has no record for (their coverage/N_pos
    accounting is denominator-driven from `records`, so an absent instance
    is excluded rather than counted as a measured-but-missing failure).
    Non-F0-dependent candidates and F0-usable instances are unaffected.

    round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`) "Count rejected F0 instances
    as missing coverage": the skip above previously left *no* record of any
    kind behind — not a `MeasurementRecord`, not a ledger event — for the
    skipped `(row_id, probe_index, candidate_id)` cell. That silent gap is
    exactly what let a candidate win selection on a reduced subset with no
    missing-rate penalty: `build_candidate_criteria()`/`candidate_fail_
    filter_report()` compute every denominator and error vector purely from
    `records`, so a cell this function never produced a record for is
    invisible to them rather than counted as a measured-but-missing
    failure. Every skipped cell is now also recorded as an explicit,
    durable `measurement_missing` ledger event (`reason: "F0_UNUSABLE"`,
    `cells: [[row_id, probe_index, candidate_id], ...]`, batched — one event
    per call, mirroring `cli._build_f0_by_instance()`'s `f0_injection_
    rejected` event) purely for provenance/audit: this ledger `kind` is
    distinct from `meter_call`, so `_completed_meter_call_records()`/
    `cli._reusable_f0_values_by_process()` never see it and resume/ledger-
    reconstruction behavior for actual measurements is unaffected. The
    *eligibility* consequence is `selection_stage.candidate_fail_filter_
    report()`'s new `coverage_incomplete` filter (`[UNDERSPEC-CAL-D64]`),
    which reads the absence of a `MeasurementRecord` against the frozen
    expected-instance set directly — this ledger event does not feed it.
    Idempotent across resume: a cell already recorded as missing by a prior
    invocation is not re-appended."""
    f0_map = f0_by_instance or {}
    already_missing: set[tuple[str, int, str]] = set()
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if not isinstance(payload, Mapping) or payload.get("kind") != "measurement_missing":
            continue
        for cell in payload.get("cells", []):
            if isinstance(cell, (list, tuple)) and len(cell) == 3:
                already_missing.add((cell[0], cell[1], cell[2]))

    # R3: single ledger scan for this whole invocation — every
    # `run_measurement_for_instance()` call below shares this one index
    # instead of each re-scanning `campaign.ledger.entries` itself.
    meter_call_index = MeterCallIndex.build(campaign.ledger.entries)

    sorted_instances = sorted(instances)
    all_records: list[MeasurementRecord] = []
    newly_missing: list[tuple[str, int, str]] = []
    # rehearsal 4 finding D (adopted, `[UNDERSPEC-CAL-D79]`): cells this
    # invocation's O(1) `is_complete()` fast path skipped without
    # reconstructing a `MeasurementRecord` — reconstructed, once, in a
    # single pass after the loop below, ONLY when this call's return value
    # will actually be used (see the `time_budget is None`/`completed_all`
    # branches at the bottom). A `PARTIAL_SLICE` return never rebuilds
    # these: every caller of a sliced `run_measure_stage()` discards
    # `records` on a non-terminal slice (`cli._partial_slice_report()`/
    # `baseline_stage`'s `{"slice_status": ...}` early return), so paying
    # to reconstruct a growing already-complete prefix on every resumed
    # slice — the exact cost rehearsal 4 measured growing 71.7s->88.3s
    # across 4 constant-new-work c3b slices — bought nothing.
    skipped_complete_cells: list[tuple[str, int, str]] = []
    instances_completed_this_run = 0
    completed_all = True
    for row_id, probe_index in sorted_instances:
        # Codex PR #345 round 4 finding #3 (adopted, category ③,
        # `[UNDERSPEC-CAL-D79]`, mirrors `render_stage.run_render_stage()`'s
        # identical fix): the budget check below must only block dispatch
        # of a GENUINELY pending instance. Pre-fix ordering checked
        # `time_budget.expired()` before consulting `meter_call_index` at
        # all — so if building `MeterCallIndex.build()` above (a full
        # ledger scan) itself consumed the whole `time_budget`, the very
        # first instance would trip the budget check and `break`
        # immediately, even when every instance's every candidate was
        # already `is_complete()` (or permanently resolved as F0-unusable)
        # and only the stage's own completion/transition was left.
        # Repeated resumes with the same small budget would then never
        # finish. `_instance_has_pending_candidate()` (already used below
        # for `instances_remaining`) is the same O(1) index-only check —
        # use it here first: an instance with nothing pending falls through
        # to the `for candidate` loop, which then only does the cheap
        # `is_complete()`/F0-unusable skip path (no PCM read, no dispatch),
        # budget-independent, same as `render_stage`'s already-completed
        # unit skip.
        # round 7 finding #2 (`[UNDERSPEC-CAL-D79]`): `is_complete()` (via
        # this helper) now raises `StaleMeasurementError(kind="duplicate")`
        # for a duplicate-key cell instead of returning `False` — surfaced
        # HERE, before the budget check below, so a duplicate is never
        # masked as merely "pending" and hidden behind a repeatable clean
        # `PARTIAL_SLICE` exit regardless of how small `time_budget` is.
        try:
            has_pending = _instance_has_pending_candidate(
                row_id, probe_index, candidates, meter_call_index, f0_unusable_instances
            )
        except StaleMeasurementError as exc:
            _append_stale_measurement_stop_event(campaign, exc)
            raise
        # R2 instance boundary: checked before starting a NEW (row_id,
        # probe_index) group that actually has pending work — an instance
        # already in flight (its own `for candidate` loop below) always
        # runs to completion.
        if has_pending and time_budget is not None and time_budget.expired():
            completed_all = False
            break
        for candidate in sorted(candidates, key=lambda c: c.candidate_id):
            if (row_id, probe_index) in f0_unusable_instances and (
                candidate.algorithm_family in F0_DEPENDENT_ALGORITHM_FAMILIES
            ):
                cell = (row_id, probe_index, candidate.candidate_id)
                if cell not in already_missing:
                    newly_missing.append(cell)
                continue
            candidate_id = candidate.candidate_id
            # round 7 finding #2: same duplicate-surfacing rule as the
            # `has_pending` check above, at this cell's own `is_complete()`
            # call.
            try:
                cell_is_complete = meter_call_index.is_complete(row_id, probe_index, candidate_id)
            except StaleMeasurementError as exc:
                _append_stale_measurement_stop_event(campaign, exc)
                raise
            if cell_is_complete:
                # Finding D: O(1) skip — no PCM read, no `MeasurementRecord`
                # reconstruction here (contrast the pre-fix behavior, which
                # called `run_measurement_for_instance()` unconditionally
                # and paid its full `meter_output_from_dict()` reconstruction
                # cost for every already-complete cell on every resumed
                # slice).
                skipped_complete_cells.append((row_id, probe_index, candidate_id))
                continue
            f0_hz = f0_map.get((row_id, probe_index))
            all_records.extend(
                run_measurement_for_instance(
                    campaign,
                    candidate,
                    row_id=row_id,
                    probe_index=probe_index,
                    sr_hz=sr_by_row[row_id],
                    f0_hz=f0_hz,
                    cap_counters=cap_counters,
                    cost_caps=cost_caps,
                    max_workers=max_workers,
                    discard_partial_groups=discard_partial_groups,
                    stage=stage,
                    meter_call_index=meter_call_index,
                )
            )
        if has_pending:
            # Codex PR #345 round 5 finding S2 (adopted, category ②,
            # `[UNDERSPEC-CAL-D79]`, same rule as round 3 finding F6's
            # `render_stage.run_render_stage()` fix): only count an
            # instance as "completed this run" when it had at least one
            # genuinely pending candidate — `has_pending` (computed above,
            # before this instance's `for candidate` loop) is exactly that
            # signal, since a `False` value means every candidate already
            # took the `is_complete()` O(1) skip path with no dispatch, and
            # a `True` value guarantees at least one candidate below is not
            # yet `is_complete()` and so IS dispatched via `run_measurement_
            # for_instance()`. Pre-fix, this incremented unconditionally for
            # every instance the loop merely walked through — so a resumed
            # slice re-entering a fully completed prefix (every candidate
            # skipped) still reported nonzero "new" progress.
            instances_completed_this_run += 1
    if newly_missing:
        campaign.ledger.append(
            {
                "kind": "measurement_missing",
                "reason": missing_reason,
                "cells": [[r, p, c] for r, p, c in sorted(newly_missing)],
            }
        )

    def _rebuild_skipped_records() -> None:
        """Finding D: the ONE place `skipped_complete_cells` is ever turned
        back into `MeasurementRecord`s — a single pass over exactly the
        cells this call's fast path bypassed, not a per-slice repeat."""
        for row_id, probe_index, candidate_id in skipped_complete_cells:
            all_records.extend(
                meter_call_index.completed_records(row_id, probe_index, candidate_id) or []
            )

    if time_budget is None:
        # No slicing: this is the call's only invocation, so the caller
        # always needs the complete record set.
        _rebuild_skipped_records()
        return all_records

    # rehearsal 4 finding G (adopted, `[UNDERSPEC-CAL-D79]`): `remaining`
    # must reflect the TRUE post-run completion state read from the index
    # — not `total_instances - instances_completed_this_run`, which was 0
    # (and so `remaining == total_instances`, ignoring every already-
    # complete instance from prior invocations) whenever the budget expired
    # before this call's loop walked even its first instance. This full
    # index-only pass is O(1) per (instance, candidate) — presence/key-set
    # checks via `is_complete()`, never a PCM read or record reconstruction
    # — so it stays cheap even though it (deliberately) covers every
    # instance in `sorted_instances`, including any this call never reached.
    # round 7 finding #2: same duplicate-surfacing rule as the two
    # `is_complete()`-consulting call sites above — an explicit loop (not a
    # generator expression) so the `StaleMeasurementError` a duplicate cell
    # anywhere in `sorted_instances` now raises can be logged before it
    # propagates, even for an instance this call's own dispatch loop above
    # never reached.
    instances_remaining = 0
    for row_id, probe_index in sorted_instances:
        try:
            pending = _instance_has_pending_candidate(
                row_id, probe_index, candidates, meter_call_index, f0_unusable_instances
            )
        except StaleMeasurementError as exc:
            _append_stale_measurement_stop_event(campaign, exc)
            raise
        if pending:
            instances_remaining += 1
    slice_status = SliceStatus(
        time_budget_seconds=time_budget.seconds,
        elapsed_seconds=time_budget.elapsed(),
        instances_completed_this_run=instances_completed_this_run,
        instances_remaining=instances_remaining,
        completed_all=completed_all,
    )
    if completed_all:
        # Completing invocation: the caller (e.g. `cli._criteria_with_fail_
        # filters()`) needs every record, not just what this call newly
        # dispatched — rebuild the skipped prefix once, here.
        _rebuild_skipped_records()
    return all_records, slice_status


__all__ = [
    "WITHIN_PROCESS_REPEATS",
    "FRESH_PROCESS_REPEATS",
    "CostCapExceededError",
    "WorkerCpuSecondsInvalidError",
    "StaleRenderError",
    "StaleMeasurementError",
    "METER_CALL_GROUP_DISCARDED_KIND",
    "MeterCallIndex",
    "resolve_measure_callable",
    "pcm_bytes_to_signal",
    "load_pcm_signal",
    "PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY",
    "F0_DEPENDENT_ALGORITHM_FAMILIES",
    "primary_output_value",
    "meter_output_to_dict",
    "meter_output_from_dict",
    "MeasurementRecord",
    "run_within_process_calls",
    "run_fresh_process_calls",
    "run_measurement_for_instance",
    "run_measure_stage",
]
