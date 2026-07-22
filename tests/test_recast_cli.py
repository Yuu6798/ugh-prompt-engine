"""`svprpe recast plan` / `svprpe recast status` CLI テスト（PR2）。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.recast.state import load_recast_state

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


def test_recast_plan_records_sha256_matching_actual_written_bytes(tmp_path: Path) -> None:
    """`recast_plan.json` の atomic 書き込みは bytes 経路に統一されている
    （Codex P2 ninth round #207: 旧実装は text モードで書いており、Windows
    では改行変換 `"\n"`→`"\r\n"` により `plan_sha256`（計算元は `"\n"` のみの
    encode 済み bytes）と実際にディスクへ書かれた bytes が乖離しうる —
    直後の `recast status` が偽 stale を報告する原因になっていた）。
    改行変換の再現自体は環境依存のため、ここでは記録された `plan_sha256` が
    実際に書かれたファイルの生 bytes の sha256 と厳密に一致することを直接
    検証する（現在の実行環境で既に一致していれば、bytes 経路であることの
    十分条件を満たす — text モードのままなら POSIX でも一致はするが、
    それでも single-source of truth 化そのものは常に検証可能）。"""
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert result.exit_code == 0, result.output

    plan_path = project_path.parent / "recast_plan.json"
    actual_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()

    state_file = load_recast_state(project_path.parent)
    recorded_plan_sha256 = state_file.runs["edm@suno"].plan_sha256
    assert recorded_plan_sha256 is not None
    assert recorded_plan_sha256 == actual_sha256


def test_recast_verification_staging_bytes_match_report_package_sha256(
    tmp_path: Path, monkeypatch
) -> None:
    """`require_verified_package` の検証用ステージングは、`compiled.report` の
    `package_sha256`（hash 計算元と同一の UTF-8 bytes）を `write_bytes()` で
    書くことを検証する（Codex P2 eleventh round #207: 旧実装は `write_text()`
    を使っており、Windows では改行変換 `"\n"` -> `"\r\n"` によりステージング先の
    実 bytes と `package_sha256` が乖離しうる — `verify_package` はステージング
    先の実 bytes から package_sha256 を再計算して突合するため、乖離があれば
    偽の `blocked_verification` を招く）。

    ステージングは通常 `tempfile.TemporaryDirectory` によりコンテキスト終了時に
    削除される一時ディレクトリのため、`svp_rpe.recast.plan.tempfile.
    TemporaryDirectory` を monkeypatch して削除しない永続ディレクトリ
    （`tmp_path` 配下）に差し替え、CLI 呼び出し完了後もステージング先の実 bytes
    を直接検査できるようにする。"""
    import contextlib

    import svp_rpe.recast.plan as recast_plan_module
    from svp_rpe.arrange.package import COMPILATION_REPORT_FILENAME, PERFORMANCE_PACKAGE_FILENAME

    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()

    @contextlib.contextmanager
    def _persistent_staging_dir(*args: object, **kwargs: object):
        # twelfth round #207 以降、呼び出し側は `dir=loaded.builds_root` を渡す
        # （builds_root 配下へのステージング配置）— このスタブは実際の一時
        # ディレクトリ配置場所を差し替えるだけなので、渡された引数は無視する。
        yield str(staging_dir)

    monkeypatch.setattr(
        recast_plan_module.tempfile, "TemporaryDirectory", _persistent_staging_dir
    )

    project_path = _copy_demo_project(tmp_path)
    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert result.exit_code == 0, result.output

    plan_path = project_path.parent / "recast_plan.json"
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_data["state_reached"] == "verified"  # require_verified_package 経路を通った

    package_bytes = (staging_dir / PERFORMANCE_PACKAGE_FILENAME).read_bytes()
    report_data = json.loads((staging_dir / COMPILATION_REPORT_FILENAME).read_bytes())

    assert hashlib.sha256(package_bytes).hexdigest() == report_data["package_sha256"]


def test_recast_plan_verification_staging_uses_project_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """検証ステージング（`tempfile.TemporaryDirectory`）は project_dir 配下に
    確保されることを検証する（Codex P2 thirteenth round #207, 指摘21:
    twelfth round #207 で builds_root 配下へ変更したが、builds_root は
    project.yaml で未実在パスを宣言できるため mkdir が必要になり、
    `build_recast_plan` の「ディスクへ副作用を持たない純粋関数」契約を破って
    いた。project_dir は loader が project.yaml を読めた時点で実在保証済み
    ── mkdir 不要かつ project files と常に同一ドライブ（twelfth round #207,
    指摘19 の同ドライブ要件は維持）。

    `tempfile.TemporaryDirectory` を実クラスを継承したスタブに monkeypatch し、
    実際に渡された `dir` kwarg を捕捉しつつ本物の挙動へ委譲する（実際の
    cleanup 動作は変えない）。CLI 呼び出し完了後、ステージング先は
    `TemporaryDirectory` の cleanup で削除済みのため project_dir 直下に
    staging 残骸が残らないことも確認する。"""
    import tempfile as tempfile_module

    import svp_rpe.recast.plan as recast_plan_module

    project_path = _copy_demo_project(tmp_path)
    project_dir = project_path.parent
    before_entries = {entry.name for entry in project_dir.iterdir()}

    real_temporary_directory = tempfile_module.TemporaryDirectory
    captured_dir_args: list[object] = []

    class _CapturingTemporaryDirectory(real_temporary_directory):  # type: ignore[misc]
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured_dir_args.append(kwargs.get("dir"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        recast_plan_module.tempfile, "TemporaryDirectory", _CapturingTemporaryDirectory
    )

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert result.exit_code == 0, result.output

    assert len(captured_dir_args) == 1
    assert Path(str(captured_dir_args[0])).resolve() == project_dir.resolve()

    # cleanup 後は project_dir 直下に staging 残骸が残らない（CLI が publish
    # する recast_plan.json / recast_state.json だけが新規に増えている）。
    after_entries = {entry.name for entry in project_dir.iterdir()}
    assert after_entries - before_entries == {"recast_plan.json", "recast_state.json"}
    assert not (project_dir / "builds").exists()  # builds_root は mkdir されない

    plan_path = project_dir / "recast_plan.json"
    plan_text = plan_path.read_text(encoding="utf-8")
    assert str(project_dir) not in plan_text  # staging パスが永続出力に混入しない


def test_build_recast_plan_does_not_create_builds_root(tmp_path: Path) -> None:
    """`build_recast_plan`（純粋関数、ディスクへの副作用なし契約）を API 直呼びし、
    呼び出し前に存在しなかった builds_root がディレクトリとして作られないことを
    検証する（Codex P2 thirteenth round #207, 指摘21: 検証 staging のために
    builds_root を mkdir していたのは純粋関数契約への違反だった）。"""
    from svp_rpe.recast import load_recast_project
    from svp_rpe.recast.plan import build_recast_plan

    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)
    builds_root = loaded.builds_root
    assert not builds_root.exists()  # デモ fixture は builds/ を同梱しない

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "verified"
    assert not builds_root.exists()  # build_recast_plan はディスクへ書き込まない


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


def test_recast_plan_blocks_authoring_on_corrupted_score(tmp_path: Path) -> None:
    """composition_score.yaml の YAML 破損・schema 不正は blocked_authoring として
    recast_plan.json に publish + state 記録されることを検証する（Codex P2
    eleventh round #207: 以前は score の parse/validate を bundle 先頭で無条件に
    呼んでおり、失敗時は捕捉されない例外として関数外へ伝播し CLI が top-level
    Error で落ちて plan/state が一切書かれなかった。score は著者成果物なので
    identity manifest/capability profile とは異なり blocked_authoring に分類する
    — 既存の TODO sentinel 未解決チェックと同じ state_reached）。"""
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text("not-a-mapping\n", encoding="utf-8")

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.is_file()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "blocked_authoring"

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0, status_result.output
    assert "blocked_authoring" in status_result.output


def test_recast_status_after_corrupted_score_plan_is_not_stale(tmp_path: Path) -> None:
    """破損 score で `recast plan` が blocked_authoring を publish した直後、
    同一入力に対する `recast status` が stale と誤判定しないことを検証する
    （Codex P2 thirteenth round #207, 指摘22: device profile digest
    component の失敗表現が、resolution が None になった実際の原因
    （score_parse_error / spec_read_error）ではなく常に `resolve_error`
    （score 破損時は未設定=None のまま）を参照していたため、
    `"NoneType: None"` という無意味な固定文字列を digest へ焼き込んでいた。
    `recast status` が使う独立の `compute_recast_inputs_digest` は実際の
    例外を捕捉するため digest が食い違い、plan/state 発行直後の同一入力
    ですら stale と誤判定していた）。

    `test_recast_plan_blocks_authoring_on_corrupted_score` は state 列に
    常に表示される `state_reached`（blocked_authoring）の存在だけを見ており
    stale ノートの有無を区別しないため、この回帰を検出できなかった —
    ここでは note に `"stale"` が **含まれない** ことを明示的に検証する。"""
    project_path = _copy_demo_project(tmp_path)
    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text("not-a-mapping\n", encoding="utf-8")

    plan_result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert plan_result.exit_code == 1
    plan_path = project_path.parent / "recast_plan.json"
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "blocked_authoring"

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0, status_result.output
    assert "blocked_authoring" in status_result.output
    assert "stale" not in status_result.output


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


def test_recast_plan_blocks_capability_on_corrupted_device_profile(
    tmp_path: Path, monkeypatch
) -> None:
    """device profile（`config/device_profiles/<generator>.yaml`）の YAML 破損・
    schema 不正も capability_profile / mode_overrides と同じ blocked_capability
    として recast_plan.json に publish + state 記録されることを検証する
    （Codex P2 tenth round #207: 以前は保存済み例外を re-raise しており、CLI が
    top-level Error で落ちて plan/state が一切書かれなかった — eighth round で
    対応した capability_profile/mode_overrides と同クラスの非一貫だった）。

    実リポジトリの `config/device_profiles/suno.yaml` は変更せず、
    `svp_rpe.recast.plan.resolve_config_bytes` を monkeypatch で差し替え、
    schema 不正な bytes を返すスタブにする（`test_recast_status_reports_stale_
    after_device_profile_changes` と同じ手法）。"""
    import svp_rpe.recast.plan as recast_plan_module

    project_path = _copy_demo_project(tmp_path)

    def _fake_resolve_config_bytes(name: str) -> bytes | None:
        if name == "device_profiles/suno":
            return b"not-a-mapping\n"
        return None

    monkeypatch.setattr(recast_plan_module, "resolve_config_bytes", _fake_resolve_config_bytes)

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


def test_recast_plan_blocks_capability_on_mode_overrides_generator_mismatch(
    tmp_path: Path,
) -> None:
    """mode_overrides.generator と capability profile.generator の不一致も
    blocked_capability として recast_plan.json に publish + state 記録される
    ことを検証する（Codex P2 twelfth round #207: eighth round で一度スコープ外に
    した箇所の再指摘 — 以前は raise していたため CLI が top-level Error で落ち、
    plan/state が一切書かれなかった）。

    backend の capability_profile を `"suno"` から `"musicgen"`（packaged
    config/capability_profiles/musicgen.yaml, generator=musicgen）へ差し替え、
    mode_overrides はデモ既定の `"suno"`（config/mode_overrides/suno.yaml,
    generator=suno）のまま残すことで両者を不一致にする。"""
    project_path = _copy_demo_project(tmp_path)
    project_text = project_path.read_text(encoding="utf-8")
    mutated = project_text.replace(
        'capability_profile: "suno"', 'capability_profile: "musicgen"'
    )
    assert mutated != project_text  # sanity: the replacement actually matched
    project_path.write_text(mutated, encoding="utf-8")

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.is_file()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "blocked_capability"
    reasons = " ".join(data["blocked"]["reasons"])
    assert "musicgen" in reasons
    assert "suno" in reasons

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
    共用するため新規 arrangement は不要）。"""
    text = project_path.read_text(encoding="utf-8")
    mutated = text.replace(
        '    mode_overrides: "suno"\n\npolicy:',
        '    mode_overrides: "suno"\n'
        "  suno_cover:\n"
        '    capability_profile: "suno"\n'
        "    invocation: manual\n"
        "    invocation_mode: cover\n"
        '    mode_overrides: "suno"\n'
        "\npolicy:",
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
