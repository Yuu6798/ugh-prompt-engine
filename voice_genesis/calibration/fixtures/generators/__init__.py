"""family 別決定論 generator（IMPLEMENTATION_MAP §2 module table）。

各 `render(row, rng) -> np.ndarray[int16]` は `(row, secret から導出された
Generator)` の純粋関数。float64 内部演算 → `common.py` で PCM 16-bit へ量子化
する（byte-determinism の最終境界）。
"""

from __future__ import annotations

import numpy as np

from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.generators import (
    aperiodicity,
    common,
    f0_control,
    formant,
    identity_sweep,
    resonance,
    tilt,
    transition,
)

_DISPATCH = {
    FixtureFamily.F0_CONTROL.value: f0_control.render,
    FixtureFamily.FORMANT_GT.value: formant.render,
    FixtureFamily.TILT_GT.value: tilt.render,
    FixtureFamily.APERIODICITY_GT.value: aperiodicity.render,
    FixtureFamily.RESONANCE_GT.value: resonance.render,
    FixtureFamily.TRANSITION_GT.value: transition.render,
    FixtureFamily.IDENTITY_CAUSAL_SWEEP.value: identity_sweep.render,
}


def render_row(row: object, rng: np.random.Generator) -> np.ndarray:
    """`row.family` に応じた generator へ配線する（PCM int16 を返す）。"""
    try:
        fn = _DISPATCH[row.family]
    except KeyError as exc:
        raise ValueError(f"generators: unknown fixture family {row.family!r}") from exc
    return fn(row, rng)


__all__ = [
    "aperiodicity",
    "common",
    "f0_control",
    "formant",
    "identity_sweep",
    "render_row",
    "resonance",
    "tilt",
    "transition",
]
