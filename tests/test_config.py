"""tests/test_config.py — Config loading tests."""
from __future__ import annotations

import pytest

from svp_rpe.utils.config_loader import load_config
from svp_rpe.utils import config_loader as config_loader_module


def test_load_pro_baseline():
    cfg = load_config("pro_baseline")
    assert "rms_mean_pro" in cfg
    assert isinstance(cfg["rms_mean_pro"], float)


def test_load_semantic_rules():
    cfg = load_config("semantic_rules")
    assert "rules" in cfg
    assert len(cfg["rules"]) >= 1


def test_load_packaged_configs_without_local_config(monkeypatch):
    monkeypatch.setattr(config_loader_module, "_local_config_paths", lambda name: [])

    assert "rms_mean_pro" in load_config("pro_baseline")
    assert "rules" in load_config("semantic_rules")
    assert "groups" in load_config("synonym_map")
    assert "default" in load_config("svp_templates")


def test_empty_local_config_override_is_preserved(monkeypatch, tmp_path):
    path = tmp_path / "semantic_rules.yaml"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(config_loader_module, "_local_config_paths", lambda name: [path])

    assert load_config("semantic_rules") == {}


def test_load_nonexistent_raises():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_config")
