"""svprpe recast plan / status."""
from __future__ import annotations

import hashlib
import json
import os
import re
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
    # PR5: manual backend は project.yaml の observation.enabled=true なら
    # svprpe recast ingest が observe→report まで自動で進める（下記
    # "reported" 参照）。local backend の recast run はこの拡張の対象外の
    # ため generated で完了 — observation.enabled でも svprpe observe を
    # 手動実行できる（ingest 非依存の従来経路）。
    "generated": (
        "manual backend + observation.enabled=true: svprpe recast ingest が "
        "observe→report まで自動実行済みのはず。無効時 / local backend は "
        "svprpe observe <package> <audio> --manifest <identity.yaml> -o <report.json> を手動実行"
    ),
    "observed": "reported へ進行中の中間状態（通常 ingest 内で即座に reported へ続く）",
    "reported": "builds_root/reports/<variant>@<backend>/recast_summary.md を確認",
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


_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MULTI_DASH_RE = re.compile(r"-{2,}")


def _slugify(value: str) -> str:
    """`recast init` の project.id / identity work_id / arrangement meta.id を
    `recast/models.py:_SLUG_PATTERN`（`^[a-z0-9][a-z0-9_-]*$`）へ機械的に
    正規化する。空・記号のみ等 slug 化不能な入力は空文字を返す（呼び出し側が
    `"recast-project"` 等のフォールバックへ倒す）。"""
    lowered = value.strip().lower()
    dashed = _SLUG_SANITIZE_RE.sub("-", lowered)
    collapsed = _SLUG_MULTI_DASH_RE.sub("-", dashed).strip("-")
    return collapsed


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
    from svp_rpe.recast.plan import build_recast_plan_artifacts
    from svp_rpe.recast.state import record_state

    try:
        loaded = load_recast_project(project_yaml)
        # `publish=True`（Codex P2 review round 10, PR3 #208 指摘19）:
        # `build_recast_plan`（読み取り専用の薄いラッパー、`publish=False`
        # 固定）は「診断だけしたい」プログラム的呼び出し向けの API であり、
        # `svprpe recast plan` CLI 自体は元々 package/report を builds_root
        # へ永続公開する副作用込みの契約（`recast run`/`ingest` がそれを
        # 再利用する設計の核）を持つ — CLI 3 コマンド（plan/run/ingest）は
        # 明示的に `build_recast_plan_artifacts(..., publish=True)` を直接
        # 呼ぶことでこの契約を保つ。
        artifacts = build_recast_plan_artifacts(
            loaded, variant=variant, backend=backend, publish=True
        )
        result = artifacts.result
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
    from svp_rpe.recast.backend import resolve_invoker, run_context_from_plan_artifacts
    from svp_rpe.recast.plan import _normalize_diagnostic, build_recast_plan_artifacts
    from svp_rpe.recast.state import record_state

    try:
        loaded = load_recast_project(project_yaml)
        artifacts = build_recast_plan_artifacts(
            loaded, variant=variant, backend=backend, publish=True
        )
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
        # `profile` は plan 段（`build_recast_plan_artifacts`）が single-read 束で
        # 既に parse・validate 済みの `artifacts.profile` をそのまま使う —
        # `load_backend_capability_profile` で capability_profile YAML を再 read/
        # 再 parse しない（Codex P2 review round 12, PR3 #208 指摘24: 従来は
        # ここで独立に再 read しており、plan の診断（recast_plan.json）が前提と
        # した profile と実際に invoke/注文書へ使われる profile が実行中の入力
        # 変化で乖離し得た）。
        ctx = run_context_from_plan_artifacts(
            loaded, variant=variant, backend=backend, artifacts=artifacts
        )
        invoker = resolve_invoker(artifacts.backend_ref, ctx.profile)
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

    Scope (PR5): once the take is collected and `generated` is recorded, if
    `project.yaml`'s `observation.enabled` is true this command continues on
    to observe the take (`svp_rpe.arrange.observe.observe_generated_artifact`,
    the same provenance-checked path `svprpe observe` uses) against the
    published `performance_package.json` + `--manifest` identity manifest,
    records `observed`, builds + publishes a `recast_report.json` +
    `recast_summary.md` pair (`svp_rpe.recast.report`) under
    `<builds_root>/reports/<variant>@<backend>/`, and records `reported`. A
    failure anywhere in the observe/report stage (extraction error, publish
    I/O failure, ...) is recorded as `observation_incomplete` and exits 1 —
    no partial `recast_report.json`/`recast_summary.md` is ever left behind
    (the pair publishes atomically as one bundle, `atomic_publish_bytes_bundle`).
    When `observation.enabled` is false this command stops at `generated`,
    unchanged from PR3/PR4 behavior — `svprpe observe` remains available as a
    manual fallback either way.

    Scope (PR6): `observation.anchors`（非空リスト）を宣言した project は
    `recast_report.json`/`recast_summary.md`（coverage 集計も含む）をその
    anchor 集合へ絞り込む（`recast.report.build_recast_report` の
    `observation_anchors` 引数）。空リスト（既定）は絞り込みなし＝全 anchor。
    未知 anchor id を宣言した project は `build_recast_plan_artifacts`
    （plan 段、identity manifest ロード直後）が `RecastError` で fail-closed
    する — この ingest コマンドに到達する前に `recast plan`/`recast run` の
    時点で既に落ちている。
    """
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.identity import IdentityManifestError
    from svp_rpe.arrange.observe import observe_generated_artifact
    from svp_rpe.arrange.package import (
        COMPILATION_REPORT_FILENAME,
        PERFORMANCE_PACKAGE_FILENAME,
    )
    from svp_rpe.arrange.resolver import ArrangementError
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.backend import (
        atomic_publish_bytes_bundle,
        resolve_invoker,
        run_context_from_plan_artifacts,
    )
    from svp_rpe.recast.plan import (
        RECAST_PLAN_FILENAME,
        _atomic_publish_text_bundle,
        _normalize_diagnostic,
        build_recast_plan_artifacts,
        compute_recast_inputs_digest,
    )
    from svp_rpe.recast.report import (
        RECAST_REPORT_FILENAME,
        RECAST_SUMMARY_FILENAME,
        build_recast_report,
        render_recast_summary_markdown,
    )
    from svp_rpe.recast.run_paths import resolve_packages_dir, resolve_reports_dir
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
        # `publish=False`（Codex P2, #210 round 3 指摘4）: 突合前に
        # `builds/packages/<variant>@<backend>/` を上書きしない。rebuild
        # 直後の digest 再突合（すぐ下）が reject した場合、packages・plan・
        # state のいずれも無変更のまま exit する契約を保つ。
        artifacts = build_recast_plan_artifacts(
            loaded, variant=variant, backend=backend, publish=False
        )
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
    # packages/`_publish_recast_plan`/`collect`/`record_state` のいずれも
    # 呼ばない（packages・plan・take・state のいずれも書き換えない — 旧
    # plan/state/packages は無傷のまま。round 3 指摘4: `publish=False` に
    # したことで packages/ もこの契約に含まれるようになった）。
    if artifacts.result.inputs_digest != run.inputs_digest:
        typer.echo(
            f"Error: {run_key} inputs changed since the order files were published "
            "(detected during rebuild — stale). Re-run 'svprpe recast plan' / "
            "'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    # 突合を通過して初めて package/report を publish する（Codex P2, #210
    # round 3 指摘4）: `build_recast_plan_artifacts` を `publish=True` で
    # 再実行せず、上の `publish=False` 呼び出しが既に確定させた
    # `artifacts.compiled`（in-memory の compile 済み成果物 — state_reached
    # が compiled/verified のときのみ non-None、`_publish_recast_plan` 直下の
    # `_COMPILED_OR_BETTER_STATES` チェックより前にここへ到達するため必ず
    # non-None）をそのまま書き出す「公開専用」の呼び出しに留める。二重
    # コンパイルも新たな読み取り機会（＝新たな TOCTOU 窓）も増やさない。
    if artifacts.compiled is not None:
        try:
            _atomic_publish_text_bundle(
                resolve_packages_dir(loaded, variant, backend),
                {
                    PERFORMANCE_PACKAGE_FILENAME: artifacts.compiled.package_json.encode(
                        "utf-8"
                    ),
                    COMPILATION_REPORT_FILENAME: artifacts.compiled.report_json.encode(
                        "utf-8"
                    ),
                },
                protected_inputs=artifacts.result.protected_inputs,
            )
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    try:
        _plan_path, plan_sha256 = _publish_recast_plan(
            loaded, artifacts.result.plan, protected_inputs=artifacts.result.protected_inputs
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        # `profile` は plan 段が single-read 束で既に parse・validate 済みの
        # `artifacts.profile` をそのまま使う（Codex P2 review round 12, PR3
        # #208 指摘24 — 詳細は `recast run` 側の同型コメント参照）。
        ctx = run_context_from_plan_artifacts(
            loaded, variant=variant, backend=backend, artifacts=artifacts
        )
        invoker = resolve_invoker(artifacts.backend_ref, ctx.profile)
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

    if not loaded.project.observation.enabled:
        console.print(
            "Next step: project.yaml の observation.enabled が無効です。"
            "svprpe observe <package.json> <audio> --manifest <identity.yaml> "
            "-o <report.json> で手動観測してください。"
        )
        raise typer.Exit(code=0)

    # PR5: observation.enabled=true の manual backend はここから observe→report
    # まで自動で継続する。`take.audio_path` は上の `atomic_publish_bytes_bundle`
    # 経由の publish が成功した後の実ファイルパスであり、`prepared.package` は
    # `PerformancePackage` オブジェクトそのものだが `observe_generated_artifact`
    # は provenance chain を自分自身で再検証する必要があるため、公開済みの
    # `performance_package.json` bytes を改めて読む（in-memory オブジェクトを
    # 信用しない — `svprpe observe` の D-3 契約と同じ posture）。
    package_path = resolve_packages_dir(loaded, variant, backend) / PERFORMANCE_PACKAGE_FILENAME
    take_relative = os.path.relpath(take.audio_path, loaded.project_dir)
    # `run_context_from_plan_artifacts`（上で `ctx` を組み立てる際）が既に
    # `artifacts.compiled is not None` を要求している（さもなくば RecastError
    # で既に exit 1 済み）ため、ここへ到達する時点で常に non-None。
    assert artifacts.compiled is not None

    try:
        # `expected_audio_sha256=take.sha256`（Codex P2, #210 round 2 指摘2）:
        # `collect()`/`invoke()` が確定させた take の sha256 を、
        # `observe_generated_artifact` 自身が `take.audio_path` を読んだ直後に
        # 突き合わせる — collect 完了からこの読み出しまでの間に take が
        # 差し替わっていた場合、「観測していない take を collect 時 hash で
        # 証明する」report を組み立てる前に fail-closed する。
        # `expected_package_sha256=artifacts.compiled.report.package_sha256`
        # （Codex P2, #210 round 3 指摘5）: 同型の pin を package 側にも適用
        # する — 直上で publish した package の sha256 を、
        # `observe_generated_artifact` が `package_path` を読んだ直後に
        # 突き合わせ、公開後に自己整合な別 package へ差し替えられていても
        # 観測前に fail-closed する。
        observation = observe_generated_artifact(
            package_path=package_path,
            manifest_path=loaded.identity_manifest_path,
            audio_path=take.audio_path,
            generated_artifact_path=take_relative,
            expected_audio_sha256=take.sha256,
            expected_package_sha256=artifacts.compiled.report.package_sha256,
        )
    except (OSError, ValueError, ValidationError, IdentityManifestError) as exc:
        obs_note = _normalize_diagnostic(f"observation failed: {exc}", loaded.project_dir)
        try:
            record_state(
                loaded.project_dir,
                variant,
                backend,
                "observation_incomplete",
                note=obs_note,
                inputs_digest=artifacts.result.inputs_digest,
                plan_sha256=plan_sha256,
                protected_inputs=prepared.protected_input_paths,
            )
        except (OSError, ValueError) as record_exc:
            typer.echo(f"Error: {record_exc}", err=True)
            raise typer.Exit(code=1) from record_exc
        typer.echo(f"Error: observation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    observed_note = (
        f"anchors={len(observation.report.anchors)} "
        f"package_sha256={observation.report.package_sha256[:12]}"
    )
    try:
        record_state(
            loaded.project_dir,
            variant,
            backend,
            "observed",
            note=observed_note,
            inputs_digest=artifacts.result.inputs_digest,
            plan_sha256=plan_sha256,
            protected_inputs=prepared.protected_input_paths,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    recast_report = build_recast_report(
        project_id=loaded.project.project.id,
        variant=variant,
        backend=backend,
        package=observation.package,
        report=observation.report,
        take_path_relative=take_relative,
        take_sha256=take.sha256,
        observation_anchors=loaded.project.observation.anchors,
    )
    summary_markdown = render_recast_summary_markdown(recast_report)
    report_json_bytes = (
        json.dumps(
            recast_report.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    reports_dir = resolve_reports_dir(loaded, variant, backend)

    try:
        atomic_publish_bytes_bundle(
            reports_dir,
            {
                RECAST_REPORT_FILENAME: report_json_bytes,
                RECAST_SUMMARY_FILENAME: summary_markdown.encode("utf-8"),
            },
            protected_inputs=prepared.protected_input_paths,
        )
    except (OSError, ValueError) as exc:
        obs_note = _normalize_diagnostic(f"report publish failed: {exc}", loaded.project_dir)
        try:
            record_state(
                loaded.project_dir,
                variant,
                backend,
                "observation_incomplete",
                note=obs_note,
                inputs_digest=artifacts.result.inputs_digest,
                plan_sha256=plan_sha256,
                protected_inputs=prepared.protected_input_paths,
            )
        except (OSError, ValueError) as record_exc:
            typer.echo(f"Error: {record_exc}", err=True)
            raise typer.Exit(code=1) from record_exc
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    reported_note = os.path.relpath(reports_dir / RECAST_SUMMARY_FILENAME, loaded.project_dir)
    try:
        record_state(
            loaded.project_dir,
            variant,
            backend,
            "reported",
            note=reported_note,
            inputs_digest=artifacts.result.inputs_digest,
            plan_sha256=plan_sha256,
            protected_inputs=prepared.protected_input_paths,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    console.print(
        f"[green]Observation report published to {reports_dir} "
        f"(verified={recast_report.coverage.verified} "
        f"violated={recast_report.coverage.violated} "
        f"not_observed={recast_report.coverage.not_observed})[/green]"
    )
    console.print(f"Next step: {reports_dir / RECAST_SUMMARY_FILENAME} を確認してください。")


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


@recast_app.command("init")
def recast_init_cmd(
    audio: str = typer.Argument(..., help="Path to source audio (WAV/MP3) to extract from"),
    project_dir: str = typer.Option(
        ..., "--project-dir", help="Directory to create the new recast project in (must be empty)"
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help=(
            "Prompt for semantic.core / semantic.avoid (up to 3 questions). "
            "--no-interactive leaves them as TODO sentinels."
        ),
    ),
) -> None:
    """Initialize a new recast project from a source audio file.

    Extracts an `RPEBundle` (`svp_rpe.rpe.extractor.extract_rpe_from_file`,
    default options only — no learned-model extras), drafts a
    `CompositionScore` (`svp_rpe.transcribe.score_draft.draft_score`) written
    as `composition_score.yaml`, and writes an `identity.yaml` whose only
    anchors are the ones the extraction can actually back: `harmony`
    (`identity/chord_progression.json`, `chord-sequence/0.1`, the exact
    canonical format `observe`'s harmony sensor reads) when the bundle has
    chord events, and `structure` (`identity/section_map.json`,
    `section-map/0.1`) when it has section markers. lyrics/melody anchors are
    never fabricated (Recast Phase 0 melody spike; no lyrics sensor input
    here) — `docs/recast_phase0_melody_spike.md`.

    Also writes a minimal `arrangements/default.yaml` (no-op `target`,
    `identity_anchors` declaring `harmony`/`structure` as `hard`) and a
    `project.yaml` (`recast-project/0.1`) declaring a `suno` manual backend
    (`prompt_only`, `mode_overrides: "suno"`) and a `deterministic` local
    backend, `policy.capability_mode: advisory`, and
    `observation.enabled: true` (PR5's ingest observe→report extension is
    opt-in per-project; `recast init` turns it on for the project it creates).

    Interactive by default (`--interactive`, the default): prompts up to 3
    questions (core / avoid / theme) and writes non-empty answers into
    `semantic.core` (theme is merged into core) / `semantic.avoid`. Blank
    answers are left as TODO sentinels — `--no-interactive` always leaves
    them as TODO. Either way, an unresolved TODO makes the first `recast
    plan` reach `blocked_authoring` (existing gate, unchanged) — this is the
    acceptance test for the TODO-preserving path, not a bug.

    Fail-closed: refuses to write into a `--project-dir` that already exists
    and is non-empty (does not overwrite). All output is built in a staging
    directory (sibling of `--project-dir`, same filesystem) and only
    published via an atomic rename after the whole project validates
    (`load_recast_project`) — any failure partway through (extraction error,
    an aborted interactive prompt, a schema error, ...) leaves `--project-dir`
    untouched, never a half-initialized directory that then blocks a retry.
    """
    import shutil

    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.observe import CHORD_SEQUENCE_ARTIFACT_SCHEMA
    from svp_rpe.arrange.section_map import SECTION_MAP_ARTIFACT_SCHEMA_0_1
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.rpe.extractor import extract_rpe_from_file
    from svp_rpe.transcribe.score_draft import (
        TODO_AUTHOR_INPUT,
        draft_score,
        render_draft_score_yaml,
    )

    audio_path = Path(audio)
    if not audio_path.is_file():
        typer.echo(f"Error: audio file not found: {audio_path}", err=True)
        raise typer.Exit(code=1)

    dest_dir = Path(project_dir)

    def _reject_nonempty_dest() -> None:
        if dest_dir.exists():
            if not dest_dir.is_dir():
                typer.echo(
                    f"Error: --project-dir exists and is not a directory: {dest_dir}", err=True
                )
                raise typer.Exit(code=1)
            if any(dest_dir.iterdir()):
                typer.echo(
                    f"Error: --project-dir already has files, refusing to overwrite: {dest_dir}",
                    err=True,
                )
                raise typer.Exit(code=1)

    _reject_nonempty_dest()

    # Codex P2 (#210, AGENTS §8-A 項目1): source audio を **1 回だけ**
    # read_bytes する（TOCTOU 排除）。この同一 bytes から (1) sha256（identity
    # manifest の source pin）を計算し、(2) staging 内 `source/` へスナップ
    # ショットとして書き出し、(3) 抽出はそのスナップショットに対して実行する
    # — 元の `audio_path` を実行中に差し替えても、抽出結果/コピー済み音声/pin
    # された sha256 が食い違わない（3 者が同一 bytes を消費する）。
    try:
        audio_bytes = audio_path.read_bytes()
    except OSError as exc:
        typer.echo(f"Error: failed to read {audio_path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    audio_sha256 = hashlib.sha256(audio_bytes).hexdigest()
    source_filename = audio_path.name

    # Codex P2 (#210 round 2 指摘3): 全出力を staging ディレクトリ（`dest_dir`
    # の親配下 = 同一ファイルシステム、`os.replace` の atomic rename 前提）で
    # 構築し、`load_recast_project` による最終検証まで成功した後にのみ
    # `dest_dir` へ atomic 公開する。途中失敗（抽出失敗・対話式入力の中断
    # (`click.exceptions.Abort`/`EOFError` を含む — Python の `finally` は
    # 例外の型を問わず必ず実行される)・schema 検証失敗等）は `finally` で
    # staging を後始末するだけで済み、「非空のため再実行不能な部分初期化」を
    # `dest_dir` に残さない（旧実装は抽出失敗の後始末のみ個別に持っていた —
    # 本方式へ統一する）。
    try:
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        typer.echo(f"Error: failed to create {dest_dir.parent}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        staging_dir = Path(tempfile.mkdtemp(prefix=f".{dest_dir.name}.", dir=dest_dir.parent))
    except OSError as exc:
        typer.echo(
            f"Error: failed to create staging directory under {dest_dir.parent}: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    published = False
    try:
        (staging_dir / "source").mkdir(parents=True, exist_ok=True)
        snapshot_path = staging_dir / "source" / source_filename
        snapshot_path.write_bytes(audio_bytes)

        try:
            bundle = extract_rpe_from_file(str(snapshot_path))
        except (OSError, ValueError) as exc:
            typer.echo(f"Error: failed to extract from {audio_path}: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        score = draft_score(bundle)

        if interactive:
            core_answer = typer.prompt(
                "この曲の核 (semantic.core)", default="", show_default=False
            )
            avoid_answer = typer.prompt(
                "避けたいもの (semantic.avoid, カンマ区切り)", default="", show_default=False
            )
            theme_answer = typer.prompt(
                "主題/題材 (任意。core に併合されます)", default="", show_default=False
            )
            core_parts = [part.strip() for part in (core_answer, theme_answer) if part.strip()]
            avoid_list = [item.strip() for item in avoid_answer.split(",") if item.strip()]
            semantic_updates: dict[str, Any] = {}
            if core_parts:
                semantic_updates["core"] = "; ".join(core_parts)
            if avoid_list:
                semantic_updates["avoid"] = avoid_list
            if semantic_updates:
                score = score.model_copy(
                    update={"semantic": score.semantic.model_copy(update=semantic_updates)}
                )

        slug = _slugify(audio_path.stem) or "recast-project"

        (staging_dir / "identity").mkdir(parents=True, exist_ok=True)
        (staging_dir / "arrangements").mkdir(parents=True, exist_ok=True)

        # Codex P2（#210 round 4 指摘6）: hash 対象になる全ファイルは encode を
        # **1 回だけ**行い、その同一 bytes を `write_bytes` と sha256 計算の
        # 両方に使う（pr2 abc2350 と同原則 — 従来の `write_text(...,
        # encoding="utf-8")` は Windows では既定の text-mode 改行変換で
        # `"\n"` → `"\r\n"` に化け、encode 済み文字列から計算した sha256 と
        # 実際にディスクへ書かれた bytes が乖離しうる）。hash 対象でない
        # ファイルも一貫性のため同じ bytes 書き込み経路に揃える。
        composition_score_bytes = render_draft_score_yaml(score).encode("utf-8")
        (staging_dir / "composition_score.yaml").write_bytes(composition_score_bytes)

        identity_anchors: list[dict[str, Any]] = []
        identity_anchor_policies: dict[str, dict[str, Any]] = {}

        if bundle.physical.chord_events:
            chord_payload = {
                "schema": CHORD_SEQUENCE_ARTIFACT_SCHEMA,
                "chords": [
                    {"root": event.root, "quality": event.quality}
                    for event in bundle.physical.chord_events
                ],
            }
            chord_json_bytes = (
                json.dumps(chord_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            (staging_dir / "identity" / "chord_progression.json").write_bytes(
                chord_json_bytes
            )
            identity_anchors.append(
                {
                    "id": "harmony",
                    "domain": "harmony",
                    "artifact": "identity/chord_progression.json",
                    "artifact_type": "chord_sequence_json",
                    "media_type": "application/json",
                    "format_version": CHORD_SEQUENCE_ARTIFACT_SCHEMA,
                    "sha256": hashlib.sha256(chord_json_bytes).hexdigest(),
                    "required": True,
                }
            )
            identity_anchor_policies["harmony"] = {"mode": "hard", "allow": []}

        if bundle.physical.structure:
            section_payload = {
                "schema_version": SECTION_MAP_ARTIFACT_SCHEMA_0_1,
                "sections": [marker.label for marker in bundle.physical.structure],
            }
            section_json_bytes = (
                json.dumps(section_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            (staging_dir / "identity" / "section_map.json").write_bytes(section_json_bytes)
            identity_anchors.append(
                {
                    "id": "structure",
                    "domain": "structure",
                    "artifact": "identity/section_map.json",
                    "artifact_type": "section_map",
                    "media_type": "application/json",
                    "format_version": SECTION_MAP_ARTIFACT_SCHEMA_0_1,
                    "sha256": hashlib.sha256(section_json_bytes).hexdigest(),
                    "required": True,
                }
            )
            identity_anchor_policies["structure"] = {"mode": "hard", "allow": []}

        identity_payload: dict[str, Any] = {
            "schema_version": "identity-manifest/0.1",
            "meta": {"work_id": slug, "version": "0.1"},
            "source": {
                "locator": f"source/{source_filename}",
                "sha256": audio_sha256,
                "rights_basis": "unknown",
            },
            "anchors": identity_anchors,
        }
        identity_yaml_bytes = yaml.safe_dump(
            identity_payload, sort_keys=False, allow_unicode=True
        ).encode("utf-8")
        (staging_dir / "identity.yaml").write_bytes(identity_yaml_bytes)

        preservation_payload: dict[str, Any] = {"score_fields": {}}
        if identity_anchor_policies:
            preservation_payload["identity_anchors"] = identity_anchor_policies
        arrangement_payload: dict[str, Any] = {
            "meta": {
                "id": f"{slug}-default-v1",
                "version": "0.1",
                "description": "svprpe recast init が生成した無変更の最小 ArrangementSpec 雛形。",
            },
            "target": {},
            "preservation": preservation_payload,
        }
        arrangement_yaml_bytes = yaml.safe_dump(
            arrangement_payload, sort_keys=False, allow_unicode=True
        ).encode("utf-8")
        (staging_dir / "arrangements" / "default.yaml").write_bytes(arrangement_yaml_bytes)

        project_payload: dict[str, Any] = {
            "schema_version": "recast-project/0.1",
            "project": {"id": slug, "builds_root": "builds"},
            "work": {"score": "composition_score.yaml", "identity_manifest": "identity.yaml"},
            "variants": {"default": {"arrangement": "arrangements/default.yaml"}},
            "backends": {
                "suno": {
                    "capability_profile": "suno",
                    "invocation": "manual",
                    "invocation_mode": "prompt_only",
                    "mode_overrides": "suno",
                },
                "deterministic": {
                    "capability_profile": "deterministic",
                    "invocation": "local",
                    "invocation_mode": "prompt_only",
                },
            },
            "policy": {
                "capability_mode": "advisory",
                "require_author_fields_resolved": True,
                "require_verified_package": True,
            },
            "observation": {"enabled": True, "anchors": []},
        }
        project_yaml_bytes = yaml.safe_dump(
            project_payload, sort_keys=False, allow_unicode=True
        ).encode("utf-8")
        (staging_dir / "project.yaml").write_bytes(project_yaml_bytes)

        try:
            loaded = load_recast_project(staging_dir / "project.yaml")
        except (RecastError, ValidationError, yaml.YAMLError) as exc:
            typer.echo(f"Error: generated recast project failed to load: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        # rename 直前の再確認（Codex P2 #210 round 2 指摘3）: staging 構築中に
        # 他プロセスが dest_dir を作成/汚染していないか、atomic move 直前に
        # もう一度確認し TOCTOU 窓を最小化する。
        _reject_nonempty_dest()
        if dest_dir.exists():
            # 直前確認を通過した時点で「存在するが空」の場合のみここに到達する
            # （非空/非ディレクトリは _reject_nonempty_dest が既に exit 1 済み）。
            # 空ディレクトリを明示的に rmdir してから rename する方が
            # `os.replace` の挙動をプラットフォーム非依存で読みやすくする。
            dest_dir.rmdir()

        try:
            os.replace(str(staging_dir), str(dest_dir))
        except OSError as exc:
            typer.echo(f"Error: failed to publish project to {dest_dir}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        published = True
    finally:
        if not published:
            shutil.rmtree(staging_dir, ignore_errors=True)

    console.print(f"[green]Recast project initialized at {dest_dir}[/green]")
    console.print(f"work_id: {slug}")
    console.print(
        f"variants: {sorted(loaded.project.variants)}  backends: {sorted(loaded.project.backends)}"
    )
    if score.semantic.core == TODO_AUTHOR_INPUT:
        console.print(
            "[yellow]semantic.core は未入力 (TODO) のままです。"
            "composition_score.yaml を編集するか --interactive で再実行するまで "
            "svprpe recast plan は blocked_authoring になります。[/yellow]"
        )
    console.print(
        "Next step: svprpe recast plan "
        f"{dest_dir / 'project.yaml'} --variant default --backend suno"
    )
