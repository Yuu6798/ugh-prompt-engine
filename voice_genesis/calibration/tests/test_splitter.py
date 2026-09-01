from __future__ import annotations

import pytest

from voice_genesis.calibration.splitter import (
    RealizedSplitMap,
    RowInput,
    SwapRecord,
    _repair_coverage,
    _required_pairs,
    realize_split,
    verify_split,
)
from voice_genesis.calibration.vocab import Split

SECRET_A = b"\x11" * 32
SECRET_B = b"\x22" * 32


def _build_48_rows() -> list[RowInput]:
    """family=FAM, 2 strata (S1/S2) x 24 rows each (both n%4==0 -> no
    family-total balancing needed: 24+24=48, per-stratum LR gives 12/6/6 each,
    summing exactly to the family target 24/12/12)."""
    rows: list[RowInput] = []
    truth_cycle = ["t1", "t2", "t3"]
    impl_cycle = ["implA", "implB"]
    for stratum in ("S1", "S2"):
        for i in range(24):
            rows.append(
                RowInput(
                    row_id=f"{stratum}-row-{i:03d}",
                    family="FAM",
                    stratum={"stratum_key": stratum},
                    truth_level=truth_cycle[i % 3],
                    generator_impl=impl_cycle[i % 2],
                    boundary_class=None,
                )
            )
    return rows


def test_realize_split_is_deterministic_across_calls() -> None:
    rows = _build_48_rows()
    a = realize_split(rows, SECRET_A, ["stratum_key"])
    b = realize_split(rows, SECRET_A, ["stratum_key"])
    assert dict(a.assignment) == dict(b.assignment)
    assert a.swaps == b.swaps
    assert a.realized_sha == b.realized_sha


def test_realize_split_exact_50_25_25_counts() -> None:
    rows = _build_48_rows()
    realized = realize_split(rows, SECRET_A, ["stratum_key"])
    counts = {Split.CALIBRATION: 0, Split.SELECTION: 0, Split.HOLDOUT: 0}
    for split in realized.assignment.values():
        counts[split] += 1
    assert counts[Split.CALIBRATION] == 24
    assert counts[Split.SELECTION] == 12
    assert counts[Split.HOLDOUT] == 12
    assert sum(counts.values()) == 48


def test_realize_split_assigns_every_row() -> None:
    rows = _build_48_rows()
    realized = realize_split(rows, SECRET_A, ["stratum_key"])
    assert set(realized.assignment.keys()) == {r.row_id for r in rows}


def test_verify_split_round_trip_true_on_unmodified() -> None:
    rows = _build_48_rows()
    realized = realize_split(rows, SECRET_A, ["stratum_key"])
    assert verify_split(rows, SECRET_A, realized) is True


def test_verify_split_round_trip_false_on_tampered_assignment() -> None:
    rows = _build_48_rows()
    realized = realize_split(rows, SECRET_A, ["stratum_key"])
    tampered_assignment = dict(realized.assignment)
    some_row = next(iter(tampered_assignment))
    # 明示的に別の split へ書き換える（tamper）。
    for candidate in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
        if candidate != tampered_assignment[some_row]:
            tampered_assignment[some_row] = candidate
            break
    tampered = RealizedSplitMap(
        stratum_factor_names=realized.stratum_factor_names,
        assignment=tampered_assignment,
        swaps=realized.swaps,
        realized_sha=realized.realized_sha,  # sha は元のまま (再計算していない)
    )
    assert verify_split(rows, SECRET_A, tampered) is False


def test_different_secrets_give_different_assignment() -> None:
    rows = _build_48_rows()
    a = realize_split(rows, SECRET_A, ["stratum_key"])
    b = realize_split(rows, SECRET_B, ["stratum_key"])
    assert dict(a.assignment) != dict(b.assignment)


def test_duplicate_row_id_is_rejected() -> None:
    rows = _build_48_rows()
    duplicate = RowInput(
        row_id=rows[0].row_id,  # 既存 row_id を再利用 -> 重複
        family="FAM",
        stratum={"stratum_key": "S1"},
        truth_level="t1",
        generator_impl="implA",
    )
    with pytest.raises(ValueError, match="duplicate row_id"):
        realize_split(rows + [duplicate], SECRET_A, ["stratum_key"])


def test_stratum_split_counts_closed_form_all_remainders() -> None:
    from voice_genesis.calibration.splitter import _stratum_split_counts

    # r=0: n=8 -> (4,2,2)
    assert _stratum_split_counts(8, 0) == {
        Split.CALIBRATION: 4,
        Split.SELECTION: 2,
        Split.HOLDOUT: 2,
    }
    # r=1: n=9 -> (5,2,2)
    assert _stratum_split_counts(9, 0) == {
        Split.CALIBRATION: 5,
        Split.SELECTION: 2,
        Split.HOLDOUT: 2,
    }
    # r=2: n=10, tie_bit=0 -> SEL gets the extra unit -> (5,3,2)
    assert _stratum_split_counts(10, 0) == {
        Split.CALIBRATION: 5,
        Split.SELECTION: 3,
        Split.HOLDOUT: 2,
    }
    # r=2: n=10, tie_bit=1 -> HOLD gets the extra unit -> (5,2,3)
    assert _stratum_split_counts(10, 1) == {
        Split.CALIBRATION: 5,
        Split.SELECTION: 2,
        Split.HOLDOUT: 3,
    }
    # r=3: n=11 -> both SEL and HOLD get the extra unit -> (5,3,3)
    assert _stratum_split_counts(11, 0) == {
        Split.CALIBRATION: 5,
        Split.SELECTION: 3,
        Split.HOLDOUT: 3,
    }
    # n=1: singleton stratum always goes entirely to CALIBRATION
    assert _stratum_split_counts(1, 0) == {
        Split.CALIBRATION: 1,
        Split.SELECTION: 0,
        Split.HOLDOUT: 0,
    }


def test_coverage_repair_white_box_swap_path_is_exercised() -> None:
    """coverage repair (`_repair_coverage`) を直接、手作りの違反シナリオで検証する。

    3 件の "rare" 行が全て CALIBRATION に集中しており、SELECTION/HOLDOUT には
    それぞれ 1 件ずつ既に "common"（3 件、こちらも要求ペア）が正しく配置され、
    さらに使い捨ての "filler" 行（要求ペア閾値未満なので制約対象外）を SEL/HOLD
    に 1 件ずつ加えてある。これにより donor(rare) を CAL から SEL/HOLD へ移す際、
    victim として安全な filler 行が選ばれ、既に満たされている "common" の被覆を
    壊さずに repair が完了できる（設計正本 §7 のカバレッジ制約 + 決定的最小 swap
    修復の実地検証）。
    """
    rows = [
        RowInput(row_id="r1", family="FAM", stratum={}, truth_level="rare"),
        RowInput(row_id="r2", family="FAM", stratum={}, truth_level="rare"),
        RowInput(row_id="r3", family="FAM", stratum={}, truth_level="rare"),
        RowInput(row_id="c1", family="FAM", stratum={}, truth_level="common"),
        RowInput(row_id="c2", family="FAM", stratum={}, truth_level="common"),
        RowInput(row_id="c3", family="FAM", stratum={}, truth_level="common"),
        RowInput(row_id="f1", family="FAM", stratum={}, truth_level="filler1"),
        RowInput(row_id="f2", family="FAM", stratum={}, truth_level="filler2"),
    ]
    rows_by_id = {r.row_id: r for r in rows}
    assignment: dict[str, Split] = {
        "r1": Split.CALIBRATION,
        "r2": Split.CALIBRATION,
        "r3": Split.CALIBRATION,
        "c1": Split.CALIBRATION,
        "c2": Split.SELECTION,
        "c3": Split.HOLDOUT,
        "f1": Split.SELECTION,
        "f2": Split.HOLDOUT,
    }
    required_pairs = _required_pairs(rows)
    assert ("truth_level", "rare") in required_pairs
    assert ("truth_level", "common") in required_pairs
    assert ("truth_level", "filler1") not in required_pairs  # count=1 < 3

    swaps = _repair_coverage(rows_by_id, assignment, SECRET_A, required_pairs)

    assert len(swaps) > 0
    assert all(isinstance(s, SwapRecord) for s in swaps)
    assert all(s.reason == "coverage" for s in swaps)

    # 事後条件: rare と common は 3 split すべてに最低 1 件ずつ存在する。
    for value in ("rare", "common"):
        for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
            present = any(
                rows_by_id[rid].truth_level == value
                for rid, s in assignment.items()
                if s == split
            )
            assert present, f"{value} missing from {split} after repair"

    # 総 row 数と各行の所属先集合は保存される（swap は所属の交換のみ）。
    assert set(assignment.keys()) == set(rows_by_id.keys())


def test_realize_split_end_to_end_coverage_guarantee_via_singleton_strata() -> None:
    """singleton stratum (n=1) は常に CALIBRATION へ確定的に割り当てられる
    (`_stratum_split_counts(1, *) == {CAL:1, SEL:0, HOLD:0}`、HMAC 依存なし)。
    これを利用して "rare" truth_level の 3 行を確定的に全て CALIBRATION に
    集中させ、bulk stratum で家族合計を揃える。coverage 制約により最終的な
    realize_split 結果では rare が 3 split 全てに現れることが、実装のループ
    不変条件として保証される（機構が family_total 補正か coverage repair の
    どちらであっても、最終事後条件は同一）。
    """
    rows: list[RowInput] = []
    for i in range(3):
        rows.append(
            RowInput(
                row_id=f"rare-{i}",
                family="FAM2",
                stratum={"stratum_key": f"solo-{i}"},
                truth_level="rare",
                generator_impl="G1",
            )
        )
    for i in range(45):
        rows.append(
            RowInput(
                row_id=f"bulk-{i:03d}",
                family="FAM2",
                stratum={"stratum_key": "bulk"},
                truth_level="common" if i % 2 == 0 else "common2",
                generator_impl="G1" if i % 2 == 0 else "G2",
            )
        )

    realized = realize_split(rows, SECRET_A, ["stratum_key"])
    rows_by_id = {r.row_id: r for r in rows}

    for split in (Split.CALIBRATION, Split.SELECTION, Split.HOLDOUT):
        present = any(
            rows_by_id[rid].truth_level == "rare"
            for rid, s in realized.assignment.items()
            if s == split
        )
        assert present, f"rare missing from {split}"

    counts = {Split.CALIBRATION: 0, Split.SELECTION: 0, Split.HOLDOUT: 0}
    for s in realized.assignment.values():
        counts[s] += 1
    assert sum(counts.values()) == 48
