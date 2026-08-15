"""test_adapter_render_donor_wiring.py — 追補 F1.2-D 配線の単体検証。
録音済み子音前置の決定論・母音分布ヘルパーを合成 fixture で検証する
（実 render パイプライン全体（WORLD 合成込み）は非依存・軽量）。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest
import soundfile as sf

import donor_bank_lab as dbl
import donor_bank_utau as dbu
import render as rd
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
