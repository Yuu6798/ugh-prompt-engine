from __future__ import annotations

import math

import pytest

from voice_genesis.calibration.gates import (
    DirectionalPair,
    EUseEvidenceRow,
    InstanceMargin,
    InvariancePair,
    MIN_RESOLVABLE_PAIRS_PER_SWEEP,
    absolute_gates as _absolute_gates_impl,
    auto_ceiling_for_unjustified,
    directional_gates,
    resolvable_pairs_possible,
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
# EUseEvidenceRow.e_use_mode (`[UNDERSPEC-CAL-D11]`, Part B — absolute/relative)
# ---------------------------------------------------------------------------


def test_e_use_evidence_row_e_use_mode_defaults_to_absolute() -> None:
    row = EUseEvidenceRow(
        construct_id="c1",
        unit="hz",
        domain="PRIMARY",
        intended_use="x",
        maximum_claim="ABSOLUTE",
        e_use_value=1.0,
        derivation_rule="none",
        evidence_class=EvidenceClass.NORMATIVE_SPEC,
        source_id_or_url="",
        source_checked_at="",
        source_hash_or_version="",
        applicability_argument="",
        review_status="draft",
    )
    assert row.e_use_mode == "absolute"


def test_e_use_evidence_row_e_use_mode_relative_accepted() -> None:
    row = EUseEvidenceRow(
        construct_id="c1",
        unit="hz",
        domain="PRIMARY",
        intended_use="x",
        maximum_claim="ABSOLUTE",
        e_use_value=0.05,
        derivation_rule="5% of declared truth",
        evidence_class=EvidenceClass.USER_ACCEPTED_USE_BOUND,
        source_id_or_url="",
        source_checked_at="",
        source_hash_or_version="",
        applicability_argument="",
        review_status="draft",
        e_use_mode="relative",
    )
    assert row.e_use_mode == "relative"


def test_e_use_evidence_row_e_use_mode_invalid_value_rejected() -> None:
    with pytest.raises(ValueError):
        EUseEvidenceRow(
            construct_id="c1",
            unit="hz",
            domain="PRIMARY",
            intended_use="x",
            maximum_claim="ABSOLUTE",
            e_use_value=1.0,
            derivation_rule="none",
            evidence_class=EvidenceClass.NORMATIVE_SPEC,
            source_id_or_url="",
            source_checked_at="",
            source_hash_or_version="",
            applicability_argument="",
            review_status="draft",
            e_use_mode="percent",
        )


# ---------------------------------------------------------------------------
# ABSOLUTE gates
# ---------------------------------------------------------------------------


def _instance(
    instance_id: str, ae: float, e: float, u_gt: float, u_num: float, e_use: float
) -> InstanceMargin:
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
        InvariancePair(
            pair_id=f"{axis}-p{i}", axis=axis, ds=ds, e_use_i0=e_use_i0, e_use_ia=e_use_ia
        )
        for i in range(n)
    ]


def absolute_gates(per_instance, **kwargs):
    """Test adapter: ordinary arithmetic tests declare their supplied PRIMARY set as frozen."""
    kwargs.setdefault(
        "expected_primary_instance_ids",
        tuple(i.instance_id for i in per_instance if i.domain == Domain.PRIMARY),
    )
    return _absolute_gates_impl(per_instance, **kwargs)


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
        instance_id="bad",
        domain=Domain.PRIMARY,
        eligible=False,
        ae=0.0,
        e=0.0,
        u_gt=0.0,
        u_num=0.0,
        e_use=1.0,
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


def test_absolute_gates_duplicate_primary_instance_id_fails() -> None:
    """[Codex レビュー 2026-09-01 P1] regression: 同一 instance_id を持つ
    high-E_use instance を複製すると、旧実装では identity を見ずに集計する
    ため gate3 の判定 (bias/median(E_use)) が反転してしまっていた。新実装は
    duplicate instance_id を検出して gate1 を FAIL させ、全体を確実に FAIL
    させる。"""
    # i1 単独だと gate3: bias=1.0, max(u_gt+u_num)=0.1, median(e_use)=2.0
    #   lhs=1.1 <= 2.0 -> True (gate3 PASS のはずのケース)
    high_e_use = _instance("i1", ae=1.0, e=1.0, u_gt=0.05, u_num=0.05, e_use=2.0)
    result_without_duplicate = absolute_gates(
        [high_e_use],
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result_without_duplicate.gate3_bias_budget is True

    # 同一 instance_id "i1" を複製 (水増し) しても、新実装は duplicate として
    # 検出し gate1 を FAIL させる (旧実装は集計だけを歪め見逃していた)。
    duplicated = InstanceMargin(
        instance_id="i1",
        domain=Domain.PRIMARY,
        eligible=True,
        ae=1.0,
        e=1.0,
        u_gt=0.05,
        u_num=0.05,
        e_use=2.0,
    )
    result = absolute_gates(
        [high_e_use, duplicated],
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
    assert any("duplicate" in r and "i1" in r for r in result.failure_reasons)


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


def test_absolute_gates_runtime_not_applicable_cannot_bypass_frozen_controls() -> None:
    """All frozen fixture families declare control_gate=APPLICABLE.  A runtime
    NOT_APPLICABLE argument must therefore fail closed instead of bypassing FDR/FNR
    and minimum-count checks."""
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
    assert result.gate5_detection is False
    assert result.passed is False
    assert any("not authorized" in reason for reason in result.failure_reasons)


def test_absolute_gates_nan_ds_in_invariance_pair_fails_gate4() -> None:
    """[P1] regression (gates.py:226): gate4' の制御フローは `if
    q95(margins) > 0: gate4 = False` で、`gate4` の初期値は `True` のため
    `NaN > 0`（常に `False`）だとこの if 節を素通りし、gate4' が誤って
    PASS になっていた。1 件でも `ds=NaN` の InvariancePair があれば gate4'
    は明示的に FAIL する。"""
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    pairs = _inv_pairs("axis1", 5, ds=0.0)
    # 1 件だけ ds を NaN にする（残り 4 件は正常値）。
    nan_pair = InvariancePair(
        pair_id="axis1-p0", axis="axis1", ds=float("nan"), e_use_i0=1.0, e_use_ia=1.0
    )
    pairs[0] = nan_pair
    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": pairs},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate4_invariance is False
    assert result.passed is False
    assert any("non-finite" in r and "axis1" in r for r in result.failure_reasons)


def test_absolute_gates_nan_ae_fails_gate2() -> None:
    """[P1] regression (gates.py:226 横展開): `InstanceMargin.ae` が NaN だと
    `G[i]` が NaN になる。gate2' は `q95_i(G[i]) <= 0` の直接代入なので
    `NaN <= 0` は `False` となり元々 FAIL していたが、本修正は非有限入力を
    明示的な typed reason で検出する（集計関数へ非有限値を渡さない）。"""
    instances = [
        InstanceMargin(
            instance_id="i1",
            domain=Domain.PRIMARY,
            eligible=True,
            ae=float("nan"),
            e=0.0,
            u_gt=0.0,
            u_num=0.0,
            e_use=1.0,
        )
    ]
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
    assert result.gate2_q95 is False
    assert result.passed is False
    assert any("non-finite" in r and "gate2'" in r for r in result.failure_reasons)


def test_absolute_gates_gate_max_nan_element_not_silently_dropped() -> None:
    """[P1] regression: Python 組込 `max()` は NaN の比較が常に `False` に
    なる副作用で、走査順序次第では NaN 要素を無言で読み飛ばし有限な最大値を
    返してしまう（`max([1.0, nan]) == 1.0`）。i2（NaN 由来の G[i]）が i1 の
    **後**に来ても、`max(g_values)` に頼らず明示的な有限性検査で FAIL する
    ことを確認する。"""
    instances = [
        _instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0),  # G = -1.0
        InstanceMargin(
            instance_id="i2",
            domain=Domain.PRIMARY,
            eligible=True,
            ae=float("nan"),
            e=0.0,
            u_gt=0.0,
            u_num=0.0,
            e_use=1.0,
        ),
    ]
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
    assert result.gate_max is False
    assert result.passed is False
    assert any("non-finite" in r and "gate_max'" in r for r in result.failure_reasons)


def test_absolute_gates_gate3_nan_u_gt_num_not_silently_dropped_by_max() -> None:
    """[P1] regression: gate3 の `max_u_gt_num` も Python 組込 `max()` を
    使うため、同じ順序依存の NaN 読み飛ばしバグを抱えていた。i1 (finite) の
    後に NaN な (u_gt+u_num) を持つ i2 が続いても gate3 は明示的に FAIL する。
    """
    instances = [
        _instance("i1", ae=0.0, e=0.0, u_gt=0.05, u_num=0.05, e_use=10.0),
        InstanceMargin(
            instance_id="i2",
            domain=Domain.PRIMARY,
            eligible=True,
            ae=0.0,
            e=0.0,
            u_gt=float("nan"),
            u_num=0.0,
            e_use=10.0,
        ),
    ]
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
    assert any("non-finite" in r and "gate3" in r for r in result.failure_reasons)


def test_absolute_gates_no_primary_instance_raises() -> None:
    boundary_only = InstanceMargin(
        instance_id="b1",
        domain=Domain.BOUNDARY,
        eligible=True,
        ae=0.0,
        e=0.0,
        u_gt=0.0,
        u_num=0.0,
        e_use=1.0,
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


def _pair(
    pair_id: str,
    delta_truth: float,
    delta_output: float,
    *,
    correct_sign: bool = True,
    is_adjacent: bool = True,
    u_gt_i: float = 0.1,
    u_num_i: float = 0.05,
    u_gt_j: float = 0.1,
    u_num_j: float = 0.05,
    sweep_id: str = "default",
) -> DirectionalPair:
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


_directional_gates_impl = directional_gates


def directional_gates(pairs, **kwargs):
    """Test adapter: make each fixture's intended adjacency an explicit declaration."""
    if "expected_adjacent_pair_ids" not in kwargs:
        expected_sweeps = set(kwargs.get("expected_sweep_ids", ()))
        kwargs["expected_adjacent_pair_ids"] = {
            sweep: tuple(
                sorted(
                    p.pair_id
                    for p in pairs
                    if p.sweep_id == sweep and p.is_adjacent
                )
            )
            for sweep in expected_sweeps
        }
    return _directional_gates_impl(pairs, **kwargs)


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


def test_directional_units_incommensurate_same_pair_would_resolve_without_combined_formula() -> (
    None
):
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
    pairs = [_pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False) for i in range(3)]
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
    # 1 件だけ measured delta を符号反転。caller metadata は故意に True のままにし、
    # gate が boolean を信頼せず recorded deltas から符号を導出することを固定する。
    pairs[0] = _pair("p0", delta_truth=1.0, delta_output=-1.0, is_adjacent=True, correct_sign=True)
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
        "nonadj-wrong", delta_truth=1.0, delta_output=-1.0, is_adjacent=False, correct_sign=True
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
    pairs = [_pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False) for i in range(3)]
    result_low_tau = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
        tau_b=0.01,
    )
    result_high_tau = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
        tau_b=0.99,
    )
    assert result_low_tau.passed == result_high_tau.passed
    assert result_low_tau.tau_b == pytest.approx(0.01)
    assert result_high_tau.tau_b == pytest.approx(0.99)


def test_directional_control_failures_block_pass() -> None:
    pairs = [_pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False) for i in range(3)]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"default"},
        negative_control_failures=1,
        positive_control_failures=0,
        units_commensurate=False,
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
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, sweep_id="sweep-A")
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
        _pair(f"a{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, sweep_id="sweep-A")
        for i in range(3)
    ] + [
        _pair(f"b{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, sweep_id="sweep-B")
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


def test_directional_full_coverage_with_real_frozen_sweep_ids_passes() -> None:
    """UNDERSPEC-CAL-D76 (supersedes D75 ruling (1)): `directional_gates()`
    itself is agnostic to what the `sweep_id` strings mean — the tests above
    already exercise the per-sweep minimum with arbitrary names — but this
    test wires in the *real* declared sweep set a production family
    actually gets (`fixtures.matrix.declared_sweeps_by_family()`, def A:
    truth-core block, nuisance-constant series), replacing the fabricated
    `"default"` sweep D74 left in place and D75's (incorrect, nuisance-axis)
    sweep ids. A full-coverage synthetic record set (3 resolvable pairs per
    declared sweep) for `TILT_GT` (6 declared sweeps under def A) can
    PASS."""
    from voice_genesis.calibration.fixtures.matrix import build_matrix, declared_sweeps_by_family

    declared = declared_sweeps_by_family(build_matrix())["TILT_GT"]
    assert len(declared) == 6, sorted(declared)

    pairs = [
        _pair(f"{sweep_id}-{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, sweep_id=sweep_id)
        for sweep_id in declared
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids=set(declared),
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is True
    assert result.sweeps_below_minimum == ()
    assert result.sweep_resolvable_counts == {sweep_id: 3 for sweep_id in declared}


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


def test_directional_duplicate_pair_id_across_sweeps_fails() -> None:
    pairs = [
        _pair(
            f"p{i}",
            delta_truth=1.0,
            delta_output=1.0,
            is_adjacent=False,
            sweep_id="sweep-A",
        )
        for i in range(3)
    ] + [
        _pair(
            f"p{i}",
            delta_truth=1.0,
            delta_output=1.0,
            is_adjacent=False,
            sweep_id="sweep-B",
        )
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
    assert result.sweep_resolvable_counts == {"sweep-A": 3, "sweep-B": 3}
    assert result.passed is False
    assert any("duplicate pair_id" in reason for reason in result.failure_reasons)


@pytest.mark.parametrize(
    ("field", "value"),
    [("ae", -1.0), ("u_gt", -1.0), ("u_num", -1.0), ("e_use", 0.0)],
)
def test_absolute_gates_reject_invalid_primary_budget_values(field: str, value: float) -> None:
    values = dict(ae=0.1, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)
    values[field] = value
    instance = _instance("invalid-budget", **values)
    result = absolute_gates(
        [instance],
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.passed is False
    assert any("gate budgets" in reason for reason in result.failure_reasons)


@pytest.mark.parametrize(("u_rep", "u_proc"), [(-0.1, 0.0), (0.0, -0.1)])
def test_absolute_gates_reject_negative_global_uncertainty(u_rep: float, u_proc: float) -> None:
    result = absolute_gates(
        [_instance("i1", ae=0.1, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)],
        u_rep=u_rep,
        u_proc=u_proc,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.passed is False
    assert any("U_rep/U_proc" in reason for reason in result.failure_reasons)


def test_absolute_gates_negative_uncertainty_cannot_cancel_large_error() -> None:
    exploit = _instance("exploit", ae=10.0, e=0.0, u_gt=-100.0, u_num=0.0, e_use=1.0)
    result = absolute_gates(
        [exploit],
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.passed is False
    assert result.gate2_q95 is False


def test_directional_gates_reject_negative_uncertainty_budget() -> None:
    pairs = [
        _pair(
            f"p{i}",
            delta_truth=1.0,
            delta_output=1.0,
            u_gt_i=-100.0,
            is_adjacent=True,
        )
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert result.resolvable_count == 0
    assert any("directional budgets" in reason for reason in result.failure_reasons)


def test_directional_sign_is_derived_from_measured_deltas_not_flag() -> None:
    pairs = [
        _pair(
            f"p{i}",
            delta_truth=1.0,
            delta_output=-1.0,
            correct_sign=True,
            is_adjacent=True,
        )
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.resolvable_count == 3
    assert result.all_resolvable_correct_sign is False
    assert result.adjacent_reversal_rate == 1.0
    assert result.passed is False


def test_absolute_gates_invariance_pair_id_reused_across_axes_fails() -> None:
    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]
    shared_ids = [f"shared-{i}" for i in range(5)]
    axis_a = [
        InvariancePair(pair_id=pid, axis="axis-a", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for pid in shared_ids
    ]
    axis_b = [
        InvariancePair(pair_id=pid, axis="axis-b", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)
        for pid in shared_ids
    ]

    result = absolute_gates(
        instances,
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis-a": axis_a, "axis-b": axis_b},
        declared_invariance_axes={"axis-a", "axis-b"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )

    assert result.gate4_invariance is False
    assert result.passed is False
    assert any(
        "reused across invariance axes" in reason and "shared-0" in reason
        for reason in result.failure_reasons
    )



def test_directional_caller_cannot_hide_reversals_with_nonadjacent_flags() -> None:
    pairs = [
        _pair(
            f"hide-{i}",
            delta_truth=1.0,
            delta_output=-1.0,
            correct_sign=True,
            is_adjacent=False,
        )
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"default"},
        expected_adjacent_pair_ids={"default": {"hide-0", "hide-1", "hide-2"}},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.resolvable_count == 3
    assert result.adjacent_reversal_rate == 1.0
    assert result.all_resolvable_correct_sign is False
    assert result.passed is False
    assert any("is_adjacent" in reason for reason in result.failure_reasons)


def test_directional_missing_frozen_adjacency_declaration_fails_closed() -> None:
    pairs = [_pair(f"decl-{i}", delta_truth=1.0, delta_output=1.0) for i in range(3)]
    result = _directional_gates_impl(
        pairs,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert any("frozen adjacent-pair declaration" in reason for reason in result.failure_reasons)


def test_absolute_gates_rejects_ae_signed_error_mismatch() -> None:
    instances = [
        _instance("ae-mismatch-pos", ae=0.0, e=100.0, u_gt=0.0, u_num=0.0, e_use=1.0),
        _instance("ae-mismatch-neg", ae=0.0, e=-100.0, u_gt=0.0, u_num=0.0, e_use=1.0),
    ]
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

    assert result.gate1_all_eligible is False
    assert result.g_values == ()
    assert result.passed is False
    assert any("AE must equal abs(e)" in reason for reason in result.failure_reasons)


# ---------------------------------------------------------------------------
# Review regressions: frozen gate populations
# ---------------------------------------------------------------------------


def test_absolute_gates_fails_when_frozen_primary_instance_is_relabelled_boundary() -> None:
    good = _instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)
    relabelled = InstanceMargin(
        instance_id="i2",
        domain=Domain.BOUNDARY,
        eligible=False,
        ae=100.0,
        e=100.0,
        u_gt=0.0,
        u_num=0.0,
        e_use=1.0,
    )
    result = absolute_gates(
        [good, relabelled],
        expected_primary_instance_ids={"i1", "i2"},
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate1_all_eligible is False
    assert result.g_values == ()
    assert result.passed is False
    assert any("PRIMARY instance set" in reason and "i2" in reason for reason in result.failure_reasons)


def test_absolute_gates_without_frozen_primary_declaration_fails_closed() -> None:
    result = _absolute_gates_impl(
        [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)],
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate1_all_eligible is False
    assert result.g_values == ()
    assert result.passed is False
    assert any("no frozen PRIMARY" in reason for reason in result.failure_reasons)


def test_directional_gates_rejects_observations_from_undeclared_sweep() -> None:
    declared = [
        _pair(
            f"declared-{i}",
            delta_truth=1.0,
            delta_output=1.0,
            sweep_id="declared",
            is_adjacent=True,
        )
        for i in range(3)
    ]
    undeclared = [
        _pair(
            f"hidden-{i}",
            delta_truth=1.0,
            delta_output=-1.0,
            sweep_id="hidden",
            is_adjacent=True,
        )
        for i in range(3)
    ]
    result = directional_gates(
        declared + undeclared,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"declared"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert any("undeclared sweep" in reason and "hidden" in reason for reason in result.failure_reasons)


# ---------------------------------------------------------------------------
# resolvable_pairs_possible() (#344 round 4 ADOPT, `[UNDERSPEC-CAL-D74]`,
# amends round 3's `[UNDERSPEC-CAL-D73]`): structural per-sweep precondition
# check, mirroring directional_gates()'s own per-sweep partitioning
# (`sweep_resolvable_counts`/`sweeps_below_minimum`) but from coverage
# counts alone (no real delta_truth/delta_output values required).
# ---------------------------------------------------------------------------


def test_resolvable_pairs_possible_three_instances_spread_over_three_sweeps_fails() -> None:
    """D74 の核心例: 3 件の usable PRIMARY instance が meter 全体では
    `C(3,2)=3 >= MIN_RESOLVABLE_PAIRS_PER_SWEEP` を満たしているように見えるが、
    3 つの異なる宣言 sweep に 1 件ずつ分散していれば、各 sweep 単独では
    `C(1,2)=0` で最低数を満たさない——`directional_gates()` の per-sweep 判定
    と同じく、宣言済み全 sweep が独立に基準を満たさなければならない（sweep 間
    で集約した合計では不十分）。round 3 の旧実装
    （`math.comb(n_total, 2) >= MIN_RESOLVABLE_PAIRS_PER_SWEEP`、meter 全体の
    集約カウント）はこのケースを誤って「構造的に達成可能」と判定していた。"""
    assert (
        resolvable_pairs_possible(
            {"sweep-A": 1, "sweep-B": 1, "sweep-C": 1},
            {"sweep-A", "sweep-B", "sweep-C"},
        )
        is False
    )


def test_resolvable_pairs_possible_one_sweep_with_enough_instances_passes() -> None:
    """全 usable PRIMARY instance (3 件) が単一の宣言 sweep に属していれば、
    その sweep は `C(3,2)=3 >= MIN_RESOLVABLE_PAIRS_PER_SWEEP` を満たすため
    `True`（旧・round 3 実装と同じ「1 sweep に集中していれば足りる」結果）。"""
    assert resolvable_pairs_possible({"sweep-A": 3}, {"sweep-A"}) is True


def test_resolvable_pairs_possible_multi_sweep_each_meeting_minimum_passes() -> None:
    """複数の宣言 sweep がそれぞれ独立に最低数を満たせば `True`。"""
    assert (
        resolvable_pairs_possible(
            {"sweep-A": 3, "sweep-B": 4},
            {"sweep-A", "sweep-B"},
        )
        is True
    )


def test_resolvable_pairs_possible_multi_sweep_one_below_minimum_fails() -> None:
    """宣言済み sweep のうち 1 つでも最低数を満たさなければ全体で `False`
    （§10.4「resolvable pair は各 sweep で >= 3」——1 sweep のみの未達でも
    全体の gate は FAIL する `directional_gates()` の `sweeps_below_minimum`
    判定と同じ意味論）。"""
    assert (
        resolvable_pairs_possible(
            {"sweep-A": 3, "sweep-B": 2},
            {"sweep-A", "sweep-B"},
        )
        is False
    )


def test_resolvable_pairs_possible_expected_sweep_with_zero_usable_instances_fails() -> None:
    """観測 0 件の宣言 sweep が黙って消えてはならない
    （`directional_gates()` の `expected_sweep_ids` 引数と同じ扱い） —
    `usable_primary_instance_counts_by_sweep` に鍵が無い宣言 sweep は
    `n=0` として扱い、`C(0,2)=0 < 3` で FAIL する。"""
    assert (
        resolvable_pairs_possible({"sweep-A": 5}, {"sweep-A", "sweep-B"}) is False
    )


def test_resolvable_pairs_possible_no_declared_sweep_fails_closed() -> None:
    """宣言済み sweep が 1 つも無ければ防御的に `False`
    （`directional_gates()` の "no expected sweep declared" と同じ
    fail-closed 側）。"""
    assert resolvable_pairs_possible({}, set()) is False


def test_resolvable_pairs_possible_uses_frozen_min_resolvable_pairs_per_sweep_constant() -> None:
    """`MIN_RESOLVABLE_PAIRS_PER_SWEEP` ちょうどの usable instance 数から作れる
    pair 数がしきい値ちょうどのとき（`C(3,2)=3`）は `True`——境界値は
    `directional_gates()` の `sweeps_with_warning`（3 ちょうど）と同じ側で
    PASS する。"""
    n = 3
    assert math.comb(n, 2) == MIN_RESOLVABLE_PAIRS_PER_SWEEP
    assert resolvable_pairs_possible({"sweep-A": n}, {"sweep-A"}) is True
    assert resolvable_pairs_possible({"sweep-A": n - 1}, {"sweep-A"}) is False
