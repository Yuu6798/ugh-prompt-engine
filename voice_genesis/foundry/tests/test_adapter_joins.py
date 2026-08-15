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


def test_assemble_deterministic_repeat_v1() -> None:
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


# ---------------------------------------------------------------------------
# 追補 F1.3-A: v2 true overlap-add (`overlap_add_concat` / `assemble_v2`)
# ---------------------------------------------------------------------------


def _wavy_sp(n: int, n_bins: int, base: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    sp = base + 0.02 * rng.standard_normal((n, n_bins)).cumsum(axis=0) * 0.05
    return np.abs(sp) + base * 0.5


def test_overlap_add_concat_shrinks_total_length() -> None:
    n_bins = 6
    n1, n2 = 40, 40
    ov = 10
    sp_a = _wavy_sp(n1, n_bins, 1.0, seed=1)
    sp_b = _wavy_sp(n2, n_bins, 5.0, seed=2)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.3)
    sp_out, ap_out, stats = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], ov)
    assert sp_out.shape[0] == n1 + n2 - ov  # true overlap-add: 重なり分だけ短くなる
    assert ap_out.shape[0] == n1 + n2 - ov
    assert stats["n_joins_applied"] == 1
    assert stats["overlap_frames_applied"] == [ov]


def test_overlap_add_concat_boundary_jump_within_interior_distribution() -> None:
    """overlap-add の「境界」= 旧 v1 の硬い切れ目に相当する 2 箇所（ブレンド区間へ
    入る継ぎ目・ブレンド区間から抜ける継ぎ目）で跳びが単位内部の跳び分布を超え
    ないことを検証する（ブレンド区間内部はレベル差を数フレームで渡すため、
    その内部ジャンプ自体は当然大きくなる = v1 の「境界 1 点」検証と同じ視点）。
    """
    n_bins = 6
    n1, n2 = 60, 60
    ov = 12
    sp_a = _wavy_sp(n1, n_bins, 1.0, seed=3)
    sp_b = _wavy_sp(n2, n_bins, 6.0, seed=4)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.3)
    sp_out, _ap_out, _stats = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], ov)
    jumps = _interior_jumps(sp_out)
    interior_all = np.concatenate([_interior_jumps(sp_a), _interior_jumps(sp_b)])
    entering_seam = jumps[n1 - ov - 1]  # 純 prev -> ブレンド開始
    exiting_seam = jumps[n1 - 1]  # ブレンド終端 -> 純 cur
    assert entering_seam <= np.max(interior_all) * 1.5
    assert exiting_seam <= np.max(interior_all) * 1.5


def test_overlap_add_concat_caps_overlap_to_shortest_piece() -> None:
    n_bins = 3
    sp_a = np.full((5, n_bins), 1.0)
    sp_b = np.full((20, n_bins), 2.0)
    ap_a = np.full((5, n_bins), 0.1)
    ap_b = np.full((20, n_bins), 0.1)
    sp_out, _ap_out, stats = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], overlap_frames=40)
    assert sp_out.shape[0] == 5 + 20 - 5  # overlap は短い方 (5) にクランプされる
    assert stats["overlap_frames_applied"] == [5]


def test_overlap_add_concat_skips_too_short_overlap() -> None:
    n_bins = 3
    sp_a = np.full((10, n_bins), 1.0)
    sp_b = np.full((10, n_bins), 2.0)
    ap_a = np.full((10, n_bins), 0.1)
    ap_b = np.full((10, n_bins), 0.1)
    sp_out, _ap_out, stats = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], overlap_frames=1)
    assert sp_out.shape[0] == 20  # 1 フレームは 2 未満 -> 単純連結にフォールバック
    assert stats["n_joins_skipped_short"] == 1
    assert stats["n_joins_applied"] == 0


def test_overlap_add_concat_per_join_overlap_sequence() -> None:
    n_bins = 3
    pieces = [
        (np.full((10, n_bins), 1.0), np.full((10, n_bins), 0.1)),
        (np.full((10, n_bins), 2.0), np.full((10, n_bins), 0.1)),
        (np.full((10, n_bins), 3.0), np.full((10, n_bins), 0.1)),
    ]
    sp_out, _ap_out, stats = jn.overlap_add_concat(pieces, [3, 5])
    assert sp_out.shape[0] == 10 + 10 + 10 - 3 - 5
    assert stats["overlap_frames_applied"] == [3, 5]


def test_overlap_add_concat_deterministic_repeat() -> None:
    n_bins = 4
    sp_a = _wavy_sp(20, n_bins, 1.0, seed=7)
    sp_b = _wavy_sp(20, n_bins, 3.0, seed=8)
    ap_a = np.full((20, n_bins), 0.1)
    ap_b = np.full((20, n_bins), 0.2)
    sp1, ap1, _ = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], 6)
    sp2, ap2, _ = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], 6)
    assert np.array_equal(sp1, sp2)
    assert np.array_equal(ap1, ap2)


def test_assemble_v2_shrinks_timeline_by_overlap_within_phrase() -> None:
    """追補 F1.3-A: フレーズ内で連続する 2 note は overlap 分だけタイムラインが
    短くなる（v1 の assemble は総尺不変だったのに対する差分。「タイムライン総尺の
    重なり補正」の acceptance）。"""
    n_bins = 5
    n1, n2 = 50, 50
    ov = 10
    sp_a = _wavy_sp(n1, n_bins, 1.0, seed=11)
    sp_b = _wavy_sp(n2, n_bins, 4.0, seed=12)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.2)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=None),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True, overlap_frames=ov),
    ]
    sp_seq, ap_seq, stats = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)
    assert stats["n_total_frames"] == n1 + n2 - ov
    assert sp_seq.shape[0] == n1 + n2 - ov
    assert ap_seq.shape[0] == n1 + n2 - ov
    assert stats["n_joins_applied"] == 1
    assert stats["overlap_frames_applied"] == [ov]


def test_assemble_v2_uses_default_overlap_when_none() -> None:
    """oto 情報の無い unit（overlap_frames=None）は固定 40ms（frame_period_ms=5 なら
    8 フレーム）にフォールバックする。"""
    n_bins = 4
    n1, n2 = 60, 60
    sp_a = np.full((n1, n_bins), 1.0)
    sp_b = np.full((n2, n_bins), 2.0)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.1)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=None),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True, overlap_frames=None),
    ]
    sp_seq, _ap_seq, stats = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)
    expected_overlap = round(jn.DEFAULT_OVERLAP_MS / 5.0)
    assert stats["overlap_frames_applied"] == [expected_overlap]
    assert sp_seq.shape[0] == n1 + n2 - expected_overlap


def test_assemble_v2_no_overlap_across_phrase_boundary() -> None:
    """has_join_to_prev=False（ブレス境界・フレーズ先頭）では overlap を適用せず、
    従来どおり gap を保持する（run の切れ目）。"""
    n_bins = 4
    n1, n2 = 30, 30
    sp_a = np.full((n1, n_bins), 1.0)
    sp_b = np.full((n2, n_bins), 5.0)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.1)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=8),
        jn.NotePlacement(n1 + 5, n1 + 5 + n2, sp_b, ap_b, has_join_to_prev=False, overlap_frames=8),
    ]
    sp_seq, _ap_seq, stats = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)
    assert stats["n_joins_applied"] == 0
    assert sp_seq.shape[0] == n1 + 5 + n2  # gap (5 frames) を保持したまま、overlap は無い
    assert np.allclose(sp_seq[n1:n1 + 5], sp_a[-1])  # gap は carry-forward


def test_assemble_v2_energy_continuity_boundary_within_interior() -> None:
    """合成 unit での接合エネルギー連続性テスト（overlap-add 版）: 境界跳びが
    単位内部の跳び分布を超えない。"""
    n_bins = 6
    n1, n2 = 80, 80
    ov = 16
    sp_a = _wavy_sp(n1, n_bins, 1.0, seed=21)
    sp_b = _wavy_sp(n2, n_bins, 6.0, seed=22)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.3)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=None),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True, overlap_frames=ov),
    ]
    sp_seq, _ap_seq, _stats = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)
    jumps = _interior_jumps(sp_seq)
    interior_all = np.concatenate([_interior_jumps(sp_a), _interior_jumps(sp_b)])
    entering_seam = jumps[n1 - ov - 1]
    exiting_seam = jumps[n1 - 1]
    assert entering_seam <= np.max(interior_all) * 1.5
    assert exiting_seam <= np.max(interior_all) * 1.5


def test_assemble_v2_deterministic_repeat() -> None:
    n_bins = 4
    n1, n2 = 50, 50
    sp_a = _wavy_sp(n1, n_bins, 1.0, seed=31)
    sp_b = _wavy_sp(n2, n_bins, 3.0, seed=32)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.2)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=None),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True, overlap_frames=9),
    ]
    sp1, ap1, stats1 = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)
    sp2, ap2, stats2 = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)
    assert np.array_equal(sp1, sp2)
    assert np.array_equal(ap1, ap2)
    assert stats1 == stats2


def test_assemble_v2_empty_placements() -> None:
    sp_seq, ap_seq, stats = jn.assemble_v2(4, [])
    assert sp_seq.shape == (0, 4)
    assert ap_seq.shape == (0, 4)
    assert stats["n_total_frames"] == 0
