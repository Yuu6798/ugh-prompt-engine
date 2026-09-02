"""stage 別 work unit の決定論的列挙（IMPLEMENTATION_MAP_v1.md §6.4）。

- **render work unit**: instance = row_id × probe_index (0..4)。1 instance に
  つき 2 回 fresh-process render（determinism 検査。§6）。
- **meter call work unit**: instance × candidate_id × {within-process 3 call,
  fresh-process 3 call}（§6: within-process repeat=3 + fresh-process repeat=3）。

stage 別集合（§6.4 render 会計 union 式・§9 selection 実行範囲）:

- C1 = (CALIBRATION ∪ SELECTION split の non-control instance) ∪
  (**全 control instance**、home split に依らない)
- C4 = HOLDOUT split の non-control instance のみ（control は C1 で render
  済みの artifact を再利用し再 render しない）
- C2 baseline = CALIBRATION split の全 instance（B0 は診断参照であり control
  除外の対象ではない）
- C3a (F0 selection) = F0_CONTROL family の SELECTION split instance ∪
  F0_CONTROL family の**全** negative control instance（home split に依らない。
  round 17 finding #1 採用、§2.7 control 共有契約）
- C3b (他 family selection) = 各 family の SELECTION split instance ∪
  当該 family の**全** negative control instance（home split に依らない）
  （F0_CONTROL を除く。§9「selection は各候補 × 自 family selection 行のみ」。
  round 17 finding #1 採用: selection 行のみでは HOLDOUT/CALIBRATION に home
  する negative control が C3 で測定されず fail filter の判定材料から漏れて
  いたため、C1 で「全 control」としてすでに render 済みの negative control
  instance を測定対象へ合流させる）

`plan_counts()` は §6/§14 の設計値（instances 2,280 / renders 4,560 /
meter calls 13,680 per implementation）を実 matrix 定数から再導出し、
dry-run `plan` サブコマンドが照合する基準値を提供する。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from voice_genesis.calibration.fixtures.axes import FixtureFamily, TOTAL_LOGICAL_CELLS
from voice_genesis.calibration.fixtures.controls import (
    PROBE_REPEATS,
    control_row_ids,
    negative_control_instances,
)
from voice_genesis.calibration.fixtures.matrix import MatrixRow
from voice_genesis.calibration.vocab import Split

Instance = tuple[str, int]
"""(row_id, probe_index)。"""


# ---------------------------------------------------------------------------
# render work units (C1 / C4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderWorkUnit:
    row_id: str
    family: str
    split: Split
    probe_index: int
    is_control: bool


def _sorted_units(units: Sequence[RenderWorkUnit]) -> tuple[RenderWorkUnit, ...]:
    return tuple(sorted(units, key=lambda u: (u.row_id, u.probe_index)))


def enumerate_c1_render_units(
    matrix_rows: Sequence[MatrixRow], assignment: Mapping[str, Split]
) -> tuple[RenderWorkUnit, ...]:
    """C1 = (CAL ∪ SEL の non-control instance) ∪ (全 control instance)。"""
    control_ids = control_row_ids(matrix_rows)
    units: list[RenderWorkUnit] = []
    for mr in matrix_rows:
        split = assignment.get(mr.row_id)
        is_control = mr.row_id in control_ids
        include = is_control or split in (Split.CALIBRATION, Split.SELECTION)
        if not include or split is None:
            continue
        units.extend(
            RenderWorkUnit(
                row_id=mr.row_id,
                family=mr.row.family,
                split=split,
                probe_index=p,
                is_control=is_control,
            )
            for p in range(PROBE_REPEATS)
        )
    return _sorted_units(units)


def enumerate_c4_render_units(
    matrix_rows: Sequence[MatrixRow], assignment: Mapping[str, Split]
) -> tuple[RenderWorkUnit, ...]:
    """C4 = HOLDOUT split の non-control instance のみ（control は C1 で
    render 済みの artifact を再利用する。再 render しない）。"""
    control_ids = control_row_ids(matrix_rows)
    units: list[RenderWorkUnit] = []
    for mr in matrix_rows:
        if mr.row_id in control_ids:
            continue
        if assignment.get(mr.row_id) != Split.HOLDOUT:
            continue
        units.extend(
            RenderWorkUnit(
                row_id=mr.row_id,
                family=mr.row.family,
                split=Split.HOLDOUT,
                probe_index=p,
                is_control=False,
            )
            for p in range(PROBE_REPEATS)
        )
    return _sorted_units(units)


# ---------------------------------------------------------------------------
# measurement instance sets (C2 / C3a / C3b / C4)
# ---------------------------------------------------------------------------


def _instances_for(
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, Split],
    *,
    split: Split,
    family: str | None = None,
    exclude_row_ids: frozenset[str] = frozenset(),
) -> tuple[Instance, ...]:
    out: list[Instance] = []
    for mr in matrix_rows:
        if family is not None and mr.row.family != family:
            continue
        if mr.row_id in exclude_row_ids:
            continue
        if assignment.get(mr.row_id) != split:
            continue
        out.extend((mr.row_id, p) for p in range(PROBE_REPEATS))
    return tuple(sorted(out))


def c2_baseline_instances(
    matrix_rows: Sequence[MatrixRow], assignment: Mapping[str, Split]
) -> tuple[Instance, ...]:
    """C2 baseline = CALIBRATION split の全 instance（B0 は診断参照。control
    除外なし）。"""
    return _instances_for(matrix_rows, assignment, split=Split.CALIBRATION)


def c3a_f0_selection_instances(
    matrix_rows: Sequence[MatrixRow], assignment: Mapping[str, Split]
) -> tuple[Instance, ...]:
    """C3a = F0_CONTROL family の SELECTION split instance ∪ F0_CONTROL family
    の全 negative control instance（round 17 finding #1 採用: home split が
    CALIBRATION/HOLDOUT の negative control も §2.7 control 共有契約により C3a
    の測定・fail filter 対象に含める。これらは C1 で「全 control」として
    render 済みのため追加 render は発生しない）。"""
    selection = _instances_for(
        matrix_rows, assignment, split=Split.SELECTION, family=FixtureFamily.F0_CONTROL.value
    )
    controls = negative_control_instances(matrix_rows, family=FixtureFamily.F0_CONTROL.value)
    return tuple(sorted(set(selection) | controls))


def c3b_family_selection_instances(
    matrix_rows: Sequence[MatrixRow], assignment: Mapping[str, Split], family: str
) -> tuple[Instance, ...]:
    """C3b = 指定 family（F0_CONTROL を除く）の SELECTION split instance ∪
    当該 family の全 negative control instance（round 17 finding #1 採用。
    c3a と同じ理由 — §2.7 control 共有契約・C1 で render 済み）。"""
    if family == FixtureFamily.F0_CONTROL.value:
        raise ValueError("c3b_family_selection_instances: F0_CONTROL uses c3a, not c3b")
    selection = _instances_for(matrix_rows, assignment, split=Split.SELECTION, family=family)
    controls = negative_control_instances(matrix_rows, family=family)
    return tuple(sorted(set(selection) | controls))


def c4_holdout_instances(
    matrix_rows: Sequence[MatrixRow],
    assignment: Mapping[str, Split],
    *,
    family: str | None = None,
) -> tuple[Instance, ...]:
    """C4 = HOLDOUT split の non-control instance（selected candidate + B0 の
    測定対象。control は §7 leakage 除外契約で render 済みの artifact のみを
    再利用し、gate 5 の N_neg 母集団としては別途 `positive_detection_instances`/
    `negative_controls_by_class` から扱う — B0/selected candidate の holdout
    測定対象そのものからは除外する）。"""
    control_ids = control_row_ids(matrix_rows)
    return _instances_for(
        matrix_rows,
        assignment,
        split=Split.HOLDOUT,
        family=family,
        exclude_row_ids=control_ids,
    )


# ---------------------------------------------------------------------------
# meter call work units
# ---------------------------------------------------------------------------

WITHIN_PROCESS_REPEATS = 3
FRESH_PROCESS_REPEATS = 3
MEASUREMENT_REPEATS_PER_INSTANCE = WITHIN_PROCESS_REPEATS + FRESH_PROCESS_REPEATS


@dataclass(frozen=True)
class MeterCallWorkUnit:
    row_id: str
    probe_index: int
    candidate_id: str
    repeat_kind: str  # "within" | "fresh"
    repeat_index: int  # 0..2


def enumerate_meter_call_units(
    instances: Sequence[Instance], candidate_ids: Sequence[str]
) -> tuple[MeterCallWorkUnit, ...]:
    """instance × candidate × {within-process 3, fresh-process 3} の決定論的
    列挙（instance 順 → candidate_id 順 → within → fresh → repeat_index 順）。"""
    units: list[MeterCallWorkUnit] = []
    for row_id, probe_index in sorted(instances):
        for candidate_id in sorted(candidate_ids):
            for i in range(WITHIN_PROCESS_REPEATS):
                units.append(
                    MeterCallWorkUnit(row_id, probe_index, candidate_id, "within", i)
                )
            for i in range(FRESH_PROCESS_REPEATS):
                units.append(
                    MeterCallWorkUnit(row_id, probe_index, candidate_id, "fresh", i)
                )
    return tuple(units)


# ---------------------------------------------------------------------------
# plan totals (dry-run cross-check against §6/§14 design values)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanCounts:
    instances_total: int
    renders_total: int
    meter_calls_per_implementation: int
    selection_order_of_magnitude: int


#: §9「実行範囲: selection は各候補 × 自 family selection 行のみ」+ 99 候補の
#: おおよその桁数（設計正本 §14: "selection 段は... 総 meter call ~10^5
#: オーダー"）。plan の照合表は桁数のみを報告し、正確な積算値は実測 selection
#: split の instance 数（realized split 依存）に左右されるため dry-run 段階
#: では概算表示に留める（memo §6.4 「realized split は secret 依存のため
#: 確定内訳は freeze 後に定まり、dry-run 段階は... 概算表示」）。
_SELECTION_ORDER_OF_MAGNITUDE = 10**5


def plan_counts() -> PlanCounts:
    """§6/§14 の設計値を実 matrix 定数（`axes.TOTAL_LOGICAL_CELLS` /
    `controls.PROBE_REPEATS`）から再導出する。`cli.py plan` が dry-run 照合に
    使う基準値（instances=2,280 / renders=4,560 / meter_calls=13,680）。"""
    instances_total = TOTAL_LOGICAL_CELLS * PROBE_REPEATS
    renders_total = instances_total * 2
    meter_calls_per_implementation = instances_total * MEASUREMENT_REPEATS_PER_INSTANCE
    return PlanCounts(
        instances_total=instances_total,
        renders_total=renders_total,
        meter_calls_per_implementation=meter_calls_per_implementation,
        selection_order_of_magnitude=_SELECTION_ORDER_OF_MAGNITUDE,
    )


@dataclass(frozen=True)
class RealizedPlan:
    """realized split（secret 依存）が確定した後の、当該 campaign の実 work
    unit 件数（`plan_counts()` の設計値と対で dry-run report に表示する）。"""

    c1_render_instances: int
    c4_render_instances: int
    c2_baseline_instances: int
    c3a_instances: int
    c3b_instances_by_family: Mapping[str, int]


def realized_plan(
    matrix_rows: Sequence[MatrixRow], assignment: Mapping[str, Split]
) -> RealizedPlan:
    c1 = enumerate_c1_render_units(matrix_rows, assignment)
    c4 = enumerate_c4_render_units(matrix_rows, assignment)
    c2 = c2_baseline_instances(matrix_rows, assignment)
    c3a = c3a_f0_selection_instances(matrix_rows, assignment)
    c3b = {
        family.value: len(c3b_family_selection_instances(matrix_rows, assignment, family.value))
        for family in FixtureFamily
        if family is not FixtureFamily.F0_CONTROL
    }
    return RealizedPlan(
        c1_render_instances=len({(u.row_id, u.probe_index) for u in c1}),
        c4_render_instances=len({(u.row_id, u.probe_index) for u in c4}),
        c2_baseline_instances=len(c2),
        c3a_instances=len(c3a),
        c3b_instances_by_family=c3b,
    )


__all__ = [
    "Instance",
    "RenderWorkUnit",
    "enumerate_c1_render_units",
    "enumerate_c4_render_units",
    "c2_baseline_instances",
    "c3a_f0_selection_instances",
    "c3b_family_selection_instances",
    "c4_holdout_instances",
    "WITHIN_PROCESS_REPEATS",
    "FRESH_PROCESS_REPEATS",
    "MEASUREMENT_REPEATS_PER_INSTANCE",
    "MeterCallWorkUnit",
    "enumerate_meter_call_units",
    "PlanCounts",
    "plan_counts",
    "RealizedPlan",
    "realized_plan",
]
