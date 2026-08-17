"""test_archive.py — VG-E0 MAP-Elites 型 Archive（`archive.py`）のテスト。

DESIGN_VG_E0.md §7 AC「Archive の elite 更新・保護スロット・追い出し記録の
テスト」を直接検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import archive as archive_mod  # noqa: E402
import bootstrap  # noqa: E402
import operators  # noqa: E402
import simplex  # noqa: E402


@pytest.fixture()
def founders():
    return bootstrap.founder_genomes()


def test_submit_empty_cell_becomes_elite_no_eviction(founders) -> None:
    ritsu, *_ = founders
    arc = archive_mod.Archive()
    result = arc.submit(ritsu, 0.8, quality_floor=0.5)
    assert result == "elite"
    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell).genome_id == ritsu.genome_id
    assert arc.eviction_log == []


def test_submit_higher_quality_evicts_previous_elite(founders) -> None:
    ritsu, *_ = founders
    arc = archive_mod.Archive()
    arc.submit(ritsu, 0.5, quality_floor=0.3)
    challenger = operators.drift(ritsu, rng_seed=1, step=0.01)
    assert simplex.cell_id(challenger.coords) == simplex.cell_id(ritsu.coords)
    result = arc.submit(challenger, 0.9, quality_floor=0.3)
    assert result == "elite"
    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell).genome_id == challenger.genome_id
    assert len(arc.eviction_log) == 1
    ev = arc.eviction_log[0]
    assert ev.slot == "elite"
    assert ev.evicted_genome_id == ritsu.genome_id
    assert ev.incoming_genome_id == challenger.genome_id
    assert ev.cell == cell


def test_submit_lower_quality_elite_rejected_no_eviction(founders) -> None:
    ritsu, *_ = founders
    arc = archive_mod.Archive()
    arc.submit(ritsu, 0.9, quality_floor=0.3)
    challenger = operators.drift(ritsu, rng_seed=1, step=0.01)
    result = arc.submit(challenger, 0.4, quality_floor=0.3)
    assert result == "rejected"
    assert arc.eviction_log == []
    cell = simplex.cell_id(ritsu.coords)
    assert arc.elite_at(cell).genome_id == ritsu.genome_id


def test_submit_below_floor_lineage_unique_goes_to_protected(founders) -> None:
    ritsu, pjs, *_ = founders
    arc = archive_mod.Archive()
    # ritsu (L-R) は elite が無いので below-floor でも lineage-unique →保護スロット
    result = arc.submit(ritsu, 0.1, quality_floor=0.5)
    assert result == "protected"
    cell = simplex.cell_id(ritsu.coords)
    assert arc.protected_at(cell).genome_id == ritsu.genome_id
    assert arc.elite_at(cell) is None


def test_submit_below_floor_non_unique_lineage_rejected(founders) -> None:
    ritsu, *_ = founders
    arc = archive_mod.Archive()
    arc.submit(ritsu, 0.9, quality_floor=0.5)  # L-R の elite を確立
    weak_sibling = operators.drift(ritsu, rng_seed=2, step=0.01)
    assert weak_sibling.lineage == "L-R"
    result = arc.submit(weak_sibling, 0.1, quality_floor=0.5)
    assert result == "rejected"


def test_protected_slot_replacement_records_eviction(founders) -> None:
    ritsu, *_ = founders
    arc = archive_mod.Archive()
    weak_a = operators.drift(ritsu, rng_seed=1, step=0.005)
    weak_b = operators.drift(ritsu, rng_seed=2, step=0.005)
    assert simplex.cell_id(weak_a.coords) == simplex.cell_id(weak_b.coords) == simplex.cell_id(ritsu.coords)
    arc.submit(weak_a, 0.1, quality_floor=0.9)
    result = arc.submit(weak_b, 0.2, quality_floor=0.9)
    assert result == "protected"
    assert len(arc.eviction_log) == 1
    ev = arc.eviction_log[0]
    assert ev.slot == "protected"
    assert ev.evicted_genome_id == weak_a.genome_id
    assert ev.incoming_genome_id == weak_b.genome_id


def test_occupancy_counts(founders) -> None:
    ritsu, pjs, usr, center = founders
    arc = archive_mod.Archive()
    arc.submit(ritsu, 0.9, quality_floor=0.5)
    arc.submit(pjs, 0.9, quality_floor=0.5)
    occ = arc.occupancy()
    assert occ["n_cells"] == 25
    assert occ["elite_occupied"] == 2
    assert occ["protected_occupied"] == 0
    assert occ["elite_coverage"] == pytest.approx(2 / 25)
    assert occ["lineages_with_elite"] == ["L-P", "L-R"]


def test_submit_rejects_nonfinite_quality(founders) -> None:
    ritsu, *_ = founders
    arc = archive_mod.Archive()
    with pytest.raises(ValueError, match="non-finite"):
        arc.submit(ritsu, float("nan"), quality_floor=0.5)


def test_all_cells_initially_empty() -> None:
    arc = archive_mod.Archive()
    assert arc.cells() == simplex.all_cell_ids(5)
    for cell in arc.cells():
        assert arc.elite_at(cell) is None
        assert arc.protected_at(cell) is None
