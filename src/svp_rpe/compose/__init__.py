"""Composition Score schema and conversion helpers."""
from __future__ import annotations

from svp_rpe.compose.convert import composition_to_target_svp
from svp_rpe.compose.fixity import field_fixity
from svp_rpe.compose.loader import load_composition_score
from svp_rpe.compose.models import (
    ChordSpec,
    CompositionScore,
    DeltaESpec,
    EventLayer,
    FixityState,
    GeneratedPrompt,
    GrvSpec,
    Meta,
    PhysicalLayer,
    RenderingConfig,
    SemanticLayer,
    StructureSection,
)
from svp_rpe.compose.prompt_renderer import ExternalPromptAdapter

__all__ = [
    "ChordSpec",
    "CompositionScore",
    "DeltaESpec",
    "EventLayer",
    "ExternalPromptAdapter",
    "FixityState",
    "GeneratedPrompt",
    "GrvSpec",
    "Meta",
    "PhysicalLayer",
    "RenderingConfig",
    "SemanticLayer",
    "StructureSection",
    "composition_to_target_svp",
    "field_fixity",
    "load_composition_score",
]
