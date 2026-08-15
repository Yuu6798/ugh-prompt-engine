"""test_adapter_render_donor_wiring.py — 追補 F1.2-D 配線の単体検証。
録音済み子音前置の決定論・母音分布ヘルパーを合成 fixture で検証する
（実 render パイプライン全体（WORLD 合成込み）は非依存・軽量）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np

import donor_bank_utau as dbu
import render as rd
import units as un


def _resolved(n_frames: int, n_bins: int = 8, fill: float = 1.0) -> un.ResolvedSegment:
    sp = np.full((n_frames, n_bins), fill, dtype=np.float64)
    ap = np.full((n_frames, n_bins), 0.1, dtype=np.float64)
    return un.ResolvedSegment(
        note_index=0, sp=sp, ap=ap, n_frames=n_frames, true_ratio=1.0, applied_ratio=1.0,
        cap_mode="none", n_loop_cycles=0,
    )


def _clip(n_frames: int, n_bins: int = 8, fill: float = 9.0) -> dbu.ConsonantClip:
    sp = np.full((n_frames, n_bins), fill, dtype=np.float64)
    ap = np.full((n_frames, n_bins), 0.9, dtype=np.float64)
    return dbu.ConsonantClip(
        onset="k", sp=sp, ap=ap, n_frames=n_frames, source_wav="w.wav", source_alias="- かA3",
        is_phrase_initial=True,
    )


def test_prepend_recorded_consonant_concatenates_in_order() -> None:
    resolved = _resolved(n_frames=10, fill=1.0)
    clip = _clip(n_frames=4, fill=9.0)
    out, event = rd.prepend_recorded_consonant(resolved, clip, note_index=3)

    assert out.n_frames == 14
    assert out.sp.shape == (14, 8)
    # クリップが先頭、母音核が後ろ（時系列順）。
    assert np.all(out.sp[:4] == 9.0)
    assert np.all(out.sp[4:] == 1.0)
    assert np.all(out.ap[:4] == 0.9)
    assert np.all(out.ap[4:] == 0.1)

    assert event.note_index == 3
    assert event.onset == "k"
    assert event.consonant_class == "recorded"
    assert event.n_frames_processed == 4


def test_prepend_recorded_consonant_is_deterministic() -> None:
    resolved = _resolved(n_frames=6)
    clip = _clip(n_frames=3)
    out1, ev1 = rd.prepend_recorded_consonant(resolved, clip, note_index=0)
    out2, ev2 = rd.prepend_recorded_consonant(resolved, clip, note_index=0)
    assert np.array_equal(out1.sp, out2.sp)
    assert np.array_equal(out1.ap, out2.ap)
    assert ev1 == ev2


def test_prepend_recorded_consonant_does_not_mutate_inputs() -> None:
    resolved = _resolved(n_frames=5, fill=2.0)
    clip = _clip(n_frames=2, fill=7.0)
    sp_before = resolved.sp.copy()
    rd.prepend_recorded_consonant(resolved, clip, note_index=0)
    assert np.array_equal(resolved.sp, sp_before)


def test_vowel_distribution_from_labels() -> None:
    labels = {0: "a", 1: "a", 2: "i", 3: "N"}
    dist = rd._vowel_distribution_from_labels(labels)
    assert dist == {"a": 2, "i": 1, "N": 1}


def test_vowel_distribution_from_labels_empty() -> None:
    assert rd._vowel_distribution_from_labels({}) == {}


# --- 追補 F1.4-B: VCV 配置（preutterance 消費のタイムライン整合） ---


class _FakeMora:
    def __init__(self, kana: str, vowel: str) -> None:
        self.kana = kana
        self.vowel = vowel


class _FakeNote:
    def __init__(self, kana: str, vowel: str, midi: float = 57.0) -> None:
        self.mora = _FakeMora(kana, vowel)
        self.midi = midi


class _FakeSeg:
    def __init__(
        self, start_sample: int, end_sample: int, is_phrase_first: bool, kana: str = "か", vowel: str = "a",
    ) -> None:
        self.start_sample = start_sample
        self.end_sample = end_sample
        self.is_phrase_first = is_phrase_first
        self.note = _FakeNote(kana, vowel)


def _vcv_unit(index: int, overlap_frames: int, preutterance_frames: int, n_frames: int = 200) -> dbu.DonorUnit:
    n = 4
    return dbu.DonorUnit(
        index=index, start_frame=0, end_frame=n_frames, median_f0=220.0, duration_s=n_frames * 5.0 / 1000.0,
        head_log_bands=np.zeros(n), tail_log_bands=np.zeros(n),
        overlap_frames=overlap_frames, preutterance_frames=preutterance_frames,
        vowel_core_start_frame=min(preutterance_frames + 4, n_frames),
    )


class _FakeVCVSelection:
    def __init__(self, unit: dbu.DonorUnit) -> None:
        self.unit = unit


class _FakeBankVCV:
    def __init__(self, n_bins: int = 4, n_frames: int = 200) -> None:
        self.sp = np.arange(n_frames * n_bins, dtype=np.float64).reshape(n_frames, n_bins) + 1.0
        self.ap = np.full((n_frames, n_bins), 0.1)


def test_build_vcv_placements_phrase_first_shifts_start_by_preutterance() -> None:
    """フレーズ先頭（is_phrase_first=True）のノートは preutterance 分だけ
    cursor より前に配置される（利用可能なギャップ = ブレス 0.25s = 50 frame
    @5ms を下回る preutterance なのでクリップされない）。"""
    sr = 24000
    breath_samples = int(0.25 * sr)
    segs = [
        _FakeSeg(0, sr, True),  # 最初のフレーズ（ギャップ無し）
        _FakeSeg(sr + breath_samples, sr + breath_samples + sr, True),  # 2番目のフレーズ頭（0.25s ブレス）
    ]
    bank = _FakeBankVCV()
    unit0 = _vcv_unit(0, overlap_frames=4, preutterance_frames=12)
    unit1 = _vcv_unit(1, overlap_frames=4, preutterance_frames=20)
    selections = [_FakeVCVSelection(unit0), _FakeVCVSelection(unit1)]

    placements, resolved_list, stats = rd._build_vcv_placements(segs, selections, bank)

    # 最初のノート: 直前ギャップ 0 -> shift=0（preutterance を消費できない）。
    assert placements[0].start_frame == 0
    # 2番目のノート: ギャップ=50frame(0.25s) >= preutterance(20) -> shift=20 満額。
    breath_frames = 50  # 0.25s @ 5ms
    naive_start = breath_frames + resolved_list[0].n_frames
    assert placements[1].start_frame == naive_start - 20
    assert stats["n_preutterance_applied"] == 1  # 最初のノートは shift=0 なのでカウント外
    assert stats["n_preutterance_clipped"] == 0
    assert stats["preutterance_shift_frames"] == [20]


def test_build_vcv_placements_clips_preutterance_within_breath_budget() -> None:
    """preutterance がブレスギャップより大きい場合はギャップ幅にクリップされる
    （0.25s の範囲内でクリップ・発動記録）。"""
    sr = 24000
    breath_samples = int(0.25 * sr)  # -> 50 frames @5ms
    segs = [
        _FakeSeg(0, sr, True),
        _FakeSeg(sr + breath_samples, sr + breath_samples + sr, True),
    ]
    bank = _FakeBankVCV()
    unit0 = _vcv_unit(0, overlap_frames=4, preutterance_frames=0)
    unit1 = _vcv_unit(1, overlap_frames=4, preutterance_frames=999)  # ギャップよりずっと大きい
    selections = [_FakeVCVSelection(unit0), _FakeVCVSelection(unit1)]

    placements, resolved_list, stats = rd._build_vcv_placements(segs, selections, bank)

    breath_frames = 50
    naive_start = breath_frames + resolved_list[0].n_frames
    assert placements[1].start_frame == naive_start - breath_frames  # クリップされ 50 frame のみ消費
    assert stats["n_preutterance_clipped"] == 1
    assert stats["preutterance_shift_frames"] == [50]


def test_build_vcv_placements_mid_phrase_note_start_unshifted() -> None:
    """フレーズ内部（has_join_to_prev=True）のノートは start_frame を
    シフトしない（接合は overlap_frames 由来の overlap-add に委ねる）。"""
    sr = 24000
    segs = [_FakeSeg(0, sr, True), _FakeSeg(sr, 2 * sr, False)]
    bank = _FakeBankVCV()
    unit0 = _vcv_unit(0, overlap_frames=4, preutterance_frames=10)
    unit1 = _vcv_unit(1, overlap_frames=4, preutterance_frames=30)
    selections = [_FakeVCVSelection(unit0), _FakeVCVSelection(unit1)]

    placements, resolved_list, _stats = rd._build_vcv_placements(segs, selections, bank)
    assert placements[1].start_frame == resolved_list[0].n_frames  # cursor そのまま（シフト無し）
    assert placements[1].has_join_to_prev is True
    assert placements[1].overlap_frames == 4


def test_build_vcv_placements_end_frame_unaffected_by_shift() -> None:
    """end_frame は shift 非依存（次 run のギャップ計算を狂わせないため）。"""
    sr = 24000
    breath_samples = int(0.25 * sr)
    segs = [
        _FakeSeg(0, sr, True),
        _FakeSeg(sr + breath_samples, sr + breath_samples + sr, True),
    ]
    bank = _FakeBankVCV()
    unit0 = _vcv_unit(0, overlap_frames=4, preutterance_frames=0)
    unit1 = _vcv_unit(1, overlap_frames=4, preutterance_frames=15)
    selections = [_FakeVCVSelection(unit0), _FakeVCVSelection(unit1)]

    placements, resolved_list, _stats = rd._build_vcv_placements(segs, selections, bank)
    breath_frames = 50
    naive_start = breath_frames + resolved_list[0].n_frames
    assert placements[1].end_frame == naive_start + resolved_list[1].n_frames
    assert placements[1].start_frame < placements[1].end_frame - resolved_list[1].n_frames + 1


# --- P1 修正 (review #262): _note_frame_track（f0/振幅トラックのノート単位抽出） ---


class _MiniSeg:
    def __init__(self, start_sample: int) -> None:
        self.start_sample = start_sample


def test_note_frame_track_samples_at_frame_boundaries() -> None:
    """圧縮前・ノート自身の実スパン内で、5ms(=24000*0.005=120 samples) おきに
    per-sample トラックをサンプリングする（consonant 前置なし = extra_head=0）。"""
    sr = 24000
    per_sample = np.arange(10000, dtype=np.float64)  # per_sample[i] == i（読み出し位置の検算用）
    seg = _MiniSeg(start_sample=1000)
    note_dur_frames = 5
    track = rd._note_frame_track(per_sample, seg, note_dur_frames, n_frames=5, sr=sr, frame_period_ms=5.0)
    samples_per_frame = sr * 5.0 / 1000.0  # 120
    expected = np.array([1000 + k * samples_per_frame for k in range(5)])
    assert np.array_equal(track, expected)


def test_note_frame_track_consonant_extended_head_maps_backward() -> None:
    """録音子音前置で n_frames > note_dur_frames の場合、末尾 note_dur_frames
    フレームが seg の実スパンへ整列し、先頭の余剰フレーム（子音）は
    seg.start_sample より前へマップされる。"""
    sr = 24000
    per_sample = np.arange(10000, dtype=np.float64)
    seg = _MiniSeg(start_sample=1000)
    note_dur_frames = 5
    n_frames = 8  # 3 フレーム分の子音が前置されたと仮定
    track = rd._note_frame_track(per_sample, seg, note_dur_frames, n_frames, sr=sr, frame_period_ms=5.0)
    samples_per_frame = 120.0
    extra_head = n_frames - note_dur_frames  # 3
    expected = np.array([1000 + (k - extra_head) * samples_per_frame for k in range(n_frames)])
    assert np.array_equal(track, expected)
    # 末尾 note_dur_frames フレームは通常ケースと一致する。
    normal = rd._note_frame_track(per_sample, seg, note_dur_frames, note_dur_frames, sr=sr, frame_period_ms=5.0)
    assert np.array_equal(track[extra_head:], normal)


def test_note_frame_track_clips_at_zero_when_head_extends_before_start() -> None:
    """seg.start_sample - extra_head*samples_per_frame が負になる場合は 0 側へ
    クランプする（per_sample トラック範囲外を読まない）。"""
    sr = 24000
    per_sample = np.arange(100, dtype=np.float64)
    seg = _MiniSeg(start_sample=10)
    track = rd._note_frame_track(per_sample, seg, note_dur_frames=2, n_frames=10, sr=sr, frame_period_ms=5.0)
    assert track[0] == 0.0  # クランプされて先頭サンプルを指す
    assert np.all(track >= 0.0)


def test_note_frame_track_empty_per_sample_returns_zeros() -> None:
    seg = _MiniSeg(start_sample=0)
    track = rd._note_frame_track(np.zeros(0), seg, note_dur_frames=4, n_frames=4, sr=24000, frame_period_ms=5.0)
    assert np.array_equal(track, np.zeros(4))


def test_note_frame_track_zero_n_frames_returns_empty() -> None:
    seg = _MiniSeg(start_sample=0)
    track = rd._note_frame_track(np.arange(10.0), seg, note_dur_frames=4, n_frames=0, sr=24000, frame_period_ms=5.0)
    assert track.shape == (0,)
