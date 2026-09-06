"""C-1 探索ステージ（RUN10-CAL-v1.2 WP4、Design Memo「開発指針 1: 探索ステージを
本番の前に必ず置く」の実装）。

armed campaign（`c0_freeze`/`campaign.cli` の凍結・封印・ledger 記帳経路）へ
候補を入れる前に、freeze も封印も ledger も無い安い診断で「正例
（positive control = family の TRUTH_CORE 行）に発火し、負例
（negative control 行: SILENCE/NOISE_ONLY/... 等）に発火しない」ことを
先に確認する。目安 30 セル・30 分以内。**本モジュールの出力に claim 能力は
無い**（`claimable` は常に `False`）——armed campaign の C0 freeze / C3 selection
/ C4 holdout を経ていない測定は、どれだけ結果が綺麗でも校正証拠にならない。

## 実経路との差分（意図的な簡略化。§ 報告参照）

本モジュールは `campaign.render_stage`/`campaign.measure_stage.
run_measure_stage()`（ledger 記帳・cap 課金・within/fresh 二重プロセス
determinism 検査・`FrozenCampaign` 前提）を呼ばない。代わりに:

1. **render**: `fixtures.generators.render_row()` + `streams.derive_generator()`
   を直接呼ぶ（`campaign.render_stage`/`_render_worker.py` が使うのと同じ
   純粋関数だが、本経路は subprocess 二重生成 byte-identity 検査を行わない
   ——診断は「発火する/しない」だけを見る cheap gate であり、generator
   determinism の証明は C1 render stage の責務のまま）。secret は固定定数
   `_DIAGNOSE_SECRET`（実 campaign の per-campaign secret とは無関係、
   `~/.vg_cal/` には一切触れない）。
2. **measure**: `campaign.measure_stage.run_within_process_calls()`
   （within-process 直接呼び出し。実 campaign の fresh-process 二重検査・
   ledger 記帳・cost cap 課金は行わない）を `repeats=1` で呼ぶ
   （CLI `--repeats` は probe_index の本数であり、`run_within_process_calls`
   内部の repeat 数とは別軸）。
3. **F0 injection**: 実経路 `campaign.cli._build_f0_by_instance()` は
   C3a で選定された F0 candidate の within/fresh 全 process 出力を集約
   （二段階中央値）し、finite かつ厳密正の instance だけを F0 依存候補へ
   注入する（`campaign.measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES`）。
   本モジュールは selection を経由せず、registry の F0 baseline
   （`F0-B0-CURRENT`）を同じ signal に対して 1 回 within-process 実行した
   単一値をそのまま使う。F0 が使用不能（欠測 or 非有限 or 非正）な instance
   では、実経路の「候補を一切呼ばない」skip 挙動を模して候補呼び出し自体を
   省略し、`missing_reason="F0_UNUSABLE"` を合成する（`fixtures.controls.
   SANCTIONED_ABSTENTIONS` の `(SILENCE, "F0_UNUSABLE")` 判定を意味のある
   ものにするため。実経路ではこれは `MeterOutput.missing_reason` ではなく
   `measurement_missing` ledger event の `reason` フィールドだが、
   ledger を持たない本モジュールでは `CellOutcome.missing_reason` という
   別軸のラベルとして同じ役割を代替する）。
4. **selection/holdout/split/approval/ledger**: 一切経由しない。
   `campaigns/` にも `~/.vg_cal/` にも書き込まない（`--out` は任意パスへの
   診断結果 JSON 出力のみ）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from voice_genesis.calibration.campaign import measure_stage
from voice_genesis.calibration.candidates import registry
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import Candidate
from voice_genesis.calibration.fixtures import matrix
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.controls import SANCTIONED_ABSTENTIONS, ControlClass, detected
from voice_genesis.calibration.fixtures.generators import common as gen_common
from voice_genesis.calibration.fixtures.generators import render_row
from voice_genesis.calibration.fixtures.matrix import FixtureRow, MatrixRow
from voice_genesis.calibration.streams import derive_generator
from voice_genesis.calibration.vocab import ClaimCeiling, MeterId

# ---------------------------------------------------------------------------
# family -> meter（他 module — 並行編集中の `campaign/cli.py` を含む — には
# 依存せず本モジュールで独立に宣言する。`matrix.py` 冒頭の既存規約
# 「他 agent が並行編集中の module には依存せず独立に宣言する」と同じ理由）。
# `cli._FAMILY_TO_METER` と値は同一だが import 元は別（IDENTITY_CAUSAL_SWEEP
# は M6 が cross-meter distance のため対応する fixture-diagnosable meter を
# 持たず、`cli._FAMILY_TO_METER` にも本表にも現れない——
# `campaign/holdout_stage.py` docstring 「`cli._FAMILY_TO_METER` は
# IDENTITY_CAUSAL_SWEEP を全く含まず」と同じ事実を独立に転記）。
# ---------------------------------------------------------------------------

FAMILY_TO_METER: Mapping[str, MeterId] = {
    FixtureFamily.F0_CONTROL.value: MeterId.F0_CONTROL,
    FixtureFamily.FORMANT_GT.value: MeterId.M3_FORMANTS,
    FixtureFamily.TILT_GT.value: MeterId.M2_SPECTRAL_TILT,
    FixtureFamily.APERIODICITY_GT.value: MeterId.M2_APERIODICITY,
    FixtureFamily.RESONANCE_GT.value: MeterId.M4_RESONANCE,
    FixtureFamily.TRANSITION_GT.value: MeterId.M5_TRANSITION,
}

#: registry の F0 baseline 候補（C3a で選定される F0 候補の既定に相当する
#: registry 側の代表値。診断は selection を経由しないため常にこれを使う）。
F0_BASELINE_CANDIDATE_ID = "F0-B0-CURRENT"

#: negative control 行が実 candidate 呼び出しを一切スキップした場合に合成する
#: missing_reason ラベル（実経路の `measurement_missing` ledger event の
#: `reason="F0_UNUSABLE"` に対応。`vocab.MissingReason` の閉語彙には属さない
#: ——`campaign/measure_stage.py` docstring 参照）。
F0_UNUSABLE_REASON = "F0_UNUSABLE"

#: 診断 render 用の固定 seed（campaign secret ではない。`~/.vg_cal/` に触れず、
#: 同一入力で常に同一波形を再現するための公開定数）。
DIAGNOSE_SEED = 0
_DIAGNOSE_SECRET = hashlib.sha256(
    f"voice_genesis.calibration.campaign.diagnose/v1/seed={DIAGNOSE_SEED}".encode("utf-8")
).digest()
_DIAGNOSE_CAMPAIGN_ID = "diagnose"
_DIAGNOSE_SPLIT = "DIAGNOSE"

SCHEMA = "diagnose/0.1"

_ROLE_POSITIVE = "positive"
_ROLE_NEGATIVE = "negative"
_ROLE_CONFOUND = "confound"


# ---------------------------------------------------------------------------
# セル選抜（決定論、手選び禁止）
# ---------------------------------------------------------------------------


def select_diagnostic_cells(family_value: str, max_cells: int) -> list[tuple[MatrixRow, str]]:
    """`fixtures.matrix.build_matrix()` の当該 family 行から、決定論的な
    4 規則 (a)-(c) で診断セルを選ぶ（手選びなし。列挙順は `build_matrix()`
    の順序をそのまま保存する）。

    (a) positive: TRUTH_CORE を truth level（`matrix.truth_identity_for_row()`）
        別に先頭 1 行。母集団は `max_cells // 2` を上限とする。
    (b) negative: `control_class is not None` の行を control_class ごとに
        先頭 1 行（`max_cells - len(positive)` を上限とする——通常 family
        あたり 2-3 control class しか無いため実際にはこの上限に達しない）。
    (c) 余りがあれば `single_axis_nuisance_tag_axis(row) is not None` の
        CONFOUND 行を先頭から詰める。

    戻り値は `(MatrixRow, selection_rule)` のリスト（`selection_rule` は
    `"positive"`/`"negative"`/`"confound"`）。合計は常に `<= max_cells`。
    """
    full = matrix.build_matrix()
    family_rows = [mr for mr in full if mr.row.family == family_value]

    positive_budget = max_cells // 2
    positive: list[tuple[MatrixRow, str]] = []
    seen_truth_levels: set[tuple[Any, ...]] = set()
    for mr in family_rows:
        if len(positive) >= positive_budget:
            break
        if mr.row.block != "TRUTH_CORE":
            continue
        level = matrix.truth_identity_for_row(mr.row)
        if level in seen_truth_levels:
            continue
        seen_truth_levels.add(level)
        positive.append((mr, _ROLE_POSITIVE))

    chosen_ids = {mr.row_id for mr, _ in positive}
    negative_budget = max(0, max_cells - len(positive))
    negative: list[tuple[MatrixRow, str]] = []
    seen_control_classes: set[str] = set()
    for mr in family_rows:
        if len(negative) >= negative_budget:
            break
        cc = mr.row.control_class
        if cc is None or cc in seen_control_classes:
            continue
        seen_control_classes.add(cc)
        negative.append((mr, _ROLE_NEGATIVE))
        chosen_ids.add(mr.row_id)

    confound_budget = max(0, max_cells - len(positive) - len(negative))
    confound: list[tuple[MatrixRow, str]] = []
    if confound_budget > 0:
        for mr in family_rows:
            if len(confound) >= confound_budget:
                break
            if mr.row_id in chosen_ids:
                continue
            if mr.row.block != "CONFOUND":
                continue
            if matrix.single_axis_nuisance_tag_axis(mr.row) is None:
                continue
            confound.append((mr, _ROLE_CONFOUND))
            chosen_ids.add(mr.row_id)

    return positive + negative + confound


# ---------------------------------------------------------------------------
# render + F0 prepass + measure（最下層 API 直呼び。ledger/campaign を経由しない）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellOutcome:
    """1 (row, probe_index) × 1 candidate の診断結果。`missing_reason` は
    `output.missing_reason.value` か、F0-unusable skip 由来の合成ラベル
    (`F0_UNUSABLE_REASON`) のいずれか（両方とも無ければ `None`）。"""

    role: str
    control_class: str | None
    output: MeterOutput
    missing_reason: str | None


def render_diagnose_signal(row: FixtureRow, row_id: str, probe_index: int) -> tuple[np.ndarray, int]:
    """`row` を 1 probe 分 render し `[-1, 1]` 正規化 float64 signal を返す
    （`fixtures.generators.render_row` + `streams.derive_generator` の直呼び。
    secret は固定公開定数 `_DIAGNOSE_SECRET` ——campaign secret ではない）。"""
    rng = derive_generator(
        _DIAGNOSE_SECRET,
        campaign_id=_DIAGNOSE_CAMPAIGN_ID,
        family=row.family,
        split=_DIAGNOSE_SPLIT,
        row_id=row_id,
        probe_index=probe_index,
        purpose="generator",
    )
    pcm = render_row(row, rng)
    return measure_stage.pcm_bytes_to_signal(gen_common.pcm16_bytes(pcm), row.sr_hz)


def resolve_f0_prepass(signal: np.ndarray, sr: int, row_id: str, probe_index: int) -> float | None:
    """registry の F0 baseline 候補 (`F0_BASELINE_CANDIDATE_ID`) を `signal`
    上で 1 回 within-process 実行し、finite かつ厳密正の f0 推定値を返す
    （`campaign.cli._build_f0_by_instance()` の finite/strictly-positive
    guard を単一候補・単一 call に単純化したもの）。使用不能なら `None`。"""
    f0_candidate = registry.candidate_by_id(F0_BASELINE_CANDIDATE_ID)
    records = measure_stage.run_within_process_calls(
        f0_candidate, signal, sr, f0_hz=None, row_id=row_id, probe_index=probe_index, repeats=1
    )
    output = records[0].output
    if output.ineligible or output.missing_reason is not None:
        return None
    value = output.values.get("f0_hz")
    if value is None or not math.isfinite(value) or value <= 0.0:
        return None
    return float(value)


def needs_f0_injection(candidate: Candidate) -> bool:
    return candidate.algorithm_family in measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES


def measure_cell(
    candidate: Candidate,
    role: str,
    control_class: str | None,
    signal: np.ndarray,
    sr: int,
    f0_hz: float | None,
    row_id: str,
    probe_index: int,
) -> CellOutcome:
    """1 (row, probe_index) × 1 candidate の測定。F0 依存候補で `f0_hz` が
    使用不能なら、実経路の skip 挙動（候補を一切呼ばない）を模して呼び出し
    自体を省略し `F0_UNUSABLE_REASON` を合成する。"""
    if needs_f0_injection(candidate) and f0_hz is None:
        return CellOutcome(role, control_class, MeterOutput(), F0_UNUSABLE_REASON)
    records = measure_stage.run_within_process_calls(
        candidate,
        signal,
        sr,
        f0_hz=f0_hz if needs_f0_injection(candidate) else None,
        row_id=row_id,
        probe_index=probe_index,
        repeats=1,
    )
    output = records[0].output
    reason = output.missing_reason.value if output.missing_reason is not None else None
    return CellOutcome(role, control_class, output, reason)


# ---------------------------------------------------------------------------
# 判定（純関数。合成 MeterOutput でテスト可能——render/measure を経由しない）
# ---------------------------------------------------------------------------


def _rate(flags: Sequence[bool]) -> float | None:
    if not flags:
        return None
    return sum(1 for f in flags if f) / len(flags)


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _verdict(
    ceiling: ClaimCeiling, positive_rate: float | None, negative_rate: float | None, all_ineligible: bool
) -> str:
    if positive_rate is None or negative_rate is None or all_ineligible:
        return "NOT_EVALUABLE"
    if positive_rate < 1.0:
        return "FAIL_POSITIVE"
    if negative_rate > 0.0:
        return "FAIL_NEGATIVE"
    if ceiling == ClaimCeiling.NONE:
        return "NO_CEILING"
    return "PASS"


def evaluate_candidate(candidate: Candidate, outcomes: Sequence[CellOutcome]) -> dict[str, Any]:
    """`outcomes`（`measure_cell()` の結果列、または合成 `CellOutcome`）から
    1 候補分の診断レポートを組み立てる純関数。CONFOUND cell は
    `missing_by_reason` にのみ寄与し、positive/negative fire rate・
    verdict の分母には含めない。"""
    positive_flags: list[bool] = []
    negative_flags_by_class: dict[str, list[bool]] = {}
    missing_by_reason: Counter[str] = Counter()
    sanctioned_abstentions = 0
    verdict_relevant_total = 0
    verdict_relevant_ineligible = 0

    for outcome in outcomes:
        if outcome.missing_reason is not None:
            missing_by_reason[outcome.missing_reason] += 1
        if outcome.role == _ROLE_CONFOUND:
            continue
        verdict_relevant_total += 1
        if outcome.output.ineligible:
            verdict_relevant_ineligible += 1
        fired = detected(outcome.output, predicate=candidate.detection_predicate)
        if outcome.role == _ROLE_POSITIVE:
            positive_flags.append(fired)
        else:
            cc = outcome.control_class
            if cc is None:
                raise ValueError("evaluate_candidate: negative-role outcome missing control_class")
            negative_flags_by_class.setdefault(cc, []).append(fired)
            if outcome.missing_reason is not None and (
                ControlClass(cc),
                outcome.missing_reason,
            ) in SANCTIONED_ABSTENTIONS:
                sanctioned_abstentions += 1

    positive_rate = _rate(positive_flags)
    negative_flags_all = [f for flags in negative_flags_by_class.values() for f in flags]
    negative_rate = _rate(negative_flags_all)
    all_ineligible = verdict_relevant_total > 0 and verdict_relevant_ineligible == verdict_relevant_total
    verdict = _verdict(candidate.claim_ceiling, positive_rate, negative_rate, all_ineligible)

    return {
        "candidate_id": candidate.candidate_id,
        "ceiling": candidate.claim_ceiling.value,
        "positive_fire_rate": _round_or_none(positive_rate),
        "negative_fire_rate": _round_or_none(negative_rate),
        "negative_fire_by_control_class": {
            cc: _round_or_none(_rate(flags))
            for cc, flags in sorted(negative_flags_by_class.items())
        },
        "sanctioned_abstentions": sanctioned_abstentions,
        "missing_by_reason": dict(sorted(missing_by_reason.items())),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# 全体オーケストレーション
# ---------------------------------------------------------------------------


def run_diagnosis(
    family_value: str,
    candidates: Sequence[Candidate],
    max_cells: int,
    repeats: int,
) -> dict[str, Any]:
    started = time.monotonic()
    cells = select_diagnostic_cells(family_value, max_cells)

    signal_cache: dict[tuple[str, int], tuple[np.ndarray, int]] = {}
    f0_cache: dict[tuple[str, int], float | None] = {}

    def _signal_for(mr: MatrixRow, probe_index: int) -> tuple[np.ndarray, int]:
        key = (mr.row_id, probe_index)
        cached = signal_cache.get(key)
        if cached is None:
            cached = render_diagnose_signal(mr.row, mr.row_id, probe_index)
            signal_cache[key] = cached
        return cached

    def _f0_for(mr: MatrixRow, probe_index: int) -> float | None:
        key = (mr.row_id, probe_index)
        if key not in f0_cache:
            signal, sr = _signal_for(mr, probe_index)
            f0_cache[key] = resolve_f0_prepass(signal, sr, mr.row_id, probe_index)
        return f0_cache[key]

    outcomes_by_candidate: dict[str, list[CellOutcome]] = {c.candidate_id: [] for c in candidates}
    for mr, role in cells:
        control_class = mr.row.control_class if role == _ROLE_NEGATIVE else None
        for probe_index in range(repeats):
            signal, sr = _signal_for(mr, probe_index)
            for candidate in candidates:
                f0_hz = _f0_for(mr, probe_index) if needs_f0_injection(candidate) else None
                outcome = measure_cell(
                    candidate, role, control_class, signal, sr, f0_hz, mr.row_id, probe_index
                )
                outcomes_by_candidate[candidate.candidate_id].append(outcome)

    candidate_reports = [
        evaluate_candidate(candidate, outcomes_by_candidate[candidate.candidate_id])
        for candidate in candidates
    ]
    elapsed = time.monotonic() - started

    return {
        "schema": SCHEMA,
        "family": family_value,
        "cells": [
            {
                "row_id": mr.row_id,
                "block": mr.row.block,
                "control_class": mr.row.control_class,
                "selection_rule": role,
            }
            for mr, role in cells
        ],
        "candidates": candidate_reports,
        "elapsed_seconds": round(elapsed, 3),
        "claimable": False,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def candidates_for_family(family_value: str) -> tuple[Candidate, ...]:
    """`--candidate` 省略時の既定候補集合（当該 family の registry 全候補）。
    `FAMILY_TO_METER` に無い family（IDENTITY_CAUSAL_SWEEP）は空集合。"""
    meter = FAMILY_TO_METER.get(family_value)
    if meter is None:
        return ()
    return registry.candidates_for_meter(meter)


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m voice_genesis.calibration.campaign.diagnose",
        description=(
            "armed campaign 投入前の C-1 探索ステージ: freeze/封印/ledger なしで"
            "正例発火・負例不発火を安く確認する（claim 不可）。"
        ),
    )
    parser.add_argument(
        "--family", required=True, choices=[f.value for f in FixtureFamily], help="診断対象 fixture family"
    )
    parser.add_argument(
        "--candidate",
        action="append",
        default=None,
        help="診断対象 candidate_id（複数指定可。省略時は当該 family の registry 全候補）",
    )
    parser.add_argument("--max-cells", type=int, default=30, help="診断セル数の上限（既定 30）")
    parser.add_argument("--repeats", type=int, default=1, help="セルあたりの probe 数（既定 1）")
    parser.add_argument("--out", type=Path, default=None, help="結果 JSON の出力先（省略時 stdout）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.max_cells < 1:
        _print({"result": "ERROR", "detail": f"--max-cells must be >= 1, got {args.max_cells!r}"})
        return 1
    if args.repeats < 1:
        _print({"result": "ERROR", "detail": f"--repeats must be >= 1, got {args.repeats!r}"})
        return 1

    if args.candidate:
        try:
            candidates: tuple[Candidate, ...] = tuple(
                registry.candidate_by_id(cid) for cid in args.candidate
            )
        except KeyError as exc:
            _print({"result": "ERROR", "detail": str(exc)})
            return 1
        meter = FAMILY_TO_METER.get(args.family)
        if meter is not None:
            mismatched = [c.candidate_id for c in candidates if c.meter != meter]
            if mismatched:
                _print(
                    {
                        "result": "ERROR",
                        "detail": (
                            f"--candidate {mismatched!r} do not belong to family "
                            f"{args.family!r} (meter {meter.value!r})"
                        ),
                    }
                )
                return 1
    else:
        candidates = candidates_for_family(args.family)

    report = run_diagnosis(args.family, candidates, args.max_cells, args.repeats)

    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _print({"result": "OK", "out": str(args.out)})
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
