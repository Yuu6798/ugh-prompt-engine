"""`svprpe recast ingest` の observe→report 拡張（PR5）の CLI/E2E テスト。

`examples/recast/demo_project` は `observation.enabled: false`（PR3/PR4 の
既存 ingest テストが `generated` で止まる前提のまま — 変更しない）ので、
observe→report 経路の E2E は harmony+structure 縦切り専用の
`examples/recast/e2e_project`（PR4 #209 round 2 で分離された committed
project。`deterministic_e2e` variant + `deterministic` backend のみ、
manifest は harmony+structure の 2 anchor のみで learned adapter
非依存）を使う。そのコピーへその場で manual backend
(`deterministic_manual`) を追加 + `observation.enabled: true` へ patch する
（`tests/test_recast_run_cli.py:_add_deterministic_variant` と同じ手法）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.recast import load_recast_project
from svp_rpe.recast.backend import (
    load_backend_capability_profile,
    resolve_invoker,
    run_context_from_plan_artifacts,
)
from svp_rpe.recast.plan import build_recast_plan_artifacts
from svp_rpe.recast.state import load_recast_state

DEMO_PROJECT = Path("examples/recast/demo_project")
E2E_PROJECT = Path("examples/recast/e2e_project")

runner = CliRunner()

_BACKENDS_DETERMINISTIC_BLOCK = (
    '  deterministic:\n'
    '    capability_profile: "deterministic"\n'
    '    invocation: local\n'
    '    invocation_mode: prompt_only\n'
    '\n'
    'policy:\n'
)
_BACKENDS_WITH_MANUAL_DETERMINISTIC_BLOCK = (
    '  deterministic:\n'
    '    capability_profile: "deterministic"\n'
    '    invocation: local\n'
    '    invocation_mode: prompt_only\n'
    '  deterministic_manual:\n'
    '    capability_profile: "deterministic"\n'
    '    invocation: manual\n'
    '    invocation_mode: prompt_only\n'
    '\n'
    'policy:\n'
)

_OBSERVATION_DISABLED_BLOCK = "observation:\n  enabled: false\n  anchors: []\n"
_OBSERVATION_ENABLED_BLOCK = "observation:\n  enabled: true\n  anchors: []\n"


def _copy_demo_project(tmp_path: Path, *, label: str) -> Path:
    dest = tmp_path / f"demo_project_{label}"
    dest.mkdir()
    shutil.copy(DEMO_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(DEMO_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(DEMO_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(DEMO_PROJECT / "identity", dest / "identity")
    shutil.copytree(DEMO_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def _copy_e2e_project(tmp_path: Path, *, label: str) -> Path:
    dest = tmp_path / f"e2e_project_{label}"
    dest.mkdir()
    shutil.copy(E2E_PROJECT / "project.yaml", dest / "project.yaml")
    shutil.copy(E2E_PROJECT / "composition_score.yaml", dest / "composition_score.yaml")
    shutil.copy(E2E_PROJECT / "identity.yaml", dest / "identity.yaml")
    shutil.copytree(E2E_PROJECT / "identity", dest / "identity")
    shutil.copytree(E2E_PROJECT / "arrangements", dest / "arrangements")
    return dest / "project.yaml"


def _add_manual_deterministic_backend_and_enable_observation(project_path: Path) -> None:
    text = project_path.read_text(encoding="utf-8")
    assert _BACKENDS_DETERMINISTIC_BLOCK in text  # sanity: fixture との drift 検出
    text = text.replace(
        _BACKENDS_DETERMINISTIC_BLOCK, _BACKENDS_WITH_MANUAL_DETERMINISTIC_BLOCK, 1
    )
    assert "deterministic_manual:" in text  # sanity

    assert _OBSERVATION_DISABLED_BLOCK in text  # sanity: fixture との drift 検出
    text = text.replace(_OBSERVATION_DISABLED_BLOCK, _OBSERVATION_ENABLED_BLOCK, 1)
    assert _OBSERVATION_ENABLED_BLOCK in text  # sanity

    project_path.write_text(text, encoding="utf-8")


def _synthesize_deterministic_take(project_path: Path) -> tuple[Path, str]:
    """`deterministic_e2e@deterministic`（local invocation）を API 経由で
    invoke し、"外部生成の代役" 音声を合成する。CLI の `recast run` を使わない
    理由は `tests/test_recast_run_cli.py:test_e2e_manual_awaiting_generation_then_ingest_cli_reaches_generated`
    と同じ: `recast_plan.json` はプロジェクト単位の単一ファイルのため、CLI 経由で
    2 つ目の backend の plan を走らせると 1 つ目（`deterministic_manual`）の
    `plan_sha256` を stale にしてしまう（`recast_plan.json` 自体は CLI の
    `_publish_recast_plan` だけが書く別の公開サイトであり、ここで渡す
    `publish=True`（Codex P2, PR3 #208 指摘19 後の必須引数）は
    `builds/packages/<variant>@<backend>/` への package 公開のみを指す —
    同 helper が使うのと同じ precedent は
    `tests/test_recast_run_cli.py` 496 行目参照）。"""
    loaded = load_recast_project(project_path)
    artifacts = build_recast_plan_artifacts(
        loaded, variant="deterministic_e2e", backend="deterministic", publish=True
    )
    assert artifacts.result.plan.state_reached == "verified", artifacts.result.text
    profile = load_backend_capability_profile(loaded, "deterministic")
    ctx = run_context_from_plan_artifacts(
        loaded, variant="deterministic_e2e", backend="deterministic",
        artifacts=artifacts, profile=profile,
    )
    invoker = resolve_invoker(artifacts.backend_ref, profile)
    prepared = invoker.prepare(ctx)
    take = invoker.invoke(prepared)
    return take.audio_path, take.sha256


def _run_manual_ingest(
    project_path: Path, audio_path: Path
) -> tuple[Path, dict[str, Any]]:
    """`deterministic_e2e@deterministic_manual`: plan → run (awaiting_generation)
    → ingest (`--audio audio_path`) を CLI 経由で実行し、
    (reports_dir, ingest CliRunner result info) を返す。"""
    plan_result = runner.invoke(
        app,
        [
            "recast", "plan", str(project_path),
            "--variant", "deterministic_e2e", "--backend", "deterministic_manual",
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output
    assert "State reached: verified" in plan_result.output

    run_result = runner.invoke(
        app,
        [
            "recast", "run", str(project_path),
            "--variant", "deterministic_e2e", "--backend", "deterministic_manual",
        ],
    )
    assert run_result.exit_code == 0, run_result.output
    state_after_run = load_recast_state(project_path.parent)
    assert (
        state_after_run.runs["deterministic_e2e@deterministic_manual"].state
        == "awaiting_generation"
    )

    ingest_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "deterministic_e2e", "--backend", "deterministic_manual",
            "--audio", str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 0, ingest_result.output

    reports_dir = (
        project_path.parent / "builds" / "reports" / "deterministic_e2e@deterministic_manual"
    )
    return reports_dir, {"output": ingest_result.output}


@pytest.mark.slow
def test_ingest_observe_report_e2e_reaches_reported(tmp_path: Path) -> None:
    project_path = _copy_e2e_project(tmp_path, label="a")
    _add_manual_deterministic_backend_and_enable_observation(project_path)

    audio_path, audio_sha256 = _synthesize_deterministic_take(project_path)
    reports_dir, ingest_info = _run_manual_ingest(project_path, audio_path)

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["deterministic_e2e@deterministic_manual"]
    assert run.state == "reported"

    report_path = reports_dir / "recast_report.json"
    summary_path = reports_dir / "recast_summary.md"
    assert report_path.is_file()
    assert summary_path.is_file()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "recast-report/0.1"
    assert report["work_id"] == "midnight-signal"
    assert report["variant"] == "deterministic_e2e"
    assert report["backend"] == "deterministic_manual"
    assert report["take"]["path"] == (
        "builds/takes/deterministic_e2e@deterministic_manual/take-01.wav"
    )
    assert report["take"]["sha256"] == audio_sha256
    assert report["identity_assessment"] == {"enabled": False}

    # 同じ variant（deterministic_e2e）・同じ capability_profile（"deterministic"）
    # を使う plan は manual/local いずれの backend でも同一 compile 結果になる
    # ため、PR4 E2E（tests/test_recast_e2e_chords_structure.py, e2e_project 経由）
    # が実測 pin した harmony/structure の観測結果と一致する — ここでは coverage
    # 集計のみを独立に実測 pin する（e2e_project の manifest は harmony+structure
    # の 2 anchor のみ: いずれも exact match 不成立の deferred = not_observed）。
    assert report["coverage"] == {"verified": 0, "violated": 0, "not_observed": 2}

    anchors_by_id = {a["anchor_id"]: a for a in report["anchors"]}
    assert set(anchors_by_id) == {"harmony", "structure"}
    assert anchors_by_id["harmony"]["policy_mode"] == "hard"
    assert anchors_by_id["harmony"]["coverage"] == "not_observed"
    assert anchors_by_id["harmony"]["determination"] == "deferred"
    assert anchors_by_id["structure"]["policy_mode"] == "hard"
    assert anchors_by_id["structure"]["coverage"] == "not_observed"
    assert anchors_by_id["structure"]["determination"] == "deferred"

    package_path = (
        project_path.parent / "builds" / "packages" / "deterministic_e2e@deterministic_manual"
        / "performance_package.json"
    )
    package_sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()
    assert report["package_sha256"] == package_sha256

    summary = summary_path.read_text(encoding="utf-8")
    assert "harmony" in summary
    assert "structure" in summary
    assert "enabled: false" in summary  # identity_assessment: no single score
    assert "svprpe recast report" not in ingest_info["output"]


@pytest.mark.slow
def test_ingest_observe_report_e2e_is_byte_identical_across_reruns(tmp_path: Path) -> None:
    """report/summary の決定論: 独立したプロジェクトコピーで同じ操作をもう一度
    実行し、`recast_report.json`/`recast_summary.md` が byte-for-byte 一致する
    ことを確認する（タイムスタンプ・絶対パスを含まない契約の実測確認）。"""
    project_path_a = _copy_e2e_project(tmp_path, label="rerun_a")
    _add_manual_deterministic_backend_and_enable_observation(project_path_a)
    audio_a, _ = _synthesize_deterministic_take(project_path_a)
    reports_dir_a, _ = _run_manual_ingest(project_path_a, audio_a)

    project_path_b = _copy_e2e_project(tmp_path, label="rerun_b")
    _add_manual_deterministic_backend_and_enable_observation(project_path_b)
    audio_b, _ = _synthesize_deterministic_take(project_path_b)
    reports_dir_b, _ = _run_manual_ingest(project_path_b, audio_b)

    report_a = (reports_dir_a / "recast_report.json").read_bytes()
    report_b = (reports_dir_b / "recast_report.json").read_bytes()
    assert report_a == report_b

    summary_a = (reports_dir_a / "recast_summary.md").read_bytes()
    summary_b = (reports_dir_b / "recast_summary.md").read_bytes()
    assert summary_a == summary_b


# --- observation_incomplete (fast: no real audio decode) -----------------------


def test_ingest_records_observation_incomplete_and_leaves_no_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """観測（`observe_generated_artifact`）が例外で落ちた場合、
    `observation_incomplete` を記録して exit 1 する。`recast_report.json`/
    `recast_summary.md` は一切書かれない（部分成果物なし — `reports/` ディレクトリ
    自体が作られないことまで検証する）。`edm@suno`（demo_project）は
    `observation.enabled: false` のままだと observe 段に到達しないため、この
    テスト専用に `observation.enabled: true` へ patch した独立コピーを使う。"""
    project_path = _copy_demo_project(tmp_path, label="fail")
    text = project_path.read_text(encoding="utf-8")
    text = text.replace(_OBSERVATION_DISABLED_BLOCK, _OBSERVATION_ENABLED_BLOCK, 1)
    project_path.write_text(text, encoding="utf-8")

    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    def _boom(**_kwargs: Any) -> Any:
        raise ValueError("synthetic observation failure (test double)")

    monkeypatch.setattr("svp_rpe.arrange.observe.observe_generated_artifact", _boom)

    ingest_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "edm", "--backend", "suno",
            "--audio", str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 1, ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "observation_incomplete"

    reports_dir = project_path.parent / "builds" / "reports" / "edm@suno"
    assert not reports_dir.exists()


def test_ingest_records_observation_incomplete_when_take_changes_between_collect_and_observe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2（#210 round 2 指摘2）: `collect()` が確定させた take の
    sha256（`GeneratedTake.sha256`）と、`observe_generated_artifact` が
    `audio_path` を読んだ直後に計算する sha256 が食い違う場合（collect
    完了〜観測の読み出しの間に take ファイルが差し替わった場合の実測代替）、
    report は一切構築・公開されず `observation_incomplete` を記録して exit 1
    する — 「観測していない take を collect 時 hash で証明する」report が
    出回らないことの機械 assert。

    実際の観測関数（`observe_generated_artifact`）をラップし、呼び出し
    直前（＝collect 完了後）に `audio_path` の中身を差し替えてから実装本体へ
    委譲する — 実装本体は自分が読んだ bytes の sha256 と
    `expected_audio_sha256`（collect 時 pin）を突き合わせて fail-closed する
    契約そのものを検証する（スタブで結果だけ模倣しない）。"""
    import svp_rpe.arrange.observe as observe_module

    real_observe_generated_artifact = observe_module.observe_generated_artifact

    project_path = _copy_demo_project(tmp_path, label="race")
    text = project_path.read_text(encoding="utf-8")
    text = text.replace(_OBSERVATION_DISABLED_BLOCK, _OBSERVATION_ENABLED_BLOCK, 1)
    project_path.write_text(text, encoding="utf-8")

    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEoriginal-bytes-collected-here")

    def _racy_observe(**kwargs: Any) -> Any:
        # collect() 完了直後・観測の読み出し直前という想定の位置で take を
        # 差し替える（実運用での同種の race を機械的に再現する）。
        kwargs["audio_path"].write_bytes(b"RIFF....WAVEtampered-bytes-different-length!!")
        return real_observe_generated_artifact(**kwargs)

    monkeypatch.setattr("svp_rpe.arrange.observe.observe_generated_artifact", _racy_observe)

    ingest_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "edm", "--backend", "suno",
            "--audio", str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 1, ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "observation_incomplete"
    assert run.note is not None and "changed since collection" in run.note

    reports_dir = project_path.parent / "builds" / "reports" / "edm@suno"
    assert not reports_dir.exists()


def test_ingest_records_observation_incomplete_when_package_changes_after_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2（#210 round 3 指摘5）: `observe_generated_artifact` が
    disk 上の `performance_package.json` を再 read するだけでは、publish
    直後〜観測の読み出しの間に **自己整合な別内容**（D-3 の内部整合チェック
    だけなら通過し得る、別 sha256 の package）へ差し替えられていても検出
    できない。`expected_package_sha256`（rebuild で確定した
    `artifacts.compiled.report.package_sha256`）との突合で、そのケースも
    観測・report 記録の前に `observation_incomplete`+exit 1 する。

    「自己整合な別内容」の再現: 公開済み package の JSON を同じ内容のまま
    再シリアライズ（indent を変える）して書き戻す — `PerformancePackage`
    としての妥当性・内部整合性は変わらない（schema 検証・D-3 の他チェック
    はすべて通過し得る）が bytes/sha256 だけが変わる、という指摘そのものの
    シナリオ。"""
    import svp_rpe.arrange.observe as observe_module

    real_observe_generated_artifact = observe_module.observe_generated_artifact

    project_path = _copy_demo_project(tmp_path, label="package-race")
    text = project_path.read_text(encoding="utf-8")
    text = text.replace(_OBSERVATION_DISABLED_BLOCK, _OBSERVATION_ENABLED_BLOCK, 1)
    project_path.write_text(text, encoding="utf-8")

    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    def _racy_observe(**kwargs: Any) -> Any:
        # publish 完了直後・観測の読み出し直前という想定の位置で package を
        # 自己整合な別 bytes（同じ内容の re-serialize）へ差し替える。
        package_path: Path = kwargs["package_path"]
        original = json.loads(package_path.read_text(encoding="utf-8"))
        package_path.write_text(json.dumps(original, indent=4), encoding="utf-8")
        return real_observe_generated_artifact(**kwargs)

    monkeypatch.setattr("svp_rpe.arrange.observe.observe_generated_artifact", _racy_observe)

    ingest_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "edm", "--backend", "suno",
            "--audio", str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 1, ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "observation_incomplete"
    assert run.note is not None and "changed since publish" in run.note

    reports_dir = project_path.parent / "builds" / "reports" / "edm@suno"
    assert not reports_dir.exists()
