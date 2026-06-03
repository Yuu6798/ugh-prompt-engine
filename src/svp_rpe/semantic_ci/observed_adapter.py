"""Adapters from extracted RPE bundles to semantic CI observations."""
from __future__ import annotations

from typing import Any

from svp_rpe.rpe.models import RPEBundle
from svp_rpe.semantic_ci.models import ObservedRPE


def rpe_bundle_to_observed(bundle: RPEBundle, *, id: str) -> ObservedRPE:
    """Convert an extracted RPE bundle into an ObservedRPE sensor snapshot."""
    physical = bundle.physical
    semantic = bundle.semantic
    combined_key = (
        f"{physical.key} {physical.mode}" if physical.key and physical.mode else physical.key
    )

    metrics: dict[str, Any] = {
        "bpm": float(physical.bpm) if physical.bpm is not None else None,
        "key": combined_key,
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
    ]

    return ObservedRPE(
        id=id,
        domain="music",
        signals=signals,
        metrics=metrics,
        source="rpe_extract",
    )
