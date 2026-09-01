"""M4-LOCAL-PROMINENCE algorithm family（設計正本 §8。全 M4 候補は
DIAGNOSTIC_ONLY 上限で閉じる = §16 の裁定）。

スペクトル包絡（移動平均による平滑化）上で `scipy.signal.find_peaks` の
`prominence` 引数を使い、宣言した閾値 (dB) 以上のトポグラフィカル
prominence を持つローカルピークを検出する。最も低域のピークの中心周波数を
resonance 推定値として報告する。

[UNDERSPEC-CAL-C04] 設計正本は包絡平滑化の具体的アルゴリズムを規定しない
（`envelope 平滑帯域` という帯域幅パラメタのみ凍結）。最も単純な選択として
移動平均（box filter、帯域幅を Hz からビン数へ換算した窓長）を採用した。
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from scipy.signal import find_peaks

from ... import vocab
from ..adapter import MeterOutput


def _analysis_window(signal: np.ndarray, start_frac: float = 0.15, end_frac: float = 0.9) -> np.ndarray:
    n = len(signal)
    a = int(n * start_frac)
    b = int(n * end_frac)
    return signal[a:b] if b > a else signal


def _smoothed_envelope_db(signal: np.ndarray, sr: int, smoothing_bandwidth_hz: float) -> tuple[np.ndarray, np.ndarray]:
    analysis = _analysis_window(np.asarray(signal, dtype=float))
    window = np.hanning(len(analysis))
    spec = np.abs(np.fft.rfft(analysis * window))
    freqs = np.fft.rfftfreq(len(analysis), d=1.0 / sr)
    eps = 1e-12
    db = 20.0 * np.log10(spec + eps)

    bin_hz = freqs[1] - freqs[0] if len(freqs) > 1 else 1.0
    box = max(int(round(smoothing_bandwidth_hz / bin_hz)), 1)
    if box > 1:
        kernel = np.ones(box) / box
        db = np.convolve(db, kernel, mode="same")
    return freqs, db


def resonance_peaks(
    signal: np.ndarray,
    sr: int,
    *,
    prominence_db: float,
    smoothing_bandwidth_hz: float,
    band_lo: float = 200.0,
    band_hi: float = 5000.0,
) -> list[float]:
    """`prominence_db` 以上のトポグラフィカル prominence を持つピーク周波数（昇順）。"""
    freqs, db = _smoothed_envelope_db(signal, sr, smoothing_bandwidth_hz)
    lo_idx = int(np.searchsorted(freqs, band_lo))
    hi_idx = int(np.searchsorted(freqs, band_hi))
    if hi_idx <= lo_idx + 2:
        return []
    segment = db[lo_idx:hi_idx]
    peak_idx, _props = find_peaks(segment, prominence=prominence_db)
    return sorted(float(freqs[lo_idx + i]) for i in peak_idx)


def measure(signal: np.ndarray, sr: int, params: Mapping[str, object]) -> MeterOutput:
    prominence_db = float(params["prominence_db"])
    smoothing_bandwidth_hz = float(params["smoothing_bandwidth_hz"])
    peaks = resonance_peaks(
        signal, sr, prominence_db=prominence_db, smoothing_bandwidth_hz=smoothing_bandwidth_hz
    )
    if not peaks:
        return MeterOutput(missing_reason=vocab.MissingReason.OUTPUT_MISSING)
    return MeterOutput(values={"center_hz": peaks[0]}, diagnostics={"n_peaks_found": len(peaks)})
