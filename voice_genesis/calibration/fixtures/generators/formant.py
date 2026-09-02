"""FORMANT_GT generator（設計正本 §4.2）: resonator code path を共有しない
2 実装。

1. **cascade**: harmonic pulse train 励起を pole ごとの 2 次 IIR 共振器
   （`scipy.signal.lfilter` の時間領域差分方程式）へ直列適用する。
2. **additive**: 同じ pole/bandwidth から閉形式で `|H(f)|`（2 次共振器の
   解析的周波数応答の大きさ）を各高調波で評価し、振幅付き正弦波の和として
   直接合成する（`lfilter` を一切呼ばない）。

pole 半径 `r = exp(-pi*bandwidth/sr)`、中心角 `theta = 2*pi*pole_hz/sr` は
両実装で共有する物理定義（標準的な 2 次デジタル共振器の帯域幅→極半径換算式）
だが、**信号を生成する実行コードパス自体は独立**（cascade は時間領域漸化式、
additive は周波数領域解析式 → 正弦波加算）であり、設計正本の要求
「resonator code path を共有しない」を満たす。

U_GT 寄与根拠: truth は pole frequency/bandwidth の解析的宣言値そのもの
（meter 側の推定を経由しない）。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from voice_genesis.calibration.fixtures.generators import common


def _pole_radius(bandwidth_hz: float, sr_hz: int) -> float:
    return float(np.exp(-np.pi * bandwidth_hz / sr_hz))


def _resonator_h_mag(f_eval: np.ndarray, pole_hz: float, bandwidth_hz: float, sr_hz: int) -> np.ndarray:
    """2 次共振器 `H(z) = (1-r) / (1 - 2r*cos(theta)*z^-1 + r^2*z^-2)` の
    `|H(f)|` を解析的に評価する（additive 実装専用。`lfilter` を使わない）。
    """
    r = _pole_radius(bandwidth_hz, sr_hz)
    theta = 2.0 * np.pi * pole_hz / sr_hz
    z_inv = np.exp(-1j * 2.0 * np.pi * f_eval / sr_hz)
    denom = 1.0 - 2.0 * r * np.cos(theta) * z_inv + (r**2) * (z_inv**2)
    h = (1.0 - r) / denom
    return np.abs(h)


def _cascade_core(row: object) -> np.ndarray:
    sr_hz = row.sr_hz
    n = common.n_samples(row.duration_s, sr_hz)
    excitation = common.harmonic_pulse_train(row.f0_hz, sr_hz, n)
    y = excitation
    for pole_hz in row.pole_freqs_hz:
        r = _pole_radius(row.bandwidth_hz, sr_hz)
        theta = 2.0 * np.pi * pole_hz / sr_hz
        b = [1.0 - r]
        a = [1.0, -2.0 * r * np.cos(theta), r * r]
        y = lfilter(b, a, y)
    return common.peak_normalize(y)


def _additive_core(row: object) -> np.ndarray:
    sr_hz = row.sr_hz
    f0_hz = row.f0_hz
    n = common.n_samples(row.duration_s, sr_hz)
    t = np.arange(n, dtype=np.float64) / sr_hz
    x = np.zeros(n, dtype=np.float64)
    k = 1
    cutoff = 0.45 * sr_hz
    while k * f0_hz < cutoff:
        f = k * f0_hz
        mag = 1.0
        for pole_hz in row.pole_freqs_hz:
            mag *= _resonator_h_mag(np.array([f]), pole_hz, row.bandwidth_hz, sr_hz)[0]
        x += (1.0 / k) * mag * np.sin(2.0 * np.pi * f * t)
        k += 1
    return common.peak_normalize(x)


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        impl = row.generator_impl or "cascade"
        core = _cascade_core(row) if impl == "cascade" else _additive_core(row)
    return common.finalize(core, row, rng)
