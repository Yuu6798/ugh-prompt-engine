from __future__ import annotations

import math

import pytest

from voice_genesis.calibration.m6_identity import (
    component_u,
    distinct,
    m6_distance,
    pair_uncertainty,
    t_null,
)
from voice_genesis.calibration.vocab import CLAIM_CRITICAL_SET, ClaimCeiling, MeterId, TerminalStatus


def test_component_u_hand_computed() -> None:
    # u_X[j] = (U_GT+U_num+U_rep+U_proc)/E_use = (0.1+0.05+0.02+0.03)/2.0 = 0.2/2.0=0.1
    assert component_u(u_gt=0.1, u_num=0.05, u_rep=0.02, u_proc=0.03, e_use=2.0) == pytest.approx(
        0.1
    )


def test_pair_uncertainty_sum_of_norms_exceeds_norm_of_sum_l2() -> None:
    """[Codex レビュー 2026-09-01] `pair_uncertainty` は `m6_distance` の
    component distance と同一の `1/n` 等重み (`n` = 各ベクトルの成分数) を
    p-norm に掛ける（distance が `weight=1/n` を掛けているのと同じ演算。
    `_weighted_norm` の docstring 参照）。修正前は重みなし p-norm をそのまま
    使っており、n 成分の pair で `U_obs_pair` が `D_obs` に対し最大 n 倍
    過大だった。

    三角不等式の非退化ケース: u_a=[3,4] (n=2, ||.||2=5 -> weighted=5/2=2.5),
    u_b=[4,3] (n=2, ||.||2=5 -> weighted=2.5)
    sum-of-norms (weighted) = 2.5+2.5 = 5.0
    norm-of-sum (weighted)  = ||[7,7]||2/2 = sqrt(49+49)/2 = 7*sqrt(2)/2 ≈ 4.9497...
    sum-of-norms (5.0) > norm-of-sum (≈4.9497) -> 同じ重みを掛けても
    採用式 (sum-of-norms) の保守性 (より大きい) は保たれる
    """
    u_a = [3.0, 4.0]
    u_b = [4.0, 3.0]
    sum_of_norms = pair_uncertainty(u_a, u_b, "L2")
    norm_of_sum = math.sqrt((3.0 + 4.0) ** 2 + (4.0 + 3.0) ** 2) / 2.0
    assert sum_of_norms == pytest.approx(5.0)
    assert norm_of_sum == pytest.approx(7 * math.sqrt(2) / 2.0)
    assert sum_of_norms > norm_of_sum


def test_pair_uncertainty_l1_hand_computed() -> None:
    # weighted L1 norm = ||v||1 / len(v) (m6_distance と同じ 1/n 等重み。
    # n は各ベクトル自身の長さ):
    #   ||[1,2,3]||1/3 = 6/3 = 2.0
    #   ||[4,5]||1/2   = 9/2 = 4.5
    # U_obs_pair = 2.0 + 4.5 = 6.5
    assert pair_uncertainty([1.0, 2.0, 3.0], [4.0, 5.0], "L1") == pytest.approx(6.5)


def test_t_null_hand_computed() -> None:
    # D_null=[0.1,0.2,0.3], U_null_pair=[0.05,0.05,0.05] -> sums=[0.15,0.25,0.35]
    # q95 linear over n=3: sorted=[0.15,0.25,0.35], rank=0.95*2=1.9, floor=1(0.25)
    # frac=0.9 -> 0.25+0.9*(0.35-0.25)=0.25+0.09=0.34
    result = t_null([0.1, 0.2, 0.3], [0.05, 0.05, 0.05])
    assert result == pytest.approx(0.34)


def test_t_null_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        t_null([0.1, 0.2], [0.05])


def test_distinct_strict_inequality() -> None:
    # d_obs - u_obs_pair > t_null: 1.0 - 0.3 = 0.7 > 0.5 -> True
    assert distinct(d_obs=1.0, u_obs_pair=0.3, t_null_value=0.5) is True
    # 境界: 差が t_null と厳密等値なら distinct ではない
    assert distinct(d_obs=1.0, u_obs_pair=0.5, t_null_value=0.5) is False
    assert distinct(d_obs=1.0, u_obs_pair=0.51, t_null_value=0.5) is False


# ---------------------------------------------------------------------------
# m6_distance
# ---------------------------------------------------------------------------


_ALL_ABSOLUTE = {m: TerminalStatus.CALIBRATED_ABSOLUTE for m in CLAIM_CRITICAL_SET}


def test_m6_distance_empty_critical_set_construction_guard() -> None:
    # CLAIM_CRITICAL_SET は凍結された非空定数だが、member_status が空マッピングの
    # 場合は全 member が非 ABSOLUTE 扱いになり NOT_EVALUABLE。
    result = m6_distance({}, {}, {}, member_status={}, norm="L1")
    assert result.status == TerminalStatus.NOT_EVALUABLE
    assert result.distance is None
    assert result.components == ()


def test_m6_distance_one_member_not_absolute_is_not_evaluable() -> None:
    # 3 member 中 1 member が CALIBRATED_DIRECTIONAL -> 部分構成であっても
    # distance を一切出力しない (NOT_EVALUABLE + distance None)。
    status_map = dict(_ALL_ABSOLUTE)
    one_member = next(iter(CLAIM_CRITICAL_SET))
    status_map[one_member] = TerminalStatus.CALIBRATED_DIRECTIONAL

    components_a = {m: 1.0 for m in CLAIM_CRITICAL_SET}
    components_b = {m: 2.0 for m in CLAIM_CRITICAL_SET}
    e_use = {m: 1.0 for m in CLAIM_CRITICAL_SET}

    result = m6_distance(components_a, components_b, e_use, member_status=status_map, norm="L2")
    assert result.status == TerminalStatus.NOT_EVALUABLE
    assert result.distance is None
    assert result.components == ()


def test_m6_distance_missing_value_for_a_member_is_not_evaluable() -> None:
    components_a = {m: 1.0 for m in CLAIM_CRITICAL_SET}
    components_b = {m: 2.0 for m in list(CLAIM_CRITICAL_SET)[:-1]}  # 1 件欠落
    e_use = {m: 1.0 for m in CLAIM_CRITICAL_SET}
    result = m6_distance(
        components_a, components_b, e_use, member_status=_ALL_ABSOLUTE, norm="L1"
    )
    assert result.status == TerminalStatus.NOT_EVALUABLE
    assert result.distance is None


def test_m6_distance_full_success_oracle_l1_and_l2() -> None:
    """[Codex レビュー 2026-09-01 第 4 巡] 正常系オラクル: CLAIM_CRITICAL_SET の
    全 member が CALIBRATED_ABSOLUTE のとき、component vector・distance
    (L1/L2)・status (CALIBRATED_DIRECTIONAL ceiling) が手計算期待値と一致する
    ことを検証する。常に NOT_EVALUABLE を返す実装はこのテストで確実に失敗する。

    手計算:
    critical_ids はアルファベット順: M2_APERIODICITY < M2_SPECTRAL_TILT < M3_FORMANTS

    components_a = {M3_FORMANTS:100.0, M2_SPECTRAL_TILT:50.0, M2_APERIODICITY:10.0}
    components_b = {M3_FORMANTS:110.0, M2_SPECTRAL_TILT:40.0, M2_APERIODICITY:16.0}
    e_use        = {M3_FORMANTS:20.0,  M2_SPECTRAL_TILT:5.0,  M2_APERIODICITY:3.0}

    diff_norm(M2_APERIODICITY)   = (10.0-16.0)/3.0  = -6.0/3.0  = -2.0
    diff_norm(M2_SPECTRAL_TILT)  = (50.0-40.0)/5.0  = 10.0/5.0  =  2.0
    diff_norm(M3_FORMANTS)       = (100.0-110.0)/20.0 = -10.0/20.0 = -0.5

    n=3, weight=1/3

    L1: sum(|d|) = 2.0+2.0+0.5 = 4.5 -> distance = 4.5/3 = 1.5
    L2: sum(d^2) = 4.0+4.0+0.25 = 8.25 -> sqrt(8.25) ≈ 2.87228132...
        distance = 2.87228132.../3 ≈ 0.95742871...
    """
    components_a = {
        MeterId.M3_FORMANTS: 100.0,
        MeterId.M2_SPECTRAL_TILT: 50.0,
        MeterId.M2_APERIODICITY: 10.0,
    }
    components_b = {
        MeterId.M3_FORMANTS: 110.0,
        MeterId.M2_SPECTRAL_TILT: 40.0,
        MeterId.M2_APERIODICITY: 16.0,
    }
    e_use = {
        MeterId.M3_FORMANTS: 20.0,
        MeterId.M2_SPECTRAL_TILT: 5.0,
        MeterId.M2_APERIODICITY: 3.0,
    }

    result_l1 = m6_distance(
        components_a, components_b, e_use, member_status=_ALL_ABSOLUTE, norm="L1"
    )
    assert result_l1.status == TerminalStatus.CALIBRATED_DIRECTIONAL
    assert result_l1.ceiling == ClaimCeiling.DIRECTIONAL
    assert result_l1.distance == pytest.approx(1.5)
    assert len(result_l1.components) == 3
    assert [c.component_id for c in result_l1.components] == [
        "M2_APERIODICITY",
        "M2_SPECTRAL_TILT",
        "M3_FORMANTS",
    ]
    by_id = {c.component_id: c for c in result_l1.components}
    assert by_id["M2_APERIODICITY"].diff_normalized == pytest.approx(-2.0)
    assert by_id["M2_APERIODICITY"].contribution == pytest.approx(2.0)  # L1: |diff|
    assert by_id["M2_SPECTRAL_TILT"].diff_normalized == pytest.approx(2.0)
    assert by_id["M3_FORMANTS"].diff_normalized == pytest.approx(-0.5)
    assert by_id["M3_FORMANTS"].value_a == pytest.approx(100.0)
    assert by_id["M3_FORMANTS"].value_b == pytest.approx(110.0)

    result_l2 = m6_distance(
        components_a, components_b, e_use, member_status=_ALL_ABSOLUTE, norm="L2"
    )
    assert result_l2.status == TerminalStatus.CALIBRATED_DIRECTIONAL
    expected_l2 = math.sqrt(4.0 + 4.0 + 0.25) / 3.0
    assert result_l2.distance == pytest.approx(expected_l2)
    by_id_l2 = {c.component_id: c for c in result_l2.components}
    assert by_id_l2["M2_APERIODICITY"].contribution == pytest.approx(4.0)  # L2: diff^2


def test_pair_uncertainty_distance_consistency_hand_derived() -> None:
    """[Codex レビュー 2026-09-01] scale-consistency オラクル: 各終端の
    不確かさベクトルが `m6_distance` の `diff_normalized` の絶対値そのもの
    (対称ケース `u_A = u_B = |d|`) のとき、`D_obs == U_obs_pair/2` が
    p in {1,2} いずれでも成立する（endpoint symmetry）。

    手計算導出（`pair_uncertainty` / `_weighted_norm` の docstring と同一）:
    ```
    weighted_norm(v,p) = ||v||_p / n   (n = len(v); m6_distance の distance が
                                          使う 1/n 等重みと同一)
    U_obs_pair = weighted_norm(u_A,p) + weighted_norm(u_B,p)
               = ||d||_p/n + ||d||_p/n = 2*(||d||_p/n)
    D_obs      = weight * norm_p(d) = (1/n) * ||d||_p   (m6_distance の式そのもの)
    => U_obs_pair = 2 * D_obs  <=>  D_obs == U_obs_pair / 2
    ```
    weight に `1/n**(1/p)` 等の p 依存重みを使う実装や、`_norm` に重みを
    掛け忘れる回帰があると、この等式は崩れ本テストは失敗する。
    """
    components_a = {
        MeterId.M3_FORMANTS: 100.0,
        MeterId.M2_SPECTRAL_TILT: 50.0,
        MeterId.M2_APERIODICITY: 10.0,
    }
    components_b = {
        MeterId.M3_FORMANTS: 110.0,
        MeterId.M2_SPECTRAL_TILT: 40.0,
        MeterId.M2_APERIODICITY: 16.0,
    }
    e_use = {
        MeterId.M3_FORMANTS: 20.0,
        MeterId.M2_SPECTRAL_TILT: 5.0,
        MeterId.M2_APERIODICITY: 3.0,
    }
    for norm in ("L1", "L2"):
        result = m6_distance(
            components_a, components_b, e_use, member_status=_ALL_ABSOLUTE, norm=norm
        )
        assert result.distance is not None
        abs_diffs = [abs(c.diff_normalized) for c in result.components]
        u_obs_pair = pair_uncertainty(abs_diffs, abs_diffs, norm)
        assert result.distance == pytest.approx(u_obs_pair / 2.0)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("a", float("nan")),
        ("a", float("inf")),
        ("b", float("-inf")),
        ("e_use", 0.0),
        ("e_use", -1.0),
        ("e_use", float("nan")),
        ("e_use", float("inf")),
    ],
)
def test_m6_distance_invalid_normalization_operands_are_not_evaluable(
    field: str, bad_value: float
) -> None:
    components_a = {m: 1.0 for m in CLAIM_CRITICAL_SET}
    components_b = {m: 2.0 for m in CLAIM_CRITICAL_SET}
    e_use = {m: 1.0 for m in CLAIM_CRITICAL_SET}
    target = next(iter(CLAIM_CRITICAL_SET))

    if field == "a":
        components_a[target] = bad_value
    elif field == "b":
        components_b[target] = bad_value
    else:
        e_use[target] = bad_value

    result = m6_distance(
        components_a, components_b, e_use, member_status=_ALL_ABSOLUTE, norm="L1"
    )
    assert result.status == TerminalStatus.NOT_EVALUABLE
    assert result.distance is None
    assert result.components == ()
