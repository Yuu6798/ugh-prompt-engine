"""`svprpe recast run` CLI テスト（PR3）+ E2E 受け入れ条件（CliRunner + API 併用）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.recast import load_recast_project
from svp_rpe.recast.backend import (
    load_backend_capability_profile,
    resolve_invoker,
    run_context_from_plan_artifacts,
)
from svp_rpe.recast.backends.manual import ManualInvoker
from svp_rpe.recast.plan import build_recast_plan_artifacts
from svp_rpe.recast.state import load_recast_state

DEMO_PROJECT = Path("examples/recast/demo_project")

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


def _add_deterministic_variant(project_path: Path) -> None:
    """`tests/test_recast_backend.py:_add_target_backend_variant` と同じ手法
    （target_backend override 用の専用 variant を追加する。理由は同モジュールの
    docstring / `examples/recast/demo_project/project.yaml` のコメント参照）。"""
    project_dir = project_path.parent
    arrangements_dir = project_dir / "arrangements"
    edm_arrangement = (arrangements_dir / "edm.yaml").read_text(encoding="utf-8")
    overridden = edm_arrangement.replace(
        "target:\n  semantic:",
        'target:\n  rendering:\n    target_backend: "deterministic"\n  semantic:',
        1,
    )
    assert overridden != edm_arrangement  # sanity
    overridden = overridden.replace(
        "preservation:\n  score_fields:\n",
        "preservation:\n  score_fields:\n    rendering.target_backend: free\n",
        1,
    )
    (arrangements_dir / "edm_deterministic.yaml").write_text(overridden, encoding="utf-8")

    project_text = project_path.read_text(encoding="utf-8")
    updated = project_text.replace(
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n",
        "variants:\n  edm:\n    arrangement: arrangements/edm.yaml\n"
        "  edm_deterministic:\n    arrangement: arrangements/edm_deterministic.yaml\n",
        1,
    )
    assert "edm_deterministic:" in updated  # sanity
    project_path.write_text(updated, encoding="utf-8")


# --- recast run: manual (order publication) -------------------------------------


def test_recast_run_manual_publishes_orders_and_records_awaiting_generation(
    tmp_path: Path,
) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert result.exit_code == 0, result.output
    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    for name in (
        "prompt.json",
        "lyrics.txt",
        "section_tags.txt",
        "order_sheet.md",
        "expected_artifacts.json",
        "next_command.txt",
    ):
        assert (order_dir / name).is_file(), name

    state_file = load_recast_state(project_path.parent)
    assert state_file.runs["edm@suno"].state == "awaiting_generation"


def test_recast_run_manual_is_idempotent(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    first = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    second = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    assert (order_dir / "prompt.json").is_file()


# --- recast run: blocked (not compiled) -----------------------------------------


def test_recast_run_exits_nonzero_when_backend_generator_mismatched(tmp_path: Path) -> None:
    """demo fixture の `edm` variant は `rendering.target_backend: "external"` を
    宣言しており、capability_profile 側 generator が "suno" 以外の backend
    （ここでは "deterministic"）と組み合わせると常に blocked_capability に到達する
    （`examples/recast/demo_project/project.yaml` のコメント参照）。`recast run`
    はこれを診断表示した上で exit 1 する。"""
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app,
        ["recast", "run", str(project_path), "--variant", "edm", "--backend", "deterministic"],
    )

    assert result.exit_code == 1
    assert "blocked_capability" in result.output


def test_recast_run_unknown_variant_exits_nonzero(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)

    result = runner.invoke(
        app,
        [
            "recast",
            "run",
            str(project_path),
            "--variant",
            "does-not-exist",
            "--backend",
            "suno",
        ],
    )

    assert result.exit_code == 1


def test_recast_help_lists_run() -> None:
    result = runner.invoke(app, ["recast", "--help"])

    assert result.exit_code == 0
    assert "run" in result.output


# --- E2E acceptance: manual run -> deterministic invoke -> collect -> generated -


@pytest.mark.slow
def test_e2e_manual_awaiting_generation_then_deterministic_collect_reaches_generated(
    tmp_path: Path,
) -> None:
    """受け入れ条件（PR3 指示書）:
    ① manual run（CLI）→ awaiting_generation
    ② deterministic invoke（API）で「外部生成の代役」音声を合成
    ③ ManualInvoker.collect()（API）でその音声を takes へ収蔵
    ④ 状態が generated へ遷移
    """
    project_path = _copy_demo_project(tmp_path)
    _add_deterministic_variant(project_path)

    # ① CLI: manual run against edm@suno -> awaiting_generation.
    result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert result.exit_code == 0, result.output
    state_after_run = load_recast_state(project_path.parent)
    assert state_after_run.runs["edm@suno"].state == "awaiting_generation"

    # Rebuild the same ManualInvoker/PreparedInvocation via the API to get a
    # handle on `order_dir`/`takes_dir` for the collect() step (the CLI process
    # itself doesn't hand these back to the test — determinism of `prepare()`
    # guarantees this reproduces the same paths, verified separately by
    # test_recast_backend.py::test_manual_order_files_are_byte_identical_across_reruns).
    loaded = load_recast_project(project_path)
    suno_artifacts = build_recast_plan_artifacts(loaded, variant="edm", backend="suno")
    suno_profile = load_backend_capability_profile(loaded, "suno")
    suno_ctx = run_context_from_plan_artifacts(
        loaded, variant="edm", backend="suno", artifacts=suno_artifacts, profile=suno_profile
    )
    suno_invoker = resolve_invoker(suno_artifacts.backend_ref, suno_profile)
    assert isinstance(suno_invoker, ManualInvoker)
    suno_prepared = suno_invoker.prepare(suno_ctx)

    # ② API: deterministic invoke synthesizes a stand-in "externally generated" take.
    det_artifacts = build_recast_plan_artifacts(
        loaded, variant="edm_deterministic", backend="deterministic"
    )
    assert det_artifacts.result.plan.state_reached in ("compiled", "verified")
    det_profile = load_backend_capability_profile(loaded, "deterministic")
    det_ctx = run_context_from_plan_artifacts(
        loaded,
        variant="edm_deterministic",
        backend="deterministic",
        artifacts=det_artifacts,
        profile=det_profile,
    )
    det_invoker = resolve_invoker(det_artifacts.backend_ref, det_profile)
    det_prepared = det_invoker.prepare(det_ctx)
    det_take = det_invoker.invoke(det_prepared)
    assert det_take.audio_path.is_file()

    # ③ API: ManualInvoker.collect() ingests that stand-in audio as if delivered
    # externally.
    from svp_rpe.recast.state import record_state

    manual_take = suno_invoker.collect(suno_prepared, det_take.audio_path)
    assert manual_take.source == "manual"
    assert manual_take.audio_path == suno_prepared.takes_dir / "take-01.wav"
    assert manual_take.audio_path.read_bytes() == det_take.audio_path.read_bytes()

    record_state(
        loaded.project_dir,
        "edm",
        "suno",
        "generated",
        note=f"{manual_take.audio_path} sha256={manual_take.sha256}",
    )

    # ④ state transitioned to generated.
    final_state = load_recast_state(loaded.project_dir)
    assert final_state.runs["edm@suno"].state == "generated"

    take_json = json.loads((suno_prepared.takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["source"] == "manual"
    assert take_json["sha256"] == manual_take.sha256
