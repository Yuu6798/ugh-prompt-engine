"""TRANSITION_GT generator（設計正本 §4.2）: 2 つの定常 segment を厳密な
join time で接続する。amplitude step / phase jump / spectral-envelope switch /
crossfade × 3 severities × 2 duration classes。exact join time・投入
discontinuity magnitude を truth として生成時に記録する（`row.join_time_s` /
`row.discontinuity_magnitude`。値そのものは `fixtures/matrix.py` が構築時に
確定済み。本モジュールはそれを波形へ具現化するのみ）。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from voice_genesis.calibration.fixtures.generators import common

_DURATION_CLASS_S: dict[str, float] = {"short": 0.005, "long": 0.050}


def _steady_tone(f0_hz: float, sr_hz: int, n: int) -> np.ndarray:
    return common.harmonic_pulse_train(f0_hz, sr_hz, n)


def _alt_envelope(x: np.ndarray, sr_hz: int) -> np.ndarray:
    """spectral-envelope switch 用の代替スペクトル包絡（低域強調 lowpass）。"""
    nyquist = sr_hz / 2.0
    cutoff = min(1200.0, nyquist * 0.9) / nyquist
    sos = butter(4, cutoff, btype="lowpass", output="sos")
    return sosfiltfilt(sos, x)


def _core(row: object) -> np.ndarray:
    sr_hz = row.sr_hz
    f0_hz = row.f0_hz
    n = common.n_samples(row.duration_s, sr_hz)
    join_sample = int(round((row.join_time_s or (row.duration_s / 2.0)) * sr_hz))
    join_sample = max(1, min(join_sample, n - 1))
    mag = row.discontinuity_magnitude if row.discontinuity_magnitude is not None else 0.35

    base = _steady_tone(f0_hz, sr_hz, n)
    join_type = row.join_type

    if join_type == "amplitude-step":
        x = base.copy()
        x[join_sample:] *= 1.0 + mag
        return common.peak_normalize(x)

    if join_type == "phase-jump":
        t = np.arange(n, dtype=np.float64) / sr_hz
        x = base.copy()
        phase_shift = mag * np.pi
        second_half = np.zeros(n, dtype=np.float64)
        k = 1
        cutoff = 0.45 * sr_hz
        while k * f0_hz < cutoff:
            second_half += (1.0 / k) * np.sin(2.0 * np.pi * k * f0_hz * t + phase_shift)
            k += 1
        second_half = common.peak_normalize(second_half)
        x[join_sample:] = second_half[join_sample:]
        return common.peak_normalize(x)

    if join_type == "spectral-envelope-switch":
        alt = common.peak_normalize(_alt_envelope(base, sr_hz))
        x = base.copy()
        x[join_sample:] = alt[join_sample:] * (1.0 + mag)
        return common.peak_normalize(x)

    if join_type == "crossfade":
        window_s = _DURATION_CLASS_S.get(row.duration_class or "long", 0.050)
        w = max(1, min(int(round(window_s * sr_hz)), n - join_sample))
        alt = common.peak_normalize(_steady_tone(f0_hz * (1.0 + mag), sr_hz, n))
        x = base.copy()
        ramp = np.linspace(0.0, 1.0, w)
        x[join_sample : join_sample + w] = (1.0 - ramp) * base[
            join_sample : join_sample + w
        ] + ramp * alt[join_sample : join_sample + w]
        x[join_sample + w :] = alt[join_sample + w :]
        return common.peak_normalize(x)

    raise ValueError(f"unknown TRANSITION_GT join_type: {join_type!r}")


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = _core(row)
    return common.finalize(core, row, rng)
