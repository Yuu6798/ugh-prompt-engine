"""Roundtrip preservation diagnosis."""
from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Mapping

from svp_rpe.compose.models import CompositionScore
from svp_rpe.roundtrip.compare import (
    normalize_label,
    repeated_chord_sequence_match_rate,
    values_match,
)
from svp_rpe.roundtrip.models import RoundtripField, RoundtripReport
from svp_rpe.sentinels import is_todo_sentinel
from svp_rpe.transcribe import (
    TODO_BPM_UNDETECTED,
    TODO_BRIGHTNESS_NEUTRAL,
    TODO_KEY_UNDETECTED,
    TODO_STEREO_BAND_UNDEFINED,
    TODO_STEREO_UNMEASURED,
    TODO_TIME_SIGNATURE_UNDETECTED,
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
CHORD_PROGRESSION_FIELD = "chord_progression"
CHORD_MATCH_THRESHOLD = 0.75

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
    if _has_chord_progression(source):
        fields.append(_diagnose_chord_progression(source, transcribed, grips))
    return RoundtripReport(
        source_id=_score_id(source),
        transcribed_id=_score_id(transcribed),
        fields=fields,
    )


def load_grip_map(path: Path | Traversable | None = None) -> dict[str, GripRecord]:
    """Load the K1 grip map used as the control-track reference."""

    fixture = path or _default_grip_resource()
    payload = json.loads(fixture.read_text(encoding="utf-8"))
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
    matched = values_match(field, source_value, transcribed_value)

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


def _diagnose_chord_progression(
    source: CompositionScore,
    transcribed: CompositionScore,
    grip_map: Mapping[str, GripRecord],
) -> RoundtripField:
    source_sequence = _chord_sequence(source)
    transcribed_sequence = _chord_sequence(transcribed)
    source_value = _chord_value(source_sequence)
    transcribed_value = _chord_value(transcribed_sequence)
    rate = repeated_chord_sequence_match_rate(source_sequence, transcribed_sequence)
    grip = grip_map.get(CHORD_PROGRESSION_FIELD)
    sensor_blind = len(transcribed_sequence) == 0

    diagnosis = "preserved"
    if sensor_blind:
        diagnosis = "sensor_blind"
    elif grip is not None and grip.classification == "dead":
        diagnosis = "knob_dead"
    elif rate < CHORD_MATCH_THRESHOLD:
        diagnosis = "calibration_disagreement"

    return RoundtripField(
        field=CHORD_PROGRESSION_FIELD,
        source_value=source_value,
        transcribed_value=transcribed_value,
        diagnosis=diagnosis,
        grip=round(grip.grip, 6) if grip is not None else None,
        grip_class=grip.classification if grip is not None else None,
        sensor="compute_chord_events",
        sensor_state="blind" if sensor_blind else "working",
        note=f"chord sequence match rate={round(rate, 3)} threshold={CHORD_MATCH_THRESHOLD}",
    )


def _has_chord_progression(score: CompositionScore) -> bool:
    return bool(score.events is not None and score.events.chord_progression)


def _chord_sequence(score: CompositionScore) -> list[tuple[str, str]]:
    if score.events is None:
        return []
    return [(chord.root, chord.quality) for chord in score.events.chord_progression]


def _chord_value(sequence: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"root": root, "quality": quality} for root, quality in sequence]


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


def _is_sensor_blind(field: str, transcribed_value: Any) -> bool:
    if transcribed_value is None or is_todo_sentinel(transcribed_value):
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
    if is_todo_sentinel(transcribed_value):
        return str(transcribed_value)
    return "sensor value is missing."


def _score_id(score: CompositionScore) -> str:
    return normalize_label(score.meta.title).replace(" ", "-") or "score"


def _default_grip_resource() -> Traversable:
    return files("svp_rpe.roundtrip.data").joinpath("expected_grip.json")
