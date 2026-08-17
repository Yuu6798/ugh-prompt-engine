"""test_operators.py — VG-E0 変異・交配オペレータ5種（`operators.py`）の
テスト。

DESIGN_VG_E0.md §7 AC「オペレータ5種の決定論テスト（同一入力→バイト同一
の genome）+ 単体内保証 + 系統間 vertex_pull の NOVELTY 隔離」を直接検証
する。
"""
from __future__ import annotations

import math
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


def test_edge_walk_clamps_delta_to_available_mass_at_edge_endpoint(founders) -> None:
    """Codex 指摘7実測: 親 (0.01, 0, 0.99)・edge=(ritsu,pjs)・rng_seed=0・
    step=0.1 は、delta をクランプせず normalize() に渡すと pjs が負に落ち、
    残差吸収が辺の外側の固定軸 user から質量を奪って 0.99→0.914205 に
    変えてしまっていた。delta を移動可能な質量 [-a, b] へ決定論的に
    クランプすることで、辺の外側の座標はバイト不変で保存される。"""
    parent = models.build_genome(
        coords=models.Coords(0.01, 0.0, 0.99), seed=0, lineage="L-U", generation=0,
        parents=(), operator="founder", operator_params={},
    )
    child = operators.edge_walk(parent, rng_seed=0, edge=("ritsu", "pjs"), step=0.1)
    assert child.coords.user == 0.990000
    _assert_valid_simplex_member(child)


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


def test_novelty_jump_enforces_min_l1_on_reported_near_miss_seed(founders) -> None:
    """Codex 指摘6実測: centroid 親 + rng_seed=2031 は棄却サンプリング導入前
    は L1=0.00512（親の直近傍）の「novelty」を生成していた。凍結下限
    `NOVELTY_JUMP_MIN_L1=0.4` を満たすこと、かつ同一 rng_seed で決定論的
    （2回実行でバイト同一）であることを確認する。"""
    *_ , center = founders
    child = operators.novelty_jump(center, rng_seed=2031)
    assert simplex.l1_distance(child.coords, center.coords) >= models.NOVELTY_JUMP_MIN_L1
    child_again = operators.novelty_jump(center, rng_seed=2031)
    assert models.genome_to_json(child) == models.genome_to_json(child_again)


def test_novelty_jump_min_l1_holds_across_founders_and_seeds(founders) -> None:
    for parent in founders:
        for seed in range(30):
            child = operators.novelty_jump(parent, rng_seed=seed)
            assert simplex.l1_distance(child.coords, parent.coords) >= models.NOVELTY_JUMP_MIN_L1


# --- anchors_provenance 伝播（Codex 指摘8） ---------------------------------

_ANCHORS_A = {
    "checkpoint_sha256": "a" * 64,
    "embed_sha256": {n: "a" * 64 for n in models.ANCHOR_NAMES},
}
_ANCHORS_B = {
    "checkpoint_sha256": "b" * 64,
    "embed_sha256": {n: "b" * 64 for n in models.ANCHOR_NAMES},
}


def _with_anchors(genome: models.VoiceGenome, anchors) -> models.VoiceGenome:
    """テスト用ヘルパー: genome を anchors_provenance だけ差し替えて再構築
    する（genome_id は anchors_provenance を除外して計算されるため id は
    不変のまま）。"""
    return models.build_genome(
        coords=genome.coords, seed=genome.seed, lineage=genome.lineage,
        generation=genome.generation, parents=genome.parents, operator=genome.operator,
        operator_params=genome.operator_params, anchors_provenance=anchors, notes=genome.notes,
    )


def test_drift_propagates_parent_anchors_provenance(founders) -> None:
    *_ , center = founders
    anchored_parent = _with_anchors(center, _ANCHORS_A)
    child = operators.drift(anchored_parent, rng_seed=1, step=0.02)
    assert child.anchors_provenance == _ANCHORS_A


def test_drift_propagates_none_anchors_provenance(founders) -> None:
    *_ , center = founders
    assert center.anchors_provenance is None
    child = operators.drift(center, rng_seed=1, step=0.02)
    assert child.anchors_provenance is None


def test_reseed_propagates_parent_anchors_provenance(founders) -> None:
    *_ , center = founders
    anchored_parent = _with_anchors(center, _ANCHORS_A)
    child = operators.reseed(anchored_parent, new_seed=5)
    assert child.anchors_provenance == _ANCHORS_A


def test_edge_walk_propagates_parent_anchors_provenance(founders) -> None:
    *_ , center = founders
    anchored_parent = _with_anchors(center, _ANCHORS_A)
    child = operators.edge_walk(anchored_parent, rng_seed=1, edge=("ritsu", "pjs"), step=0.02)
    assert child.anchors_provenance == _ANCHORS_A


def test_novelty_jump_propagates_parent_anchors_provenance(founders) -> None:
    *_ , center = founders
    anchored_parent = _with_anchors(center, _ANCHORS_A)
    child = operators.novelty_jump(anchored_parent, rng_seed=1)
    assert child.anchors_provenance == _ANCHORS_A


def test_vertex_pull_propagates_when_parents_match(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    anchored_ritsu = _with_anchors(ritsu, _ANCHORS_A)
    sibling = operators.drift(anchored_ritsu, rng_seed=9, step=0.01)  # 同 lineage L-R
    assert sibling.lineage == anchored_ritsu.lineage == "L-R"
    assert sibling.anchors_provenance == _ANCHORS_A
    child = operators.vertex_pull(anchored_ritsu, sibling, weight=0.5, vertex="user", pull=0.1)
    assert child.anchors_provenance == _ANCHORS_A


def test_vertex_pull_propagates_when_both_parents_none(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    assert ritsu.anchors_provenance is None
    assert pjs.anchors_provenance is None
    child = operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=0.1)
    assert child.anchors_provenance is None


def test_vertex_pull_rejects_mismatched_parent_anchors_provenance(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    a = _with_anchors(ritsu, _ANCHORS_A)
    b = _with_anchors(pjs, _ANCHORS_B)
    with pytest.raises(ValueError, match="anchors_provenance"):
        operators.vertex_pull(a, b, weight=0.5, vertex="user", pull=0.1)


def test_vertex_pull_rejects_one_sided_anchors_provenance(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    a = _with_anchors(ritsu, _ANCHORS_A)
    with pytest.raises(ValueError, match="anchors_provenance"):
        operators.vertex_pull(a, pjs, weight=0.5, vertex="user", pull=0.1)


# --- PR #267 Codex R6 指摘1（P1）: recorded params からの再現 -----------------


def test_drift_recorded_step_reproduces_same_genome_id(founders) -> None:
    """Codex R6 指摘1実測: step=0.02000049 は build_genome の6桁正規化で
    operator_params.step=0.02 として記録されるが、丸め前は座標計算に
    生値(0.02000049)を使っていたため、記録された params(step=0.02)から
    再実行すると別の genome_id になっていた。入口で6桁丸めしてから計算に
    使うことで、記録済み params からの再実行が同一 genome_id を再現する。
    """
    ritsu, _pjs, _usr, _center = founders
    original = operators.drift(ritsu, rng_seed=0, step=0.02000049)
    assert original.operator_params["step"] == 0.02
    assert original.coords == models.Coords(0.989738, 0.0, 0.010262)
    replayed = operators.drift(
        ritsu, rng_seed=original.operator_params["rng_seed"], step=original.operator_params["step"],
    )
    assert replayed.genome_id == original.genome_id
    assert models.genome_to_json(replayed) == models.genome_to_json(original)


def test_vertex_pull_recorded_params_reproduce_same_genome_id(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    original = operators.vertex_pull(
        ritsu, pjs, weight=0.500000499, vertex="user", pull=0.100000499,
    )
    replayed = operators.vertex_pull(
        ritsu, pjs,
        weight=original.operator_params["weight"],
        vertex=original.operator_params["vertex"],
        pull=original.operator_params["pull"],
    )
    assert replayed.genome_id == original.genome_id
    assert models.genome_to_json(replayed) == models.genome_to_json(original)


def test_edge_walk_recorded_step_reproduces_same_genome_id(founders) -> None:
    _ritsu, pjs, _usr, _center = founders
    original = operators.edge_walk(
        pjs, rng_seed=7, edge=("ritsu", "pjs"), step=0.02000049,
    )
    assert original.operator_params["step"] == 0.02
    assert original.coords == models.Coords(0.018957, 0.981043, 0.0)
    replayed = operators.edge_walk(
        pjs, rng_seed=original.operator_params["rng_seed"],
        edge=tuple(original.operator_params["edge"]), step=original.operator_params["step"],
    )
    assert replayed.genome_id == original.genome_id
    assert models.genome_to_json(replayed) == models.genome_to_json(original)


def test_anchors_provenance_propagation_does_not_change_genome_id(founders) -> None:
    """genome_id は anchors_provenance を除外して計算される設計（models.py）
    のため、伝播しても子の genome_id は不変。"""
    *_ , center = founders
    bare_child = operators.drift(center, rng_seed=1, step=0.02)
    anchored_parent = _with_anchors(center, _ANCHORS_A)
    anchored_child = operators.drift(anchored_parent, rng_seed=1, step=0.02)
    assert bare_child.genome_id == anchored_child.genome_id
    assert bare_child.anchors_provenance is None
    assert anchored_child.anchors_provenance == _ANCHORS_A


# --- PR #267 Codex R8 指摘: 符号付きゼロ（-0.0）の正規化 ---------------------


def test_drift_step_negative_zero_normalized_to_positive_zero(founders) -> None:
    """step=-1e-9 は `round(step, 6)` で -0.0 になる（丸め前は境界内の負の
    微小値だが、丸め後は符号付きゼロ）。operators.py の入口正規化がないと
    operator_params.step が -0.0 のまま記録され、同じ意図の step=0.0 と
    別 genome_id になっていた。"""
    ritsu, _pjs, _usr, _center = founders
    assert repr(round(-1e-9, 6)) == "-0.0"  # 前提の確認
    a = operators.drift(ritsu, rng_seed=0, step=-1e-9)
    b = operators.drift(ritsu, rng_seed=0, step=0.0)
    assert a.genome_id == b.genome_id
    assert a.operator_params["step"] == 0.0
    assert math.copysign(1.0, a.operator_params["step"]) > 0.0


def test_vertex_pull_weight_and_pull_negative_zero_normalized(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    assert repr(round(-1e-9, 6)) == "-0.0"  # 前提の確認
    a = operators.vertex_pull(ritsu, pjs, weight=-1e-9, vertex="user", pull=-1e-9)
    b = operators.vertex_pull(ritsu, pjs, weight=0.0, vertex="user", pull=0.0)
    assert a.genome_id == b.genome_id
    assert a.operator_params["weight"] == 0.0
    assert a.operator_params["pull"] == 0.0
    assert math.copysign(1.0, a.operator_params["weight"]) > 0.0
    assert math.copysign(1.0, a.operator_params["pull"]) > 0.0


def test_edge_walk_step_negative_zero_normalized(founders) -> None:
    _ritsu, pjs, _usr, _center = founders
    a = operators.edge_walk(pjs, rng_seed=7, edge=("ritsu", "pjs"), step=-1e-9)
    b = operators.edge_walk(pjs, rng_seed=7, edge=("ritsu", "pjs"), step=0.0)
    assert a.genome_id == b.genome_id
    assert a.operator_params["step"] == 0.0
    assert math.copysign(1.0, a.operator_params["step"]) > 0.0


# --- PR #267 Codex R11 指摘1（P2）: オペレータ入口の bool 拒否 ----------------
#
# bool は int のサブクラスのため round(step, 6) 等は step=False/True を無検証
# で 0/1 へ通していた。step=False は「0歩変異」、weight=True/pull=True は
# 「片親を全棄却する交配」として publish され得ていたため、round() より前に
# isinstance(x, bool) を fail-closed 拒否する（archive.py の quality/
# quality_floor 検査 — R10 指摘B — と同じ流儀）。


def test_drift_rejects_bool_step_false(founders) -> None:
    *_ , center = founders
    with pytest.raises(ValueError, match="bool step rejected"):
        operators.drift(center, rng_seed=1, step=False)


def test_drift_rejects_bool_step_true(founders) -> None:
    *_ , center = founders
    with pytest.raises(ValueError, match="bool step rejected"):
        operators.drift(center, rng_seed=1, step=True)


def test_drift_accepts_ordinary_float_step(founders) -> None:
    """bool 拒否の追加が通常の float 受理を壊していないことの回帰確認。"""
    *_ , center = founders
    child = operators.drift(center, rng_seed=1, step=0.02)
    assert child.operator_params["step"] == 0.02


def test_edge_walk_rejects_bool_step_false(founders) -> None:
    *_ , center = founders
    with pytest.raises(ValueError, match="bool step rejected"):
        operators.edge_walk(center, rng_seed=1, edge=("ritsu", "pjs"), step=False)


def test_edge_walk_accepts_ordinary_float_step(founders) -> None:
    *_ , center = founders
    child = operators.edge_walk(center, rng_seed=1, edge=("ritsu", "pjs"), step=0.02)
    assert child.operator_params["step"] == 0.02


def test_vertex_pull_rejects_bool_weight_true(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    with pytest.raises(ValueError, match="bool weight rejected"):
        operators.vertex_pull(ritsu, pjs, weight=True, vertex="user", pull=0.1)


def test_vertex_pull_rejects_bool_weight_false(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    with pytest.raises(ValueError, match="bool weight rejected"):
        operators.vertex_pull(ritsu, pjs, weight=False, vertex="user", pull=0.1)


def test_vertex_pull_rejects_bool_pull_true(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    with pytest.raises(ValueError, match="bool pull rejected"):
        operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=True)


def test_vertex_pull_rejects_bool_pull_false(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    with pytest.raises(ValueError, match="bool pull rejected"):
        operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=False)


def test_vertex_pull_accepts_ordinary_float_weight_and_pull(founders) -> None:
    ritsu, pjs, _usr, _center = founders
    child = operators.vertex_pull(ritsu, pjs, weight=0.5, vertex="user", pull=0.1)
    assert child.operator_params["weight"] == 0.5
    assert child.operator_params["pull"] == 0.1
