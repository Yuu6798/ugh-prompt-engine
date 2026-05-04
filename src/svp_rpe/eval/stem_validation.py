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
    """Return a mono waveform by summing selected stems, padded to the longest stem.

    Stems of unequal length are zero-padded to the longest length before summing
    so that a missing tail in any one stem becomes part of the reconstruction
    error rather than being silently truncated.
    """
    names = tuple(stem_names)
    if not names:
        return np.zeros(0, dtype=np.float32)

    target_length = max(len(stem_bundle.stems[name]) for name in names)
    summed = np.zeros(target_length, dtype=np.float64)
    for name in names:
        stem = stem_bundle.stems[name]
        summed[: len(stem)] += stem.astype(np.float64, copy=False)
    return summed.astype(np.float32)


def validate_stem_reconstruction(
    audio: AudioData,
    stem_bundle: StemBundle,
    *,
    threshold: float = DEFAULT_STEM_RESIDUAL_THRESHOLD,
) -> StemReconstructionValidation:
    """Validate summed-stem reconstruction residual against the full mix.

    The metric is `rms(source - sum(stems)) / rms(source)` evaluated over the
    full union length: source and reconstructed signals are zero-padded to the
    longer of the two so that any tail loss (in either direction) shows up as
    residual energy. This is a validation signal, not a guarantee that Demucs
    is energy-conserving on real music.

    A silent source paired with non-empty reconstruction fails (ratio = 1.0)
    because there is no truth signal to recover yet stems contribute audible
    energy.
    """
    if int(audio.sr) != int(stem_bundle.sample_rate):
        raise ValueError(
            "audio and stem_bundle sample rates must match for reconstruction "
            f"validation: audio={audio.sr}, stems={stem_bundle.sample_rate}"
        )

    source = np.asarray(audio.y_mono, dtype=np.float32)
    reconstructed = sum_stems(stem_bundle)
    overlap_samples = min(source.size, reconstructed.size)
    length_delta = abs(int(source.size) - int(reconstructed.size))
    target_length = max(source.size, reconstructed.size)

    if target_length == 0:
        return StemReconstructionValidation(
            residual_ratio=0.0,
            residual_rms=0.0,
            source_rms=0.0,
            threshold=threshold,
            compared_samples=0,
            length_delta_samples=0,
            passed=True,
        )

    source_padded = np.zeros(target_length, dtype=np.float64)
    source_padded[: source.size] = source
    recon_padded = np.zeros(target_length, dtype=np.float64)
    recon_padded[: reconstructed.size] = reconstructed

    residual = source_padded - recon_padded
    source_rms = _rms(source_padded)
    residual_rms = _rms(residual)

    if source_rms <= _EPSILON:
        residual_ratio = 0.0 if residual_rms <= _EPSILON else 1.0
    else:
        residual_ratio = residual_rms / source_rms

    return StemReconstructionValidation(
        residual_ratio=round(float(residual_ratio), 6),
        residual_rms=round(float(residual_rms), 8),
        source_rms=round(float(source_rms), 8),
        threshold=threshold,
        compared_samples=overlap_samples,
        length_delta_samples=length_delta,
        passed=bool(residual_ratio <= threshold),
    )


def _ordered_required_stems(required_stems: Iterable[str]) -> tuple[str, ...]:
    required_set = set(required_stems)
    if not required_set:
        raise ValueError("required_stems must not be empty")

    known_required = tuple(name for name in STEM_NAMES if name in required_set)
    extra_required = tuple(sorted(required_set - set(STEM_NAMES)))
    return known_required + extra_required


def _collect_stem_bpms(
    physical: PhysicalRPE, required: Iterable[str]
) -> dict[str, float | None]:
    stem_bpms: dict[str, float | None] = {}
    for name in required:
        stem_physical = physical.stem_rpe.get(name)
        stem_bpms[name] = stem_physical.bpm if stem_physical is not None else None
    return stem_bpms


def _stem_bpm_diff(full_bpm: float | None, stem_bpm: float | None) -> float | None:
    if full_bpm is None or stem_bpm is None:
        return None
    return round(abs(float(stem_bpm) - float(full_bpm)), 4)


def _collect_bpm_diffs(
    full_bpm: float | None, stem_bpms: dict[str, float | None]
) -> dict[str, float | None]:
    return {
        name: _stem_bpm_diff(full_bpm, stem_bpm)
        for name, stem_bpm in stem_bpms.items()
    }


def _bpm_alignment_passed(
    full_bpm: float | None,
    bpm_diffs: dict[str, float | None],
    missing_stems: list[str],
    tolerance: float,
) -> bool:
    if full_bpm is None or missing_stems:
        return False
    return all(diff is not None and diff <= tolerance for diff in bpm_diffs.values())


def validate_stem_bpm_alignment(
    physical: PhysicalRPE,
    *,
    tolerance: float = DEFAULT_STEM_BPM_TOLERANCE,
    required_stems: Iterable[str] = REQUIRED_STEMS,
) -> StemBPMAlignmentValidation:
    """Validate that each required stem BPM stays close to the full-mix BPM.

    The order in the resulting `stem_bpms` and `bpm_diffs` mirrors `STEM_NAMES`
    for known stems (vocals/drums/bass/other), with any unknown stems passed
    via `required_stems` appended in sorted order. Pass a custom iterable to
    validate a subset (e.g. ``["vocals", "drums"]``) or to add new stems from a
    multi-stem Demucs variant; missing stems land in `missing_stems` and force
    `passed=False`.
    """
    required = _ordered_required_stems(required_stems)
    missing = [name for name in required if name not in physical.stem_rpe]
    stem_bpms = _collect_stem_bpms(physical, required)
    bpm_diffs = _collect_bpm_diffs(physical.bpm, stem_bpms)

    return StemBPMAlignmentValidation(
        full_bpm=physical.bpm,
        stem_bpms=stem_bpms,
        bpm_diffs=bpm_diffs,
        tolerance=tolerance,
        missing_stems=missing,
        passed=_bpm_alignment_passed(
            physical.bpm,
            bpm_diffs,
            missing,
            tolerance,
        ),
    )
