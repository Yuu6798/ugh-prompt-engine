"""utils/config_loader.py — YAML config loading."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _find_config_dir() -> Path:
    """Locate the config/ directory. Searches relative to module, then CWD."""
    candidates = [
        Path(__file__).resolve().parents[3] / "config",  # src layout
        Path.cwd() / "config",                            # CWD fallback
    ]
    for p in candidates:
        if p.is_dir():
            return p
    # Return first candidate even if missing — FileNotFoundError on load
    return candidates[0]


_CONFIG_DIR = _find_config_dir()


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
