"""test_adapter_donor_bank_utau.py — VG-F1.2-B UTAU oto.ini バンクローダーの検証。
合成 fixture（oto.ini テキスト + 短い合成 wav）で高速・実波音リツ非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest
import soundfile as sf

import donor_bank_utau as dbu


# --- 純関数（音声非依存） ---


def test_note_name_to_hz_a4_is_440() -> None:
    assert dbu.note_name_to_hz("A4") == pytest.approx(440.0)


def test_note_name_to_hz_a3_is_220() -> None:
    assert dbu.note_name_to_hz("A3") == pytest.approx(220.0)


def test_note_name_to_hz_sharp() -> None:
    # C#5 = 554.37Hz 近辺
    assert dbu.note_name_to_hz("C#5") == pytest.approx(554.365, abs=0.1)


def test_normalize_mora_kana_plain_table() -> None:
    onset, vowel, status = dbu.normalize_mora_kana("か")
    assert (onset, vowel, status) == ("k", "a", "ok")


def test_normalize_mora_kana_vowel_only() -> None:
    onset, vowel, status = dbu.normalize_mora_kana("あ")
    assert (onset, vowel, status) == (None, "a", "ok")


def test_normalize_mora_kana_extra_table_voiced() -> None:
    onset, vowel, status = dbu.normalize_mora_kana("ば")
    assert (onset, vowel, status) == ("b", "a", "ok")


def test_normalize_mora_kana_moraic_nasal() -> None:
    onset, vowel, status = dbu.normalize_mora_kana("ん")
    assert (onset, vowel, status) == (None, "N", "moraic_nasal")


def test_normalize_mora_kana_sokuon() -> None:
    onset, vowel, status = dbu.normalize_mora_kana("っ")
    assert (onset, vowel, status) == (None, None, "sokuon")


def test_normalize_mora_kana_long_vowel() -> None:
    onset, vowel, status = dbu.normalize_mora_kana("ー")
    assert onset is None
    assert status == "long_vowel"
    # 直前モーラ不明時は既定で "a" を維持する（phoneme_jp.kana_to_morae の仕様）
    assert vowel == "a"


def test_normalize_mora_kana_unmapped() -> None:
    onset, vowel, status = dbu.normalize_mora_kana("№")
    assert (onset, vowel, status) == (None, None, "unmapped")


def test_parse_alias_mora_vcv_with_prev_vowel() -> None:
    prev, mora, is_initial = dbu.parse_alias_mora("a かA3", ["A3", "F4"])
    assert prev == "a"
    assert mora == "か"
    assert is_initial is False


def test_parse_alias_mora_phrase_initial() -> None:
    prev, mora, is_initial = dbu.parse_alias_mora("- かA3", ["A3", "F4"])
    assert prev is None
    assert mora == "か"
    assert is_initial is True


def test_parse_oto_ini_basic(tmp_path: Path) -> None:
    text = (
        "_test.wav=- かA3,99,166,4430,72,9\n"
        "_test.wav=a かA3,470,370,3789,300,100\n"
    )
    p = tmp_path / "oto.ini"
    p.write_bytes(text.encode("cp932"))
    entries = dbu.parse_oto_ini(p)
    assert len(entries) == 2
    e0 = entries[0]
    assert e0.wav_filename == "_test.wav"
    assert e0.alias == "- かA3"
    assert e0.offset_ms == 99.0
    assert e0.consonant_ms == 166.0
    assert e0.blank_ms == 4430.0
    assert e0.preutterance_ms == 72.0
    assert e0.overlap_ms == 9.0


def test_parse_oto_ini_skips_malformed_lines(tmp_path: Path) -> None:
    text = "_test.wav=- かA3,99,166,4430,72,9\nnot a valid line without equals or commas\n"
    p = tmp_path / "oto.ini"
    p.write_bytes(text.encode("cp932"))
    entries = dbu.parse_oto_ini(p)
    assert len(entries) == 1


def test_decode_oto_bytes_cp932() -> None:
    data = "波音リツ".encode("cp932")
    assert dbu.decode_oto_bytes(data) == "波音リツ"


def test_decode_oto_bytes_utf8_fallback() -> None:
    data = "波音リツ".encode("utf-8")
    assert dbu.decode_oto_bytes(data) == "波音リツ"


def test_cutoff_position_ms_distance_from_end_interpretation() -> None:
    # 実測較正（波音リツ強連続音 Ver1.5.1 A3 の実データ）: offset=2166, blank=1733,
    # wav_duration=4667.0ms -> cutoff = 4667-1733 = 2934ms（絶対位置解釈だと矛盾する行）。
    cutoff = dbu.cutoff_position_ms(offset_ms=2166.0, blank_ms=1733.0, wav_duration_ms=4667.0)
    assert cutoff == pytest.approx(2934.0)


def test_cutoff_position_ms_negative_blank_same_interpretation() -> None:
    # 符号付き（負）の行でも abs() で同じ扱いになる（実データに 2-3 件存在）。
    cutoff_pos = dbu.cutoff_position_ms(offset_ms=2749.0, blank_ms=-600.0, wav_duration_ms=4596.24)
    assert cutoff_pos == pytest.approx(4596.24 - 600.0)


def test_cutoff_position_ms_clamped_to_offset_when_degenerate() -> None:
    # 破損値（blank が過大で cutoff < offset になりうる場合）は offset へクランプ。
    cutoff = dbu.cutoff_position_ms(offset_ms=3000.0, blank_ms=9000.0, wav_duration_ms=4000.0)
    assert cutoff == pytest.approx(3000.0)


# --- 貪欲被覆選択 ---


def _entry(offset_ms: float, alias: str) -> dbu.OtoEntry:
    return dbu.OtoEntry(
        wav_filename="w", alias=alias, offset_ms=offset_ms, consonant_ms=50.0,
        blank_ms=500.0, preutterance_ms=10.0, overlap_ms=5.0,
    )


def test_select_wav_subset_covers_required_onsets() -> None:
    pitch_dirs = ["A3"]
    entries_by_wav = {
        "f_a.wav": [_entry(0.0, "- あA3")],
        "f_ka.wav": [_entry(0.0, "- かA3")],
        "f_sa.wav": [_entry(0.0, "- さA3")],
        "f_ta.wav": [_entry(0.0, "- たA3")],
        "f_ga.wav": [_entry(0.0, "- がA3")],
        "f_ma.wav": [_entry(0.0, "- まA3")],
        "f_na.wav": [_entry(0.0, "- なA3")],
        "f_ra.wav": [_entry(0.0, "- らA3")],
        "f_ha.wav": [_entry(0.0, "- はA3")],
        "f_ya.wav": [_entry(0.0, "- やA3")],
        "f_wa.wav": [_entry(0.0, "- わA3")],
    }
    from collections import OrderedDict

    ordered = OrderedDict(sorted(entries_by_wav.items()))
    selected, stats = dbu._select_wav_subset(
        ordered, pitch_dirs, required_onsets=dbu.REQUIRED_ONSETS, min_units_per_vowel=1, max_wav_files=20
    )
    assert set(stats["missing_onsets"]) == set()
    assert set(dbu.REQUIRED_ONSETS) <= set(stats["covered_onsets"])
    assert selected == sorted(selected)  # 決定論（ファイル名昇順で確定）


def test_select_wav_subset_respects_max_wav_files_cap() -> None:
    from collections import OrderedDict

    entries_by_wav = OrderedDict(
        (f"f{i}.wav", [_entry(0.0, "- あA3")]) for i in range(20)
    )
    selected, stats = dbu._select_wav_subset(
        entries_by_wav, ["A3"], required_onsets=(), min_units_per_vowel=1, max_wav_files=3
    )
    assert len(selected) <= 3


# --- End-to-end（合成 wav + oto.ini、実データ非依存） ---


def _write_sine_wav(path: Path, duration_s: float, freq_hz: float = 220.0, sr: int = 44100) -> None:
    """WORLD (harvest) が確実にピッチを検出できるよう、倍音を持つ疑似声帯波形
    （基本波 + 減衰する高調波の重畳）を書き出す（純音は harvest がほぼ無声判定
    してしまうため、実測で確認したこの構成を採用・[実装決定]）。"""
    t = np.arange(int(duration_s * sr)) / sr
    y = np.zeros_like(t)
    for k in range(1, 8):
        y += (1.0 / k) * np.sin(2 * np.pi * freq_hz * k * t)
    y *= 0.2 / np.max(np.abs(y))
    sf.write(str(path), y.astype(np.float64), sr, subtype="PCM_16")


def test_build_donor_bank_utau_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)

    # 2 モーラ分の VCV セグメントを含む 1 wav（あ→か、か行のみで単純化）。
    # 母音核区間: entry1 = [80,500)ms（consonant_end=80, cutoff=2000-1500=500）、
    # entry2 = [950,1700)ms（consonant_end=800+150=950, cutoff=2000-300=1700）。
    _write_sine_wav(pdir / "_test.wav", duration_s=2.0)
    oto_text = (
        "_test.wav=- あA3,0,80,1500,40,10\n"
        "_test.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    bank, unit_vowels, consonant_clips, stats = dbu.build_donor_bank_utau(
        root, min_units_per_vowel=1, max_wav_files=5
    )

    assert len(bank.units) == 2
    assert set(unit_vowels.values()) == {"a"}
    assert "k" in consonant_clips
    assert consonant_clips["k"][0].n_frames > 0
    assert stats["n_units_kept"] == 2
    assert stats["n_unmapped_kana"] == 0

    # 決定論: 同一入力を再構築して bit 一致（キャッシュ無効の素の再計算経路）。
    bank2, unit_vowels2, _clips2, _stats2 = dbu.build_donor_bank_utau(
        root, min_units_per_vowel=1, max_wav_files=5
    )
    assert np.array_equal(bank.sp, bank2.sp)
    assert np.array_equal(bank.ap, bank2.ap)
    assert unit_vowels == unit_vowels2


def test_build_donor_bank_utau_cache_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    cache_dir = tmp_path / "cache"
    bank1, uv1, _c1, s1 = dbu.build_donor_bank_utau(root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5)
    assert s1["cache_hit"] is False
    bank2, uv2, _c2, s2 = dbu.build_donor_bank_utau(root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5)
    assert np.array_equal(bank1.sp, bank2.sp)
    assert uv1 == uv2


# --- 追補 F1.3-A item1: unit スキーマ拡張（oto overlap / preutterance） ---


def test_build_donor_bank_utau_units_carry_overlap_and_preutterance(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=2.0)
    # entry1: preutterance=40ms(=8frames@5ms), overlap=10ms(=2frames)
    # entry2: preutterance=80ms(=16frames), overlap=20ms(=4frames)
    oto_text = (
        "_test.wav=- あA3,0,80,1500,40,10\n"
        "_test.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    bank, _uv, _clips, _stats = dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)
    assert len(bank.units) == 2
    assert bank.units[0].overlap_frames == 2
    assert bank.units[0].preutterance_frames == 8
    assert bank.units[1].overlap_frames == 4
    assert bank.units[1].preutterance_frames == 16


def test_build_donor_bank_utau_negative_overlap_clamped_to_zero(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    # overlap=-15ms (負値) -> クランプされ 0 フレームになる。
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,-15\n".encode("cp932"))

    bank, _uv, _clips, stats = dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)
    assert bank.units[0].overlap_frames == 0
    assert stats["n_negative_overlap_clamped"] == 1


def test_build_donor_bank_utau_cache_key_changed_by_schema_version(tmp_path: Path) -> None:
    """[実装決定・record] 追補 F1.3-A のスキーマ拡張でキー材料へバージョンマーカーを
    足したため、旧スキーマ（overlap/preutterance フィールド無し）のキャッシュファイル名
    とは絶対に衝突しないことを確認する（衝突すれば古い pickle を AttributeError 無しに
    読み込んでしまい、新フィールドが欠落したまま静かに動いてしまう）。
    """
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    cache_dir = tmp_path / "cache"
    dbu.build_donor_bank_utau(root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5)
    cached = list(cache_dir.glob("utau_bank_*.pkl"))
    assert len(cached) == 1
    # 旧スキーマ相当のキー材料（バージョンマーカー無し）と衝突しないことを確認する。
    import hashlib

    legacy_key_material = f"{root}|A3|5.0|1|5"
    legacy_key = hashlib.sha256(legacy_key_material.encode("utf-8")).hexdigest()[:24]
    assert legacy_key not in cached[0].name


# --- 追補 F1.4-A: VCV unit 化（donor_bank_utau v2） ---


def test_select_wav_subset_for_contexts_covers_required_and_records_missing() -> None:
    from collections import OrderedDict

    entries_by_wav = OrderedDict(
        [
            ("f_aka.wav", [_entry(0.0, "a かA3"), _entry(500.0, "a きA3")]),
            ("f_hatsu.wav", [_entry(0.0, "- あA3")]),
            ("f_unrelated.wav", [_entry(0.0, "i さA3")]),
        ]
    )
    required = [(None, "あ"), ("a", "か")]
    selected, stats = dbu._select_wav_subset_for_contexts(entries_by_wav, ["A3"], required)
    assert set(selected) == {"f_aka.wav", "f_hatsu.wav"}
    assert stats["missing_contexts"] == []
    assert stats["n_contexts_found"] == 2
    assert selected == sorted(selected)  # 決定論


def test_select_wav_subset_for_contexts_records_missing_context() -> None:
    from collections import OrderedDict

    entries_by_wav = OrderedDict([("f1.wav", [_entry(0.0, "a かA3")])])
    required = [(None, "あ"), ("a", "か")]  # "(None, あ)" は存在しない
    selected, stats = dbu._select_wav_subset_for_contexts(entries_by_wav, ["A3"], required)
    assert selected == ["f1.wav"]
    assert stats["missing_contexts"] == [(None, "あ")]
    assert stats["n_contexts_found"] == 1


def test_select_wav_subset_for_contexts_none_uses_generic_fallback() -> None:
    from collections import OrderedDict

    entries_by_wav = OrderedDict((f"f{i}.wav", [_entry(0.0, "- あA3")]) for i in range(10))
    selected, stats = dbu._select_wav_subset_for_contexts(entries_by_wav, ["A3"], None, max_wav_files=3)
    assert len(selected) == 3
    assert stats["mode"] == "generic"


def test_build_donor_bank_utau_vcv_unit_spans_full_offset_to_cutoff(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=2.0)
    # wav_duration=2000ms。cutoff = wav_duration - |blank|（cutoff_position_ms 規約）。
    # entry1: cutoff=2000-1500=500ms=100frame -> [0,100)frame、
    #   consonant_end=0+80=80ms=16frame -> vcore_rel=16。
    # entry2: offset=800ms=160frame、cutoff=2000-300=1700ms=340frame -> [160,340)frame、
    #   consonant_end=800+150=950ms=190frame -> vcore_rel=190-160=30frame。
    oto_text = (
        "_test.wav=- あA3,0,80,1500,40,10\n"
        "_test.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    bank, unit_contexts, stats = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5)
    assert len(bank.units) == 2
    assert bank.source == "utau_oto_vcv"

    u0, u1 = bank.units
    assert (u0.start_frame, u0.end_frame) == (0, 100)
    assert u0.vowel_core_start_frame == 16
    assert (u1.start_frame, u1.end_frame) == (160, 340)
    assert u1.vowel_core_start_frame == 30
    assert u1.overlap_frames == 4
    assert u1.preutterance_frames == 16

    assert unit_contexts[0].prev_vowel is None
    assert unit_contexts[0].mora == "あ"
    assert unit_contexts[0].is_phrase_initial is True
    assert unit_contexts[1] == dbu.VCVContext(prev_vowel="a", mora="か", is_phrase_initial=False)
    assert stats["n_units_kept"] == 2


def test_build_donor_bank_utau_vcv_deterministic_repeat(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=2.0)
    oto_text = (
        "_test.wav=- あA3,0,80,1500,40,10\n"
        "_test.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    bank1, ctx1, _s1 = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5)
    bank2, ctx2, _s2 = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5)
    assert np.array_equal(bank1.sp, bank2.sp)
    assert np.array_equal(bank1.ap, bank2.ap)
    assert ctx1 == ctx2


def test_build_donor_bank_utau_vcv_cache_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    cache_dir = tmp_path / "cache"
    bank1, ctx1, s1 = dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5)
    assert s1["cache_hit"] is False
    bank2, ctx2, s2 = dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5)
    assert np.array_equal(bank1.sp, bank2.sp)
    assert ctx1 == ctx2


def test_build_donor_bank_utau_vcv_required_contexts_change_cache_key(tmp_path: Path) -> None:
    """[実装決定・record] required_contexts が異なれば選択される wav 部分集合が
    変わりうるため、キャッシュキーへ含める（衝突すると別スコア向けの部分
    集合バンクを誤って再利用してしまう）。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    cache_dir = tmp_path / "cache"
    dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5, required_contexts=[(None, "あ")])
    dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5, required_contexts=[("a", "か")])
    cached = list(cache_dir.glob("utau_bank_vcv_*.pkl"))
    assert len(cached) == 2
