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

import hashlib
import importlib
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from voice_genesis.calibration.campaign import render_stage
from voice_genesis.calibration.campaign.caps import CostCapExceededError, save_cap_counters
from voice_genesis.calibration.campaign.state import FrozenCampaign
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
    （差し替えられた/破損した bytes を測定しない — `render_stage.py` の
    resume 判定が sidecar 無しの sha だけを見ていたのに対し、本関数は
    ledger 側の pin も独立に要求する二重照合）。PCM ファイル自体が存在
    しない場合は（render が一度も行われていない、より基本的な状態）
    `FileNotFoundError` のまま送出する — こちらは「差し替え」ではなく
    「まだ render していない」なので既存呼び出し元の分岐と型を変えない。
    """
    pcm_path = campaign.renders_dir / row_id / f"{probe_index}.pcm"
    if not pcm_path.is_file():
        raise FileNotFoundError(
            f"measure_stage: pcm not rendered for row_id={row_id!r} "
            f"probe_index={probe_index}: {pcm_path}"
        )
    pcm_bytes = pcm_path.read_bytes()
    actual_sha = hashlib.sha256(pcm_bytes).hexdigest()

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

    sha_path = pcm_path.with_suffix(".sha256")
    if not sha_path.is_file():
        raise _stale(f"sha256 sidecar missing: {sha_path}")
    sidecar_sha = sha_path.read_text(encoding="utf-8").strip()
    if sidecar_sha != actual_sha:
        raise _stale(
            f"pcm sha256={actual_sha!r} does not match sidecar {sha_path}={sidecar_sha!r}"
        )

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


def meter_output_to_dict(output: MeterOutput) -> dict[str, object]:
    return {
        "values": dict(output.values),
        "missing_reason": output.missing_reason.value if output.missing_reason is not None else None,
        "ineligible": output.ineligible,
        "ineligible_reason": output.ineligible_reason,
    }


def meter_output_from_dict(d: Mapping[str, object]) -> MeterOutput:
    missing = d.get("missing_reason")
    values_raw = d.get("values") or {}
    if not isinstance(values_raw, Mapping):
        raise ValueError("measure_stage: MeterOutput payload 'values' must be an object")
    ineligible_reason = d.get("ineligible_reason")
    return MeterOutput(
        values={str(k): float(v) for k, v in values_raw.items()},
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
    判断（ledger 調査の上での手動復旧）に委ねる。"""

    def __init__(self, row_id: str, probe_index: int, candidate_id: str, detail: str) -> None:
        self.row_id = row_id
        self.probe_index = probe_index
        self.candidate_id = candidate_id
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


def _completed_meter_call_records(
    ledger_entries: Sequence[LedgerEntry],
    row_id: str,
    probe_index: int,
    candidate_id: str,
) -> list[MeasurementRecord] | None:
    """finding #9: ledger から (row_id, probe_index, candidate_id) の
    `meter_call` を `(repeat_kind, repeat_index)` キーで再構成する。

    - 1 件も無ければ `None`（未着手 — 呼び出し元は通常どおり測定する）。
    - within `WITHIN_PROCESS_REPEATS` 件 + fresh `FRESH_PROCESS_REPEATS`
      件がちょうど 1 件ずつ揃っていれば、その内容から再構成した
      `MeasurementRecord` 列を返す（呼び出し元は再測定・再記帳しない —
      二重追記の禁止）。
    - それ以外（部分的にしか揃っていない、または同一キーに複数件記帳）は
      `StaleMeasurementError` を送出する（呼び出し元は ledger `stop_event`
      を記帳してから re-raise する）。
    """
    by_key: dict[tuple[str, int], list[Mapping[str, object]]] = {}
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping) or payload.get("kind") != "meter_call":
            continue
        if (
            payload.get("row_id") != row_id
            or payload.get("probe_index") != probe_index
            or payload.get("candidate_id") != candidate_id
        ):
            continue
        repeat_kind = payload.get("repeat_kind")
        repeat_index = payload.get("repeat_index")
        if repeat_kind not in ("within", "fresh") or not isinstance(repeat_index, int):
            continue
        by_key.setdefault((repeat_kind, repeat_index), []).append(payload)

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


def _run_one_fresh_call(
    candidate_id: str, pcm_path: Path, sr: int, f0_hz: float | None, timeout_s: float
) -> MeterOutput:
    payload = {
        "candidate_id": candidate_id,
        "pcm_path": str(pcm_path),
        "sr_hz": sr,
        "f0_hz": f0_hz,
    }
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "voice_genesis.calibration.campaign._measure_worker",
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=True,
    )
    return meter_output_from_dict(json.loads(proc.stdout))


def run_fresh_process_calls(
    candidate: Candidate,
    pcm_path: Path,
    sr: int,
    *,
    f0_hz: float | None,
    row_id: str,
    probe_index: int,
    repeats: int = FRESH_PROCESS_REPEATS,
    timeout_s: float = 60.0,
    max_workers: int = 1,
) -> list[MeasurementRecord]:
    """`repeats` 回、subprocess worker (`_measure_worker.py`) を起動して測定
    する。`max_workers>1` なら `ThreadPoolExecutor` で並行起動する（結果は
    repeat_index 順に整列して返す — ledger への append 順を決定論的に保つ
    ため）。"""
    if max_workers <= 1:
        outputs = [
            _run_one_fresh_call(candidate.candidate_id, pcm_path, sr, f0_hz, timeout_s)
            for _ in range(repeats)
        ]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    _run_one_fresh_call, candidate.candidate_id, pcm_path, sr, f0_hz, timeout_s
                )
                for _ in range(repeats)
            ]
            outputs = [f.result() for f in futures]
    return [
        MeasurementRecord(
            row_id=row_id,
            probe_index=probe_index,
            candidate_id=candidate.candidate_id,
            repeat_kind="fresh",
            repeat_index=i,
            process_id=f"fresh-process-{i}",
            output=output,
        )
        for i, output in enumerate(outputs)
    ]


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
    一切行わない）。"""
    try:
        resumed = _completed_meter_call_records(
            campaign.ledger.entries, row_id, probe_index, candidate.candidate_id
        )
    except StaleMeasurementError as exc:
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

    t0 = time.perf_counter()
    within = run_within_process_calls(
        candidate, signal, sr, f0_hz=f0_hz, row_id=row_id, probe_index=probe_index
    )
    fresh = run_fresh_process_calls(
        candidate,
        pcm_path,
        sr,
        f0_hz=f0_hz,
        row_id=row_id,
        probe_index=probe_index,
        max_workers=max_workers,
    )
    elapsed = time.perf_counter() - t0

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
            **meter_output_to_dict(record.output),
        }
        campaign.ledger.append(payload)
        storage_bytes += len(json.dumps(payload).encode("utf-8"))

    if cap_counters is not None:
        # round 13 finding #3: this measurement (1 instance x 1 candidate =
        # within3 + fresh3) is 1 budget work unit — same accounting rule and
        # granularity as render_stage.render_instance().
        budget_charge = cost_caps.budget_charge_per_work_unit() if cost_caps is not None else 0.0
        cap_counters.add(compute=elapsed, storage=storage_bytes, budget=budget_charge)
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


def run_measure_stage(
    campaign: FrozenCampaign,
    instances: Sequence[tuple[str, int]],
    candidates: Sequence[Candidate],
    *,
    sr_by_row: Mapping[str, int],
    f0_by_instance: Mapping[tuple[str, int], float] | None = None,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    max_workers: int = 1,
) -> list[MeasurementRecord]:
    """`instances × candidates` の全 work unit を決定論的順序（instance →
    candidate_id 昇順）で処理する。"""
    f0_map = f0_by_instance or {}
    all_records: list[MeasurementRecord] = []
    for row_id, probe_index in sorted(instances):
        for candidate in sorted(candidates, key=lambda c: c.candidate_id):
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
                )
            )
    return all_records


__all__ = [
    "WITHIN_PROCESS_REPEATS",
    "FRESH_PROCESS_REPEATS",
    "CostCapExceededError",
    "StaleRenderError",
    "StaleMeasurementError",
    "resolve_measure_callable",
    "pcm_bytes_to_signal",
    "load_pcm_signal",
    "PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY",
    "primary_output_value",
    "meter_output_to_dict",
    "meter_output_from_dict",
    "MeasurementRecord",
    "run_within_process_calls",
    "run_fresh_process_calls",
    "run_measurement_for_instance",
    "run_measure_stage",
]
