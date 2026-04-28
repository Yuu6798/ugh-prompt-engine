"""tests/test_loudness.py — ITU-R BS.1770 loudness measurement validation.

Verifies that `compute_loudness` matches the ITU-R BS.1770 reference signal
specification: a 1 kHz sine at -20 dBFS (mono) measures -23.045 LUFS.
"""
from __future__ import annotations

import numpy as np
import pytest

from svp_rpe.rpe.physical_features import compute_loudness


SR = 48_000
ITU_REFERENCE_LUFS = -23.045  # 1 kHz / -20 dBFS / mono per BS.1770-4


def _generate_sine(amplitude: float, freq_hz: float, duration_s: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0.0, duration_s, int(sr * duration_s), endpoint=False)
    return (amplitude * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float64)


def test_itu_reference_signal_within_half_lu() -> None:
    """1 kHz sine at -20 dBFS / 30s mono must be -23.045 ± 0.5 LUFS."""
    signal = _generate_sine(amplitude=0.1, freq_hz=1000.0, duration_s=30.0)
    lufs, true_peak = compute_loudness(signal, SR)

    assert lufs is not None, "LUFS must be computed for canonical reference signal"
    assert abs(lufs - ITU_REFERENCE_LUFS) <= 0.5, (
        f"LUFS {lufs} drifted from ITU reference {ITU_REFERENCE_LUFS} by >0.5 LU"
    )
    # -20 dBFS sine peaks at amplitude 0.1; true peak should land near -20 dB.
    assert true_peak is not None
    assert -20.5 <= true_peak <= -19.5, f"true peak {true_peak} dBFS outside ±0.5 of -20"


def test_short_audio_returns_none() -> None:
    """Audio shorter than the gating block (0.4 s) cannot be measured."""
    short = _generate_sine(amplitude=0.1, freq_hz=1000.0, duration_s=0.2)
    lufs, true_peak = compute_loudness(short, SR)
    assert lufs is None
    assert true_peak is None


def test_silence_returns_none_lufs() -> None:
    """Digital silence yields LUFS = -inf which we surface as None."""
    silent = np.zeros(int(SR * 1.0), dtype=np.float64)
    lufs, true_peak = compute_loudness(silent, SR)
    assert lufs is None
    assert true_peak is None


def test_louder_signal_has_higher_lufs() -> None:
    """Sanity: doubling amplitude should raise LUFS by ~6 dB (sine input)."""
    quiet = _generate_sine(amplitude=0.1, freq_hz=1000.0, duration_s=10.0)
    loud = _generate_sine(amplitude=0.2, freq_hz=1000.0, duration_s=10.0)

    lufs_q, _ = compute_loudness(quiet, SR)
    lufs_l, _ = compute_loudness(loud, SR)
    assert lufs_q is not None and lufs_l is not None
    delta = lufs_l - lufs_q
    assert 5.5 <= delta <= 6.5, f"expected ~6 dB rise, got {delta:.2f}"


@pytest.mark.parametrize(
    ("amplitude", "expected_true_peak"),
    [(0.1, -20.0), (0.5, -6.0), (0.7079, -3.0)],
)
def test_true_peak_matches_amplitude(amplitude: float, expected_true_peak: float) -> None:
    """True peak (4x oversampled) of a sine matches its amplitude in dBFS."""
    signal = _generate_sine(amplitude=amplitude, freq_hz=1000.0, duration_s=2.0)
    _, true_peak = compute_loudness(signal, SR)
    assert true_peak is not None
    assert abs(true_peak - expected_true_peak) <= 0.5
