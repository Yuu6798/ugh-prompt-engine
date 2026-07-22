"""svprpe recast plan / status."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

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
    "verified": "backend を実行して音声を生成する（invocation_mode に従う）",
    "awaiting_generation": "backend を実行して音声を生成する",
    "generated": "svprpe observe <package.json> <audio> --manifest <identity.yaml> -o <report.json>",
    "observed": "観測結果を確認し reported へ進める",
    "reported": "run 完了",
    "blocked_authoring": "TODO(transcribe) sentinel / preservation 契約違反を解消して再実行",
    "blocked_capability": "capability_mode を advisory へ切替するか anchor 要求を降格して再実行",
    "blocked_verification": "identity manifest / package artifact の整合性を修正して再実行",
    "generation_failed": "生成失敗の原因を調査し、生成をやり直す",
    "observation_incomplete": "完全な音声 artifact で svprpe observe をやり直す",
}


def _read_plan_sha256(path: Path) -> Optional[str]:
    """`path`（`recast_plan.json`）の現在の bytes の sha256 を返す。読めない
    （不在・権限エラー等）場合は `None`（`recast status` はこれを「不明」では
    なく永続化済み `plan_sha256` との不一致として扱い、fail-closed に stale
    表示へ倒す — Codex P2 fourth round #207）。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


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
    from svp_rpe.recast.plan import (
        RECAST_PLAN_FILENAME,
        build_recast_plan,
        unsupported_changed_field_reasons,
    )
    from svp_rpe.recast.state import record_state

    try:
        loaded = load_recast_project(project_yaml)
        result = build_recast_plan(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError, ArrangementError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    plan_path = loaded.project_dir / RECAST_PLAN_FILENAME
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
    # plan_sha256 は実際に publish したバイト列そのものから計算する（Codex P2
    # fourth round #207: publish 後の recast_plan.json 削除・破損・別
    # (variant,backend) の plan による上書きを `recast status` が検出できる
    # ようにする）。
    plan_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if result.plan.blocked is not None:
        state_note = "; ".join(result.plan.blocked.reasons)
    else:
        reasons = unsupported_changed_field_reasons(
            result.plan.changed_fields, result.plan.invocation_mode
        )
        state_note = "; ".join(reasons) if reasons else None
    record_state(
        loaded.project_dir,
        variant,
        backend,
        result.plan.state_reached,
        state_note,
        inputs_digest=result.inputs_digest,
        plan_sha256=plan_sha256,
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

    if result.plan.blocked is not None:
        console.print(f"[red]Blocked: {result.plan.blocked.state}[/red]")
        for reason in result.plan.blocked.reasons:
            console.print(f"  - {reason}")
    console.print(f"State reached: [bold]{result.plan.state_reached}[/bold]")
    console.print(f"Recommendation: {result.plan.recommendation}")
    console.print(f"[green]Recast plan saved to {plan_path}[/green]")

    if result.plan.blocked is not None:
        raise typer.Exit(code=1)


@recast_app.command("status")
def recast_status_cmd(
    project_yaml: str = typer.Argument(..., help="Path to RecastProject YAML"),
) -> None:
    """Show `recast_state.json`'s current state per (variant, backend) run + next step.

    入力（score/identity_manifest/arrangement spec/capability profile/
    mode_overrides/device profile）が記録済み run の `inputs_digest` と一致しない
    場合、その run は stale（`recast plan` 再実行が必要）として表示する — 旧
    state をそのまま信用して次の一手（例: 生成に進む）を勧めない（Codex P2
    #207）。加えて、記録済み `plan_sha256` を現在の `recast_plan.json` の
    bytes（不在/読取失敗を含む）と突き合わせる — publish 後に plan 成果物が
    削除・破損・別 (variant,backend) の plan で上書きされた場合も同様に stale
    表示へ倒す（Codex P2 fourth round #207: fail-closed）。
    """
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.plan import RECAST_PLAN_FILENAME, compute_recast_inputs_digest
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
    plan_path = loaded.project_dir / RECAST_PLAN_FILENAME
    current_plan_sha256 = _read_plan_sha256(plan_path)
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
                elif run.plan_sha256 is not None and run.plan_sha256 != current_plan_sha256:
                    note = "stale（plan 成果物が変更/不在）— svprpe recast plan 再実行が必要"
                    next_step = replan_step
                else:
                    next_step = _NEXT_STEP_BY_STATE.get(state, "-")
            label = f"{state} {note}".strip()
            table.add_row(key, label, next_step)
    console.print(table)
