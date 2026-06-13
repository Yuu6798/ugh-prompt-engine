"""Pydantic models for author-facing Composition Score YAML."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, field_validator


class CompositionModel(BaseModel):
    """Base model that rejects keys outside the canonical Composition Score schema."""

    model_config = ConfigDict(extra="forbid")


class Meta(CompositionModel):
    title: str
    version: str | float

    @field_validator("version")
    @classmethod
    def normalize_version(cls, value: str | float) -> str:
        return str(value)


class GrvSpec(CompositionModel):
    primary: str
    secondary: str


class DeltaESpec(CompositionModel):
    overall: str


class SemanticLayer(CompositionModel):
    core: str
    grv: GrvSpec
    delta_e: DeltaESpec
    avoid: List[str]


class PhysicalLayer(CompositionModel):
    bpm: int | str
    key: str
    time_signature: str
    active_rate_target: str
    valley_depth_target: str
    brightness: str
    stereo_width: str


class StructureSection(CompositionModel):
    section: str
    bars: int
    role: str
    physical: str


class RenderingConfig(CompositionModel):
    target_backend: str
    prompt_max_chars: int
    priority: List[str]


class CompositionScore(CompositionModel):
    meta: Meta
    semantic: SemanticLayer
    physical: PhysicalLayer
    structure: List[StructureSection]
    rendering: RenderingConfig


class GeneratedPrompt(CompositionModel):
    backend: Literal["external", "musicgen", "midi"] = "external"
    text: str
    tags: List[str]
    negative_tags: List[str]
    dropped_elements: List[str]
