"""test_adapter_joins.py — 接合クロスフェードの連続性（境界フレームの log-sp
跳びが単位内部の跳び分布を超えない）+ 無声ギャップの carry-forward を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np

import joins as jn


def _interior_jumps(sp: np.ndarray) -> np.ndarray:
    diffs = np.log(sp[1:] + 1e-8) - np.log(sp[:-1] + 1e-8)
    return np.linalg.norm(diffs, axis=1)


def test_assemble_crossfade_reduces_boundary_jump() -> None:
    n_bins = 8
    n1, n2 = 80, 80
    rng = np.random.default_rng(0)
    # 2 unit の sp を明確に異なるレベル + 内部は緩やかに変動する形にする
    sp_a = 1.0 + 0.02 * rng.standard_normal((n1, n_bins)).cumsum(axis=0) * 0.05
    sp_a = np.abs(sp_a) + 0.5
    sp_b = 6.0 + 0.02 * rng.standard_normal((n2, n_bins)).cumsum(axis=0) * 0.05
    sp_b = np.abs(sp_b) + 4.0
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.3)

    placements_no_join = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=False),
    ]
    sp_nojoin, _, _ = jn.assemble(n1 + n2, n_bins, placements_no_join)

    placements_join = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True),
    ]
    sp_join, _, join_stats = jn.assemble(n1 + n2, n_bins, placements_join)

    jumps_nojoin = _interior_jumps(sp_nojoin)
    jumps_join = _interior_jumps(sp_join)

    boundary_jump_nojoin = jumps_nojoin[n1 - 1]
    boundary_jump_join = jumps_join[n1 - 1]

    assert boundary_jump_join < boundary_jump_nojoin

    interior_a = _interior_jumps(sp_a)
    interior_b = _interior_jumps(sp_b)
    interior_all = np.concatenate([interior_a, interior_b])
    assert boundary_jump_join <= np.max(interior_all) * 1.5
    assert join_stats["n_joins_applied"] == 1


def test_assemble_no_crossfade_when_phrase_first() -> None:
    n_bins = 4
    n1, n2 = 40, 40
    sp_a = np.full((n1, n_bins), 1.0)
    sp_b = np.full((n2, n_bins), 5.0)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.1)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=False),
    ]
    sp_seq, _, stats = jn.assemble(n1 + n2, n_bins, placements)
    assert stats["n_joins_applied"] == 0
    assert np.array_equal(sp_seq[:n1], sp_a)
    assert np.array_equal(sp_seq[n1:], sp_b)


def test_assemble_carry_forward_gap_between_placements() -> None:
    n_bins = 3
    n_total = 100
    sp_a = np.full((20, n_bins), 2.0)
    ap_a = np.full((20, n_bins), 0.2)
    sp_b = np.full((15, n_bins), 9.0)
    ap_b = np.full((15, n_bins), 0.4)
    # placement A: [10,30) / placement B: [60,75) -> gap [0,10), [30,60), [75,100)
    placements = [
        jn.NotePlacement(10, 30, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(60, 75, sp_b, ap_b, has_join_to_prev=False),
    ]
    sp_seq, ap_seq, stats = jn.assemble(n_total, n_bins, placements)
    # 先頭ギャップは最初の有効フレームで埋める（後方 hold）
    assert np.allclose(sp_seq[0:10], sp_a[0])
    # A と B の間のギャップは A の最終フレームで carry-forward
    assert np.allclose(sp_seq[30:60], sp_a[-1])
    # 末尾ギャップは B の最終フレームで carry-forward
    assert np.allclose(sp_seq[75:100], sp_b[-1])
    assert stats["n_gap_frames"] == 10 + 30 + 25


def test_assemble_deterministic_repeat() -> None:
    n_bins = 4
    n1, n2 = 50, 50
    rng = np.random.default_rng(1)
    sp_a = np.abs(rng.standard_normal((n1, n_bins))) + 1.0
    sp_b = np.abs(rng.standard_normal((n2, n_bins))) + 1.0
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.2)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True),
    ]
    sp1, ap1, _ = jn.assemble(n1 + n2, n_bins, placements)
    sp2, ap2, _ = jn.assemble(n1 + n2, n_bins, placements)
    assert np.array_equal(sp1, sp2)
    assert np.array_equal(ap1, ap2)
