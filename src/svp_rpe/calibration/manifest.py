"""Genre calibration manifest schema."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class GenreSample(BaseModel):
    """One labeled calibration sample."""

    model_config = ConfigDict(extra="forbid")

    id: str
    genre_label: str
    generator: str
    prompt: str
    measured: dict[str, Any] | None = None
    audio_hash: str | None = None
    audio_locator: str | None = None
    drive_file_id: str | None = None
    excluded: bool = False
    notes: list[str] = Field(default_factory=list)


class GenreCorpusManifest(BaseModel):
    """Genre-labeled corpus manifest for calibration analysis."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    samples: list[GenreSample] = Field(default_factory=list)


def load_genre_manifest(path: str | Path) -> GenreCorpusManifest:
    """Load a YAML genre calibration manifest."""

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return GenreCorpusManifest.model_validate(data)
