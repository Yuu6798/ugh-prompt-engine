"""F0_CONTROL generator（設計正本 §4.2）: 解析的 sinusoid（PURE_SINE negative
control 用）+ band-limited harmonic pulse train（truth/confound/boundary 用）。
truth = 宣言 F0。

U_GT 寄与根拠: pulse train の基本周波数は解析的に指定した `f0_hz` そのもの
（推定・回帰を経ない）。PCM 量子化誤差以外の truth 不確かさ源を持たないため
`U_GT` は量子化半ステップ由来の最小値で保守上限化できる。
"""

from __future__ import annotations

import numpy as np

from voice_genesis.calibration.fixtures.generators import common


def render(row: object, rng: np.random.Generator) -> np.ndarray:
    n = common.n_samples(row.duration_s, row.sr_hz)
    core = common.negative_control_core(row, rng, n, row.f0_hz)
    if core is None:
        core = common.harmonic_pulse_train(row.f0_hz, row.sr_hz, n)
    return common.finalize(core, row, rng)
