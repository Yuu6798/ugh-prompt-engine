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
