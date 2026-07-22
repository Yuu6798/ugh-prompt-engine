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


def test_recast_plan_blocks_capability_on_corrupted_capability_profile(tmp_path: Path) -> None:
    """capability_profile の YAML 破損は identity manifest / arrangement spec の
    同種の失敗（blocked_verification / blocked_authoring）と一貫させ
    blocked_capability として recast_plan.json に publish + state 記録される
    ことを検証する（Codex P2 eighth round #207: 以前は保存済み例外を re-raise
    し、CLI が top-level Error で落ちて plan/state が一切書かれなかった）。"""
    project_path = _copy_demo_project(tmp_path)
    project_text = project_path.read_text(encoding="utf-8")
    mutated = project_text.replace(
        'capability_profile: "suno"', 'capability_profile: "capability_profile.yaml"'
    )
    assert mutated != project_text  # sanity: the replacement actually matched
    project_path.write_text(mutated, encoding="utf-8")
    (project_path.parent / "capability_profile.yaml").write_text(
        "not-a-mapping\n", encoding="utf-8"
    )

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.is_file()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "blocked_capability"

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0, status_result.output
    assert "blocked_capability" in status_result.output


def test_recast_plan_blocks_capability_on_corrupted_mode_overrides(tmp_path: Path) -> None:
    """mode_overrides の YAML 破損も同様に blocked_capability として publish +
    state 記録される（Codex P2 eighth round #207）。"""
    project_path = _copy_demo_project(tmp_path)
    project_text = project_path.read_text(encoding="utf-8")
    mutated = project_text.replace(
        'mode_overrides: "suno"', 'mode_overrides: "mode_overrides.yaml"'
    )
    assert mutated != project_text  # sanity: the replacement actually matched
    project_path.write_text(mutated, encoding="utf-8")
    (project_path.parent / "mode_overrides.yaml").write_text("not-a-mapping\n", encoding="utf-8")

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.is_file()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "blocked_capability"

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0, status_result.output
    assert "blocked_capability" in status_result.output


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


def test_recast_status_reports_stale_after_device_profile_changes(
    tmp_path: Path, monkeypatch
) -> None:
    """`recast plan` 実行後に、実際に使われる device profile
    （`config/device_profiles/suno.yaml`）の内容が変わった場合、`recast status`
    は stale と表示することを検証する（Codex P2 fourth round #207:
    `inputs_digest` が device profile の中身を一切見ていなかった欠陥）。

    実リポジトリの `config/device_profiles/suno.yaml` は変更せず、
    `svp_rpe.recast.plan.resolve_config_bytes`（digest 専用の解決経路）だけを
    monkeypatch でスタブに差し替え、tmp_path 配下の fake ファイルを指すように
    する（実 pipeline が使う `load_device_profile` 経路とは独立 — plan の
    verified 到達には影響しない）。"""
    import svp_rpe.recast.plan as recast_plan_module

    project_path = _copy_demo_project(tmp_path)

    device_profile_path = tmp_path / "fake_device_profile.yaml"
    device_profile_path.write_bytes(b"schema_version: device-profile/fake\ngenerator: suno\n")

    def _fake_resolve_config_bytes(name: str) -> bytes | None:
        if name == "device_profiles/suno":
            return device_profile_path.read_bytes()
        return None

    monkeypatch.setattr(recast_plan_module, "resolve_config_bytes", _fake_resolve_config_bytes)

    plan_result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert plan_result.exit_code == 0, plan_result.output

    device_profile_path.write_bytes(
        b"schema_version: device-profile/fake\ngenerator: suno\nnotes: changed\n"
    )

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])

    assert status_result.exit_code == 0, status_result.output
    assert "stale" in status_result.output
    assert "再実行" in status_result.output


def _add_cover_backend(project_path: Path) -> None:
    """既存 `suno` backend と同じ `capability_profile`/`mode_overrides` を再利用し
    invocation_mode だけ異なる第 2 backend `suno_cover` を project.yaml に追記する
    （recast_plan.json 上書きテスト専用 — variant はどの backend でも `edm` を
    共用するため新規 arrangement は不要）。`suno:` backend ブロック直後（唯一の
    `mode_overrides: "suno"` 行の直後）へ挿入する — PR3 で `deterministic:`
    backend が `suno:` の後に追加されたため、`policy:` への直接隣接は前提に
    できない（backends マッピング内での挿入位置は YAML/pydantic の dict なので
    どこでもよい）。"""
    text = project_path.read_text(encoding="utf-8")
    mutated = text.replace(
        '    mode_overrides: "suno"\n',
        '    mode_overrides: "suno"\n'
        "  suno_cover:\n"
        '    capability_profile: "suno"\n'
        "    invocation: manual\n"
        "    invocation_mode: cover\n"
        '    mode_overrides: "suno"\n',
        1,
    )
    assert mutated != text  # sanity: the replacement actually matched
    project_path.write_text(mutated, encoding="utf-8")


def test_recast_status_reports_stale_after_plan_overwritten_by_other_run(
    tmp_path: Path,
) -> None:
    """`recast_plan.json` は project 単位で単一ファイル — 別の (variant, backend)
    向けに `recast plan` を再実行すると同じファイルが上書きされる。元の run
    （`edm@suno`）の state はまだ `verified` のままだが、その `plan_sha256` は
    もはや現在の `recast_plan.json`（`edm@suno_cover` 分）と一致しない。
    `recast status` はこれを検出して stale 表示にすることを検証する（Codex P2
    fourth round #207: 生成物の削除・破損・別 run による上書きへの fail-closed
    fallback）。"""
    project_path = _copy_demo_project(tmp_path)
    _add_cover_backend(project_path)

    first = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert first.exit_code == 0, first.output

    # 2 回目の実行結果（verified/blocked）は問わない — recast_plan.json が
    # 別内容で上書きされることだけがこのテストの前提。
    runner.invoke(
        app,
        ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno_cover"],
    )
    assert (project_path.parent / "recast_plan.json").is_file()

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])

    assert status_result.exit_code == 0, status_result.output
    assert "stale" in status_result.output
    assert "再実行" in status_result.output


def test_recast_help_lists_plan_and_status() -> None:
    result = runner.invoke(app, ["recast", "--help"])

    assert result.exit_code == 0
    assert "plan" in result.output
    assert "status" in result.output
