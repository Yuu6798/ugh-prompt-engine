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


def test_recast_plan_write_failure_does_not_persist_state(tmp_path: Path, monkeypatch) -> None:
    """`recast_plan.json` の atomic publish が失敗した場合、`record_state` は
    呼ばれず `recast_state.json` も書き換わらないことを検証する（Codex P2 second
    round #207: state を plan 公開成功後にのみ記録する順序保証。公開失敗時に
    stale state を残さない）。"""
    import svp_rpe.cli.recast_cmd as recast_cmd

    project_path = _copy_demo_project(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated disk failure")

    monkeypatch.setattr(recast_cmd.os, "replace", _boom)

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    plan_path = project_path.parent / "recast_plan.json"
    assert not plan_path.exists()
    state_path = project_path.parent / "recast_state.json"
    assert not state_path.exists()


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


def test_recast_status_reports_stale_after_input_changes(tmp_path: Path) -> None:
    """`recast plan` 実行後に入力（score）が書き換わった場合、`recast status` は
    永続化済み state をそのまま信用せず stale（`recast plan` 再実行が必要）と
    表示することを検証する（Codex P2 second round #207: `inputs_digest` による
    stale run 検出）。"""
    project_path = _copy_demo_project(tmp_path)
    plan_result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert plan_result.exit_code == 0, plan_result.output

    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text(
        score_path.read_text(encoding="utf-8").replace(
            'core: "introspective night drive"',
            'core: "introspective night drive, revised"',
        ),
        encoding="utf-8",
    )

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])

    assert status_result.exit_code == 0, status_result.output
    # rich がテーブル幅で折り返すため、断片同士の連結ではなく個別トークンで検証する。
    assert "stale" in status_result.output
    assert "再実行" in status_result.output


def test_recast_status_reports_stale_after_identity_artifact_changes(tmp_path: Path) -> None:
    """`recast plan` 実行後に identity manifest **自身**は無変更のまま、それが
    参照する anchor artifact（`identity/lyrics.txt`）だけが書き換わった場合でも
    `recast status` は stale と表示し、生成系の次の一手を出さないことを検証する
    （Codex P2 third round #207: `identity_manifest` component だけでは
    manifest.yaml 無変更・参照先のみ drift のケースを検出できなかった欠陥）。"""
    project_path = _copy_demo_project(tmp_path)
    plan_result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert plan_result.exit_code == 0, plan_result.output

    lyrics_path = project_path.parent / "identity" / "lyrics.txt"
    original_bytes = lyrics_path.read_bytes()
    lyrics_path.write_bytes(original_bytes + b"X")  # 1 byte tamper, hash now stale

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])

    assert status_result.exit_code == 0, status_result.output
    assert "stale" in status_result.output
    assert "再実行" in status_result.output
    # generated への案内（generation next step）は出ない — 生成系の次の一手を出さない。
    assert "backend を実行して音声を生成する" not in status_result.output


def test_recast_help_lists_plan_and_status() -> None:
    result = runner.invoke(app, ["recast", "--help"])

    assert result.exit_code == 0
    assert "plan" in result.output
    assert "status" in result.output
