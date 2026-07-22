"""`svprpe recast init` のテスト（PR5）。

実音源からの抽出（`extract_rpe_from_file`）を伴うため各テストは
`@pytest.mark.slow`（`examples/sample_input/*.wav` 1 本あたり実測 ~15-20 秒）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from svp_rpe.arrange.identity import load_identity_manifest
from svp_rpe.recast import load_recast_project
from svp_rpe.sentinels import is_todo_sentinel
from svp_rpe.cli import app

runner = CliRunner()

SAMPLE_AUDIO = Path("examples/sample_input/synth_01_slow_pad_c_major.wav")


@pytest.mark.slow
def test_recast_init_no_interactive_generates_full_project_with_todo_core(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "proj"

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--no-interactive",
        ],
    )
    assert result.exit_code == 0, result.output

    # --- generated file set --------------------------------------------------
    assert (project_dir / "project.yaml").is_file()
    assert (project_dir / "composition_score.yaml").is_file()
    assert (project_dir / "identity.yaml").is_file()
    assert (project_dir / "identity" / "chord_progression.json").is_file()
    assert (project_dir / "identity" / "section_map.json").is_file()
    assert (project_dir / "arrangements" / "default.yaml").is_file()
    assert (project_dir / "source" / SAMPLE_AUDIO.name).is_file()
    assert (
        project_dir / "source" / SAMPLE_AUDIO.name
    ).read_bytes() == SAMPLE_AUDIO.read_bytes()

    # --- loadable + sha256 pins integrity ------------------------------------
    loaded = load_recast_project(project_dir / "project.yaml")
    assert set(loaded.project.variants) == {"default"}
    assert set(loaded.project.backends) == {"suno", "deterministic"}
    assert loaded.project.observation.enabled is True

    manifest = load_identity_manifest(project_dir / "identity.yaml")  # raises on hash mismatch
    anchor_ids = {a.id for a in manifest.anchors}
    assert anchor_ids == {"harmony", "structure"}
    assert manifest.source.rights_basis == "unknown"
    assert manifest.source.locator == f"source/{SAMPLE_AUDIO.name}"
    assert manifest.source.sha256 == hashlib.sha256(SAMPLE_AUDIO.read_bytes()).hexdigest()

    chord_payload = json.loads(
        (project_dir / "identity" / "chord_progression.json").read_text(encoding="utf-8")
    )
    assert chord_payload["schema"] == "chord-sequence/0.1"
    assert len(chord_payload["chords"]) > 0

    section_payload = json.loads(
        (project_dir / "identity" / "section_map.json").read_text(encoding="utf-8")
    )
    assert section_payload["schema_version"] == "section-map/0.1"
    assert len(section_payload["sections"]) > 0

    arrangement = yaml.safe_load(
        (project_dir / "arrangements" / "default.yaml").read_text(encoding="utf-8")
    )
    assert arrangement["preservation"]["identity_anchors"]["harmony"]["mode"] == "hard"
    assert arrangement["preservation"]["identity_anchors"]["structure"]["mode"] == "hard"

    # --- --no-interactive leaves semantic.core as TODO -------------------------
    score = yaml.safe_load(
        (project_dir / "composition_score.yaml").read_text(encoding="utf-8")
    )
    assert is_todo_sentinel(score["semantic"]["core"])

    # --- fail-closed acceptance test: unresolved TODO -> blocked_authoring ------
    plan_result = runner.invoke(
        app,
        [
            "recast", "plan", str(project_dir / "project.yaml"),
            "--variant", "default", "--backend", "suno",
        ],
    )
    assert plan_result.exit_code == 1
    assert "blocked_authoring" in plan_result.output


@pytest.mark.slow
def test_recast_init_interactive_fills_semantic_core_and_avoid(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--interactive",
        ],
        input="introspective night drive\nclutter, harsh clipping\nheadlights on a wet road\n",
    )
    assert result.exit_code == 0, result.output

    score = yaml.safe_load(
        (project_dir / "composition_score.yaml").read_text(encoding="utf-8")
    )
    assert score["semantic"]["core"] == (
        "introspective night drive; headlights on a wet road"
    )
    assert score["semantic"]["avoid"] == ["clutter", "harsh clipping"]
    assert not is_todo_sentinel(score["semantic"]["core"])


@pytest.mark.slow
def test_recast_init_refuses_nonempty_project_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "existing.txt").write_text("do not touch", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "recast", "init", str(SAMPLE_AUDIO),
            "--project-dir", str(project_dir),
            "--no-interactive",
        ],
    )
    assert result.exit_code == 1
    assert not (project_dir / "project.yaml").exists()
    assert (project_dir / "existing.txt").read_text(encoding="utf-8") == "do not touch"
