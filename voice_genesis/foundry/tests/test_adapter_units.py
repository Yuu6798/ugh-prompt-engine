"""test_adapter_units.py — 単位選択の決定論 + 伸縮キャップとループの検証。
合成ミニドナー（DonorUnit を手組み）で高速・実 vocadito 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest

import donor_bank as db
import units as un


def _make_unit(index: int, start: int, end: int, median_f0: float, head=None, tail=None) -> db.DonorUnit:
    n = db.N_LOG_BANDS
    if head is None:
        head = np.zeros(n)
    if tail is None:
        tail = np.zeros(n)
    return db.DonorUnit(
        index=index, start_frame=start, end_frame=end, median_f0=median_f0,
        duration_s=(end - start) * 5.0 / 1000.0, head_log_bands=np.asarray(head, dtype=np.float64),
        tail_log_bands=np.asarray(tail, dtype=np.float64),
    )


def test_select_units_deterministic_repeat() -> None:
    units = [
        _make_unit(0, 0, 100, 220.0),
        _make_unit(1, 100, 180, 330.0),
        _make_unit(2, 180, 260, 220.0),
    ]
    targets = [
        un.TargetNote(pitch_hz=220.0, duration_sec=0.5),
        un.TargetNote(pitch_hz=330.0, duration_sec=0.4),
    ]
    sel1, stats1 = un.select_units(targets, units)
    sel2, stats2 = un.select_units(targets, units)
    assert [s.unit.index for s in sel1] == [s.unit.index for s in sel2]
    assert stats1 == stats2


def test_select_units_tie_break_smallest_index() -> None:
    # unit 0 と unit 2 が全コスト同点になるよう設計 (同じ pitch/duration/head)
    units = [
        _make_unit(0, 0, 100, 220.0, head=[1.0] * db.N_LOG_BANDS),
        _make_unit(1, 100, 180, 500.0, head=[9.0] * db.N_LOG_BANDS),  # 遠い pitch (候補外)
        _make_unit(2, 180, 280, 220.0, head=[1.0] * db.N_LOG_BANDS),
    ]
    targets = [un.TargetNote(pitch_hz=220.0, duration_sec=0.5)]
    sel, _ = un.select_units(targets, units)
    assert sel[0].unit.index == 0  # tie は最小 index


def test_select_units_expands_candidate_range_when_empty() -> None:
    units = [_make_unit(0, 0, 100, 220.0)]
    # 700Hz は 220Hz から 3 半音レンジを大きく外れる -> 段階拡張が発生するはず
    targets = [un.TargetNote(pitch_hz=700.0, duration_sec=0.3)]
    sel, stats = un.select_units(targets, units)
    assert sel[0].expanded is True
    assert sel[0].unit.index == 0
    assert stats["n_expansions"] == 1


def test_select_units_concat_cost_prefers_continuity() -> None:
    # 2 note 目で、pitch/duration が同等な 2 候補のうち head が prev.tail に近い方を選ぶはず
    prev_tail = np.array([5.0] * db.N_LOG_BANDS)
    close_head = prev_tail + 0.01
    far_head = prev_tail + 50.0
    units = [
        _make_unit(0, 0, 100, 220.0, tail=prev_tail),
        _make_unit(1, 100, 200, 220.0, head=close_head),
        _make_unit(2, 200, 300, 220.0, head=far_head),
    ]
    targets = [
        un.TargetNote(pitch_hz=220.0, duration_sec=0.5),
        un.TargetNote(pitch_hz=220.0, duration_sec=0.5),
    ]
    sel, _ = un.select_units(targets, units)
    assert sel[0].unit.index == 0
    assert sel[1].unit.index == 1  # close_head の方が concat cost 小


def test_select_units_empty_units_raises() -> None:
    with pytest.raises(ValueError):
        un.select_units([un.TargetNote(220.0, 0.5)], [])


class _FakeBank:
    def __init__(self, sp: np.ndarray, ap: np.ndarray):
        self.sp = sp
        self.ap = ap


def _sp_ap_for_unit(n_frames: int, n_bins: int, base: float) -> tuple[np.ndarray, np.ndarray]:
    sp = np.full((n_frames, n_bins), base) + np.arange(n_frames)[:, None] * 0.01
    ap = np.full((n_frames, n_bins), 0.1)
    return sp, ap


def test_resolve_unit_to_note_no_cap_exact_length() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(100, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 100, 220.0)
    target_n_frames = 120  # ratio 1.2 -> [0.5,2.0] 内
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "none"
    assert resolved.sp.shape == (target_n_frames, n_bins)
    assert resolved.true_ratio == pytest.approx(1.2)


def test_resolve_unit_to_note_extended_loop_for_long_note() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(50, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 50, 220.0)
    target_n_frames = 400  # ratio 8.0 >> 2.0 -> loop 必須
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "extended_looped"
    assert resolved.applied_ratio == pytest.approx(2.0)
    assert resolved.n_loop_cycles >= 1
    assert resolved.sp.shape == (target_n_frames, n_bins)


def test_resolve_unit_to_note_compressed_truncated_for_short_target() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(200, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 200, 220.0)
    target_n_frames = 40  # ratio 0.2 << 0.5 -> 圧縮キャップ + 切り詰め
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "compressed_truncated"
    assert resolved.applied_ratio == pytest.approx(0.5)
    assert resolved.sp.shape == (target_n_frames, n_bins)


def test_resolve_unit_to_note_deterministic_repeat() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(70, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 70, 220.0)
    r1 = un.resolve_unit_to_note(bank, unit, 500)
    r2 = un.resolve_unit_to_note(bank, unit, 500)
    assert np.array_equal(r1.sp, r2.sp)
    assert np.array_equal(r1.ap, r2.ap)
    assert r1.n_loop_cycles == r2.n_loop_cycles


# --- 追補 F1.3-A item3: 長音ループ境界の overlap-add ---


def _interior_jumps(sp: np.ndarray) -> np.ndarray:
    diffs = np.log(sp[1:] + 1e-8) - np.log(sp[:-1] + 1e-8)
    return np.linalg.norm(diffs, axis=1)


def test_ping_pong_pad_v2_matches_v1_total_length() -> None:
    n_bins = 4
    seg_len = 20
    sp = np.abs(np.random.default_rng(5).standard_normal((seg_len, n_bins))) + 1.0
    ap = np.full((seg_len, n_bins), 0.2)
    deficit = 90  # 4.5 cycles 相当

    sp_v1, n1 = un._ping_pong_pad(sp, deficit)
    sp_v2, ap_v2, n2 = un._ping_pong_pad_v2(sp, ap, deficit, overlap_frames=6)

    assert n1 == n2  # サイクル数（ピース数）は overlap-add の有無で不変
    assert sp_v1.shape[0] == deficit  # v1: 硬い折返し・deficit ちょうど
    # v2: overlap-add で継ぎ目ごとに短くなる（ピース数-1 回の join 分）。
    assert sp_v2.shape[0] < deficit
    assert ap_v2.shape[0] == sp_v2.shape[0]


def test_ping_pong_pad_v2_seam_smoother_than_hard_fold() -> None:
    """折返し境界の overlap-add が「硬い折返し」より継ぎ目の跳びを増大させない
    ことを確認する（`_ping_pong_pad` (v1) との比較。完全な平滑化は主張しないが、
    overlap-add 適用箇所の跳びが単位内部の跳び分布を極端に超えないことを見る）。
    """
    n_bins = 5
    seg_len = 30
    rng = np.random.default_rng(9)
    sp = np.abs(1.0 + 0.3 * rng.standard_normal((seg_len, n_bins)).cumsum(axis=0) * 0.05) + 0.5
    ap = np.full((seg_len, n_bins), 0.2)
    deficit = 3 * seg_len  # ちょうど 3 ピース（2 継ぎ目）

    sp_v2, _ap_v2, n_cycles = un._ping_pong_pad_v2(sp, ap, deficit, overlap_frames=8)
    assert n_cycles == 3
    jumps_v2 = _interior_jumps(sp_v2)
    jumps_interior = _interior_jumps(sp)
    # 折返し継ぎ目付近の跳びが unit 内部の跳び分布の最大値を極端に超えない
    # （硬い折返しであれば方向反転点で跳びが跳ね上がりうる）。
    assert np.max(jumps_v2) <= np.max(jumps_interior) * 3.0


def test_resolve_unit_to_note_extended_loop_v2_seams_use_overlap_add() -> None:
    """resolve_unit_to_note が実際に v2 ループパディングを使っており、
    (a) 最終形状は target_n_frames ちょうど、(b) n_loop_cycles >= 1、
    という既存 acceptance を壊さないことを確認する（後方互換）。"""
    n_bins = 4
    sp, ap = _sp_ap_for_unit(50, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 50, 220.0)
    target_n_frames = 400
    resolved = un.resolve_unit_to_note(bank, unit, target_n_frames, frame_period_ms=5.0)
    assert resolved.n_frames == target_n_frames
    assert resolved.cap_mode == "extended_looped"
    assert resolved.n_loop_cycles >= 1
    assert resolved.sp.shape == (target_n_frames, n_bins)


def test_resolve_unit_to_note_loop_deterministic_with_frame_period() -> None:
    n_bins = 4
    sp, ap = _sp_ap_for_unit(50, n_bins, 1.0)
    bank = _FakeBank(sp, ap)
    unit = _make_unit(0, 0, 50, 220.0)
    r1 = un.resolve_unit_to_note(bank, unit, 350, frame_period_ms=5.0)
    r2 = un.resolve_unit_to_note(bank, unit, 350, frame_period_ms=5.0)
    assert np.array_equal(r1.sp, r2.sp)
    assert np.array_equal(r1.ap, r2.ap)
    assert r1.n_loop_cycles == r2.n_loop_cycles
