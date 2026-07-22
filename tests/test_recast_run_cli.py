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


# --- recast ingest ---------------------------------------------------------------


def test_recast_ingest_transitions_awaiting_generation_to_generated(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    # next_command.txt が実在コマンドを案内していることの機械 assert（Codex P2
    # review round 2 指摘 5）: 文字列冒頭が `svprpe recast ingest ` であること
    # の直接検証は tests/test_recast_backend.py 側（実行までの機械 assert）で
    # 行う。ここでは案内された通りの --audio 相対パスへ音声を置いてから
    # ingest する実運用手順そのものを検証する。
    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    next_command = (order_dir / "next_command.txt").read_text(encoding="utf-8").strip()
    assert next_command.startswith("svprpe recast ingest ")

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    ingest_result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )
    assert ingest_result.exit_code == 0, ingest_result.output
    # demo fixture の project.yaml は observation.enabled: false のため、
    # ingest は observe/report 段へ進まず `generated` で止まる（PR5: 有効化
    # されたプロジェクトの ingest→observe→report 経路は
    # tests/test_recast_ingest_report.py が別途検証する）。無効時の次の一手は
    # 実在する `svprpe observe` コマンドへの手動フォールバック案内であり、
    # 存在しない `svprpe recast report` サブコマンドは案内しない。
    assert "svprpe recast report" not in ingest_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "generated"
    assert run.inputs_digest is not None
    assert run.plan_sha256 is not None

    take_json = json.loads((takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["source"] == "manual"


def test_recast_ingest_rejects_when_not_awaiting_generation(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "awaiting_generation" in result.output


def test_recast_ingest_rejects_when_inputs_are_stale(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    score_path = project_path.parent / "composition_score.yaml"
    score_path.write_text(
        score_path.read_text(encoding="utf-8").replace(
            'core: "introspective night drive"', 'core: "changed after awaiting_generation"'
        ),
        encoding="utf-8",
    )
    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "stale" in result.output

    # ingest が拒否された run に対して status も stale 表示し、ingest 案内を
    # 出さない（Codex P2 review round 2 指摘 4 の受け入れ条件）。
    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0
    assert "stale" in status_result.output
    assert "ingest" not in status_result.output


def test_recast_ingest_rejects_when_plan_artifact_is_stale(tmp_path: Path) -> None:
    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    plan_path = project_path.parent / "recast_plan.json"
    plan_path.unlink()  # recast_plan.json 削除（P2 fourth round: 不在も stale 扱い）
    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "plan artifact" in result.output


def test_recast_ingest_rejects_when_pin_is_missing(tmp_path: Path) -> None:
    """Codex P2 review round 4（PR3 #208 指摘 9）: 旧形式/手動コピーされた
    `recast_state.json`（`inputs_digest`/`plan_sha256` が `None`）を「未確認
    だから判定スキップ」で信用してしまうと、実際には変更済みかもしれない
    入力に対して素通りで ingest してしまう。`None` は stale と同扱いにして
    拒否することを検証する（`recast plan`/`recast run` の再実行を促す）。"""
    from svp_rpe.recast.state import record_state

    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    state_file = load_recast_state(project_path.parent)
    run = state_file.runs["edm@suno"]
    assert run.state == "awaiting_generation"
    # pin を落とした state を直接記録する（旧 schema や手動コピーの模倣）。
    record_state(
        project_path.parent,
        "edm",
        "suno",
        run.state,
        note=run.note,
        inputs_digest=None,
        plan_sha256=None,
        protected_inputs=[],
    )

    audio_path = tmp_path / "external.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "no pin" in result.output.lower() or "pin" in result.output.lower()
    assert "stale" in result.output.lower()

    status_result = runner.invoke(app, ["recast", "status", str(project_path)])
    assert status_result.exit_code == 0
    assert "pin" in status_result.output


def test_recast_ingest_rejects_when_inputs_swapped_during_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex P2 eighth round #207 指摘16（recast_cmd.py:555）: `recast ingest`
    の precheck（`compute_recast_inputs_digest` vs 記録済み pin）と、その直後の
    plan rebuild（`build_recast_plan_artifacts`）は別々の入力読み取りであり
    atomic ではない。precheck を通過させた**後**・rebuild が実際に入力を読む
    **直前**に composition_score.yaml を差し替えることで、この窓を再現する
    — rebuild は新しい（差し替え後の）入力から新しい plan/inputs_digest を
    構築してしまうが、その新 digest は記録済み pin（旧入力に対する digest）
    とは一致しないはずで、publish/collect の前に検出・拒否されるべきである。
    `svp_rpe.recast.plan.build_recast_plan_artifacts` をラップして「呼び出し
    直前に score を書き換えてから本物を呼ぶ」スタブに monkeypatch する —
    `recast_ingest_cmd` はこれをローカル `from ... import` で毎回名前解決する
    ため、定義元モジュールの属性を差し替えれば意図した箇所を確実に横取り
    できる。"""
    import svp_rpe.recast.plan as recast_plan_module

    project_path = _copy_demo_project(tmp_path)
    run_result = runner.invoke(
        app, ["recast", "run", str(project_path), "--variant", "edm", "--backend", "suno"]
    )
    assert run_result.exit_code == 0, run_result.output

    project_dir = project_path.parent
    plan_path = project_dir / "recast_plan.json"
    plan_bytes_before = plan_path.read_bytes()
    state_before = load_recast_state(project_dir)
    assert state_before.runs["edm@suno"].state == "awaiting_generation"

    # `recast run` が plan 段（`build_recast_plan_artifacts(..., publish=True)`）
    # で既に公開済みの package（Codex P2, #210 round 3 指摘4 の対象そのもの）。
    package_path = project_dir / "builds" / "packages" / "edm@suno" / "performance_package.json"
    assert package_path.is_file()  # sanity: recast run が公開済み
    package_bytes_before = package_path.read_bytes()

    takes_dir = project_dir / "builds" / "takes" / "edm@suno"
    takes_dir.mkdir(parents=True, exist_ok=True)
    audio_path = takes_dir / "take-01.wav"
    audio_path.write_bytes(b"RIFF....WAVEfake-audio-bytes")

    score_path = project_dir / "composition_score.yaml"
    original_score_text = score_path.read_text(encoding="utf-8")
    swapped_score_text = original_score_text.replace(
        'core: "introspective night drive"', 'core: "swapped during rebuild window"'
    )
    assert swapped_score_text != original_score_text  # sanity

    real_build_recast_plan_artifacts = recast_plan_module.build_recast_plan_artifacts

    def _swap_inputs_then_build(*args: object, **kwargs: object):
        # precheck は既に通過済み（この関数に到達した時点）— rebuild が実際に
        # composition_score.yaml を読む直前に差し替える。
        score_path.write_text(swapped_score_text, encoding="utf-8")
        return real_build_recast_plan_artifacts(*args, **kwargs)

    monkeypatch.setattr(
        recast_plan_module, "build_recast_plan_artifacts", _swap_inputs_then_build
    )

    result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(audio_path),
        ],
    )

    assert result.exit_code == 1
    assert "stale" in result.output.lower()

    # 旧 plan は非破壊（rebuild 済みの新入力向け plan で上書き publish されない）。
    assert plan_path.read_bytes() == plan_bytes_before

    # 旧 package も非破壊（Codex P2, #210 round 3 指摘4: rebuild を
    # `publish=False` で行い、digest 突合を通過するまで package を
    # publish しないことの機械 assert — 従来は rebuild 自体が
    # `publish=True` で走っていたため、この後の digest 拒否より前に
    # 差し替え後の入力で package が上書きされてしまっていた）。
    assert package_path.read_bytes() == package_bytes_before

    # take は収蔵されない（旧注文向け音声が新 plan の generated として記録されない）。
    assert not (takes_dir / "take.json").exists()

    # state も awaiting_generation のまま（generated へ誤って遷移しない）。
    state_after = load_recast_state(project_dir)
    assert state_after.runs["edm@suno"].state == "awaiting_generation"


def test_recast_help_lists_ingest() -> None:
    result = runner.invoke(app, ["recast", "--help"])

    assert result.exit_code == 0
    assert "ingest" in result.output


# --- E2E acceptance: manual run -> deterministic invoke -> ingest CLI -> generated


@pytest.mark.slow
def test_e2e_manual_awaiting_generation_then_ingest_cli_reaches_generated(
    tmp_path: Path,
) -> None:
    """受け入れ条件（PR3 指示書 + Codex P2 review round 2 指摘 5 対応）:
    ① manual run（CLI）→ awaiting_generation
    ② deterministic invoke（API）で「外部生成の代役」音声を合成
    ③ svprpe recast ingest（CLI — next_command.txt が実際に案内するコマンド）で
       takes へ atomic 収蔵
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

    order_dir = project_path.parent / "builds" / "orders" / "edm@suno"
    next_command = (order_dir / "next_command.txt").read_text(encoding="utf-8").strip()
    assert next_command.startswith("svprpe recast ingest ")

    # ② API: deterministic invoke synthesizes a stand-in "externally generated"
    # take. This deliberately stays API-level rather than going through
    # `svprpe recast run --backend deterministic`: `recast_plan.json` is a
    # single file shared by the whole project directory, so running a second
    # backend's plan through the CLI would overwrite it and make the suno
    # run's `plan_sha256` stale — exactly what an *external* generation step
    # (Suno UI, not this repo's own CLI) must not do.
    loaded = load_recast_project(project_path)
    det_artifacts = build_recast_plan_artifacts(
        loaded, variant="edm_deterministic", backend="deterministic", publish=True
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

    # ③ CLI: svprpe recast ingest — the exact command next_command.txt advertises.
    ingest_result = runner.invoke(
        app,
        [
            "recast",
            "ingest",
            str(project_path),
            "--variant",
            "edm",
            "--backend",
            "suno",
            "--audio",
            str(det_take.audio_path),
        ],
    )
    assert ingest_result.exit_code == 0, ingest_result.output

    # ④ state transitioned to generated.
    final_state = load_recast_state(project_path.parent)
    run = final_state.runs["edm@suno"]
    assert run.state == "generated"
    assert run.plan_sha256 is not None

    takes_dir = project_path.parent / "builds" / "takes" / "edm@suno"
    take_json = json.loads((takes_dir / "take.json").read_text(encoding="utf-8"))
    assert take_json["source"] == "manual"
    assert (takes_dir / "take-01.wav").read_bytes() == det_take.audio_path.read_bytes()
