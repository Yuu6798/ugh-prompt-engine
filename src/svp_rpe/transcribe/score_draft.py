"""Draft CompositionScore generation from an extracted RPE bundle."""
from __future__ import annotations

from typing import Any

import yaml

from svp_rpe.compose.models import (
    CompositionScore,
    DeltaESpec,
    GrvSpec,
    Meta,
    PhysicalLayer,
    RenderingConfig,
    SemanticLayer,
    StructureSection,
)
from svp_rpe.rpe.models import RPEBundle, SectionMarker
from svp_rpe.transcribe.measure import SCORE_FIELDS, measure_fields
from svp_rpe.transcribe.models import FieldMeasurement

TODO_SENTINEL_PREFIX = "TODO(transcribe):"
TODO_AUTHOR_INPUT = f"{TODO_SENTINEL_PREFIX} author input required"
TODO_STEREO_BAND_UNDEFINED = f"{TODO_SENTINEL_PREFIX} stereo band undefined"
TODO_STEREO_UNMEASURED = f"{TODO_SENTINEL_PREFIX} stereo unmeasured"

DEFAULT_RENDERING = RenderingConfig(
    target_backend="external",
    prompt_max_chars=650,
    priority=[
        "semantic.core",
        "semantic.grv",
        "physical.bpm",
        "physical.key",
        "structure",
        "semantic.avoid",
        "physical.optional",
    ],
)


def draft_score(bundle: RPEBundle) -> CompositionScore:
    """Build a loader-valid draft CompositionScore from measured physical fields."""

    report = measure_fields(bundle, list(SCORE_FIELDS))
    measurements = {item.score_field: item for item in report.measurements}
    physical = PhysicalLayer(
        bpm=_required_int_score(measurements["bpm"]),
        key=_required_str_score(measurements["key"]),
        time_signature=_required_str_score(measurements["time_signature"]),
        active_rate_target=_format_fixed_range(
            _required_float_raw(measurements["active_rate_target"])
        ),
        valley_depth_target=_format_fixed_range(
            _required_float_raw(measurements["valley_depth_target"])
        ),
        brightness=_required_str_score(measurements["brightness"]),
        stereo_width=_stereo_width_value(measurements["stereo_width"]),
    )
    return CompositionScore(
        meta=Meta(title=report.sample_id, version="0.1"),
        semantic=SemanticLayer(
            core=TODO_AUTHOR_INPUT,
            grv=GrvSpec(primary=TODO_AUTHOR_INPUT, secondary=TODO_AUTHOR_INPUT),
            delta_e=DeltaESpec(overall=TODO_AUTHOR_INPUT),
            avoid=[],
        ),
        physical=physical,
        structure=_draft_structure(bundle, physical.bpm, physical.time_signature),
        rendering=DEFAULT_RENDERING.model_copy(deep=True),
    )


def render_draft_score_yaml(score: CompositionScore) -> str:
    """Render a CompositionScore as deterministic YAML."""

    return yaml.safe_dump(
        score.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
    )


def _format_fixed_range(raw_value: float) -> str:
    rounded = round(float(raw_value), 2)
    lower = max(0.0, rounded - 0.02)
    upper = min(1.0, rounded + 0.02)
    return f"{lower:.2f}-{upper:.2f}"


def _draft_structure(
    bundle: RPEBundle,
    bpm: int,
    time_signature: str,
) -> list[StructureSection]:
    return [
        StructureSection(
            section=marker.label,
            bars=_bars_for_section(marker, bpm, time_signature),
            role=TODO_AUTHOR_INPUT,
            physical=TODO_AUTHOR_INPUT,
        )
        for marker in bundle.physical.structure
    ]


def _bars_for_section(marker: SectionMarker, bpm: int, time_signature: str) -> int:
    duration_sec = max(0.0, marker.end_sec - marker.start_sec)
    beats_per_bar = _beats_per_bar(time_signature)
    seconds_per_bar = (60.0 / max(1, bpm)) * beats_per_bar
    return max(1, round(duration_sec / seconds_per_bar))


def _beats_per_bar(time_signature: str) -> int:
    try:
        beats = int(str(time_signature).split("/", maxsplit=1)[0])
    except (TypeError, ValueError):
        return 4
    return beats if beats > 0 else 4


def _stereo_width_value(measurement: FieldMeasurement) -> str:
    if measurement.raw_value is None:
        return TODO_STEREO_UNMEASURED
    return TODO_STEREO_BAND_UNDEFINED


def _required_int_score(measurement: FieldMeasurement) -> int:
    value = measurement.score_value
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{measurement.score_field} has no integer score_value")
    return int(value)


def _required_str_score(measurement: FieldMeasurement) -> str:
    value = measurement.score_value
    if value is None:
        raise ValueError(f"{measurement.score_field} has no string score_value")
    return str(value)


def _required_float_raw(measurement: FieldMeasurement) -> float:
    value: Any = measurement.raw_value
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{measurement.score_field} has no numeric raw_value")
    return float(value)
