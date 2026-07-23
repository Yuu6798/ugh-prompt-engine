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

from svp_rpe.arrange.observe import observe_generated_artifact
from svp_rpe.cli import app
from svp_rpe.recast import load_recast_project
from svp_rpe.recast.backend import resolve_invoker, run_context_from_plan_artifacts
from svp_rpe.recast.plan import build_recast_plan_artifacts
from svp_rpe.recast.run_paths import resolve_packages_dir
from svp_rpe.recast.state import load_recast_state
from svp_rpe.rpe.models import ChordEvent, PhysicalRPE, RPEBundle, SectionMarker, SpectralProfile
from svp_rpe.rpe.semantic_rules import generate_semantic

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
_OBSERVATION_ENABLED_HARMONY_ONLY_BLOCK = "observation:\n  enabled: true\n  anchors: [harmony]\n"


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
    # `profile` は plan 段（single-read 束）の `artifacts.profile` をそのまま
    # 使う — `load_backend_capability_profile` での再 read は行わない（Codex
    # P2 review round 12, PR3 #208 指摘24。`tests/test_recast_run_cli.py`
    # 505 行目付近と同じ precedent）。
    ctx = run_context_from_plan_artifacts(
        loaded, variant="deterministic_e2e", backend="deterministic", artifacts=artifacts
    )
    invoker = resolve_invoker(artifacts.backend_ref, ctx.profile)
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
def test_ingest_observe_report_e2e_narrows_to_declared_observation_anchors(
    tmp_path: Path,
) -> None:
    """PR6: `observation.anchors: [harmony]` の project は `recast_report.json`
    を harmony のみへ絞り込む — `structure`（e2e_project の manifest が持つ
    もう一方の anchor）は report に現れず、coverage もその 1 anchor 分のみ集計
    される（`test_ingest_observe_report_e2e_reaches_reported` の全 anchor 経路
    との対照）。"""
    project_path = _copy_e2e_project(tmp_path, label="anchors_subset")
    text = project_path.read_text(encoding="utf-8")
    assert _BACKENDS_DETERMINISTIC_BLOCK in text  # sanity: fixture との drift 検出
    text = text.replace(
        _BACKENDS_DETERMINISTIC_BLOCK, _BACKENDS_WITH_MANUAL_DETERMINISTIC_BLOCK, 1
    )
    assert _OBSERVATION_DISABLED_BLOCK in text  # sanity: fixture との drift 検出
    text = text.replace(_OBSERVATION_DISABLED_BLOCK, _OBSERVATION_ENABLED_HARMONY_ONLY_BLOCK, 1)
    project_path.write_text(text, encoding="utf-8")

    audio_path, _ = _synthesize_deterministic_take(project_path)
    reports_dir, _ = _run_manual_ingest(project_path, audio_path)

    report = json.loads((reports_dir / "recast_report.json").read_text(encoding="utf-8"))
    assert [a["anchor_id"] for a in report["anchors"]] == ["harmony"]
    assert report["coverage"] == {"verified": 0, "violated": 0, "not_observed": 1}

    summary = (reports_dir / "recast_summary.md").read_text(encoding="utf-8")
    assert "harmony" in summary
    assert "structure" not in summary


@pytest.mark.slow
def test_recast_status_stale_observation_then_reobserve_refreshes_report(
    tmp_path: Path,
) -> None:
    """Codex P2 review（PR #212 指摘・plan.py:807）の受け入れ条件:

    ① `reported` 到達後に `observation.anchors` を編集すると、
       `recast status` は該当 run を stale（observation 設定が report 生成時
       から変更）と表示し、`reported` の通常の次の一手（summary 確認案内）は
       出さない — `inputs_digest` は observation 節を意図的に除外している
       ため無変化のままだが、report にとって observation 節は直接の入力
       であるため、この digest 抜きでは stale が検出できない。
    ② 同一 take（`--audio` に既存の `take-01.wav` を再指定）で `recast
       ingest` を再実行すると、take を再 collect せずに observe→report を
       やり直し、`reported` へ復帰する（新しい report は絞り込み後の
       anchors のみを反映する）。
    ③ `observation` 設定を変更しなければ、`recast status` は従来どおり
       fresh（stale 表示なし）のままである。
    """
    project_path = _copy_e2e_project(tmp_path, label="reobserve")
    _add_manual_deterministic_backend_and_enable_observation(project_path)

    audio_path, audio_sha256 = _synthesize_deterministic_take(project_path)
    reports_dir, _ = _run_manual_ingest(project_path, audio_path)

    run_key = "deterministic_e2e@deterministic_manual"
    state_after_first_report = load_recast_state(project_path.parent)
    assert state_after_first_report.runs[run_key].state == "reported"
    observation_digest_before = state_after_first_report.runs[run_key].observation_digest
    assert observation_digest_before is not None

    # ③ observation 設定を変更していない時点では fresh のまま。
    status_before = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_before.exit_code == 0, status_before.output
    assert "stale" not in status_before.output

    # observation.anchors を [] → [harmony] へ変更する（report にとっての
    # 入力そのものの変更 — inputs_digest は不変のまま）。
    project_text = project_path.read_text(encoding="utf-8")
    assert _OBSERVATION_ENABLED_BLOCK in project_text  # sanity
    project_text = project_text.replace(
        _OBSERVATION_ENABLED_BLOCK, _OBSERVATION_ENABLED_HARMONY_ONLY_BLOCK, 1
    )
    project_path.write_text(project_text, encoding="utf-8")

    # ① stale 表示 + summary 案内なし。
    status_after_edit = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_after_edit.exit_code == 0, status_after_edit.output
    assert "stale" in status_after_edit.output
    assert "再観測" in status_after_edit.output
    assert "recast_summary.md を確認" not in status_after_edit.output

    # ② 同一 take（--audio に既存 take-01.wav をそのまま再指定）で再観測。
    takes_dir = project_path.parent / "builds" / "takes" / run_key
    existing_take_path = takes_dir / "take-01.wav"
    assert existing_take_path.is_file()
    reobserve_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "deterministic_e2e", "--backend", "deterministic_manual",
            "--audio", str(existing_take_path),
        ],
    )
    assert reobserve_result.exit_code == 0, reobserve_result.output
    assert "Re-observing existing take" in reobserve_result.output

    state_after_reobserve = load_recast_state(project_path.parent)
    run_after_reobserve = state_after_reobserve.runs[run_key]
    assert run_after_reobserve.state == "reported"
    assert run_after_reobserve.observation_digest is not None
    assert run_after_reobserve.observation_digest != observation_digest_before

    # 同じ take（sha256 不変・再 collect されていない）のまま report だけが
    # 絞り込み後の観測設定を反映する。
    refreshed_report = json.loads((reports_dir / "recast_report.json").read_text(encoding="utf-8"))
    assert refreshed_report["take"]["sha256"] == audio_sha256
    assert [a["anchor_id"] for a in refreshed_report["anchors"]] == ["harmony"]
    assert refreshed_report["coverage"] == {"verified": 0, "violated": 0, "not_observed": 1}

    # ③ 再観測後は fresh に戻る。
    status_after_reobserve = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_after_reobserve.exit_code == 0, status_after_reobserve.output
    assert "stale" not in status_after_reobserve.output


@pytest.mark.slow
def test_recast_ingest_reobserve_rejects_audio_that_does_not_match_existing_take(
    tmp_path: Path,
) -> None:
    """再観測経路（`is_reobserve`）は「同一 take」に対してのみ許される —
    `--audio` に別音源を渡した場合は take を差し替えず `RecastError` で拒否
    する（`_load_existing_take_for_reobserve` の fail-closed 契約）。"""
    project_path = _copy_e2e_project(tmp_path, label="reobserve_mismatch")
    _add_manual_deterministic_backend_and_enable_observation(project_path)

    audio_path, _ = _synthesize_deterministic_take(project_path)
    _run_manual_ingest(project_path, audio_path)

    different_audio = project_path.parent / "different-take.wav"
    different_audio.write_bytes(b"RIFF....WAVEnot-the-same-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "deterministic_e2e", "--backend", "deterministic_manual",
            "--audio", str(different_audio),
        ],
    )

    assert result.exit_code == 1
    assert "does not match the already-collected take" in result.output

    # 拒否された場合、state は変更されない（reported のまま）。
    state_file = load_recast_state(project_path.parent)
    assert state_file.runs["deterministic_e2e@deterministic_manual"].state == "reported"


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


# --- anchor_scope (Codex P2, PR #211) -------------------------------------------


def _empty_chord_bundle() -> RPEBundle:
    """`_observe_harmony` が読む `bundle.physical.chord_events` だけを持つ
    最小 stub（一致は問わない — このテストの関心は scope 絞り込みそのもの
    であり harmony の一致判定精度ではない）。"""
    physical = PhysicalRPE(
        bpm=120.0,
        bpm_confidence=0.9,
        key="C",
        mode="minor",
        key_confidence=0.9,
        duration_sec=8.0,
        sample_rate=44100,
        time_signature="4/4",
        time_signature_confidence=0.9,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=8.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.73,
        valley_depth=0.18,
        thickness=1.0,
        spectral_centroid=900.0,
        spectral_profile=SpectralProfile(
            centroid=900.0, low_ratio=0.4, mid_ratio=0.5, high_ratio=0.1, brightness=0.1,
        ),
        stereo_profile=None,
        onset_density=2.0,
        chord_events=[
            ChordEvent(
                chord="C minor", root="C", quality="minor",
                start_sec=0.0, end_sec=1.0, confidence=0.9,
            )
        ],
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file="fixture.wav",
        audio_duration_sec=8.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


def test_observe_generated_artifact_anchor_scope_skips_excluded_anchors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2（#211）: `anchor_scope={"harmony"}` の場合、`demo_project`
    （lyrics/melody/harmony の 3 anchor）で除外 anchor（lyrics/melody）の
    artifact を破損させても観測は落ちない（① 事前検証ゼロ）。learned 系
    provider（`_transcribe_lyrics_for_observation`/`_extract_melody_for_
    observation`）は一切呼ばれない（② monkeypatch カウンタ = 呼ばれたら
    AssertionError）。結果の report/ObservationReport は harmony のみを
    収載する（③）。"""
    import svp_rpe.arrange.observe as observe_module

    project_path = _copy_demo_project(tmp_path, label="anchor-scope")

    # まず正常な artifact のまま plan/publish する（`recast plan` 自体は
    # anchor_scope 非対応で manifest 全体をコンパイルするため、ここでは
    # 破損させない — 破損させるのは publish 後、観測直前）。
    plan_result = runner.invoke(
        app,
        [
            "recast", "plan", str(project_path),
            "--variant", "edm", "--backend", "suno",
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output

    # ① 除外 anchor（lyrics/melody）の artifact を publish 後に破損させる —
    # 観測（anchor_scope 適用後）がこれを一切事前検証しないことの対象。
    (project_path.parent / "identity" / "lyrics.txt").write_bytes(b"\x00corrupted-lyrics")
    (project_path.parent / "identity" / "melody_notes.json").write_bytes(
        b"{not-valid-json-at-all"
    )

    loaded = load_recast_project(project_path)
    package_path = resolve_packages_dir(loaded, "edm", "suno") / "performance_package.json"
    assert package_path.is_file()

    audio_path = tmp_path / "fake-take.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes-for-anchor-scope-test")

    def _fail_lyrics(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "_transcribe_lyrics_for_observation must not be called when lyrics "
            "is outside anchor_scope"
        )

    def _fail_melody(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "_extract_melody_for_observation must not be called when melody "
            "is outside anchor_scope"
        )

    monkeypatch.setattr(
        observe_module, "extract_rpe_from_file", lambda *_a, **_k: _empty_chord_bundle()
    )
    monkeypatch.setattr(observe_module, "_transcribe_lyrics_for_observation", _fail_lyrics)
    monkeypatch.setattr(observe_module, "_extract_melody_for_observation", _fail_melody)

    observation = observe_generated_artifact(
        package_path=package_path,
        manifest_path=loaded.identity_manifest_path,
        audio_path=audio_path,
        anchor_scope={"harmony"},
    )

    # ③ report は harmony のみ（lyrics/melody は事前フィルタで manifest から
    # 消える — anchor_id set 自体に現れない）。
    anchor_ids = {anchor.anchor_id for anchor in observation.report.anchors}
    assert anchor_ids == {"harmony"}
    assert observation.report.anchors[0].sensor.available is True


def test_observe_generated_artifact_rejects_unknown_anchor_scope_id(tmp_path: Path) -> None:
    """Codex P2（#210 round 9 指摘11）: `anchor_scope` に typo/削除済み id
    （manifest に存在しない id）が含まれる場合、フィルタへそのまま通すと
    単に「該当なし」として静かに消え、ゼロ anchor の report が組み立て可能に
    なってしまう。`observe_generated_artifact` はフィルタ適用前に
    `anchor_scope ⊆ manifest anchor id 集合` を検証し、外れている id を
    列挙した `ValueError` で拒否する（report は一切構築されない）。"""
    project_path = _copy_demo_project(tmp_path, label="unknown-scope")

    plan_result = runner.invoke(
        app,
        [
            "recast", "plan", str(project_path),
            "--variant", "edm", "--backend", "suno",
        ],
    )
    assert plan_result.exit_code == 0, plan_result.output

    loaded = load_recast_project(project_path)
    package_path = resolve_packages_dir(loaded, "edm", "suno") / "performance_package.json"
    audio_path = tmp_path / "fake-take-unknown-scope.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes-for-unknown-scope-test")

    with pytest.raises(ValueError, match="harmnoy"):
        observe_generated_artifact(
            package_path=package_path,
            manifest_path=loaded.identity_manifest_path,
            audio_path=audio_path,
            # "harmnoy" は実在する "harmony" の typo — manifest 側に存在しない。
            anchor_scope={"harmnoy"},
        )


def test_ingest_rejects_unknown_observation_anchor_id_as_config_error_not_observation_incomplete(
    tmp_path: Path,
) -> None:
    """Codex P2（#210 round 9 指摘11、round 10 指摘13 でチェック位置を前倒し）
    の CLI 経路: `project.yaml` の `observation.anchors` に typo/削除済み id
    が混入している場合、`recast ingest` は既存の `observation_incomplete`
    （観測実行時の実測失敗用の state）を記録しないだけでなく、`generated`
    （take の collect）へも一切進まない — スコープ検証を他の precheck 群
    （inputs_digest/plan_sha256/orders_digest）と同じ「collect・publish・
    state 記録より前」の位置へ移動したため、typo な設定のまま ingest を
    叩いても state は `awaiting_generation` のまま変化せず、plain な設定
    エラーとして exit 1 する（typo 修正後、同じ take でそのまま ingest を
    やり直せる — 旧位置だと `generated` が先に確定してしまい再 ingest
    できなかった）。`recast_report.json`/`recast_summary.md` は一切書かれ
    ない。"""
    project_path = _copy_demo_project(tmp_path, label="unknown-anchor-cli")
    text = project_path.read_text(encoding="utf-8")
    text = text.replace(_OBSERVATION_DISABLED_BLOCK, _OBSERVATION_ENABLED_BLOCK, 1)
    # "harmnoy" は実在する "harmony" の typo — manifest（lyrics/melody/harmony
    # の3 anchor）には存在しない。
    text = text.replace(
        "observation:\n  enabled: true\n  anchors: []\n",
        'observation:\n  enabled: true\n  anchors: ["harmnoy"]\n',
        1,
    )
    project_path.write_text(text, encoding="utf-8")

    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    ingest_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "edm", "--backend", "suno",
            "--audio", str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 1, ingest_result.output
    assert "harmnoy" in ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    # スコープ検証は collect/publish/state 記録より前で走るため、typo な
    # 設定のまま ingest を叩いても take の collect 自体に到達せず、state は
    # `recast run` が記録した `awaiting_generation` のまま変化しない。
    assert run.state == "awaiting_generation"

    reports_dir = project_path.parent / "builds" / "reports" / "edm@suno"
    assert not reports_dir.exists()

    # typo 修正後、同じ awaiting_generation run に対してそのまま ingest を
    # やり直せる（旧位置だと `generated` が先に確定し、orders_digest
    # precheck が次回 ingest を弾いていた）— 少なくとも「設定 typo による
    # 拒否」を二度と踏まないことを、typo 修正後の 2 回目呼び出しがもう
    # unknown anchor id のエラーメッセージを出さないことで確認する（実
    # 抽出の成否はこのテストの関心事ではないため、状態機械のゲートを
    # 通過できたかどうかのみを見る）。
    text = project_path.read_text(encoding="utf-8")
    text = text.replace(
        'observation:\n  enabled: true\n  anchors: ["harmnoy"]\n',
        'observation:\n  enabled: true\n  anchors: ["harmony"]\n',
        1,
    )
    project_path.write_text(text, encoding="utf-8")
    retry_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "edm", "--backend", "suno",
            "--audio", str(audio_path),
        ],
    )
    assert "not present in the identity manifest" not in retry_result.output


def test_ingest_ignores_stale_observation_anchors_when_observation_disabled(
    tmp_path: Path,
) -> None:
    """Codex P2（#211 追加指摘）: `observation.enabled: false` の project は
    元々 collect→`generated` で停止する契約（observe 自体に進まない）ため、
    `observation.anchors` に typo/削除済み id が残っていても無関係のはず
    だが、unknown-anchor precheck（round 9/10）が `observation.enabled` を
    見ずに無条件で先走っていたため、observation が無効なのに「anchor id が
    manifest に存在しない」という誤ったエラーで collect 前に拒否していた。
    `observation.enabled: false` + typo な `observation.anchors` の project
    で ingest が正常に collect 成功・`generated` で停止し、observe/report
    には一切進まないことを検証する。"""
    project_path = _copy_demo_project(tmp_path, label="disabled-typo-anchors")
    text = project_path.read_text(encoding="utf-8")
    # "harmnoy" は実在する "harmony" の typo — manifest には存在しない。
    # observation.enabled は false のまま（デフォルト値を変更しない）。
    text = text.replace(
        _OBSERVATION_DISABLED_BLOCK,
        'observation:\n  enabled: false\n  anchors: ["harmnoy"]\n',
        1,
    )
    project_path.write_text(text, encoding="utf-8")

    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    ingest_result = runner.invoke(
        app,
        [
            "recast", "ingest", str(project_path),
            "--variant", "edm", "--backend", "suno",
            "--audio", str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 0, ingest_result.output
    assert "not present in the identity manifest" not in ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "generated"

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
