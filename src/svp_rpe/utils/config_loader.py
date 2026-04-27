"""utils/config_loader.py — YAML config loading."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any, Optional

import yaml


def load_config(name: str) -> dict[str, Any]:
    """Load YAML config, preferring local config/ over packaged resources."""
    data = _load_local_config(name) or _load_packaged_config(name)
    if data is None:
        searched = ", ".join(str(p) for p in _local_config_paths(name))
        raise FileNotFoundError(
            f"config not found for {name!r}; searched {searched} "
            "and packaged svp_rpe.config resources"
        )
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: {name}")
    return data


def _load_local_config(name: str) -> Optional[dict[str, Any]]:
    for path in _local_config_paths(name):
        if path.is_file():
            return _load_config_file(path)
    return None


def _load_config_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: {path}")
    return data


def _load_packaged_config(name: str) -> Optional[dict[str, Any]]:
    try:
        resource = files("svp_rpe.config").joinpath(f"{name}.yaml")
    except ModuleNotFoundError:
        return None
    if not resource.is_file():
        return None
    data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config must be a mapping: packaged {name}.yaml")
    return data


def _local_config_paths(name: str) -> list[Path]:
    return [
        Path(__file__).resolve().parents[3] / "config" / f"{name}.yaml",
        Path.cwd() / "config" / f"{name}.yaml",
    ]
