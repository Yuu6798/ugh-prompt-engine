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
    tmp_path: Path,
) -> None:
    """`performance_package.json` の公開 bytes が `compiled.report` の
    `package_sha256`（hash 計算元と同一の UTF-8 bytes）と一致することを CLI
    E2E で検証する（Codex P2 eleventh round #207: 旧実装は `write_text()` を
    使っており、Windows では改行変換 `"\n"` -> `"\r\n"` によりステージング先の
    実 bytes と `package_sha256` が乖離しうる — `verify_package` はステージング
    先の実 bytes から package_sha256 を再計算して突合するため、乖離があれば
    偽の `blocked_verification` を招く。PR3 の `_atomic_publish_text_bundle`
    は `write_bytes()` に統一済み — `tests/test_recast_backend.py::
    test_published_package_json_bytes_sha256_matches_recorded_package_sha256`
    がライブラリ API 直呼びで同じ性質を検証する一方、本テストは `recast plan`
    CLI 経由の E2E 変種として残す）。

    PR3 では package/report は `<builds_root>/packages/<variant>@<backend>/`
    という永続ディレクトリへ公開される（pr2 の一時 `tempfile.
    TemporaryDirectory` ベースの検証ステージングとは異なる — #208 指摘 3）
    ため、monkeypatch 不要で CLI 呼び出し後にそのまま実ファイルを検査できる。"""
    from svp_rpe.arrange.package import COMPILATION_REPORT_FILENAME, PERFORMANCE_PACKAGE_FILENAME

    project_path = _copy_demo_project(tmp_path)
    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert result.exit_code == 0, result.output

    plan_path = project_path.parent / "recast_plan.json"
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan_data["state_reached"] == "verified"  # require_verified_package 経路を通った

    package_dir = project_path.parent / "builds" / "packages" / "edm@suno"
    package_bytes = (package_dir / PERFORMANCE_PACKAGE_FILENAME).read_bytes()
    report_data = json.loads((package_dir / COMPILATION_REPORT_FILENAME).read_bytes())

    assert hashlib.sha256(package_bytes).hexdigest() == report_data["package_sha256"]


def test_recast_plan_verification_staging_uses_builds_root(
    tmp_path: Path, monkeypatch
) -> None:
    """検証ステージング（`tempfile.TemporaryDirectory`）は project の builds_root
    配下に確保されることを検証する（Codex P2 twelfth round #207: 既定の system
    temp（`dir` 省略時）は project files と別ドライブに配置されうる — Windows で
    システムドライブと project ドライブが異なる場合、直後の `os.path.relpath`
    （project files ↔ staging dir）が同一ドライブ前提のため ValueError を送出し、
    本来 valid な plan を偽の `blocked_capability` にしてしまう。`builds_root` は
    loader が project_dir 配下に confine 済みで project files と常に同一
    ドライブにある）。

    pr2 は thirteenth round #207 指摘21（`build_recast_plan` が builds_root を
    mkdir しており「ディスクへ副作用を持たない純粋関数」契約に反する）で
    staging 先を project_dir へ変更したが、PR3 の `build_recast_plan_artifacts`
    はそもそも「plan JSON の publish/state 記録だけが CLI 側の責務」という
    契約であり、package/report の builds_root への永続公開は本関数自身が
    意図して持つ副作用（本モジュール上部の docstring 参照）。PR3 は pr2 の
    「検証専用の使い捨て staging」ではなく「永続 `packages_dir`
    （`builds_root/packages/<variant>@<backend>/`）＋その配下の
    `_atomic_publish_text_bundle` 内部 staging」という別設計で、pr2 が
    懸念する同ドライブ要件（指摘19）を構造的に満たす（#208 指摘 3）。

    Codex P2 review round 11（PR3 #208 指摘21）: 検証・mode gate を通過する
    前に packages を永続公開しないよう、`tempfile.TemporaryDirectory` の
    呼び出しが 2 段になった — (1) 検証専用 staging（`packages_dir` の**兄弟**
    ディレクトリ、`dir=builds_root/packages`。深さを `packages_dir` と揃える
    ことで `artifact_base_locator` が公開後と同一の値になる）と、(2) 検証・
    mode gate 通過後にのみ実行される `_atomic_publish_text_bundle` 自身の
    内部 staging（`dir=packages_dir`）。`tempfile.TemporaryDirectory` を実
    クラス継承のスタブに monkeypatch し、この 2 回の呼び出しの `dir` kwarg が
    それぞれ意図した場所（builds_root 配下・project files と常に同一
    ドライブ）であることを検証する。公開後の `packages_dir` は "検証専用の
    空 staging" ではなく最終成果物 2 ファイルが永続する場所のため、
    「cleanup 後に空」ではなく「最終ファイル 2 つのみで staging 残骸がない」
    ことを確認する。"""
    import tempfile as tempfile_module

    import svp_rpe.recast.plan as recast_plan_module

    project_path = _copy_demo_project(tmp_path)
    builds_root = project_path.parent / "builds"
    assert not builds_root.exists()  # デモ fixture は builds/ を同梱しない

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

    package_dir = builds_root / "packages" / "edm@suno"
    packages_parent_dir = builds_root / "packages"
    assert len(captured_dir_args) == 2
    # (1) 検証専用 staging: packages_dir の兄弟（packages_dir の親配下）。
    assert Path(str(captured_dir_args[0])).resolve() == packages_parent_dir.resolve()
    # (2) 検証・mode gate 通過後の実 publish: _atomic_publish_text_bundle 自身の
    # 内部 staging（packages_dir そのもの配下）。
    assert Path(str(captured_dir_args[1])).resolve() == package_dir.resolve()
    # builds_root 配下（project_dir に confine 済み）にあるため、project files
    # と常に同一ドライブであることが構造的に保証される。
    assert package_dir.resolve().is_relative_to(builds_root.resolve())

    # 公開後は最終ファイル 2 つのみが残り、staging 残骸（`.prev`/一時ディレクトリ
    # 等）は無い。
    assert sorted(p.name for p in package_dir.iterdir()) == [
        "compilation_report.json",
        "performance_package.json",
    ]

    plan_path = project_path.parent / "recast_plan.json"
    plan_text = plan_path.read_text(encoding="utf-8")
    assert str(builds_root) not in plan_text  # staging パスが永続出力に混入しない


def test_build_recast_plan_does_not_create_builds_root(tmp_path: Path) -> None:
    """`build_recast_plan`（`build_recast_plan_artifacts(..., publish=False)`
    の薄いラッパー）を API 直呼びし、`compiled`/`verified` 到達時にも
    `builds_root` へ一切触れない（ディレクトリすら作られない）ことを検証する
    （Codex P2 review round 10, PR3 #208 指摘19: 読み取り専用を謳う
    `build_recast_plan()` が内部で package/report を永続公開しており、
    pr2 由来の「診断のみの純粋関数」契約に反していた。`publish` を
    `build_recast_plan_artifacts` の必須引数として切り出し、
    `build_recast_plan` は常に `publish=False` で呼ぶよう固定した — 公開を
    伴う経路は `svprpe recast plan`/`run`/`ingest` の 3 CLI コマンドが直接
    `build_recast_plan_artifacts(..., publish=True)` を呼ぶ形に限定する）。
    `policy.require_verified_package` が真の demo project でも、検証用の
    package/report は project_dir 配下の ephemeral tempdir だけに書かれ、
    関数を抜けると跡形も残らない。"""
    from svp_rpe.recast import load_recast_project
    from svp_rpe.recast.plan import build_recast_plan

    project_path = _copy_demo_project(tmp_path)
    loaded = load_recast_project(project_path)
    builds_root = loaded.builds_root
    assert not builds_root.exists()  # デモ fixture は builds/ を同梱しない

    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "verified"
    assert not builds_root.exists()  # build_recast_plan はディスクへ書き込まない


def test_build_recast_plan_writes_nothing_when_verification_not_required(
    tmp_path: Path,
) -> None:
    """`policy.require_verified_package: false` の project では
    `build_recast_plan`（`publish=False`）が検証用の ephemeral tempdir すら
    作らない — 真の意味で一切ディスクへ書き込まない純粋関数であることを
    検証する（Codex P2 review round 10, PR3 #208 指摘19: `require_verified_
    package` が真の場合のみ verify_package 用に ephemeral tempdir へ書く
    設計だが、偽の場合は compile 結果を in-memory のまま使い、
    ディスク書き込みが一切発生しないことも回帰対象に含める）。project_dir
    直下のエントリ集合が呼び出し前後で完全に不変であることを確認する。"""
    from svp_rpe.recast import load_recast_project
    from svp_rpe.recast.plan import build_recast_plan

    project_path = _copy_demo_project(tmp_path)
    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        "require_verified_package: true", "require_verified_package: false"
    )
    assert updated != project_text  # sanity
    project_path.write_text(updated, encoding="utf-8")

    project_dir = project_path.parent
    before_entries = {entry.name for entry in project_dir.iterdir()}

    loaded = load_recast_project(project_path)
    result = build_recast_plan(loaded, variant="edm", backend="suno")

    assert result.plan.state_reached == "compiled"
    after_entries = {entry.name for entry in project_dir.iterdir()}
    assert after_entries == before_entries  # 新規ファイル/ディレクトリが一切増えない


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


def test_recast_plan_publishes_blocked_verification_on_corrupted_identity_manifest(
    tmp_path: Path,
) -> None:
    """Codex P2 review round 7（PR3 #208 指摘 13）: identity manifest（`work.
    identity_manifest`）の YAML 破損は `build_recast_plan_artifacts` の
    single-read 束が既に捕捉し `blocked_verification` として finalize
    済みだが、従来の `_publish_recast_plan` は publish 前ガードとして独立に
    `collect_protected_input_paths` を呼んでおり、これが manifest を**再
    parse**して同じ YAML 破損に遭遇し、`_publish_recast_plan`/`record_state`
    が捕捉しない例外（`RecastError`/`yaml.YAMLError` 等、`(OSError,
    ValueError)` の catch に入らない型）を送出していた — 「blocked でも plan
    は公開される」契約が崩れ、`recast plan` が recast_plan.json/state を
    一切書かずに CLI top-level Error で落ちる回帰があった（本テストはこの
    回帰の再発防止）。`recast plan` が exit 1 で `blocked_verification` を
    正常に publish し、state にも記録されることを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    identity_path = project_path.parent / "identity.yaml"
    identity_path.write_text("not-a-mapping\n", encoding="utf-8")

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output  # 未捕捉例外で top-level Error に落ちていない
    plan_path = project_path.parent / "recast_plan.json"
    assert plan_path.is_file()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["state_reached"] == "blocked_verification"

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0, status_result.output
    assert "blocked_verification" in status_result.output


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


def test_recast_plan_rejects_score_aliased_to_recast_plan_output(tmp_path: Path) -> None:
    """Codex P2 review round 5（PR3 #208 指摘 10）: `work.score`（あるいは
    project.yaml 自体）が `<project_dir>/recast_plan.json` という publish 先と
    同じパスを指す project 構成では、従来 `_publish_recast_plan` が衝突ガード
    無しで publish していたため、最初の `recast plan` が入力（score）を
    plan JSON で上書き破壊し得た。`work.score` を `recast_plan.json` へ
    向け、publish 前に fail-closed で拒否され、かつ元の score 内容が一切
    上書きされないことを検証する。"""
    project_path = _copy_demo_project(tmp_path)
    project_dir = project_path.parent

    # score の内容を recast_plan.json という名前へ移し、work.score をそこへ
    # 向け直す（project.yaml 自体は別ファイルのまま — 衝突対象は score 側）。
    original_score_bytes = (project_dir / "composition_score.yaml").read_bytes()
    aliased_score_path = project_dir / "recast_plan.json"
    aliased_score_path.write_bytes(original_score_bytes)
    (project_dir / "composition_score.yaml").unlink()

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace("score: composition_score.yaml", "score: recast_plan.json", 1)
    assert updated != project_text  # sanity
    project_path.write_text(updated, encoding="utf-8")

    result = runner.invoke(
        app, ["recast", "plan", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 1
    assert "collides with a protected input path" in result.output

    # fail-closed: score の内容（recast_plan.json という名前のファイル）が
    # plan JSON で上書きされていない。
    assert aliased_score_path.read_bytes() == original_score_bytes
    # record_state も呼ばれていない（publish 失敗後は状態を記録しない順序保証）。
    assert not (project_dir / "recast_state.json").exists()


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
