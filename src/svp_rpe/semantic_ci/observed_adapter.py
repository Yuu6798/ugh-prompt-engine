"""Adapters from extracted RPE bundles to semantic CI observations."""
from __future__ import annotations

from typing import Any

from svp_rpe.rpe.models import RPEBundle
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.semantic_ci.models import ObservedRPE


def rpe_bundle_to_observed(bundle: RPEBundle, *, id: str) -> ObservedRPE:
    """Convert an extracted RPE bundle into an ObservedRPE sensor snapshot."""
    physical = bundle.physical
    bucketed_semantic = generate_semantic(physical)
    semantic = bundle.semantic

    metrics: dict[str, Any] = {
        "bpm": float(physical.bpm) if physical.bpm is not None else None,
        "key": physical.key,
        "mode": physical.mode,
        "time_signature": physical.time_signature,
        "active_rate": float(physical.active_rate),
        "valley_depth": float(physical.valley_depth),
        "brightness": float(physical.spectral_profile.brightness),
        "stereo_width": (
            float(physical.stereo_profile.width) if physical.stereo_profile is not None else None
        ),
    }
    signals = [
        semantic.por_core,
        *[label.label for label in semantic.por_surface],
        semantic.grv_anchor.primary,
        *semantic.grv_anchor.secondary,
        semantic.delta_e_profile.transition_type,
        semantic.delta_e_profile.transition_type.replace("_", " "),
        *[label.label for label in bucketed_semantic.por_surface],
    ]

    return ObservedRPE(
        id=id,
        domain="music",
        signals=signals,
        metrics=metrics,
        source="rpe_extract",
    )
