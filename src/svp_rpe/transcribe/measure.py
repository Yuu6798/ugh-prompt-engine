"""Measure CompositionScore physical fields from an extracted RPE bundle."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from svp_rpe.rpe.models import RPEBundle
from svp_rpe.semantic_ci import rpe_bundle_to_observed
from svp_rpe.semantic_ci.core import SENSOR_METRIC_OVERRIDES
from svp_rpe.transcribe.models import FieldMeasurement, MeasurementReport

SCORE_FIELDS = (
    "bpm",
    "key",
    "time_signature",
    "brightness",
    "active_rate_target",
    "valley_depth_target",
    "stereo_width",
)

CALIBRATION_NOTES: dict[str, list[str]] = {
    "bpm": [
        "High-level tempo targets can measure low (K1: target 140 -> observed 127; "
        "docs/controllability_poc.md section 5.1).",
        "Confidence is CV-based; systematic ground-truth validation is not recorded "
        "(docs/roadmap_goal1.md Q1-3 inventory).",
    ],
    "key": [
        "Validation matched 5/5 synthetic fixtures (docs/validation.md section 2).",
    ],
    "time_signature": [
        "Validation is 5/5 exact, but the 3/4 fixture has only one sample and the "
        "acceptance gate remains incomplete (docs/roadmap_goal1.md Q1-2 inventory).",
    ],
    "brightness": [
        "Canonical sensor is spectral_centroid with dark <=1200 Hz and bright >=2500 Hz "
        "(PR #66); legacy band-ratio brightness is not used.",
        "The interior band (1200, 2500) is reported as the first-class label 'neutral'.",
    ],
    "active_rate_target": [
        "Range-string conversion for score fields is intentionally deferred to T1.",
    ],
    "valley_depth_target": [
        "Range-string conversion for score fields is intentionally deferred to T1.",
    ],
    "stereo_width": [
        "Label bands are undefined; missing stereo_profile is reported as null.",
    ],
}

_BRIGHTNESS_SENSOR = SENSOR_METRIC_OVERRIDES["brightness"]


def parse_field_filter(fields: str | Iterable[str] | None) -> list[str]:
    """Parse and validate a comma-separated field filter."""

    if fields is None:
        return list(SCORE_FIELDS)
    if isinstance(fields, str):
        requested = [item.strip() for item in fields.split(",") if item.strip()]
    else:
        requested = [str(item).strip() for item in fields if str(item).strip()]
    if not requested:
        return list(SCORE_FIELDS)

    unknown = sorted(set(requested) - set(SCORE_FIELDS))
    if unknown:
        valid = ", ".join(SCORE_FIELDS)
        raise ValueError(f"unknown field(s): {', '.join(unknown)}; valid fields: {valid}")
    return requested


def measure_fields(bundle: RPEBundle, fields: list[str]) -> MeasurementReport:
    """Return deterministic per-field measurements for an extracted RPE bundle."""

    requested = parse_field_filter(fields)
    observed = rpe_bundle_to_observed(bundle, id=_sample_id(bundle))
    metrics = observed.metrics
    measurements = [_measure_field(field, metrics) for field in requested]
    return MeasurementReport(
        sample_id=_sample_id(bundle),
        duration_sec=_round_float(bundle.audio_duration_sec),
        measurements=measurements,
    )


def render_measurement_json(report: MeasurementReport) -> str:
    """Render a report as stable JSON for snapshots and CLI output files."""

    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def _measure_field(field: str, metrics: dict[str, Any]) -> FieldMeasurement:
    if field == "bpm":
        raw = _optional_float(metrics.get("bpm"))
        return FieldMeasurement(
            score_field=field,
            sensor="physical.bpm",
            raw_value=_round_optional(raw),
            unit="BPM",
            score_value=round(raw) if raw is not None else None,
            calibration=CALIBRATION_NOTES[field],
        )
    if field == "key":
        raw = _optional_str(metrics.get("key"))
        return FieldMeasurement(
            score_field=field,
            sensor='f"{key} {mode}"',
            raw_value=raw,
            unit=None,
            score_value=raw,
            calibration=CALIBRATION_NOTES[field],
        )
    if field == "time_signature":
        raw = _optional_str(metrics.get("time_signature"))
        return FieldMeasurement(
            score_field=field,
            sensor="physical.time_signature",
            raw_value=raw,
            unit=None,
            score_value=raw,
            calibration=CALIBRATION_NOTES[field],
        )
    if field == "brightness":
        raw = _optional_float(metrics.get(_BRIGHTNESS_SENSOR))
        return FieldMeasurement(
            score_field=field,
            sensor=f"physical.{_BRIGHTNESS_SENSOR}",
            raw_value=_round_optional(raw),
            unit="Hz",
            score_value=_brightness_score_value(raw),
            calibration=CALIBRATION_NOTES[field],
        )
    if field == "active_rate_target":
        raw = _optional_float(metrics.get("active_rate"))
        return FieldMeasurement(
            score_field=field,
            sensor="physical.active_rate",
            raw_value=_round_optional(raw),
            unit="ratio",
            score_value=None,
            calibration=CALIBRATION_NOTES[field],
        )
    if field == "valley_depth_target":
        raw = _optional_float(metrics.get("valley_depth"))
        return FieldMeasurement(
            score_field=field,
            sensor="physical.valley_depth",
            raw_value=_round_optional(raw),
            unit="ratio",
            score_value=None,
            calibration=CALIBRATION_NOTES[field],
        )
    if field == "stereo_width":
        raw = _optional_float(metrics.get("stereo_width"))
        return FieldMeasurement(
            score_field=field,
            sensor="physical.stereo_profile.width",
            raw_value=_round_optional(raw),
            unit="ratio",
            score_value=None,
            calibration=CALIBRATION_NOTES[field],
        )
    raise ValueError(f"unknown field: {field}")


def _brightness_score_value(raw_value: float | None) -> str | None:
    if raw_value is None:
        return None
    if raw_value <= 1200.0:
        return "dark"
    if raw_value >= 2500.0:
        return "bright"
    return "neutral"


def _sample_id(bundle: RPEBundle) -> str:
    return Path(bundle.audio_file).stem or "unknown"


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _round_optional(value: float | None) -> float | None:
    if value is None:
        return None
    return _round_float(value)


def _round_float(value: float) -> float:
    return round(float(value), 6)
