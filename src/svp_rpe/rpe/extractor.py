"""rpe/extractor.py — RPE integrated extraction pipeline.

Combines audio loading, physical feature extraction, improved structure detection,
valley strategy, and semantic rule-based mapping into RPEBundle output.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from svp_rpe.eval.diff_models import SectionFeature, ValleyDiagnostics
from svp_rpe.io.audio_loader import AudioData, load_audio
from svp_rpe.rpe.models import PhysicalRPE, RPEBundle, SectionMarker
from svp_rpe.rpe.physical_features import (
    compute_active_rate,
    compute_bpm,
    compute_crest_factor,
    compute_key,
    compute_loudness,
    compute_onset_density,
    compute_rms_mean,
    compute_spectral_profile,
    compute_stereo_profile,
    compute_thickness,
)
from svp_rpe.rpe.section_features import extract_section_features
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.rpe.structure_labels import assign_labels
from svp_rpe.rpe.structure_novelty import compute_novelty_curve, find_boundaries
from svp_rpe.rpe.valley import compute_valley_depth

# Keep legacy import for backward compat


def _detect_sections_v2(y: np.ndarray, sr: int) -> list[SectionMarker]:
    """Improved section detection using multi-feature novelty."""
    import librosa

    duration = len(y) / sr
    if duration <= 5.0:
        return [SectionMarker(label="Full", start_sec=0.0, end_sec=round(duration, 4))]

    novelty = compute_novelty_curve(y, sr)
    boundaries = find_boundaries(novelty, sr, duration)

    rms = librosa.feature.rms(y=y, hop_length=512)[0]

    # Compute section-level RMS for labeling
    section_rms: list[float] = []
    for i in range(len(boundaries) - 1):
        start_frame = int(boundaries[i] * sr / 512)
        end_frame = min(int(boundaries[i + 1] * sr / 512), len(rms))
        sec_rms = float(np.mean(rms[start_frame:end_frame])) if end_frame > start_frame else 0.0
        section_rms.append(sec_rms)

    # Assign labels
    labels = assign_labels(section_rms, len(section_rms))

    sections = []
    for i in range(len(boundaries) - 1):
        label = labels[i] if i < len(labels) else f"section_{i + 1:02d}"
        sections.append(SectionMarker(
            label=label,
            start_sec=boundaries[i],
            end_sec=boundaries[i + 1],
            rms_mean=round(section_rms[i], 4) if i < len(section_rms) else None,
        ))

    if not sections:
        sections = [SectionMarker(label="Full", start_sec=0.0, end_sec=round(duration, 4))]

    return sections


def extract_physical(
    audio: AudioData,
    *,
    valley_method: str = "hybrid",
) -> tuple[PhysicalRPE, Optional[ValleyDiagnostics], list[SectionFeature]]:
    """Extract PhysicalRPE with improved structure and valley estimation.

    Returns (PhysicalRPE, ValleyDiagnostics, [SectionFeature]).
    """
    y = audio.y_mono
    sr = audio.sr

    rms_mean = compute_rms_mean(y, sr)
    peak_amplitude = float(np.max(np.abs(y)))
    crest_factor = compute_crest_factor(y)
    active_rate = compute_active_rate(y, sr)
    thickness = compute_thickness(y, sr)
    spectral_profile = compute_spectral_profile(y, sr)
    onset_density = compute_onset_density(y, sr)

    bpm, bpm_confidence = compute_bpm(y, sr)
    key, mode, key_confidence = compute_key(y, sr)

    # ITU-R BS.1770 loudness; uses stereo when available, else mono.
    loudness_input = audio.y_stereo if audio.y_stereo is not None else y
    loudness_lufs, true_peak_dbfs = compute_loudness(loudness_input, sr)

    stereo_profile = None
    if audio.y_stereo is not None:
        stereo_profile = compute_stereo_profile(audio.y_stereo, sr)

    # Improved structure detection
    structure = _detect_sections_v2(y, sr)

    # Valley depth with strategy pattern
    valley_depth, valley_diag = compute_valley_depth(
        y, sr, structure, method=valley_method,
    )

    # Per-section features
    section_features = extract_section_features(y, sr, structure)

    phys = PhysicalRPE(
        bpm=bpm,
        bpm_confidence=bpm_confidence,
        key=key,
        mode=mode,
        key_confidence=key_confidence,
        duration_sec=round(len(y) / sr, 4),
        sample_rate=sr,
        structure=structure,
        rms_mean=round(rms_mean, 4),
        peak_amplitude=round(peak_amplitude, 4),
        crest_factor=crest_factor,
        loudness_lufs_integrated=loudness_lufs,
        true_peak_dbfs=true_peak_dbfs,
        active_rate=round(active_rate, 4),
        valley_depth=valley_depth,
        valley_depth_method=valley_method,
        thickness=thickness,
        spectral_centroid=spectral_profile.centroid,
        spectral_profile=spectral_profile,
        stereo_profile=stereo_profile,
        onset_density=onset_density,
    )

    return phys, valley_diag, section_features


def extract_physical_from_file(path: str) -> PhysicalRPE:
    """Convenience: load audio file and extract PhysicalRPE in one call."""
    audio = load_audio(path)
    phys, _, _ = extract_physical(audio)
    return phys


def extract_rpe(
    audio: AudioData,
    *,
    valley_method: str = "hybrid",
) -> RPEBundle:
    """Full RPE extraction: physical + semantic → RPEBundle."""
    phys, valley_diag, section_features = extract_physical(
        audio, valley_method=valley_method,
    )
    sem = generate_semantic(phys)
    return RPEBundle(
        physical=phys,
        semantic=sem,
        audio_file=audio.metadata.file_path,
        audio_duration_sec=audio.metadata.duration_sec,
        audio_sample_rate=audio.sr,
        audio_channels=audio.metadata.channels,
        audio_format=audio.metadata.format,
    )


def extract_rpe_from_file(
    path: str,
    *,
    valley_method: str = "hybrid",
) -> RPEBundle:
    """Convenience: load audio file and extract full RPEBundle."""
    audio = load_audio(path)
    return extract_rpe(audio, valley_method=valley_method)
