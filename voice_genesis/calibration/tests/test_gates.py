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


def _inv_pairs(
    axis: str, n: int, *, ds: float = 0.0, e_use_i0: float = 1.0, e_use_ia: float = 1.0
) -> list[InvariancePair]:
    """axis 上に `n` 件の *distinct* pair_id を持つ InvariancePair を生成する。
    gate4' の重複 pair_id 検出（Codex レビュー 2026-09-01）を通過させるため、
    テストは同一観測を使い回すのではなく必ずこのヘルパーで一意な観測を作る。"""
    return [
        InvariancePair(pair_id=f"{axis}-p{i}", axis=axis, ds=ds, e_use_i0=e_use_i0, e_use_ia=e_use_ia)
        for i in range(n)
    ]


def test_absolute_gates_g_zero_passes_boundary() -> None:
    # G = ae+u_gt+u_num+u_rep+u_proc-e_use = 1.0+0+0+0+0-1.0 = 0.0 -> PASS 側
    instances = [_instance("i1", ae=1.0, e=1.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
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
    # gate4 (5 distinct pairs, identical ds/e_use): margin = 0.01+0.05+0.05-1.0 = -0.89 <= 0
    instances = [
        _instance("i1", ae=1.0, e=1.0, u_gt=0.2, u_num=0.1, e_use=2.0),
        _instance("i2", ae=0.5, e=-0.5, u_gt=0.2, u_num=0.1, e_use=2.0),
    ]
    invariance = {"axis1": _inv_pairs("axis1", 5, ds=0.01)}
    result = absolute_gates(
        instances,
        u_rep=0.05,
        u_proc=0.05,
        invariance_pairs_by_axis=invariance,
        declared_invariance_axes={"axis1"},
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
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
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
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
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
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 4)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate4_invariance is False
    assert "axis1" in "".join(result.failure_reasons)


def test_absolute_gates_invariance_duplicate_pair_id_fails() -> None:
    """[Codex レビュー 2026-09-01] regression: 同一観測 (同じ pair_id) を 5 回
    複製しても ">=5 pairs" を満たしたことにはならない。旧実装は
    `len(pairs) < 5` しか見ておらず、1 件の観測を 5 回カウントする水増しを
    通してしまっていた。"""
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    duplicated_observation = InvariancePair(
        pair_id="dup", axis="axis1", ds=0.0, e_use_i0=1.0, e_use_ia=1.0
    )
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": [duplicated_observation] * 5},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate4_invariance is False
    assert result.passed is False
    assert any("duplicate" in r for r in result.failure_reasons)


def test_absolute_gates_declared_axis_absent_from_pairs_fails() -> None:
    """[Codex レビュー 2026-09-01] regression: `declared_invariance_axes` は
    C0 で凍結した閉集合として走査するため、対応する pair が
    `invariance_pairs_by_axis` に 1 件もない軸は黙って消えず、明示的に
    `<5 pairs` として gate4' を FAIL させる。"""
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={},  # axis2 に対応する pair が 1 件もない
        declared_invariance_axes={"axis2"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate4_invariance is False
    assert result.passed is False
    assert "axis2" in "".join(result.failure_reasons)


def test_absolute_gates_invariance_axis_mismatch_fails() -> None:
    """[Codex レビュー 2026-09-01 P1] regression: `invariance_pairs_by_axis` の
    バケットキーは呼び出し側が組み立てるだけの辞書キーであり、各
    `InvariancePair.axis` と一致している保証はない。duration 軸の pair 5 件を
    "gain" バケットへ紛れ込ませても、旧実装はバケットキーだけを信頼して
    `len(pairs) >= 5` を満たすため gate4' を PASS させてしまっていた。新実装は
    `p.axis == axis` を検証し、不一致があれば gate4' を FAIL させる。"""
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    duration_pairs_under_gain_key = [
        InvariancePair(pair_id=f"dur-p{i}", axis="duration", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for i in range(5)
    ]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"gain": duration_pairs_under_gain_key},
        declared_invariance_axes={"gain"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate4_invariance is False
    assert result.passed is False
    assert any("mismatched" in r and "gain" in r for r in result.failure_reasons)


def test_absolute_gates_invariance_unknown_bucket_key_fails() -> None:
    """[Codex レビュー 2026-09-01 P1] regression: `invariance_pairs_by_axis` に
    `declared_invariance_axes` に含まれないバケットキーがあっても、旧実装は
    宣言済み軸集合のみを走査するため黙って無視していた。新実装はこれも
    gate4' FAIL として明示的に検出する。"""
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={
            "axis1": _inv_pairs("axis1", 5),
            "undeclared-axis": _inv_pairs("undeclared-axis", 5),
        },
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate4_invariance is False
    assert result.passed is False
    assert any("undeclared-axis" in r for r in result.failure_reasons)


def test_absolute_gates_gate5_detection_fails_when_fdr_nonzero() -> None:
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
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
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
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
            declared_invariance_axes=(),
            fdr0=0.0,
            fnr1=0.0,
            min_count_met=True,
        )


# ---------------------------------------------------------------------------
# DIRECTIONAL gates
# ---------------------------------------------------------------------------


def _pair(pair_id: str, delta_truth: float, delta_output: float, *, correct_sign: bool = True,
          is_adjacent: bool = True, u_gt_i: float = 0.1, u_num_i: float = 0.05,
          u_gt_j: float = 0.1, u_num_j: float = 0.05, sweep_id: str = "default") -> DirectionalPair:
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
        sweep_id=sweep_id,
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
        expected_sweep_ids={"default"},
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
        expected_sweep_ids={"default"},
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
        expected_sweep_ids={"default"},
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
        expected_sweep_ids={"default"},
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
        expected_sweep_ids={"default"},
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
        expected_sweep_ids={"default"},
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
        expected_sweep_ids={"default"},
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
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.adjacent_reversal_rate > 0.0
    assert result.passed is False


def test_directional_nonadjacent_wrong_sign_does_not_block_pass() -> None:
    """[Codex レビュー 2026-09-01] regression: §10.4 は「全 resolvable
    **adjacent** pair の正符号」を要求する。resolvable な non-adjacent pair
    の符号違反は pass 判定基準に数えてはならない（旧実装は non-adjacent の
    符号違反でも `all_correct=False` にしていた）。"""
    adjacent_ok = [
        _pair(f"adj{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, correct_sign=True)
        for i in range(3)
    ]
    nonadjacent_wrong = _pair(
        "nonadj-wrong", delta_truth=1.0, delta_output=1.0, is_adjacent=False, correct_sign=False
    )
    result = directional_gates(
        [*adjacent_ok, nonadjacent_wrong],
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert nonadjacent_wrong.pair_id in result.resolvable_pairs  # 記録はされる
    assert result.all_resolvable_correct_sign is True
    assert result.adjacent_reversal_rate == 0.0
    assert result.passed is True


def test_directional_tau_b_recorded_but_never_gates_pass() -> None:
    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False) for i in range(3)
    ]
    result_low_tau = directional_gates(
        pairs, u_rep=0.02, u_proc=0.01, expected_sweep_ids={"default"},
        negative_control_failures=0, positive_control_failures=0,
        units_commensurate=False, tau_b=0.01,
    )
    result_high_tau = directional_gates(
        pairs, u_rep=0.02, u_proc=0.01, expected_sweep_ids={"default"},
        negative_control_failures=0, positive_control_failures=0,
        units_commensurate=False, tau_b=0.99,
    )
    assert result_low_tau.passed == result_high_tau.passed
    assert result_low_tau.tau_b == pytest.approx(0.01)
    assert result_high_tau.tau_b == pytest.approx(0.99)


def test_directional_control_failures_block_pass() -> None:
    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False) for i in range(3)
    ]
    result = directional_gates(
        pairs, u_rep=0.02, u_proc=0.01, expected_sweep_ids={"default"},
        negative_control_failures=1,
        positive_control_failures=0, units_commensurate=False,
    )
    assert result.passed is False


def test_directional_duplicate_pair_id_within_sweep_fails() -> None:
    """[Codex レビュー 2026-09-01] regression: 同一 pair_id が同一 sweep 内で
    重複していると、resolvable 件数の水増しを防ぐため gate は FAIL する。"""
    pairs = [
        _pair("dup", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-A")
        for _ in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"sweep-A"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert any("duplicate" in r for r in result.failure_reasons)


# ---------------------------------------------------------------------------
# resolvable-pair minimum is PER SWEEP (Codex review 2026-09-01)
# ---------------------------------------------------------------------------


def test_directional_three_pairs_spread_over_three_sweeps_fails() -> None:
    """3 件の resolvable pair が 3 つの異なる sweep に 1 件ずつ分散していると、
    集約カウントでは 3 件で足りているように見えるが、各 sweep 単独では最低数
    (>= 3) を満たさないため gate は FAIL する。"""
    pairs = [
        _pair("p1", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-A"),
        _pair("p2", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-B"),
        _pair("p3", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-C"),
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"sweep-A", "sweep-B", "sweep-C"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.resolvable_count == 3  # aggregate count looks sufficient...
    assert result.passed is False  # ...but no single sweep reaches the minimum
    assert set(result.sweeps_below_minimum) == {"sweep-A", "sweep-B", "sweep-C"}
    assert result.sweep_resolvable_counts == {"sweep-A": 1, "sweep-B": 1, "sweep-C": 1}
    assert result.sweeps_with_warning == ()


def test_directional_one_sweep_with_exactly_three_pairs_passes_with_warning() -> None:
    """全ての resolvable pair (3 件) が単一 sweep に属していれば、その sweep は
    最低数を満たし gate は通過するが、「ちょうど 3」の警告がその sweep に
    立つ。"""
    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-A")
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"sweep-A"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is True
    assert result.sweep_resolvable_counts == {"sweep-A": 3}
    assert result.sweeps_with_warning == ("sweep-A",)
    assert result.sweeps_below_minimum == ()
    assert result.three_pair_warning is True


def test_directional_multi_sweep_each_meeting_minimum_passes() -> None:
    """複数 sweep それぞれが独立に最低数 (>= 3) を満たせば gate は通過する。"""
    pairs = [
        _pair(f"a{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-A")
        for i in range(3)
    ] + [
        _pair(f"b{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-B")
        for i in range(4)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"sweep-A", "sweep-B"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is True
    assert result.sweeps_below_minimum == ()
    assert result.sweep_resolvable_counts == {"sweep-A": 3, "sweep-B": 4}
    assert result.sweeps_with_warning == ("sweep-A",)  # sweep-B has 4, not exactly 3


def test_directional_expected_sweep_with_no_observed_pairs_fails() -> None:
    """[Codex レビュー 2026-09-01] regression: `expected_sweep_ids` で宣言した
    2 sweep のうち 1 つに observed pair が 1 件もない場合、frozen closed-set
    原則により黙って消えず明示的に FAIL する（NOT_EVALUABLE 側へ写像される
    べき欠落）。"""
    pairs = [
        _pair(f"a{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-A")
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"sweep-A", "sweep-B"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert "sweep-B" in result.sweeps_below_minimum
    assert result.sweep_resolvable_counts["sweep-B"] == 0
