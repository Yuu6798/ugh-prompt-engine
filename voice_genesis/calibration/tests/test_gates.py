from __future__ import annotations

import pytest

from voice_genesis.calibration.gates import (
    DirectionalPair,
    EUseEvidenceRow,
    InstanceMargin,
    InvariancePair,
    absolute_gates,
    auto_ceiling_for_unjustified,
    directional_gates,
    threshold_margin,
)
from voice_genesis.calibration.vocab import ClaimCeiling, Domain, EvidenceClass


def test_threshold_margin_hand_computed() -> None:
    assert threshold_margin(e_use=1.0, u_gt=0.6, u_num=0.5) == pytest.approx(-0.1)


def test_threshold_margin_not_evaluable_boundary() -> None:
    m = threshold_margin(e_use=1.0, u_gt=0.6, u_num=0.4)
    assert m <= 0  # ちょうど 0 でも ABSOLUTE は NOT_EVALUABLE


def test_e_use_evidence_row_unjustified_with_numeric_value_raises() -> None:
    with pytest.raises(ValueError):
        EUseEvidenceRow(
            construct_id="c1",
            unit="Hz",
            domain="PRIMARY",
            intended_use="x",
            maximum_claim="ABSOLUTE",
            e_use_value=1.0,
            derivation_rule="none",
            evidence_class=EvidenceClass.UNJUSTIFIED,
            source_id_or_url="",
            source_checked_at="",
            source_hash_or_version="",
            applicability_argument="",
            review_status="draft",
        )


def test_e_use_evidence_row_unjustified_without_value_ok() -> None:
    row = EUseEvidenceRow(
        construct_id="c1",
        unit="Hz",
        domain="PRIMARY",
        intended_use="x",
        maximum_claim="DIAGNOSTIC_ONLY",
        e_use_value=None,
        derivation_rule="none",
        evidence_class=EvidenceClass.UNJUSTIFIED,
        source_id_or_url="",
        source_checked_at="",
        source_hash_or_version="",
        applicability_argument="",
        review_status="draft",
    )
    assert row.e_use_value is None


def test_auto_ceiling_for_unjustified() -> None:
    assert auto_ceiling_for_unjustified(True) == ClaimCeiling.DIRECTIONAL
    assert auto_ceiling_for_unjustified(False) == ClaimCeiling.DIAGNOSTIC_ONLY


# ---------------------------------------------------------------------------
# ABSOLUTE gates
# ---------------------------------------------------------------------------


def _instance(instance_id: str, ae: float, e: float, u_gt: float, u_num: float, e_use: float) -> InstanceMargin:
    return InstanceMargin(
        instance_id=instance_id,
        domain=Domain.PRIMARY,
        eligible=True,
        ae=ae,
        e=e,
        u_gt=u_gt,
        u_num=u_num,
        e_use=e_use,
    )


def test_absolute_gates_g_zero_passes_boundary() -> None:
    # G = ae+u_gt+u_num+u_rep+u_proc-e_use = 1.0+0+0+0+0-1.0 = 0.0 -> PASS 側
    instances = [_instance("i1", ae=1.0, e=1.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={
            "axis1": [InvariancePair("axis1", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)] * 5
        },
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.g_values == (0.0,)
    assert result.gate2_q95 is True
    assert result.gate_max is True


def test_absolute_gates_hand_computed_full_pass() -> None:
    # 手計算 (docstring 参照):
    # i1: G = 1.0+0.2+0.1+0.05+0.05-2.0 = 1.4-2.0 = -0.6
    # i2: G = 0.5+0.2+0.1+0.05+0.05-2.0 = 0.9-2.0 = -1.1
    # q95([-0.6,-1.1]) linear: sorted=[-1.1,-0.6], n=2, rank=0.95*(2-1)=0.95
    #   floor=0(-1.1), frac=0.95 -> -1.1+0.95*(-0.6-(-1.1)) = -1.1+0.475 = -0.625
    # gate3: bias=mean([1.0,-0.5])=0.25, |bias|=0.25
    #   max(u_gt+u_num)=max(0.3,0.3)=0.3, median(e_use)=median([2.0,2.0])=2.0
    #   lhs = 0.25+0.3+0.05+0.05 = 0.65 <= 2.0 -> True
    # gate4 (5 pairs, all identical): margin = 0.01+0.05+0.05-1.0 = -0.89 <= 0
    instances = [
        _instance("i1", ae=1.0, e=1.0, u_gt=0.2, u_num=0.1, e_use=2.0),
        _instance("i2", ae=0.5, e=-0.5, u_gt=0.2, u_num=0.1, e_use=2.0),
    ]
    invariance = {
        "axis1": [InvariancePair("axis1", ds=0.01, e_use_i0=1.0, e_use_ia=1.0) for _ in range(5)]
    }
    result = absolute_gates(
        instances,
        u_rep=0.05,
        u_proc=0.05,
        invariance_pairs_by_axis=invariance,
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.g_values[0] == pytest.approx(-0.6)
    assert result.g_values[1] == pytest.approx(-1.1)
    assert result.gate2_q95 is True  # q95=-0.625 <=0
    assert result.gate_max is True
    assert result.gate3_bias_budget is True
    assert result.gate4_invariance is True
    assert result.gate5_detection is True
    assert result.passed is True


def test_absolute_gates_gate3_arithmetic_fails_hand_computed() -> None:
    # bias=mean([1.0])=1.0, |bias|=1.0; max(u_gt+u_num)=0.1; u_rep+u_proc=0.0
    # lhs = 1.0+0.1+0.0 = 1.1; median(e_use)=1.0 -> 1.1 <= 1.0 は False
    instances = [_instance("i1", ae=1.0, e=1.0, u_gt=0.05, u_num=0.05, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={
            "axis1": [InvariancePair("axis1", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)] * 5
        },
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate3_bias_budget is False
    assert result.passed is False


def test_absolute_gates_ineligible_primary_fails_gate1() -> None:
    ineligible = InstanceMargin(
        instance_id="bad", domain=Domain.PRIMARY, eligible=False,
        ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0,
    )
    good = _instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)
    result = absolute_gates(
        [ineligible, good],
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={
            "axis1": [InvariancePair("axis1", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)] * 5
        },
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate1_all_eligible is False
    assert result.passed is False


def test_absolute_gates_invariance_axis_needs_5_pairs() -> None:
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={
            "axis1": [InvariancePair("axis1", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)] * 4
        },
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate4_invariance is False
    assert "axis1" in "".join(result.failure_reasons)


def test_absolute_gates_gate5_detection_fails_when_fdr_nonzero() -> None:
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={
            "axis1": [InvariancePair("axis1", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)] * 5
        },
        fdr0=0.1,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate5_detection is False


def test_absolute_gates_gate5_not_applicable_passthrough() -> None:
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={
            "axis1": [InvariancePair("axis1", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)] * 5
        },
        fdr0=0.9,
        fnr1=0.9,
        min_count_met=False,
        control_gate="NOT_APPLICABLE",
    )
    assert result.gate5_detection is True


def test_absolute_gates_no_primary_instance_raises() -> None:
    boundary_only = InstanceMargin(
        instance_id="b1", domain=Domain.BOUNDARY, eligible=True,
        ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0,
    )
    with pytest.raises(ValueError):
        absolute_gates(
            [boundary_only],
            u_rep=0.0,
            u_proc=0.0,
            invariance_pairs_by_axis={},
            fdr0=0.0,
            fnr1=0.0,
            min_count_met=True,
        )


# ---------------------------------------------------------------------------
# DIRECTIONAL gates
# ---------------------------------------------------------------------------


def _pair(pair_id: str, delta_truth: float, delta_output: float, *, correct_sign: bool = True,
          is_adjacent: bool = True, u_gt_i: float = 0.1, u_num_i: float = 0.05,
          u_gt_j: float = 0.1, u_num_j: float = 0.05) -> DirectionalPair:
    return DirectionalPair(
        pair_id=pair_id,
        delta_truth=delta_truth,
        delta_output=delta_output,
        u_gt_i=u_gt_i,
        u_num_i=u_num_i,
        u_gt_j=u_gt_j,
        u_num_j=u_num_j,
        correct_sign=correct_sign,
        is_adjacent=is_adjacent,
    )


def test_directional_resolvable_exact_equality_is_not_resolvable() -> None:
    # r_truth = 0.15+0.15 = 0.3; u_rep+u_proc=0.03 -> r_combined = 0.3+0.06=0.36
    # delta_truth == r_combined (等号) -> strict > を満たさず not resolvable
    # (units_commensurate=True で v1.0 合算式相当を課したケース)
    p = _pair("p1", delta_truth=0.36, delta_output=1.0)  # (a),(b) は通過
    result = directional_gates(
        [p],
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=True,
    )
    assert result.resolvable_count == 0
    assert p.pair_id not in result.resolvable_pairs


def test_directional_resolvable_strictly_greater_is_resolvable() -> None:
    p = _pair("p1", delta_truth=0.37, delta_output=1.0)
    result = directional_gates(
        [p],
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=True,
    )
    assert result.resolvable_count == 1
    assert "p1" in result.resolvable_pairs


def test_directional_units_incommensurate_truth_passes_output_fails() -> None:
    """[Codex レビュー 2026-09-01 第 3 巡] オラクル 1: 単位非可換ケースで
    (a) truth 側は通過するが (b) output 側有意性が不通過 -> not resolvable。

    r_truth = 0.15+0.15 = 0.3。delta_truth=0.31 > 0.3 -> (a) 通過。
    2*(u_rep+u_proc) = 2*(0.02+0.01) = 0.06。delta_output=0.05, |0.05|<=0.06
    -> (b) 不通過。units_commensurate=False なので合算式 (c) は課さないが、
    (b) 不通過だけで resolvable にならないことを確認する。
    """
    p = _pair("p1", delta_truth=0.31, delta_output=0.05)
    result = directional_gates(
        [p],
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.resolvable_count == 0


def test_directional_units_commensurate_two_conjuncts_pass_but_combined_fails() -> None:
    """[Codex レビュー 2026-09-01 第 3 巡] オラクル 2: 可換ケースで (a)/(b) の
    二連言は通るが、v1.0 合算式 (c) で落ちる -> not resolvable。

    r_truth=0.3, delta_truth=0.33 > 0.3 -> (a) 通過。
    delta_output=0.5, |0.5| > 0.06 -> (b) 通過。
    r_combined = 0.3+0.06 = 0.36。delta_truth=0.33 <= 0.36 -> (c) 不通過。
    units_commensurate=True なので (c) も必須 -> 全体として not resolvable。
    """
    p = _pair("p1", delta_truth=0.33, delta_output=0.5)
    result = directional_gates(
        [p],
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=True,
    )
    assert result.resolvable_count == 0


def test_directional_units_incommensurate_same_pair_would_resolve_without_combined_formula() -> None:
    # 同じ (a)+(b) 通過ケースで units_commensurate=False なら合算式 (c) を課さない
    # ため resolvable になる（第 3 巡オラクル 2 との対比）。
    p = _pair("p1", delta_truth=0.33, delta_output=0.5)
    result = directional_gates(
        [p],
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.resolvable_count == 1


def test_directional_exactly_three_pairs_sets_warning_flag() -> None:
    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False)
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.resolvable_count == 3
    assert result.three_pair_warning is True


def test_directional_fewer_than_three_pairs_fails() -> None:
    pairs = [_pair("p1", delta_truth=1.0, delta_output=1.0)]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert result.resolvable_count == 1


def test_directional_adjacent_reversal_fails() -> None:
    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, correct_sign=True)
        for i in range(3)
    ]
    # 1 件だけ符号反転
    pairs[0] = _pair("p0", delta_truth=1.0, delta_output=1.0, is_adjacent=True, correct_sign=False)
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.adjacent_reversal_rate > 0.0
    assert result.passed is False


def test_directional_tau_b_recorded_but_never_gates_pass() -> None:
    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False) for i in range(3)
    ]
    result_low_tau = directional_gates(
        pairs, u_rep=0.02, u_proc=0.01, negative_control_failures=0,
        positive_control_failures=0, units_commensurate=False, tau_b=0.01,
    )
    result_high_tau = directional_gates(
        pairs, u_rep=0.02, u_proc=0.01, negative_control_failures=0,
        positive_control_failures=0, units_commensurate=False, tau_b=0.99,
    )
    assert result_low_tau.passed == result_high_tau.passed
    assert result_low_tau.tau_b == pytest.approx(0.01)
    assert result_high_tau.tau_b == pytest.approx(0.99)


def test_directional_control_failures_block_pass() -> None:
    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False) for i in range(3)
    ]
    result = directional_gates(
        pairs, u_rep=0.02, u_proc=0.01, negative_control_failures=1,
        positive_control_failures=0, units_commensurate=False,
    )
    assert result.passed is False
