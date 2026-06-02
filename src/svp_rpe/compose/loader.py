"""YAML loader for Composition Score documents."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from svp_rpe.compose.models import CompositionScore


def load_composition_score(path: Path | str) -> CompositionScore:
    """Load a Composition Score YAML file and validate it against the canonical schema."""
    data = _load_yaml_mapping(Path(path))
    return CompositionScore.model_validate(data)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"composition score must be a mapping: {path}")
    return data
