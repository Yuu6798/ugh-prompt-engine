"""Composition Score schema and conversion helpers."""
from __future__ import annotations

from svp_rpe.compose.convert import composition_to_target_svp
from svp_rpe.compose.device_profile import (
    CrossCoupling,
    DeviceProfile,
    KnobQuirk,
    QuirkStatus,
    SpectralBias,
    load_device_profile,
)
from svp_rpe.compose.fixity import field_fixity
from svp_rpe.compose.loader import load_composition_score
from svp_rpe.compose.models import (
    ChordSpec,
    CompositionScore,
    ControlGrip,
    DeltaESpec,
    EventLayer,
    FixityState,
    GeneratedPrompt,
    GeneratorProfile,
    GripClass,
    GrvSpec,
    Meta,
    PhysicalLayer,
    RenderingConfig,
    SEMANTIC_CONTROL_FIELDS,
    SemanticLayer,
    StructureSection,
)
from svp_rpe.compose.prompt_renderer import (
    BackendDescriptor,
    ExternalPromptAdapter,
    resolve_backend_descriptor,
)

__all__ = [
    "BackendDescriptor",
    "ChordSpec",
    "CompositionScore",
    "ControlGrip",
    "CrossCoupling",
    "DeltaESpec",
    "DeviceProfile",
    "EventLayer",
    "ExternalPromptAdapter",
    "FixityState",
    "GeneratedPrompt",
    "GeneratorProfile",
    "GripClass",
    "GrvSpec",
    "KnobQuirk",
    "Meta",
    "PhysicalLayer",
    "QuirkStatus",
    "RenderingConfig",
    "SEMANTIC_CONTROL_FIELDS",
    "SemanticLayer",
    "SpectralBias",
    "StructureSection",
    "composition_to_target_svp",
    "field_fixity",
    "load_composition_score",
    "load_device_profile",
    "resolve_backend_descriptor",
]
