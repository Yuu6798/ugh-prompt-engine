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


def test_empty_local_config_override_is_preserved(monkeypatch, tmp_path):
    path = tmp_path / "semantic_rules.yaml"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(config_loader_module, "_local_config_paths", lambda name: [path])

    assert load_config("semantic_rules") == {}


def test_load_nonexistent_raises():
    with pytest.raises(FileNotFoundError):
        load_config("nonexistent_config")


def test_packaged_configs_match_repo_configs() -> None:
    """パッケージ同梱 config はリポジトリ config と同期していること。

    load_config はローカル checkout が無い環境でパッケージリソースへフォールバック
    するため、乖離するとインストール実行時のみ旧ルールで動く（PR #66 レビュー指摘）。

    `config/` 直下と `config/domain_profiles/` `config/device_profiles/` の全 YAML
    を動的に列挙して比較する（固定ファイル名の列挙だと新規追加分がすり抜ける —
    負債監査指摘）。両側のファイル集合の一致も assert し、片側にしか無いファイルを
    検出する。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    repo_config_root = root / "config"
    packaged_config_root = root / "src" / "svp_rpe" / "config"

    subdirs = ("", "domain_profiles", "device_profiles")

    repo_relatives: set[str] = set()
    for subdir in subdirs:
        repo_dir = repo_config_root / subdir if subdir else repo_config_root
        for path in sorted(repo_dir.glob("*.yaml")):
            repo_relatives.add(str((Path(subdir) / path.name)) if subdir else path.name)

    packaged_relatives: set[str] = set()
    for subdir in subdirs:
        packaged_dir = packaged_config_root / subdir if subdir else packaged_config_root
        for path in sorted(packaged_dir.glob("*.yaml")):
            packaged_relatives.add(str((Path(subdir) / path.name)) if subdir else path.name)

    assert repo_relatives == packaged_relatives, (
        f"config file set mismatch: repo-only={repo_relatives - packaged_relatives}, "
        f"packaged-only={packaged_relatives - repo_relatives}"
    )
    assert repo_relatives, "expected at least one config YAML to compare"

    for relative in sorted(repo_relatives):
        repo_copy = (repo_config_root / relative).read_text(encoding="utf-8")
        packaged_copy = (packaged_config_root / relative).read_text(encoding="utf-8")
        assert repo_copy == packaged_copy, f"config drift: {relative}"
