"""svprpe recast plan / status."""
from __future__ import annotations

import hashlib
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


def _read_plan_sha256(path: Path) -> Optional[str]:
    """`path`（`recast_plan.json`）の現在の bytes の sha256 を返す。読めない
    （不在・権限エラー等）場合は `None`（`recast status` はこれを「不明」では
    なく永続化済み `plan_sha256` との不一致として扱い、fail-closed に stale
    表示へ倒す — Codex P2 fourth round #207）。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _write_recast_plan_atomically(path: Path, content: bytes) -> None:
    """tempfile + `os.replace` による atomic publish（`cli/observe_cmd.py` の
    `_write_observation_report_atomically` と同型）。bytes を受け取り binary
    モードで書く（Codex P2 ninth round #207: 旧実装は text モード
    `open(..., "w", encoding="utf-8")` で書いており、Windows では改行が
    `"\n"` → `"\r\n"` に変換される。`plan_sha256` は `canonical.encode("utf-8")`
    から計算していたため、記録した hash と実際にディスクへ書かれた bytes が
    乖離し、publish 直後の `recast status` が偽 stale を報告しうる欠陥が
    あった。呼び出し側が 1 回だけ encode した bytes をそのまま書き込み、
    同じ bytes から hash も計算する single-source 設計に統一する）。"""
    output_dir = path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _plan_state_note(result: Any) -> Optional[str]:
    """`record_state` へ渡す note を `RecastPlanResult` から組み立てる single
    source（`recast plan` / `recast run` 両方の plan 段が共有する — Codex P2
    #207 の意味論を run 側にも一貫適用）: blocked なら `plan.blocked.reasons`、
    blocked でなければ `result.mode_gate_reasons`（`build_recast_plan` の
    strict/advisory ゲートが確定した診断一式 — unsupported + mode_overrides
    宣言時のみ unknown も合成済み。fifth round #207: ここで再導出しない
    single source）、それも無ければ `None`。"""
    plan = result.plan
    if plan.blocked is not None:
        return "; ".join(plan.blocked.reasons)
    return "; ".join(result.mode_gate_reasons) if result.mode_gate_reasons else None


def _print_plan_warnings(plan: Any) -> None:
    """advisory の unsupported changed field warnings 等、`plan.warnings` を
    表示する（`recast plan`/`recast run` 共通 — run 側にも同じ可視性を持たせる、
    Codex P2 #207 の意味論の一貫適用）。"""
    if not plan.warnings:
        return
    console.print("[yellow]Warnings:[/yellow]")
    for warning in plan.warnings:
        console.print(f"  - {warning}")


def _publish_recast_plan(
    loaded: Any, plan: Any, *, protected_inputs: list[Path]
) -> tuple[Path, str]:
    """`plan`（`RecastPlan`）を `<project_dir>/recast_plan.json` へ canonical
    形式（sorted keys, 2-space indent, trailing newline — deterministic）で
    atomic 公開し、publish したバイト列自身の sha256（`plan_sha256`）を返す。

    `recast plan`/`recast run`/`recast ingest` の 3 コマンドすべてが計画
    パイプラインを評価するたびに使う single source（Codex P2 fourth round
    #207 の `plan_sha256` 突合契約 — publish 後の削除・破損・別 (variant,
    backend) の plan による上書きを `recast status` が検出できるようにする
    — を `run`/`ingest` にも一貫適用する。従来 `run` は `recast_plan.json`
    を publish しなかったため、`ingest` が信頼する `plan_sha256` の実体が
    存在しなかった）。呼び出し側は書き込み失敗（`OSError`）を own の Exit
    コードへ変換する。

    `protected_inputs` は呼び出し側が `RecastPlanResult.protected_inputs`
    （plan 段の single-read 束から副作用なく再構成済みの集合）から渡す
    必須引数（デフォルト値なし）。公開前にこの集合との alias 検査を行い、
    衝突時は何も書かずに `ValueError` を送出する（fail-closed — 他公開サイト
    と同じ契約、Codex P2 review round 5, PR3 #208 指摘 10）。

    ここで独自に `collect_protected_input_paths` を呼んで identity manifest
    を**再 parse**しないのが要点（Codex P2 review round 7, PR3 #208 指摘 13:
    再 parse すると、manifest 破損で plan が既に `blocked_verification` へ
    finalize 済みのケースでもこの guard 自身が例外を送出し、「blocked でも
    plan は公開される」契約を破って CLI top-level Error に落ちていた —
    `RecastPlanResult.protected_inputs` は束の失敗許容を継承済みで再 parse
    しないため、blocked plan の publish を妨げない）。

    encode は 1 回だけ行い、書き込みも `plan_sha256` の hash 計算もこの同一
    bytes から行う（Codex P2 review round 6, PR3 #208 由来の pr2 統合分
    #207 ninth round: 旧実装は text モード `open(..., "w", encoding="utf-8")`
    で書いており、Windows では改行が `"\n"` → `"\r\n"` に変換される。
    `plan_sha256` は encode 済み bytes から計算していたため、記録した hash
    と実際にディスクへ書かれた bytes が乖離し、publish 直後の `recast
    status` が偽 stale を報告しうる欠陥があった）。
    """
    from svp_rpe.recast.plan import RECAST_PLAN_FILENAME

    plan_path = loaded.project_dir / RECAST_PLAN_FILENAME
    if plan_path.resolve() in {candidate.resolve() for candidate in protected_inputs}:
        raise ValueError(f"output path collides with a protected input path: {plan_path}")

    canonical = (
        json.dumps(
            plan.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    canonical_bytes = canonical.encode("utf-8")
    _write_recast_plan_atomically(plan_path, canonical_bytes)
    plan_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    return plan_path, plan_sha256


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

    # `result.protected_inputs` は plan 段の single-read 束から副作用なく
    # 再構成済み（Codex P2 review round 7, PR3 #208 指摘 13）— ここで
    # `collect_protected_input_paths` を独立に呼んで manifest を再 parse
    # しない（blocked_verification でも publish 前ガードが例外を送出しない
    # ようにする）。
    try:
        plan_path, plan_sha256 = _publish_recast_plan(
            loaded, result.plan, protected_inputs=result.protected_inputs
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # 状態記録は plan JSON の publish が成功した後にのみ行う（Codex P2 #207）:
    # 書き込み失敗時に stale な recast_state.json を残さないための順序保証。
    try:
        record_state(
            loaded.project_dir,
            variant,
            backend,
            result.plan.state_reached,
            _plan_state_note(result),
            inputs_digest=result.inputs_digest,
            plan_sha256=plan_sha256,
            protected_inputs=result.protected_inputs,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

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
    artifacts, not recomputing them) — and, like `recast plan`, publishes
    `recast_plan.json` and records the plan-stage outcome (`state_reached`,
    `blocked`/advisory-unsupported note, `inputs_digest`, `plan_sha256`) via
    the exact same `record_state`/`_publish_recast_plan` semantics (Codex P2
    #207 / fourth round applied consistently here — `run` used to skip
    publishing `recast_plan.json` at all, which left `plan_sha256` with no
    real file to stand for, breaking the fail-closed staleness contract
    `recast ingest` needs before trusting a recorded `awaiting_generation`).
    Reaching `compiled`/`verified` (whichever `policy.require_verified_package`
    demands) is required — anything short of that (a `blocked_*` state) is
    reported (including advisory warnings) and exits 1 without invoking a
    backend.

    manual backends publish the 6 order files under
    `<builds_root>/orders/<variant>@<backend>/`, *then* record
    `awaiting_generation`, and exit 0. local backends invoke the generator,
    *then* record `generated` (or `generation_failed` on error), and exit 0/1
    accordingly — publish-before-record in both cases, matching the plan
    stage's publish-before-record order. Every `record_state` call in this
    command carries the same `inputs_digest`/`plan_sha256` computed by the
    plan stage, so `recast status`'s (and `recast ingest`'s) stale-run
    detection also covers post-generation states.
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
    from svp_rpe.recast.plan import _normalize_diagnostic, build_recast_plan_artifacts
    from svp_rpe.recast.state import record_state

    try:
        loaded = load_recast_project(project_yaml)
        artifacts = build_recast_plan_artifacts(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError, ArrangementError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # `artifacts.result.protected_inputs` は plan 段の single-read 束から
    # 副作用なく再構成済み（Codex P2 review round 7, PR3 #208 指摘 13）—
    # `collect_protected_input_paths` を独立に呼んで manifest を再 parse しない。
    protected_inputs = artifacts.result.protected_inputs
    try:
        _plan_path, plan_sha256 = _publish_recast_plan(
            loaded, artifacts.result.plan, protected_inputs=protected_inputs
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    inputs_digest = artifacts.result.inputs_digest
    # plan 段の到達状態は plan JSON の publish 成功後にのみ記録する（Codex P2
    # #207 の順序保証を run にも適用 — plan_sha256 が実在ファイルを指すように）。
    try:
        record_state(
            loaded.project_dir,
            variant,
            backend,
            artifacts.result.plan.state_reached,
            _plan_state_note(artifacts.result),
            inputs_digest=inputs_digest,
            plan_sha256=plan_sha256,
            protected_inputs=protected_inputs,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
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
        # inputs_digest/plan_sha256 は plan 段で計算・publish 済みの値を引き回す
        # （Codex P2 review round 2 指摘 4: run 後の record_state が
        # inputs_digest=None で上書きすると、plan 実行後の入力変更を status の
        # stale 検出が見失う。plan_sha256 も同様に引き回さないと `recast ingest`
        # の plan ファイル突合が機能しない）。
        note = os.path.relpath(prepared.order_dir, loaded.project_dir)
        try:
            record_state(
                loaded.project_dir,
                variant,
                backend,
                "awaiting_generation",
                note=note,
                inputs_digest=inputs_digest,
                plan_sha256=plan_sha256,
                protected_inputs=protected_inputs,
            )
        except (OSError, ValueError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        console.print(f"[green]Order files published to {prepared.order_dir}[/green]")
        console.print(f"Next step: see {prepared.order_dir / 'next_command.txt'}")
        raise typer.Exit(code=0)

    try:
        take = invoker.invoke(prepared)
    except RecastError as exc:
        # note は recast_plan.json と同じ絞り口（`_normalize_diagnostic`）を通し、
        # 例外メッセージに実行マシンの絶対パスが residual として残らないようにする
        # （Codex P2 review round 4, PR3 #208: run/ingest 側の state note にも
        # plan.py 側と同一の正規化を一貫適用する要請への対応）。
        try:
            record_state(
                loaded.project_dir,
                variant,
                backend,
                "generation_failed",
                note=_normalize_diagnostic(str(exc), loaded.project_dir),
                inputs_digest=inputs_digest,
                plan_sha256=plan_sha256,
                protected_inputs=protected_inputs,
            )
        except (OSError, ValueError) as record_exc:
            typer.echo(f"Error: {record_exc}", err=True)
            raise typer.Exit(code=1) from record_exc
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    note = f"{os.path.relpath(take.audio_path, loaded.project_dir)} sha256={take.sha256}"
    try:
        record_state(
            loaded.project_dir,
            variant,
            backend,
            "generated",
            note=note,
            inputs_digest=inputs_digest,
            plan_sha256=plan_sha256,
            protected_inputs=protected_inputs,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
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
    (per `recast_state.json`). Before trusting that recorded state, applies
    the exact same fail-closed staleness checks as `recast status`
    (Codex P2 review round 2 indicated this command is where PR2's own
    forward-looking `plan_sha256` note applies): the recorded `inputs_digest`
    must match the current inputs, *and* the recorded `plan_sha256` must
    match the current `recast_plan.json` bytes (missing/unreadable counts as
    a mismatch) — either mismatch is reported and exits 1 without touching
    any file. A `None` `inputs_digest`/`plan_sha256` (e.g. an old-schema or
    hand-copied state) is *not* trusted as "unknown, skip the check" — it is
    treated as stale too (Codex P2 review round 4, PR3 #208 指摘 9: a missing
    pin is exactly the case a stale/forged state most plausibly has). Only
    then does it rebuild the plan context via the same
    `build_recast_plan_artifacts` path `recast run` uses. Before that rebuild's
    result is trusted (re-published as `recast_plan.json` or used to `collect`
    the take), its freshly recomputed `inputs_digest` is re-compared against
    the same recorded pin (Codex P2 eighth round #207 指摘16: the precheck
    above and this rebuild are not atomic — inputs could be swapped in the gap
    between them, letting an old order's externally generated audio get
    recorded as the `generated` take for a *new* plan built from the swapped
    inputs). A mismatch here exits 1 without publishing the plan, collecting
    the audio, or touching `recast_state.json` — the old plan/state are left
    untouched. Only once that re-check passes does it re-publish
    `recast_plan.json` (same `_publish_recast_plan` single source), resolve
    the `ManualInvoker`, and call `collect(audio)` to atomically ingest the
    audio into `<builds_root>/takes/<variant>@<backend>/`. Records `generated`
    (with the freshly (re)computed `inputs_digest`/`plan_sha256`) only after
    that publish succeeds — this is the command `next_command.txt`/
    `order_sheet.md` (`ManualInvoker`) advertise.

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
    from svp_rpe.recast.plan import (
        RECAST_PLAN_FILENAME,
        build_recast_plan_artifacts,
        compute_recast_inputs_digest,
    )
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

    # 状態を信用する前の突合（`recast status` と同じ fail-closed 意味論、
    # Codex P2 review round 2 指摘 4 / PR2 の申し送り「run コマンド追加時は
    # plan_sha256 突合を先に行う」対応）: inputs_digest → plan_sha256 の順。
    try:
        current_digest = compute_recast_inputs_digest(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if run.inputs_digest is None:
        typer.echo(
            f"Error: {run_key} has no recorded inputs_digest (stale — no pin). "
            "Re-run 'svprpe recast plan' / 'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    if run.inputs_digest != current_digest:
        typer.echo(
            f"Error: {run_key} inputs changed since the order files were published (stale). "
            "Re-run 'svprpe recast plan' / 'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    current_plan_sha256 = _read_plan_sha256(loaded.project_dir / RECAST_PLAN_FILENAME)
    if run.plan_sha256 is None:
        typer.echo(
            f"Error: {run_key} has no recorded plan_sha256 (stale — no pin). "
            "Re-run 'svprpe recast plan' / 'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)
    if run.plan_sha256 != current_plan_sha256:
        typer.echo(
            f"Error: {run_key} plan artifact (recast_plan.json) changed or is missing since "
            "the order files were published (stale). Re-run 'svprpe recast plan' / "
            "'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        artifacts = build_recast_plan_artifacts(loaded, variant=variant, backend=backend)
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError, ArrangementError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # rebuild 後・publish/collect の前に、rebuild が実際に見た入力の digest を
    # 記録済み pin（`run.inputs_digest` — 上の precheck で `current_digest` との
    # 一致を既に確認済み）と再突合する（Codex P2 eighth round #207 指摘16:
    # precheck（518行目 `compute_recast_inputs_digest`）と rebuild（直上の
    # `build_recast_plan_artifacts`）の間には TOCTOU 窓があり、その間に入力
    # （例: composition_score.yaml）が差し替えられると rebuild は新しい入力で
    # 新しい plan を構築してしまう — precheck は既に通過済みなのでここを
    # チェックしないと、旧注文（旧 prompt/lyrics 向けに外部生成された音声）を
    # 新 plan の `generated` として記録・plan を新入力で上書き公開してしまう。
    # `build_recast_plan_artifacts` は同じ入力から独立に digest を再計算する
    # ため（`compute_recast_inputs_digest` と同一ロジック — plan.py 内)、
    # 差し替えがあれば必ず値が変わり検出できる。ここで拒否する場合は
    # `_publish_recast_plan`/`collect`/`record_state` のいずれも呼ばない
    # （plan・take・state のいずれも書き換えない — 旧 plan/state は無傷のまま）。
    if artifacts.result.inputs_digest != run.inputs_digest:
        typer.echo(
            f"Error: {run_key} inputs changed since the order files were published "
            "(detected during rebuild — stale). Re-run 'svprpe recast plan' / "
            "'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        _plan_path, plan_sha256 = _publish_recast_plan(
            loaded, artifacts.result.plan, protected_inputs=artifacts.result.protected_inputs
        )
    except (OSError, ValueError) as exc:
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
    try:
        record_state(
            loaded.project_dir,
            variant,
            backend,
            "generated",
            note=note,
            inputs_digest=artifacts.result.inputs_digest,
            plan_sha256=plan_sha256,
            protected_inputs=prepared.protected_input_paths,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Ingested take: {take.audio_path} (sha256={take.sha256})[/green]")
    console.print("Next step: observe/report は今後のコマンドで提供予定です（未実装）。")


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
    表示へ倒す（Codex P2 fourth round #207: fail-closed）。`inputs_digest`/
    `plan_sha256` のいずれかが `None`（旧形式/手動コピーされた state 等）の
    場合も「未確認だからスキップ」ではなく stale（pin なし）として表示する
    （Codex P2 review round 4, PR3 #208 指摘 9）。
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
                if run.inputs_digest is None or run.plan_sha256 is None:
                    note = "stale（pin なし）— svprpe recast plan 再実行が必要"
                    next_step = replan_step
                elif run.inputs_digest != current_digest:
                    note = "stale（入力が変更済み）— svprpe recast plan 再実行が必要"
                    next_step = replan_step
                elif run.plan_sha256 != current_plan_sha256:
                    note = "stale（plan 成果物が変更/不在）— svprpe recast plan 再実行が必要"
                    next_step = replan_step
                else:
                    next_step = _NEXT_STEP_BY_STATE.get(state, "-")
            label = f"{state} {note}".strip()
            table.add_row(key, label, next_step)
    console.print(table)
