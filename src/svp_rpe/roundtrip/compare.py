"""Shared field comparison helpers for roundtrip diagnostics and corpus runs."""
from __future__ import annotations

import re
from typing import Any

from svp_rpe.perform import parse_key

BPM_MATCH_TOLERANCE = 5.0
RANGE_PATTERN = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\s*$"
)
_TRANSCRIBE_TODO_PREFIX = "TODO(transcribe):"


def values_match(field: str, expected_value: Any, observed_value: Any) -> bool:
    """Return whether two score-field values match under roundtrip calibration."""

    if _is_todo(expected_value) or _is_todo(observed_value):
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
        if expected_range is None or observed_range is None:
            return str(expected_value) == str(observed_value)
        return ranges_overlap(expected_range, observed_range)
    return normalize_label(expected_value) == normalize_label(observed_value)


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


def _is_todo(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_TRANSCRIBE_TODO_PREFIX)
