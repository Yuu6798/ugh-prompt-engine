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


# ---------------------------------------------------------------------------
# P1 修正 (review #262 R2): preutterance 消費（`extra_trim_frames` /
# `_resolve_extra_trim`）— overlap のみでは attack 点が preutt-overlap 分
# 名目ビートより遅延する不具合の修正。
# ---------------------------------------------------------------------------


def test_overlap_add_concat_extra_trim_removes_prev_tail_before_blend() -> None:
    n_bins = 2
    sp_a = np.full((30, n_bins), 1.0)
    sp_b = np.full((30, n_bins), 2.0)
    ap_a = np.full((30, n_bins), 0.1)
    ap_b = np.full((30, n_bins), 0.2)
    ov = 4
    trim = 6
    sp_out, _ap_out, stats = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], ov, trim)
    # 総尺は overlap 分だけでなく trim 分もさらに短くなる。
    assert sp_out.shape[0] == 30 + 30 - ov - trim
    assert stats["n_extra_trim_frames_applied"] == trim
    assert stats["overlap_frames_applied"] == [ov]


def test_overlap_add_concat_extra_trim_none_is_backward_compatible() -> None:
    """`extra_trim_frames` 省略（None）は従来どおり trim=0（後方互換）。"""
    n_bins = 3
    sp_a = np.full((20, n_bins), 1.0)
    sp_b = np.full((20, n_bins), 2.0)
    ap_a = np.full((20, n_bins), 0.1)
    ap_b = np.full((20, n_bins), 0.1)
    ov = 5
    sp_with_none, _ap1, stats1 = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], ov, None)
    sp_omitted, _ap2, stats2 = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], ov)
    assert np.array_equal(sp_with_none, sp_omitted)
    assert stats1["n_extra_trim_frames_applied"] == stats2["n_extra_trim_frames_applied"] == 0


def test_overlap_add_concat_extra_trim_clamped_to_accumulator_length() -> None:
    """trim 要求が現時点のアキュムレータ長を超える場合はアキュムレータ全体
    までにクランプする（負の長さにならない）。"""
    n_bins = 2
    sp_a = np.full((5, n_bins), 1.0)
    sp_b = np.full((10, n_bins), 2.0)
    ap_a = np.full((5, n_bins), 0.1)
    ap_b = np.full((10, n_bins), 0.1)
    sp_out, _ap_out, stats = jn.overlap_add_concat([(sp_a, ap_a), (sp_b, ap_b)], 2, 999)
    assert stats["n_extra_trim_frames_applied"] == 5  # 5 フレームしかないアキュムレータへクランプ
    assert sp_out.shape[0] >= 0


def _resolve_extra_trim_direct(preutt, ov: int) -> int:
    return jn._resolve_extra_trim(preutt, ov)


def test_resolve_extra_trim_none_preutterance_is_zero() -> None:
    """`preutterance_frames=None`（vocadito/pjs unit）は常に trim=0。"""
    assert _resolve_extra_trim_direct(None, 4) == 0


def test_resolve_extra_trim_positive_when_preutterance_exceeds_overlap() -> None:
    assert _resolve_extra_trim_direct(60, 20) == 40


def test_resolve_extra_trim_zero_when_preutterance_not_exceeding_overlap() -> None:
    assert _resolve_extra_trim_direct(10, 20) == 0
    assert _resolve_extra_trim_direct(20, 20) == 0


def test_assemble_v2_preutterance_attack_frame_lands_at_nominal_beat() -> None:
    """タイミングテスト（review #262 R2 P1 の実害検証）: mid-phrase note の
    `preutterance_frames` フレーム目（真の母音アタック点）が、直前 note の
    累積長（= 名目上のビート位置）にちょうど整列することを検証する。

    overlap のみを消費する旧実装では、この位置に `sp_b[overlap_frames]`
    （録音済み遷移の途中、まだアタック前）が来てしまい、`preutterance_frames -
    overlap_frames` フレーム分だけアタックが遅延していた。
    """
    n_bins = 2
    n1 = 50
    overlap = 5
    preutt = 18  # overlap より大きい（実データの overlap<preutterance 不変条件を模す）
    sp_a = np.full((n1, n_bins), 1.0)
    ap_a = np.full((n1, n_bins), 0.1)
    n2 = 40
    # unit_b の各フレームを一意な値でマークする（frame index の検算用）。
    sp_b = np.stack([np.full(n_bins, 100.0 + i) for i in range(n2)], axis=0)
    ap_b = np.full((n2, n_bins), 0.2)

    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=None),
        jn.NotePlacement(
            n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True,
            overlap_frames=overlap, preutterance_frames=preutt,
        ),
    ]
    sp_seq, _ap_seq, stats = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)

    # 総尺は overlap ではなく preutterance 分だけ短くなる（trim=preutt-overlap
    # が overlap 消費に追加で乗る）。
    assert sp_seq.shape[0] == n1 + n2 - preutt
    assert stats["n_extra_trim_frames_applied"] == preutt - overlap

    # unit_b の frame[preutt]（真のアタック点）が、直前 note の累積長 n1
    # （= 名目ビート位置）にちょうど一致する。
    assert np.allclose(sp_seq[n1], sp_b[preutt])


def test_assemble_v2_preutterance_none_matches_overlap_only_behavior() -> None:
    """`preutterance_frames=None`（vocadito/pjs unit）は従来どおり overlap の
    みを消費する（後方互換・回帰なし）。"""
    n_bins = 3
    n1, n2 = 40, 40
    ov = 8
    sp_a = np.full((n1, n_bins), 1.0)
    ap_a = np.full((n1, n_bins), 0.1)
    sp_b = np.full((n2, n_bins), 2.0)
    ap_b = np.full((n2, n_bins), 0.2)

    placements_no_preutt = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=None),
        jn.NotePlacement(n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True, overlap_frames=ov),
    ]
    sp_seq, _ap_seq, stats = jn.assemble_v2(n_bins, placements_no_preutt, frame_period_ms=5.0)
    assert sp_seq.shape[0] == n1 + n2 - ov
    assert stats["n_extra_trim_frames_applied"] == 0


# ---------------------------------------------------------------------------
# P1 修正 (review #262): f0/振幅トラックの圧縮後タイムライン整合
# ---------------------------------------------------------------------------


def _flat_placement(
    n_frames: int, n_bins: int, sp_value: float, ap_value: float, has_join_to_prev: bool,
    overlap_frames,
) -> jn.NotePlacement:
    sp = np.full((n_frames, n_bins), sp_value)
    ap = np.full((n_frames, n_bins), ap_value)
    # start_frame/end_frame は assemble_v2/assemble_control_tracks_v2 のどちらも
    # 「圧縮前の素朴な連結カーソル」を仮定するので、この検証ではギャップ無しの
    # 単純な累積値を使う（render.py の実配線と同じ規約）。
    return jn.NotePlacement(
        start_frame=0, end_frame=n_frames, sp=sp, ap=ap,
        has_join_to_prev=has_join_to_prev, overlap_frames=overlap_frames,
    )


def _relayout_start_end(placements: list) -> list:
    """start_frame/end_frame を「素朴な連結カーソル」で振り直す（render.py の
    cursor 方式と同じ・ギャップ無し）。"""
    out = []
    cursor = 0
    for p in placements:
        n = p.sp.shape[0]
        out.append(
            jn.NotePlacement(
                start_frame=cursor, end_frame=cursor + n, sp=p.sp, ap=p.ap,
                has_join_to_prev=p.has_join_to_prev, overlap_frames=p.overlap_frames,
            )
        )
        cursor += n
    return out


def test_assemble_control_tracks_v2_matches_sp_ap_frame_for_frame_with_multiple_joins() -> None:
    """P1 修正 acceptance: 接合 2 回以上を含む系列で、f0/振幅トラックが
    sp/ap と全く同じ圧縮後タイムライン（run 構造・overlap 量）に整列する
    ことを検証する（review #262 の指摘: 旧実装は圧縮前タイムラインを
    圧縮後フレーム数でナイーブに再インデックスしていたためズレていた）。

    note track の値を sp/ap の flat 値そのものに合わせておけば
    （n_bins=1）、`assemble_control_tracks_v2` の出力は
    `assemble_v2`（sp/ap 側）の出力と厳密に一致するはず（両者が同一の
    run 分割・同一の overlap 量で `overlap_add_concat` を呼ぶため）。
    厳密一致は「±1 フレーム」より強い保証であり、第 N ノートの音高変化
    フレームが圧縮後タイムラインのノート境界と一致することを直接示す。
    """
    n_bins = 1
    note_values = [1.0, 5.0, 9.0, 13.0]  # 4 note -> 3 join（「接合 2 回以上」を満たす）
    note_lens = [60, 55, 65, 50]
    overlaps = [None, 12, 7]  # None は DEFAULT_OVERLAP_MS へフォールバック

    placements = []
    for i, (val, n) in enumerate(zip(note_values, note_lens)):
        ov = overlaps[i - 1] if i > 0 else None
        placements.append(
            _flat_placement(
                n, n_bins, sp_value=val, ap_value=val * 0.1,
                has_join_to_prev=(i > 0), overlap_frames=ov,
            )
        )
    placements = _relayout_start_end(placements)

    sp_seq, ap_seq, join_stats = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)
    assert join_stats["n_joins_applied"] >= 2  # 「接合 2 回以上」を満たすことを確認

    note_f0_tracks = [np.full(p.sp.shape[0], note_values[i]) for i, p in enumerate(placements)]
    note_amp_tracks = [np.full(p.sp.shape[0], note_values[i] * 0.1) for i, p in enumerate(placements)]
    f0_seq, amp_seq, control_stats = jn.assemble_control_tracks_v2(
        placements, note_f0_tracks, note_amp_tracks, frame_period_ms=5.0
    )

    assert control_stats["n_total_frames"] == join_stats["n_total_frames"]
    assert f0_seq.shape[0] == sp_seq.shape[0]
    # 厳密一致（同一 run/overlap 構造で同一の重みを使って blend しているため）。
    assert np.allclose(f0_seq, sp_seq[:, 0])
    assert np.allclose(amp_seq, ap_seq[:, 0])

    # ノート境界（第 N ノートの「音高変化」フレーム）が圧縮後タイムライン上で
    # 一致することを明示的にも確認する: 各ノートの blend 区間終端（純粋に
    # そのノートの値になる最初のフレーム）が f0_seq/sp_seq で同一インデックス。
    for note_val in note_values[1:]:
        sp_first_pure = int(np.argmax(np.isclose(sp_seq[:, 0], note_val)))
        f0_first_pure = int(np.argmax(np.isclose(f0_seq, note_val)))
        assert abs(sp_first_pure - f0_first_pure) <= 1


def test_assemble_control_tracks_v2_stays_aligned_with_sp_ap_when_preutterance_set() -> None:
    """P1 修正 (review #262 R2): mid-phrase note が `preutterance_frames` を
    持つ場合（VCV unit）、`assemble_control_tracks_v2`（f0/振幅）は
    `assemble_v2`（sp/ap）と全く同じ extra_trim を適用してフレーム数が
    厳密一致し続ける（別々の trim 計算になると `n_total_frames` が食い違い、
    render.py の assert（f0_seq.shape[0]==n_total_frames）が落ちる）。
    """
    n_bins = 1
    n1, n2 = 50, 40
    overlap, preutt = 5, 18
    sp_a = np.full((n1, n_bins), 1.0)
    ap_a = np.full((n1, n_bins), 0.1)
    sp_b = np.full((n2, n_bins), 5.0)
    ap_b = np.full((n2, n_bins), 0.5)

    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False, overlap_frames=None),
        jn.NotePlacement(
            n1, n1 + n2, sp_b, ap_b, has_join_to_prev=True,
            overlap_frames=overlap, preutterance_frames=preutt,
        ),
    ]
    sp_seq, _ap_seq, join_stats = jn.assemble_v2(n_bins, placements, frame_period_ms=5.0)

    note_f0_tracks = [np.full(n1, 1.0), np.full(n2, 5.0)]
    note_amp_tracks = [np.full(n1, 0.1), np.full(n2, 0.5)]
    f0_seq, _amp_seq, control_stats = jn.assemble_control_tracks_v2(
        placements, note_f0_tracks, note_amp_tracks, frame_period_ms=5.0
    )

    assert control_stats["n_total_frames"] == join_stats["n_total_frames"] == n1 + n2 - preutt
    assert f0_seq.shape[0] == sp_seq.shape[0]


def test_assemble_control_tracks_v2_gap_is_zero_not_carried_forward() -> None:
    """run 間ギャップ（ブレス等）は f0/振幅では 0（無声）で埋める（sp/ap の
    carry-forward とは異なる意味論であることの確認）。"""
    n_bins = 1
    p1 = _flat_placement(20, n_bins, sp_value=2.0, ap_value=0.2, has_join_to_prev=False, overlap_frames=None)
    p2 = _flat_placement(15, n_bins, sp_value=9.0, ap_value=0.4, has_join_to_prev=False, overlap_frames=None)
    placements = [
        jn.NotePlacement(10, 30, p1.sp, p1.ap, has_join_to_prev=False),
        jn.NotePlacement(60, 75, p2.sp, p2.ap, has_join_to_prev=False),
    ]
    note_f0_tracks = [np.full(20, 220.0), np.full(15, 330.0)]
    note_amp_tracks = [np.full(20, 0.8), np.full(15, 0.6)]
    f0_seq, amp_seq, stats = jn.assemble_control_tracks_v2(
        placements, note_f0_tracks, note_amp_tracks, frame_period_ms=5.0
    )
    # assemble_v2 系（v1 の `assemble` と異なり明示的な n_total_frames 引数を
    # 取らない）は最後の placement の end_frame までしか出力しない
    # （末尾の「未配置領域」という概念自体が無い）。
    assert stats["n_total_frames"] == 75
    assert np.all(f0_seq[0:10] == 0.0)  # 先頭ギャップ
    assert np.all(f0_seq[30:60] == 0.0)  # note 間ギャップ
    assert np.all(amp_seq[30:60] == 0.0)
    assert np.allclose(f0_seq[10:30], 220.0)
    assert np.allclose(f0_seq[60:75], 330.0)


def test_assemble_control_tracks_v2_empty_placements() -> None:
    f0_seq, amp_seq, stats = jn.assemble_control_tracks_v2([], [], [])
    assert f0_seq.shape == (0,)
    assert amp_seq.shape == (0,)
    assert stats["n_total_frames"] == 0


def test_assemble_control_tracks_v2_deterministic_repeat() -> None:
    n_bins = 1
    placements = [
        _flat_placement(40, n_bins, 1.0, 0.1, has_join_to_prev=False, overlap_frames=None),
        _flat_placement(40, n_bins, 5.0, 0.3, has_join_to_prev=True, overlap_frames=9),
    ]
    placements = _relayout_start_end(placements)
    note_f0 = [np.full(40, 200.0), np.full(40, 300.0)]
    note_amp = [np.full(40, 0.5), np.full(40, 0.7)]
    f0_1, amp_1, stats1 = jn.assemble_control_tracks_v2(placements, note_f0, note_amp, frame_period_ms=5.0)
    f0_2, amp_2, stats2 = jn.assemble_control_tracks_v2(placements, note_f0, note_amp, frame_period_ms=5.0)
    assert np.array_equal(f0_1, f0_2)
    assert np.array_equal(amp_1, amp_2)
    assert stats1 == stats2


# ---------------------------------------------------------------------------
# F1.4 R3 (review #262 R3・設計判定による内部発見): 絶対グリッド配置
# (`assemble_absolute` / `assemble_control_tracks_absolute`) — 接合のたびに
# 総尺が score から累積的に縮む問題（sakura で 19% 相当）の修正。
# ---------------------------------------------------------------------------


def test_assemble_absolute_total_frames_matches_fixed_buffer() -> None:
    """(a) acceptance: 出力総フレーム数は常に呼び出し側が指定した
    `n_total_frames`（score 由来の総尺）ちょうどになる（trim しないため）。
    """
    n_bins = 4
    n_total = 300
    sp_a = np.full((60, n_bins), 1.0)
    ap_a = np.full((60, n_bins), 0.1)
    sp_b = np.full((80, n_bins), 2.0)
    ap_b = np.full((80, n_bins), 0.2)
    # note A: [40,100)（先頭に無音ギャップ）/ note B: 同一フレーズ内で
    # A の preutterance 分だけ前方へシフトして配置（[85,165)）。
    placements = [
        jn.NotePlacement(40, 100, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(85, 165, sp_b, ap_b, has_join_to_prev=True),
    ]
    sp_seq, ap_seq, stats = jn.assemble_absolute(n_total, n_bins, placements)
    assert sp_seq.shape[0] == n_total
    assert ap_seq.shape[0] == n_total
    assert stats["n_total_frames"] == n_total


def test_assemble_absolute_attack_frame_lands_at_declared_absolute_position() -> None:
    """(b) acceptance: unit の「アタック点」（呼び出し側が preutterance オフ
    セット分だけ前へシフトして配置した結果、`start_frame + preutterance` が
    絶対タイムライン上の拍位置に一致する）を、frame ごとに一意な値でマーク
    して検算する。ブレンド区間（重なり `ov` フレーム）を抜けた最初の純粋な
    unit_b フレームが、期待した拍位置ちょうどに現れることを確認する。
    """
    n_bins = 2
    n_total = 200
    beat = 120
    preutt = 30
    overlap_natural = 5  # unit_b が unit_a の placed 末尾へ食い込む幅（設計上の想定値）
    start_b = beat - preutt  # 呼び出し側（render.py）が既に確定済みの絶対座標
    prev_len = start_b + overlap_natural  # unit_a の末尾がちょうど overlap_natural 分 unit_b の始点へ食い込む長さ

    sp_a = np.full((prev_len, n_bins), 1.0)
    ap_a = np.full((prev_len, n_bins), 0.1)
    n_b = 60
    # unit_b の各フレームを一意な値でマークする（frame index の検算用）。
    sp_b = np.stack([np.full(n_bins, 100.0 + i) for i in range(n_b)], axis=0)
    ap_b = np.full((n_b, n_bins), 0.2)

    placements = [
        jn.NotePlacement(0, prev_len, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(start_b, start_b + n_b, sp_b, ap_b, has_join_to_prev=True),
    ]
    sp_seq, _ap_seq, stats = jn.assemble_absolute(n_total, n_bins, placements)

    # unit_b のアタック点（frame[preutt]、= 100.0+preutt でマークされている）は
    # 絶対タイムライン上で start_b + preutt == beat に現れる。
    assert np.allclose(sp_seq[beat], sp_b[preutt])
    assert stats["n_joins_applied"] == 1  # unit_a の placed 末尾へ実際に重なった


def test_assemble_absolute_overlap_region_blends_not_trims() -> None:
    """(c) acceptance: 重なり区間はクロスフェードでブレンドされる（トリムでは
    ないため、両ノートとも自身の宣言した frame 数ぶん出力へ寄与する = 総尺は
    重なり分だけ短くならない。`test_assemble_v2_shrinks_timeline_by_overlap_
    within_phrase` との対比）。
    """
    n_bins = 5
    n_total = 120
    n1, n2, ov = 50, 50, 10
    sp_a = _wavy_sp(n1, n_bins, 1.0, seed=41)
    sp_b = _wavy_sp(n2, n_bins, 4.0, seed=42)
    ap_a = np.full((n1, n_bins), 0.1)
    ap_b = np.full((n2, n_bins), 0.2)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(n1 - ov, n1 - ov + n2, sp_b, ap_b, has_join_to_prev=True),
    ]
    sp_seq, _ap_seq, stats = jn.assemble_absolute(n_total, n_bins, placements)
    assert stats["n_joins_applied"] == 1
    assert stats["overlap_frames_applied"] == [ov]
    # トリムしない: unit_b の末尾は宣言どおり n1-ov+n2 に達する（総尺は縮まない・
    # 末尾の未配置区間は carry-forward で埋まる）。
    jumps = _interior_jumps(sp_seq[: n1 - ov + n2])
    interior_all = np.concatenate([_interior_jumps(sp_a), _interior_jumps(sp_b)])
    entering_seam = jumps[n1 - ov - 1]
    exiting_seam = jumps[n1 - 1]
    assert entering_seam <= np.max(interior_all) * 1.5
    assert exiting_seam <= np.max(interior_all) * 1.5


def test_assemble_absolute_no_overlap_direct_placement_and_gap_carry_forward() -> None:
    """重ならない場合（隙間ゼロまたは隙間あり）は単純配置 + carry-forward
    （v1 `assemble` と同じ規約）。"""
    n_bins = 3
    n_total = 100
    sp_a = np.full((20, n_bins), 2.0)
    ap_a = np.full((20, n_bins), 0.2)
    sp_b = np.full((15, n_bins), 9.0)
    ap_b = np.full((15, n_bins), 0.4)
    placements = [
        jn.NotePlacement(10, 30, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(60, 75, sp_b, ap_b, has_join_to_prev=False),
    ]
    sp_seq, _ap_seq, stats = jn.assemble_absolute(n_total, n_bins, placements)
    assert np.allclose(sp_seq[0:10], sp_a[0])
    assert np.allclose(sp_seq[30:60], sp_a[-1])
    assert np.allclose(sp_seq[75:100], sp_b[-1])
    assert stats["n_joins_applied"] == 0
    assert stats["n_gap_frames"] == 10 + 30 + 25


def test_assemble_absolute_deterministic_repeat() -> None:
    n_bins = 4
    n_total = 150
    sp_a = _wavy_sp(60, n_bins, 1.0, seed=51)
    sp_b = _wavy_sp(60, n_bins, 3.0, seed=52)
    ap_a = np.full((60, n_bins), 0.1)
    ap_b = np.full((60, n_bins), 0.2)
    placements = [
        jn.NotePlacement(0, 60, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(55, 115, sp_b, ap_b, has_join_to_prev=True),
    ]
    sp1, ap1, stats1 = jn.assemble_absolute(n_total, n_bins, placements)
    sp2, ap2, stats2 = jn.assemble_absolute(n_total, n_bins, placements)
    assert np.array_equal(sp1, sp2)
    assert np.array_equal(ap1, ap2)
    assert stats1 == stats2


def test_assemble_absolute_empty_placements() -> None:
    sp_seq, ap_seq, stats = jn.assemble_absolute(50, 4, [])
    assert sp_seq.shape == (50, 4)
    assert ap_seq.shape == (50, 4)
    assert stats["n_total_frames"] == 50
    assert stats["n_gap_frames"] == 50


def test_assemble_control_tracks_absolute_matches_sp_ap_placement() -> None:
    """f0/振幅トラックが sp/ap（`assemble_absolute`）と同一の絶対配置
    （同一 start_frame/end_frame/has_join_to_prev）で再構築されることを、
    n_bins=1 の場合の厳密一致で確認する（`assemble_control_tracks_v2` の
    同種テストの絶対グリッド版）。"""
    n_bins = 1
    n_total = 150
    n1, n2 = 60, 60
    start_b = 52  # n1 と重なる（ov=8 相当）
    val_a, val_b = 1.0, 5.0
    sp_a = np.full((n1, n_bins), val_a)
    ap_a = np.full((n1, n_bins), val_a * 0.1)
    sp_b = np.full((n2, n_bins), val_b)
    ap_b = np.full((n2, n_bins), val_b * 0.1)
    placements = [
        jn.NotePlacement(0, n1, sp_a, ap_a, has_join_to_prev=False),
        jn.NotePlacement(start_b, start_b + n2, sp_b, ap_b, has_join_to_prev=True),
    ]
    sp_seq, ap_seq, join_stats = jn.assemble_absolute(n_total, n_bins, placements)

    note_f0_tracks = [np.full(n1, val_a), np.full(n2, val_b)]
    note_amp_tracks = [np.full(n1, val_a * 0.1), np.full(n2, val_b * 0.1)]
    f0_seq, amp_seq, control_stats = jn.assemble_control_tracks_absolute(
        n_total, placements, note_f0_tracks, note_amp_tracks
    )
    assert control_stats["n_total_frames"] == join_stats["n_total_frames"] == n_total
    assert f0_seq.shape[0] == sp_seq.shape[0]
    # 配置区間内は厳密一致（同一 blend 幅・同一位置で処理しているため）。
    assert np.allclose(f0_seq[: start_b + n2], sp_seq[: start_b + n2, 0])
    assert np.allclose(amp_seq[: start_b + n2], ap_seq[: start_b + n2, 0])
    # gap（末尾の未配置区間）は sp/ap と異なり 0 埋め（carry-forward しない）。
    assert np.all(f0_seq[start_b + n2:] == 0.0)
    assert not np.allclose(sp_seq[start_b + n2:, 0], 0.0)


def test_assemble_control_tracks_absolute_empty_placements() -> None:
    f0_seq, amp_seq, stats = jn.assemble_control_tracks_absolute(40, [], [], [])
    assert f0_seq.shape == (40,)
    assert amp_seq.shape == (40,)
    assert np.all(f0_seq == 0.0)
    assert stats["n_total_frames"] == 40
