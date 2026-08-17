"""test_adapter_render_donor_wiring.py — 追補 F1.2-D 配線の単体検証。
録音済み子音前置の決定論・母音分布ヘルパーを合成 fixture で検証する
（実 render パイプライン全体（WORLD 合成込み）は非依存・軽量）。
"""
from __future__ import annotations

import csv
import hashlib
import sys
from pathlib import Path
from typing import Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest
import soundfile as sf

import donor_bank_lab as dbl
import donor_bank_utau as dbu
import render as rd  # sys.path へ singer/ を追加する副作用に依存（下の performance import 前に必要）
import performance as perf
import units as un
import voice_spec as vs


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


def test_build_vcv_placements_mid_phrase_note_start_shifted_by_preutterance() -> None:
    """F1.4 R3（絶対グリッド配置・PR #262 R3）: フレーズ内部
    （has_join_to_prev=True）のノートも自身の preutterance 分だけ
    start_frame が前方へシフトされる。

    R2 までは mid-phrase note の start_frame を cursor のまま据え置き、
    `joins.assemble_v2` 側の追加トリム（`_resolve_extra_trim`）で
    preutterance を消費していた。この方式は接合のたびに直前アキュムレータ
    末尾を実際にトリムして総尺を縮める副作用があり、run を跨いで縮みが
    伝播し score 総尺から累積的にずれていた（record 参照）。R3 は shift を
    配置そのものへ織り込み、`joins.assemble_absolute` が固定長バッファへ
    trim なしで配置する（重なりがあれば trim ではなくブレンドする）。
    """
    sr = 24000
    segs = [_FakeSeg(0, sr, True), _FakeSeg(sr, 2 * sr, False)]
    bank = _FakeBankVCV()
    unit0 = _vcv_unit(0, overlap_frames=4, preutterance_frames=10)
    unit1 = _vcv_unit(1, overlap_frames=4, preutterance_frames=30)
    selections = [_FakeVCVSelection(unit0), _FakeVCVSelection(unit1)]

    placements, resolved_list, _stats = rd._build_vcv_placements(segs, selections, bank)
    assert placements[1].start_frame == resolved_list[0].n_frames - 30
    assert placements[1].has_join_to_prev is True
    assert placements[1].overlap_frames == 4
    # R3: preutterance_frames は record/後方互換のため引き続き渡すが、
    # assemble_absolute はこのフィールドを読まない（絶対座標の交差のみで
    # 重なりを決めるため）。
    assert placements[1].preutterance_frames == 30


def test_build_vcv_placements_end_frame_shifted_with_start_frame() -> None:
    """F1.4 R3: end_frame は start_frame と同じ shift を受ける（R2 までの
    「end_frame は shift 非依存」を撤廃）。絶対グリッド上では unit 自身の長さ
    （`resolved.n_frames` = target_n_frames）は shift の影響を受けないため、
    `end_frame = start_frame + resolved.n_frames` が常に成り立つ。
    """
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
    assert placements[1].start_frame == naive_start - 15
    assert placements[1].end_frame == placements[1].start_frame + resolved_list[1].n_frames
    assert placements[1].end_frame == naive_start - 15 + resolved_list[1].n_frames


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


# ---------------------------------------------------------------------------
# P1 修正 (review #262 R4・`r3789341843`): _note_frame_track の origin_shift_frames
# （VCV 経路の f0/振幅サンプリング原点を絶対配置シフトへ整合させる）
# ---------------------------------------------------------------------------


def test_note_frame_track_origin_shift_moves_sampling_window_earlier() -> None:
    """`origin_shift_frames=shift` は、k=0 が `seg.start_sample -
    shift*samples_per_frame` を指すようサンプリング原点を前へずらす
    （`_build_vcv_placements` の `start_frame = cursor - shift` と同じ原点
    シフトを、コントロールトラック抽出にも適用する）。"""
    sr = 24000
    per_sample = np.arange(10000, dtype=np.float64)  # per_sample[i] == i
    seg = _MiniSeg(start_sample=5000)  # 0 側クランプの影響を受けないよう十分大きく取る
    note_dur_frames = 5
    samples_per_frame = 120.0  # 24000 * 5ms/1000
    shift = 20  # frame

    track = rd._note_frame_track(
        per_sample, seg, note_dur_frames, n_frames=5, sr=sr, frame_period_ms=5.0,
        origin_shift_frames=shift,
    )
    expected = np.array([5000 - shift * samples_per_frame + k * samples_per_frame for k in range(5)])
    assert np.array_equal(track, expected)


def test_note_frame_track_origin_shift_zero_matches_default_behavior() -> None:
    """`origin_shift_frames=0`（既定値）は従来どおり無補正
    （vocadito/pjs 経路の完全不変を保証する回帰ガード）。"""
    sr = 24000
    per_sample = np.arange(10000, dtype=np.float64)
    seg = _MiniSeg(start_sample=1000)
    default = rd._note_frame_track(per_sample, seg, 5, n_frames=5, sr=sr, frame_period_ms=5.0)
    explicit_zero = rd._note_frame_track(
        per_sample, seg, 5, n_frames=5, sr=sr, frame_period_ms=5.0, origin_shift_frames=0,
    )
    assert np.array_equal(default, explicit_zero)


def test_note_frame_track_origin_shift_preutterance_frame_lands_at_beat_sample() -> None:
    """origin_shift_frames=shift・shift=preutterance のとき、track の
    frame[shift]（= unit の真のアタック点）は per_sample[seg.start_sample]
    （= score 上の拍位置そのもの）を指す（P1 検証 (a)/(b): f0 の拍位置整合）。"""
    sr = 24000
    per_sample = np.arange(10000, dtype=np.float64)
    seg = _MiniSeg(start_sample=1000)
    shift = 20
    track = rd._note_frame_track(
        per_sample, seg, note_dur_frames=40, n_frames=40, sr=sr, frame_period_ms=5.0,
        origin_shift_frames=shift,
    )
    assert track[shift] == 1000.0


def test_note_frame_track_origin_shift_pre_beat_region_holds_previous_note_value() -> None:
    """P1 検証 (c): shift 適用前の pre-beat 区間（k < shift）は
    `seg.start_sample` より前の per_sample（= 前ノートの f0/振幅を保持する
    連続トラック上の値）から読み出され、次ノート自身の frame[0]（=
    seg.start_sample の値）が前倒しで現れないことを確認する。"""
    sr = 24000
    per_sample = np.concatenate([np.full(1000, 111.0), np.full(9000, 222.0)])  # 境界=1000
    seg = _MiniSeg(start_sample=1000)
    shift = 20
    track = rd._note_frame_track(
        per_sample, seg, note_dur_frames=40, n_frames=40, sr=sr, frame_period_ms=5.0,
        origin_shift_frames=shift,
    )
    # pre-beat 区間（k=0..shift-1）は前ノート値（111.0）を保持する。
    assert np.all(track[:shift] == 111.0)
    # frame[shift]（拍位置）以降は新ノート値（222.0）。
    assert np.all(track[shift:] == 222.0)


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R14・`r3789636053`): _extend_phrase_head_amp_for_preutterance
# （origin-shift されたフレーズ頭 preutterance の消音を防ぐ）
# ---------------------------------------------------------------------------


def _amp_env_with_phrase(
    total_samples: int, phrase_start: int, phrase_end: int, arch_level: float, sr: int = 24000,
) -> np.ndarray:
    """`perf.build_amplitude_envelope` の出力形状（フレーズ区間のみ非ゼロ +
    フレーズ先頭の attack フェード）を模した合成 fixture。"""
    amp = np.zeros(total_samples, dtype=np.float64)
    amp[phrase_start:phrase_end] = arch_level
    attack_release = int(sr * perf.NOTE_ATTACK_RELEASE_MS / 1000.0)
    n = phrase_end - phrase_start
    ar = min(attack_release, n // 4) if n >= 4 else 0
    if ar > 0:
        amp[phrase_start:phrase_start + ar] *= np.linspace(0.0, 1.0, ar)
    return amp


def test_extend_phrase_head_amp_makes_preutterance_region_audible() -> None:
    """origin-shift 分（shift_frames）だけ前方に amp_env を定数外挿し、旧実装で
    ゼロだったブレス区間（preutterance を置いた区間）が非ゼロになることを
    確認する（実害の再現・review #262 R13 `r3789636053`）。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)
    shift_frames = 20  # -> 20 * 120 = 2400 samples
    # 修正前: この区間はブレスのゼロ初期化のまま。
    assert np.all(amp_env[2600:5000] == 0.0)

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    # 拡張区間の大半（フェード完了後）は非ゼロ（子音/遷移が可聴になる）。
    assert extended[4500] > 0.0
    assert extended[4999] > 0.0
    # フェード完了後の水準は「フェード適用前のアーチ境界値」（= arch_level）
    # に一致する（マジックナンバー再実装ではなく amp_env から読んだ値）。
    assert extended[4999] == pytest.approx(0.9, abs=1e-9)


def test_extend_phrase_head_amp_fades_in_at_extended_edge_no_click() -> None:
    """外挿区間の先頭は 0 から滑らかにフェードイン（クリック防止）し、突然
    フルレベルへジャンプしない。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)
    shift_frames = 20

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    ext_start = 5000 - shift_frames * 120  # 2600
    assert extended[ext_start] == 0.0  # フェード先頭
    assert 0.0 < extended[ext_start + 100] < 0.9  # フェード途中は単調に増加
    assert extended[ext_start + 100] < extended[4999]


def test_extend_phrase_head_amp_untouched_before_extended_region() -> None:
    """外挿区間より手前（本来のブレス無音）は変更しない。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)
    shift_frames = 20

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    ext_start = 5000 - shift_frames * 120
    assert np.all(extended[:ext_start] == 0.0)


def test_extend_phrase_head_amp_leaves_phrase_body_past_attack_unchanged() -> None:
    """フレーズアーチ自体（`[start_sample+ar, phrase_end)`）の形は変更しない。
    `[start_sample, start_sample+ar)` は R19 修正で旧 attack フェードから
    定数充填（延長端のフェードと連続）へ置き換わるため、この区間は本来
    `[phrase_start, phrase_end)` 全体不変の対象から除く（旧テストは R14
    実装の副作用——二重フェード——を「不変」と誤認していた）。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)
    shift_frames = 20
    attack_release = int(sr * perf.NOTE_ATTACK_RELEASE_MS / 1000.0)
    ar = min(attack_release, (7000 - 5000) // 4)

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    assert np.array_equal(extended[5000 + ar:7000], amp_env[5000 + ar:7000])


def test_extend_phrase_head_amp_no_double_fade_notch_and_monotonic() -> None:
    """R19 指摘1: 旧実装は拡張区間のみ埋め、`build_amplitude_envelope` が
    書いた元の attack フェード `[start, start+ar)` を残していたため、
    「立ち上がり→`seg.start_sample` でゼロ落ち→再立ち上がり」のノッチが
    生じていた。修正後はフェードを延長端 `[ext_start, ext_start+ar)` へ
    移設した単一の立ち上がりになるため、`[ext_start, start+ar]` は
    単調非減少（ノッチ不在）で、`start+ar` は既存エンベロープの同点の値と
    連続に接続する。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)
    shift_frames = 20
    attack_release = int(sr * perf.NOTE_ATTACK_RELEASE_MS / 1000.0)
    ar = min(attack_release, (7000 - 5000) // 4)
    ext_start = 5000 - shift_frames * 120  # 2600

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    window = extended[ext_start:5000 + ar + 1]
    assert np.all(np.diff(window) >= -1e-12)  # 単調非減少 = ノッチ不在
    assert extended[5000 + ar] == pytest.approx(amp_env[5000 + ar], abs=1e-9)


def test_extend_phrase_head_amp_skips_mid_phrase_notes() -> None:
    """`is_phrase_first=False`（レガート接続の mid-phrase ノート）は shift>0
    でも変更しない（このノートには preutterance によるブレス消音問題が
    発生しない——直前ノートの音が既にそこにある）。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=False)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [20], sr, frame_period_ms=5.0,
    )

    assert np.array_equal(extended, amp_env)


def test_extend_phrase_head_amp_zero_shift_is_noop() -> None:
    """shift=0（preutterance を消費できない=直前ギャップ 0）は変更しない
    （vocadito/pjs 経路は shift 全 0 のリストで呼ばれるため完全不変）。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [0], sr, frame_period_ms=5.0,
    )

    assert np.array_equal(extended, amp_env)


def test_extend_phrase_head_amp_clamps_at_zero_when_shift_exceeds_start() -> None:
    """shift がノート開始位置そのものより大きい場合、外挿区間はバッファ先頭
    （index 0）でクランプされる（負インデックスへは書き込まない）。"""
    sr = 24000
    seg = _FakeSeg(start_sample=100, end_sample=2100, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=100, phrase_end=2100, arch_level=0.9, sr=sr)
    shift_frames = 20  # shift_samples=2400 > seg.start_sample=100

    extended = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    assert extended.shape == amp_env.shape  # クラッシュせず、負インデックスを書かない
    assert extended[0] == 0.0  # 0 側でクランプされた外挿区間の先頭 = フェード始点
    assert extended[99] > 0.0  # クランプ後の区間内は可聴（フェード進行中）
    assert extended[99] < extended[460]  # フェードは単調に増加し、アーチ水準へ向かう


# ---------------------------------------------------------------------------
# P3 修正 (review #262 R19 指摘 3): _extend_phrase_head_f0_for_preutterance
# （amp 側と対になる F0 後方延長。origin-shift された preutterance の無声化を防ぐ）
# ---------------------------------------------------------------------------


def _f0_persample_with_note(
    total_samples: int, note_start: int, note_end: int, f0_hz: float,
) -> np.ndarray:
    """`perf.build_f0_contour` の出力形状（ノート区間のみ非ゼロ・ブレスは0）を
    模した合成 fixture。"""
    f0 = np.zeros(total_samples, dtype=np.float64)
    f0[note_start:note_end] = f0_hz
    return f0


def test_extend_phrase_head_f0_fills_preutterance_with_note_onset_pitch() -> None:
    """延長区間 `[ext_start, seg.start_sample)` はノート開始時点の F0 で
    定数外挿され、preutterance 区間が有声になる（R19 指摘3の再現確認）。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    f0 = _f0_persample_with_note(10000, note_start=5000, note_end=7000, f0_hz=311.13)
    shift_frames = 20  # -> ext_start = 5000 - 2400 = 2600
    ext_start = 5000 - shift_frames * 120
    # 修正前: この区間はブレスのゼロ初期化のまま（無声）。
    assert np.all(f0[ext_start:5000] == 0.0)

    extended = rd._extend_phrase_head_f0_for_preutterance(
        f0, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    window = extended[ext_start:5000]
    assert np.all(window > 0.0)
    assert np.all(window == pytest.approx(311.13, abs=1e-9))
    # ビブラート等の時間変化は付与しない（定数外挿・全フレーム同一値）。
    assert np.all(window == window[0])


def test_extend_phrase_head_f0_falls_back_to_first_voiced_value_when_onset_is_zero() -> None:
    """`seg.start_sample` 自体がまだ無声（0）の場合、ノート内で最初に現れる
    有声値へフォールバックする。"""
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    f0 = _f0_persample_with_note(10000, note_start=5050, note_end=7000, f0_hz=233.08)  # 立ち上がり遅延
    shift_frames = 20
    ext_start = 5000 - shift_frames * 120

    extended = rd._extend_phrase_head_f0_for_preutterance(
        f0, [seg], [shift_frames], sr, frame_period_ms=5.0,
    )

    assert np.all(extended[ext_start:5000] == pytest.approx(233.08, abs=1e-9))


def test_extend_phrase_head_f0_skips_mid_phrase_notes() -> None:
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=False)
    f0 = _f0_persample_with_note(10000, note_start=5000, note_end=7000, f0_hz=311.13)

    extended = rd._extend_phrase_head_f0_for_preutterance(
        f0, [seg], [20], sr, frame_period_ms=5.0,
    )

    assert np.array_equal(extended, f0)


def test_extend_phrase_head_f0_zero_shift_is_noop() -> None:
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    f0 = _f0_persample_with_note(10000, note_start=5000, note_end=7000, f0_hz=311.13)

    extended = rd._extend_phrase_head_f0_for_preutterance(
        f0, [seg], [0], sr, frame_period_ms=5.0,
    )

    assert np.array_equal(extended, f0)


def test_extend_phrase_head_f0_does_not_mutate_input() -> None:
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    f0 = _f0_persample_with_note(10000, note_start=5000, note_end=7000, f0_hz=311.13)
    before = f0.copy()

    rd._extend_phrase_head_f0_for_preutterance(f0, [seg], [20], sr, frame_period_ms=5.0)

    assert np.array_equal(f0, before)


def test_extend_phrase_head_amp_and_f0_shiftless_path_is_bit_exact_noop() -> None:
    """`donor != 'ritsu'`（vocadito/pjs）経路は全ノート shift=0 のリストで
    呼ばれるため、R19 の修正（amp のフェード移設・f0 の後方延長追加）の
    影響を一切受けない（bit 一致・既存出力の非破壊）。"""
    sr = 24000
    segs = [
        _FakeSeg(start_sample=0, end_sample=2000, is_phrase_first=True),
        _FakeSeg(start_sample=2000, end_sample=5000, is_phrase_first=False),
    ]
    amp_env = _amp_env_with_phrase(10000, phrase_start=0, phrase_end=5000, arch_level=0.85, sr=sr)
    f0 = _f0_persample_with_note(10000, note_start=0, note_end=5000, f0_hz=220.0)
    zero_shifts = [0, 0]

    extended_amp = rd._extend_phrase_head_amp_for_preutterance(
        amp_env, segs, zero_shifts, sr, frame_period_ms=5.0,
    )
    extended_f0 = rd._extend_phrase_head_f0_for_preutterance(
        f0, segs, zero_shifts, sr, frame_period_ms=5.0,
    )

    assert extended_amp.tobytes() == amp_env.tobytes()
    assert extended_f0.tobytes() == f0.tobytes()


def test_extend_phrase_head_amp_does_not_mutate_input() -> None:
    sr = 24000
    seg = _FakeSeg(start_sample=5000, end_sample=7000, is_phrase_first=True)
    amp_env = _amp_env_with_phrase(10000, phrase_start=5000, phrase_end=7000, arch_level=0.9, sr=sr)
    before = amp_env.copy()

    rd._extend_phrase_head_amp_for_preutterance(amp_env, [seg], [20], sr, frame_period_ms=5.0)

    assert np.array_equal(amp_env, before)


def test_build_vcv_placements_stats_shift_per_note_matches_start_frame_shift() -> None:
    """`_build_vcv_placements` の `stats["shift_per_note"]` は各 note の実効
    shift（`cursor - start_frame`）と一致する（`_note_frame_track` の
    `origin_shift_frames` へそのまま渡せることの検算）。"""
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

    placements, resolved_list, stats = rd._build_vcv_placements(segs, selections, bank)

    assert stats["shift_per_note"] == [0, 15]
    breath_frames = 50
    naive_start_1 = breath_frames + resolved_list[0].n_frames
    assert placements[1].start_frame == naive_start_1 - stats["shift_per_note"][1]


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R14・`r3789636058`): 名目開始フレームは cursor 累積で
# なく seg.start_sample からの絶対換算（累積ドリフト回避）
# ---------------------------------------------------------------------------


def test_build_vcv_placements_nominal_start_matches_absolute_sample_rounding() -> None:
    """非整数フレーム長の拍（umi の 1 拍 = 16,364 サンプル @24kHz・5ms hop
    = 136.36... frame）を含むスコアで、各ノートの名目開始フレーム
    （`start_frame + shift_per_note`）が `round(seg.start_sample / hop)` と
    厳密に一致することを検証する（record 実測: 旧 cursor 累積方式は umi 終盤
    で −3 frame(15ms) ドリフトしていた）。"""
    sr = 24000
    beat = 16364  # umi score の 1 拍サンプル数（非整数 frame 長: 136.36...）
    n_notes = 8
    # 全ノート mid-phrase（is_phrase_first=False）にして preutterance shift を
    # 0 に保ち、名目位置そのものを直接検証する（shift の影響を分離）。
    segs = [_FakeSeg(i * beat, (i + 1) * beat, i == 0) for i in range(n_notes)]
    bank = _FakeBankVCV()
    units = [_vcv_unit(i, overlap_frames=4, preutterance_frames=0) for i in range(n_notes)]
    selections = [_FakeVCVSelection(u) for u in units]

    placements, _resolved_list, stats = rd._build_vcv_placements(segs, selections, bank)

    for i, seg in enumerate(segs):
        expected_nominal = rd.frames_for_samples(seg.start_sample, sr)
        actual_nominal = placements[i].start_frame + stats["shift_per_note"][i]
        assert actual_nominal == expected_nominal, f"note {i}: drift detected"


def test_build_vcv_placements_no_end_padding_drift_across_many_notes() -> None:
    """非整数フレーム長ビートが 20 ノート続いても、最終ノートの名目終端フレーム
    が score 総尺の絶対換算と一致し続ける（丸め誤差が伝播しない = 末尾パディング
    が生じない）ことを回帰的に確認する。"""
    sr = 24000
    beat = 16364
    n_notes = 20
    segs = [_FakeSeg(i * beat, (i + 1) * beat, False) for i in range(n_notes)]
    bank = _FakeBankVCV()
    units = [_vcv_unit(i, overlap_frames=4, preutterance_frames=0) for i in range(n_notes)]
    selections = [_FakeVCVSelection(u) for u in units]

    placements, _resolved_list, stats = rd._build_vcv_placements(segs, selections, bank)

    last_seg = segs[-1]
    expected_nominal_last = rd.frames_for_samples(last_seg.start_sample, sr)
    actual_nominal_last = placements[-1].start_frame + stats["shift_per_note"][-1]
    assert actual_nominal_last == expected_nominal_last


# ---------------------------------------------------------------------------
# P1 修正 (review #262 R2): spec.donor の provenance 照合（fail-closed）
# ---------------------------------------------------------------------------


def _spec_with_donor(donor: dict) -> vs.FoundryVoiceSpec:
    return vs.FoundryVoiceSpec(schema=vs.SCHEMA, donor=donor, warp={}, perf={}, seed=1)


def test_validate_spec_donor_vocadito_matching_hash_passes(tmp_path: Path) -> None:
    wav_path = tmp_path / "donor.wav"
    wav_path.write_bytes(b"not a real wav, only hashed as bytes")
    sha = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    spec = _spec_with_donor({"dataset": "vocadito", "clip": 2, "sha256": sha})
    result = rd._validate_spec_donor(spec, "vocadito", wav_path, None)
    assert result["dataset"] == "vocadito"
    assert result["actual_sha256"] == sha


def test_validate_spec_donor_vocadito_hash_mismatch_raises(tmp_path: Path) -> None:
    wav_path = tmp_path / "donor.wav"
    wav_path.write_bytes(b"actual donor bytes")
    spec = _spec_with_donor({"dataset": "vocadito", "clip": 2, "sha256": "0" * 64})
    with pytest.raises(rd.DonorProvenanceError):
        rd._validate_spec_donor(spec, "vocadito", wav_path, None)


def test_validate_spec_donor_dataset_mismatch_raises(tmp_path: Path) -> None:
    """spec.donor.dataset='vocadito' のまま --donor=ritsu で render しようとする
    ケース（review #262 R2 P1 の core scenario: 委員会 spec を無関係な
    --voicebank-root と組み合わせて provenance を偽装できた）。"""
    wav_path = tmp_path / "donor.wav"
    wav_path.write_bytes(b"x")
    sha = hashlib.sha256(wav_path.read_bytes()).hexdigest()
    spec = _spec_with_donor({"dataset": "vocadito", "clip": 2, "sha256": sha})
    with pytest.raises(rd.DonorProvenanceError):
        rd._validate_spec_donor(spec, "ritsu", None, tmp_path)


def _write_minimal_oto(pdir: Path, wav_filename: str = "_test.wav") -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "oto.ini").write_bytes(f"{wav_filename}=- あ{pdir.name},0,80,900,40,10\n".encode("cp932"))


def test_validate_spec_donor_ritsu_matching_hash_passes(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    _write_minimal_oto(root / "A3")
    _write_minimal_oto(root / "F4")
    actual_sha, _pitch_dirs = dbu.voicebank_identity_hash(root)
    spec = _spec_with_donor({"dataset": "ritsu", "voicebank_sha256": actual_sha})
    result = rd._validate_spec_donor(spec, "ritsu", None, root)
    assert result["dataset"] == "ritsu"
    assert result["actual_sha256"] == actual_sha


def test_validate_spec_donor_ritsu_hash_mismatch_raises(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    _write_minimal_oto(root / "A3")
    spec = _spec_with_donor({"dataset": "ritsu", "voicebank_sha256": "0" * 64})
    with pytest.raises(rd.DonorProvenanceError):
        rd._validate_spec_donor(spec, "ritsu", None, root)


def test_validate_spec_donor_ritsu_missing_hash_key_raises(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    _write_minimal_oto(root / "A3")
    spec = _spec_with_donor({"dataset": "ritsu"})  # voicebank_sha256 欠落
    with pytest.raises(rd.DonorProvenanceError):
        rd._validate_spec_donor(spec, "ritsu", None, root)


def _write_minimal_pjs_lab(root: Path, name: str) -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{name}.lab").write_text("0 1000000 pau\n1000000 2000000 a\n2000000 3000000 pau\n")


def test_validate_spec_donor_pjs_matching_hash_passes(tmp_path: Path) -> None:
    root = tmp_path / "pjs_corpus"
    _write_minimal_pjs_lab(root, "pjs001")
    _write_minimal_pjs_lab(root, "pjs002")
    actual_sha = dbl.corpus_identity_hash(root)
    spec = _spec_with_donor({"dataset": "pjs", "corpus_sha256": actual_sha})
    result = rd._validate_spec_donor(spec, "pjs", None, root)
    assert result["dataset"] == "pjs"
    assert result["actual_sha256"] == actual_sha


def test_validate_spec_donor_pjs_hash_mismatch_raises(tmp_path: Path) -> None:
    root = tmp_path / "pjs_corpus"
    _write_minimal_pjs_lab(root, "pjs001")
    spec = _spec_with_donor({"dataset": "pjs", "corpus_sha256": "f" * 64})
    with pytest.raises(rd.DonorProvenanceError):
        rd._validate_spec_donor(spec, "pjs", None, root)


# ---------------------------------------------------------------------------
# P1 修正 (review #262 R4・`r3789341845`): identity ハッシュへの WAV バイト包含
# ---------------------------------------------------------------------------


def _write_minimal_oto_with_wav(
    pdir: Path, wav_filename: str = "_test.wav", wav_bytes: bytes = b"WAVDATA"
) -> None:
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "oto.ini").write_bytes(f"{wav_filename}=- あ{pdir.name},0,80,900,40,10\n".encode("cp932"))
    (pdir / wav_filename).write_bytes(wav_bytes)


def test_voicebank_identity_hash_changes_when_wav_bytes_change(tmp_path: Path) -> None:
    """oto.ini を変えずに WAV バイトだけ差し替えても identity ハッシュが変わる
    ことを確認する（旧実装は oto.ini のみをハッシュしており検知できなかった
    ——review #262 R3 指摘の実害シナリオそのもの）。"""
    root = tmp_path / "voicebank"
    _write_minimal_oto_with_wav(root / "A3", wav_bytes=b"original wav bytes")
    sha_before, _ = dbu.voicebank_identity_hash(root)

    (root / "A3" / "_test.wav").write_bytes(b"replaced wav bytes!!")  # oto.ini 無変更
    sha_after, _ = dbu.voicebank_identity_hash(root)

    assert sha_before != sha_after


def test_voicebank_identity_hash_stable_when_nothing_changes(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    _write_minimal_oto_with_wav(root / "A3", wav_bytes=b"stable wav bytes")
    sha1, _ = dbu.voicebank_identity_hash(root)
    sha2, _ = dbu.voicebank_identity_hash(root)
    assert sha1 == sha2


def test_validate_spec_donor_ritsu_detects_wav_swap_with_unchanged_oto(tmp_path: Path) -> None:
    """実害シナリオ（review #262 R3 指摘 `r3789341845`）: oto.ini を無変更の
    まま WAV だけ差し替えても、旧実装（oto.ini のみハッシュ）は fail-closed
    検証を素通りしていた。新実装は WAV バイトも identity に含むため検出する。
    """
    root = tmp_path / "voicebank"
    _write_minimal_oto_with_wav(root / "A3", wav_bytes=b"pinned donor bytes")
    pinned_sha, _ = dbu.voicebank_identity_hash(root)
    spec = _spec_with_donor({"dataset": "ritsu", "voicebank_sha256": pinned_sha})
    rd._validate_spec_donor(spec, "ritsu", None, root)  # pin 時点では一致する

    (root / "A3" / "_test.wav").write_bytes(b"swapped donor bytes!")  # oto.ini 無変更
    with pytest.raises(rd.DonorProvenanceError):
        rd._validate_spec_donor(spec, "ritsu", None, root)


def _write_minimal_pjs_lab_with_wav(root: Path, name: str, wav_bytes: bytes = b"WAVDATA") -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{name}.lab").write_text("0 1000000 pau\n1000000 2000000 a\n2000000 3000000 pau\n")
    (pdir / f"{name}_song.wav").write_bytes(wav_bytes)


def test_corpus_identity_hash_changes_when_song_wav_bytes_change(tmp_path: Path) -> None:
    root = tmp_path / "pjs_corpus"
    _write_minimal_pjs_lab_with_wav(root, "pjs001", wav_bytes=b"original song bytes")
    sha_before = dbl.corpus_identity_hash(root)

    (root / "pjs001" / "pjs001_song.wav").write_bytes(b"replaced song bytes!!")  # .lab 無変更
    sha_after = dbl.corpus_identity_hash(root)

    assert sha_before != sha_after


def test_validate_spec_donor_pjs_detects_wav_swap_with_unchanged_lab(tmp_path: Path) -> None:
    root = tmp_path / "pjs_corpus"
    _write_minimal_pjs_lab_with_wav(root, "pjs001", wav_bytes=b"pinned song bytes")
    pinned_sha = dbl.corpus_identity_hash(root)
    spec = _spec_with_donor({"dataset": "pjs", "corpus_sha256": pinned_sha})
    rd._validate_spec_donor(spec, "pjs", None, root)  # pin 時点では一致する

    (root / "pjs001" / "pjs001_song.wav").write_bytes(b"swapped song bytes!")  # .lab 無変更
    with pytest.raises(rd.DonorProvenanceError):
        rd._validate_spec_donor(spec, "pjs", None, root)


def test_validate_spec_donor_committed_presets_match_real_assets_if_present() -> None:
    """presets/{neutral,warped,ritsu_neutral,pjs_neutral}.json の donor 宣言は
    それぞれが依拠するデータセットの pin。ここではスキーマ形状のみ検証する
    （実データ（vocadito/ritsu/pjs）は非コミット・実体照合は環境依存のため
    対象外 — 実体照合ロジック自体は上記の合成 fixture テストで検証済み）。
    """
    presets_dir = Path(__file__).resolve().parent.parent / "adapter" / "presets"
    expectations = {
        "neutral.json": ("vocadito", "sha256"),
        "warped.json": ("vocadito", "sha256"),
        "ritsu_neutral.json": ("ritsu", "voicebank_sha256"),
        "pjs_neutral.json": ("pjs", "corpus_sha256"),
    }
    for filename, (dataset, hash_key) in expectations.items():
        spec = vs.load_voice_spec(presets_dir / filename)
        assert spec.donor["dataset"] == dataset
        assert isinstance(spec.donor[hash_key], str) and len(spec.donor[hash_key]) == 64


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R6・`r3789428504`): bank 構築完了後の post-build
# revalidation（`_validate_spec_donor` 単体の TOCTOU 検知力は上の swap テスト
# 群で確認済み。ここでは render() が実際に bank 構築後にもう一度呼ぶ配線を
# 直接検証する）。
# ---------------------------------------------------------------------------


def test_render_ritsu_revalidates_donor_provenance_after_bank_build(
    tmp_path: Path, monkeypatch
) -> None:
    """`_validate_spec_donor` の初回呼び出し（重い WORLD 分析より前の
    fail-fast）と実際の bank 構築 read の間に voicebank が書き換わる TOCTOU
    窓を、post-build revalidation が検出することを render() 全体の配線として
    確認する。

    `dbu.build_donor_bank_utau_vcv`（実際の bank 構築本体）を軽量スタブへ
    差し替え、その副作用として oto.ini を書き換える（bank 構築「中」に
    voicebank が変化する状況を模す）。post-build revalidation が配線されて
    いなければ、この変化は検出されないまま render() がそのまま（この後の
    重い WORLD 合成へ）進んでしまう。
    """
    root = tmp_path / "voicebank"
    _write_minimal_oto(root / "A3")
    pinned_sha, _pitch_dirs = dbu.voicebank_identity_hash(root)
    spec = _spec_with_donor({"dataset": "ritsu", "voicebank_sha256": pinned_sha})
    spec_path = tmp_path / "spec.json"
    vs.save_voice_spec(spec, spec_path)

    def _tampering_build_stub(voicebank_root, cache_dir=None, required_contexts=None):
        oto_path = Path(voicebank_root) / "A3" / "oto.ini"
        oto_path.write_bytes(oto_path.read_bytes() + b"tampered=x,0,0,0,0,0\n")
        return None, {}, {}  # bank/unit_contexts/donor_extra_stats（値は未使用まで到達しない）

    monkeypatch.setattr(rd.dbu, "build_donor_bank_utau_vcv", _tampering_build_stub)

    with pytest.raises(rd.DonorProvenanceError):
        rd.render("sakura", spec_path, donor="ritsu", voicebank_root=root)


def test_render_ritsu_no_revalidation_error_when_voicebank_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    """対照ケース: bank 構築中に voicebank が変化しなければ post-build
    revalidation は通過する（誤検知しないことの確認）。build スタブは
    tamper なしで即座に戻り、その直後（`db.normalize_unit_energy` 呼び出し
    直前）に配線された revalidation を通過した先で sentinel 例外を送出する
    `normalize_unit_energy` スタブに到達することで、間に挟まる revalidation
    が `DonorProvenanceError` を出していないことを直接確認する。
    """
    root = tmp_path / "voicebank"
    _write_minimal_oto(root / "A3")
    pinned_sha, _pitch_dirs = dbu.voicebank_identity_hash(root)
    spec = _spec_with_donor({"dataset": "ritsu", "voicebank_sha256": pinned_sha})
    spec_path = tmp_path / "spec.json"
    vs.save_voice_spec(spec, spec_path)

    class _ReachedPastRevalidation(Exception):
        pass

    def _untampered_build_stub(voicebank_root, cache_dir=None, required_contexts=None):
        return None, {}, {}  # tamper なし

    def _sentinel_normalize(bank):
        raise _ReachedPastRevalidation("normalize_unit_energy reached without DonorProvenanceError")

    monkeypatch.setattr(rd.dbu, "build_donor_bank_utau_vcv", _untampered_build_stub)
    monkeypatch.setattr(rd.db, "normalize_unit_energy", _sentinel_normalize)

    with pytest.raises(_ReachedPastRevalidation):
        rd.render("sakura", spec_path, donor="ritsu", voicebank_root=root)


# ---------------------------------------------------------------------------
# P1 修正 (review #262 R2): --out の保護入力衝突拒否（fail-closed）
# ---------------------------------------------------------------------------


def test_reject_output_collision_out_equals_wav_path_raises(tmp_path: Path) -> None:
    wav_path = tmp_path / "donor.wav"
    wav_path.write_bytes(b"x")
    with pytest.raises(rd.OutputCollisionError):
        rd._reject_output_collision(wav_path, protected_files=[wav_path], protected_roots=[])


def test_reject_output_collision_out_equals_voice_spec_path_raises(tmp_path: Path) -> None:
    spec_path = tmp_path / "voice.json"
    spec_path.write_text("{}")
    with pytest.raises(rd.OutputCollisionError):
        rd._reject_output_collision(spec_path, protected_files=[None, spec_path, None], protected_roots=[])


def test_reject_output_collision_out_inside_voicebank_root_raises(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    root.mkdir()
    out_path = root / "A3" / "clobbered.wav"
    with pytest.raises(rd.OutputCollisionError):
        rd._reject_output_collision(out_path, protected_files=[], protected_roots=[root])


def test_reject_output_collision_unrelated_path_does_not_raise(tmp_path: Path) -> None:
    wav_path = tmp_path / "donor.wav"
    wav_path.write_bytes(b"x")
    root = tmp_path / "voicebank"
    root.mkdir()
    out_path = tmp_path / "out" / "result.wav"
    rd._reject_output_collision(out_path, protected_files=[wav_path], protected_roots=[root])  # ok: no raise


def test_reject_output_collision_symlinked_out_resolves_before_compare(tmp_path: Path) -> None:
    """resolved containment（symlink 解決後）で判定する（AGENTS.md Persistent
    Artifact Safety Gate 項目2）。"""
    real_wav = tmp_path / "real_donor.wav"
    real_wav.write_bytes(b"x")
    alias = tmp_path / "alias.wav"
    alias.symlink_to(real_wav)
    with pytest.raises(rd.OutputCollisionError):
        rd._reject_output_collision(alias, protected_files=[real_wav], protected_roots=[])


def test_reject_output_collision_none_paths_are_skipped(tmp_path: Path) -> None:
    out_path = tmp_path / "out.wav"
    rd._reject_output_collision(out_path, protected_files=[None, None], protected_roots=[None])  # no raise


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R11・`r3789543157`): --cache-dir を衝突ガード対象へ追加
# ---------------------------------------------------------------------------


def test_reject_output_collision_out_inside_cache_dir_raises(tmp_path: Path) -> None:
    """--out が --cache-dir 配下（donor_bank_*.npz/utau_bank_*.pkl/lab_bank_*.pkl
    が生成される場所）を指す場合、cache-hit render がキャッシュを WAV バイト列で
    atomic 置換してしまう前に fail-closed で拒否する。"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    out_path = cache_dir / "donor_bank_vocadito_abc123.npz"
    with pytest.raises(rd.OutputCollisionError):
        rd._reject_output_collision(out_path, protected_files=[], protected_roots=[cache_dir])


def test_reject_output_collision_out_equals_existing_cache_file_raises(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "utau_bank_deadbeef.pkl"
    cache_file.write_bytes(b"cached")
    with pytest.raises(rd.OutputCollisionError):
        rd._reject_output_collision(cache_file, protected_files=[], protected_roots=[cache_dir])


def test_reject_output_collision_cache_dir_none_is_skipped(tmp_path: Path) -> None:
    """--cache-dir 省略時（None）は他の未使用 optional root と同様にスキップされ、
    誤検知しない（`render()` の既定引数 `cache_dir=None` と同じ形）。"""
    out_path = tmp_path / "out" / "result.wav"
    rd._reject_output_collision(out_path, protected_files=[], protected_roots=[None])  # no raise


def test_render_reject_output_collision_call_includes_cache_dir_root() -> None:
    """`render()` が `_reject_output_collision` の `protected_roots` へ
    `cache_dir` を実際に配線していることのソースレベル回帰ガード。
    （完走 E2E は WORLD 分析込みで重く、この配線 1 行の検証には不釣り合いなため、
    直接呼び出しテスト（上記）で衝突判定ロジック自体は検証済みという前提で、
    呼び出し引数の配線のみをここで確認する。)"""
    import inspect

    src = inspect.getsource(rd.render)
    assert "protected_roots=[voicebank_root, cache_dir]" in src


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R2): WAV 出力の atomic 公開
# ---------------------------------------------------------------------------


def test_atomic_write_wav_writes_readable_content(tmp_path: Path) -> None:
    out_path = tmp_path / "out.wav"
    y = np.linspace(-0.5, 0.5, 240).astype(np.float64)
    rd._atomic_write_wav(y, 24000, out_path)
    assert out_path.exists()
    y_read, sr_read = sf.read(str(out_path))
    assert sr_read == 24000
    assert len(y_read) == 240
    # tempfile が残っていない（staging cleanup 確認）。
    leftovers = list(tmp_path.glob("out.wav.*.tmp"))
    assert leftovers == []


def test_atomic_write_wav_returns_digest_matching_published_bytes(tmp_path: Path) -> None:
    """P2 修正 (review #262 R8・`r3789486148`): 戻り値の `output_sha256` が
    実際に公開された WAV ファイルの実測 sha256 と一致すること（入力=ドナー
    ハッシュだけでなく出力バイト列のハッシュも記録する AGENTS.md 要件）。"""
    out_path = tmp_path / "out.wav"
    y = np.linspace(-0.5, 0.5, 240).astype(np.float64)
    output_sha256 = rd._atomic_write_wav(y, 24000, out_path)
    assert output_sha256 == hashlib.sha256(out_path.read_bytes()).hexdigest()


def test_atomic_write_wav_no_partial_file_on_failure(tmp_path: Path, monkeypatch) -> None:
    """AGENTS.md Persistent Artifact Safety Gate 項目7「公開途中失敗の注入
    テスト」: `sf.write` が失敗しても最終 out_path に部分成果物を残さない。"""
    out_path = tmp_path / "out.wav"

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(rd.sf, "write", _boom)
    y = np.zeros(10)
    with pytest.raises(RuntimeError):
        rd._atomic_write_wav(y, 24000, out_path)
    assert not out_path.exists()
    assert list(tmp_path.glob("out.wav.*.tmp")) == []


def test_atomic_write_wav_does_not_clobber_existing_output_on_failure(tmp_path: Path, monkeypatch) -> None:
    """公開直前まで既存の有効な出力を保持し、失敗時は破壊しない
    （staging + os.replace の atomicity）。"""
    out_path = tmp_path / "out.wav"
    rd._atomic_write_wav(np.zeros(10), 24000, out_path)
    before_bytes = out_path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(rd.sf, "write", _boom)
    with pytest.raises(RuntimeError):
        rd._atomic_write_wav(np.ones(10), 24000, out_path)
    assert out_path.read_bytes() == before_bytes  # 旧ファイルが無傷のまま残る


# review #262 R9 (`r3789495255`): donor="ritsu" は VCV 録音遷移が不可分のため
# consonant_source="none"/"synthetic"（および --no-consonants の "none" 翻訳）は
# 重い bank 分析（WORLD 解析・oto.ini 全数走査）より前に fail-closed で拒否する。
# 新規チェックは `spec = vs.load_voice_spec(...)` より前に位置するため、
# voice_spec_path に実在しないダミーパスを渡しても ValueError が先に飛ぶ
# （=「重い分析より前」であることをテスト自体が証明する）。


def test_render_ritsu_consonant_source_none_raises_before_heavy_analysis() -> None:
    with pytest.raises(ValueError, match="consonant_source"):
        rd.render(
            "sakura", "/nonexistent/voice_spec.json",
            donor="ritsu", consonant_source="none", voicebank_root="/nonexistent/voicebank",
        )


def test_render_ritsu_consonant_source_synthetic_raises_before_heavy_analysis() -> None:
    with pytest.raises(ValueError, match="consonant_source"):
        rd.render(
            "sakura", "/nonexistent/voice_spec.json",
            donor="ritsu", consonant_source="synthetic", voicebank_root="/nonexistent/voicebank",
        )


def test_render_ritsu_no_consonants_flag_raises_before_heavy_analysis() -> None:
    with pytest.raises(ValueError, match="consonant_source"):
        rd.render(
            "sakura", "/nonexistent/voice_spec.json",
            donor="ritsu", apply_consonants=False, voicebank_root="/nonexistent/voicebank",
        )


def test_render_ritsu_consonant_source_recorded_does_not_raise_at_this_gate() -> None:
    """明示的に 'recorded'（＝ VCV 既定）を指定した場合は本ゲートを通過する
    （後続の spec ファイル読み込みで FileNotFoundError になることまでは許容・
    本ゲートの対象外であることのみを確認する）。"""
    with pytest.raises(FileNotFoundError):
        rd.render(
            "sakura", "/nonexistent/voice_spec.json",
            donor="ritsu", consonant_source="recorded", voicebank_root="/nonexistent/voicebank",
        )


def test_render_ritsu_consonant_source_default_none_does_not_raise_at_this_gate() -> None:
    """`consonant_source` 省略時は donor='ritsu' で 'recorded' へ既定解決される
    （render.py 冒頭）ため、本ゲートには抵触しない。"""
    with pytest.raises(FileNotFoundError):
        rd.render(
            "sakura", "/nonexistent/voice_spec.json",
            donor="ritsu", voicebank_root="/nonexistent/voicebank",
        )


# ---------------------------------------------------------------------------
# P2 修正 (review #265 R5・有限性ファミリー終端掃討): unit 選択コストの重み
# w_p/w_d/w_c/w_v は有限・非負を要求する（重い WORLD 分析より前）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), -0.5])
@pytest.mark.parametrize("kwarg_name", ["w_p", "w_d", "w_c", "w_v"])
def test_render_rejects_non_finite_or_negative_weight_before_heavy_analysis(
    kwarg_name: str, bad_value: float
) -> None:
    """`w_p`/`w_d`/`w_c`/`w_v` は `units.py` の `total = w_p * cp + w_d * cd +
    w_c * cc + cv` に使われる — `nan` は全候補の `total` を `nan` にして
    選択を無効化し、`inf`/負値も無意味な入力のため、重い分析（spec 読み込み
    より前）で fail-closed 拒否する。voice_spec_path に実在しないダミー
    パスを渡しても本ゲートの ValueError が先に飛ぶことで「重い分析より
    前」であることも合わせて検証する。"""
    kwargs = {"w_p": 1.0, "w_d": 0.3, "w_c": 1.0, "w_v": 25.0}
    kwargs[kwarg_name] = bad_value
    with pytest.raises(ValueError, match=kwarg_name):
        rd.render("sakura", "/nonexistent/voice_spec.json", **kwargs)


def test_render_default_weights_do_not_raise_at_this_gate() -> None:
    """既定値（`un.DEFAULT_W_P` 等）はいずれも有限・非負のため本ゲートに
    抵触せず、後続の spec 読み込みまで到達する（誤検知しないことの対照
    テスト）。"""
    with pytest.raises(FileNotFoundError):
        rd.render("sakura", "/nonexistent/voice_spec.json")


# ---------------------------------------------------------------------------
# P1 修正 (review #265): --timing-out の衝突検査を合成前に fail-closed で行う
# ---------------------------------------------------------------------------


def _ritsu_spec_and_voicebank(tmp_path: Path) -> Tuple[Path, Path]:
    """`donor='ritsu'` の最小 spec + voicebank を組み立て、`(spec_path, root)`
    を返す（`test_render_ritsu_revalidates_donor_provenance_after_bank_build`
    と同型のヘルパー）。"""
    root = tmp_path / "voicebank"
    _write_minimal_oto(root / "A3")
    pinned_sha, _pitch_dirs = dbu.voicebank_identity_hash(root)
    spec = _spec_with_donor({"dataset": "ritsu", "voicebank_sha256": pinned_sha})
    spec_path = tmp_path / "spec.json"
    vs.save_voice_spec(spec, spec_path)
    return spec_path, root


def test_render_timing_out_equals_out_raises_before_build_score(tmp_path: Path, monkeypatch) -> None:
    """`--timing-out` が `--out` と一致する呼び出しは、`build_score` 以降の
    重い合成パイプラインへ入る前に fail-closed で拒否される（`--out` は
    まだファイルとして存在しない通常ケースでも検出できることが重要 ——
    `_reject_output_collision` の `protected_files` は「既存の入力ファイル」
    前提で `.exists()` スキップするため、それをそのまま `--out` へ流用すると
    見逃す。build_score をセンチネル化し、「合成前」の位置であることも
    合わせて検証する）。"""
    spec_path, root = _ritsu_spec_and_voicebank(tmp_path)
    out_path = tmp_path / "out.wav"
    assert not out_path.exists()  # 通常ケース: --out はまだ存在しない

    def _sentinel_build_score(_name):
        raise AssertionError(
            "build_score reached — timing-out collision check did not fail before synthesis"
        )

    monkeypatch.setattr(rd, "build_score", _sentinel_build_score)

    with pytest.raises(rd.OutputCollisionError, match="timing-out"):
        rd.render(
            "sakura", spec_path, donor="ritsu", voicebank_root=root,
            out_path=out_path, timing_out_path=out_path,
        )


def test_render_timing_out_inside_protected_root_raises_before_build_score(
    tmp_path: Path, monkeypatch
) -> None:
    """`--timing-out` が保護入力（`--voicebank-root` 配下）と一致する場合も
    合成前に fail-closed で拒否される。"""
    spec_path, root = _ritsu_spec_and_voicebank(tmp_path)
    out_path = tmp_path / "out.wav"
    timing_out_path = root / "A3" / "oto.ini"  # voicebank_root 配下に衝突させる

    def _sentinel_build_score(_name):
        raise AssertionError(
            "build_score reached — timing-out collision check did not fail before synthesis"
        )

    monkeypatch.setattr(rd, "build_score", _sentinel_build_score)

    with pytest.raises(rd.OutputCollisionError):
        rd.render(
            "sakura", spec_path, donor="ritsu", voicebank_root=root,
            out_path=out_path, timing_out_path=timing_out_path,
        )


def test_render_timing_out_none_skips_collision_check_and_reaches_build_score(
    tmp_path: Path, monkeypatch
) -> None:
    """`timing_out_path` 省略時（`None`）はこのゲートに抵触せず、従来どおり
    `build_score` まで到達する（新規ゲートが無関係な呼び出しを誤検知しない
    ことの対照テスト）。"""
    spec_path, root = _ritsu_spec_and_voicebank(tmp_path)
    out_path = tmp_path / "out.wav"

    class _ReachedBuildScore(Exception):
        pass

    def _sentinel_build_score(_name):
        raise _ReachedBuildScore("build_score reached as expected")

    monkeypatch.setattr(rd, "build_score", _sentinel_build_score)

    with pytest.raises(_ReachedBuildScore):
        rd.render("sakura", spec_path, donor="ritsu", voicebank_root=root, out_path=out_path)


# ---------------------------------------------------------------------------
# P2 修正 (review #265 R7): timing_out_path の単独指定（out_path=None）は
# 合成のみ行い何も書かずに正常終了する false-success だったため、
# out_path 必須（同時指定のみ許可）を fail-closed で強制する
# ---------------------------------------------------------------------------


def test_render_timing_out_alone_without_out_raises_before_build_score(
    tmp_path: Path, monkeypatch
) -> None:
    """`timing_out_path` のみ指定・`out_path=None` は、合成だけ行って WAV も
    timing CSV も書かずに正常終了する false-success だった（§ 後段の公開
    ブロックが `if out_path is not None:` の中にしか無く丸ごとスキップ
    されるため）。重い合成（`build_score`）へ入る前に即座に拒否する。"""
    spec_path, root = _ritsu_spec_and_voicebank(tmp_path)
    timing_out_path = tmp_path / "out_timing.csv"

    def _sentinel_build_score(_name):
        raise AssertionError(
            "build_score reached — timing_out_path-without-out_path check did not "
            "fail before synthesis"
        )

    monkeypatch.setattr(rd, "build_score", _sentinel_build_score)

    with pytest.raises(ValueError, match="timing_out_path"):
        rd.render(
            "sakura", spec_path, donor="ritsu", voicebank_root=root,
            timing_out_path=timing_out_path,
        )
    assert not timing_out_path.exists()


def test_render_out_alone_without_timing_out_does_not_raise_at_this_gate() -> None:
    """`out_path` のみ指定・`timing_out_path` 省略は既存の正常系（WAV 単独
    公開）のため、本ゲートには抵触しない（誤検知しないことの対照テスト）。"""
    with pytest.raises(FileNotFoundError):
        rd.render(
            "sakura", "/nonexistent/voice_spec.json",
            donor="ritsu", voicebank_root="/nonexistent/voicebank",
            out_path="/nonexistent/out_dir/out.wav",
        )


# ---------------------------------------------------------------------------
# P1 修正 (review #265): WAV + timing CSV の両方 staging → 両方公開
# （非対称な部分公開の解消）
# ---------------------------------------------------------------------------


def test_atomic_write_wav_and_timing_publishes_both_files(tmp_path: Path) -> None:
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    y = np.linspace(-0.5, 0.5, 240).astype(np.float64)
    rows = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rows[0]["row_index"] = "0"
    rows[0]["row_kind"] = "rest"

    output_sha256 = rd._atomic_write_wav_and_timing(y, 24000, out_path, rows, timing_out_path)

    assert out_path.exists()
    assert timing_out_path.exists()
    assert output_sha256 == hashlib.sha256(out_path.read_bytes()).hexdigest()
    with open(timing_out_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        written_rows = list(reader)
    assert written_rows == rows
    # staging tempfile が残っていない。
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_wav_and_timing_no_wav_publish_when_csv_staging_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """P1 修正 (review #265) の核心: timing CSV の staging（書き込み）が失敗
    したら、`--out` の WAV も公開されない（旧実装は WAV を先に公開してから
    別途 CSV を書いていたため、CSV 失敗時に WAV だけが残る非対称があった）。"""
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    y = np.linspace(-0.5, 0.5, 240).astype(np.float64)
    rows = [{k: "" for k in rd.TIMING_CSV_FIELDS}]

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated timing csv write failure")

    monkeypatch.setattr(rd, "_stage_timing_csv_bytes", _boom)

    with pytest.raises(RuntimeError):
        rd._atomic_write_wav_and_timing(y, 24000, out_path, rows, timing_out_path)

    assert not out_path.exists()  # WAV も非公開のまま（非対称解消の確認）
    assert not timing_out_path.exists()
    assert list(tmp_path.glob("*.tmp")) == []  # staging tempfile も残らない


def test_atomic_write_wav_and_timing_does_not_clobber_existing_files_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """既存の有効な WAV/CSV がある状態で、CSV staging が失敗しても両方とも
    無傷のまま残る。"""
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    rows = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rd._atomic_write_wav_and_timing(np.zeros(10), 24000, out_path, rows, timing_out_path)
    wav_before = out_path.read_bytes()
    csv_before = timing_out_path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated timing csv write failure")

    monkeypatch.setattr(rd, "_stage_timing_csv_bytes", _boom)
    with pytest.raises(RuntimeError):
        rd._atomic_write_wav_and_timing(np.ones(10), 24000, out_path, rows, timing_out_path)

    assert out_path.read_bytes() == wav_before
    assert timing_out_path.read_bytes() == csv_before


def test_atomic_write_wav_and_timing_rolls_back_both_when_second_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """P2 修正 (review #265 R3): 1 個目 (WAV) の `os.replace` が成功した直後に
    2 個目 (timing CSV) が失敗すると、旧実装は WAV だけが新世代へ差し替わった
    不整合状態を残していた。既存宛先のスナップショット（同一ディレクトリへの
    退避 rename）を取ってから両方公開し、失敗時は両方とも巻き戻す実装により、
    WAV/CSV 双方とも呼び出し前のバイト列へ完全に戻ることを確認する。"""
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    rows_v1 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rows_v1[0]["row_index"] = "0"
    # 旧世代を公開しておく。
    rd._atomic_write_wav_and_timing(np.zeros(10), 24000, out_path, rows_v1, timing_out_path)
    wav_before = out_path.read_bytes()
    csv_before = timing_out_path.read_bytes()

    real_replace = rd.os.replace
    call_count = {"n": 0}

    def _replace_second_call_fails(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on the second os.replace (timing csv)")
        return real_replace(src, dst)

    monkeypatch.setattr(rd.os, "replace", _replace_second_call_fails)

    rows_v2 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rows_v2[0]["row_index"] = "1"
    with pytest.raises(RuntimeError, match="second os.replace"):
        rd._atomic_write_wav_and_timing(np.ones(10), 24000, out_path, rows_v2, timing_out_path)

    # 両方とも呼び出し前のバイト列へ完全に戻る（WAV だけが新しくなる非対称の
    # 解消がこのテストの核心）。
    assert out_path.read_bytes() == wav_before
    assert timing_out_path.read_bytes() == csv_before
    # 退避 (.bak) / staging (.tmp) の痕跡が残らない。
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_wav_and_timing_rolls_back_both_when_first_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """対称ケース: 1 個目 (WAV) の `os.replace` が失敗しても、2 個目 (CSV) は
    未着手のまま・既存の旧世代は無傷で残る。"""
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    rows_v1 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rd._atomic_write_wav_and_timing(np.zeros(10), 24000, out_path, rows_v1, timing_out_path)
    wav_before = out_path.read_bytes()
    csv_before = timing_out_path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure on the first os.replace (wav)")

    monkeypatch.setattr(rd.os, "replace", _boom)

    rows_v2 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    with pytest.raises(RuntimeError, match="first os.replace"):
        rd._atomic_write_wav_and_timing(np.ones(10), 24000, out_path, rows_v2, timing_out_path)

    assert out_path.read_bytes() == wav_before
    assert timing_out_path.read_bytes() == csv_before
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_wav_and_timing_first_publish_rolls_back_to_absence_on_second_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """既存の宛先が無い（初回公開）状態で 2 個目が失敗した場合、WAV も
    公開されず、双方とも「未公開」状態（呼び出し前と同じ = 存在しない）へ
    戻る（`wav_had_previous=False` 経路の巻き戻し確認）。"""
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    assert not out_path.exists()
    assert not timing_out_path.exists()

    real_replace = rd.os.replace
    call_count = {"n": 0}

    def _replace_second_call_fails(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on the second os.replace")
        return real_replace(src, dst)

    monkeypatch.setattr(rd.os, "replace", _replace_second_call_fails)

    rows = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    with pytest.raises(RuntimeError):
        rd._atomic_write_wav_and_timing(np.ones(10), 24000, out_path, rows, timing_out_path)

    assert not out_path.exists()
    assert not timing_out_path.exists()
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# P2 修正 (review #265 R5): 退避 rename フェーズ自体もロールバック対象に
# 含める（R3 時点では退避 rename が BaseException ハンドラの外にあり、
# 1 個目の退避成功後・2 個目の退避失敗で正 WAV が退避先へ移動したまま
# 復元されず終わる退行があった）
# ---------------------------------------------------------------------------


def test_atomic_write_wav_and_timing_restores_wav_when_second_backup_rename_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """R5 が修正した核心シナリオ: 1 個目 (WAV) の退避 rename が成功した直後に
    2 個目 (timing CSV) の退避 rename が失敗しても、WAV は退避先 (`.bak`) に
    取り残されず、元のパス・元のバイト列へ完全に戻る（CSV も未着手のまま
    元のバイト列で残る）。"""
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    rows_v1 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rows_v1[0]["row_index"] = "0"
    rd._atomic_write_wav_and_timing(np.zeros(10), 24000, out_path, rows_v1, timing_out_path)
    wav_before = out_path.read_bytes()
    csv_before = timing_out_path.read_bytes()

    real_rename = rd.os.rename
    call_count = {"n": 0}

    def _rename_second_call_fails(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated failure on the second backup rename (timing csv)")
        return real_rename(src, dst)

    monkeypatch.setattr(rd.os, "rename", _rename_second_call_fails)

    rows_v2 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rows_v2[0]["row_index"] = "1"
    with pytest.raises(RuntimeError, match="second backup rename"):
        rd._atomic_write_wav_and_timing(np.ones(10), 24000, out_path, rows_v2, timing_out_path)

    # R5 以前はここで out_path が存在しなかった（退避先に取り残されたまま）。
    assert out_path.exists()
    assert out_path.read_bytes() == wav_before
    assert timing_out_path.exists()
    assert timing_out_path.read_bytes() == csv_before
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_wav_and_timing_restores_csv_when_wav_backup_rename_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """対称ケース: 1 個目 (WAV) の退避 rename そのものが失敗した場合、CSV は
    未着手のまま・WAV も元のバイト列のまま残る（rename は原子的なので部分
    バイト列は生じない）。"""
    out_path = tmp_path / "out.wav"
    timing_out_path = tmp_path / "out_timing.csv"
    rows_v1 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rd._atomic_write_wav_and_timing(np.zeros(10), 24000, out_path, rows_v1, timing_out_path)
    wav_before = out_path.read_bytes()
    csv_before = timing_out_path.read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated failure on the first backup rename (wav)")

    monkeypatch.setattr(rd.os, "rename", _boom)

    rows_v2 = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    with pytest.raises(RuntimeError, match="first backup rename"):
        rd._atomic_write_wav_and_timing(np.ones(10), 24000, out_path, rows_v2, timing_out_path)

    assert out_path.read_bytes() == wav_before
    assert timing_out_path.read_bytes() == csv_before
    assert list(tmp_path.glob("*.bak")) == []
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_wav_and_timing_matches_atomic_write_wav_bytes(tmp_path: Path) -> None:
    """timing CSV 経由の公開でも、WAV バイト列そのものは単独経路
    (`_atomic_write_wav`) と完全一致する（合成結果に一切影響しないことの
    直接的な裏付け）。"""
    y = np.linspace(-0.3, 0.7, 480).astype(np.float64)
    solo_path = tmp_path / "solo.wav"
    rd._atomic_write_wav(y, 24000, solo_path)

    combo_path = tmp_path / "combo.wav"
    timing_out_path = tmp_path / "combo_timing.csv"
    rows = [{k: "" for k in rd.TIMING_CSV_FIELDS}]
    rd._atomic_write_wav_and_timing(y, 24000, combo_path, rows, timing_out_path)

    assert solo_path.read_bytes() == combo_path.read_bytes()
