"""TILT_GT generator（設計正本 §4.2）: 宣言 harmonic 範囲上の dB/oct slope
1 定義に凍結。

[UNDERSPEC-CAL-B11] slope 定義の凍結: `A_k[dB] = slope_db_per_oct * log2(k)`
（`k` = 高調波次数, 基本波 `k=1` を 0dB 基準とする）。これは "octave" を
高調波次数の log2（`f_k = k*f0` なので `log2(f_k/f0) = log2(k)`）で測る、
最も単純な閉形式の 1 定義である。回帰ルーチンは一切使わない（meter 側の
tilt 推定と共有されない生成則）。

U_GT 寄与根拠: truth は slope の解析的宣言値。生成振幅は上式から直接計算され、
推定・フィッティングを経由しない。
"""

from __future__ import annotations

import numpy as np

from voice_genesis.calibration.fixtures.generators import common


def _core(row: object) -> np.ndarray:
    sr_hz = row.sr_hz
    f0_hz = row.f0_hz
    n = common.n_samples(row.duration_s, sr_hz)
    t = np.arange(n, dtype=np.float64) / sr_hz
    x = np.zeros(n, dtype=np.float64)
    k = 1
    cutoff = 0.45 * sr_hz
    while k * f0_hz < cutoff:
        amp_db = row.slope_db_per_oct * np.log2(k)
        amp = 10.0 ** (amp_db / 20.0)
        x += amp * np.sin(2.0 * np.pi * k * f0_hz * t)
        k += 1
    return common.peak_normalize(x)


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = _core(row)
    return common.finalize(core, row, rng)
