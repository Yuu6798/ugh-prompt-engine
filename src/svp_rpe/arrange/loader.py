"""YAML loader for ArrangementSpec documents."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from svp_rpe.arrange.models import ArrangementSpec


def load_arrangement_spec(path: Path | str) -> ArrangementSpec:
    """Load an ArrangementSpec YAML file and validate it against the canonical schema."""
    data = _load_yaml_mapping(Path(path))
    return ArrangementSpec.model_validate(data)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"arrangement spec must be a mapping: {path}")
    return data
