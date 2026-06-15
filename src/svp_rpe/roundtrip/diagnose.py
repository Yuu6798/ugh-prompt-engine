"""Three-state roundtrip preservation diagnosis."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from svp_rpe.compose.models import CompositionScore
from svp_rpe.roundtrip.models import RoundtripField, RoundtripReport
from svp_rpe.transcribe import (
    TODO_BPM_UNDETECTED,
    TODO_BRIGHTNESS_NEUTRAL,
    TODO_KEY_UNDETECTED,
    TODO_SENTINEL_PREFIX,
    TODO_STEREO_BAND_UNDEFINED,
    TODO_STEREO_UNMEASURED,
    TODO_TIME_SIGNATURE_UNDETECTED,
)

RANGE_PATTERN = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\s*$"
)

ROUNDTRIP_FIELDS = (
    "bpm",
    "key",
    "time_signature",
    "brightness",
    "active_rate_target",
    "valley_depth_target",
    "stereo_width",
)

FIELD_SENSORS = {
    "bpm": "physical.bpm",
    "key": 'f"{key} {mode}"',
    "time_signature": "physical.time_signature",
    "brightness": "physical.spectral_centroid",
    "active_rate_target": "physical.active_rate",
    "valley_depth_target": "physical.valley_depth",
    "stereo_width": "physical.stereo_profile.width",
}

GRIP_KEYS = {
    "bpm": "bpm",
    "key": "key",
    "brightness": "brightness",
    "active_rate_target": "active_rate_target",
    "valley_depth_target": "valley_depth_target",
    "stereo_width": "stereo_width",
}

SENSOR_BLIND_SENTINELS = {
    TODO_BPM_UNDETECTED,
    TODO_BRIGHTNESS_NEUTRAL,
    TODO_KEY_UNDETECTED,
    TODO_STEREO_BAND_UNDEFINED,
    TODO_STEREO_UNMEASURED,
    TODO_TIME_SIGNATURE_UNDETECTED,
}

BPM_MATCH_TOLERANCE = 5.0


@dataclass(frozen=True)
class GripRecord:
    """Grip fixture record used by the roundtrip diagnosis."""

    knob: str
    sensor: str
    grip: float
    classification: str


def diagnose_roundtrip(
    source: CompositionScore,
    transcribed: CompositionScore,
    *,
    grip_map: Mapping[str, GripRecord] | None = None,
) -> RoundtripReport:
    """Compare two CompositionScores as descriptive roundtrip needles."""

    grips = grip_map if grip_map is not None else load_grip_map()
    fields = [
        _diagnose_field(field, source, transcribed, grips)
        for field in ROUNDTRIP_FIELDS
    ]
    return RoundtripReport(
        source_id=_score_id(source),
        transcribed_id=_score_id(transcribed),
        fields=fields,
    )


def load_grip_map(path: Path | None = None) -> dict[str, GripRecord]:
    """Load the K1 grip map used as the control-track reference."""

    fixture_path = path or _default_grip_path()
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    records: dict[str, GripRecord] = {}
    for item in payload.get("results", []):
        knob = str(item["knob"])
        records[knob] = GripRecord(
            knob=knob,
            sensor=str(item.get("sensor", "")),
            grip=float(item.get("grip", 0.0)),
            classification=str(item.get("classification", "")),
        )
    return records


def _diagnose_field(
    field: str,
    source: CompositionScore,
    transcribed: CompositionScore,
    grip_map: Mapping[str, GripRecord],
) -> RoundtripField:
    source_value = _physical_value(source, field)
    transcribed_value = _physical_value(transcribed, field)
    grip = grip_map.get(GRIP_KEYS.get(field, field))
    sensor_blind = _is_sensor_blind(field, transcribed_value)
    matched = _values_match(field, source_value, transcribed_value)

    diagnosis = "preserved"
    note = None
    if not matched:
        if sensor_blind:
            diagnosis = "sensor_blind"
            note = _sensor_blind_note(field, transcribed_value)
        elif grip is not None and grip.classification == "dead":
            diagnosis = "knob_dead"
            note = "K1 grip map classifies this control as dead."
        elif grip is not None and grip.classification == "tight":
            diagnosis = "calibration_disagreement"
            note = "K1 grip is tight, but the working sensor disagrees."
        else:
            diagnosis = "calibration_disagreement"
            note = "Grip class is unavailable or non-tight; inspect calibration."

    return RoundtripField(
        field=field,
        source_value=source_value,
        transcribed_value=transcribed_value,
        diagnosis=diagnosis,
        grip=round(grip.grip, 6) if grip is not None else None,
        grip_class=grip.classification if grip is not None else None,
        sensor=FIELD_SENSORS[field],
        sensor_state="blind" if sensor_blind else "working",
        note=note,
    )


def _physical_value(score: CompositionScore, field: str) -> Any:
    physical = score.physical
    if field == "bpm":
        return physical.bpm
    if field == "key":
        return physical.key
    if field == "time_signature":
        return physical.time_signature
    if field == "brightness":
        return physical.brightness
    if field == "active_rate_target":
        return physical.active_rate_target
    if field == "valley_depth_target":
        return physical.valley_depth_target
    if field == "stereo_width":
        return physical.stereo_width
    raise ValueError(f"unknown roundtrip field: {field}")


def _values_match(field: str, source_value: Any, transcribed_value: Any) -> bool:
    if _is_todo(source_value) or _is_todo(transcribed_value):
        return False
    if field == "bpm":
        source_number = _number(source_value)
        transcribed_number = _number(transcribed_value)
        return (
            source_number is not None
            and transcribed_number is not None
            and abs(source_number - transcribed_number) <= BPM_MATCH_TOLERANCE
        )
    if field in {"active_rate_target", "valley_depth_target"}:
        source_range = _parse_numeric_range(source_value)
        transcribed_range = _parse_numeric_range(transcribed_value)
        if source_range is None or transcribed_range is None:
            return str(source_value) == str(transcribed_value)
        return _ranges_overlap(source_range, transcribed_range)
    return _normalize_label(source_value) == _normalize_label(transcribed_value)


def _is_sensor_blind(field: str, transcribed_value: Any) -> bool:
    if transcribed_value is None or _is_todo(transcribed_value):
        return True
    if field == "brightness" and transcribed_value == TODO_BRIGHTNESS_NEUTRAL:
        return True
    if field == "stereo_width":
        # T1 intentionally leaves stereo labels undefined even when the width is
        # measured; this is a blind sensor until a stereo band map exists.
        return True
    return False


def _sensor_blind_note(field: str, transcribed_value: Any) -> str:
    if field == "brightness" and transcribed_value == TODO_BRIGHTNESS_NEUTRAL:
        return "spectral centroid fell into the neutral calibration band."
    if field == "stereo_width":
        return "stereo label bands are not calibrated for T1 drafts."
    if _is_todo(transcribed_value):
        return str(transcribed_value)
    return "sensor value is missing."


def _parse_numeric_range(value: Any) -> tuple[float, float] | None:
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


def _ranges_overlap(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return max(first[0], second[0]) <= min(first[1], second[1])


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_todo(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(TODO_SENTINEL_PREFIX)


def _normalize_label(value: Any) -> str:
    return " ".join(str(value).strip().lower().split())


def _score_id(score: CompositionScore) -> str:
    return _normalize_label(score.meta.title).replace(" ", "-") or "score"


def _default_grip_path() -> Path:
    return Path(__file__).resolve().parents[3] / "examples" / "control" / "k1" / "expected_grip.json"
