"""Pydantic models for author-facing Composition Score YAML."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, field_validator

_TRANSCRIBE_TODO_PREFIX = "TODO(transcribe):"


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

    @field_validator("bpm", mode="before")
    @classmethod
    def normalize_bpm(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            signless = stripped[1:] if stripped[:1] in {"+", "-"} else stripped
            if signless.isdigit():
                return int(stripped)
        return value

    @field_validator("bpm")
    @classmethod
    def reject_unknown_bpm_text(cls, value: int | str) -> int | str:
        if isinstance(value, str) and not value.startswith(_TRANSCRIBE_TODO_PREFIX):
            raise ValueError(
                "bpm must be an integer, numeric string, or TODO(transcribe): sentinel"
            )
        return value


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
