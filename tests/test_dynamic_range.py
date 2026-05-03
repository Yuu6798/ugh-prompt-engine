"""tests/test_dynamic_range.py — RMS-based dynamic range descriptor."""
from __future__ import annotations

import numpy as np
import pytest

from svp_rpe.rpe.physical_features import compute_dynamic_range_db


SR = 22_050


def _sine(amplitude: float, freq_hz: float, duration_s: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0.0, duration_s, int(sr * duration_s), endpoint=False)
    return (amplitude * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float32)


def test_empty_input_returns_none() -> None:
    assert compute_dynamic_range_db(np.array([], dtype=np.float32), SR) is None


def test_constant_amplitude_yields_near_zero_db() -> None:
    """A steady sine at constant amplitude has ~no dynamic range."""
    y = _sine(amplitude=0.3, freq_hz=440.0, duration_s=2.0)
    dr = compute_dynamic_range_db(y, SR)
    assert dr is not None
    assert abs(dr) < 1.0  # tight tolerance: should be essentially flat


def test_loud_quiet_alternation_has_high_dynamic_range() -> None:
    """A signal alternating between loud and quiet sections has large DR."""
    loud = _sine(amplitude=0.5, freq_hz=440.0, duration_s=2.0)
    quiet = _sine(amplitude=0.05, freq_hz=440.0, duration_s=2.0)  # 20 dB lower
    y = np.concatenate([loud, quiet, loud, quiet])
    dr = compute_dynamic_range_db(y, SR)
    assert dr is not None
    # Loud / quiet ratio is 0.5 / 0.05 = 10x = 20 dB; DR should approach this.
    assert dr > 15.0


def test_silence_does_not_explode() -> None:
    """Pure silence is floored to avoid log(0); result must be finite."""
    y = np.zeros(int(SR * 1.0), dtype=np.float32)
    dr = compute_dynamic_range_db(y, SR)
    assert dr is not None
    assert dr == pytest.approx(0.0, abs=0.1)


def test_dynamic_signal_exceeds_compressed_signal() -> None:
    """Discrimination: a dynamic signal must score higher than a compressed one."""
    compressed = _sine(amplitude=0.3, freq_hz=440.0, duration_s=4.0)
    dynamic = np.concatenate([
        _sine(amplitude=0.5, freq_hz=440.0, duration_s=1.0),
        _sine(amplitude=0.05, freq_hz=440.0, duration_s=1.0),
        _sine(amplitude=0.5, freq_hz=440.0, duration_s=1.0),
        _sine(amplitude=0.05, freq_hz=440.0, duration_s=1.0),
    ])
    dr_compressed = compute_dynamic_range_db(compressed, SR)
    dr_dynamic = compute_dynamic_range_db(dynamic, SR)
    assert dr_compressed is not None
    assert dr_dynamic is not None
    assert dr_dynamic > dr_compressed + 5.0


def test_deterministic_output() -> None:
    """Same input → same output."""
    y = _sine(amplitude=0.4, freq_hz=440.0, duration_s=1.5)
    assert compute_dynamic_range_db(y, SR) == compute_dynamic_range_db(y, SR)
