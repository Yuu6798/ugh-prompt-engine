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
