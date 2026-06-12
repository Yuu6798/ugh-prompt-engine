"""tests/test_config.py — Config loading tests."""
from __future__ import annotations

import pytest

from svp_rpe.utils.config_loader import load_config
from svp_rpe.utils import config_loader as config_loader_module


def test_load_pro_baseline():
    cfg = load_config("pro_baseline")
    assert "rms_mean_pro" in cfg
    assert isinstance(cfg["rms_mean_pro"], float)


@pytest.mark.parametrize("name", ["loud_pop_baseline", "acoustic_baseline", "edm_baseline"])
def test_load_baseline_profiles(name: str):
    cfg = load_config(name)
    assert set(cfg) == {
        "rms_mean_pro",
        "active_rate_ideal",
        "crest_factor_ideal",
        "valley_depth_pro",
        "thickness_pro",
    }


def test_load_semantic_rules():
    cfg = load_config("semantic_rules")
    assert cfg["schema_version"] == "2.0"
    assert len(cfg["perceptual"]) >= 1
    assert len(cfg["structural"]) >= 1
    assert len(cfg["semantic_hypothesis"]) >= 1


def test_load_packaged_configs_without_local_config(monkeypatch):
    monkeypatch.setattr(config_loader_module, "_local_config_paths", lambda name: [])

    assert "rms_mean_pro" in load_config("pro_baseline")
    assert "rms_mean_pro" in load_config("loud_pop_baseline")
    assert "rms_mean_pro" in load_config("acoustic_baseline")
    assert "rms_mean_pro" in load_config("edm_baseline")
    assert load_config("semantic_rules")["schema_version"] == "2.0"
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


def test_packaged_semantic_rules_match_repo_config() -> None:
    """パッケージ同梱 config はリポジトリ config と同期していること。

    load_config はローカル checkout が無い環境でパッケージリソースへフォールバック
    するため、乖離するとインストール実行時のみ旧ルールで動く（PR #66 レビュー指摘）。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    repo_copy = (root / "config" / "semantic_rules.yaml").read_text(encoding="utf-8")
    packaged_copy = (
        root / "src" / "svp_rpe" / "config" / "semantic_rules.yaml"
    ).read_text(encoding="utf-8")
    assert repo_copy == packaged_copy
