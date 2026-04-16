"""rpe/semantic_rules.py — rule-based physical-to-semantic mapping.

Maps PhysicalRPE features to SemanticRPE labels using deterministic rules
from config/semantic_rules.yaml. No LLM, no probabilistic inference.
"""
from __future__ import annotations

from typing import List

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    PhysicalRPE,
    SemanticRPE,
)
from svp_rpe.utils.config_loader import load_config


def _check_condition(condition: dict, phys: PhysicalRPE) -> bool:
    """Check if a rule condition matches the physical features."""
    for key, threshold in condition.items():
        if key == "bpm_min" and (phys.bpm is None or phys.bpm < threshold):
            return False
        if key == "bpm_max" and (phys.bpm is None or phys.bpm > threshold):
            return False
        if key == "brightness_min" and phys.spectral_profile.brightness < threshold:
            return False
        if key == "brightness_max" and phys.spectral_profile.brightness > threshold:
            return False
        if key == "active_rate_min" and phys.active_rate < threshold:
            return False
        if key == "active_rate_max" and phys.active_rate > threshold:
            return False
        if key == "mode" and phys.mode != threshold:
            return False
        if key == "spectral_centroid_max" and phys.spectral_centroid > threshold:
            return False
        if key == "spectral_centroid_min" and phys.spectral_centroid < threshold:
            return False
        if key == "valley_depth_min" and phys.valley_depth < threshold:
            return False
        if key == "valley_depth_max" and phys.valley_depth > threshold:
            return False
        if key == "stereo_width_min":
            if phys.stereo_profile is None or phys.stereo_profile.width < threshold:
                return False
        if key == "low_ratio_min" and phys.spectral_profile.low_ratio < threshold:
            return False
    return True


def _infer_grv_anchor(phys: PhysicalRPE, por_surface: List[str]) -> GrvAnchor:
    """Determine gravity anchor from physical features."""
    # Primary based on strongest spectral region
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


def _build_por_core(por_surface: List[str], phys: PhysicalRPE) -> str:
    """Build core semantic description from surface labels and features."""
    if not por_surface:
        return f"Audio with {phys.spectral_profile.brightness:.0%} brightness"

    core_labels = por_surface[:3]
    return f"A {', '.join(core_labels)} sonic character"


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
    """Generate SemanticRPE from PhysicalRPE using rule-based mapping.

    Loads rules from config/semantic_rules.yaml.
    Deterministic: same PhysicalRPE → same SemanticRPE.
    """
    try:
        config = load_config("semantic_rules")
        rules = config.get("rules", [])
    except FileNotFoundError:
        rules = []

    # Apply rules to get surface labels
    por_surface: List[str] = []
    confidence_notes: List[str] = []

    for rule in rules:
        condition = rule.get("condition", {})
        labels = rule.get("por_labels", [])
        if _check_condition(condition, phys):
            por_surface.extend(labels)
            confidence_notes.append(f"rule matched: {condition} → {labels}")

    # Deduplicate while preserving order
    seen = set()
    unique_surface: List[str] = []
    for label in por_surface:
        if label not in seen:
            unique_surface.append(label)
            seen.add(label)
    por_surface = unique_surface

    por_core = _build_por_core(por_surface, phys)
    grv_anchor = _infer_grv_anchor(phys, por_surface)
    delta_e_profile = _infer_delta_e_profile(phys)
    cultural_context = _infer_cultural_context(phys)

    # Instrumentation summary (heuristic)
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
