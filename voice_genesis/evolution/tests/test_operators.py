"""test_operators.py — VG-E0 変異・交配オペレータ5種（`operators.py`）の
テスト。

DESIGN_VG_E0.md §7 AC「オペレータ5種の決定論テスト（同一入力→バイト同一
の genome）+ 単体内保証 + 系統間 vertex_pull の NOVELTY 隔離」を直接検証
する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bootstrap  # noqa: E402
import models  # noqa: E402
import operators  # noqa: E402
import simplex  # noqa: E402


@pytest.fixture()
def founders():
    return bootstrap.founder_genomes()


def _assert_valid_simplex_member(genome: models.VoiceGenome) -> None:
    total = genome.coords.ritsu + genome.coords.pjs + genome.coords.user
    assert genome.coords.ritsu >= 0.0
    assert genome.coords.pjs >= 0.0
    assert genome.coords.user >= 0.0
    assert abs(total - 1.0) < 1e-9


# --- drift --------------------------------------------------------------


def test_drift_deterministic_byte_identical(founders) -> None:
    ritsu, _pjs, _usr, center = founders
    a = operators.drift(center, rng_seed=42, step=0.05)
    b = operators.drift(center, rng_seed=42, step=0.05)
    assert models.genome_to_json(a) == models.genome_to_json(b)
    assert a.genome_id == b.genome_id


def test_drift_different_seed_different_result(founders) -> None:
    *_ , center = founders
    a = operators.drift(center, rng_seed=1, step=0.05)
    b = operators.drift(center, rng_seed=2, step=0.05)
    assert a.genome_id != b.genome_id


def test_drift_stays_within_simplex(founders) -> None:
    ritsu, _pjs, _usr, center = founders
    child = operators.drift(ritsu, rng_seed=3, step=models.DRIFT_STEP_MAX)
    _assert_valid_simplex_member(child)


def test_drift_rejects_step_above_max(founders) -> None:
    *_ , center = founders
    with pytest.raises(ValueError, match="step"):
        operators.drift(center, rng_seed=1, step=models.DRIFT_STEP_MAX + 0.01)


def test_drift_records_parent_and_generation(founders) -> None:
    *_ , center = founders
    child = operators.drift(center, rng_seed=1, step=0.02)
    assert child.parents == (center.genome_id,)
    assert child.generation == center.generation + 1
    assert child.operator == "drift"
    assert child.seed == center.seed  # 演奏 seed は不変


# --- vertex_pull ----------------------------------------------------------


def test_vertex_pull_deterministic_byte_identical(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    a = operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=0.1)
    b = operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=0.1)
    assert models.genome_to_json(a) == models.genome_to_json(b)


def test_vertex_pull_cross_lineage_forces_novelty(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    assert ritsu.lineage != pjs.lineage
    child = operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=0.1)
    assert child.lineage == "NOVELTY"


def test_vertex_pull_same_lineage_uses_coordinate_lineage(founders) -> None:
    ritsu, _pjs, _usr, _center = founders
    sibling = operators.drift(ritsu, rng_seed=9, step=0.01)
    assert sibling.lineage == ritsu.lineage == "L-R"
    child = operators.vertex_pull(ritsu, sibling, weight=0.5, vertex="user", pull=0.1)
    assert child.lineage == simplex.assign_lineage(child.coords)
    assert child.lineage != "NOVELTY"


def test_vertex_pull_at_pull_1_reaches_vertex_exactly(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    sibling = operators.drift(ritsu, rng_seed=1, step=0.01)
    child = operators.vertex_pull(ritsu, sibling, weight=0.5, vertex="user", pull=models.VERTEX_PULL_PULL_MAX)
    _assert_valid_simplex_member(child)


def test_vertex_pull_rejects_pull_above_max(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    with pytest.raises(ValueError, match="pull"):
        operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=models.VERTEX_PULL_PULL_MAX + 0.01)


def test_vertex_pull_records_both_parents(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    child = operators.vertex_pull(ritsu, pjs, weight=0.3, vertex="ritsu", pull=0.05)
    assert child.parents == (ritsu.genome_id, pjs.genome_id)
    assert child.generation == max(ritsu.generation, pjs.generation) + 1


# --- reseed -----------------------------------------------------------------


def test_reseed_preserves_coords_changes_seed(founders) -> None:
    *_ , center = founders
    child = operators.reseed(center, new_seed=123)
    assert child.coords == center.coords
    assert child.seed == 123
    assert child.seed != center.seed


def test_reseed_deterministic(founders) -> None:
    *_ , center = founders
    a = operators.reseed(center, new_seed=7)
    b = operators.reseed(center, new_seed=7)
    assert a.genome_id == b.genome_id


def test_reseed_reverts_novelty_parent_to_coordinate_lineage(founders) -> None:
    """DESIGN_VG_E0.md §3.1「次世代で座標帰属に復帰」: NOVELTY 親を reseed
    すると、子は座標由来の lineage へ復帰する（NOVELTY を継承しない）。"""
    *_ , center = founders
    novel_parent = operators.novelty_jump(center, rng_seed=5)
    assert novel_parent.lineage == "NOVELTY"
    child = operators.reseed(novel_parent, new_seed=1)
    assert child.lineage == simplex.assign_lineage(novel_parent.coords)
    assert child.lineage != "NOVELTY"  # assign_lineage() は NOVELTY を返さない


# --- edge_walk ----------------------------------------------------------


def test_edge_walk_deterministic_byte_identical(founders) -> None:
    *_ , center = founders
    a = operators.edge_walk(center, rng_seed=11, edge=("ritsu", "pjs"), step=0.05)
    b = operators.edge_walk(center, rng_seed=11, edge=("ritsu", "pjs"), step=0.05)
    assert models.genome_to_json(a) == models.genome_to_json(b)


def test_edge_walk_holds_third_axis_approximately_fixed(founders) -> None:
    """辺 (ritsu,pjs) に沿った移動は user 成分を変えない（丸め/残差吸収に
    よる 1e-6 桁の揺れのみ許容）。"""
    *_ , center = founders
    child = operators.edge_walk(center, rng_seed=3, edge=("ritsu", "pjs"), step=0.08)
    assert abs(child.coords.user - center.coords.user) < 1e-5


def test_edge_walk_stays_within_simplex(founders) -> None:
    ritsu, _pjs, _usr, _center = founders
    child = operators.edge_walk(ritsu, rng_seed=4, edge=("ritsu", "user"), step=models.EDGE_WALK_STEP_MAX)
    _assert_valid_simplex_member(child)


def test_edge_walk_rejects_duplicate_edge_endpoints(founders) -> None:
    *_ , center = founders
    with pytest.raises(ValueError, match="distinct"):
        operators.edge_walk(center, rng_seed=1, edge=("ritsu", "ritsu"), step=0.02)


def test_edge_walk_rejects_step_above_max(founders) -> None:
    *_ , center = founders
    with pytest.raises(ValueError, match="step"):
        operators.edge_walk(center, rng_seed=1, edge=("ritsu", "pjs"), step=models.EDGE_WALK_STEP_MAX + 0.01)


# --- novelty_jump -------------------------------------------------------


def test_novelty_jump_deterministic_byte_identical(founders) -> None:
    *_ , center = founders
    a = operators.novelty_jump(center, rng_seed=77)
    b = operators.novelty_jump(center, rng_seed=77)
    assert models.genome_to_json(a) == models.genome_to_json(b)


def test_novelty_jump_always_novelty_lineage(founders) -> None:
    ritsu, pjs, usr, center = founders
    for parent in (ritsu, pjs, usr, center):
        child = operators.novelty_jump(parent, rng_seed=1)
        assert child.lineage == "NOVELTY"


def test_novelty_jump_stays_within_simplex(founders) -> None:
    *_ , center = founders
    for seed in range(20):
        child = operators.novelty_jump(center, rng_seed=seed)
        _assert_valid_simplex_member(child)


def test_novelty_jump_different_seeds_differ(founders) -> None:
    *_ , center = founders
    a = operators.novelty_jump(center, rng_seed=1)
    b = operators.novelty_jump(center, rng_seed=2)
    assert a.genome_id != b.genome_id
