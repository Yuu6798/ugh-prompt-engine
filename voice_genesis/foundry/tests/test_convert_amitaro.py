"""test_convert_amitaro.py — run 7 教師 intake 変換（DESIGN_S6_run7.md §2-3/§2-4）。

検証対象 = 選定規則の決定論実装: 文番号昇順走査・走査中除外（累積不算入）・
dosage 打ち切り + ±tolerance fail-closed・被覆検査・staged 入力 sha 記帳・
44.1kHz 化 + BS.1770 正規化・選定来歴（selection.json）の全数記帳。

走査規模はモジュール定数 `N_RECITATION`（本番 324）を monkeypatch で 3 に
縮めて検証する（関数はグローバルを実行時参照するため安全）。音声は
`test_convert_user.py` と同型の合成フィクスチャ（純音 + 無音）を 48kHz で
生成する。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_dataprep"))
import convert_amitaro as ca  # noqa: E402
import convert_user as cu  # noqa: E402

_FFMPEG_MISSING = cu.convert_d3.FFMPEG_PATH is None
_ffmpeg_required = pytest.mark.skipif(
    _FFMPEG_MISSING, reason="ffmpeg が見つからない環境ではスキップ（resample_to_44k1 依存）"
)

SR = 48000

# 実 dsdict の構造を縮約再現: 基本モーラ = ひらがな（小文字母音）+ 同じ
# モーラのカタカナ = 大文字母音（別音素クラス）+ 外来音結合 = カタカナのみ。
_TEST_DSDICT = {
    "さ": ["s", "a"], "す": ["s", "u"], "か": ["k", "a"], "た": ["t", "a"],
    "サ": ["s", "A"], "ス": ["s", "U"], "カ": ["k", "A"], "タ": ["t", "A"],
    "ティ": ["t", "I"],
}


def _write_dsdict(tmp_path: Path) -> Path:
    payload = {"entries": [{"grapheme": g, "phonemes": list(p)}
                           for g, p in _TEST_DSDICT.items()]}
    path = tmp_path / "dsdict.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def _tone(freq_hz: float, duration_sec: float, amp: float = 0.3) -> np.ndarray:
    n = int(round(duration_sec * SR))
    t = np.arange(n) / SR
    return amp * np.sin(2.0 * np.pi * freq_hz * t)


def _silence(duration_sec: float) -> np.ndarray:
    return np.zeros(int(round(duration_sec * SR)), dtype=np.float64)


def _write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sr, subtype="PCM_16")


def _write_transcript(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "recitation_transcript_utf8.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


_DEFAULT_LINES = [
    "RECITATION324_001:さす。,サス。",
    "RECITATION324_002:ヴぁ。,ヴァ。",
    "RECITATION324_003:かた、さす。,カタ、サス。",
]


def _stage_default_audio(staged: Path) -> None:
    # 001「サス。」= 1 フレーズ → 1 有声ラン（1.0s）。
    _write_wav(staged / "recitation001.wav",
               np.concatenate([_silence(0.2), _tone(220.0, 1.0), _silence(0.2)]))
    # 002 は dsdict 未収載（ヴァ）で除外される — 音声内容は任意（存在は必須）。
    _write_wav(staged / "recitation002.wav",
               np.concatenate([_silence(0.2), _tone(220.0, 0.6), _silence(0.2)]))
    # 003「カタ、サス。」= 2 フレーズ → 2 有声ラン（0.4s ギャップ）。
    _write_wav(staged / "recitation003.wav",
               np.concatenate([_silence(0.2), _tone(220.0, 0.9), _silence(0.4),
                               _tone(247.0, 0.9), _silence(0.2)]))


@pytest.fixture()
def mini_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ca, "N_RECITATION", 3)
    staged = tmp_path / "staged"
    staged.mkdir()
    _stage_default_audio(staged)
    transcript = _write_transcript(tmp_path, _DEFAULT_LINES)
    dsdict = _write_dsdict(tmp_path)
    return staged, transcript, dsdict


# --- 純関数（transcript 形状・フレーズ分割） --------------------------------


def test_parse_transcript_shape_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ca, "N_RECITATION", 3)
    path = _write_transcript(tmp_path, _DEFAULT_LINES)
    parsed = ca.parse_transcript(path)
    assert parsed[3]["reading"] == "カタ、サス。"
    assert parsed[1]["sentence_id"] == "RECITATION324_001"


def test_parse_transcript_rejects_wrong_line_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ca, "N_RECITATION", 3)
    path = _write_transcript(tmp_path, _DEFAULT_LINES[:2])
    with pytest.raises(ca.ConvertAmitaroError, match="expected 3"):
        ca.parse_transcript(path)


def test_parse_transcript_rejects_wrong_sentence_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ca, "N_RECITATION", 3)
    lines = list(_DEFAULT_LINES)
    lines[1] = "RECITATION324_009:x,ア。"
    path = _write_transcript(tmp_path, lines)
    with pytest.raises(ca.ConvertAmitaroError, match="RECITATION324_002"):
        ca.parse_transcript(path)


def test_tokenize_reading_prefers_hiragana_lowercase_vowels(tmp_path: Path) -> None:
    """カタカナ読みの基本モーラは**ひらがな側（小文字母音）**へ写す — 生カナ
    優先だと大文字母音クラスへ落ち、歌唱コーパスの通常母音と不連続になる
    （2026-08-19 実測・convert_amitaro docstring 参照）。外来音結合（ティ）は
    ひらがな写像が未収載のため生カタカナへフォールバックする。"""
    import convert_user as _cu
    dsdict = _cu.load_dsdict(_write_dsdict(tmp_path))
    tokens = ca.tokenize_reading_with_dsdict(dsdict, "スティサ")
    assert tokens == [("ス", ["s", "u"]), ("ティ", ["t", "I"]), ("サ", ["s", "a"])]


def test_split_reading_phrases() -> None:
    assert ca.split_reading_phrases("カタ、サス。") == ["カタ", "サス"]
    assert ca.split_reading_phrases("サス") == ["サス"]
    with pytest.raises(ca.ConvertAmitaroError, match="zero phrase"):
        ca.split_reading_phrases("。")


# --- convert() 本体 ----------------------------------------------------------


@_ffmpeg_required
def test_convert_happy_path_selects_by_dosage_and_publishes(mini_corpus, tmp_path: Path) -> None:
    staged, transcript, dsdict = mini_corpus
    out = tmp_path / "amitaro_dataset"
    summary = ca.convert(
        staged, transcript, dsdict, out,
        normalize_loudness_lufs=-30.0, dosage_target_s=3.0, dosage_tolerance=0.5,
    )
    # 001（採用・1.4s）→ 002（除外・不算入）→ 003（採用・cum 4.0 ≥ 3.0 で打ち切り）
    assert summary["included_cards"] == ["recitation001", "recitation003"]
    assert [e["card_id"] for e in summary["excluded_cards"]] == ["recitation002"]
    assert summary["scan"] == {"n_scanned": 3, "n_total": 3}

    rows = (out / "transcriptions.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3  # header + 2
    for name in ("recitation001", "recitation003"):
        info = sf.info(str(out / "wavs" / f"{name}.wav"))
        assert info.samplerate == 44100 and info.channels == 1

    loudness = json.loads((out / "loudness_normalization.json").read_text(encoding="utf-8"))
    assert loudness["target_lufs"] == -30.0
    assert set(loudness["entries"]) == {"recitation001.wav", "recitation003.wav"}
    for entry in loudness["entries"].values():
        assert abs(entry["post_lufs"] - (-30.0)) < 0.3

    selection = json.loads((out / "selection.json").read_text(encoding="utf-8"))
    assert selection["dosage"]["actual_total_ph_dur_s"] == pytest.approx(
        sum(v["total_ph_dur_s"] for v in selection["per_card_ph_dur"].values()), abs=0.01
    )
    # staged 入力 sha は走査した 3 本全数（除外文も含む — 再現に必要な入力の全体）
    assert set(selection["staged_source_sha256"]) == {
        "recitation001.wav", "recitation002.wav", "recitation003.wav"
    }
    for wav_name, sha in selection["staged_source_sha256"].items():
        assert sha == hashlib.sha256((staged / wav_name).read_bytes()).hexdigest()

    exclusions = json.loads((out / "exclusions.json").read_text(encoding="utf-8"))
    assert exclusions["n_included"] == 2 and exclusions["n_excluded"] == 1
    assert "unmapped grapheme" in exclusions["excluded"][0]["reason"]


@_ffmpeg_required
def test_convert_is_deterministic(mini_corpus, tmp_path: Path) -> None:
    staged, transcript, dsdict = mini_corpus
    outs = []
    for name in ("out_a", "out_b"):
        out = tmp_path / name
        ca.convert(staged, transcript, dsdict, out,
                   normalize_loudness_lufs=-30.0, dosage_target_s=3.0,
                   dosage_tolerance=0.5)
        outs.append(out)
    for rel in ("transcriptions.csv", "selection.json", "exclusions.json",
                "loudness_normalization.json", "wavs/recitation001.wav",
                "wavs/recitation003.wav"):
        a = (outs[0] / rel).read_bytes()
        b = (outs[1] / rel).read_bytes()
        assert a == b, f"non-deterministic output: {rel}"


def test_convert_dosage_floor_unreachable_fails_closed(mini_corpus, tmp_path: Path) -> None:
    staged, transcript, dsdict = mini_corpus
    out = tmp_path / "amitaro_dataset"
    with pytest.raises(ca.ConvertAmitaroError, match="dosage out of band"):
        ca.convert(staged, transcript, dsdict, out,
                   normalize_loudness_lufs=-30.0, dosage_target_s=100.0,
                   dosage_tolerance=0.1)
    assert not out.exists()  # 何も公開しない


def test_convert_missing_staged_file_fails_closed(mini_corpus, tmp_path: Path) -> None:
    staged, transcript, dsdict = mini_corpus
    (staged / "recitation002.wav").unlink()
    with pytest.raises(ca.ConvertAmitaroError, match="staged source missing"):
        ca.convert(staged, transcript, dsdict, tmp_path / "out",
                   normalize_loudness_lufs=-30.0, dosage_target_s=3.0,
                   dosage_tolerance=0.5)


def test_convert_rejects_unexpected_sample_rate(mini_corpus, tmp_path: Path) -> None:
    staged, transcript, dsdict = mini_corpus
    # 24kHz の取り違え素材（ITA 配布仕様 = 48kHz）
    data, _sr = sf.read(str(staged / "recitation001.wav"))
    sf.write(str(staged / "recitation001.wav"), data, 24000, subtype="PCM_16")
    with pytest.raises(ca.ConvertAmitaroError, match="sample rate"):
        ca.convert(staged, transcript, dsdict, tmp_path / "out",
                   normalize_loudness_lufs=-30.0, dosage_target_s=3.0,
                   dosage_tolerance=0.5)


def test_convert_coverage_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """さ行 onset / 文末 [s,u] を含まない選定セットは被覆検査で停止する
    （DESIGN_S6 §2-3-6 — 黙って手動追加しない・memo 改訂で裁定し直す）。"""
    monkeypatch.setattr(ca, "N_RECITATION", 1)
    staged = tmp_path / "staged"
    staged.mkdir()
    _write_wav(staged / "recitation001.wav",
               np.concatenate([_silence(0.2), _tone(220.0, 1.0), _silence(0.2)]))
    transcript = _write_transcript(tmp_path, ["RECITATION324_001:かた。,カタ。"])
    dsdict = _write_dsdict(tmp_path)
    with pytest.raises(ca.ConvertAmitaroError, match="coverage"):
        ca.convert(staged, transcript, dsdict, tmp_path / "out",
                   normalize_loudness_lufs=-30.0, dosage_target_s=1.0,
                   dosage_tolerance=0.9)
