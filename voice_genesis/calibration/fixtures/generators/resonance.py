"""RESONANCE_GT generator（設計正本 §4.2）: 宣言 pole/Q resonator を broadband
励起（white noise）に適用し、broadband floor に対する declared prominence
（dB）を実現する。truth = center/bandwidth/prominence。M3（formant）とは
異なる出力 construct を宣言（§4.2）— broadband 励起上の単一ピークであり、
harmonic pulse train 励起の pole/zero 構造（FORMANT_GT）とは生成経路が異なる。

prominence 実現則: floor（white noise）と peak 成分（`scipy.signal.iirpeak`
を broadband white noise に適用したもの）を、`peak_rms / floor_rms` が
`10**(prominence_db/20)` になるようスケールしてから加算する。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import iirpeak, lfilter

from voice_genesis.calibration.fixtures.generators import common


def _core(row: object, rng: np.random.Generator) -> np.ndarray:
    sr_hz = row.sr_hz
    n = common.n_samples(row.duration_s, sr_hz)
    floor = rng.standard_normal(n)

    center = row.center_hz
    bandwidth = row.resonance_bandwidth_hz
    nyquist = sr_hz / 2.0
    w0 = float(np.clip(center / nyquist, 1e-4, 0.999))
    q = max(center / bandwidth, 0.5)
    b, a = iirpeak(w0, q)
    peak_component = lfilter(b, a, rng.standard_normal(n))

    floor_rms = common.rms(floor) or 1e-9
    peak_rms = common.rms(peak_component) or 1e-9
    target_ratio = 10.0 ** (row.prominence_db / 20.0)
    scaled_peak = peak_component * (target_ratio * floor_rms / peak_rms)

    mixed = floor + scaled_peak
    return common.peak_normalize(mixed)


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = _core(row, rng)
    return common.finalize(core, row, rng)
