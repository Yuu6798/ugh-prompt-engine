"""GRV anchor matching between RPE and SVP."""
from __future__ import annotations

from typing import List, Optional


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def grv_anchor_match(
    *,
    primary_a: str,
    primary_b: str,
    bpm_a: Optional[float],
    bpm_b: Optional[float],
    key_a: Optional[str],
    key_b: Optional[str],
    duration_a: Optional[float] = None,
    duration_b: Optional[float] = None,
    anchors_a: Optional[List[str]] = None,
    anchors_b: Optional[List[str]] = None,
) -> float:
    """Score anchor alignment between two RPE/SVP sources.

    Checks: primary match, BPM proximity, key match, duration proximity,
    and anchor term overlap.
    """
    def primary_score() -> float:
        primary_a_l = primary_a.lower()
        primary_b_l = primary_b.lower()
        if primary_a_l == primary_b_l:
            return 1.0
        if primary_a_l in primary_b_l or primary_b_l in primary_a_l:
            return 0.5
        return 0.0

    def bpm_score() -> float | None:
        if bpm_a is None or bpm_b is None:
            return None
        diff = abs(bpm_a - bpm_b)
        if diff <= 10:
            return 1.0
        if diff <= 20:
            return 0.5
        return 0.0

    def key_score() -> float | None:
        if not key_a or not key_b:
            return None
        return 1.0 if key_a.lower() == key_b.lower() else 0.0

    def duration_score() -> float | None:
        if not duration_a or not duration_b or duration_a <= 0:
            return None
        ratio = abs(duration_a - duration_b) / duration_a
        if ratio <= 0.1:
            return 1.0
        if ratio <= 0.2:
            return 0.5
        return 0.0

    def anchor_overlap_score() -> float | None:
        if not anchors_a or not anchors_b:
            return None
        set_a = set(t.lower() for t in anchors_a)
        set_b = set(t.lower() for t in anchors_b)
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / max(len(set_a), len(set_b))

    scores = [
        primary_score(),
        bpm_score(),
        key_score(),
        duration_score(),
        anchor_overlap_score(),
    ]
    active_scores = [score for score in scores if score is not None]
    return _clamp(sum(active_scores) / max(len(active_scores), 1))
