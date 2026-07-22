"""svprpe recast plan / status."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import typer
from rich.table import Table

from svp_rpe.cli._app import app, console

recast_app = typer.Typer(
    help="Recast workspace: build/inspect a recast plan against existing sidecars."
)
app.add_typer(recast_app, name="recast")

# state -> 次に打つべきコマンドの固定マップ（`recast status` の「次の一手」列）。
_NEXT_STEP_BY_STATE: dict[str, str] = {
    "draft": "svprpe recast plan <project.yaml> --variant <variant> --backend <backend>",
    "authored": "svprpe recast plan <project.yaml> --variant <variant> --backend <backend>",
    "compiled": "policy.require_verified_package を有効にするか svprpe verify で検証",
    "verified": "svprpe recast run <project.yaml> --variant <variant> --backend <backend>",
    "awaiting_generation": (
        "外部生成後: svprpe recast ingest <project.yaml> --variant <variant> "
        "--backend <backend> --audio <takes_dir>/take-01.wav (注文書 next_command.txt 参照)"
    ),
    "generated": "観測 (svprpe observe 連携) は PR5 で configure 予定",
    "observed": "観測結果を確認し reported へ進める",
    "reported": "run 完了",
    "blocked_authoring": "TODO(transcribe) sentinel / preservation 契約違反を解消して再実行",
    "blocked_capability": "capability_mode を advisory へ切替するか anchor 要求を降格して再実行",
    "blocked_verification": "identity manifest / package artifact の整合性を修正して再実行",
    "generation_failed": "生成失敗の原因を調査し、生成をやり直す",
    "observation_incomplete": "完全な音声 artifact で svprpe observe をやり直す",
}


def _write_recast_plan_atomically(path: Path, content: str) -> None:
    """tempfile + `os.replace` による atomic publish（`cli/observe_cmd.py` の
    `_write_observation_report_atomically` と同型）。"""
    output_dir = path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _plan_state_note(plan: Any) -> Optional[str]:
    """`record_state` へ渡す note を `RecastPlan` から組み立てる single source
    （`recast plan` / `recast run` 両方の plan 段が共有する — Codex P2 #207 の
    意味論を run 側にも一貫適用: blocked なら reasons、blocked でなければ
    advisory の unsupported changed field 診断、それも無ければ `None`）。"""
    from svp_rpe.recast.plan import unsupported_changed_field_reasons

    if plan.blocked is not None:
        return "; ".join(plan.blocked.reasons)
    reasons = unsupported_changed_field_reasons(plan.changed_fields, plan.invocation_mode)
    return "; ".join(reasons) if reasons else None


def _print_plan_warnings(plan: Any) -> None:
    """advisory の unsupported changed field warnings 等、`plan.warnings` を
    表示する（`recast plan`/`recast run` 共通 — run 側にも同じ可視性を持たせる、
    Codex P2 #207 の意味論の一貫適用）。"""
    if not plan.warnings:
        return
    console.print("[yellow]Warnings:[/yellow]")
    for warning in plan.warnings:
        console.print(f"  - {warning}")


@recast_app.command("plan")
def recast_plan_cmd(
    project_yaml: str = typer.Argument(..., help="Path to RecastProject YAML"),
    variant: str = typer.Option(..., "--variant", help="Variant name declared in the project"),
    backend: str = typer.Option(..., "--backend", help="Backend name declared in the project"),
) -> None:
    """Build a recast plan against existing sidecars, persisting the plan + run state.

    Writes `<project_dir>/recast_plan.json` (canonical: sorted keys, 2-space
    indent, trailing newline — deterministic across checkouts) and records the
    reached state to `<project_dir>/recast_state.json` (`recast.state.record_state`).
    Exits 1 (without failing to write the plan) when the run reaches a
    `blocked_*` state, exits 0 otherwise.
    """
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.resolver import ArrangementError
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.plan import build_recast_plan
    from svp_rpe.recast.state import record_state

    try:
        loaded = load_recast_project(project_yaml)
        result = build_recast_plan(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError, ArrangementError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    plan_path = loaded.project_dir / "recast_plan.json"
    canonical = (
        json.dumps(
            result.plan.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    try:
        _write_recast_plan_atomically(plan_path, canonical)
    except OSError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # 状態記録は plan JSON の publish が成功した後にのみ行う（Codex P2 #207）:
    # 書き込み失敗時に stale な recast_state.json を残さないための順序保証。
    record_state(
        loaded.project_dir,
        variant,
        backend,
        result.plan.state_reached,
        _plan_state_note(result.plan),
        inputs_digest=result.inputs_digest,
    )

    anchors_table = Table(title=f"Anchors: {variant}@{backend}")
    anchors_table.add_column("anchor_id")
    anchors_table.add_column("domain")
    anchors_table.add_column("policy_mode")
    anchors_table.add_column("delivery")
    anchors_table.add_column("channel")
    anchors_table.add_column("sensor_available")
    for anchor in result.plan.anchors:
        anchors_table.add_row(
            anchor.anchor_id,
            anchor.domain,
            anchor.policy_mode or "-",
            anchor.delivery,
            anchor.channel or "-",
            "yes" if anchor.sensor_available else "no",
        )
    console.print(anchors_table)

    changed_table = Table(title="Changed fields")
    changed_table.add_column("path")
    changed_table.add_column("preservation_mode")
    changed_table.add_column("mode_support")
    changed_table.add_column("note")
    for change in result.plan.changed_fields:
        changed_table.add_row(
            change.path, change.preservation_mode, change.mode_support, change.note or "-"
        )
    console.print(changed_table)

    _print_plan_warnings(result.plan)

    if result.plan.blocked is not None:
        console.print(f"[red]Blocked: {result.plan.blocked.state}[/red]")
        for reason in result.plan.blocked.reasons:
            console.print(f"  - {reason}")
    console.print(f"State reached: [bold]{result.plan.state_reached}[/bold]")
    console.print(f"Recommendation: {result.plan.recommendation}")
    console.print(f"[green]Recast plan saved to {plan_path}[/green]")

    if result.plan.blocked is not None:
        raise typer.Exit(code=1)


_COMPILED_OR_BETTER_STATES = frozenset({"compiled", "verified"})


@recast_app.command("run")
def recast_run_cmd(
    project_yaml: str = typer.Argument(..., help="Path to RecastProject YAML"),
    variant: str = typer.Option(..., "--variant", help="Variant name declared in the project"),
    backend: str = typer.Option(..., "--backend", help="Backend name declared in the project"),
) -> None:
    """Resolve the backend invoker for (variant, backend) and run it.

    Step 1 runs the same plan pipeline as `recast plan` (reusing its compiled
    artifacts, not recomputing them) — the plan-stage outcome (`state_reached`,
    `blocked`/advisory-unsupported note, `inputs_digest`) is recorded via the
    same `record_state` semantics as `recast plan` (Codex P2 #207 applied
    consistently here too), even though `run` does not itself publish
    `recast_plan.json` (there is no artifact-ordering concern for a file this
    command never writes — the record purely reflects what this invocation's
    plan pipeline evaluated). Reaching `compiled`/`verified` (whichever
    `policy.require_verified_package` demands) is required — anything short of
    that (a `blocked_*` state) is reported (including advisory warnings) and
    exits 1 without invoking a backend.

    manual backends publish the 6 order files under
    `<builds_root>/orders/<variant>@<backend>/`, *then* record
    `awaiting_generation`, and exit 0. local backends invoke the generator,
    *then* record `generated` (or `generation_failed` on error), and exit 0/1
    accordingly — publish-before-record in both cases, matching the plan
    stage's publish-before-record order. Every `record_state` call in this
    command carries the same `inputs_digest` computed by the plan stage, so
    `recast status`'s stale-run detection also covers post-generation states.
    """
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.resolver import ArrangementError
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.backend import (
        load_backend_capability_profile,
        resolve_invoker,
        run_context_from_plan_artifacts,
    )
    from svp_rpe.recast.plan import build_recast_plan_artifacts
    from svp_rpe.recast.state import record_state

    try:
        loaded = load_recast_project(project_yaml)
        artifacts = build_recast_plan_artifacts(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError, ArrangementError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    inputs_digest = artifacts.result.inputs_digest
    # plan 段の到達状態を記録する（Codex P2 #207 の意味論を run にも一貫適用）。
    # run は recast_plan.json を publish しないため「publish 成功後に記録」の
    # artifact-ordering 制約はそもそも掛からない — この record は単に「この
    # 呼び出しの plan パイプラインが何を評価したか」を反映するだけ。
    record_state(
        loaded.project_dir,
        variant,
        backend,
        artifacts.result.plan.state_reached,
        _plan_state_note(artifacts.result.plan),
        inputs_digest=inputs_digest,
    )
    _print_plan_warnings(artifacts.result.plan)

    state_reached = artifacts.result.plan.state_reached
    if state_reached not in _COMPILED_OR_BETTER_STATES:
        console.print(
            f"[red]recast run: {variant}@{backend} has not reached a compiled package "
            f"(state_reached={state_reached})[/red]"
        )
        if artifacts.result.plan.blocked is not None:
            console.print(f"[red]Blocked: {artifacts.result.plan.blocked.state}[/red]")
            for reason in artifacts.result.plan.blocked.reasons:
                console.print(f"  - {reason}")
        console.print(f"Recommendation: {artifacts.result.plan.recommendation}")
        raise typer.Exit(code=1)

    try:
        profile = load_backend_capability_profile(loaded, backend)
        ctx = run_context_from_plan_artifacts(
            loaded, variant=variant, backend=backend, artifacts=artifacts, profile=profile
        )
        invoker = resolve_invoker(artifacts.backend_ref, profile)
        prepared = invoker.prepare(ctx)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if artifacts.backend_ref.invocation == "manual":
        # 注文書 6 ファイルは invoker.prepare() が既に atomic 公開済み — 記録は
        # その公開成功後（prepare() が例外なく戻った後）にのみ行う。
        # inputs_digest は plan 段で計算済みの値を引き回す（Codex P2 review round 2
        # 指摘 4: run 後の record_state が inputs_digest=None で上書きすると、
        # plan 実行後の入力変更を status の stale 検出が見失う）。
        note = os.path.relpath(prepared.order_dir, loaded.project_dir)
        record_state(
            loaded.project_dir,
            variant,
            backend,
            "awaiting_generation",
            note=note,
            inputs_digest=inputs_digest,
        )
        console.print(f"[green]Order files published to {prepared.order_dir}[/green]")
        console.print(f"Next step: see {prepared.order_dir / 'next_command.txt'}")
        raise typer.Exit(code=0)

    try:
        take = invoker.invoke(prepared)
    except RecastError as exc:
        record_state(
            loaded.project_dir,
            variant,
            backend,
            "generation_failed",
            note=str(exc),
            inputs_digest=inputs_digest,
        )
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    note = f"{os.path.relpath(take.audio_path, loaded.project_dir)} sha256={take.sha256}"
    record_state(
        loaded.project_dir, variant, backend, "generated", note=note, inputs_digest=inputs_digest
    )
    console.print(f"[green]Generated take: {take.audio_path} (sha256={take.sha256})[/green]")


@recast_app.command("ingest")
def recast_ingest_cmd(
    project_yaml: str = typer.Argument(..., help="Path to RecastProject YAML"),
    variant: str = typer.Option(..., "--variant", help="Variant name declared in the project"),
    backend: str = typer.Option(..., "--backend", help="Backend name declared in the project"),
    audio: str = typer.Option(
        ..., "--audio", help="Path to the externally generated audio (.wav/.mp3)"
    ),
) -> None:
    """Ingest an externally generated take for a manual backend run.

    Requires `(variant, backend)` to currently be at `awaiting_generation`
    (per `recast_state.json`) — anything else (including a stale
    `inputs_digest`, same detection as `recast status`) is reported and
    exits 1 without touching any file. Rebuilds the same plan context as
    `recast run` (not a different code path), resolves the `ManualInvoker`,
    and calls `collect(audio)` to atomically ingest the audio into
    `<builds_root>/takes/<variant>@<backend>/`. Records `generated` (with the
    same `inputs_digest` the plan stage computed) only after that publish
    succeeds — this is the command `next_command.txt`/`order_sheet.md`
    (`ManualInvoker`) advertise.

    Scope: this command stops at `generated`. `observe`/`report` integration
    (`recast status`'s next-step for `generated`) is PR5 scope — this command
    never names an unimplemented command.
    """
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.resolver import ArrangementError
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.backend import (
        load_backend_capability_profile,
        resolve_invoker,
        run_context_from_plan_artifacts,
    )
    from svp_rpe.recast.plan import build_recast_plan_artifacts, compute_recast_inputs_digest
    from svp_rpe.recast.state import load_recast_state, record_state

    try:
        loaded = load_recast_project(project_yaml)
        state_file = load_recast_state(loaded.project_dir)
    except (OSError, ValueError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    run_key = f"{variant}@{backend}"
    run = state_file.runs.get(run_key)
    if run is None or run.state != "awaiting_generation":
        current_state = run.state if run is not None else "draft"
        typer.echo(
            f"Error: {run_key} is at state {current_state!r}, not 'awaiting_generation'. "
            "Run 'svprpe recast run' first to publish order files for a manual backend.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        current_digest = compute_recast_inputs_digest(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if run.inputs_digest is not None and run.inputs_digest != current_digest:
        typer.echo(
            f"Error: {run_key} inputs changed since the order files were published (stale). "
            "Re-run 'svprpe recast plan' / 'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        artifacts = build_recast_plan_artifacts(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError, ArrangementError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        profile = load_backend_capability_profile(loaded, backend)
        ctx = run_context_from_plan_artifacts(
            loaded, variant=variant, backend=backend, artifacts=artifacts, profile=profile
        )
        invoker = resolve_invoker(artifacts.backend_ref, profile)
        prepared = invoker.prepare(ctx)
        take = invoker.collect(prepared, Path(audio))
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    note = f"{os.path.relpath(take.audio_path, loaded.project_dir)} sha256={take.sha256}"
    record_state(
        loaded.project_dir,
        variant,
        backend,
        "generated",
        note=note,
        inputs_digest=artifacts.result.inputs_digest,
    )
    console.print(f"[green]Ingested take: {take.audio_path} (sha256={take.sha256})[/green]")
    console.print("Next step: observe/report は今後のコマンドで提供予定です（未実装）。")


@recast_app.command("status")
def recast_status_cmd(
    project_yaml: str = typer.Argument(..., help="Path to RecastProject YAML"),
) -> None:
    """Show `recast_state.json`'s current state per (variant, backend) run + next step.

    入力（score/identity_manifest/arrangement spec/capability profile/
    mode_overrides）が記録済み run の `inputs_digest` と一致しない場合、その run
    は stale（`recast plan` 再実行が必要）として表示する — 旧 state をそのまま
    信用して次の一手（例: 生成に進む）を勧めない（Codex P2 #207）。
    """
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.plan import compute_recast_inputs_digest
    from svp_rpe.recast.state import load_recast_state

    try:
        loaded = load_recast_project(project_yaml)
        state_file = load_recast_state(loaded.project_dir)
    except (OSError, ValueError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    table = Table(title=f"Recast status: {loaded.project.project.id}")
    table.add_column("variant@backend")
    table.add_column("state")
    table.add_column("next step")

    replan_step = _NEXT_STEP_BY_STATE["draft"]
    for variant in sorted(loaded.project.variants):
        for backend in sorted(loaded.project.backends):
            key = f"{variant}@{backend}"
            run = state_file.runs.get(key)
            if run is None:
                state = "draft"
                note = "(plan 未実行)"
                next_step = _NEXT_STEP_BY_STATE.get(state, "-")
            else:
                state = run.state
                note = run.note or ""
                current_digest = compute_recast_inputs_digest(
                    loaded, variant=variant, backend=backend
                )
                if run.inputs_digest is not None and run.inputs_digest != current_digest:
                    note = "stale（入力が変更済み）— svprpe recast plan 再実行が必要"
                    next_step = replan_step
                else:
                    next_step = _NEXT_STEP_BY_STATE.get(state, "-")
            label = f"{state} {note}".strip()
            table.add_row(key, label, next_step)
    console.print(table)
