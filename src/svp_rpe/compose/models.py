"""Pydantic models for author-facing Composition Score YAML."""
from __future__ import annotations

from typing import Any, List, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_serializer,
    model_validator,
)

from svp_rpe.sentinels import TODO_SENTINEL_PREFIX, is_todo_sentinel

FixityState = Literal["locked", "unlocked"]


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
        if isinstance(value, str) and not value.startswith(TODO_SENTINEL_PREFIX):
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
    fixity: dict[str, FixityState] | None = None

    @field_validator("fixity")
    @classmethod
    def validate_fixity_keys(
        cls, value: dict[str, FixityState] | None
    ) -> dict[str, FixityState] | None:
        if value is None:
            return None
        allowed = set(PhysicalLayer.model_fields)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                "fixity keys must be CompositionScore.physical fields; "
                f"unknown keys: {', '.join(unknown)}"
            )
        missing = sorted(allowed - set(value))
        if missing:
            raise ValueError(
                "fixity must cover every CompositionScore.physical field; "
                f"missing keys: {', '.join(missing)}"
            )
        return dict(value)

    @model_validator(mode="after")
    def validate_fixity_matches_physical_values(self) -> Self:
        if self.fixity is None:
            return self
        mismatches = [
            field
            for field, state in self.fixity.items()
            if state != _fixity_state_for_value(getattr(self.physical, field))
        ]
        if mismatches:
            raise ValueError(
                "fixity states must match physical TODO(transcribe): state; "
                f"mismatched keys: {', '.join(sorted(mismatches))}"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_without_empty_fixity(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.fixity is None:
            data.pop("fixity", None)
        return data


class GeneratedPrompt(CompositionModel):
    backend: Literal["external", "musicgen", "midi"] = "external"
    text: str
    tags: List[str]
    negative_tags: List[str]
    dropped_elements: List[str]


def _fixity_state_for_value(value: Any) -> FixityState:
    return "unlocked" if is_todo_sentinel(value) else "locked"
