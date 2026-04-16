"""rpe/extractor.py — RPE integrated extraction pipeline.

Combines audio loading, physical feature extraction, structure detection,
and semantic rule-based mapping into RPEBundle output.
"""
from __future__ import annotations

import numpy as np

from svp_rpe.io.audio_loader import AudioData, load_audio
from svp_rpe.rpe.models import PhysicalRPE, RPEBundle
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.rpe.physical_features import (
    compute_active_rate,
    compute_bpm,
    compute_crest_factor,
    compute_key,
    compute_onset_density,
    compute_rms_mean,
    compute_spectral_profile,
    compute_stereo_profile,
    compute_thickness,
    compute_valley_depth,
)
from svp_rpe.rpe.structure import detect_sections


def extract_physical(audio: AudioData) -> PhysicalRPE:
    """Extract PhysicalRPE from loaded audio data.

    Deterministic: same AudioData → same PhysicalRPE.
    """
    y = audio.y_mono
    sr = audio.sr

    rms_mean = compute_rms_mean(y, sr)
    peak_amplitude = float(np.max(np.abs(y)))
    crest_factor = compute_crest_factor(y)
    active_rate = compute_active_rate(y, sr)
    valley_depth = compute_valley_depth(y, sr)
    thickness = compute_thickness(y, sr)
    spectral_profile = compute_spectral_profile(y, sr)
    onset_density = compute_onset_density(y, sr)

    bpm, bpm_confidence = compute_bpm(y, sr)
    key, mode, key_confidence = compute_key(y, sr)

    stereo_profile = None
    if audio.y_stereo is not None:
        stereo_profile = compute_stereo_profile(audio.y_stereo, sr)

    structure = detect_sections(y, sr)

    return PhysicalRPE(
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
        active_rate=round(active_rate, 4),
        valley_depth=valley_depth,
        thickness=thickness,
        spectral_centroid=spectral_profile.centroid,
        spectral_profile=spectral_profile,
        stereo_profile=stereo_profile,
        onset_density=onset_density,
    )


def extract_physical_from_file(path: str) -> PhysicalRPE:
    """Convenience: load audio file and extract PhysicalRPE in one call."""
    audio = load_audio(path)
    return extract_physical(audio)


def extract_rpe(audio: AudioData) -> RPEBundle:
    """Full RPE extraction: physical + semantic → RPEBundle."""
    phys = extract_physical(audio)
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


def extract_rpe_from_file(path: str) -> RPEBundle:
    """Convenience: load audio file and extract full RPEBundle."""
    audio = load_audio(path)
    return extract_rpe(audio)
