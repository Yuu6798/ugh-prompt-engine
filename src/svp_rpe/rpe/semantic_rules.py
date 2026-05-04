"""Rule-based physical-to-semantic mapping with evidence-bearing labels."""
from __future__ import annotations

from typing import Any, Iterable, List, Literal, Mapping, Optional

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    PhysicalRPE,
    SemanticLabel,
    SemanticRPE,
)
from svp_rpe.utils.config_loader import load_config

SemanticLayer = Literal["perceptual", "structural", "semantic_hypothesis"]
SEMANTIC_LAYERS: tuple[SemanticLayer, ...] = (
    "perceptual",
    "structural",
    "semantic_hypothesis",
)
HIGH_CONFIDENCE = 0.70


def _feature_value(name: str, phys: PhysicalRPE) -> Any:
    """Return a comparable physical value by rule key."""
    getters = {
        "bpm": lambda item: item.bpm,
        "brightness": lambda item: item.spectral_profile.brightness,
        "active_rate": lambda item: item.active_rate,
        "mode": lambda item: item.mode,
        "spectral_centroid": lambda item: item.spectral_centroid,
        "valley_depth": lambda item: item.valley_depth,
        "stereo_width": lambda item: item.stereo_profile.width
        if item.stereo_profile
        else None,
        "low_ratio": lambda item: item.spectral_profile.low_ratio,
        "mid_ratio": lambda item: item.spectral_profile.mid_ratio,
        "high_ratio": lambda item: item.spectral_profile.high_ratio,
        "crest_factor": lambda item: item.crest_factor,
        "thickness": lambda item: item.thickness,
        "onset_density": lambda item: item.onset_density,
    }
    getter = getters.get(name)
    if getter is None:
        return getattr(phys, name, None)
    return getter(phys)


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _condition_key(raw_key: str) -> tuple[str, str]:
    if raw_key.endswith("_min"):
        return raw_key[: -len("_min")], ">="
    if raw_key.endswith("_max"):
        return raw_key[: -len("_max")], "<="
    return raw_key, "=="


def _condition_evidence(condition: Mapping[str, Any], phys: PhysicalRPE) -> Optional[list[str]]:
    """Return evidence strings when all conditions match, otherwise None."""
    def matches(operator: str, actual: Any, expected: Any) -> bool:
        if operator == ">=":
            return isinstance(actual, (int, float)) and float(actual) >= float(expected)
        if operator == "<=":
            return isinstance(actual, (int, float)) and float(actual) <= float(expected)
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.lower() == expected.lower()
        return actual == expected

    def evidence_for(raw_key: str, expected: Any) -> Optional[str]:
        feature_name, operator = _condition_key(raw_key)
        actual = _feature_value(feature_name, phys)
        if actual is None or not matches(operator, actual, expected):
            return None
        return f"{feature_name}={_fmt(actual)} {operator} {_fmt(expected)}"

    evidence: list[str] = []
    for raw_key, expected in condition.items():
        result = evidence_for(raw_key, expected)
        if result is None:
            return None
        evidence.append(result)
    return evidence


def _emit_labels(
    *,
    layer: SemanticLayer,
    rule: Mapping[str, Any],
    evidence: list[str],
) -> list[SemanticLabel]:
    labels: list[SemanticLabel] = []
    source_rule = str(rule.get("id") or f"{layer}.unnamed")
    for spec in rule.get("labels", []):
        if isinstance(spec, str):
            label = spec
            confidence = 0.5
        else:
            label = str(spec.get("label", ""))
            confidence = float(spec.get("confidence", 0.5))
        if not label:
            continue
        labels.append(
            SemanticLabel(
                label=label,
                layer=layer,
                confidence=round(confidence, 4),
                evidence=list(evidence),
                source_rule=source_rule,
            )
        )
    return labels


def _dedupe_labels(labels: Iterable[SemanticLabel]) -> list[SemanticLabel]:
    best: dict[tuple[str, str], SemanticLabel] = {}
    order: list[tuple[str, str]] = []
    for item in labels:
        key = (item.layer, item.label)
        if key not in best:
            best[key] = item
            order.append(key)
            continue
        if item.confidence > best[key].confidence:
            best[key] = item
    return [best[key] for key in order]


def _validate_rule(layer: SemanticLayer, rule: Mapping[str, Any]) -> None:
    if layer == "semantic_hypothesis" and len(rule.get("condition", {})) < 2:
        rule_id = rule.get("id", "<unnamed>")
        raise ValueError(f"semantic_hypothesis rule requires >=2 conditions: {rule_id}")


def _labels_from_rules(phys: PhysicalRPE, config: Mapping[str, Any]) -> tuple[list[SemanticLabel], list[str]]:
    labels: list[SemanticLabel] = []
    confidence_notes: list[str] = []
    for layer in SEMANTIC_LAYERS:
        for rule in config.get(layer, []):
            _validate_rule(layer, rule)
            condition = rule.get("condition", {})
            evidence = _condition_evidence(condition, phys)
            if evidence is None:
                continue
            emitted = _emit_labels(layer=layer, rule=rule, evidence=evidence)
            labels.extend(emitted)
            confidence_notes.append(
                f"rule matched: {rule.get('id', '<unnamed>')} -> "
                f"{[label.label for label in emitted]}"
            )
    return _dedupe_labels(labels), confidence_notes


def _infer_grv_anchor(phys: PhysicalRPE, por_surface: List[SemanticLabel]) -> GrvAnchor:
    """Determine gravity anchor from physical features."""
    sp = phys.spectral_profile
    if sp.low_ratio >= sp.mid_ratio and sp.low_ratio >= sp.high_ratio:
        primary = "bass-heavy"
    elif sp.high_ratio >= sp.mid_ratio:
        primary = "bright"
    else:
        primary = "mid-focused"

    secondary = []
    if phys.active_rate > 0.8:
        secondary.append("dense")
    if phys.valley_depth > 0.2:
        secondary.append("dynamic")
    if phys.stereo_profile and phys.stereo_profile.width > 0.5:
        secondary.append("wide-field")

    confidence = min(0.8, 0.3 + len(por_surface) * 0.1)
    return GrvAnchor(primary=primary, secondary=secondary[:3], confidence=round(confidence, 2))


def _infer_delta_e_profile(phys: PhysicalRPE) -> DeltaEProfile:
    """Infer energy transition profile from physical features."""
    vd = phys.valley_depth
    ar = phys.active_rate

    if vd > 0.3:
        transition_type = "dramatic_contrast"
        intensity = min(1.0, vd * 2)
        description = "High dynamic range with significant energy transitions"
    elif vd > 0.15:
        transition_type = "gradual_build"
        intensity = vd * 2
        description = "Moderate energy variation with gradual transitions"
    elif ar > 0.8:
        transition_type = "sustained_energy"
        intensity = ar * 0.5
        description = "Consistently high energy with minimal transitions"
    else:
        transition_type = "flat"
        intensity = 0.2
        description = "Low energy variation, relatively static"

    return DeltaEProfile(
        transition_type=transition_type,
        intensity=round(intensity, 4),
        description=description,
    )


def _build_por_core(por_surface: List[SemanticLabel], phys: PhysicalRPE) -> str:
    """Build core description from high-confidence perceptual/structural labels."""
    high_conf_labels = sorted(
        [
            label
            for label in por_surface
            if label.layer in ("perceptual", "structural")
            and label.confidence >= HIGH_CONFIDENCE
        ],
        key=lambda label: (-label.confidence, label.label),
    )[:3]
    if not high_conf_labels:
        return f"Audio with {phys.spectral_profile.brightness:.0%} brightness"
    return f"A {', '.join(label.label for label in high_conf_labels)} sonic character"


def _infer_cultural_context(phys: PhysicalRPE) -> List[str]:
    """Infer cultural/genre context from features."""
    contexts = []
    if phys.bpm and phys.bpm > 140 and phys.active_rate > 0.8:
        contexts.append("electronic/dance")
    if phys.bpm and phys.bpm < 90:
        contexts.append("ambient/downtempo")
    if phys.spectral_profile.low_ratio > 0.4:
        contexts.append("bass-music")
    if phys.valley_depth > 0.3:
        contexts.append("cinematic/orchestral")
    if not contexts:
        contexts.append("general")
    return contexts


def generate_semantic(phys: PhysicalRPE) -> SemanticRPE:
    """Generate SemanticRPE from PhysicalRPE using deterministic rules."""
    try:
        config = load_config("semantic_rules")
    except FileNotFoundError:
        config = {}

    por_surface, confidence_notes = _labels_from_rules(phys, config)
    por_core = _build_por_core(por_surface, phys)
    grv_anchor = _infer_grv_anchor(phys, por_surface)
    delta_e_profile = _infer_delta_e_profile(phys)
    cultural_context = _infer_cultural_context(phys)

    instrumentation = "Unknown instrumentation"
    if phys.spectral_profile.low_ratio > 0.35:
        instrumentation = "Bass-heavy production with prominent low-end"
    elif phys.spectral_profile.high_ratio > 0.3:
        instrumentation = "Bright production with emphasis on highs"
    elif phys.spectral_profile.mid_ratio > 0.5:
        instrumentation = "Mid-focused production"

    production_notes = []
    if phys.crest_factor < 3:
        production_notes.append("Heavily compressed (low crest factor)")
    elif phys.crest_factor > 10:
        production_notes.append("Very dynamic (high crest factor)")
    if phys.active_rate > 0.9:
        production_notes.append("Continuous energy, minimal silence")
    if not production_notes:
        production_notes.append("Standard dynamic range")

    return SemanticRPE(
        por_core=por_core,
        por_surface=por_surface,
        grv_anchor=grv_anchor,
        delta_e_profile=delta_e_profile,
        cultural_context=cultural_context,
        instrumentation_summary=instrumentation,
        production_notes=production_notes,
        confidence_notes=confidence_notes,
    )
