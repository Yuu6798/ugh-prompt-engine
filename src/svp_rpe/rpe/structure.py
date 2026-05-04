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

    def _full_section() -> list[SectionMarker]:
        return [
            SectionMarker(
                label="section_01",
                start_sec=0.0,
                end_sec=round(duration, 4),
            )
        ]

    def _smooth_onset(onset_env: np.ndarray) -> np.ndarray:
        kernel_size = max(1, len(onset_env) // 20)
        if kernel_size <= 1:
            return onset_env
        kernel = np.ones(kernel_size) / kernel_size
        return np.convolve(onset_env, kernel, mode="same")

    def _local_minima(smoothed: np.ndarray) -> list[int]:
        min_distance = max(1, int(min_section_sec * sr / 512))
        neg = -smoothed
        peaks = []
        for i in range(min_distance, len(neg) - min_distance):
            if neg[i] > neg[i - 1] and neg[i] > neg[i + 1]:
                peaks.append(i)
        return peaks

    def _section_boundaries(times: np.ndarray) -> list[float]:
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
        if len(boundaries) - 1 <= max_sections:
            return boundaries

        step = (len(boundaries) - 1) / max_sections
        indices = [0]
        indices.extend(int(round(step * i)) for i in range(1, max_sections))
        indices.append(len(boundaries) - 1)
        return [boundaries[i] for i in sorted(set(indices))]

    def _build_sections(boundaries: list[float], rms: np.ndarray) -> list[SectionMarker]:
        sections = []
        for i in range(len(boundaries) - 1):
            start = boundaries[i]
            end = boundaries[i + 1]
            start_frame = int(start * sr / 512)
            end_frame = min(int(end * sr / 512), len(rms))
            sec_rms = (
                float(np.mean(rms[start_frame:end_frame]))
                if end_frame > start_frame
                else 0.0
            )
            sections.append(
                SectionMarker(
                    label=f"section_{i + 1:02d}",
                    start_sec=start,
                    end_sec=end,
                    rms_mean=round(sec_rms, 4),
                )
            )
        return sections

    if duration <= min_section_sec:
        return _full_section()

    # Compute RMS envelope
    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    if len(rms) < 4:
        return _full_section()

    # Compute self-similarity novelty curve
    # Use onset strength as a proxy for section boundaries
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    smoothed = _smooth_onset(onset_env)
    peaks = _local_minima(smoothed)

    # Convert frame indices to seconds
    times = librosa.frames_to_time(np.array(peaks), sr=sr, hop_length=512)
    boundaries = _cap_boundaries(_section_boundaries(times))
    sections = _build_sections(boundaries, rms)

    # Safety: guarantee at least one section
    if not sections:
        return _full_section()

    return sections
