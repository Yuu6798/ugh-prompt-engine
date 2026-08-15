"""test_adapter_donor_bank_lab.py — VG-F1.2-C PJS .lab バンクローダーの検証。
合成 fixture（.lab テキスト + 短い合成 wav）で高速・実 PJS 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest
import soundfile as sf

import donor_bank_lab as dbl


# --- 純関数（音声非依存） ---


def test_parse_lab_converts_100ns_units(tmp_path: Path) -> None:
    p = tmp_path / "x.lab"
    p.write_text("0 1000000 pau\n1000000 2000000 a\n")
    phonemes = dbl.parse_lab(p)
    assert len(phonemes) == 2
    assert phonemes[0].start_s == pytest.approx(0.0)
    assert phonemes[0].end_s == pytest.approx(0.1)
    assert phonemes[1].phoneme == "a"


def test_group_lab_to_morae_consonant_vowel() -> None:
    phonemes = [
        dbl.LabPhoneme(0.0, 0.5, "pau"),
        dbl.LabPhoneme(0.5, 0.6, "k"),
        dbl.LabPhoneme(0.6, 1.0, "a"),
        dbl.LabPhoneme(1.0, 1.5, "pau"),
    ]
    morae, stats = dbl.group_lab_to_morae(phonemes)
    assert len(morae) == 1
    m = morae[0]
    assert m.onset == "k"
    assert m.vowel == "a"
    assert m.onset_start_s == pytest.approx(0.5)
    assert m.onset_end_s == pytest.approx(0.6)
    assert m.vowel_start_s == pytest.approx(0.6)
    assert m.vowel_end_s == pytest.approx(1.0)


def test_group_lab_to_morae_vowel_only() -> None:
    phonemes = [dbl.LabPhoneme(0.0, 0.4, "o")]
    morae, _stats = dbl.group_lab_to_morae(phonemes)
    assert len(morae) == 1
    assert morae[0].onset is None
    assert morae[0].vowel == "o"


def test_group_lab_to_morae_moraic_nasal() -> None:
    phonemes = [dbl.LabPhoneme(0.0, 0.3, "N")]
    morae, _stats = dbl.group_lab_to_morae(phonemes)
    assert len(morae) == 1
    assert morae[0].onset is None
    assert morae[0].vowel == "N"


def test_group_lab_to_morae_pau_resets_pending_consonant() -> None:
    # 子音の直後に無音が来て母音へ接続しない場合、その子音は破棄される
    # （n_dangling_onset_dropped に計上・次の母音には結合しない）。
    phonemes = [
        dbl.LabPhoneme(0.0, 0.1, "s"),
        dbl.LabPhoneme(0.1, 0.3, "pau"),
        dbl.LabPhoneme(0.3, 0.6, "a"),
    ]
    morae, stats = dbl.group_lab_to_morae(phonemes)
    assert len(morae) == 1
    assert morae[0].onset is None  # pau で子音バッファがリセットされたため
    assert stats["n_dangling_onset_dropped"] == 1


def test_group_lab_to_morae_cl_before_consonant() -> None:
    # 促音（cl）は直後の実子音バッファへ吸収され、onset ラベルには使われない。
    phonemes = [
        dbl.LabPhoneme(0.0, 0.05, "cl"),
        dbl.LabPhoneme(0.05, 0.15, "t"),
        dbl.LabPhoneme(0.15, 0.5, "a"),
    ]
    morae, stats = dbl.group_lab_to_morae(phonemes)
    assert len(morae) == 1
    assert morae[0].onset == "t"
    assert morae[0].onset_start_s == pytest.approx(0.0)  # cl の開始位置を保持
    assert stats["n_cl_seen"] == 1


def test_compute_octave_transpose_up_one_octave() -> None:
    # donor 110Hz, target ~220Hz -> +12 半音
    semitones = dbl.compute_octave_transpose(donor_median_hz=110.0, target_median_hz=210.0)
    assert semitones == 12


def test_compute_octave_transpose_down_one_octave() -> None:
    semitones = dbl.compute_octave_transpose(donor_median_hz=440.0, target_median_hz=230.0)
    assert semitones == -12


def test_compute_octave_transpose_same_register() -> None:
    semitones = dbl.compute_octave_transpose(donor_median_hz=220.0, target_median_hz=225.0)
    assert semitones == 0


def test_compute_octave_transpose_zero_guard() -> None:
    assert dbl.compute_octave_transpose(0.0, 220.0) == 0
    assert dbl.compute_octave_transpose(220.0, 0.0) == 0


def test_select_lab_files_covers_onsets_and_vowels(tmp_path: Path) -> None:
    # ファイルごとに異なる音素トークンを持つ .lab を用意し、貪欲被覆を確認する。
    specs = {
        "pjs001": ["pau", "k", "a", "s", "i", "pau"],
        "pjs002": ["pau", "t", "u", "pau"],
        "pjs003": ["pau", "g", "e", "r", "o", "pau"],
        "pjs004": ["pau", "n", "a", "m", "o", "pau"],
        "pjs005": ["pau", "h", "a", "y", "o", "w", "a", "pau"],
    }
    paths = []
    for stem, toks in specs.items():
        d = tmp_path / stem
        d.mkdir()
        p = d / f"{stem}.lab"
        lines = []
        t = 0
        for tok in toks:
            lines.append(f"{t} {t + 1000000} {tok}")
            t += 1000000
        p.write_text("\n".join(lines) + "\n")
        paths.append(p)

    selected, stats = dbl._select_lab_files(paths, required_onsets=dbl.REQUIRED_ONSETS, min_files=2, max_files=10)
    assert set(dbl.REQUIRED_ONSETS) <= set(stats["covered_onsets"])
    assert set(stats["covered_vowels"]) == set(dbl.VOWELS_5)
    assert selected == sorted(selected, key=lambda p: p.stem)


# --- End-to-end（合成 wav + .lab、実データ非依存） ---


def _write_sine_wav(path: Path, duration_s: float, freq_hz: float = 130.0, sr: int = 48000) -> None:
    """WORLD (harvest) が確実にピッチを検出できるよう、倍音を持つ疑似声帯波形
    （基本波 + 減衰する高調波の重畳）を書き出す（純音は harvest がほぼ無声判定
    してしまうため、実測で確認したこの構成を採用・[実装決定]）。"""
    t = np.arange(int(duration_s * sr)) / sr
    y = np.zeros_like(t)
    for k in range(1, 8):
        y += (1.0 / k) * np.sin(2 * np.pi * freq_hz * k * t)
    y *= 0.2 / np.max(np.abs(y))
    sf.write(str(path), y.astype(np.float64), sr, subtype="PCM_24")


def test_build_donor_bank_lab_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "pjs_corpus"
    d = root / "pjs001"
    d.mkdir(parents=True)
    _write_sine_wav(d / "pjs001_song.wav", duration_s=2.0)

    # 100ns 単位: 0-0.3s pau / 0.3-0.4s k / 0.4-0.9s a / 0.9-1.0s pau
    lab_lines = [
        "0 3000000 pau",
        "3000000 4000000 k",
        "4000000 9000000 a",
        "9000000 10000000 pau",
    ]
    (d / "pjs001.lab").write_text("\n".join(lab_lines) + "\n")

    bank, unit_vowels, consonant_clips, stats = dbl.build_donor_bank_lab(
        root, target_median_hz=260.0, min_files=1, max_files=5
    )

    assert len(bank.units) == 1
    assert list(unit_vowels.values()) == ["a"]
    assert "k" in consonant_clips
    assert consonant_clips["k"][0].n_frames > 0
    assert stats["transpose_semitones"] == 12  # donor ~130Hz -> target 260Hz = +1 oct

    # 決定論: 再構築して bit 一致。
    bank2, unit_vowels2, _c2, _s2 = dbl.build_donor_bank_lab(root, target_median_hz=260.0, min_files=1, max_files=5)
    assert np.array_equal(bank.sp, bank2.sp)
    assert unit_vowels == unit_vowels2


def test_build_donor_bank_lab_cache_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "pjs_corpus"
    d = root / "pjs001"
    d.mkdir(parents=True)
    _write_sine_wav(d / "pjs001_song.wav", duration_s=1.0)
    (d / "pjs001.lab").write_text("0 3000000 pau\n3000000 4000000 s\n4000000 9000000 o\n")

    cache_dir = tmp_path / "cache"
    bank1, uv1, _c1, s1 = dbl.build_donor_bank_lab(
        root, target_median_hz=260.0, cache_dir=cache_dir, min_files=1, max_files=5
    )
    assert s1["cache_hit"] is False
    bank2, uv2, _c2, _s2 = dbl.build_donor_bank_lab(
        root, target_median_hz=260.0, cache_dir=cache_dir, min_files=1, max_files=5
    )
    assert np.array_equal(bank1.sp, bank2.sp)
    assert uv1 == uv2


def test_build_donor_bank_lab_cache_stale_after_lab_edit(tmp_path: Path) -> None:
    """P1 修正 (review #262): `--cache-dir` 再利用中に .lab を編集（母音境界を
    ずらす）すると、古い pickle を返さず再計算されることを確認する（旧実装は
    root path + options のみがキー材料で .lab/wav の内容は反映されなかった）。
    """
    root = tmp_path / "pjs_corpus"
    d = root / "pjs001"
    d.mkdir(parents=True)
    _write_sine_wav(d / "pjs001_song.wav", duration_s=1.0)
    lab_path = d / "pjs001.lab"
    lab_path.write_text("0 3000000 pau\n3000000 4000000 s\n4000000 9000000 o\n")

    cache_dir = tmp_path / "cache"
    bank1, _uv1, _c1, _s1 = dbl.build_donor_bank_lab(
        root, target_median_hz=260.0, cache_dir=cache_dir, min_files=1, max_files=5
    )
    dur1 = bank1.units[0].duration_s

    # 母音境界を後ろへずらす（vowel span [4000000,9000000) -> [4000000,7000000)）。
    lab_path.write_text("0 3000000 pau\n3000000 4000000 s\n4000000 7000000 o\n")
    bank2, _uv2, _c2, _s2 = dbl.build_donor_bank_lab(
        root, target_median_hz=260.0, cache_dir=cache_dir, min_files=1, max_files=5
    )
    dur2 = bank2.units[0].duration_s

    assert dur2 != dur1  # 古いキャッシュを再利用していれば dur1 のまま
    assert len(list(cache_dir.glob("lab_bank_*.pkl"))) == 2
