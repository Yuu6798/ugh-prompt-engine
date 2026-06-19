"""Shared field comparison helpers for roundtrip diagnostics and corpus runs."""
from __future__ import annotations

import re
from typing import Any, Sequence

from svp_rpe.perform import parse_key
from svp_rpe.sentinels import is_todo_sentinel

BPM_MATCH_TOLERANCE = 5.0
RANGE_PATTERN = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\s*$"
)


def values_match(field: str, expected_value: Any, observed_value: Any) -> bool:
    """Return whether two score-field values match under roundtrip calibration."""

    if is_todo_sentinel(expected_value) or is_todo_sentinel(observed_value):
        return False
    if field == "bpm":
        expected_number = number(expected_value)
        observed_number = number(observed_value)
        return (
            expected_number is not None
            and observed_number is not None
            and abs(expected_number - observed_number) <= BPM_MATCH_TOLERANCE
        )
    if field == "key":
        return keys_match(expected_value, observed_value)
    if field in {"active_rate_target", "valley_depth_target"}:
        expected_range = parse_numeric_range(expected_value)
        observed_range = parse_numeric_range(observed_value)
        if expected_range is not None and observed_range is not None:
            return ranges_overlap(expected_range, observed_range)
        if expected_range is not None:
            observed_point = number(observed_value)
            if observed_point is not None:
                return point_in_range(observed_point, expected_range)
        if observed_range is not None:
            expected_point = number(expected_value)
            if expected_point is not None:
                return point_in_range(expected_point, observed_range)
        return str(expected_value) == str(observed_value)
    return normalize_label(expected_value) == normalize_label(observed_value)


def chord_sequence_match_rate(
    source: Sequence[tuple[str, str]],
    transcribed: Sequence[tuple[str, str]],
) -> float:
    """Return position-aligned match rate for ``(root, quality)`` chord sequences."""

    if not source and not transcribed:
        return 1.0
    denominator = max(len(source), len(transcribed))
    if denominator == 0:
        return 1.0
    matches = sum(
        1
        for source_chord, transcribed_chord in zip(source, transcribed)
        if source_chord == transcribed_chord
    )
    return matches / denominator


def parse_numeric_range(value: Any) -> tuple[float, float] | None:
    """Parse an inclusive numeric range string such as ``0.80-0.85``."""

    if not isinstance(value, str):
        return None
    match = RANGE_PATTERN.match(value)
    if match is None:
        return None
    lower = float(match.group(1))
    upper = float(match.group(2))
    if lower > upper:
        lower, upper = upper, lower
    return lower, upper


def ranges_overlap(first: tuple[float, float], second: tuple[float, float]) -> bool:
    """Return whether two inclusive numeric ranges overlap."""

    return max(first[0], second[0]) <= min(first[1], second[1])


def point_in_range(value: float, value_range: tuple[float, float]) -> bool:
    """Return whether a numeric sensor point falls inside an inclusive range."""

    return value_range[0] <= value <= value_range[1]


def number(value: Any) -> float | None:
    """Return a float for numeric-like values, excluding bool/null."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def keys_match(expected_value: Any, observed_value: Any) -> bool:
    """Compare key labels with enharmonic parsing when possible."""

    expected_key = parse_key_label(expected_value)
    observed_key = parse_key_label(observed_value)
    if expected_key is not None and observed_key is not None:
        return expected_key == observed_key
    return normalize_label(expected_value) == normalize_label(observed_value)


def parse_key_label(value: Any) -> tuple[int, str] | None:
    """Parse a key label using the deterministic performer key parser."""

    if not isinstance(value, str):
        return None
    normalized = (
        value.strip()
        .replace("\u266f", "#")
        .replace("\uff03", "#")
        .replace("\u266d", "b")
    )
    try:
        return parse_key(normalized)
    except ValueError:
        return None


def normalize_label(value: Any) -> str:
    """Normalize a label for stable string comparison and ids."""

    return " ".join(str(value).strip().lower().split())
