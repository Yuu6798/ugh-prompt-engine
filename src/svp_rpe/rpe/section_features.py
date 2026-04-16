"""rpe/section_features.py — Per-section feature extraction."""
from __future__ import annotations

from typing import List

import librosa
import numpy as np

from svp_rpe.eval.diff_models import SectionFeature
from svp_rpe.rpe.models import SectionMarker


def extract_section_features(
    y: np.ndarray,
    sr: int,
    sections: List[SectionMarker],
    *,
    hop_length: int = 512,
    rms_threshold: float = 0.01,
) -> List[SectionFeature]:
    """Extract per-section feature vectors."""
    rms_full = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    features: List[SectionFeature] = []

    for sec in sections:
        start_sample = int(sec.start_sec * sr)
        end_sample = min(int(sec.end_sec * sr), len(y))
        start_frame = int(sec.start_sec * sr / hop_length)
        end_frame = min(int(sec.end_sec * sr / hop_length), len(rms_full))

        if end_sample <= start_sample or end_frame <= start_frame:
            features.append(SectionFeature(
                label=sec.label, start_sec=sec.start_sec, end_sec=sec.end_sec,
            ))
            continue

        y_sec = y[start_sample:end_sample]
        rms_sec = rms_full[start_frame:end_frame]

        # RMS mean
        rms_mean = float(np.mean(rms_sec))

        # Active rate
        active_rate = float(np.sum(rms_sec > rms_threshold) / len(rms_sec))

        # Spectral centroid
        try:
            sc = librosa.feature.spectral_centroid(y=y_sec, sr=sr, hop_length=hop_length)[0]
            spectral_centroid = float(np.mean(sc))
        except Exception:
            spectral_centroid = 0.0

        # Onset density
        try:
            onsets = librosa.onset.onset_detect(y=y_sec, sr=sr, units="time")
            sec_duration = sec.end_sec - sec.start_sec
            onset_density = len(onsets) / max(sec_duration, 0.01)
        except Exception:
            onset_density = 0.0

        # Spectral flux
        try:
            S = np.abs(librosa.stft(y_sec, hop_length=hop_length))
            if S.shape[1] > 1:
                flux = np.sqrt(np.sum(np.diff(S, axis=1) ** 2, axis=0))
                spectral_flux_mean = float(np.mean(flux))
            else:
                spectral_flux_mean = 0.0
        except Exception:
            spectral_flux_mean = 0.0

        # Chroma change
        try:
            chroma = librosa.feature.chroma_cqt(y=y_sec, sr=sr, hop_length=hop_length)
            if chroma.shape[1] > 1:
                chroma_diff = np.sum(np.abs(np.diff(chroma, axis=1)))
                chroma_change = float(chroma_diff / max(chroma.shape[1] - 1, 1))
            else:
                chroma_change = 0.0
        except Exception:
            chroma_change = 0.0

        features.append(SectionFeature(
            label=sec.label,
            start_sec=sec.start_sec,
            end_sec=sec.end_sec,
            rms_mean=round(rms_mean, 4),
            active_rate=round(active_rate, 4),
            spectral_centroid=round(spectral_centroid, 2),
            onset_density=round(onset_density, 4),
            spectral_flux_mean=round(spectral_flux_mean, 4),
            chroma_change=round(chroma_change, 4),
        ))

    return features
