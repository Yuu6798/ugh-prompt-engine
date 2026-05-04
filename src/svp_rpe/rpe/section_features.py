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

    def _empty_feature(sec: SectionMarker) -> SectionFeature:
        return SectionFeature(
            label=sec.label,
            start_sec=sec.start_sec,
            end_sec=sec.end_sec,
        )

    def _section_window(sec: SectionMarker) -> tuple[int, int, int, int]:
        start_sample = int(sec.start_sec * sr)
        end_sample = min(int(sec.end_sec * sr), len(y))
        start_frame = int(sec.start_sec * sr / hop_length)
        end_frame = min(int(sec.end_sec * sr / hop_length), len(rms_full))
        return start_sample, end_sample, start_frame, end_frame

    def _spectral_centroid(y_sec: np.ndarray) -> float:
        try:
            sc = librosa.feature.spectral_centroid(
                y=y_sec, sr=sr, hop_length=hop_length,
            )[0]
            return float(np.mean(sc))
        except Exception:
            return 0.0

    def _onset_density(y_sec: np.ndarray, sec: SectionMarker) -> float:
        try:
            onsets = librosa.onset.onset_detect(y=y_sec, sr=sr, units="time")
            sec_duration = sec.end_sec - sec.start_sec
            return len(onsets) / max(sec_duration, 0.01)
        except Exception:
            return 0.0

    def _spectral_flux_mean(y_sec: np.ndarray) -> float:
        try:
            S = np.abs(librosa.stft(y_sec, hop_length=hop_length))
            if S.shape[1] <= 1:
                return 0.0
            flux = np.sqrt(np.sum(np.diff(S, axis=1) ** 2, axis=0))
            return float(np.mean(flux))
        except Exception:
            return 0.0

    def _chroma_change(y_sec: np.ndarray) -> float:
        try:
            chroma = librosa.feature.chroma_cqt(y=y_sec, sr=sr, hop_length=hop_length)
            if chroma.shape[1] <= 1:
                return 0.0
            chroma_diff = np.sum(np.abs(np.diff(chroma, axis=1)))
            return float(chroma_diff / max(chroma.shape[1] - 1, 1))
        except Exception:
            return 0.0

    def _feature_for_section(sec: SectionMarker) -> SectionFeature:
        start_sample, end_sample, start_frame, end_frame = _section_window(sec)

        if end_sample <= start_sample or end_frame <= start_frame:
            return _empty_feature(sec)

        y_sec = y[start_sample:end_sample]
        rms_sec = rms_full[start_frame:end_frame]

        rms_mean = float(np.mean(rms_sec))
        active_rate = float(np.sum(rms_sec > rms_threshold) / len(rms_sec))
        spectral_centroid = _spectral_centroid(y_sec)
        onset_density = _onset_density(y_sec, sec)
        spectral_flux_mean = _spectral_flux_mean(y_sec)
        chroma_change = _chroma_change(y_sec)

        return SectionFeature(
            label=sec.label,
            start_sec=sec.start_sec,
            end_sec=sec.end_sec,
            rms_mean=round(rms_mean, 4),
            active_rate=round(active_rate, 4),
            spectral_centroid=round(spectral_centroid, 2),
            onset_density=round(onset_density, 4),
            spectral_flux_mean=round(spectral_flux_mean, 4),
            chroma_change=round(chroma_change, 4),
        )

    features: List[SectionFeature] = []

    for sec in sections:
        features.append(_feature_for_section(sec))

    return features
