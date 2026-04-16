"""eval/delta_e_alignment.py — ΔE profile alignment scoring."""
from __future__ import annotations


_TRANSITION_TYPES = {
    "flat", "sustained_energy", "gradual_build", "dramatic_contrast",
    "sudden_drop", "crescendo", "decrescendo",
}

_COMPATIBLE_PAIRS = {
    ("gradual_build", "crescendo"),
    ("dramatic_contrast", "sudden_drop"),
    ("sustained_energy", "flat"),
}


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def delta_e_profile_alignment(
    profile_a: str,
    profile_b: str,
    intensity_a: float = 0.5,
    intensity_b: float = 0.5,
) -> float:
    """Score alignment between two ΔE profiles.

    Compares transition type similarity and intensity proximity.
    """
    # Type match
    type_a = profile_a.lower().replace("-", "_").replace(" ", "_")
    type_b = profile_b.lower().replace("-", "_").replace(" ", "_")

    if type_a == type_b:
        type_score = 1.0
    elif (type_a, type_b) in _COMPATIBLE_PAIRS or (type_b, type_a) in _COMPATIBLE_PAIRS:
        type_score = 0.7
    elif any(w in type_b for w in type_a.split("_")) or any(w in type_a for w in type_b.split("_")):
        type_score = 0.4
    else:
        type_score = 0.0

    # Intensity proximity
    intensity_score = 1.0 - min(abs(intensity_a - intensity_b), 1.0)

    return round(_clamp(type_score * 0.7 + intensity_score * 0.3), 4)
