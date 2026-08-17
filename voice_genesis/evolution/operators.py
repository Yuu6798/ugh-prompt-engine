"""operators.py — VG-E0: 変異・交配オペレータ 5 種（DESIGN_VG_E0.md §4）。

drift / vertex_pull / reseed / edge_walk / novelty_jump。乱数は必ず
`random.Random(operator_params['rng_seed'])` のみを使う（グローバル乱数・
NumPy 非依存 — 設計書 §4「乱数は必ず...グローバル乱数禁止・NumPy 非依存 =
SIMD 契約の外に置く」）。座標演算は `simplex.normalize()`（単体内への射影 +
丸め規約の一元化）を必ず経由する。系統（lineage）決定は原則
`simplex.assign_lineage(coords)`（座標のみからの機械決定）だが、
novelty_jump の出力と系統間 vertex_pull の出力は座標によらず強制的に
`NOVELTY` を付与する（§3.1 / §4「系統内/系統間判定はオペレータではなく
台帳が行う」— 本実装ではオペレータ関数自身がその判定点を担う。台帳
（ledger.py）はオペレータが確定させた lineage をそのまま記帳するのみで
再判定はしない）。
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path
from typing import Tuple

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import models  # noqa: E402
import simplex  # noqa: E402

# Δ² の接平面（{(dr,dp,du): dr+dp+du=0}）の正規直交基底。drift の
# 「一様ランダム方向」・edge_walk の「辺方向」を張るために使う。
_TANGENT_B1: Tuple[float, float, float] = (1.0 / math.sqrt(2.0), -1.0 / math.sqrt(2.0), 0.0)
_TANGENT_B2: Tuple[float, float, float] = (
    1.0 / math.sqrt(6.0), 1.0 / math.sqrt(6.0), -2.0 / math.sqrt(6.0),
)


def _tangent_displacement(theta: float, magnitude: float) -> Tuple[float, float, float]:
    c, s = math.cos(theta), math.sin(theta)
    return (
        magnitude * (c * _TANGENT_B1[0] + s * _TANGENT_B2[0]),
        magnitude * (c * _TANGENT_B1[1] + s * _TANGENT_B2[1]),
        magnitude * (c * _TANGENT_B1[2] + s * _TANGENT_B2[2]),
    )


def drift(parent: models.VoiceGenome, *, rng_seed: int, step: float) -> models.VoiceGenome:
    """重心座標を一様ランダム方向へ最大 step の微動（単体内へ射影）。
    親1個体。`operator_params = {rng_seed, step<=0.08}`。
    """
    if not (0.0 <= step <= models.DRIFT_STEP_MAX):
        raise ValueError(f"drift step must be within [0, {models.DRIFT_STEP_MAX}], got {step}")
    rng = random.Random(rng_seed)
    theta = rng.uniform(0.0, 2.0 * math.pi)
    magnitude = rng.uniform(0.0, step)
    dr, dp, du = _tangent_displacement(theta, magnitude)
    raw = {
        "ritsu": parent.coords.ritsu + dr,
        "pjs": parent.coords.pjs + dp,
        "user": parent.coords.user + du,
    }
    coords = simplex.normalize(raw)
    lineage = simplex.assign_lineage(coords)
    return models.build_genome(
        coords=coords, seed=parent.seed, lineage=lineage, generation=parent.generation + 1,
        parents=(parent.genome_id,), operator="drift",
        operator_params={"rng_seed": rng_seed, "step": step},
    )


def vertex_pull(
    parent_a: models.VoiceGenome, parent_b: models.VoiceGenome, *,
    weight: float, vertex: str, pull: float,
) -> models.VoiceGenome:
    """2親の重み付き平均 → 指定頂点方向へ pull 混合。親2個体。
    `operator_params = {weight, vertex, pull<=0.2}`。

    `weight` は `parent_a` へかかる重み（`parent_b` は `1-weight`）。
    親2個体の lineage が異なる場合、生成個体は座標によらず NOVELTY 隔離
    （DESIGN_VG_E0.md §4「系統内/系統間判定はオペレータではなく台帳が行う」
    — 本実装ではこの判定点を vertex_pull 自身が担う）。
    """
    if not (0.0 <= weight <= 1.0):
        raise ValueError(f"vertex_pull weight must be within [0, 1], got {weight}")
    if vertex not in models.ANCHOR_NAMES:
        raise ValueError(f"vertex_pull vertex must be one of {models.ANCHOR_NAMES}, got {vertex!r}")
    if not (0.0 <= pull <= models.VERTEX_PULL_PULL_MAX):
        raise ValueError(f"vertex_pull pull must be within [0, {models.VERTEX_PULL_PULL_MAX}], got {pull}")

    mix = {
        name: weight * getattr(parent_a.coords, name) + (1.0 - weight) * getattr(parent_b.coords, name)
        for name in models.ANCHOR_NAMES
    }
    vertex_coord = {name: (1.0 if name == vertex else 0.0) for name in models.ANCHOR_NAMES}
    raw = {name: (1.0 - pull) * mix[name] + pull * vertex_coord[name] for name in models.ANCHOR_NAMES}
    coords = simplex.normalize(raw)

    cross_lineage = parent_a.lineage != parent_b.lineage
    lineage = "NOVELTY" if cross_lineage else simplex.assign_lineage(coords)

    generation = max(parent_a.generation, parent_b.generation) + 1
    return models.build_genome(
        coords=coords, seed=parent_a.seed, lineage=lineage, generation=generation,
        parents=(parent_a.genome_id, parent_b.genome_id), operator="vertex_pull",
        operator_params={"weight": weight, "vertex": vertex, "pull": pull},
    )


def reseed(parent: models.VoiceGenome, *, new_seed: int) -> models.VoiceGenome:
    """coords 不変・seed のみ変更（Performance Revision）。親1個体。
    `operator_params = {new_seed}`。coords が不変であるため lineage は
    `simplex.assign_lineage(parent.coords)` を再計算する — 親が NOVELTY
    （1世代限定隔離）でも、reseed 個体自身は座標帰属へ復帰する
    （DESIGN_VG_E0.md §3.1「次世代で座標帰属に復帰」）。
    """
    if isinstance(new_seed, bool) or not isinstance(new_seed, int):
        raise ValueError(f"reseed new_seed must be an int, got {new_seed!r}")
    lineage = simplex.assign_lineage(parent.coords)
    return models.build_genome(
        coords=parent.coords, seed=new_seed, lineage=lineage, generation=parent.generation + 1,
        parents=(parent.genome_id,), operator="reseed", operator_params={"new_seed": new_seed},
    )


def edge_walk(
    parent: models.VoiceGenome, *, rng_seed: int, edge: Tuple[str, str], step: float,
) -> models.VoiceGenome:
    """指定2頂点を結ぶ辺方向のみに移動（軸制限変異）。親1個体。
    `operator_params = {rng_seed, edge, step<=0.1}`。
    """
    if edge[0] not in models.ANCHOR_NAMES or edge[1] not in models.ANCHOR_NAMES:
        raise ValueError(f"edge_walk edge must name anchors from {models.ANCHOR_NAMES}, got {edge!r}")
    if edge[0] == edge[1]:
        raise ValueError(f"edge_walk edge must name two distinct anchors, got {edge!r}")
    if not (0.0 <= step <= models.EDGE_WALK_STEP_MAX):
        raise ValueError(f"edge_walk step must be within [0, {models.EDGE_WALK_STEP_MAX}], got {step}")

    rng = random.Random(rng_seed)
    sign = rng.choice((-1.0, 1.0))
    magnitude = rng.uniform(0.0, step)
    delta = sign * magnitude

    raw = {name: getattr(parent.coords, name) for name in models.ANCHOR_NAMES}
    raw[edge[0]] += delta
    raw[edge[1]] -= delta
    coords = simplex.normalize(raw)
    lineage = simplex.assign_lineage(coords)
    return models.build_genome(
        coords=coords, seed=parent.seed, lineage=lineage, generation=parent.generation + 1,
        parents=(parent.genome_id,), operator="edge_walk",
        operator_params={"rng_seed": rng_seed, "edge": [edge[0], edge[1]], "step": step},
    )


def novelty_jump(parent: models.VoiceGenome, *, rng_seed: int) -> models.VoiceGenome:
    """一様サンプルした遠方座標へ跳躍。NOVELTY 隔離付与。親1個体。
    `operator_params = {rng_seed}`。

    Δ² 上の一様分布サンプリングは Kraemer 法（2つの一様乱数を昇順ソートし
    区間幅を3成分の重心座標として使う）を用いる。coords は親の位置に依存
    しない独立サンプルだが、台帳・genome_id の来歴としては親1個体を記録する
    （設計書 §4 の表「入力: 親1」）。
    """
    rng = random.Random(rng_seed)
    x1, x2 = sorted((rng.random(), rng.random()))
    raw = {"ritsu": x1, "pjs": x2 - x1, "user": 1.0 - x2}
    coords = simplex.normalize(raw)
    return models.build_genome(
        coords=coords, seed=parent.seed, lineage="NOVELTY", generation=parent.generation + 1,
        parents=(parent.genome_id,), operator="novelty_jump", operator_params={"rng_seed": rng_seed},
    )
