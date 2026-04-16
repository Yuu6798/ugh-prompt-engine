"""eval/anchor_matcher.py — GRV anchor matching between RPE and SVP."""
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
    checks = 0
    total = 0

    # Primary anchor match
    total += 1
    if primary_a.lower() == primary_b.lower():
        checks += 1
    elif primary_a.lower() in primary_b.lower() or primary_b.lower() in primary_a.lower():
        checks += 0.5

    # BPM proximity (within ±10)
    if bpm_a is not None and bpm_b is not None:
        total += 1
        if abs(bpm_a - bpm_b) <= 10:
            checks += 1
        elif abs(bpm_a - bpm_b) <= 20:
            checks += 0.5

    # Key match
    if key_a and key_b:
        total += 1
        if key_a.lower() == key_b.lower():
            checks += 1

    # Duration proximity (within 10%)
    if duration_a and duration_b and duration_a > 0:
        total += 1
        ratio = abs(duration_a - duration_b) / duration_a
        if ratio <= 0.1:
            checks += 1
        elif ratio <= 0.2:
            checks += 0.5

    # Anchor term overlap
    if anchors_a and anchors_b:
        total += 1
        set_a = set(t.lower() for t in anchors_a)
        set_b = set(t.lower() for t in anchors_b)
        if set_a and set_b:
            overlap = len(set_a & set_b) / max(len(set_a), len(set_b))
            checks += overlap

    return _clamp(checks / max(total, 1))
