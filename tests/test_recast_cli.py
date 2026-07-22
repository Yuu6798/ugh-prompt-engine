"""`svprpe recast plan` / `svprpe recast status` CLI テスト（PR2）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from svp_rpe.cli import app

DEMO_PROJECT = Path("examples/recast/demo_project")
EXPECTED_PLAN = DEMO_PROJECT / "expected" / "recast_plan_edm_suno.json"

runner = CliRunner()


def _copy_demo_project(tmp_path: Path) -> Path:
    dest = tmp_path / "demo_project"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def test_recast_plan_succeeds_and_writes_plan_json(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 0, result.output
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.is_file()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "verified"
    assert "blocked" not in data or data["blocked"] is None


def test_recast_plan_matches_committed_snapshot_via_cli(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 0, result.output
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.read_text(encoding="utf-8") == EXPECTED_PLAN.read_text(encoding="utf-8")


def test_recast_plan_exits_nonzero_when_blocked(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text(
        score_path.read_text(encoding="utf-8").replace(
            'core: "introspective night drive"',
            'core: "TODO(transcribe): author input required"',
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.is_file()  # blocked plans are still published
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "blocked_authoring"


def test_recast_plan_unknown_variant_exits_nonzero_without_writing_plan(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app,
        ["recast", "plan", str(project_path), "--variant", "does-not-exist", "--backend", "suno"],
    )

    assert result.exit_code == 1
    assert not (project_path.parent / "recast_plan.json").exists()


def test_recast_status_reports_draft_before_any_plan_run(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(app, ["recast", "status", str(project_path)])

    assert result.exit_code == 0, result.output
    assert "draft" in result.output
    assert "edm@suno" in result.output


def test_recast_status_reflects_state_after_plan_run(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    plan_result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert plan_result.exit_code == 0, plan_result.output

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])

    assert status_result.exit_code == 0, status_result.output
    assert "verified" in status_result.output


def test_recast_help_lists_plan_and_status() -> None:
    result = runner.invoke(app, ["recast", "--help"])

    assert result.exit_code == 0
    assert "plan" in result.output
    assert "status" in result.output
