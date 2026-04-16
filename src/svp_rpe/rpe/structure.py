"""rpe/structure.py — RMS/onset-based audio segment division.

Guarantees at least one section (empty list prohibited by PhysicalRPE validator).
"""
from __future__ import annotations

import librosa
import numpy as np

from svp_rpe.rpe.models import SectionMarker


def detect_sections(
    y: np.ndarray,
    sr: int,
    *,
    min_section_sec: float = 5.0,
    max_sections: int = 8,
) -> list[SectionMarker]:
    """Detect audio sections via RMS-based novelty.

    Falls back to a single section spanning the full duration if
    no meaningful boundaries are found.
    """
    duration = len(y) / sr
    if duration <= min_section_sec:
        return [SectionMarker(
            label="section_01", start_sec=0.0, end_sec=round(duration, 4),
        )]

    # Compute RMS envelope
    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    if len(rms) < 4:
        return [SectionMarker(
            label="section_01", start_sec=0.0, end_sec=round(duration, 4),
        )]

    # Compute self-similarity novelty curve
    # Use onset strength as a proxy for section boundaries
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    # Smooth and find peaks
    kernel_size = max(1, len(onset_env) // 20)
    if kernel_size > 1:
        kernel = np.ones(kernel_size) / kernel_size
        smoothed = np.convolve(onset_env, kernel, mode="same")
    else:
        smoothed = onset_env

    # Find local minima in smoothed onset strength (section boundaries)
    min_distance = int(min_section_sec * sr / 512)
    if min_distance < 1:
        min_distance = 1

    # Use negative smoothed to find minima via peak detection
    neg = -smoothed
    peaks = []
    for i in range(min_distance, len(neg) - min_distance):
        if neg[i] > neg[i - 1] and neg[i] > neg[i + 1]:
            peaks.append(i)

    # Convert frame indices to seconds
    times = librosa.frames_to_time(np.array(peaks), sr=sr, hop_length=512)
    # Filter: only keep boundaries that are at least min_section_sec apart
    boundaries = [0.0]
    for t in times:
        if t - boundaries[-1] >= min_section_sec and t < duration - min_section_sec / 2:
            boundaries.append(round(float(t), 4))
    boundaries.append(round(duration, 4))

    # Cap at max_sections
    if len(boundaries) - 1 > max_sections:
        # Keep evenly spaced boundaries
        step = (len(boundaries) - 1) / max_sections
        indices = [0] + [int(round(step * i)) for i in range(1, max_sections)] + [len(boundaries) - 1]
        boundaries = [boundaries[i] for i in sorted(set(indices))]

    sections = []
    for i in range(len(boundaries) - 1):
        label = f"section_{i + 1:02d}"
        start = boundaries[i]
        end = boundaries[i + 1]
        # Compute section-level RMS
        start_frame = int(start * sr / 512)
        end_frame = min(int(end * sr / 512), len(rms))
        sec_rms = float(np.mean(rms[start_frame:end_frame])) if end_frame > start_frame else 0.0
        sections.append(SectionMarker(
            label=label, start_sec=start, end_sec=end,
            rms_mean=round(sec_rms, 4),
        ))

    # Safety: guarantee at least one section
    if not sections:
        sections = [SectionMarker(
            label="section_01", start_sec=0.0, end_sec=round(duration, 4),
        )]

    return sections
