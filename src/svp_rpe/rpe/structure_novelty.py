"""rpe/structure_novelty.py — Multi-feature change point detection.

Combines RMS, onset strength, spectral flux, and chroma change
for improved section boundary detection.
"""
from __future__ import annotations

import librosa
import numpy as np


def compute_novelty_curve(
    y: np.ndarray,
    sr: int,
    *,
    hop_length: int = 512,
) -> np.ndarray:
    """Compute a combined novelty curve from multiple features.

    Combines:
    - RMS envelope derivative
    - Onset strength
    - Spectral flux
    - Chroma self-similarity novelty
    """
    # RMS novelty (absolute derivative of RMS)
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    rms_diff = np.abs(np.diff(rms, prepend=rms[0]))
    if rms_diff.max() > 0:
        rms_diff = rms_diff / rms_diff.max()

    # Onset strength
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    if onset.max() > 0:
        onset = onset / onset.max()

    # Spectral flux
    S = np.abs(librosa.stft(y, hop_length=hop_length))
    flux = np.sqrt(np.sum(np.diff(S, axis=1) ** 2, axis=0))
    flux = np.concatenate([[0.0], flux])
    if flux.max() > 0:
        flux = flux / flux.max()

    # Chroma change
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
    chroma_diff = np.sum(np.abs(np.diff(chroma, axis=1)), axis=0)
    chroma_diff = np.concatenate([[0.0], chroma_diff])
    if chroma_diff.max() > 0:
        chroma_diff = chroma_diff / chroma_diff.max()

    # Align lengths
    min_len = min(len(rms_diff), len(onset), len(flux), len(chroma_diff))
    combined = (
        0.25 * rms_diff[:min_len]
        + 0.30 * onset[:min_len]
        + 0.25 * flux[:min_len]
        + 0.20 * chroma_diff[:min_len]
    )

    return combined


def find_boundaries(
    novelty: np.ndarray,
    sr: int,
    duration: float,
    *,
    hop_length: int = 512,
    min_section_sec: float = 5.0,
    max_sections: int = 8,
) -> list[float]:
    """Find section boundaries from novelty curve.

    Returns list of boundary times in seconds, always starting with 0.0
    and ending with duration.
    """
    def _smooth_curve() -> np.ndarray:
        kernel_size = max(1, len(novelty) // 15)
        if kernel_size <= 1:
            return novelty
        kernel = np.ones(kernel_size) / kernel_size
        return np.convolve(novelty, kernel, mode="same")

    def _peak_indices(smoothed: np.ndarray) -> list[int]:
        min_frames = max(1, int(min_section_sec * sr / hop_length))
        threshold = float(np.mean(smoothed) + 0.5 * np.std(smoothed))
        peaks = []
        for i in range(min_frames, len(smoothed) - min_frames):
            if (
                smoothed[i] > smoothed[i - 1]
                and smoothed[i] > smoothed[i + 1]
                and smoothed[i] > threshold
            ):
                peaks.append(i)
        return peaks

    def _filtered_boundaries(times: np.ndarray) -> list[float]:
        boundaries = [0.0]
        for t in times:
            if (
                t - boundaries[-1] >= min_section_sec
                and t < duration - min_section_sec / 2
            ):
                boundaries.append(round(float(t), 4))
        boundaries.append(round(duration, 4))
        return boundaries

    def _cap_boundaries(boundaries: list[float]) -> list[float]:
        while len(boundaries) - 1 > max_sections:
            gaps = [
                (boundaries[i + 1] - boundaries[i], i)
                for i in range(1, len(boundaries) - 1)
            ]
            if not gaps:
                break
            _, idx = min(gaps)
            boundaries.pop(idx)
        return boundaries

    if len(novelty) < 4:
        return [0.0, duration]

    smoothed = _smooth_curve()
    peaks = _peak_indices(smoothed)

    # Convert to times
    times = librosa.frames_to_time(np.array(peaks), sr=sr, hop_length=hop_length)
    return _cap_boundaries(_filtered_boundaries(times))
