"""Composition audit control-panel reports.

The audit layer reports needle positions for controllability work. It does not
produce verdicts, thresholds, or aggregate scoring.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from svp_rpe.eval.anchor_matcher import grv_anchor_match
from svp_rpe.eval.delta_e_alignment import delta_e_profile_alignment
from svp_rpe.eval.semantic_similarity import por_lexical_similarity
from svp_rpe.keys import weighted_key_score
from svp_rpe.rpe.models import RPEBundle
from svp_rpe.semantic_ci.models import ObservedRPE
from svp_rpe.semantic_ci.observed_adapter import rpe_bundle_to_observed
from svp_rpe.sentinels import is_todo_sentinel
from svp_rpe.utils.config_loader import load_config

if TYPE_CHECKING:
    from svp_rpe.compose.models import CompositionScore

RANGE_PATTERN = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\s*$"
)
DELTA_E_TRANSITION_TYPES = (
    "dramatic_contrast",
    "sustained_energy",
    "gradual_build",
    "sudden_drop",
    "decrescendo",
    "crescendo",
    "flat",
)


class AuditNeedle(BaseModel):
    """One control-panel needle for a physical or semantic knob."""

    model_config = ConfigDict(extra="forbid")

    name: str
    layer: Literal["physical", "semantic"]
    target: Any
    observed: Any = None
    observed_band: Optional[str] = None
    target_band: Optional[str] = None
    deviation: Optional[float] = None
    score: Optional[float] = None
    unit: Optional[str] = None
    sensor: Optional[str] = None
    note: Optional[str] = None


class AuditReport(BaseModel):
    """Structured audit report for downstream control-panel tools."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    score_id: str
    observed_id: str
    source: str
    observed_signals: list[str] = Field(default_factory=list)
    physical: list[AuditNeedle] = Field(default_factory=list)
    semantic: list[AuditNeedle] = Field(default_factory=list)


def build_audit_report(
    score: "CompositionScore",
    bundle: RPEBundle,
    *,
    observed_id: Optional[str] = None,
) -> AuditReport:
    """Build a no-verdict composition audit report."""
    from svp_rpe.compose.convert import composition_to_target_svp

    target = composition_to_target_svp(score)
    observed = rpe_bundle_to_observed(bundle, id=observed_id or target.id)
    semantic = bundle.semantic

    physical = [
        _bpm_needle(target.metric_targets.get("bpm"), observed),
        _key_needle(target.metric_targets.get("key"), observed),
        _time_signature_needle(target.metric_targets.get("time_signature"), observed),
        _metric_band_needle(
            name="active_rate",
            target=target.metric_targets.get("active_rate_target"),
            observed=observed,
        ),
        _metric_band_needle(
            name="valley_depth",
            target=target.metric_targets.get("valley_depth_target"),
            observed=observed,
        ),
        _metric_band_needle(
            name="brightness",
            target=target.metric_targets.get("brightness"),
            observed=observed,
            metric=_brightness_sensor_metric(target.metric_targets.get("brightness")),
        ),
        _metric_band_needle(
            name="stereo_width",
            target=target.metric_targets.get("stereo_width"),
            observed=observed,
        ),
    ]

    observed_grv = semantic.grv_anchor
    core_score = _round_score(por_lexical_similarity(score.semantic.core, semantic.por_core))
    grv_score = _round_score(
        grv_anchor_match(
            primary_a=score.semantic.grv.primary,
            primary_b=observed_grv.primary,
            bpm_a=_numeric_metric(score.physical.bpm),
            bpm_b=_numeric_metric(observed.metrics.get("bpm")),
            key_a=_text_metric_without_transcribe_todo(score.physical.key),
            key_b=_text_metric(observed.metrics.get("key")),
            anchors_a=[item for item in [score.semantic.grv.secondary] if item],
            anchors_b=observed_grv.secondary,
        )
    )
    semantic_needles = [
        AuditNeedle(
            name="core",
            layer="semantic",
            target=score.semantic.core,
            observed=semantic.por_core,
            score=core_score,
            deviation=_round_score(1.0 - core_score),
            sensor="por_lexical_similarity",
        ),
        AuditNeedle(
            name="grv",
            layer="semantic",
            target=[score.semantic.grv.primary, score.semantic.grv.secondary],
            observed=[observed_grv.primary, *observed_grv.secondary],
            score=grv_score,
            deviation=_round_score(1.0 - grv_score),
            sensor="grv_anchor_match",
        ),
        _delta_e_needle(score, bundle),
    ]

    return AuditReport(
        score_id=target.id,
        observed_id=observed.id,
        source=observed.source,
        observed_signals=observed.signals,
        physical=physical,
        semantic=semantic_needles,
    )


def render_audit_text(report: AuditReport) -> str:
    """Render a compact control-panel table without outcome language."""
    lines = [
        "# Composition Audit Control Panel",
        "",
        f"- score_id: {report.score_id}",
        f"- observed_id: {report.observed_id}",
        f"- source: {report.source}",
        f"- observed_signals: {', '.join(report.observed_signals) or '(none)'}",
        "",
        "## Physical Needles",
        "",
        _render_table(report.physical),
        "",
        "## Semantic Needles",
        "",
        _render_table(report.semantic),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _render_table(needles: Iterable[AuditNeedle]) -> str:
    rows = [
        "| knob | target | observed | band | deviation | score | note |",
        "|---|---:|---:|---|---:|---:|---|",
    ]
    for needle in needles:
        rows.append(
            "| "
            f"{needle.name} | "
            f"{_display(needle.target)} | "
            f"{_display(needle.observed)} | "
            f"{_display(needle.observed_band)} | "
            f"{_display(needle.deviation)} | "
            f"{_display(needle.score)} | "
            f"{_display(needle.note)} |"
        )
    return "\n".join(rows)


def _bpm_needle(target: Any, observed: ObservedRPE) -> AuditNeedle:
    target_value = _numeric_metric(target)
    observed_value = _numeric_metric(observed.metrics.get("bpm"))
    if target_value is None or observed_value is None:
        return AuditNeedle(
            name="bpm",
            layer="physical",
            target=target,
            observed=observed.metrics.get("bpm"),
            unit="bpm",
            sensor="PhysicalRPE.bpm",
            note="sensor missing",
        )

    diff = observed_value - target_value
    return AuditNeedle(
        name="bpm",
        layer="physical",
        target=int(target_value) if target_value.is_integer() else target_value,
        observed=_round_float(observed_value),
        deviation=_round_float(diff),
        unit="bpm",
        sensor="PhysicalRPE.bpm",
    )


def _key_needle(target: Any, observed: ObservedRPE) -> AuditNeedle:
    observed_key = _text_metric(observed.metrics.get("key"))
    target_key = str(target) if target is not None else None
    if is_todo_sentinel(target_key):
        return AuditNeedle(
            name="key",
            layer="physical",
            target=target_key,
            observed=observed_key,
            sensor="mir_eval.key.evaluate",
            note="sensor missing",
        )
    if not target_key or not observed_key:
        return AuditNeedle(
            name="key",
            layer="physical",
            target=target_key,
            observed=observed_key,
            sensor="mir_eval.key.evaluate",
            note="sensor missing",
        )
    result = weighted_key_score(target_key, observed_key)
    score = _round_score(result.score)
    note = "mir_eval unavailable; exact match fallback" if result.sensor == "exact_key_match" else None
    return AuditNeedle(
        name="key",
        layer="physical",
        target=target_key,
        observed=observed_key,
        deviation=_round_score(1.0 - score),
        score=score,
        sensor=result.sensor,
        note=note,
    )


def _time_signature_needle(target: Any, observed: ObservedRPE) -> AuditNeedle:
    observed_value = observed.metrics.get("time_signature")
    target_value = str(target) if target is not None else None
    observed_text = str(observed_value) if observed_value is not None else None
    if is_todo_sentinel(target_value):
        return AuditNeedle(
            name="time_signature",
            layer="physical",
            target=target_value,
            observed=observed_text,
            sensor="PhysicalRPE.time_signature",
            note="sensor missing",
        )
    if not target_value or not observed_text:
        return AuditNeedle(
            name="time_signature",
            layer="physical",
            target=target_value,
            observed=observed_text,
            sensor="PhysicalRPE.time_signature",
            note="sensor missing",
        )

    score = 1.0 if target_value == observed_text else 0.0
    return AuditNeedle(
        name="time_signature",
        layer="physical",
        target=target_value,
        observed=observed_text,
        deviation=_round_score(1.0 - score),
        score=score,
        sensor="PhysicalRPE.time_signature",
    )


def _brightness_sensor_metric(target: Any) -> str:
    """brightness ツマミのセンサー選択（semantic_ci/core.py の比較側と同方針）。

    legacy 数値ターゲット（数値 or 数値レンジ文字列、旧 band-ratio 単位）は
    exact key の band-ratio と比較し、定性ターゲット（dark/bright 等）は
    正規センサー spectral_centroid を使う（band-ratio は HF の乏しい素材で盲目。
    controllability_poc.md §5.1 / semantic_rules.yaml perc.dark/bright）。
    """
    if isinstance(target, bool):
        return "spectral_centroid"
    if isinstance(target, (int, float)):
        return "brightness"
    if isinstance(target, str) and _parse_numeric_range(target) is not None:
        return "brightness"
    return "spectral_centroid"


def _metric_band_needle(
    name: str,
    target: Any,
    observed: ObservedRPE,
    metric: Optional[str] = None,
) -> AuditNeedle:
    """ツマミ名と観測メトリクス名が異なる場合は metric でセンサーを指定する。"""
    metric_name = metric or name
    observed_value = _numeric_metric(observed.metrics.get(metric_name))
    observed_band = _observed_band(metric_name, observed.metrics)
    if observed_value is None:
        return AuditNeedle(
            name=name,
            layer="physical",
            target=target,
            observed=None,
            observed_band=observed_band,
            sensor=f"PhysicalRPE.{metric_name}",
            note="sensor missing",
        )

    band = _target_band(metric_name, target)
    return AuditNeedle(
        name=name,
        layer="physical",
        target=target,
        observed=_round_float(observed_value),
        observed_band=observed_band,
        target_band=band.description if band else None,
        deviation=band.deviation(observed_value) if band else None,
        sensor=f"PhysicalRPE.{metric_name}",
        note=None if band else "target band undefined; raw observation only",
    )


def _delta_e_needle(score: "CompositionScore", bundle: RPEBundle) -> AuditNeedle:
    observed = bundle.semantic.delta_e_profile
    target = score.semantic.delta_e.overall
    canonical_target = _canonical_delta_e_target(target)
    value = delta_e_profile_alignment(
        canonical_target,
        observed.transition_type,
        0.5,
        observed.intensity,
    )
    rounded = _round_score(value)
    return AuditNeedle(
        name="delta_e",
        layer="semantic",
        target=target,
        observed=observed.transition_type,
        score=rounded,
        deviation=_round_score(1.0 - rounded),
        sensor="delta_e_profile_alignment",
        note=(
            f"canonical target={canonical_target}"
            if canonical_target != _normalize_transition(target)
            else None
        ),
    )


class _Band:
    def __init__(self, description: str, lower: Optional[float], upper: Optional[float]) -> None:
        self.description = description
        self.lower = lower
        self.upper = upper

    def deviation(self, value: float) -> float:
        if self.lower is not None and value < self.lower:
            return _round_float(value - self.lower)
        if self.upper is not None and value > self.upper:
            return _round_float(value - self.upper)
        return 0.0


def _target_band(feature_name: str, target: Any) -> Optional[_Band]:
    numeric_range = _parse_numeric_range(target)
    if numeric_range is not None:
        lower, upper = numeric_range
        return _Band(f"{lower:g}-{upper:g}", lower, upper)

    if target is None:
        return None
    target_label = _normalize_label(str(target))
    target_aliases = _label_aliases(target_label)
    for rule in _semantic_rules_for_feature(feature_name):
        labels = {_normalize_label(label) for label in rule["labels"]}
        if not target_aliases.intersection(labels):
            continue
        lower = rule["bounds"].get("min")
        upper = rule["bounds"].get("max")
        if lower is not None and upper is not None:
            description = f"{feature_name} {lower:g}-{upper:g}"
        elif lower is not None:
            description = f"{feature_name} >= {lower:g}"
        elif upper is not None:
            description = f"{feature_name} <= {upper:g}"
        else:
            description = str(target)
        return _Band(description, lower, upper)
    return None


def _observed_band(feature_name: str, metrics: Mapping[str, Any]) -> Optional[str]:
    labels: list[str] = []
    for rule in _semantic_rules_for_feature(feature_name):
        if len(_condition_feature_names(rule["condition"])) != 1:
            continue
        if not _condition_matches_observed(rule["condition"], metrics):
            continue
        for label in rule["labels"]:
            labels.append(label)
    return ", ".join(dict.fromkeys(labels)) or None


def _semantic_rules_for_feature(feature_name: str) -> list[dict[str, Any]]:
    try:
        config = load_config("semantic_rules")
    except FileNotFoundError:
        return []

    rules: list[dict[str, Any]] = []
    for layer in ("perceptual", "structural", "semantic_hypothesis"):
        for raw_rule in config.get(layer, []):
            condition = raw_rule.get("condition", {})
            bounds = _feature_bounds(feature_name, condition)
            if bounds is None:
                continue
            rules.append({
                "labels": _labels_from_rule(raw_rule),
                "condition": condition,
                "bounds": bounds,
            })
    return rules


def _feature_bounds(feature_name: str, condition: Mapping[str, Any]) -> Optional[dict[str, float]]:
    bounds: dict[str, float] = {}
    saw_feature = False
    for raw_key, raw_value in condition.items():
        condition_feature, operator = _condition_key(raw_key)
        if condition_feature != feature_name:
            continue
        saw_feature = True
        if operator == ">=":
            bounds["min"] = float(raw_value)
        elif operator == "<=":
            bounds["max"] = float(raw_value)
    return bounds if saw_feature else None


def _labels_from_rule(rule: Mapping[str, Any]) -> list[str]:
    labels: list[str] = []
    for item in rule.get("labels", []):
        if isinstance(item, str):
            labels.append(item)
        else:
            label = item.get("label")
            if label:
                labels.append(str(label))
    return labels


def _condition_matches_observed(condition: Mapping[str, Any], metrics: Mapping[str, Any]) -> bool:
    for raw_key, expected in condition.items():
        feature_name, operator = _condition_key(raw_key)
        actual = metrics.get(feature_name)
        if actual is None:
            return False
        if operator == ">=":
            if _numeric_metric(actual) is None or float(actual) < float(expected):
                return False
        elif operator == "<=":
            if _numeric_metric(actual) is None or float(actual) > float(expected):
                return False
        elif isinstance(actual, str) and isinstance(expected, str):
            if actual.lower() != expected.lower():
                return False
        elif actual != expected:
            return False
    return True


def _condition_feature_names(condition: Mapping[str, Any]) -> set[str]:
    return {_condition_key(raw_key)[0] for raw_key in condition}


def _label_aliases(label: str) -> set[str]:
    aliases = {label}
    try:
        config = load_config("synonym_map")
    except FileNotFoundError:
        return aliases

    for group in config.get("groups", []):
        normalized_group = {_normalize_label(str(item)) for item in group}
        if label in normalized_group:
            aliases.update(normalized_group)
    return aliases


def _condition_key(raw_key: str) -> tuple[str, str]:
    if raw_key.endswith("_min"):
        return raw_key[: -len("_min")], ">="
    if raw_key.endswith("_max"):
        return raw_key[: -len("_max")], "<="
    return raw_key, "=="


def _parse_numeric_range(target: Any) -> Optional[tuple[float, float]]:
    if not isinstance(target, str):
        return None
    match = RANGE_PATTERN.match(target)
    if match is None:
        return None
    lower = float(match.group(1))
    upper = float(match.group(2))
    if lower > upper:
        lower, upper = upper, lower
    return lower, upper


def _text_metric(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _text_metric_without_transcribe_todo(value: Any) -> Optional[str]:
    if is_todo_sentinel(value):
        return None
    return _text_metric(value)


def _numeric_metric(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_label(value: str) -> str:
    return value.strip().lower()


def _normalize_transition(value: str) -> str:
    return value.lower().replace("-", "_").replace(" ", "_")


def _canonical_delta_e_target(value: str) -> str:
    normalized = _normalize_transition(value)
    for transition_type in DELTA_E_TRANSITION_TYPES:
        if transition_type in normalized:
            return transition_type
    return normalized


def _round_float(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), 4)


def _round_score(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4g}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item)
    return str(value)
