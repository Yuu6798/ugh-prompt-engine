"""Draft CompositionScore generation from an extracted RPE bundle."""
from __future__ import annotations

from typing import Any

import yaml

from svp_rpe.compose.models import (
    ChordSpec,
    CompositionScore,
    DeltaESpec,
    EventLayer,
    FixityState,
    GrvSpec,
    Meta,
    PhysicalLayer,
    RenderingConfig,
    SemanticLayer,
    StructureSection,
)
from svp_rpe.sentinels import TODO_SENTINEL_PREFIX, is_todo_sentinel
from svp_rpe.rpe.models import LearnedAudioAnnotations, RPEBundle, SectionMarker
from svp_rpe.transcribe.measure import SCORE_FIELDS, measure_fields
from svp_rpe.transcribe.models import FieldMeasurement

TODO_AUTHOR_INPUT = f"{TODO_SENTINEL_PREFIX} author input required"
TODO_BPM_UNDETECTED = f"{TODO_SENTINEL_PREFIX} bpm undetected"
TODO_BRIGHTNESS_MISSING = f"{TODO_SENTINEL_PREFIX} brightness sensor unavailable"
TODO_KEY_UNDETECTED = f"{TODO_SENTINEL_PREFIX} key undetected"
TODO_STEREO_BAND_UNDEFINED = f"{TODO_SENTINEL_PREFIX} stereo band undefined"
TODO_STEREO_UNMEASURED = f"{TODO_SENTINEL_PREFIX} stereo unmeasured"
TODO_TIME_SIGNATURE_UNDETECTED = f"{TODO_SENTINEL_PREFIX} time signature undetected"

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
    bpm_measurement = measurements["bpm"]
    time_signature = _time_signature_value(measurements["time_signature"], bundle)
    physical = PhysicalLayer(
        bpm=_bpm_value(bpm_measurement, bundle),
        key=_key_value(measurements["key"], bundle),
        time_signature=time_signature,
        active_rate_target=_format_fixed_range(
            _required_float_raw(measurements["active_rate_target"])
        ),
        valley_depth_target=_format_fixed_range(
            _required_float_raw(measurements["valley_depth_target"])
        ),
        brightness=_brightness_value(measurements["brightness"]),
        stereo_width=_stereo_width_value(measurements["stereo_width"]),
    )
    events = _draft_events(bundle)
    return CompositionScore(
        meta=Meta(title=report.sample_id, version="0.1"),
        semantic=SemanticLayer(
            core=TODO_AUTHOR_INPUT,
            grv=GrvSpec(primary=TODO_AUTHOR_INPUT, secondary=TODO_AUTHOR_INPUT),
            delta_e=DeltaESpec(overall=TODO_AUTHOR_INPUT),
            avoid=[],
        ),
        physical=physical,
        structure=_draft_structure(
            bundle,
            _bpm_for_structure(bpm_measurement, bundle),
            _time_signature_for_structure(time_signature),
        ),
        events=events,
        rendering=DEFAULT_RENDERING.model_copy(deep=True),
        fixity=_draft_fixity(physical, events),
    )


def render_draft_score_yaml(score: CompositionScore) -> str:
    """Render a CompositionScore as deterministic YAML."""

    return yaml.safe_dump(
        score.model_dump(mode="json"),
        sort_keys=False,
        allow_unicode=True,
    )


def render_semantic_axes_advisory(annotations: LearnedAudioAnnotations) -> str:
    """Render CLAP semantic-axis readings as a YAML comment block.

    Returns a `#`-commented advisory header (empty string when there are no
    `semantic_axes`) meant to be prepended to a draft-score YAML. Every line
    is a comment, so the combined document stays loader-valid
    (`yaml.safe_load` / `load_composition_score` ignore comments).

    This deliberately does NOT write into `SemanticLayer.core` / `grv` /
    `delta_e`: those remain the author's fields (DD-D,
    docs/score_centric_planning.md §5). The readings are surfaced next to the
    blank fields as instrument context for authoring — the "計器 = 作曲前の
    パラメータ取得道具" use — never as auto-filled values. `contrast_fit` is a
    signed learned grip, not a verdict (docs/semantic_sensor_clap.md).
    """
    axes = annotations.semantic_axes
    if not axes:
        return ""
    width = max(len(axis.axis) for axis in axes)
    lines = [
        "# ---- CLAP semantic sensor (advisory) ----",
        "# Learned A/B contrast_fit readings of the SOURCE audio (signed grip,",
        "# not a verdict). Instrument context for authoring the semantic.*",
        "# fields below — they are NOT auto-filled: semantic.core / grv /",
        "# delta_e stay TODO for the author (DD-D). See docs/semantic_sensor_clap.md.",
    ]
    lines.extend(
        f"#   {axis.axis:<{width}} : {axis.contrast_fit:+.4f}" for axis in axes
    )
    lines.append(f"# source_model: {axes[0].source_model}")
    lines.append("# ------------------------------------------")
    return "\n".join(lines) + "\n"


def _format_fixed_range(raw_value: float) -> str:
    rounded = round(float(raw_value), 2)
    lower = max(0.0, rounded - 0.02)
    upper = min(1.0, rounded + 0.02)
    return f"{lower:.2f}-{upper:.2f}"


def _draft_structure(
    bundle: RPEBundle,
    bpm: int | None,
    time_signature: str | None,
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


def _bars_for_section(
    marker: SectionMarker,
    bpm: int | None,
    time_signature: str | None,
) -> int:
    if bpm is None or time_signature is None:
        return 1
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


def _brightness_value(measurement: FieldMeasurement) -> str:
    if measurement.score_value is None:
        return TODO_BRIGHTNESS_MISSING
    return str(measurement.score_value)


def _key_value(measurement: FieldMeasurement, bundle: RPEBundle) -> str:
    if measurement.score_value is None or _is_untrusted_confidence(
        bundle.physical.key_confidence
    ):
        return TODO_KEY_UNDETECTED
    return _required_str_score(measurement)


def _bpm_untrusted(bundle: RPEBundle) -> bool:
    # An octave-ambiguous (half-fold) BPM is treated as sensor-blind: the
    # estimate may be ×2 off (roundtrip_case_studies.md §4), so transcribing it
    # as a trusted, locked value would let a half-folded match be diagnosed as
    # faithful. The CV-based confidence gate is the separate, regularity axis.
    return bundle.physical.bpm_octave_ambiguous or _is_untrusted_confidence(
        bundle.physical.bpm_confidence
    )


def _bpm_value(measurement: FieldMeasurement, bundle: RPEBundle) -> int | str:
    if measurement.score_value is None or _bpm_untrusted(bundle):
        return TODO_BPM_UNDETECTED
    return _required_int_score(measurement)


def _bpm_for_structure(measurement: FieldMeasurement, bundle: RPEBundle) -> int | None:
    if measurement.score_value is None or _bpm_untrusted(bundle):
        return None
    return _required_int_score(measurement)


def _time_signature_value(measurement: FieldMeasurement, bundle: RPEBundle) -> str:
    if measurement.score_value is None or _is_untrusted_confidence(
        bundle.physical.time_signature_confidence
    ):
        # Downstream audit treats transcribe TODO targets as sensor-missing needles.
        return TODO_TIME_SIGNATURE_UNDETECTED
    return _required_str_score(measurement)


def _time_signature_for_structure(time_signature: str) -> str | None:
    if time_signature.startswith(TODO_SENTINEL_PREFIX):
        return None
    return time_signature


def _draft_events(bundle: RPEBundle) -> EventLayer | None:
    if not bundle.physical.chord_events:
        return None
    return EventLayer(
        chord_progression=[
            ChordSpec(root=event.root, quality=event.quality)
            for event in bundle.physical.chord_events
        ]
    )


def _draft_fixity(
    physical: PhysicalLayer,
    events: EventLayer | None = None,
) -> dict[str, FixityState]:
    fixity: dict[str, FixityState] = {
        field: "unlocked" if is_todo_sentinel(getattr(physical, field)) else "locked"
        for field in PhysicalLayer.model_fields
    }
    if events is not None and events.chord_progression:
        fixity["chord_progression"] = "locked"
    return fixity


def _is_untrusted_confidence(confidence: float | None) -> bool:
    return confidence is not None and confidence <= 0.0


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
