"""IDENTITY_CAUSAL_SWEEP generator（設計正本 §4.2, §12）: 4 synthetic founders
（distinct F0/formant-set/tilt parameter bundle）× 3 claim-critical traits ×
`delta in {-2..+2}` generator units。content/duration/SNR は founder 内で
固定し、`trait` の指定する 1 軸だけを `delta` に応じて row baseline から
摂動する（one-factor causal sweep, §12）。

trait -> 物理量換算（`fixtures/axes.py` の
`IDENTITY_TRAIT_UNIT_{CENTS,FORMANT_SCALE,TILT_DB}` を参照。§12 は物理 scalar
GT を主張しないため、これらは construct validation 用の内部一貫した合成規則で
あり ABSOLUTE claim の対象ではない):

- ``F0``: `f0 *= 2 ** (delta * 5 cents / 1200)`
- ``FORMANT_SHIFT``: 各 pole 周波数 `*= 1 + 0.02 * delta`
- ``TILT_SLOPE``: `tilt_db_per_oct += 1.0 * delta`

`row.f0_hz` は confound / boundary 行で founder F0 を上書きしうる凍結済み
row-level 条件であるため、常に synthesized F0 の baseline として優先する。
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from voice_genesis.calibration.fixtures.axes import (
    IDENTITY_FOUNDERS,
    IDENTITY_TRAIT_UNIT_CENTS,
    IDENTITY_TRAIT_UNIT_FORMANT_SCALE,
    IDENTITY_TRAIT_UNIT_TILT_DB,
)
from voice_genesis.calibration.fixtures.generators import common


def _effective_params(row: object) -> tuple[float, tuple[float, ...], float, float]:
    founder = IDENTITY_FOUNDERS[row.founder_id]
    # Truth-core rows carry the founder F0 in row.f0_hz, while confound / boundary
    # rows may deliberately override it (e.g. high-F0 interactions or G2/C5 probes).
    # The canonical row value is therefore the authoritative baseline.  Fall back
    # to the founder only for defensive compatibility with row-like test doubles.
    row_f0_hz = getattr(row, "f0_hz", None)
    f0_hz = float(founder["f0_hz"] if row_f0_hz is None else row_f0_hz)
    poles = tuple(float(p) for p in founder["pole_freqs_hz"])
    bandwidth_hz = float(founder["bandwidth_hz"])
    tilt = float(founder["tilt_db_per_oct"])

    delta = row.delta or 0
    if row.trait == "F0":
        f0_hz = f0_hz * (2.0 ** (delta * IDENTITY_TRAIT_UNIT_CENTS / 1200.0))
    elif row.trait == "FORMANT_SHIFT":
        scale = 1.0 + IDENTITY_TRAIT_UNIT_FORMANT_SCALE * delta
        poles = tuple(p * scale for p in poles)
    elif row.trait == "TILT_SLOPE":
        tilt = tilt + IDENTITY_TRAIT_UNIT_TILT_DB * delta
    else:
        raise ValueError(f"unknown IDENTITY trait: {row.trait!r}")
    return f0_hz, poles, bandwidth_hz, tilt


def _core(row: object) -> np.ndarray:
    f0_hz, poles, bandwidth_hz, tilt = _effective_params(row)
    sr_hz = row.sr_hz
    n = common.n_samples(row.duration_s, sr_hz)
    t = np.arange(n, dtype=np.float64) / sr_hz

    x = np.zeros(n, dtype=np.float64)
    k = 1
    cutoff = 0.45 * sr_hz
    while k * f0_hz < cutoff:
        amp = 10.0 ** (tilt * np.log2(k) / 20.0)
        x += amp * np.sin(2.0 * np.pi * k * f0_hz * t)
        k += 1
    x = common.peak_normalize(x)

    for pole_hz in poles:
        r = float(np.exp(-np.pi * bandwidth_hz / sr_hz))
        theta = 2.0 * np.pi * pole_hz / sr_hz
        b = [1.0 - r]
        a = [1.0, -2.0 * r * np.cos(theta), r * r]
        x = lfilter(b, a, x)
    return common.peak_normalize(x)


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    f0_hz, _poles, _bw, _tilt = _effective_params(row)
    core = common.negative_control_core(row, rng, n, f0_hz)
    if core is None:
        core = _core(row)
    return common.finalize(core, row, rng)
