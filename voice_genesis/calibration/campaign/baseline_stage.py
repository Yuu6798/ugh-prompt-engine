"""C2 baseline stage: B0 candidates × CALIBRATION split + tolerance 導出
（IMPLEMENTATION_MAP_v1.md §6.4, 設計正本 §6）。

`tolerance.py` の pooled dispersion を使い、B0 各候補の主要出力フィールド
（`values` の唯一のキー、または B0 実装が返す最初のキー）を CALIBRATION
split 全 instance（probe repeat + within/fresh-process の 6 call）から
プールする。

`[UNDERSPEC-CAL-D15]` 設計正本 §6 は tolerance を「(family × condition
class) で pooled した dispersion」と定めるが、D2 baseline 監査の粒度として
本モジュールは **candidate_id 単位**（同一候補の全 CALIBRATION instance を
1 プールとする）を採用する。これは「family × condition class」の最も単純な
上位近似（1 候補=1 construct=1 family という関係が B0 候補では自明に成立
する）であり、より細かい condition class 分割は E_use/観測モデルが本 Phase
の範囲外であるため導入しない（`baseline_audit` event の raw per-instance
values は記録するため、後段でより細かい再集計を行う余地は失わない）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from voice_genesis.calibration.campaign import measure_stage, workunits
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.campaign.time_budget import TimeBudget
from voice_genesis.calibration.candidates.registry import ALL_CANDIDATES, Candidate
from voice_genesis.calibration.canonical import manifest_sha
from voice_genesis.calibration.cost_caps import CapCounters, CostCaps
from voice_genesis.calibration.fixtures.matrix import MatrixRow
from voice_genesis.calibration.tolerance import derive_floor, pooled_dispersion
from voice_genesis.calibration.tolerance import tolerance as tolerance_of

#: PCM16 量子化半ステップ（`fixtures/generators/common.quantize_pcm16` の
#: [-1,1] 正規化スケールに対応）。`derive_floor()` の既定 floor 入力。
_DEFAULT_PCM_QUANTIZATION_STEP = 2.0 / 32767.0
_DEFAULT_FLOAT_EPS_BOUND = 1e-9

#: tolerance = max(k * pooled_SD, floor) の k（設計正本 §6 は具体値を凍結し
#: ない。`[UNDERSPEC-CAL-D15]` 系列: 統計学で一般的な保守係数として 3 を採用）。
DEFAULT_TOLERANCE_K = 3.0


def b0_candidates() -> tuple[Candidate, ...]:
    """B0 baseline 候補（`candidate_id` に `"-B0-"` を含む全候補。設計正本
    §8「`B0_CURRENT` を必ず含める」）。"""
    return tuple(c for c in ALL_CANDIDATES if "-B0-" in c.candidate_id)


def _primary_value(values: Mapping[str, float]) -> float | None:
    if not values:
        return None
    return next(iter(sorted(values.items())))[1]


def run_baseline_stage(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[MatrixRow],
    *,
    k: float = DEFAULT_TOLERANCE_K,
    meter_declared_resolution: float | None = None,
    max_workers: int = 1,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
    discard_partial_groups: bool = False,
    time_budget: TimeBudget | None = None,
    invocation_id: str | None = None,
) -> dict[str, object]:
    """C2: B0 candidates × CALIBRATION split の実測 → pooled tolerance 導出
    → `baseline_audit` + `baseline_audited` ledger event。戻り値は
    `{"baseline_audit_sha": str, "tolerances": {candidate_id: {...}}}`。
    `cap_counters`/`cost_caps`（finding #1）は素通しで
    `measure_stage.run_measure_stage` へ渡す — cap 超過は
    `measure_stage.CostCapExceededError` として fail-closed に伝播する。

    R1 の `discard_partial_groups` は素通しで `measure_stage.run_measure_
    stage` へ渡す（design memo `design_runner_robustness.md`,
    `[UNDERSPEC-CAL-D79]`）。

    R2: `time_budget` が渡され、かつ measure stage が instance 境界で
    予算超過して完走しなかった場合、tolerance 導出・`baseline_audit`/
    `baseline_audited` event の記帳（= phase transition）を一切行わず
    `{"slice_status": SliceStatus}` を返す（呼び出し元 `cli._run_c2` が
    これを検出して `PARTIAL_SLICE` report を組み立てる）。"""
    assignment = campaign.realized_split.assignment
    instances = workunits.c2_baseline_instances(matrix_rows, assignment)
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    candidates = b0_candidates()

    if time_budget is not None:
        records, slice_status = measure_stage.run_measure_stage(
            campaign,
            instances,
            candidates,
            sr_by_row=sr_by_row,
            max_workers=max_workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            discard_partial_groups=discard_partial_groups,
            stage="c2",
            time_budget=time_budget,
            invocation_id=invocation_id,
        )
        if not slice_status.completed_all:
            return {"slice_status": slice_status}
    else:
        records = measure_stage.run_measure_stage(
            campaign,
            instances,
            candidates,
            sr_by_row=sr_by_row,
            max_workers=max_workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
            discard_partial_groups=discard_partial_groups,
            stage="c2",
            invocation_id=invocation_id,
        )

    values_by_cell: dict[tuple[str, str], list[float]] = {}
    for record in records:
        primary = _primary_value(record.output.values)
        if primary is None:
            continue
        values_by_cell.setdefault((record.candidate_id, record.row_id), []).append(primary)

    pooled = pooled_dispersion(values_by_cell, pool_key=lambda cell: cell[0])
    floor_value, floor_formula = derive_floor(
        pcm_quantization_step=_DEFAULT_PCM_QUANTIZATION_STEP,
        float_eps_bound=_DEFAULT_FLOAT_EPS_BOUND,
        meter_declared_resolution=meter_declared_resolution,
    )

    tolerances: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        pooled_sd = pooled.get(candidate.candidate_id, 0.0)
        tolerances[candidate.candidate_id] = {
            "pooled_sd": pooled_sd,
            "tolerance": tolerance_of(pooled_sd, k, floor_value),
            "floor": floor_value,
            "floor_formula": floor_formula,
            "k": k,
        }

    audit_payload = {
        "candidate_ids": sorted(c.candidate_id for c in candidates),
        "instance_count": len(instances),
        "tolerances": tolerances,
    }
    baseline_audit_sha = manifest_sha(audit_payload)

    campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": baseline_audit_sha, "payload": audit_payload}
    )
    campaign.ledger.append(
        {
            "kind": "baseline_audited",
            "baseline_audit_sha": baseline_audit_sha,
            "candidate_count": len(candidates),
            "instance_count": len(instances),
        }
    )
    return {"baseline_audit_sha": baseline_audit_sha, "tolerances": tolerances}


__all__ = [
    "DEFAULT_TOLERANCE_K",
    "b0_candidates",
    "run_baseline_stage",
]
