"""rpe/valley.py — Valley depth estimation with strategy pattern.

Methods: rms_percentile, section_ar, hybrid (default).
All methods return (value, ValleyDiagnostics).
"""
from __future__ import annotations

from typing import List

import librosa
import numpy as np

from svp_rpe.eval.diff_models import ValleyDiagnostics
from svp_rpe.rpe.models import SectionMarker


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def valley_rms_percentile(y: np.ndarray, sr: int) -> tuple[float, dict]:
    """P90 - P10 of frame RMS."""
    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    if len(rms) < 2:
        return 0.0, {"rms_p90": 0.0, "rms_p10": 0.0}
    p90 = float(np.percentile(rms, 90))
    p10 = float(np.percentile(rms, 10))
    return round(max(0.0, p90 - p10), 4), {"rms_p90": round(p90, 4), "rms_p10": round(p10, 4)}


def valley_section_ar(
    y: np.ndarray,
    sr: int,
    sections: List[SectionMarker],
    threshold: float = 0.01,
) -> tuple[float, dict]:
    """Active Rate variation across sections: AR_main - AR_min."""
    if not sections:
        return 0.0, {"ar_main": 0.0, "ar_min": 0.0}

    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    section_ars: list[tuple[str, float]] = []

    for sec in sections:
        start_frame = int(sec.start_sec * sr / 512)
        end_frame = min(int(sec.end_sec * sr / 512), len(rms))
        if end_frame <= start_frame:
            continue
        sec_rms = rms[start_frame:end_frame]
        ar = float(np.sum(sec_rms > threshold) / len(sec_rms)) if len(sec_rms) > 0 else 0.0
        section_ars.append((sec.label, ar))

    if not section_ars:
        return 0.0, {"ar_main": 0.0, "ar_min": 0.0}

    ar_values = [ar for _, ar in section_ars]
    ar_main = float(np.mean(ar_values))
    ar_min = float(min(ar_values))
    lowest = min(section_ars, key=lambda x: x[1])

    # Identify chorus-like sections (highest AR)
    sorted_by_ar = sorted(section_ars, key=lambda x: x[1], reverse=True)
    chorus_sections = [name for name, ar in sorted_by_ar[:2]]

    value = round(max(0.0, ar_main - ar_min), 4)

    return value, {
        "ar_main": round(ar_main, 4),
        "ar_min": round(ar_min, 4),
        "lowest_section": lowest[0],
        "chorus_sections": chorus_sections,
    }


def compute_valley_depth(
    y: np.ndarray,
    sr: int,
    sections: List[SectionMarker],
    *,
    method: str = "hybrid",
) -> tuple[float, ValleyDiagnostics]:
    """Compute valley depth with selectable strategy.

    Methods:
        - "rms_percentile": P90 - P10 of frame RMS
        - "section_ar": AR_main - AR_min across sections
        - "hybrid": 0.5 * rms_percentile + 0.5 * section_ar (default)

    Returns (valley_depth, diagnostics).
    """
    rms_val, rms_diag = valley_rms_percentile(y, sr)
    ar_val, ar_diag = valley_section_ar(y, sr, sections)

    if method == "rms_percentile":
        value = rms_val
    elif method == "section_ar":
        value = ar_val
    elif method == "hybrid":
        value = round(0.5 * rms_val + 0.5 * ar_val, 4)
    else:
        raise ValueError(f"unknown valley method: {method}. use rms_percentile/section_ar/hybrid")

    # Confidence based on data quality
    confidence = 0.5
    if len(sections) >= 3:
        confidence += 0.2
    if rms_val > 0.01:
        confidence += 0.15
    if ar_val > 0.01:
        confidence += 0.15
    confidence = min(1.0, confidence)

    diagnostics = ValleyDiagnostics(
        method=method,
        rms_p90=rms_diag.get("rms_p90", 0.0),
        rms_p10=rms_diag.get("rms_p10", 0.0),
        ar_main=ar_diag.get("ar_main", 0.0),
        ar_min=ar_diag.get("ar_min", 0.0),
        chorus_sections=ar_diag.get("chorus_sections", []),
        lowest_section=ar_diag.get("lowest_section", ""),
        confidence=round(confidence, 4),
        rms_percentile_value=rms_val,
        section_ar_value=ar_val,
        hybrid_value=round(0.5 * rms_val + 0.5 * ar_val, 4),
    )

    return value, diagnostics
