"""test_measure_bands.py — VG-F0 計測計器（帯域占有率 + HNR）の検証。

`measure_bands.analyze_wav` が v5 診断記録と同一手法で決定論的に動作することを
確認する。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import numpy as np
import pytest
import scipy.signal as sig
import soundfile as sf

import measure_bands as mb


def _write_wav(tmp_path: Path, name: str, x: np.ndarray, sr: int) -> Path:
    p = tmp_path / name
    sf.write(str(p), x, sr, subtype="PCM_16")
    return p


def test_pure_tone_low_band_and_hnr(tmp_path: Path) -> None:
    sr = 22050
    t = np.arange(int(1.0 * sr)) / sr
    x = 0.5 * np.sin(2 * np.pi * 220.0 * t)
    p = _write_wav(tmp_path, "tone220.wav", x, sr)

    r = mb.analyze_wav(p)

    assert r["b0_500"] > 0.95
    band_sum = (
        r["b0_500"] + r["b500_1k"] + r["b1k_3k"] + r["b3k_5k"] + r["b5k_8k"] + r["b8k_nyq"]
    )
    assert band_sum == pytest.approx(1.0, abs=1e-3)
    # 参照実装 f0_track() は放物線補間なしの整数ラグ自己相関ピークを HNR に使う
    # ため、220Hz（周期 sr/220≈100.2 サンプル、非整数）では r が僅かに減衰し
    # HNR は約 8.9dB になる（無音/帯域ノイズとの弁別が付く閾値として 8dB を採用）。
    assert r["hnr_median_db"] > 8
    assert r["dur_s"] == pytest.approx(1.0, abs=1e-3)


def test_bandpass_noise_high_band(tmp_path: Path) -> None:
    sr = 22050
    n = int(1.0 * sr)
    rng = np.random.default_rng(0)
    white = rng.standard_normal(n)
    sos = sig.butter(8, [4000, 7000], btype="bandpass", fs=sr, output="sos")
    x = sig.sosfilt(sos, white)
    x = x / (np.max(np.abs(x)) + 1e-12) * 0.5
    p = _write_wav(tmp_path, "bpnoise.wav", x, sr)

    r = mb.analyze_wav(p)

    assert r["b3k_5k"] + r["b5k_8k"] > 0.8
    assert r["b0_500"] < 0.05


def test_silence_hnr_is_nan(tmp_path: Path) -> None:
    sr = 22050
    x = np.zeros(int(0.5 * sr))
    p = _write_wav(tmp_path, "silence.wav", x, sr)

    r = mb.analyze_wav(p)

    assert np.isnan(r["hnr_median_db"])


def test_analyze_wav_reads_file_bytes_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """P2 修正 (review #262): decode (soundfile) と hash (sha256) を同一
    バイト列から導出する（Persistent Artifact Safety Gate 項目1「単一 read で
    parse + hash」）。`Path.read_bytes` の呼び出し回数が厳密に 1 回であることを
    直接検証する（TOCTOU 窓の排除を、実装詳細ではなく振る舞いとして enforce）。
    """
    sr = 22050
    t = np.arange(int(0.3 * sr)) / sr
    x = 0.4 * np.sin(2 * np.pi * 440.0 * t)
    p = _write_wav(tmp_path, "single_read.wav", x, sr)

    call_count = 0
    orig_read_bytes = Path.read_bytes

    def _counting_read_bytes(self):
        nonlocal call_count
        call_count += 1
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)

    r = mb.analyze_wav(p)

    assert call_count == 1
    assert r["sha256"] == hashlib_sha256_of(p)


def hashlib_sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_analyze_wav_sha256_matches_full_file_bytes(tmp_path: Path) -> None:
    """sha256 は実際にデコードしたのと同一バイト列（ファイル全体）から
    導出されていることを確認する（decode に使った BytesIO の中身と一致）。"""
    sr = 22050
    x = np.zeros(int(0.2 * sr))
    p = _write_wav(tmp_path, "match.wav", x, sr)
    r = mb.analyze_wav(p)
    assert r["sha256"] == hashlib_sha256_of(p)


def test_deterministic_repeat(tmp_path: Path) -> None:
    sr = 22050
    t = np.arange(int(0.5 * sr)) / sr
    x = 0.5 * np.sin(2 * np.pi * 330.0 * t)
    p = _write_wav(tmp_path, "det.wav", x, sr)

    r1 = mb.analyze_wav(p)
    r2 = mb.analyze_wav(p)

    for key, v1 in r1.items():
        v2 = r2[key]
        if isinstance(v1, float) and np.isnan(v1):
            assert np.isnan(v2)
        else:
            assert v1 == v2


# ---------------------------------------------------------------------------
# P1 修正 (review #262 R4・`r3789341847`): --out が入力 WAV と衝突する場合の拒否
# ---------------------------------------------------------------------------


def test_reject_output_collision_out_equals_input_wav_raises(tmp_path: Path) -> None:
    sr = 22050
    p = _write_wav(tmp_path, "donor.wav", np.zeros(int(0.1 * sr)), sr)
    with pytest.raises(mb.OutputCollisionError):
        mb._reject_output_collision(p, [p])


def test_reject_output_collision_out_equals_one_of_multiple_inputs_raises(tmp_path: Path) -> None:
    sr = 22050
    p1 = _write_wav(tmp_path, "a.wav", np.zeros(int(0.1 * sr)), sr)
    p2 = _write_wav(tmp_path, "b.wav", np.zeros(int(0.1 * sr)), sr)
    with pytest.raises(mb.OutputCollisionError):
        mb._reject_output_collision(p2, [p1, p2])


def test_reject_output_collision_unrelated_out_does_not_raise(tmp_path: Path) -> None:
    sr = 22050
    p = _write_wav(tmp_path, "donor.wav", np.zeros(int(0.1 * sr)), sr)
    out_path = tmp_path / "report.json"
    mb._reject_output_collision(out_path, [p])  # no raise


def test_reject_output_collision_symlinked_out_resolves_before_compare(tmp_path: Path) -> None:
    sr = 22050
    real_wav = _write_wav(tmp_path, "real.wav", np.zeros(int(0.1 * sr)), sr)
    alias = tmp_path / "alias.wav"
    alias.symlink_to(real_wav)
    with pytest.raises(mb.OutputCollisionError):
        mb._reject_output_collision(alias, [real_wav])


def test_reject_output_collision_missing_input_path_is_skipped(tmp_path: Path) -> None:
    out_path = tmp_path / "report.json"
    missing = tmp_path / "does_not_exist.wav"
    mb._reject_output_collision(out_path, [missing])  # no raise (input never existed)


def test_main_rejects_out_aliasing_input(tmp_path: Path, monkeypatch, capsys) -> None:
    """CLI 経由（`measure_bands.py donor.wav --out donor.wav`）でも fail-closed
    で拒否され、donor.wav が JSON で上書きされないことを確認する（実害の再現）。"""
    sr = 22050
    p = _write_wav(tmp_path, "donor.wav", np.zeros(int(0.1 * sr)), sr)
    before_bytes = p.read_bytes()
    monkeypatch.setattr(sys, "argv", ["measure_bands.py", str(p), "--out", str(p)])
    with pytest.raises(mb.OutputCollisionError):
        mb.main()
    assert p.read_bytes() == before_bytes  # donor.wav は無傷のまま


# ---------------------------------------------------------------------------
# P2 修正 (review #262 R4・`r3789341850`): 計測レポート JSON の atomic 公開
# ---------------------------------------------------------------------------


def test_atomic_write_text_writes_readable_content(tmp_path: Path) -> None:
    out_path = tmp_path / "report.json"
    mb._atomic_write_text('{"a": 1}\n', out_path)
    assert out_path.read_text(encoding="utf-8") == '{"a": 1}\n'
    # tempfile が残っていない（staging cleanup 確認）。
    assert list(tmp_path.glob("report.json.*.tmp")) == []


def test_atomic_write_text_no_partial_file_on_failure(tmp_path: Path, monkeypatch) -> None:
    """AGENTS.md Persistent Artifact Safety Gate 項目7「公開途中失敗の注入
    テスト」: staging への write が失敗しても最終 out_path に部分成果物を残さない。"""
    out_path = tmp_path / "report.json"

    class _BoomFile:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def write(self, *_args, **_kwargs):
            raise RuntimeError("simulated write failure")

    monkeypatch.setattr(mb.os, "fdopen", lambda *_a, **_k: _BoomFile())
    with pytest.raises(RuntimeError):
        mb._atomic_write_text('{"a": 1}\n', out_path)
    assert not out_path.exists()
    assert list(tmp_path.glob("report.json.*.tmp")) == []


def test_atomic_write_text_does_not_clobber_existing_output_on_failure(tmp_path: Path, monkeypatch) -> None:
    """公開直前まで既存の有効な出力を保持し、失敗時は破壊しない
    （staging + os.replace の atomicity）。"""
    out_path = tmp_path / "report.json"
    mb._atomic_write_text('{"a": 1}\n', out_path)
    before_bytes = out_path.read_bytes()

    class _BoomFile:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def write(self, *_args, **_kwargs):
            raise RuntimeError("simulated write failure")

    monkeypatch.setattr(mb.os, "fdopen", lambda *_a, **_k: _BoomFile())
    with pytest.raises(RuntimeError):
        mb._atomic_write_text('{"a": 2}\n', out_path)
    assert out_path.read_bytes() == before_bytes  # 旧ファイルが無傷のまま残る


def test_main_writes_out_atomically(tmp_path: Path, monkeypatch) -> None:
    sr = 22050
    p = _write_wav(tmp_path, "src.wav", np.zeros(int(0.1 * sr)), sr)
    out_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", ["measure_bands.py", str(p), "--out", str(out_path)])
    mb.main()
    assert out_path.exists()
    assert list(tmp_path.glob("report.json.*.tmp")) == []
