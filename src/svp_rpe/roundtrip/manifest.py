"""Roundtrip corpus manifest schema and local audio resolver."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from svp_rpe.compose.models import PhysicalLayer
from svp_rpe.perform import sha256_bytes

SendForm = Literal["numeric_knob", "qualitative_cue", "not_sent"]
TakeLayer = Literal["calibratable", "observation_log"]


class FieldIntent(BaseModel):
    """One intended score field and how it was sent to the generator."""

    model_config = ConfigDict(extra="forbid")

    value: Any
    send_form: SendForm


class RoundtripTake(BaseModel):
    """One corpus take record.

    ``intent`` is intentionally partial: real generator prompts often specify
    only a subset of the seven physical CompositionScore fields.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    generator: str
    prompt: str
    intent: dict[str, FieldIntent]
    measured: dict[str, Any] | None = None
    audio_hash: str | None = None
    audio_locator: str | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("audio_locator")
    @classmethod
    def reject_unsupported_artifact_uri(cls, value: str | None) -> str | None:
        if value is not None and "://" in value:
            raise ValueError(
                "audio_locator currently supports only repository-relative local "
                "paths; artifact URI resolution is not implemented"
            )
        return value

    @field_validator("intent")
    @classmethod
    def validate_intent_keys(
        cls, value: dict[str, FieldIntent]
    ) -> dict[str, FieldIntent]:
        allowed = set(PhysicalLayer.model_fields)
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                "intent keys must be CompositionScore.physical fields; "
                f"unknown keys: {', '.join(unknown)}"
            )
        return dict(value)


class RoundtripManifest(BaseModel):
    """Corpus manifest containing calibratable and observation-log takes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    takes: list[RoundtripTake] = Field(default_factory=list)


def load_manifest(path: str | Path) -> RoundtripManifest:
    """Load a YAML roundtrip corpus manifest."""

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RoundtripManifest.model_validate(data)


def classify_take(
    take: RoundtripTake,
    *,
    repo_root: str | Path | None = None,
) -> TakeLayer:
    """Classify a take by whether its local audio can be resolved and verified."""

    path = resolve_audio_path(take, repo_root=repo_root)
    if path is None or not take.audio_hash:
        return "observation_log"
    actual_hash = sha256_bytes(path.read_bytes())
    if actual_hash == take.audio_hash:
        return "calibratable"
    return "observation_log"


def resolve_audio_path(
    take: RoundtripTake,
    *,
    repo_root: str | Path | None = None,
) -> Path | None:
    """Resolve a repository-relative audio locator to a local file path."""

    if not take.audio_locator:
        return None
    locator = Path(take.audio_locator)
    if locator.is_absolute():
        return None
    root = Path(repo_root) if repo_root is not None else _repo_root()
    candidate = (root / locator).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
