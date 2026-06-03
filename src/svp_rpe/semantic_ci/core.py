"""Deterministic semantic CI pipeline for Target SVP / RPE fixtures."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from pydantic import BaseModel

from svp_rpe.eval.delta_e_alignment import TRANSITION_TYPES
from svp_rpe.utils.config_loader import load_config
from svp_rpe.semantic_ci.models import (
    ExpectedRPE,
    MetricDiff,
    ObservedRPE,
    RepairAction,
    RepairSVP,
    RoundTripLog,
    RoundTripStep,
    SemanticCIRun,
    SemanticDiff,
    TargetSVP,
    normalize_signals,
)

RANGE_PATTERN = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\s*$"
)


def _canonical_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_data(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(k): _canonical_data(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_canonical_data(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def canonical_json(value: Any) -> str:
    """Return stable JSON used for hashes and deterministic snapshots."""

    return json.dumps(
        _canonical_data(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    """Hash any semantic CI object by its canonical JSON representation."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def generate_expected_rpe(target_svp: TargetSVP) -> ExpectedRPE:
    """Generate Expected RPE deterministically from a Target SVP."""

    delta_e_required, delta_e_allowed = _delta_e_signal_values(target_svp.delta_e_profile)
    required = normalize_signals(
        [
            target_svp.core,
            *delta_e_required,
            *target_svp.surface,
            *target_svp.grv,
            *target_svp.preserve,
        ]
    )
    allowed = normalize_signals([*required, *delta_e_allowed, *target_svp.lock])
    expected = ExpectedRPE(
        source_svp_id=target_svp.id,
        domain=target_svp.domain,
        required_signals=required,
        allowed_signals=allowed,
        prohibited_signals=target_svp.avoid,
        locked_signals=target_svp.lock,
        metric_targets=dict(sorted(target_svp.metric_targets.items())),
        tolerances=dict(sorted(target_svp.tolerances.items())),
        change_budget=target_svp.change_budget,
        source_hash=stable_hash(target_svp),
    )
    return expected


def _delta_e_signal_values(value: str) -> tuple[list[str], list[str]]:
    if not value:
        return [], []

    canonical = _canonical_delta_e_transition(value)
    if canonical is None:
        return [value], [value]

    display = canonical.replace("_", " ")
    return [display], [display, canonical]


def _canonical_delta_e_transition(value: str) -> str | None:
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    for transition_type in sorted(TRANSITION_TYPES):
        if transition_type in normalized:
            return transition_type
    return None


def _compare_metric(name: str, expected: Any, observed: Any, tolerance: float | None) -> MetricDiff:
    if _is_number(expected) and _is_number(observed):
        diff = abs(float(observed) - float(expected))
        passed = diff <= (tolerance if tolerance is not None else 0.0)
        return MetricDiff(
            name=name,
            expected=expected,
            observed=observed,
            tolerance=tolerance,
            diff=round(diff, 6),
            passed=passed,
        )

    observed_number = _numeric_value(observed)
    if isinstance(expected, str) and observed_number is not None:
        bounds = _parse_numeric_range(expected) or _target_band_bounds(name, expected)
        if bounds is not None:
            diff = _range_distance(observed_number, *bounds)
            return MetricDiff(
                name=name,
                expected=expected,
                observed=observed,
                tolerance=tolerance,
                diff=round(diff, 6),
                passed=diff <= (tolerance if tolerance is not None else 0.0),
            )

    return MetricDiff(
        name=name,
        expected=expected,
        observed=observed,
        tolerance=tolerance,
        diff=None,
        passed=observed == expected,
    )


def _observed_metric_value(metrics: Mapping[str, Any], name: str) -> Any:
    if name in metrics:
        return metrics.get(name)
    sensor_name = _sensor_metric_name(name)
    return metrics.get(sensor_name)


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_numeric_range(value: str) -> tuple[float, float] | None:
    match = RANGE_PATTERN.match(value)
    if match is None:
        return None
    lower = float(match.group(1))
    upper = float(match.group(2))
    if lower > upper:
        lower, upper = upper, lower
    return lower, upper


def _target_band_bounds(name: str, target: str) -> tuple[float | None, float | None] | None:
    feature_name = _sensor_metric_name(name)
    aliases = _label_aliases(_normalize_label(target))
    for rule in _semantic_rules_for_feature(feature_name):
        labels = {_normalize_label(label) for label in rule["labels"]}
        if aliases.intersection(labels):
            return rule["bounds"].get("min"), rule["bounds"].get("max")

    if feature_name == "brightness" and "dark" in aliases:
        for rule in _semantic_rules_for_feature(feature_name):
            labels = {_normalize_label(label) for label in rule["labels"]}
            lower = rule["bounds"].get("min")
            if "bright" in labels and lower is not None:
                return None, lower
    return None


def _range_distance(value: float, lower: float | None, upper: float | None) -> float:
    if lower is not None and value < lower:
        return lower - value
    if upper is not None and value > upper:
        return value - upper
    return 0.0


def _sensor_metric_name(name: str) -> str:
    return name[: -len("_target")] if name.endswith("_target") else name


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
                "bounds": bounds,
            })
    return rules


def _feature_bounds(feature_name: str, condition: Mapping[str, Any]) -> dict[str, float] | None:
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


def _condition_key(raw_key: str) -> tuple[str, str]:
    if raw_key.endswith("_min"):
        return raw_key[: -len("_min")], ">="
    if raw_key.endswith("_max"):
        return raw_key[: -len("_max")], "<="
    return raw_key, "=="


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


def _normalize_label(value: str) -> str:
    return value.strip().lower()


def _metric_loss(metric_diffs: list[MetricDiff]) -> float:
    if not metric_diffs:
        return 0.0

    losses: list[float] = []
    for metric in metric_diffs:
        if metric.passed:
            losses.append(0.0)
        elif metric.diff is not None:
            tolerance = metric.tolerance if metric.tolerance is not None else 0.0
            baseline = _numeric_value(metric.expected)
            if baseline is None:
                baseline = _numeric_value(metric.observed)
            denominator = max(abs(float(baseline or 0.0)), tolerance, 1.0)
            losses.append(min(1.0, metric.diff / denominator))
        else:
            losses.append(1.0)
    return sum(losses) / len(losses)


def compare_expected_observed(
    expected: ExpectedRPE,
    observed: ObservedRPE,
    threshold: float = 0.0,
) -> SemanticDiff:
    """Compare Expected RPE against an Observed RPE fixture."""

    expected_required = set(expected.required_signals)
    expected_allowed = set(expected.allowed_signals)
    expected_prohibited = set(expected.prohibited_signals)
    observed_signals = set(observed.signals)

    missing = sorted(expected_required - observed_signals)
    preserved = sorted(expected_required & observed_signals)
    unexpected = observed_signals - expected_allowed
    prohibited_present = observed_signals & expected_prohibited
    over_changed = sorted(unexpected | prohibited_present)

    metric_diffs = [
        _compare_metric(
            name,
            target,
            _observed_metric_value(observed.metrics, name),
            expected.tolerances.get(name),
        )
        for name, target in sorted(expected.metric_targets.items())
    ]

    missing_loss = len(missing) / max(len(expected.required_signals), 1)
    over_loss = len(over_changed) / max(len(observed.signals), 1)
    metrics_loss = _metric_loss(metric_diffs)
    loss = round(min(1.0, 0.6 * missing_loss + 0.2 * over_loss + 0.2 * metrics_loss), 4)

    return SemanticDiff(
        missing=missing,
        preserved=preserved,
        over_changed=over_changed,
        metric_diffs=metric_diffs,
        loss=loss,
        threshold=threshold,
        verdict="pass" if loss <= threshold else "repair",
    )


def generate_repair_svp(target_svp: TargetSVP, diff: SemanticDiff) -> RepairSVP:
    """Convert SemanticDiff into a budgeted preserve/restore/reduce/lock repair plan."""

    preserve = normalize_signals([*target_svp.preserve, *diff.preserved])
    lock = normalize_signals([*target_svp.lock, *diff.preserved])

    budget = target_svp.change_budget
    restore = diff.missing[:budget]
    budget -= len(restore)
    reduce = diff.over_changed[:budget]

    deferred_restore = diff.missing[len(restore):]
    deferred_reduce = diff.over_changed[len(reduce):]

    repair_order = [
        *(RepairAction(op="preserve", signal=signal) for signal in preserve),
        *(RepairAction(op="restore", signal=signal) for signal in restore),
        *(RepairAction(op="reduce", signal=signal) for signal in reduce),
        *(RepairAction(op="lock", signal=signal) for signal in lock),
        *(RepairAction(op="restore", signal=signal, applied=False) for signal in deferred_restore),
        *(RepairAction(op="reduce", signal=signal, applied=False) for signal in deferred_reduce),
    ]

    return RepairSVP(
        source_svp_id=target_svp.id,
        change_budget=target_svp.change_budget,
        preserve=preserve,
        restore=restore,
        reduce=reduce,
        lock=lock,
        deferred_restore=deferred_restore,
        deferred_reduce=deferred_reduce,
        repair_order=repair_order,
    )


def apply_repair_svp(target_svp: TargetSVP, repair_svp: RepairSVP) -> TargetSVP:
    """Apply the structured repair plan back to TargetSVP fields."""

    return target_svp.model_copy(
        update={
            "preserve": normalize_signals([*target_svp.preserve, *repair_svp.preserve]),
            "surface": normalize_signals([*target_svp.surface, *repair_svp.restore]),
            "avoid": normalize_signals([*target_svp.avoid, *repair_svp.reduce]),
            "lock": normalize_signals([*target_svp.lock, *repair_svp.lock]),
        }
    )


def _build_roundtrip_log(
    target_svp: TargetSVP,
    expected_rpe: ExpectedRPE,
    observed_rpe: ObservedRPE,
    semantic_diff: SemanticDiff,
    repair_svp: RepairSVP,
    threshold: float = 0.0,
) -> RoundTripLog:
    target_hash = stable_hash(target_svp)
    expected_hash = stable_hash(expected_rpe)
    observed_hash = stable_hash(observed_rpe)
    threshold_hash = stable_hash({"threshold": threshold})
    diff_hash = stable_hash(semantic_diff)
    repair_hash = stable_hash(repair_svp)
    transitions = [
        RoundTripStep(name="target_svp", output_hash=target_hash),
        RoundTripStep(name="expected_rpe", input_hash=target_hash, output_hash=expected_hash),
        RoundTripStep(name="observed_rpe", output_hash=observed_hash),
        RoundTripStep(
            name="semantic_diff",
            input_hash=f"{expected_hash}:{observed_hash}:{threshold_hash}",
            output_hash=diff_hash,
        ),
        RoundTripStep(name="repair_svp", input_hash=diff_hash, output_hash=repair_hash),
    ]
    return RoundTripLog(
        target_svp_hash=target_hash,
        expected_rpe_hash=expected_hash,
        observed_rpe_hash=observed_hash,
        semantic_diff_hash=diff_hash,
        repair_svp_hash=repair_hash,
        transitions=transitions,
        final_hash=stable_hash([target_hash, expected_hash, observed_hash, diff_hash, repair_hash]),
    )


def run_semantic_ci(
    target_svp: TargetSVP,
    observed_rpe: ObservedRPE,
    threshold: float = 0.0,
) -> SemanticCIRun:
    """Run the Phase 1 deterministic semantic CI loop."""

    expected_rpe = generate_expected_rpe(target_svp)
    semantic_diff = compare_expected_observed(expected_rpe, observed_rpe, threshold=threshold)
    repair_svp = generate_repair_svp(target_svp, semantic_diff)
    repaired_svp = apply_repair_svp(target_svp, repair_svp)
    roundtrip_log = _build_roundtrip_log(
        target_svp,
        expected_rpe,
        observed_rpe,
        semantic_diff,
        repair_svp,
        threshold,
    )
    return SemanticCIRun(
        target_svp=target_svp,
        expected_rpe=expected_rpe,
        observed_rpe=observed_rpe,
        semantic_diff=semantic_diff,
        repair_svp=repair_svp,
        repaired_svp=repaired_svp,
        roundtrip_log=roundtrip_log,
    )
