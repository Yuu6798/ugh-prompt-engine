"""Q3 stem-level validation helpers.

These helpers validate the observable contracts around separated stems without
requiring Demucs at import time.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterable

import numpy as np

from svp_rpe.io.audio_loader import AudioData
from svp_rpe.io.source_separator import REQUIRED_STEMS, STEM_NAMES, StemBundle
from svp_rpe.rpe.models import PhysicalRPE

DEFAULT_STEM_RESIDUAL_THRESHOLD = 0.05
DEFAULT_STEM_BPM_TOLERANCE = 5.0
_EPSILON = 1e-12


@dataclass(frozen=True)
class StemReconstructionValidation:
    """Result for summed-stem waveform residual against the full mix."""

    residual_ratio: float
    residual_rms: float
    source_rms: float
    threshold: float
    compared_samples: int
    length_delta_samples: int
    passed: bool


@dataclass(frozen=True)
class StemBPMAlignmentValidation:
    """Result for per-stem BPM alignment against the full-mix BPM."""

    full_bpm: float | None
    stem_bpms: dict[str, float | None]
    bpm_diffs: dict[str, float | None]
    tolerance: float
    missing_stems: list[str]
    passed: bool


def _rms(y: np.ndarray) -> float:
    if y.size == 0:
        return 0.0
    y64 = np.asarray(y, dtype=np.float64)
    return float(np.sqrt(np.mean(np.square(y64))))


def sum_stems(stem_bundle: StemBundle, stem_names: Iterable[str] = STEM_NAMES) -> np.ndarray:
    """Return a mono waveform made by summing selected stems over common length."""
    names = tuple(stem_names)
    if not names:
        return np.zeros(0, dtype=np.float32)

    common_length = min(len(stem_bundle.stems[name]) for name in names)
    summed = np.zeros(common_length, dtype=np.float64)
    for name in names:
        summed += stem_bundle.stems[name][:common_length].astype(np.float64, copy=False)
    return summed.astype(np.float32)


def validate_stem_reconstruction(
    audio: AudioData,
    stem_bundle: StemBundle,
    *,
    threshold: float = DEFAULT_STEM_RESIDUAL_THRESHOLD,
) -> StemReconstructionValidation:
    """Validate summed-stem reconstruction residual against the full mix.

    The metric is `rms(source - sum(stems)) / rms(source)` over the common
    sample range. This is a validation signal, not a guarantee that Demucs is
    energy-conserving on real music.
    """
    if int(audio.sr) != int(stem_bundle.sample_rate):
        raise ValueError(
            "audio and stem_bundle sample rates must match for reconstruction "
            f"validation: audio={audio.sr}, stems={stem_bundle.sample_rate}"
        )

    source = np.asarray(audio.y_mono, dtype=np.float32)
    reconstructed = sum_stems(stem_bundle)
    compared_samples = min(source.size, reconstructed.size)
    length_delta = abs(int(source.size) - int(reconstructed.size))

    if compared_samples == 0:
        source_rms = _rms(source)
        reconstructed_rms = _rms(reconstructed)
        residual_rms = max(source_rms, reconstructed_rms)
        residual_ratio = 0.0 if residual_rms <= _EPSILON else 1.0
    else:
        source_common = source[:compared_samples]
        residual = source_common - reconstructed[:compared_samples]
        source_rms = _rms(source_common)
        residual_rms = _rms(residual)
        residual_ratio = 0.0 if source_rms <= _EPSILON else residual_rms / source_rms

    return StemReconstructionValidation(
        residual_ratio=round(float(residual_ratio), 6),
        residual_rms=round(float(residual_rms), 8),
        source_rms=round(float(source_rms), 8),
        threshold=threshold,
        compared_samples=compared_samples,
        length_delta_samples=length_delta,
        passed=bool(residual_ratio <= threshold),
    )


def validate_stem_bpm_alignment(
    physical: PhysicalRPE,
    *,
    tolerance: float = DEFAULT_STEM_BPM_TOLERANCE,
    required_stems: Iterable[str] = REQUIRED_STEMS,
) -> StemBPMAlignmentValidation:
    """Validate that each required stem BPM stays close to the full-mix BPM."""
    required_set = set(required_stems)
    if not required_set:
        raise ValueError("required_stems must not be empty")

    known_required = tuple(name for name in STEM_NAMES if name in required_set)
    extra_required = tuple(sorted(required_set - set(STEM_NAMES)))
    required = known_required + extra_required
    missing = [name for name in required if name not in physical.stem_rpe]

    stem_bpms: dict[str, float | None] = {}
    bpm_diffs: dict[str, float | None] = {}
    all_within_tolerance = physical.bpm is not None and not missing

    for name in required:
        stem_physical = physical.stem_rpe.get(name)
        stem_bpm = stem_physical.bpm if stem_physical is not None else None
        stem_bpms[name] = stem_bpm

        if physical.bpm is None or stem_bpm is None:
            bpm_diffs[name] = None
            all_within_tolerance = False
            continue

        diff = abs(float(stem_bpm) - float(physical.bpm))
        bpm_diffs[name] = round(diff, 4)
        if diff > tolerance:
            all_within_tolerance = False

    return StemBPMAlignmentValidation(
        full_bpm=physical.bpm,
        stem_bpms=stem_bpms,
        bpm_diffs=bpm_diffs,
        tolerance=tolerance,
        missing_stems=missing,
        passed=bool(all_within_tolerance),
    )
