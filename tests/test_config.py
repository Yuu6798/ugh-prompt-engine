"""tests/test_config.py — Config loading tests."""
from __future__ import annotations

import pytest

from svp_rpe.utils.config_loader import load_config


def test_load_pro_baseline():
    cfg = load_config("pro_baseline")
    assert "rms_mean_pro" in cfg
    assert isinstance(cfg["rms_mean_pro"], float)


def test_load_semantic_rules():
    cfg = load_config("semantic_rules")
    assert "rules" in cfg
    assert len(cfg["rules"]) >= 1


def test_load_nonexistent_raises():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_config")
