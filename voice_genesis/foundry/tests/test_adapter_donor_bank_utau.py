"""test_adapter_donor_bank_utau.py — VG-F1.2-B UTAU oto.ini バンクローダーの検証。
合成 fixture（oto.ini テキスト + 短い合成 wav）で高速・実波音リツ非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapter"))

import numpy as np
import pytest
import soundfile as sf

import donor_bank as db
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


def test_cutoff_position_ms_negative_blank_is_offset_relative_fixed_length() -> None:
    """P1 修正 (review #262 R2): 負の blank は「ファイル終端からの距離」ではなく
    「offset からの固定長」（cutoff = offset - blank_ms = offset + |blank_ms|）。

    実データ較正: 波音リツ強連続音 Ver1.5.1 A3 `n ぬA3`
    （offset=2749, blank=-600, wav_duration=4596.24）。一次資料
    https://utaudb.sakura.ne.jp/knowledge.php?id=21 「ブランクが負の数の場合、
    オフセットからの時間[ms]に-1を乗じたもので定義される」
    = `blank_ms = -(cutoff_ms - offset_ms)` = `cutoff_ms = offset_ms - blank_ms`。
    旧実装（`wav_duration_ms - abs(blank_ms)` = 3996.24ms）は offset からの
    固定長という符号意味を無視しており、647ms も過大な区間を切り出していた。
    """
    cutoff_pos = dbu.cutoff_position_ms(offset_ms=2749.0, blank_ms=-600.0, wav_duration_ms=4596.24)
    assert cutoff_pos == pytest.approx(2749.0 + 600.0)
    assert cutoff_pos == pytest.approx(3349.0)


def test_cutoff_position_ms_negative_blank_independent_of_wav_duration() -> None:
    """負の blank は offset からの固定長なので、wav_duration_ms を変えても
    cutoff は不変（旧実装は wav_duration_ms に依存しており、この不変条件を
    満たさなかった）。"""
    cutoff_a = dbu.cutoff_position_ms(offset_ms=100.0, blank_ms=-50.0, wav_duration_ms=1000.0)
    cutoff_b = dbu.cutoff_position_ms(offset_ms=100.0, blank_ms=-50.0, wav_duration_ms=5000.0)
    assert cutoff_a == cutoff_b == pytest.approx(150.0)


def test_cutoff_position_ms_negative_blank_clamped_to_wav_duration_when_degenerate() -> None:
    """offset + |blank| が wav_duration_ms を超える破損値は wav_duration_ms へ
    クランプする（従来の破損値保護を負分岐でも維持）。"""
    cutoff = dbu.cutoff_position_ms(offset_ms=3900.0, blank_ms=-500.0, wav_duration_ms=4000.0)
    assert cutoff == pytest.approx(4000.0)


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
    assert bank1.stats["cache_hit"] is False
    bank2, uv2, _c2, s2 = dbu.build_donor_bank_utau(root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5)
    assert np.array_equal(bank1.sp, bank2.sp)
    assert uv1 == uv2
    # P2 修正 (review #262 R3): pickle 復元時に cache_hit=True へ更新される
    # ことを検証する（旧実装は保存時の False を復元後もそのまま返していた）。
    assert s2["cache_hit"] is True
    assert bank2.stats["cache_hit"] is True


def test_build_donor_bank_utau_cache_write_failure_leaves_no_corrupt_pickle(
    tmp_path: Path, monkeypatch
) -> None:
    """P2 修正 (review #262 R5・`r3789400805`): v1 builder の pickle cache
    書き込み中の失敗（プロセス kill 相当）が最終 `cache_path` に破損 pickle
    を残さないことを検証する（実害シナリオの直接再現。§ donor_bank_lab.py
    の同種テストと対称の設計）。
    """
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))
    cache_dir = tmp_path / "cache"

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated pickle.dump failure")

    monkeypatch.setattr(db.pickle, "dump", _boom)
    with pytest.raises(RuntimeError):
        dbu.build_donor_bank_utau(root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5)
    assert list(cache_dir.glob("utau_bank_*.pkl")) == []
    assert list(cache_dir.glob("utau_bank_*.pkl.*.tmp")) == []

    monkeypatch.undo()
    _bank, _uv, _c, stats = dbu.build_donor_bank_utau(
        root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5
    )
    assert stats["cache_hit"] is False
    assert len(list(cache_dir.glob("utau_bank_*.pkl"))) == 1


def test_build_donor_bank_utau_vcv_cache_write_failure_leaves_no_corrupt_pickle(
    tmp_path: Path, monkeypatch
) -> None:
    """P2 修正 (review #262 R5・`r3789400805`): v2 (VCV) builder の同種是正。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))
    cache_dir = tmp_path / "cache"

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated pickle.dump failure")

    monkeypatch.setattr(db.pickle, "dump", _boom)
    with pytest.raises(RuntimeError):
        dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5)
    assert list(cache_dir.glob("utau_bank_vcv_*.pkl")) == []
    assert list(cache_dir.glob("utau_bank_vcv_*.pkl.*.tmp")) == []

    monkeypatch.undo()
    _bank, _ctx, stats = dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5)
    assert stats["cache_hit"] is False
    assert len(list(cache_dir.glob("utau_bank_vcv_*.pkl"))) == 1


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


# --- P1 修正 (review #262): キャッシュキーの内容ハッシュ（oto.ini 編集検知） ---


def test_build_donor_bank_utau_cache_stale_after_oto_edit(tmp_path: Path) -> None:
    """v1 builder: `--cache-dir` 再利用中に oto.ini を 1 バイト編集（overlap
    値を変更）すると、古い pickle を返さず再計算されることを確認する
    （旧実装は root path + options のみがキー材料で、oto.ini の内容は
    キーに反映されなかった）。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    oto_path = pdir / "oto.ini"
    oto_path.write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    cache_dir = tmp_path / "cache"
    bank1, _uv1, _c1, s1 = dbu.build_donor_bank_utau(
        root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5
    )
    assert s1["cache_hit"] is False
    assert bank1.units[0].overlap_frames == 2  # 10ms @ 5ms

    oto_path.write_bytes("_test.wav=- あA3,0,80,900,40,30\n".encode("cp932"))  # overlap 10->30
    bank2, _uv2, _c2, _s2 = dbu.build_donor_bank_utau(
        root, cache_dir=cache_dir, min_units_per_vowel=1, max_wav_files=5
    )
    # 古いキャッシュ（overlap_frames=2）を誤って再利用していれば ==2 のまま
    # になる。編集が反映されている（==6）ことが「キャッシュが stale でない」
    # ことの直接証拠。
    assert bank2.units[0].overlap_frames == 6  # 30ms @ 5ms
    assert len(list(cache_dir.glob("utau_bank_*.pkl"))) == 2


def test_build_donor_bank_utau_vcv_cache_stale_after_oto_edit(tmp_path: Path) -> None:
    """v2 (VCV) builder: 同じく oto.ini 編集がキャッシュキーへ反映される。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    oto_path = pdir / "oto.ini"
    oto_path.write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    cache_dir = tmp_path / "cache"
    bank1, _ctx1, s1 = dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5)
    assert s1["cache_hit"] is False
    assert bank1.units[0].overlap_frames == 2

    oto_path.write_bytes("_test.wav=- あA3,0,80,900,40,30\n".encode("cp932"))
    bank2, _ctx2, _s2 = dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5)
    assert bank2.units[0].overlap_frames == 6
    assert len(list(cache_dir.glob("utau_bank_vcv_*.pkl"))) == 2


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
    assert bank1.stats["cache_hit"] is False
    bank2, ctx2, s2 = dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5)
    assert np.array_equal(bank1.sp, bank2.sp)
    assert ctx1 == ctx2
    # P2 修正 (review #262 R3): pickle 復元時に cache_hit=True へ更新される。
    assert s2["cache_hit"] is True
    assert bank2.stats["cache_hit"] is True


def _count_read_bytes_per_path(monkeypatch) -> Dict[str, int]:
    """`Path.read_bytes` の呼び出し回数を解決パス文字列ごとに数える（P2 修正
    review #262 R3: split-read 排除の直接検証。measure_bands.py の
    `test_analyze_wav_reads_file_bytes_exactly_once` と同じ流儀）。"""
    counts: Dict[str, int] = {}
    orig_read_bytes = Path.read_bytes

    def _counting_read_bytes(self):
        key = str(self.resolve())
        counts[key] = counts.get(key, 0) + 1
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)
    return counts


def test_build_donor_bank_utau_vcv_reads_each_file_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """P2 修正 (review #262 R3): oto.ini と選択された各 wav は、ハッシュと
    decode/parse の両方に同一 read 結果を使う（split-read を直接 enforce）。
    """
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    wav_path = pdir / "_test.wav"
    _write_sine_wav(wav_path, duration_s=1.0)
    oto_path = pdir / "oto.ini"
    oto_path.write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    counts = _count_read_bytes_per_path(monkeypatch)
    dbu.build_donor_bank_utau_vcv(root, max_wav_files=5)

    assert counts.get(str(oto_path.resolve())) == 1
    assert counts.get(str(wav_path.resolve())) == 1


def test_build_donor_bank_utau_reads_each_file_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """P2 修正 (review #262 R3): v1 builder（`build_donor_bank_utau`）も
    同じ single-read 契約を満たす。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    wav_path = pdir / "_test.wav"
    _write_sine_wav(wav_path, duration_s=1.0)
    oto_path = pdir / "oto.ini"
    oto_path.write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    counts = _count_read_bytes_per_path(monkeypatch)
    dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)

    assert counts.get(str(oto_path.resolve())) == 1
    assert counts.get(str(wav_path.resolve())) == 1


# --- review #262 R9 (`r3789495252`): 選択された wav が欠けていれば fail-closed ---
# （PJS `_song.wav` 欠落チェック（R6・donor_bank_lab.build_donor_bank_lab）と対称）。


def test_build_donor_bank_utau_vcv_missing_selected_wav_raises(tmp_path: Path) -> None:
    """oto.ini はあるが対応する wav が実在しない（部分コピー相当）場合、旧実装は
    黙って skip して sel_stats の coverage 報告と食い違う false-success 状態に
    なっていた。新実装は選択段階で欠落を検出し fail-closed で拒否する。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))
    # `_test.wav` 自体は書き込まない（選択されるが存在しない wav）。

    with pytest.raises(FileNotFoundError, match=r"A3/_test\.wav"):
        dbu.build_donor_bank_utau_vcv(root, max_wav_files=5)


def test_build_donor_bank_utau_missing_selected_wav_raises(tmp_path: Path) -> None:
    """v1 builder（`build_donor_bank_utau`）でも同じ fail-closed 契約を満たす。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    with pytest.raises(FileNotFoundError, match=r"A3/_test\.wav"):
        dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)


def test_build_donor_bank_utau_vcv_required_contexts_change_cache_key(tmp_path: Path) -> None:
    """[実装決定・record] required_contexts が異なれば選択される wav 部分集合が
    変わりうるため、キャッシュキーへ含める（衝突すると別スコア向けの部分
    集合バンクを誤って再利用してしまう）。

    [R12 是正で fixture 拡張] realized coverage の fail-closed 検証
    （§ enforce_realized_context_coverage）が入ったため、各呼び出しの
    `required_contexts` は fixture 内で実際に realized 可能な文脈でなければ
    ならない。両方の文脈を用意した上で、呼び出しごとに異なる部分集合を
    要求することでキャッシュキーの独立性のみを検証する。
    """
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=2.0)
    oto_text = (
        "_test.wav=- あA3,0,80,1500,40,10\n"
        "_test.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    cache_dir = tmp_path / "cache"
    dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5, required_contexts=[(None, "あ")])
    dbu.build_donor_bank_utau_vcv(root, cache_dir=cache_dir, max_wav_files=5, required_contexts=[("a", "か")])
    cached = list(cache_dir.glob("utau_bank_vcv_*.pkl"))
    assert len(cached) == 2


# --- P2 修正 (review #262 R8・`r3789486145`): oto.ini の wav 参照が voicebank 外へ脱出する 3 形態 ---


def test_reject_wav_paths_outside_root_relative_traversal(tmp_path: Path) -> None:
    """`../` による相対脱出は拒否され、違反エントリの相対パスがエラーに列挙される。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    outside = tmp_path / "evil.wav"
    outside.write_bytes(b"not-a-real-wav")

    with pytest.raises(ValueError, match=r"A3/\.\./\.\./evil\.wav"):
        dbu._reject_wav_paths_outside_root(pdir, "A3", ["../../evil.wav"], root)


def test_reject_wav_paths_outside_root_absolute_path(tmp_path: Path) -> None:
    """絶対パスの wav 参照は voicebank root を無視して結合されてしまうため拒否する。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    outside = tmp_path / "evil.wav"
    outside.write_bytes(b"not-a-real-wav")

    with pytest.raises(ValueError, match=r"A3/.*evil\.wav"):
        dbu._reject_wav_paths_outside_root(pdir, "A3", [str(outside)], root)


def test_reject_wav_paths_outside_root_symlink_escape(tmp_path: Path) -> None:
    """ファイル名自体は `../` を含まなくても、symlink が root 外の実体を指す場合は拒否する。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    outside = tmp_path / "evil.wav"
    outside.write_bytes(b"not-a-real-wav")
    (pdir / "linked.wav").symlink_to(outside)

    with pytest.raises(ValueError, match=r"A3/linked\.wav"):
        dbu._reject_wav_paths_outside_root(pdir, "A3", ["linked.wav"], root)


def test_reject_wav_paths_outside_root_accepts_in_bounds_files(tmp_path: Path) -> None:
    """root 配下に収まる通常の wav 参照は素通しする（fail-closed の過検知が無いことを確認）。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    (pdir / "_test.wav").write_bytes(b"not-a-real-wav")

    dbu._reject_wav_paths_outside_root(pdir, "A3", ["_test.wav"], root)  # 例外なし


# --- P2 修正 (review #262 R10・`r3789520235`): resolve 後の root 包含検査だけでは ---
# pitch dir 間の横断（root 内に留まるが自分の pitch dir を脱出）を見逃す。resolve
# より前の語彙的検査（`..` 成分・絶対パス）で遮断する。


def test_reject_wav_paths_outside_root_cross_pitch_dir_traversal(tmp_path: Path) -> None:
    """`A3/../F4/foo.wav` は resolve 後も voicebank root 配下に収まるため
    旧実装（resolve 後の root 包含検査のみ）は素通ししていたが、A3 の
    annotation が F4 の音声を分析してしまう pitch dir 脱出のため拒否する。"""
    root = tmp_path / "voicebank"
    pdir_a3 = root / "A3"
    pdir_a3.mkdir(parents=True)
    pdir_f4 = root / "F4"
    pdir_f4.mkdir(parents=True)
    (pdir_f4 / "foo.wav").write_bytes(b"not-a-real-wav")

    with pytest.raises(ValueError, match=r"A3/\.\./F4/foo\.wav"):
        dbu._reject_wav_paths_outside_root(pdir_a3, "A3", ["../F4/foo.wav"], root)


def test_reject_wav_paths_outside_root_absolute_path_inside_root(tmp_path: Path) -> None:
    """絶対パスが voicebank root 内の実在ファイルを指していても、resolve 後の
    root 包含検査だけでは通過してしまうため、絶対パスは語彙的に拒否する。"""
    root = tmp_path / "voicebank"
    pdir_a3 = root / "A3"
    pdir_a3.mkdir(parents=True)
    pdir_f4 = root / "F4"
    pdir_f4.mkdir(parents=True)
    inside = pdir_f4 / "foo.wav"
    inside.write_bytes(b"not-a-real-wav")

    with pytest.raises(ValueError, match=r"A3/.*foo\.wav"):
        dbu._reject_wav_paths_outside_root(pdir_a3, "A3", [str(inside)], root)


# --- P2 修正 (review #262 R13・`_wav_ref_is_basename`): 非 basename ネスト参照 ---
# （`samples/foo.wav` 等）は絶対パスでも `..` 脱出でもないため R8/R10 の検査を
# いずれも素通りし resolve 後も root 配下に留まるが、`voicebank_identity_hash`
# の非再帰 glob からは不可視のまま分析される。


def test_wav_ref_is_basename_accepts_plain_filename() -> None:
    assert dbu._wav_ref_is_basename("_test.wav") is True


def test_wav_ref_is_basename_rejects_forward_slash_nesting() -> None:
    assert dbu._wav_ref_is_basename("samples/foo.wav") is False


def test_wav_ref_is_basename_rejects_backslash_nesting() -> None:
    """pathlib は POSIX 上で `\\` を区切り文字と解釈しないため、文字列検査で拾う。"""
    assert dbu._wav_ref_is_basename("samples\\foo.wav") is False


def test_wav_ref_is_basename_rejects_dotdot_and_dot() -> None:
    assert dbu._wav_ref_is_basename("..") is False
    assert dbu._wav_ref_is_basename(".") is False


def test_wav_ref_is_basename_rejects_absolute_path() -> None:
    assert dbu._wav_ref_is_basename("/etc/passwd") is False


def test_wav_ref_is_lexically_safe_delegates_to_basename_check() -> None:
    assert dbu._wav_ref_is_lexically_safe("_test.wav") is True
    assert dbu._wav_ref_is_lexically_safe("samples/foo.wav") is False


def test_reject_wav_paths_outside_root_rejects_nested_reference_even_when_inside_root(
    tmp_path: Path,
) -> None:
    """`samples/foo.wav` は resolve 後も voicebank root 配下（A3 のサブ
    ディレクトリ）に収まるため、R8/R10 の検査（絶対パス・`..`・resolve 後
    包含）はいずれも素通りする。R13 で非 basename 参照自体を拒否する。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    (pdir / "samples").mkdir(parents=True)
    (pdir / "samples" / "foo.wav").write_bytes(b"not-a-real-wav")

    with pytest.raises(ValueError, match=r"A3/samples/foo\.wav"):
        dbu._reject_wav_paths_outside_root(pdir, "A3", ["samples/foo.wav"], root)


def test_build_donor_bank_utau_rejects_nested_wav_reference_end_to_end(tmp_path: Path) -> None:
    """oto.ini が `samples/foo.wav`（pitch dir のサブディレクトリ）を参照する
    場合、`build_donor_bank_utau` は WORLD 分析へ進む前に fail-closed で拒否
    する（識別対象を pitch dir 直下に固定したまま、その外を指す参照を
    黙って分析しない）。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    (pdir / "samples").mkdir(parents=True)
    _write_sine_wav(pdir / "samples" / "foo.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("samples/foo.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    with pytest.raises(ValueError, match="voicebank root"):
        dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)


def test_build_donor_bank_utau_rejects_oto_wav_traversal_end_to_end(tmp_path: Path) -> None:
    """P2 修正 (review #262 R8・`r3789486145`): `../` を含む filename= を供給する
    悪意/破損 oto.ini から `build_donor_bank_utau` を呼ぶと、WORLD 分析（外部 wav の
    read）へ進む前に fail-closed で拒否される。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    outside = tmp_path / "evil.wav"
    _write_sine_wav(outside, duration_s=1.0)
    # oto.ini の filename フィールドへ相対脱出パスを直接埋め込む
    # （A3 → voicebank → tmp_path の 2 階層上りで root 外へ脱出する）。
    (pdir / "oto.ini").write_bytes("../../evil.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    with pytest.raises(ValueError, match="voicebank root"):
        dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)


# --- review #262 R9 (`r3789495249`): 公開 `wav_sha256` を (相対パス, digest) ---
# ペアで集約する（UTAU 音源では pitch dir 間で同名 wav ファイルが頻出するため、
# ファイル名を保ったまま A3/F4 間で中身を入れ替えても旧実装では検知できなかった）。


def _two_pitch_dir_root(tmp_path: Path) -> Path:
    root = tmp_path / "voicebank"
    for pdir_name, freq_hz in (("A3", 220.0), ("F4", 349.0)):
        pdir = root / pdir_name
        pdir.mkdir(parents=True)
        _write_sine_wav(pdir / "_test.wav", duration_s=1.0, freq_hz=freq_hz)
        (pdir / "oto.ini").write_bytes(f"_test.wav=- あ{pdir_name},0,80,900,40,10\n".encode("cp932"))
    return root


def test_build_donor_bank_utau_vcv_wav_sha256_changes_when_same_named_wavs_swap_content(
    tmp_path: Path,
) -> None:
    root = _two_pitch_dir_root(tmp_path)
    bank_before, _ctx1, _s1 = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5)

    # A3/_test.wav と F4/_test.wav の中身を入れ替える（ファイル名はどちらも
    # 変わらない = 旧実装の bare-digest sorted-join では検知不能な swap）。
    a3_bytes = (root / "A3" / "_test.wav").read_bytes()
    f4_bytes = (root / "F4" / "_test.wav").read_bytes()
    (root / "A3" / "_test.wav").write_bytes(f4_bytes)
    (root / "F4" / "_test.wav").write_bytes(a3_bytes)

    bank_after, _ctx2, _s2 = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5)
    assert bank_before.wav_sha256 != bank_after.wav_sha256


def test_build_donor_bank_utau_wav_sha256_changes_when_same_named_wavs_swap_content(
    tmp_path: Path,
) -> None:
    """v1 builder（`build_donor_bank_utau`）でも同じ対称の是正。"""
    root = _two_pitch_dir_root(tmp_path)
    bank_before, _uv1, _c1, _s1 = dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)

    a3_bytes = (root / "A3" / "_test.wav").read_bytes()
    f4_bytes = (root / "F4" / "_test.wav").read_bytes()
    (root / "A3" / "_test.wav").write_bytes(f4_bytes)
    (root / "F4" / "_test.wav").write_bytes(a3_bytes)

    bank_after, _uv2, _c2, _s2 = dbu.build_donor_bank_utau(root, min_units_per_vowel=1, max_wav_files=5)
    assert bank_before.wav_sha256 != bank_after.wav_sha256


# --- review #262 R12 (`r3789559165`): coverage は選択時の約束ではなく実際に ---
# 構築できた unit から導出する（終端修正）。§ donor_bank_lab.py の対称テストと
# 対で読む。


def test_build_donor_bank_utau_vcv_frontload_skips_invalid_entry_selects_later_valid_wav(
    tmp_path: Path,
) -> None:
    """先頭で見つかった oto エントリの `[offset, cutoff)` 区間が不正/短小
    （MIN_UNIT_FRAMES_ABS 未満）でも、同じ VCV 文脈を持つ後続の有効な wav が
    選択・分析され、実際に unit が構築されることを確認する。旧実装は
    エイリアス文字列の一致だけで即座に文脈を `remaining` から除去していた
    ため、不正な先行エントリが有効な後続 wav の選択を遮っていた（selection
    の "発見済み" 報告と実際に構築できる unit が乖離する実害）。
    """
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    # f_a_bad.wav（ファイル名昇順で先）: duration=100ms。blank=99 -> cutoff =
    # 100-99=1ms -> [0,1)ms は frame_period=5ms で 0 frame（不正/短小区間）。
    _write_sine_wav(pdir / "f_a_bad.wav", duration_s=0.1)
    # f_b_good.wav（ファイル名昇順で後）: duration=2000ms の有効な wav。
    _write_sine_wav(pdir / "f_b_good.wav", duration_s=2.0)
    oto_text = (
        "f_a_bad.wav=a かA3,0,10,99,10,5\n"
        "f_b_good.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    bank, unit_contexts, stats = dbu.build_donor_bank_utau_vcv(
        root, max_wav_files=5, required_contexts=[("a", "か")]
    )

    # 不正な先行エントリしか持たない f_a_bad.wav は分析対象へ選ばれず、
    # 有効な f_b_good.wav のみが選択・分析される。
    assert stats["n_wav_files_analyzed"] == 1
    assert stats["selection_stats_by_dir"]["A3"]["missing_contexts"] == []
    assert stats["selection_stats_by_dir"]["A3"]["n_entries_rejected_unusable"] == 1
    assert len(bank.units) == 1
    assert unit_contexts[0] == dbu.VCVContext(prev_vowel="a", mora="か", is_phrase_initial=False)
    assert stats["realized_coverage"]["missing_contexts"] == []
    assert stats["realized_coverage"]["covered_contexts"] == [("a", "か")]


def test_build_donor_bank_utau_vcv_frontload_deterministic_repeat(tmp_path: Path) -> None:
    """§ 上記テストと同一 fixture の決定論再現（前倒し検証込みの経路）。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "f_a_bad.wav", duration_s=0.1)
    _write_sine_wav(pdir / "f_b_good.wav", duration_s=2.0)
    oto_text = (
        "f_a_bad.wav=a かA3,0,10,99,10,5\n"
        "f_b_good.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    bank1, ctx1, _s1 = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5, required_contexts=[("a", "か")])
    bank2, ctx2, _s2 = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5, required_contexts=[("a", "か")])
    assert np.array_equal(bank1.sp, bank2.sp)
    assert np.array_equal(bank1.ap, bank2.ap)
    assert ctx1 == ctx2


def test_build_donor_bank_utau_vcv_realized_coverage_matches_selection_when_valid(
    tmp_path: Path,
) -> None:
    """正常系: 全文脈が有効に構築できる場合、選択時 coverage と realized
    coverage が一致する（分母を隠さない・乖離が無いことの直接確認）。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=2.0)
    oto_text = (
        "_test.wav=- あA3,0,80,1500,40,10\n"
        "_test.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))
    required = [(None, "あ"), ("a", "か")]

    bank, unit_contexts, stats = dbu.build_donor_bank_utau_vcv(root, max_wav_files=5, required_contexts=required)

    sel_missing = stats["selection_stats_by_dir"]["A3"]["missing_contexts"]
    realized_missing = stats["realized_coverage"]["missing_contexts"]
    assert sel_missing == [] == realized_missing
    assert stats["realized_coverage"]["covered_contexts"] == sorted(required, key=lambda c: (c[0] or "", c[1]))
    assert len(bank.units) == 2


def test_build_donor_bank_utau_vcv_fails_closed_when_required_context_unreachable(
    tmp_path: Path,
) -> None:
    """必須文脈が voicebank 内のどこにも存在しない場合、近傍/global
    フォールバックへ黙って縮退せず、不足文脈を列挙した `ValueError` で
    fail-closed する。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    with pytest.raises(ValueError, match=r"realized coverage"):
        dbu.build_donor_bank_utau_vcv(
            root, max_wav_files=5, required_contexts=[(None, "あ"), ("a", "か")]
        )


def test_build_donor_bank_utau_required_onsets_vowels_pass_when_satisfied(tmp_path: Path) -> None:
    """v1 builder（`build_donor_bank_utau`）でも realized coverage の必須要件
    enforcement が使える（§ VCV 版と対称。3 銀行統一の終端修正）。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=2.0)
    oto_text = (
        "_test.wav=- あA3,0,80,1500,40,10\n"
        "_test.wav=a かA3,800,150,300,80,20\n"
    )
    (pdir / "oto.ini").write_bytes(oto_text.encode("cp932"))

    bank, unit_vowels, consonant_clips, stats = dbu.build_donor_bank_utau(
        root, min_units_per_vowel=1, max_wav_files=5,
        required_onsets=["k"], required_vowels=["a"],
    )
    assert stats["realized_coverage"]["missing_onsets"] == []
    assert stats["realized_coverage"]["missing_vowels"] == []
    assert "k" in consonant_clips
    assert set(unit_vowels.values()) == {"a"}


def test_build_donor_bank_utau_required_onsets_fails_closed_when_missing(tmp_path: Path) -> None:
    """v1 builder: 必須 onset がどこにも構築できない場合は fail-closed する。"""
    root = tmp_path / "voicebank"
    pdir = root / "A3"
    pdir.mkdir(parents=True)
    _write_sine_wav(pdir / "_test.wav", duration_s=1.0)
    (pdir / "oto.ini").write_bytes("_test.wav=- あA3,0,80,900,40,10\n".encode("cp932"))

    with pytest.raises(ValueError, match=r"realized coverage"):
        dbu.build_donor_bank_utau(
            root, min_units_per_vowel=1, max_wav_files=5,
            required_onsets=["k"], required_vowels=["a"],
        )
