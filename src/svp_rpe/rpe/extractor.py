"""rpe/extractor.py — RPE integrated extraction pipeline.

Combines audio loading, physical feature extraction, improved structure detection,
valley strategy, and semantic rule-based mapping into RPEBundle output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from svp_rpe.eval.diff_models import SectionFeature, ValleyDiagnostics
from svp_rpe.io.audio_loader import AudioData, AudioMetadata, load_audio
from svp_rpe.io.source_separator import (
    STEM_NAMES,
    StemBundle,
    separate_stems as separate_audio_stems,
)
from svp_rpe.rpe.models import PhysicalRPE, RPEBundle, SectionMarker
from svp_rpe.rpe.dynamics_summary import compute_dynamics_summary
from svp_rpe.rpe.physical_features import (
    compute_active_rate,
    compute_bpm,
    compute_chord_events,
    compute_crest_factor,
    compute_dynamic_range_db,
    compute_key,
    compute_loudness,
    compute_melody_contour,
    compute_onset_density,
    compute_rms_mean,
    compute_spectral_profile,
    compute_stereo_profile,
    compute_thickness,
    compute_downbeat_times,
    compute_time_signature,
)
from svp_rpe.rpe.section_features import extract_section_features
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.rpe.structure_labels import assign_labels
from svp_rpe.rpe.structure_novelty import compute_novelty_curve, find_boundaries
from svp_rpe.rpe.valley import compute_valley_depth


def _detect_sections_v2(
    y: np.ndarray, sr: int,
) -> tuple[list[SectionMarker], Optional[np.ndarray]]:
    """Improved section detection using multi-feature novelty.

    Returns (sections, novelty_curve). novelty_curve is None for audio too
    short to segment (≤5s), so callers can short-circuit aggregates that
    depend on it.
    """
    import librosa

    duration = len(y) / sr
    if duration <= 5.0:
        return (
            [SectionMarker(label="Full", start_sec=0.0, end_sec=round(duration, 4))],
            None,
        )

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

    return sections, novelty


def _audio_from_stem(
    stem_bundle: StemBundle,
    stem_name: str,
    stem: np.ndarray,
) -> AudioData:
    sample_rate = stem_bundle.sample_rate
    duration_sec = round(len(stem) / sample_rate, 4)
    metadata = AudioMetadata(
        file_path=f"{stem_bundle.source_path}#{stem_name}",
        duration_sec=duration_sec,
        sample_rate=sample_rate,
        channels=1,
        format="stem",
    )
    return AudioData(
        metadata=metadata,
        y_mono=stem.astype(np.float32, copy=False),
        y_stereo=None,
        sr=sample_rate,
    )


def _extract_stem_rpe(
    stem_bundle: StemBundle,
    *,
    valley_method: str,
) -> dict[str, PhysicalRPE]:
    stem_rpe: dict[str, PhysicalRPE] = {}
    for stem_name in STEM_NAMES:
        stem_audio = _audio_from_stem(
            stem_bundle,
            stem_name,
            stem_bundle.stems[stem_name],
        )
        stem_phys, _, _ = extract_physical(stem_audio, valley_method=valley_method)
        stem_rpe[stem_name] = stem_phys
    return stem_rpe


def _maybe_separate_stems(
    path: str | Path,
    *,
    include_stems: bool,
    separation_model: str,
    separation_device: str,
) -> StemBundle | None:
    if not include_stems:
        return None
    return separate_audio_stems(
        Path(path),
        model=separation_model,
        device=separation_device,
    )


def extract_physical(
    audio: AudioData,
    *,
    valley_method: str = "hybrid",
    stem_bundle: StemBundle | None = None,
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
    time_signature, time_signature_confidence = compute_time_signature(y, sr)
    downbeat_times = compute_downbeat_times(y, sr, time_signature)
    chord_events = compute_chord_events(y, sr)
    melody_contour = compute_melody_contour(y, sr)
    key, mode, key_confidence = compute_key(y, sr)

    # ITU-R BS.1770 loudness; uses stereo when available, else mono.
    loudness_input = audio.y_stereo if audio.y_stereo is not None else y
    loudness_lufs, true_peak_dbfs = compute_loudness(loudness_input, sr)

    stereo_profile = None
    if audio.y_stereo is not None:
        stereo_profile = compute_stereo_profile(audio.y_stereo, sr)

    # Improved structure detection (also returns the novelty curve so we can
    # reuse it for the dynamics summary without recomputing).
    structure, novelty = _detect_sections_v2(y, sr)

    # Valley depth with strategy pattern
    valley_depth, valley_diag = compute_valley_depth(
        y, sr, structure, method=valley_method,
    )

    # Track-level descriptive aggregates.
    dynamic_range_db = compute_dynamic_range_db(y, sr)
    dynamics_summary = (
        compute_dynamics_summary(novelty) if novelty is not None else None
    )

    # Per-section features
    section_features = extract_section_features(y, sr, structure)
    stem_rpe = (
        _extract_stem_rpe(stem_bundle, valley_method=valley_method)
        if stem_bundle is not None
        else {}
    )

    phys = PhysicalRPE(
        bpm=bpm,
        bpm_confidence=bpm_confidence,
        key=key,
        mode=mode,
        key_confidence=key_confidence,
        time_signature=time_signature,
        time_signature_confidence=time_signature_confidence,
        downbeat_times=downbeat_times,
        chord_events=chord_events,
        melody_contour=melody_contour,
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
        dynamic_range_db=dynamic_range_db,
        dynamics_summary=dynamics_summary,
        stem_rpe=stem_rpe,
    )

    return phys, valley_diag, section_features


def extract_physical_from_file(
    path: str,
    *,
    valley_method: str = "hybrid",
    include_stems: bool = False,
    separation_model: str = "htdemucs_ft",
    separation_device: str = "cpu",
) -> PhysicalRPE:
    """Convenience: load audio file and extract PhysicalRPE in one call."""
    audio = load_audio(path)
    stem_bundle = _maybe_separate_stems(
        path,
        include_stems=include_stems,
        separation_model=separation_model,
        separation_device=separation_device,
    )
    phys, _, _ = extract_physical(
        audio,
        valley_method=valley_method,
        stem_bundle=stem_bundle,
    )
    return phys


def extract_rpe(
    audio: AudioData,
    *,
    valley_method: str = "hybrid",
    stem_bundle: StemBundle | None = None,
) -> RPEBundle:
    """Full RPE extraction: physical + semantic → RPEBundle."""
    phys, valley_diag, section_features = extract_physical(
        audio,
        valley_method=valley_method,
        stem_bundle=stem_bundle,
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
    include_stems: bool = False,
    separation_model: str = "htdemucs_ft",
    separation_device: str = "cpu",
    preloaded_audio: AudioData | None = None,
) -> RPEBundle:
    """Convenience: load audio file and extract full RPEBundle.

    ``preloaded_audio`` lets callers reuse audio decoded from ``path`` when the
    same waveform is also needed for optional learned-model adapters.
    Callers are responsible for ensuring it was decoded from ``path``; the
    extractor does not verify the match.
    """
    audio = preloaded_audio if preloaded_audio is not None else load_audio(path)
    # Stem separation remains path-based because Demucs/fallback separation
    # manages its own loading and output artifacts.
    stem_bundle = _maybe_separate_stems(
        path,
        include_stems=include_stems,
        separation_model=separation_model,
        separation_device=separation_device,
    )
    return extract_rpe(
        audio,
        valley_method=valley_method,
        stem_bundle=stem_bundle,
    )
