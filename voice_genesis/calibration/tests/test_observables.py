from __future__ import annotations

import pytest

from voice_genesis.calibration.observables import (
    DetectionResult,
    DuplicateInstanceIdError,
    ErrorTerms,
    bias,
    detection_rates,
    error_terms,
    failure_boundary,
    mae,
    nuisance_ds,
    q95,
    two_stage_median,
    u_proc,
    u_rep,
)


def _keyed(prefix: str, outcomes: list[bool]) -> list[tuple[str, bool]]:
    """distinct instance_id を割り振った `(instance_id, outcome)` list を作る
    テスト用ヘルパー（`detection_rates` の keyed outcomes 契約用）。"""
    return [(f"{prefix}{i}", v) for i, v in enumerate(outcomes)]


def test_two_stage_median_hand_computed_oracle_unbalanced_repeats() -> None:
    # process 1: repeats [1,2,3,100] (4 反復, 外れ値 100 混入) -> median = (2+3)/2=2.5
    # process 2: repeats [10] (1 反復のみ) -> median = 10
    # process 3: repeats [4,5] (2 反復) -> median = (4+5)/2 = 4.5
    # 二段目: median([2.5, 10, 4.5]) -> sorted [2.5, 4.5, 10] -> 中央値 = 4.5
    x = {"p1": [1.0, 2.0, 3.0, 100.0], "p2": [10.0], "p3": [4.0, 5.0]}
    assert two_stage_median(x) == pytest.approx(4.5)


def test_two_stage_median_process_with_many_repeats_does_not_dominate() -> None:
    # process 1 は 100 反復すべて 0.0 (repeat 数が多い process が支配しないことの確認)
    # process 2 は 1 反復のみ 10.0
    # 一段目 median: p1 -> 0.0, p2 -> 10.0
    # 二段目 median([0.0, 10.0]) = 5.0
    # (仮に repeat を平坦化して単純 median を取ると 100 個の 0.0 に 10.0 が
    #  埋もれ 0.0 になってしまうところを、二段 median が防ぐ)
    x = {"p1": [0.0] * 100, "p2": [10.0]}
    assert two_stage_median(x) == pytest.approx(5.0)


def test_two_stage_median_raises_on_no_data() -> None:
    with pytest.raises(ValueError):
        two_stage_median({})


def test_error_terms_hand_computed() -> None:
    # m=7.5, truth=5.0 -> e=2.5, AE=2.5, RE=2.5/max(5.0, 0.1)=2.5/5.0=0.5
    result = error_terms(m=7.5, truth=5.0, zero_guard=0.1)
    assert result == ErrorTerms(e=2.5, ae=2.5, re=0.5)


def test_error_terms_zero_guard_applies_near_zero_truth() -> None:
    # truth=0.0, zero_guard=0.2 -> denom=max(0.0,0.2)=0.2; m=0.4 -> e=0.4,AE=0.4
    # RE=0.4/0.2=2.0
    result = error_terms(m=0.4, truth=0.0, zero_guard=0.2)
    assert result.e == pytest.approx(0.4)
    assert result.ae == pytest.approx(0.4)
    assert result.re == pytest.approx(2.0)


def test_bias_hand_computed() -> None:
    # mean([1,-1,3,-3,2]) = 2/5 = 0.4
    assert bias([1.0, -1.0, 3.0, -3.0, 2.0]) == pytest.approx(0.4)


def test_mae_hand_computed() -> None:
    # mean(|[1,-2,3,-4]|) = mean([1,2,3,4]) = 10/4 = 2.5
    assert mae([1.0, -2.0, 3.0, -4.0]) == pytest.approx(2.5)


def test_q95_hand_computed_oracle_linear_method() -> None:
    # values = [1,2,3,4,5,6,7,8,9,10] (n=10), q95 linear interpolation:
    # numpy "linear": rank = q*(n-1) = 0.95*9 = 8.55
    # floor=8 (value=9, 0-indexed sorted[8]=9), frac=0.55
    # result = sorted[8] + 0.55*(sorted[9]-sorted[8]) = 9 + 0.55*(10-9) = 9.55
    values = [float(i) for i in range(1, 11)]
    assert q95(values) == pytest.approx(9.55)


def test_q95_single_value() -> None:
    assert q95([42.0]) == pytest.approx(42.0)


def test_u_rep_hand_computed_oracle() -> None:
    # cell (i1,p1): [1,2,3,7] -> range/2 = (7-1)/2 = 3.0
    # cell (i1,p2): [2,4] -> range/2 = (4-2)/2 = 1.0
    # cell (i2,p1): [0,10,5] -> range/2 = (10-0)/2 = 5.0
    # q95 of [3.0, 1.0, 5.0] linear: n=3, rank=0.95*2=1.9, sorted=[1.0,3.0,5.0]
    # floor=1 (value 3.0), frac=0.9 -> 3.0+0.9*(5.0-3.0)=3.0+1.8=4.8
    cells = {
        ("i1", "p1"): [1.0, 2.0, 3.0, 7.0],
        ("i1", "p2"): [2.0, 4.0],
        ("i2", "p1"): [0.0, 10.0, 5.0],
    }
    assert u_rep(cells) == pytest.approx(4.8)


def test_u_rep_excludes_singleton_cells() -> None:
    # singleton cell (only 1 repeat) の range は未定義であり 0 として算入しては
    # ならない。混入していても除外されて、非 singleton セルのみで q95 を計算する。
    cells = {
        ("i1", "p1"): [1.0, 2.0, 3.0, 7.0],  # range/2 = 3.0
        ("i1", "p2"): [2.0, 4.0],  # range/2 = 1.0
        ("i2", "p1"): [99.0],  # singleton -> 除外 (range=0 として混入しない)
    }
    # singleton を除外すると q95([3.0, 1.0]) linear: n=2, rank=0.95*1=0.95
    # sorted=[1.0,3.0], floor=0, frac=0.95 -> 1.0+0.95*(3.0-1.0)=1.0+1.9=2.9
    assert u_rep(cells) == pytest.approx(2.9)


def test_u_rep_all_singleton_returns_none() -> None:
    cells = {("i1", "p1"): [1.0], ("i2", "p1"): [5.0]}
    assert u_rep(cells) is None


def test_u_rep_empty_cell_ignored_not_singleton_special_case() -> None:
    cells = {("i1", "p1"): [], ("i2", "p1"): [1.0, 3.0]}
    # 空セルは無視、非空セル (1 件) は range/2 = 1.0 のみ -> q95([1.0]) = 1.0
    assert u_rep(cells) == pytest.approx(1.0)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_u_rep_nonfinite_repeat_returns_none(invalid: float) -> None:
    """非有限 repeat を Python max/min の順序依存で有限 range に縮退させない。"""
    cells = {
        ("i1", "p1"): [1.0, invalid],
        ("i2", "p1"): [0.0, 2.0],
    }
    assert u_rep(cells) is None


def test_u_proc_two_process_hand_computed() -> None:
    # instance i1: [med1=2.0, med2=6.0] -> |diff|=4.0
    # instance i2: [med1=10.0, med2=10.5] -> |diff|=0.5
    # q95([4.0, 0.5]) linear: n=2, rank=0.95, sorted=[0.5,4.0]
    # floor=0,frac=0.95 -> 0.5+0.95*(4.0-0.5)=0.5+3.325=3.825
    # u_proc = 3.825/2 = 1.9125
    medians = {"i1": [2.0, 6.0], "i2": [10.0, 10.5]}
    assert u_proc(medians) == pytest.approx(1.9125)


def test_u_proc_three_process_generalizes_to_all_pair_diffs() -> None:
    # instance i1 に 3 process の median [1.0, 3.0, 8.0]
    # 全 pair 差: |1-3|=2.0, |1-8|=7.0, |3-8|=5.0
    # q95([2.0,7.0,5.0]) linear: n=3, sorted=[2.0,5.0,7.0], rank=0.95*2=1.9
    # floor=1(value5.0),frac=0.9 -> 5.0+0.9*(7.0-5.0)=5.0+1.8=6.8
    # u_proc = 6.8/2 = 3.4
    medians = {"i1": [1.0, 3.0, 8.0]}
    assert u_proc(medians) == pytest.approx(3.4)


def test_u_proc_ignores_instances_with_single_process() -> None:
    medians = {"i1": [1.0, 3.0], "i2": [999.0]}
    # i2 has only 1 process -> no pair diffs contributed; only i1's diff=2.0 used
    # q95([2.0]) = 2.0 -> u_proc = 2.0/2 = 1.0
    assert u_proc(medians) == pytest.approx(1.0)


def test_nuisance_ds_hand_computed() -> None:
    assert nuisance_ds(anchor_error=0.3, varied_error=-0.5) == pytest.approx(0.8)


def test_detection_rates_missing_counted_in_numerator() -> None:
    # neg_outcomes: 10 controls, 2 fired (誤検出/missing扱い) -> FDR0=2/10=0.2
    # pos_outcomes: 10 controls, 1 不発火 -> FNR1=1/10=0.1
    neg = _keyed("neg", [True, True] + [False] * 8)
    pos = _keyed("pos", [False] + [True] * 9)
    result = detection_rates(neg, pos)
    assert result == DetectionResult(
        fdr0=0.2, fnr1=0.1, n_neg=10, n_pos=10, min_count_met=True, control_gate="APPLICABLE"
    )


def test_detection_rates_missing_does_not_reduce_denominator() -> None:
    # 分母は常に distinct instance 数であり、missing/invalid を除外して分母を
    # 縮めてはならない (設計正本 §10.1)。
    neg = _keyed("neg", [True] * 3 + [False] * 7)  # 10 件中 3 件 "fired"
    result = detection_rates(neg, _keyed("pos", [True] * 10))
    assert result.n_neg == 10
    assert result.fdr0 == pytest.approx(0.3)


def test_detection_rates_min_count_not_met() -> None:
    result = detection_rates(_keyed("neg", [False] * 5), _keyed("pos", [True] * 12))
    assert result.min_count_met is False


def test_detection_rates_not_applicable_control_gate_passthrough() -> None:
    result = detection_rates({}, {}, control_gate="NOT_APPLICABLE")
    assert result.control_gate == "NOT_APPLICABLE"
    assert result.fdr0 == 0.0
    assert result.fnr1 == 0.0


def test_detection_rates_accepts_mapping_form() -> None:
    """`Mapping[instance_id, outcome]` 形式でも `Sequence[tuple]` 形式と同じ
    結果になること。"""
    neg = {"n0": True, "n1": True, **{f"n{i}": False for i in range(2, 10)}}
    pos = {"p0": False, **{f"p{i}": True for i in range(1, 10)}}
    result = detection_rates(neg, pos)
    assert result == DetectionResult(
        fdr0=0.2, fnr1=0.1, n_neg=10, n_pos=10, min_count_met=True, control_gate="APPLICABLE"
    )


def test_detection_rates_duplicate_instance_id_in_neg_rejected() -> None:
    """[Codex レビュー 2026-09-01 P1] regression: 同一 instance を 10 回繰り返す
    (raw sequence 水増し) だけでは N>=10 を満たしたことにはならない。
    `neg_outcomes` に重複 `instance_id` があれば `DuplicateInstanceIdError` で
    typed failure として reject される。"""
    duplicated_10x = [("row-1", False)] * 10
    with pytest.raises(DuplicateInstanceIdError) as excinfo:
        detection_rates(duplicated_10x, _keyed("pos", [True] * 10))
    assert excinfo.value.kind == "neg"
    assert excinfo.value.duplicate_ids == ("row-1",)


def test_detection_rates_duplicate_instance_id_in_pos_rejected() -> None:
    duplicated_10x = [("row-1", True)] * 10
    with pytest.raises(DuplicateInstanceIdError) as excinfo:
        detection_rates(_keyed("neg", [False] * 10), duplicated_10x)
    assert excinfo.value.kind == "pos"
    assert excinfo.value.duplicate_ids == ("row-1",)


def test_detection_rates_duplicate_instance_id_does_not_satisfy_min_count() -> None:
    """1 instance を 10 回複製しても `min_count_met` を trivially 満たせない
    ことの直接確認: 例外送出そのものが「水増しは通らない」ことを保証する
    （呼び出し側が例外を fail-closed として扱えば、min_count_met=True の結果は
    決して得られない）。"""
    duplicated_10x = [("row-1", True)] * 10
    with pytest.raises(DuplicateInstanceIdError):
        detection_rates(duplicated_10x, duplicated_10x)


def test_failure_boundary_missing_counts_as_first_fail() -> None:
    # levels in order; True=pass, False=fail, None=missing(=fail)
    levels = ["L1", "L2", "L3", "L4"]
    flags = [True, True, None, False]
    last_pass, first_fail = failure_boundary(levels, flags)
    assert last_pass == "L2"
    assert first_fail == "L3"


def test_failure_boundary_all_pass() -> None:
    last_pass, first_fail = failure_boundary(["a", "b"], [True, True])
    assert last_pass == "b"
    assert first_fail is None


def test_failure_boundary_all_fail() -> None:
    last_pass, first_fail = failure_boundary(["a", "b"], [False, None])
    assert last_pass is None
    assert first_fail == "a"


def test_failure_boundary_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        failure_boundary(["a", "b"], [True])

def test_detection_rates_rejects_instance_ids_reused_across_control_classes() -> None:
    shared_ids = [f"shared-{i}" for i in range(10)]
    neg = {instance_id: False for instance_id in shared_ids}
    pos = {instance_id: True for instance_id in shared_ids}

    with pytest.raises(DuplicateInstanceIdError) as excinfo:
        detection_rates(neg, pos)

    assert excinfo.value.kind == "cross_class"
    assert excinfo.value.duplicate_ids == tuple(shared_ids)
def test_failure_boundary_stops_at_first_failure() -> None:
    levels = ["L1", "L2", "L3"]
    flags = [True, None, True]

    last_pass, first_fail = failure_boundary(levels, flags)

    assert last_pass == "L1"
    assert first_fail == "L2"
