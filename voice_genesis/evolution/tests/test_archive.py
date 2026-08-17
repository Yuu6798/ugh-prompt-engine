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
import models  # noqa: E402
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


# --- PR #267 Codex R5 指摘2（P2）: 保護スロットの stale 占有 ----------------


def test_elite_acceptance_evicts_stale_protected_slot_of_same_lineage(founders) -> None:
    """L-R の保護個体が cell A のスロットを占有した後、L-R の elite が別の
    cell B に受理されると、L-R はもはや「唯一」ではないため cell A の
    保護個体は evict される（追い出しイベント記録付き）。evict 後は、
    それまで quality 比較で弾かれていた未代表系統（NOVELTY）の個体が
    より低品質でも cell A の保護スロットへ入れる。
    """
    arc = archive_mod.Archive()

    # cell A の L-R 保護個体（elite が無いので below-floor でも保護スロットへ）。
    protected_occupant = models.build_genome(
        coords=models.Coords(0.9, 0.05, 0.05), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    cell_a = simplex.cell_id(protected_occupant.coords)
    result = arc.submit(protected_occupant, 0.2, quality_floor=0.9)
    assert result == "protected"
    assert arc.protected_at(cell_a).genome_id == protected_occupant.genome_id

    # cell A に着地する NOVELTY 個体は、まだ protected_occupant (0.2) に
    # quality で劣るため rejected（修正前後で不変の前提条件）。
    novelty_challenger = models.build_genome(
        coords=protected_occupant.coords, seed=1, lineage="NOVELTY", generation=1,
        parents=(protected_occupant.genome_id,), operator="novelty_jump",
        operator_params={"rng_seed": 1},
    )
    assert simplex.cell_id(novelty_challenger.coords) == cell_a
    assert arc.submit(novelty_challenger, 0.1, quality_floor=0.9) == "rejected"

    # 別 cell (B) で L-R の elite が受理される（quality >= floor）。
    lr_elite = models.build_genome(
        coords=models.Coords(0.6, 0.3, 0.1), seed=0, lineage="L-R", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    cell_b = simplex.cell_id(lr_elite.coords)
    assert cell_b != cell_a
    result = arc.submit(lr_elite, 0.95, quality_floor=0.9)
    assert result == "elite"
    assert arc.elite_at(cell_b).genome_id == lr_elite.genome_id

    # cell A の L-R 保護個体は evict され、追い出しイベントが記録されている。
    assert arc.protected_at(cell_a) is None
    stale_evictions = [
        ev for ev in arc.eviction_log
        if ev.reason == "lineage_no_longer_unique" and ev.evicted_genome_id == protected_occupant.genome_id
    ]
    assert len(stale_evictions) == 1
    ev = stale_evictions[0]
    assert ev.slot == "protected"
    assert ev.cell == cell_a
    assert ev.incoming_genome_id == lr_elite.genome_id

    # 以後、NOVELTY 個体はより低品質のままでも cell A の保護スロットに入れる。
    result = arc.submit(novelty_challenger, 0.1, quality_floor=0.9)
    assert result == "protected"
    assert arc.protected_at(cell_a).genome_id == novelty_challenger.genome_id


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
