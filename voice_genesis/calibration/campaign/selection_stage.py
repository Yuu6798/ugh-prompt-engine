"""C3a (F0 selection) / C3b (他 family selection) stage
（IMPLEMENTATION_MAP_v1.md §6.4, 設計正本 §9, memo §2.6 ceiling 階級間裁定）。

`selection.select_across_ceilings()` を family ごとに独立に呼び、
`SELECTION_FROZEN` event を記帳する。unseal（§7）が要求する 5 sha 相互参照の
うち 4 つ（`baseline_audit_sha`/`candidate_space_sha`/`selection_rule_sha`/
`selected_candidate_sha`）は `provenance.py` の `_UNSEAL_PREREQUISITE_KINDS`
契約に厳密に従う: これらのフィールド値は参照先イベントの **`artifact_sha`
ではなく `entry_sha`（ledger chain 上のハッシュ）** でなければならない
（`provenance._references_prior_prerequisites` が `prior_entries_by_sha` を
`entry_sha` でインデックスするため）。

`[UNDERSPEC-CAL-D20]` `provenance.py` の unseal 相互参照契約は単一の
`selected_candidate` prerequisite（`candidate_id: str` 1 件）を要求する形状
だが、本キャンペーンは meter family ごとに独立した selection を持つ（§9）。
そのため C3b は **全 non-F0 family の選択結果を 1 つの `selected_candidate`
prerequisite event へ集約**する: `candidate_id` は family→selected_id を
`family:id` 形式で連結した監査用文字列、`artifact_sha` は全 family の
raw/rounded vector を含む canonical summary の sha。個々の family の詳細
（`SelectionOutcome`）は `selection_frozen` event の payload
（`selected_by_family`/`raw_vectors_by_family`/`rounded_vectors_by_family`）
にも別途フルで記録するため、集約による情報損失はない。

F0 selection（C3a）は unseal の 5-sha 相互参照チェーンには参加しない
（`kind="f0_selection_frozen"`。§8「F0 選択は下流候補実行前に完了する
一方向依存」を `state.CampaignPhase.F0_SELECTION_FROZEN` の手続順序で表現
する — `cli.py`/`state.py` が C3b 開始前に F0_SELECTION_FROZEN 到達を要求
する）。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import kendalltau

import voice_genesis.calibration.selection as selection_module
from voice_genesis.calibration.campaign import measure_stage
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.candidates.registry import ALL_CANDIDATES, Candidate
from voice_genesis.calibration.canonical import manifest_sha
from voice_genesis.calibration.fixtures.matrix import FixtureRow
from voice_genesis.calibration.observables import q95
from voice_genesis.calibration.selection import CandidateCriteria, SelectionOutcome, select_across_ceilings


def candidate_space_sha(candidates: Sequence[Candidate] | None = None) -> str:
    """`registry.ALL_CANDIDATES`（または明示指定した候補集合）の canonical
    snapshot sha。C0 で凍結済みの 99 候補宣言を C3 時点で再確認・記録する。"""
    pool = candidates if candidates is not None else ALL_CANDIDATES
    payload = {
        c.candidate_id: {
            "meter": c.meter.value,
            "construct": c.construct,
            "unit": c.unit,
            "algorithm_family": c.algorithm_family,
            "parameters": dict(c.parameters),
            "domain": c.domain,
            "missing_rule": c.missing_rule,
            "independence_tier": c.independence_tier.value,
            "claim_ceiling": c.claim_ceiling.value,
            "complexity_rank": c.complexity_rank,
            "implementation_ref": c.implementation_ref,
        }
        for c in pool
    }
    return manifest_sha(payload)


def selection_rule_sha() -> str:
    """`selection.py` ソース bytes の sha256（deliverable 要求: "sha of
    selection.py source"）。"""
    path = Path(selection_module.__file__)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vectors_jsonable(
    vectors: Mapping[str, tuple[float | int | str, ...]],
) -> dict[str, list[float | int | str]]:
    return {k: list(v) for k, v in sorted(vectors.items())}


@dataclass(frozen=True)
class F0SelectionResult:
    outcome: SelectionOutcome
    f0_selection_frozen_entry_sha: str


def run_c3a_f0_selection(
    campaign: FrozenCampaign, criteria: Sequence[CandidateCriteria]
) -> F0SelectionResult:
    """C3a: F0_CONTROL candidates の selection。`f0_selection_frozen` event を
    記帳する（unseal の 5-sha チェーンには参加しない — 上記モジュール
    docstring 参照）。"""
    outcome = select_across_ceilings(criteria)
    payload = {
        "kind": "f0_selection_frozen",
        "candidate_space_sha": candidate_space_sha(),
        "selection_rule_sha": selection_rule_sha(),
        "family": "F0_CONTROL",
        "selected_candidate_id": outcome.selected_candidate_id,
        "outcome": outcome.outcome,
        "raw_vectors": _vectors_jsonable(outcome.raw_vectors),
        "rounded_vectors": _vectors_jsonable(outcome.rounded_vectors),
        "ineligible_candidates": [list(t) for t in outcome.ineligible_candidates],
    }
    entry = campaign.ledger.append(payload)
    return F0SelectionResult(outcome=outcome, f0_selection_frozen_entry_sha=entry.entry_sha)


@dataclass(frozen=True)
class SelectionFreezeResult:
    outcomes_by_family: Mapping[str, SelectionOutcome]
    selection_frozen_entry_sha: str
    baseline_audit_prerequisite_sha: str
    candidate_space_prerequisite_sha: str
    selection_rule_prerequisite_sha: str
    selected_candidate_prerequisite_sha: str


def run_c3b_selection(
    campaign: FrozenCampaign,
    criteria_by_family: Mapping[str, Sequence[CandidateCriteria]],
    *,
    baseline_audit_entry_sha: str,
) -> SelectionFreezeResult:
    """C3b: F0_CONTROL を除く各 family の selection を独立に
    `select_across_ceilings()` で行い、4 前提 event（`candidate_space`/
    `selection_rule`/`selected_candidate`、`baseline_audit` は呼び出し側が
    C2 で既に記帳した event の entry_sha を渡す）+ 1 つの `selection_frozen`
    event を記帳する。`baseline_audit_entry_sha` は `baseline_stage` が
    `campaign.ledger.append({"kind": "baseline_audit", ...})` した際の
    `LedgerEntry.entry_sha`（`artifact_sha` ではない — モジュール docstring
    参照）。
    """
    if "F0_CONTROL" in criteria_by_family:
        raise ValueError("run_c3b_selection: F0_CONTROL selection uses run_c3a_f0_selection")

    outcomes: dict[str, SelectionOutcome] = {}
    for family in sorted(criteria_by_family):
        outcomes[family] = select_across_ceilings(criteria_by_family[family])

    cs_entry = campaign.ledger.append(
        {"kind": "candidate_space", "artifact_sha": candidate_space_sha()}
    )
    sr_entry = campaign.ledger.append(
        {"kind": "selection_rule", "artifact_sha": selection_rule_sha()}
    )

    selected_by_family = {
        family: outcome.selected_candidate_id for family, outcome in sorted(outcomes.items())
    }
    aggregate_summary = {
        "selected_by_family": selected_by_family,
        "raw_vectors_by_family": {
            family: _vectors_jsonable(outcome.raw_vectors)
            for family, outcome in sorted(outcomes.items())
        },
        "rounded_vectors_by_family": {
            family: _vectors_jsonable(outcome.rounded_vectors)
            for family, outcome in sorted(outcomes.items())
        },
    }
    aggregate_sha = manifest_sha(aggregate_summary)
    candidate_id_join = (
        "|".join(f"{family}:{cid}" for family, cid in sorted(selected_by_family.items()) if cid)
        or "NONE"
    )
    sc_entry = campaign.ledger.append(
        {
            "kind": "selected_candidate",
            "artifact_sha": aggregate_sha,
            "candidate_id": candidate_id_join,
        }
    )

    selection_frozen_payload = {
        "kind": "selection_frozen",
        "baseline_audit_sha": baseline_audit_entry_sha,
        "candidate_space_sha": cs_entry.entry_sha,
        "selection_rule_sha": sr_entry.entry_sha,
        "selected_candidate_sha": sc_entry.entry_sha,
        "selected_by_family": selected_by_family,
        "outcomes_by_family": {
            family: outcome.outcome for family, outcome in sorted(outcomes.items())
        },
        **aggregate_summary,
    }
    sf_entry = campaign.ledger.append(selection_frozen_payload)

    return SelectionFreezeResult(
        outcomes_by_family=outcomes,
        selection_frozen_entry_sha=sf_entry.entry_sha,
        baseline_audit_prerequisite_sha=baseline_audit_entry_sha,
        candidate_space_prerequisite_sha=cs_entry.entry_sha,
        selection_rule_prerequisite_sha=sr_entry.entry_sha,
        selected_candidate_prerequisite_sha=sc_entry.entry_sha,
    )


# ---------------------------------------------------------------------------
# CandidateCriteria construction from real measurement records
# [UNDERSPEC-CAL-D13] (truth field) / [UNDERSPEC-CAL-D16] (criteria
# aggregation rule)
# ---------------------------------------------------------------------------

#: family の known-truth field（`c0_freeze._KNOWN_TRUTH_FIELD` と同じ対応
#: だが、他 agent が並行編集中の `c0_freeze.py` には依存せず本モジュールで
#: 独立に宣言する）。FORMANT_GT のみ `pole_freqs_hz`（tuple）の先頭要素
#: （F1）をスカラー truth として使う。
_TRUTH_FIELD_BY_FAMILY: Mapping[str, str] = {
    "F0_CONTROL": "f0_hz",
    "TILT_GT": "slope_db_per_oct",
    "APERIODICITY_GT": "injected_noise_fraction",
    "RESONANCE_GT": "center_hz",
    "TRANSITION_GT": "discontinuity_magnitude",
    "IDENTITY_CAUSAL_SWEEP": "delta",
}


def truth_value_for_row(row: FixtureRow) -> float | None:
    """`[UNDERSPEC-CAL-D13]` family の主要 truth スカラーを返す（FORMANT_GT
    は先頭 pole=F1 を代表値とする）。値が未定義なら `None`。"""
    if row.family == "FORMANT_GT":
        return float(row.pole_freqs_hz[0]) if row.pole_freqs_hz else None
    field = _TRUTH_FIELD_BY_FAMILY.get(row.family)
    if field is None:
        return None
    value = getattr(row, field, None)
    return None if value is None else float(value)


def build_candidate_criteria(
    candidate: Candidate,
    records: Sequence[measure_stage.MeasurementRecord],
    truth_by_instance: Mapping[tuple[str, int], float],
    *,
    zero_guard: float = 1e-9,
) -> CandidateCriteria:
    """`[UNDERSPEC-CAL-D16]` 実測 record 列（within+fresh 6 call/instance）
    から `CandidateCriteria` を構築する最も単純な集計規則:

    - instance ごとに 6 call の主要出力（`measure_stage.primary_output_value`）
      の平均を測定値とし、normalized error `(measured-truth)/max(|truth|,
      zero_guard)` を作る。ABSOLUTE 系列は normalized MAE / |signed bias| /
      q95(AE)、DIRECTIONAL 系列は `scipy.stats.kendalltau(truth, measured)`
      と truth 順ソート上の隣接反転率を使う。
    - `nuisance_sensitivity_max` は本 D2 infra では `0.0` 固定とする
      （confound axis ペアリングの実測配線 — §5.1 targeted interaction 行と
      anchor 行の対応付け — は `holdout_stage.RawInstanceObservation`/
      `nuisance_ds` という building block を既に提供しており、config 化は
      後続 PR の対象）。
    - `missing_failure_rate` は missing/ineligible/欠損 truth の instance
      割合。1 件も有効な instance が無ければ `eligible=False` を返す。
    """
    own_records = [r for r in records if r.candidate_id == candidate.candidate_id]
    per_instance_values: dict[tuple[str, int], list[float]] = {}
    missing_count = 0
    total_count = 0
    for r in own_records:
        total_count += 1
        value = measure_stage.primary_output_value(candidate, r.output)
        if value is None or not math.isfinite(value):
            missing_count += 1
            continue
        per_instance_values.setdefault((r.row_id, r.probe_index), []).append(value)

    missing_failure_rate = (missing_count / total_count) if total_count else 1.0

    truths: list[float] = []
    measured: list[float] = []
    errors: list[float] = []
    for key in sorted(per_instance_values):
        truth = truth_by_instance.get(key)
        if truth is None:
            continue
        m = float(np.mean(per_instance_values[key]))
        truths.append(truth)
        measured.append(m)
        errors.append((m - truth) / max(abs(truth), zero_guard))

    if not errors:
        return CandidateCriteria(
            candidate_id=candidate.candidate_id,
            eligible=False,
            complexity_rank=candidate.complexity_rank,
            missing_failure_rate=missing_failure_rate,
            ceiling=candidate.claim_ceiling,
        )

    normalized_mae = float(np.mean(np.abs(errors)))
    signed_bias = float(np.mean(errors))
    primary_q95_ae = q95([abs(e) for e in errors])

    kendall_tau = 0.0
    if len(truths) >= 2 and len(set(truths)) >= 2 and len(set(measured)) >= 2:
        tau, _p_value = kendalltau(truths, measured)
        if tau is not None and math.isfinite(float(tau)):
            kendall_tau = float(tau)

    order = sorted(range(len(truths)), key=lambda i: truths[i])
    reversal_pairs = max(len(order) - 1, 1)
    reversals = 0
    for a, b in zip(order, order[1:]):
        delta_truth = truths[b] - truths[a]
        delta_measured = measured[b] - measured[a]
        if delta_truth != 0 and (delta_measured > 0) != (delta_truth > 0):
            reversals += 1
    adjacent_reversal_rate = reversals / reversal_pairs

    return CandidateCriteria(
        candidate_id=candidate.candidate_id,
        eligible=True,
        complexity_rank=candidate.complexity_rank,
        nuisance_sensitivity_max=0.0,
        missing_failure_rate=missing_failure_rate,
        ceiling=candidate.claim_ceiling,
        primary_normalized_mae=normalized_mae,
        signed_bias=signed_bias,
        primary_q95_ae=primary_q95_ae,
        kendall_tau=kendall_tau,
        adjacent_reversal_rate=adjacent_reversal_rate,
    )


__all__ = [
    "candidate_space_sha",
    "selection_rule_sha",
    "F0SelectionResult",
    "run_c3a_f0_selection",
    "SelectionFreezeResult",
    "run_c3b_selection",
    "truth_value_for_row",
    "build_candidate_criteria",
]
