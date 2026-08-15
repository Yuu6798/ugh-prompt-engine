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
