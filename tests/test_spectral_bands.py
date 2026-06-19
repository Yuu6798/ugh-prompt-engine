"""Q1-5 additive spectral/tempo/HPSS physical sensors."""
from __future__ import annotations

import numpy as np
import pytest

from svp_rpe.io.audio_loader import AudioData, AudioMetadata
from svp_rpe.rpe.extractor import extract_physical
from svp_rpe.rpe.physical_features import (
    compute_hpss_ratio,
    compute_spectral_bands,
    compute_spectral_profile,
    compute_tempo_stability,
)


SR = 22050


def _tone(
    freq: float,
    *,
    duration: float = 3.0,
    amp: float = 0.5,
    sr: int = SR,
) -> np.ndarray:
    t = np.linspace(0.0, duration, int(sr * duration), endpoint=False)
    return (amp * np.sin(2.0 * np.pi * freq * t)).astype(np.float32)


def _broadband_signal() -> np.ndarray:
    duration = 3.0
    t = np.linspace(0.0, duration, int(SR * duration), endpoint=False)
    low = 0.45 * np.sin(2.0 * np.pi * 55.0 * t)
    rng = np.random.default_rng(seed=42)
    high = np.zeros_like(t)
    for freq, phase in zip(np.linspace(4500.0, 10000.0, 64), rng.uniform(0.0, 2.0 * np.pi, 64)):
        high += 0.025 * np.sin(2.0 * np.pi * freq * t + phase)
    y = low + high
    return (0.8 * y / np.max(np.abs(y))).astype(np.float32)


def _click_track(*, duration: float = 8.0, bpm: float = 120.0) -> np.ndarray:
    y = np.zeros(int(SR * duration), dtype=np.float32)
    interval = 60.0 / bpm
    pulse = np.hanning(64).astype(np.float32)
    for time in np.arange(0.0, duration, interval):
        start = int(time * SR)
        end = min(start + pulse.size, y.size)
        y[start:end] += pulse[: end - start]
    return y


def _audio_data(y: np.ndarray) -> AudioData:
    duration = len(y) / SR
    return AudioData(
        metadata=AudioMetadata(
            file_path="synthetic.wav",
            duration_sec=round(duration, 4),
            sample_rate=SR,
            channels=1,
            format="wav",
        ),
        y_mono=y.astype(np.float32, copy=False),
        y_stereo=None,
        sr=SR,
    )


def _band_sum(bands: object) -> float:
    return sum(
        getattr(bands, field)
        for field in (
            "sub_bass",
            "bass",
            "low_mid",
            "mid",
            "high_mid",
            "presence",
            "brilliance",
        )
    )


def test_magnitude_spectral_bands_sum_to_one() -> None:
    bands = compute_spectral_bands(_broadband_signal(), SR)

    assert _band_sum(bands) == pytest.approx(1.0, abs=0.02)
    assert bands.presence + bands.brilliance > 0.0


def test_spectral_bands_zero_profile_for_silence() -> None:
    bands = compute_spectral_bands(np.zeros(4096, dtype=np.float32), SR)

    assert _band_sum(bands) == 0.0


def test_magnitude_high_frequency_exceeds_legacy_power_high_ratio() -> None:
    y = _broadband_signal()
    magnitude_bands = compute_spectral_bands(y, SR)
    legacy_power = compute_spectral_profile(y, SR)

    magnitude_high = magnitude_bands.presence + magnitude_bands.brilliance

    assert magnitude_high > legacy_power.high_ratio + 0.2
    assert magnitude_high > legacy_power.high_ratio * 2.0


def test_brilliance_band_is_clipped_to_nyquist() -> None:
    sr = 12000
    bands = compute_spectral_bands(_tone(5500.0, sr=sr), sr)

    assert bands.presence > 0.9
    assert bands.brilliance == 0.0
    assert _band_sum(bands) == pytest.approx(1.0, abs=0.02)


def test_spectral_band_denominator_excludes_unreported_bins() -> None:
    sr = 44100
    audible_bin = 46 * sr / 2048
    out_of_band_bin = 1000 * sr / 2048
    y = _tone(audible_bin, sr=sr) + _tone(out_of_band_bin, sr=sr, amp=0.8)

    bands = compute_spectral_bands(y, sr)

    assert _band_sum(bands) == pytest.approx(1.0, abs=0.02)
    assert bands.mid > 0.95
    assert bands.brilliance < 0.05


def test_tempo_stability_click_track_is_finite_and_low() -> None:
    stability = compute_tempo_stability(_click_track(), SR)

    assert stability is not None
    assert stability >= 0.0
    assert stability < 5.0


def test_tempo_stability_short_or_empty_input_is_none() -> None:
    assert compute_tempo_stability(np.array([], dtype=np.float32), SR) is None
    assert compute_tempo_stability(np.zeros(16, dtype=np.float32), SR) is None


def test_hpss_ratios_sum_to_one_and_pure_tone_is_harmonic() -> None:
    harmonic_ratio, percussive_ratio = compute_hpss_ratio(_tone(440.0))

    assert harmonic_ratio is not None
    assert percussive_ratio is not None
    assert harmonic_ratio + percussive_ratio == pytest.approx(1.0, abs=0.02)
    assert harmonic_ratio > 0.8


def test_hpss_ratio_for_silence_is_none() -> None:
    assert compute_hpss_ratio(np.zeros(4096, dtype=np.float32)) == (None, None)


def test_extract_physical_new_fields_are_deterministic() -> None:
    y = _click_track(duration=6.0) + _tone(440.0, duration=6.0, amp=0.1)
    audio = _audio_data(y)

    first, _, _ = extract_physical(audio)
    second, _, _ = extract_physical(audio)

    assert first.spectral_bands == second.spectral_bands
    assert first.tempo_stability_std == second.tempo_stability_std
    assert first.harmonic_ratio == second.harmonic_ratio
    assert first.percussive_ratio == second.percussive_ratio
    assert first.spectral_bands is not None
    assert first.tempo_stability_std is not None
    assert first.harmonic_ratio is not None
    assert first.percussive_ratio is not None
    assert first.model_dump()["spectral_bands"] == first.spectral_bands.model_dump()
