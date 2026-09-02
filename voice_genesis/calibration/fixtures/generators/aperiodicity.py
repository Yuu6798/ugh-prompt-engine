"""APERIODICITY_GT generator（設計正本 §4.2）: `injected_noise_fraction`
（power fraction）が truth。harmonic complex + noise を宣言 fraction で混合する。
bandwise variants はノイズ成分のみを帯域フィルタする。

混合則: `h`/`nz` をそれぞれ単位パワーへ正規化した上で
`mixed = sqrt(1-fraction)*h + sqrt(1-fraction を除く)*sqrt(fraction)*nz`
… ではなく単純に振幅重み `sqrt(1-fraction)` / `sqrt(fraction)` で線形結合する
（`fraction` = mixed 信号中のノイズ成分パワー比、という直接的な定義）。

U_GT 寄与根拠: truth は注入時に指定した power fraction そのもの。D4C 等の
下流推定値との absolute equality を主張しない（設計正本 §4.2）。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from voice_genesis.calibration.fixtures.generators import common


def _band_filter(x: np.ndarray, sr_hz: int, band: str | None) -> np.ndarray:
    if band is None or band == "broadband":
        return x
    nyquist = sr_hz / 2.0
    if band == "0-3kHz":
        sos = butter(4, min(3000.0, nyquist * 0.999) / nyquist, btype="lowpass", output="sos")
    elif band == "3-6kHz":
        hi = min(6000.0, nyquist * 0.999)
        sos = butter(4, [3000.0 / nyquist, hi / nyquist], btype="bandpass", output="sos")
    elif band == "6kHz-Nyquist":
        lo = min(6000.0, nyquist * 0.999)
        sos = butter(4, lo / nyquist, btype="highpass", output="sos")
    else:
        raise ValueError(f"unknown aperiodicity band: {band!r}")
    return sosfiltfilt(sos, x)


def _core(row: object, rng: np.random.Generator) -> np.ndarray:
    sr_hz = row.sr_hz
    n = common.n_samples(row.duration_s, sr_hz)
    harmonic = common.harmonic_pulse_train(row.f0_hz, sr_hz, n)
    noise = rng.standard_normal(n)
    noise = _band_filter(noise, sr_hz, row.bandwise_band)

    h_rms = common.rms(harmonic) or 1.0
    nz_rms = common.rms(noise) or 1.0
    h = harmonic / h_rms
    nz = noise / nz_rms

    fraction = float(np.clip(row.injected_noise_fraction, 0.0, 1.0))
    mixed = np.sqrt(1.0 - fraction) * h + np.sqrt(fraction) * nz
    return common.peak_normalize(mixed)


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = _core(row, rng)
    return common.finalize(core, row, rng)
