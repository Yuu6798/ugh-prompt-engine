"""utils/config_loader.py — YAML config loading."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any, Optional

import yaml


def load_config(name: str) -> dict[str, Any]:
    """Load YAML config, preferring local config/ over packaged resources."""
    data = _load_local_config(name)
    if data is None:
        data = _load_packaged_config(name)
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


def load_packaged_config(name: str) -> Optional[dict[str, Any]]:
    """Load only the packaged config resource, ignoring local config/.

    Used to backfill sections that a stale or partial local config omits,
    avoiding silent regressions when local config wins over packaged.
    """
    return _load_packaged_config(name)


def resolve_config_bytes(name: str) -> Optional[bytes]:
    """`load_config` と同じ local→packaged 解決順序で `name` の raw bytes を返す。

    中身のパース/検証を必要としない呼び出し側（例: `recast.plan` の
    stale-run 検出用 digest 計算 — 実際に読まれるファイルと同一の解決パスを
    再現する必要がある）向け。見つからなければ `None`（`load_config` の
    `FileNotFoundError` 相当だが、こちらは「見つからない」を正当な結果として
    返す — 呼び出し側が存在有無を明示的に扱えるようにする）。"""
    for path in _local_config_paths(name):
        if path.is_file():
            return path.read_bytes()
    return _load_packaged_config_bytes(name)


def _load_packaged_config_bytes(name: str) -> Optional[bytes]:
    try:
        resource = files("svp_rpe.config").joinpath(f"{name}.yaml")
    except ModuleNotFoundError:
        return None
    if not resource.is_file():
        return None
    return resource.read_bytes()


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
