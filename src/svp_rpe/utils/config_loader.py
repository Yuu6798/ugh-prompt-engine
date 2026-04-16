"""utils/config_loader.py — YAML config loading."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config"


def load_config(name: str) -> dict[str, Any]:
    """Load a YAML config file from the config/ directory.

    Args:
        name: config file name (e.g. "pro_baseline" loads config/pro_baseline.yaml)

    Returns:
        Parsed YAML dict.
    """
    path = _CONFIG_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"config not found: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)
