"""test_simplex.py — VG-E0 単体幾何（`simplex.py`）のテスト。

DESIGN_VG_E0.md §7 AC「simplex 演算（normalize / L1距離 / セル割当）の
プロパティテスト（網羅・非重複・丸め安定・決定論）」を直接検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models  # noqa: E402
import simplex  # noqa: E402

_finite_float = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False, width=32,
)


# --- normalize() ------------------------------------------------------------


def test_normalize_rejects_nan() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        simplex.normalize({"ritsu": float("nan"), "pjs": 0.5, "user": 0.5})


def test_normalize_rejects_inf() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        simplex.normalize({"ritsu": float("inf"), "pjs": 0.5, "user": 0.5})


def test_normalize_already_valid_point_is_stable() -> None:
    c = simplex.normalize({"ritsu": 0.5, "pjs": 0.3, "user": 0.2})
    assert (c.ritsu, c.pjs, c.user) == (0.5, 0.3, 0.2)


def test_normalize_clamps_negative_and_absorbs_into_max_raw_component() -> None:
    # raw sum = 0.3 - 0.05 + 0.6 = 0.85。クランプ後 (pjs: -0.05→0) の合計は
    # 0.3+0+0.6=0.9 で、まだ 1.0 に 0.1 足りない（クランプは負値を 0 へ
    # 引き上げるだけで質量を生成しないため、raw sum<1 の分の不足は残る）。
    # 不足分 0.1 は raw 最大成分（user=0.6）へ吸収される。
    c = simplex.normalize({"ritsu": 0.3, "pjs": -0.05, "user": 0.6})
    assert c.ritsu == pytest.approx(0.3, abs=1e-9)
    assert c.pjs == pytest.approx(0.0, abs=1e-9)
    assert c.user == pytest.approx(0.7, abs=1e-9)
    assert round(c.ritsu + c.pjs + c.user, 6) == 1.0


def test_normalize_rejects_wildly_off_simplex_input() -> None:
    """raw が単体から大きく外れ、残差吸収先の成分まで負に落ちる場合は
    fail-closed で例外にする（際限なく救済しない）。"""
    with pytest.raises(ValueError, match="negative"):
        simplex.normalize({"ritsu": 0.0, "pjs": 2.0, "user": 2.0})


def test_normalize_center_point_deterministic_tiebreak_favors_ritsu() -> None:
    c = simplex.normalize({"ritsu": 1.0 / 3.0, "pjs": 1.0 / 3.0, "user": 1.0 / 3.0})
    assert c.ritsu == 0.333334
    assert c.pjs == 0.333333
    assert c.user == 0.333333


@given(
    r=st.floats(min_value=-0.5, max_value=1.5, allow_nan=False, allow_infinity=False),
    p=st.floats(min_value=-0.5, max_value=1.5, allow_nan=False, allow_infinity=False),
    u=st.floats(min_value=-0.5, max_value=1.5, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200)
def test_normalize_output_always_valid_simplex_point(r: float, p: float, u: float) -> None:
    # normalize() は「既に Δ² 上（またはその近傍）にある生値の丸め+射影」を
    # 契約とする（全オペレータ出力＝親座標±小さな摂動が入力）。任意の raw
    # 値（例: 全成分が単体から大きく外れた (0,2,2)）まで残差吸収で救うのは
    # 契約外 — 吸収先の成分が負に落ちる場合は明示的に例外にする（simplex.py
    # の docstring 参照）ため、ここでは「合計がおおよそ1に近い」入力域に
    # 限定してプロパティを検証する。
    assume(abs(r + p + u - 1.0) < 0.5)
    c = simplex.normalize({"ritsu": r, "pjs": p, "user": u})
    for v in (c.ritsu, c.pjs, c.user):
        assert v >= 0.0
        assert round(v, 6) == v
    assert abs(c.ritsu + c.pjs + c.user - 1.0) < 1e-9


def test_normalize_is_deterministic() -> None:
    a = simplex.normalize({"ritsu": 0.123456789, "pjs": 0.2, "user": 0.676543211})
    b = simplex.normalize({"ritsu": 0.123456789, "pjs": 0.2, "user": 0.676543211})
    assert a == b


def test_normalize_accepts_models_coords_input() -> None:
    a = simplex.normalize(models.Coords(0.5, 0.3, 0.2))
    b = simplex.normalize({"ritsu": 0.5, "pjs": 0.3, "user": 0.2})
    assert a == b


# --- l1_distance() -----------------------------------------------------------


def test_l1_distance_zero_for_identical_point() -> None:
    c = models.Coords(0.5, 0.3, 0.2)
    assert simplex.l1_distance(c, c) == 0.0


def test_l1_distance_symmetric() -> None:
    a = models.Coords(1.0, 0.0, 0.0)
    b = models.Coords(0.0, 1.0, 0.0)
    assert simplex.l1_distance(a, b) == simplex.l1_distance(b, a)


def test_l1_distance_between_vertices_is_two() -> None:
    a = models.Coords(1.0, 0.0, 0.0)
    b = models.Coords(0.0, 1.0, 0.0)
    assert simplex.l1_distance(a, b) == 2.0


def test_l1_distance_rejects_nonfinite() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        simplex.l1_distance({"ritsu": float("nan"), "pjs": 0.5, "user": 0.5}, models.Coords(1, 0, 0))


# --- assign_lineage() --------------------------------------------------------


@pytest.mark.parametrize(
    "coords,expected",
    [
        ({"ritsu": 0.55, "pjs": 0.30, "user": 0.15}, "L-R"),
        ({"ritsu": 0.30, "pjs": 0.55, "user": 0.15}, "L-P"),
        ({"ritsu": 0.15, "pjs": 0.30, "user": 0.55}, "L-U"),
        ({"ritsu": 0.549999, "pjs": 0.450001, "user": 0.0}, "L-C"),
        ({"ritsu": 1.0 / 3.0, "pjs": 1.0 / 3.0, "user": 1.0 / 3.0}, "L-C"),
        ({"ritsu": 0.5, "pjs": 0.5, "user": 0.0}, "L-C"),  # 中点=2ドナー拮抗はL-C
        ({"ritsu": 1.0, "pjs": 0.0, "user": 0.0}, "L-R"),
    ],
)
def test_assign_lineage_threshold(coords: dict, expected: str) -> None:
    assert simplex.assign_lineage(coords) == expected


def test_assign_lineage_never_returns_novelty() -> None:
    """NOVELTY は座標のみからは導出できない（operators.py の責務）。"""
    for r, p, u in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1 / 3, 1 / 3, 1 / 3)):
        assert simplex.assign_lineage({"ritsu": r, "pjs": p, "user": u}) != "NOVELTY"


# --- cell_id() / all_cell_ids(): 網羅・非重複 --------------------------------


def test_all_cell_ids_count_is_n_squared() -> None:
    assert len(simplex.all_cell_ids(5)) == 25


def test_all_cell_ids_index_bounds() -> None:
    for i, j, k in simplex.all_cell_ids(5):
        assert 0 <= i <= 4
        assert 0 <= j <= 4
        assert 0 <= k <= 4


def test_cell_id_always_within_all_cell_ids() -> None:
    valid = simplex.all_cell_ids(5)
    for a in range(0, 101, 5):
        for b in range(0, 101 - a, 5):
            c = 100 - a - b
            coords = simplex.normalize({"ritsu": a / 100.0, "pjs": b / 100.0, "user": c / 100.0})
            assert simplex.cell_id(coords) in valid


def test_all_25_cells_are_reachable() -> None:
    """全セル25種が到達可能（dense サンプリングで踏破を確認）。"""
    reached = set()
    for a in range(101):
        for b in range(101 - a):
            c = 100 - a - b
            coords = simplex.normalize({"ritsu": a / 100.0, "pjs": b / 100.0, "user": c / 100.0})
            reached.add(simplex.cell_id(coords))
    assert reached == simplex.all_cell_ids(5)


def test_cell_id_vertices_map_to_corner_cells() -> None:
    assert simplex.cell_id(models.Coords(1.0, 0.0, 0.0)) == (4, 0, 0)
    assert simplex.cell_id(models.Coords(0.0, 1.0, 0.0)) == (0, 4, 0)
    assert simplex.cell_id(models.Coords(0.0, 0.0, 1.0)) == (0, 0, 4)


def test_cell_id_lattice_boundary_point_resolves_via_lexicographic_rule() -> None:
    """r=0.2, p=0.2, u=0.6 は3軸同時に格子線上（design §3.2 の「境界は辞書順
    で下側セルへ割当」が適用される唯一のケース: naive floor では
    (1,1,3)（合計5=N）となり無効。辞書順（ritsu→pjs→user）で最初の非ゼロ
    軸=ritsuを1減じ、(0,1,3)（合計4=N-1）へ落ちる。"""
    coords = models.Coords(0.2, 0.2, 0.6)
    assert simplex.cell_id(coords) == (0, 1, 3)
    assert simplex.cell_id(coords) in simplex.all_cell_ids(5)


def test_cell_id_boundary_between_two_cells_goes_to_lower_index() -> None:
    # r=0.4 exactly: 境界は floor(0.4*5)=2 側（[0.4,0.6) の下端）に割り当て
    # られる。u が非格子値のため sum(idx)=n-1 の通常ケース（補正不要）。
    coords = simplex.normalize({"ritsu": 0.4, "pjs": 0.35, "user": 0.25})
    i, _, _ = simplex.cell_id(coords)
    assert i == 2


def test_cell_id_rejects_nonfinite() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        simplex.cell_id({"ritsu": float("nan"), "pjs": 0.5, "user": 0.5})


@given(
    a=st.integers(min_value=0, max_value=200),
    b=st.integers(min_value=0, max_value=200),
)
@settings(max_examples=300)
def test_cell_id_property_always_valid_and_deterministic(a: int, b: int) -> None:
    """任意座標が exactly 1 セルへ決定論的に割り当たる（同一入力→同一
    セル、かつ常に有効な25セルのいずれか）。"""
    total = a + b
    if total > 400:
        return
    c = 400 - total
    coords = simplex.normalize({"ritsu": a / 400.0, "pjs": b / 400.0, "user": c / 400.0})
    valid = simplex.all_cell_ids(5)
    cell1 = simplex.cell_id(coords)
    cell2 = simplex.cell_id(coords)
    assert cell1 == cell2
    assert cell1 in valid


def test_cell_id_requires_grid_size_dividing_micro_unit() -> None:
    with pytest.raises(ValueError, match="evenly divide"):
        simplex.cell_id(models.Coords(0.5, 0.3, 0.2), n=7)
