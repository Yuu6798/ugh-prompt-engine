"""`campaign/workunits.py` のテスト: 純算術（FULL matrix 上でも高速）。"""

from __future__ import annotations

from voice_genesis.calibration.campaign import workunits
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.controls import control_row_ids
from voice_genesis.calibration.fixtures.matrix import build_matrix
from voice_genesis.calibration.splitter import RowInput, realize_split

STRATUM_FACTOR_NAMES = ("truth_level", "boundary_class")


def test_plan_counts_match_design_totals() -> None:
    counts = workunits.plan_counts()
    assert counts.instances_total == 2280
    assert counts.renders_total == 4560
    assert counts.meter_calls_per_implementation == 13680
    assert counts.selection_order_of_magnitude == 10**5


def _full_realized_split():
    matrix_rows = build_matrix()
    row_inputs = [
        RowInput(
            row_id=mr.row_id,
            family=mr.row.family,
            stratum={"truth_level": mr.row.block, "boundary_class": mr.domain.value},
            truth_level=mr.row.block,
            generator_impl=mr.row.generator_impl,
            boundary_class=mr.domain.value,
        )
        for mr in matrix_rows
    ]
    realized = realize_split(row_inputs, b"plan-secret" * 4, STRATUM_FACTOR_NAMES)
    return matrix_rows, realized


def test_c1_c4_render_union_covers_all_instances_exactly_once_on_full_matrix() -> None:
    """§6.4 render 会計の union 式: c1 と c4 の render 対象 instance 集合は
    互いに素で、合わせて全 2,280 instance を厳密に 1 回ずつ被覆する。"""
    matrix_rows, realized = _full_realized_split()
    assignment = realized.assignment

    c1_units = workunits.enumerate_c1_render_units(matrix_rows, assignment)
    c4_units = workunits.enumerate_c4_render_units(matrix_rows, assignment)

    c1_instances = {(u.row_id, u.probe_index) for u in c1_units}
    c4_instances = {(u.row_id, u.probe_index) for u in c4_units}

    assert not (c1_instances & c4_instances), "C1/C4 render instance sets must be disjoint"
    assert len(c1_instances) == len(c1_units), "no duplicate render work units within C1"
    assert len(c4_instances) == len(c4_units), "no duplicate render work units within C4"

    union = c1_instances | c4_instances
    assert len(union) == 456 * 5

    # every control instance must be in C1 regardless of its home split
    control_ids = control_row_ids(matrix_rows)
    for row_id in control_ids:
        for probe_index in range(5):
            assert (row_id, probe_index) in c1_instances
            assert (row_id, probe_index) not in c4_instances

    design = workunits.plan_counts()
    assert (len(c1_instances) + len(c4_instances)) * 2 == design.renders_total


def test_c2_c3a_c3b_c4_instance_sets_are_split_correct() -> None:
    matrix_rows, realized = _full_realized_split()
    assignment = realized.assignment

    c2 = workunits.c2_baseline_instances(matrix_rows, assignment)
    c3a = workunits.c3a_f0_selection_instances(matrix_rows, assignment)

    from voice_genesis.calibration.vocab import Split

    cal_row_ids = {rid for rid, s in assignment.items() if s == Split.CALIBRATION}
    assert {row_id for row_id, _p in c2} <= cal_row_ids
    assert len(c2) == len(cal_row_ids) * 5

    f0_sel_row_ids = {
        mr.row_id
        for mr in matrix_rows
        if mr.row.family == FixtureFamily.F0_CONTROL.value
        and assignment.get(mr.row_id) == Split.SELECTION
    }
    assert {row_id for row_id, _p in c3a} == f0_sel_row_ids

    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        c3b = workunits.c3b_family_selection_instances(matrix_rows, assignment, family.value)
        expected_row_ids = {
            mr.row_id
            for mr in matrix_rows
            if mr.row.family == family.value and assignment.get(mr.row_id) == Split.SELECTION
        }
        assert {row_id for row_id, _p in c3b} == expected_row_ids

    c4 = workunits.c4_holdout_instances(matrix_rows, assignment)
    control_ids = control_row_ids(matrix_rows)
    holdout_row_ids = {rid for rid, s in assignment.items() if s == Split.HOLDOUT}
    assert {row_id for row_id, _p in c4} == (holdout_row_ids - control_ids)


def test_meter_call_units_within_and_fresh_repeats() -> None:
    instances = [("row-a", 0), ("row-b", 1)]
    candidates = ["CAND-B", "CAND-A"]
    units = workunits.enumerate_meter_call_units(instances, candidates)
    assert len(units) == len(instances) * len(candidates) * workunits.MEASUREMENT_REPEATS_PER_INSTANCE
    within = [u for u in units if u.repeat_kind == "within"]
    fresh = [u for u in units if u.repeat_kind == "fresh"]
    assert len(within) == len(instances) * len(candidates) * workunits.WITHIN_PROCESS_REPEATS
    assert len(fresh) == len(instances) * len(candidates) * workunits.FRESH_PROCESS_REPEATS
    # deterministic ordering: candidate_id sorted within each instance
    first_instance_units = [u for u in units if (u.row_id, u.probe_index) == ("row-a", 0)]
    assert [u.candidate_id for u in first_instance_units[:6]] == ["CAND-A"] * 6


def test_realized_plan_totals_are_consistent_with_matrix() -> None:
    matrix_rows, realized = _full_realized_split()
    plan = workunits.realized_plan(matrix_rows, realized.assignment)
    total_selection = plan.c3a_instances + sum(plan.c3b_instances_by_family.values())
    # sanity: selection-split instance count should be << full 2280 and > 0
    assert 0 < total_selection < 2280
    assert plan.c1_render_instances + plan.c4_render_instances == 2280
