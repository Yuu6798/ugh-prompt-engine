"""Q3 validation tests for per-stem reconstruction and BPM alignment."""
from __future__ import annotations

import numpy as np
import pytest

from svp_rpe.eval.stem_validation import (
    DEFAULT_STEM_RESIDUAL_THRESHOLD,
    validate_stem_bpm_alignment,
    validate_stem_reconstruction,
)
from svp_rpe.io.audio_loader import AudioData, AudioMetadata
from svp_rpe.io.source_separator import REQUIRED_STEMS, StemBundle
from svp_rpe.rpe.extractor import extract_physical
from svp_rpe.rpe.models import PhysicalRPE, SectionMarker, SpectralProfile

pytestmark = [
    pytest.mark.filterwarnings("ignore:Trying to estimate tuning.*:UserWarning"),
    pytest.mark.filterwarnings("ignore:n_fft=.*too large.*:UserWarning"),
]


def _pulsed_sine(
    *,
    frequency_hz: float,
    amplitude: float,
    sample_rate: int,
    duration_sec: float,
    bpm: float,
) -> np.ndarray:
    t = np.linspace(0.0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    y = np.zeros_like(t, dtype=np.float32)
    beat_period_sec = 60.0 / bpm
    burst_samples = int(0.05 * sample_rate)
    burst_t = np.arange(burst_samples) / sample_rate
    burst_envelope = np.exp(-np.linspace(0.0, 6.0, burst_samples))
    burst = np.sin(2 * np.pi * frequency_hz * burst_t) * burst_envelope
    burst[0] = 1.0
    burst[1:20] += 0.5

    for beat_sec in np.arange(0.5, duration_sec, beat_period_sec):
        start = int(beat_sec * sample_rate)
        end = min(start + burst_samples, y.size)
        if end > start:
            y[start:end] += amplitude * burst[: end - start].astype(np.float32)
    return y


def _synthetic_stem_fixture() -> tuple[AudioData, StemBundle]:
    sample_rate = 22050
    duration_sec = 8.0
    bpm = 120.0
    stems = {
        "vocals": _pulsed_sine(
            frequency_hz=440.0,
            amplitude=0.12,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            bpm=bpm,
        ),
        "drums": _pulsed_sine(
            frequency_hz=160.0,
            amplitude=0.18,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            bpm=bpm,
        ),
        "bass": _pulsed_sine(
            frequency_hz=60.0,
            amplitude=0.20,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            bpm=bpm,
        ),
        "other": _pulsed_sine(
            frequency_hz=660.0,
            amplitude=0.10,
            sample_rate=sample_rate,
            duration_sec=duration_sec,
            bpm=bpm,
        ),
    }
    mix = np.sum(np.stack(tuple(stems.values()), axis=0), axis=0).astype(np.float32)
    audio = AudioData(
        metadata=AudioMetadata(
            file_path="synthetic-stem-fixture.wav",
            duration_sec=duration_sec,
            sample_rate=sample_rate,
            channels=1,
            format="synthetic",
        ),
        y_mono=mix,
        y_stereo=None,
        sr=sample_rate,
    )
    bundle = StemBundle(
        source_path=audio.metadata.file_path,
        model_name="synthetic-stems",
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        stems=stems,
    )
    return audio, bundle


def _minimal_physical() -> PhysicalRPE:
    return PhysicalRPE(
        bpm=120.0,
        duration_sec=1.0,
        sample_rate=22050,
        structure=[SectionMarker(label="Full", start_sec=0.0, end_sec=1.0)],
        rms_mean=0.1,
        peak_amplitude=0.2,
        crest_factor=2.0,
        active_rate=1.0,
        valley_depth=0.0,
        thickness=0.5,
        spectral_centroid=440.0,
        spectral_profile=SpectralProfile(
            centroid=440.0,
            low_ratio=0.2,
            mid_ratio=0.7,
            high_ratio=0.1,
            brightness=0.1,
        ),
        onset_density=2.0,
    )


def test_synthetic_stem_sum_residual_passes_q3_threshold() -> None:
    audio, bundle = _synthetic_stem_fixture()

    result = validate_stem_reconstruction(audio, bundle)

    assert result.passed
    assert result.compared_samples == audio.y_mono.size
    assert result.length_delta_samples == 0
    assert result.residual_ratio < DEFAULT_STEM_RESIDUAL_THRESHOLD


def test_stem_sum_residual_detects_missing_energy() -> None:
    audio, bundle = _synthetic_stem_fixture()
    stems = dict(bundle.stems)
    stems["other"] = np.zeros_like(stems["other"])
    degraded = bundle.model_copy(update={"stems": stems})

    result = validate_stem_reconstruction(audio, degraded)

    assert not result.passed
    assert result.residual_ratio > DEFAULT_STEM_RESIDUAL_THRESHOLD


def test_stem_sum_residual_fails_for_empty_source_with_nonempty_stems() -> None:
    _, bundle = _synthetic_stem_fixture()
    empty_audio = AudioData(
        metadata=AudioMetadata(
            file_path="empty.wav",
            duration_sec=0.0,
            sample_rate=bundle.sample_rate,
            channels=1,
            format="synthetic",
        ),
        y_mono=np.zeros(0, dtype=np.float32),
        y_stereo=None,
        sr=bundle.sample_rate,
    )

    result = validate_stem_reconstruction(empty_audio, bundle)

    assert not result.passed
    assert result.residual_ratio == 1.0


def test_silent_source_with_audible_stems_fails() -> None:
    """Source RMS == 0 + non-zero summed stems must fail (no auto-pass)."""
    _, bundle = _synthetic_stem_fixture()
    silent_audio = AudioData(
        metadata=AudioMetadata(
            file_path="silent.wav",
            duration_sec=bundle.duration_sec,
            sample_rate=bundle.sample_rate,
            channels=1,
            format="synthetic",
        ),
        y_mono=np.zeros(bundle.stems["vocals"].size, dtype=np.float32),
        y_stereo=None,
        sr=bundle.sample_rate,
    )

    result = validate_stem_reconstruction(silent_audio, bundle)

    assert not result.passed
    assert result.residual_ratio == 1.0
    assert result.source_rms == 0.0


def test_stem_tail_loss_is_counted_in_residual() -> None:
    """Truncating one stem's tail must surface as residual energy, not be dropped.

    `sum_stems` zero-pads to the longest stem so the truncated stem's missing
    tail becomes silence in the reconstruction. Source still has audible energy
    in that region, so the residual ratio should exceed the Q3 threshold.
    """
    audio, bundle = _synthetic_stem_fixture()
    full_length = audio.y_mono.size
    truncated_length = full_length // 2
    stems = dict(bundle.stems)
    stems["other"] = stems["other"][:truncated_length].copy()
    truncated = bundle.model_copy(update={"stems": stems})

    baseline = validate_stem_reconstruction(audio, bundle)
    result = validate_stem_reconstruction(audio, truncated)

    assert baseline.passed
    assert not result.passed
    # The dropped half of "other" energy must reach the residual rather than
    # being silently truncated; residual must exceed the same-length baseline.
    assert result.residual_ratio > baseline.residual_ratio
    assert result.residual_ratio > DEFAULT_STEM_RESIDUAL_THRESHOLD


def test_stem_sum_residual_rejects_sample_rate_mismatch() -> None:
    audio, bundle = _synthetic_stem_fixture()
    mismatched_audio = audio.model_copy(update={"sr": audio.sr // 2})

    with pytest.raises(ValueError, match="sample rates must match"):
        validate_stem_reconstruction(mismatched_audio, bundle)


def test_per_stem_bpm_matches_full_mix_on_synthetic_fixture() -> None:
    audio, bundle = _synthetic_stem_fixture()

    physical, _, _ = extract_physical(audio, stem_bundle=bundle)
    result = validate_stem_bpm_alignment(physical)

    assert set(physical.stem_rpe) == REQUIRED_STEMS
    assert result.passed
    assert result.full_bpm is not None
    assert all(
        diff is not None and diff <= result.tolerance
        for diff in result.bpm_diffs.values()
    )


def test_bpm_alignment_rejects_empty_required_stems() -> None:
    physical = _minimal_physical()

    with pytest.raises(ValueError, match="required_stems"):
        validate_stem_bpm_alignment(physical, required_stems=[])
