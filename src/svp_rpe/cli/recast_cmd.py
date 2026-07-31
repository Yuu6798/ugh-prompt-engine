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


def _orders_digest_matches(
    loaded: Any, variant: str, backend: str, recorded_orders_digest: Optional[str]
) -> bool:
    """`recast status`（Codex P2 review round 10 指摘12）: `awaiting_generation`
    の manual backend run に対し、注文書ディレクトリの現在の
    `compute_orders_digest` を再計算し `recorded_orders_digest`（`recast run`
    が pin した値）と突き合わせる。`recorded_orders_digest` が `None`（pin
    なし・旧 state）、orders ディレクトリが読めない（不在・欠落ファイル
    等）、または digest が不一致のいずれでも `False`（stale）を返す —
    `recast ingest` 自身の collect 前 precheck（本ファイル内、同じ
    `compute_orders_digest`/`resolve_orders_dir` を使う）と同じ fail-closed
    判定を、実行前に status で先出しする。"""
    from svp_rpe.recast.backends.manual import compute_orders_digest
    from svp_rpe.recast.run_paths import resolve_orders_dir

    if recorded_orders_digest is None:
        return False
    order_dir = resolve_orders_dir(loaded, variant, backend)
    try:
        current_orders_digest = compute_orders_digest(order_dir)
    except (OSError, ValueError):
        return False
    return current_orders_digest == recorded_orders_digest


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
    同じ bytes から hash も計算する single-source 設計に統一する）。

    薄いラッパー — 実体は `svp_rpe.utils.atomic_io.atomic_write_bytes` へ集約済み。
    """
    from svp_rpe.utils.atomic_io import atomic_write_bytes

    atomic_write_bytes(path, content)


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


def _validate_variant_backend_declared(loaded: Any, variant: str, backend: str) -> None:
    """`variant`/`backend` が project.yaml に宣言されていることを確認する
    （Codex P2 review round 14, PR5 #210 指摘19）。

    `plan`/`run`/`ingest` の 3 CLI コマンドはいずれも preflight
    （`_preflight_reject_plan_state_output_collision`）を最初に呼ぶが、その
    内部の `collect_protected_input_paths` は `loaded.arrangement_paths
    [variant]`/`loaded.capability_profile_paths[backend]` を dict 添字で直接
    引く。`--variant`/`--backend` に typo があると、この preflight が
    `build_recast_plan_artifacts` 内の既存 actionable な検証（`recast/
    plan.py` の `if variant not in project.variants: raise RecastError(...)`
    等）へ到達する前に生 `KeyError` を送出し、CLI の except 節が拾わない
    内部エラー（traceback 付き）に落ちてしまう。

    この関数を preflight の**前**に呼び、`recast/plan.py` と同一文言の
    `RecastError` を actionable に送出する（呼び出し側の既存
    `except (..., RecastError, ...)` にそのまま乗る — 新しい except 節は
    不要）。`collect_protected_input_paths` 自身にも同型の検証を追加した
    （defense in depth — 本関数を経由しない将来の呼び出し経路への備え）。
    """
    from svp_rpe.recast import RecastError

    project = loaded.project
    if variant not in project.variants:
        raise RecastError(
            f"recast project '{project.project.id}': unknown variant {variant!r} "
            f"(declared: {sorted(project.variants)})"
        )
    if backend not in project.backends:
        raise RecastError(
            f"recast project '{project.project.id}': unknown backend {backend!r} "
            f"(declared: {sorted(project.backends)})"
        )


def _preflight_reject_plan_state_output_collision(loaded: Any, variant: str, backend: str) -> None:
    """`<project_dir>/recast_plan.json`（便宜コピー）/
    `<builds_root>/plans/<variant>@<backend>/recast_plan.json`（正典
    per-run 公開先、Codex P2 review, PR #212 指摘）/`recast_state.json`
    （`recast plan`/`run`/`ingest` が最終的に書き込む出力ファイル群）が、この
    (variant, backend) run の保護対象入力パス一式（`collect_protected_
    input_paths`）のいずれかと alias していないかを、**plan 束を構築する
    （＝ `publish=True` で packages/ へ公開する）前**に検査する（Codex P2
    review round 15, PR3 #208 指摘30）。

    従来この種の alias は `_publish_recast_plan`/`record_state` それぞれの
    公開直前ガード（`ValueError` で fail-closed）でしか検出されなかった。
    しかし `build_recast_plan_artifacts(publish=True)` は verification/mode
    gate 通過後に packages/ を**先に**公開する設計（round 11 指摘21）であり、
    この packages 公開自体には `recast_plan.json`/`recast_state.json` との
    alias ガードが（そもそも対象外なので）存在しない。そのため、例えば
    project.yaml の `work.score` が誤って `<project_dir>/recast_state.json`
    を指すような構成では、`record_state` が最終的に衝突を検出して拒否
    しても、その前段で既に packages/ の公開自体は成功してしまっており、
    「plan/state のどちらにも一切対応しない成果物」が builds_root に残る
    ことになる。この preflight はそれを、packages 公開の前段（plan 束の
    構築そのものより前）で fail-closed に止める。

    `collect_protected_input_paths`（`recast/run_paths.py`）を直接呼ぶ
    純パス比較のみ（plan 束をまだ構築していないため
    `RecastPlanResult.protected_inputs` は利用できない — 呼び出しコストは
    identity manifest の 1 回の read/parse のみで、同関数は manifest
    破損時も例外を送出せず degrade する契約を既に持つ）。

    衝突を検出した場合はエラーメッセージを出力して `typer.Exit(code=1)` を
    送出する（呼び出し側は何も書かれていないことを保証される）。

    既存の `_publish_recast_plan`/`record_state` 内ガードは撤去しない —
    本 preflight を経由しない将来の呼び出し経路に対する defense in depth
    として維持する。
    """
    from svp_rpe.recast.plan import RECAST_PLAN_FILENAME
    from svp_rpe.recast.run_paths import collect_protected_input_paths, resolve_plans_dir
    from svp_rpe.recast.state import RECAST_STATE_FILENAME

    resolved_inputs = {
        candidate.resolve()
        for candidate in collect_protected_input_paths(loaded, variant, backend)
    }
    for output_path in (
        loaded.project_dir / RECAST_PLAN_FILENAME,
        resolve_plans_dir(loaded, variant, backend) / RECAST_PLAN_FILENAME,
        loaded.project_dir / RECAST_STATE_FILENAME,
    ):
        if output_path.resolve() in resolved_inputs:
            typer.echo(
                f"Error: output path collides with a protected input path: {output_path}",
                err=True,
            )
            raise typer.Exit(code=1)


def _publish_recast_plan(
    loaded: Any, plan: Any, *, variant: str, backend: str, protected_inputs: list[Path]
) -> tuple[Path, str]:
    """`plan`（`RecastPlan`）を正典の per-run 位置
    `<builds_root>/plans/<variant>@<backend>/recast_plan.json`
    （`resolve_plans_dir` — packages/orders/takes/reports と同じ命名規約）へ
    canonical 形式（sorted keys, 2-space indent, trailing newline —
    deterministic）で atomic 公開し、publish したバイト列自身の sha256
    （`plan_sha256`）を返す。加えて `<project_dir>/recast_plan.json` へも
    同じ bytes を「最新診断の便宜コピー」として書き続けるが、こちらは
    pin・突合対象ではない（人がその場で最新の診断を覗くための利便性のみ）。

    レイアウト変更の経緯（Codex P2 review 3 巡目, PR #212 指摘）: 従来は
    project 直下の単一ファイルを正典としていたため、ある (variant, backend)
    の `recast plan`/`run` が別の (variant, backend) の plan で同じファイルを
    上書きし、前者が `awaiting_generation` のまま `plan_sha256` 突合に落ちて
    正当な外部生成 take を stale 拒否していた。`recast status`/`recast
    ingest` の `plan_sha256` 突合はこの正典 per-run ファイルへ向ける
    （`recast_status_cmd`/`recast_ingest_cmd` 側の呼び出し箇所参照）。

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
    必須引数（デフォルト値なし）。公開前に**両方の公開先**（正典 per-run
    ファイル + 便宜コピー）とこの集合との alias 検査を行い、いずれかが
    衝突すれば何も書かずに `ValueError` を送出する（fail-closed — 他公開
    サイトと同じ契約、Codex P2 review round 5, PR3 #208 指摘 10）。

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
    status` が偽 stale を報告しうる欠陥があった）。同じ bytes を 2 箇所へ
    書くため、この保証は便宜コピー側にも自動的に及ぶ。
    """
    from svp_rpe.recast.plan import RECAST_PLAN_FILENAME
    from svp_rpe.recast.run_paths import resolve_plans_dir

    canonical_plan_path = resolve_plans_dir(loaded, variant, backend) / RECAST_PLAN_FILENAME
    convenience_plan_path = loaded.project_dir / RECAST_PLAN_FILENAME

    resolved_protected = {candidate.resolve() for candidate in protected_inputs}
    for candidate_path in (canonical_plan_path, convenience_plan_path):
        if candidate_path.resolve() in resolved_protected:
            raise ValueError(f"output path collides with a protected input path: {candidate_path}")

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
    _write_recast_plan_atomically(canonical_plan_path, canonical_bytes)
    plan_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    # 便宜コピー: pin・突合対象ではないが、正典と同じ bytes を project 直下
    # にも書いておく（人がその場で最新診断を覗くための利便性のみ）。
    _write_recast_plan_atomically(convenience_plan_path, canonical_bytes)
    return canonical_plan_path, plan_sha256


# `recast plan` の再実行が降格させてはいけない進行状態（Codex P2 review,
# PR #212 指摘・recast_cmd.py:372 参照）。plan パイプライン
# （`build_recast_plan_artifacts`）自体は `compiled`/`verified`/`blocked_*`
# までしか到達しない — これらより先（生成段）に進んだ run を、入力が
# 実際には無変更のまま plan だけ再実行しただけで verified/compiled へ巻き
# 戻すのは、生成済み/収蔵済み take を無意味に失わせる（orders_digest も
# 一緒に失われ、次の `recast ingest` が拒否される）。
_PROGRESSED_RUN_STATES = frozenset(
    {"awaiting_generation", "generated", "observed", "reported"}
)


def _find_progressed_run_to_preserve(
    loaded: Any, variant: str, backend: str, *, inputs_digest: str, plan_sha256: str
) -> Optional[Any]:
    """`recast plan` の再実行前に、既存 run エントリが「進行状態」
    （`_PROGRESSED_RUN_STATES`）かつ入力・plan が実際には無変更（再計算した
    `inputs_digest` と公開した `plan_sha256` の両方が既存エントリと一致）で
    あれば、それをそのまま返す（呼び出し側はこれが非 None なら `record_state`
    を呼ばず既存エントリを温存する）。

    根拠: `observation` 節の分離（PR #212 の別指摘）と同じ精神 —
    plan パイプラインは入力一式（project/score/identity_manifest/
    arrangement spec/capability profile/mode_overrides/device profile）の
    純関数であり、`inputs_digest` が不変なら実際の解決結果（anchors/
    changed_fields/blocked 等）も不変で `plan_sha256`（決定論的 canonical
    JSON の hash）も同じ bytes になるはず。両方が一致する場合に限り「plan は
    診断として再実行されただけで、注文書・生成物の正当性には一切影響しない」
    と判断できる — 入力が実際に変わっていれば（どちらかが不一致）従来どおり
    plan 段の状態で上書きする（古い注文はどのみち stale で無効なため、
    降格が正しい）。

    `orders_digest` はこの判定に含めない: 一致条件が満たされる時点で
    `record_state` 自体を呼ばないため、既存エントリの `orders_digest` は
    その他のフィールド（`note`/`history` 等）ごと無条件で温存される。"""
    from svp_rpe.recast.state import load_recast_state

    state_file = load_recast_state(loaded.project_dir)
    existing = state_file.runs.get(f"{variant}@{backend}")
    if existing is None or existing.state not in _PROGRESSED_RUN_STATES:
        return None
    if existing.inputs_digest != inputs_digest or existing.plan_sha256 != plan_sha256:
        return None
    return existing


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
        # `--variant`/`--backend` の宣言確認（Codex P2 review round 14,
        # PR5 #210 指摘19 — `_validate_variant_backend_declared` docstring
        # 参照）: 直後の preflight が dict 添字で生 KeyError を送出する前に、
        # typo を actionable な RecastError として拒否する。
        _validate_variant_backend_declared(loaded, variant, backend)
        # packages/ への publish=True 公開前の preflight（Codex P2 review
        # round 15, PR3 #208 指摘30 — `_preflight_reject_plan_state_output_
        # collision` docstring 参照）: plan 束を構築する前に、この run が
        # 最終的に書く 2 出力（recast_plan.json/recast_state.json）が保護
        # 対象入力と alias していないかを検査する。
        _preflight_reject_plan_state_output_collision(loaded, variant, backend)
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
            loaded,
            result.plan,
            variant=variant,
            backend=backend,
            protected_inputs=result.protected_inputs,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # 状態記録は plan JSON の publish が成功した後にのみ行う（Codex P2 #207）:
    # 書き込み失敗時に stale な recast_state.json を残さないための順序保証。
    #
    # 入力・plan が実際には無変更のまま `recast plan` を再実行しただけの
    # 場合、既存 run が進行状態（awaiting_generation 以降）に到達済みなら
    # それを保持し record_state を呼ばない（Codex P2 review, PR #212 指摘・
    # `_find_progressed_run_to_preserve` docstring 参照 — plan パイプライン
    # 自体は compiled/verified までしか到達しないため、無条件に record_state
    # すると生成済み/収蔵済み take の pin（orders_digest 含む）を失う）。
    preserved_run = _find_progressed_run_to_preserve(
        loaded, variant, backend, inputs_digest=result.inputs_digest, plan_sha256=plan_sha256
    )
    if preserved_run is not None:
        console.print(
            f"[cyan]既存の進行状態を保持しました: {preserved_run.state}"
            "（入力・plan は無変更のため降格しません。recast_plan.json のみ再公開）[/cyan]"
        )
    else:
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

# `recast ingest` の再観測経路（Codex P2 review, PR #212 指摘）が受理する
# 到達済み状態。この状態にある run は take が既に収蔵済みであり、
# 収蔵済み take の再 collect は行わず observation 設定の再評価だけを行う
# （`recast_ingest_cmd` 内の `is_reobserve` 分岐参照）。`awaiting_generation`
# は対象外（そちらは従来どおり「未収蔵の take を collect する」新規 ingest）。
_REOBSERVABLE_STATES = frozenset({"generated", "observed", "reported"})


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
    from svp_rpe.recast.backends.manual import compute_orders_digest
    from svp_rpe.recast.plan import _normalize_diagnostic, build_recast_plan_artifacts
    from svp_rpe.recast.state import record_state

    try:
        loaded = load_recast_project(project_yaml)
        # `--variant`/`--backend` の宣言確認（Codex P2 review round 14,
        # PR5 #210 指摘19 — `recast plan` 側の同型コメント参照）。
        _validate_variant_backend_declared(loaded, variant, backend)
        # packages/ への publish=True 公開前の preflight（Codex P2 review
        # round 15, PR3 #208 指摘30 — `recast plan` 側の同型コメント参照）。
        _preflight_reject_plan_state_output_collision(loaded, variant, backend)
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
            loaded,
            artifacts.result.plan,
            variant=variant,
            backend=backend,
            protected_inputs=protected_inputs,
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
        #
        # `orders_digest`（Codex P2 review round 13, PR3 #208 指摘28）: 直上で
        # atomic 公開された 6 ファイルの content digest を計算し pin する —
        # `recast ingest` が collect 前にこの pin と disk 上の現在の 6 ファイル
        # を突合し、run 後に注文書が人手で編集されていないことを検証する。
        try:
            orders_digest = compute_orders_digest(prepared.order_dir)
        except OSError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
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
                orders_digest=orders_digest,
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


def _load_existing_take_for_reobserve(loaded: Any, variant: str, backend: str, supplied_audio: Path) -> Any:
    """再観測（`is_reobserve`、Codex P2 review, PR #212 指摘）専用: 収蔵済みの
    take を再 collect せず、disk 上の `take.json`（`ManualInvoker.collect`/
    `DeterministicInvoker.invoke` が publish 時に書く provenance）から情報を
    読み出して `GeneratedTake` を再構成する。

    fail-closed 契約（いずれも `RecastError` を送出 — 呼び出し側の既存
    `except (..., RecastError, ...)` にそのまま乗る）:
    - `takes_dir` に `take-01.*`（`take.json` を除く）がちょうど 1 件
      見つからない場合（take 自体が欠落/複数ある壊れた状態からの再観測は
      許さない — 通常の `awaiting_generation` → ingest 経路で正規に
      collect し直すべき）
    - `take.json` が読めない/`sha256` フィールドが無い場合
    - disk 上の take 音声の実 sha256 が `take.json` の宣言 sha256 と一致
      しない場合（collect 後の改変を許さない — 既存の inputs_digest/
      plan_sha256 pin と同じ fail-closed 精神）
    - `--audio` に渡された供給ファイルの sha256 が上記の実 sha256 と一致
      しない場合（再観測は「同じ take」に対してのみ許す — 別音源を
      `is_reobserve` 経路経由で忍び込ませ、orders_digest/collect の正規
      ガードを迂回させない。呼び出し側は次のコマンドの `--audio` に
      既存 take のパスをそのまま渡せばよい）
    """
    from svp_rpe.recast import RecastError
    from svp_rpe.recast.backend import GeneratedTake
    from svp_rpe.recast.run_paths import resolve_takes_dir

    takes_dir = resolve_takes_dir(loaded, variant, backend)
    candidates = sorted(p for p in takes_dir.glob("take-01.*") if p.name != "take.json")
    if len(candidates) != 1:
        raise RecastError(
            f"cannot re-observe {variant}@{backend}: expected exactly one existing take "
            f"(take-01.*) under {takes_dir}, found {len(candidates)}"
        )
    existing_audio_path = candidates[0]

    take_json_path = takes_dir / "take.json"
    try:
        take_record = json.loads(take_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecastError(
            f"cannot re-observe {variant}@{backend}: unreadable {take_json_path}: {exc}"
        ) from exc
    declared_sha256 = take_record.get("sha256") if isinstance(take_record, dict) else None
    if not isinstance(declared_sha256, str):
        raise RecastError(
            f"cannot re-observe {variant}@{backend}: {take_json_path} has no sha256 field"
        )

    actual_sha256 = hashlib.sha256(existing_audio_path.read_bytes()).hexdigest()
    if actual_sha256 != declared_sha256:
        raise RecastError(
            f"cannot re-observe {variant}@{backend}: existing take at {existing_audio_path} "
            "does not match its recorded take.json sha256 (tampered after collection)"
        )

    supplied_sha256 = hashlib.sha256(supplied_audio.read_bytes()).hexdigest()
    if supplied_sha256 != actual_sha256:
        raise RecastError(
            f"cannot re-observe {variant}@{backend}: supplied --audio "
            f"(sha256={supplied_sha256}) does not match the already-collected take "
            f"(sha256={actual_sha256}). Re-observation must reference the same take — "
            f"point --audio at {existing_audio_path}."
        )

    source = take_record.get("source")
    backend_name = take_record.get("backend_name")
    return GeneratedTake(
        audio_path=existing_audio_path,
        sha256=actual_sha256,
        source=source if source in ("local", "manual") else "manual",
        backend_name=backend_name if isinstance(backend_name, str) else backend,
        note="re-observed (same take, no re-collection)",
    )


def _ingest_prechecks(
    loaded: Any,
    variant: str,
    backend: str,
    run: Any,
    is_reobserve: bool,
) -> None:
    """precheck①②③（`recast_ingest_cmd` C4+C5+C6 抽出）: `recast status` と
    同じ fail-closed な staleness チェック（inputs_digest → plan_sha256 →
    orders_digest の順）を、collect/publish/record_state より前に行う。
    いずれかの不一致は `typer.Exit(1)` を送出し、ファイルには一切触れない。
    """
    from svp_rpe.recast import RecastError
    from svp_rpe.recast.backends.manual import compute_orders_digest
    from svp_rpe.recast.plan import RECAST_PLAN_FILENAME, compute_recast_inputs_digest
    from svp_rpe.recast.run_paths import resolve_orders_dir, resolve_plans_dir

    run_key = f"{variant}@{backend}"

    # 状態を信用する前の突合（`recast status` と同じ fail-closed 意味論、
    # Codex P2 review round 2 指摘 4 / PR2 の申し送り「run コマンド追加時は
    # plan_sha256 突合を先に行う」対応）: inputs_digest → plan_sha256 →
    # orders_digest の順（Codex P2 review round 13, PR3 #208 指摘28 で
    # orders_digest を追加）。
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

    # 正典 per-run plan ファイル（`resolve_plans_dir` — Codex P2 review,
    # PR #212 指摘）へ突合する。project 直下の `recast_plan.json` は便宜
    # コピーに過ぎず、別 (variant, backend) の plan/run で上書きされ得るため
    # pin 突合には使わない。
    current_plan_sha256 = _read_plan_sha256(
        resolve_plans_dir(loaded, variant, backend) / RECAST_PLAN_FILENAME
    )
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

    # orders_digest 突合（Codex P2 review round 13, PR3 #208 指摘28）: 注文書
    # 6 ファイルが `recast run` 公開時点から disk 上で一切変わっていないことを
    # 検証する。証拠保全のため、不一致・ファイル欠落いずれの場合も
    # republish（＝再公開そのものが編集痕跡を消してしまう）は一切行わず、
    # ここで即座に exit する — 以降のどの publish/collect/record_state にも
    # 到達しない。
    #
    # 再観測（`is_reobserve`）では対象外: take を再 collect しない（既存
    # take をそのまま使う）ため注文書そのものとは無関係。local backend の
    # run は元々 orders_digest を持たない（常に `None`）ため、これを
    # チェックすると再観測が local backend で常に拒否されてしまう。
    if not is_reobserve:
        order_dir = resolve_orders_dir(loaded, variant, backend)
        if run.orders_digest is None:
            typer.echo(
                f"Error: {run_key} has no recorded orders_digest (stale — no pin). "
                "Re-run 'svprpe recast plan' / 'svprpe recast run' first.",
                err=True,
            )
            raise typer.Exit(code=1)
        try:
            current_orders_digest = compute_orders_digest(order_dir)
        except OSError as exc:
            typer.echo(
                f"Error: {run_key} order files are missing or unreadable at {order_dir} "
                f"(cannot verify orders_digest, not re-publishing): {exc}",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        if current_orders_digest != run.orders_digest:
            typer.echo(
                f"Error: {run_key} order files changed since they were published by "
                "'svprpe recast run' (orders_digest mismatch — an order file was edited "
                "after publication). Not re-publishing (would erase the evidence of the "
                "edit). Re-run 'svprpe recast run' to publish a fresh order for the current "
                "inputs before generating and ingesting audio.",
                err=True,
            )
            raise typer.Exit(code=1)


def _precheck_observation_anchors(loaded: Any) -> tuple[Optional[set[str]], str]:
    """C7 抽出: `observation_digest` 計算 + `observation.anchors` 未知 id
    事前検査（`observation.enabled` 時のみ）。未知 anchor id があれば
    `typer.Exit(1)`（何も書かない、collect/publish/record_state より前）。
    """
    import yaml

    from svp_rpe.recast.plan import compute_observation_digest

    # `observation` 節全体の pin（Codex P2 review, PR #212 指摘）:
    # `observed`/`reported` を記録する際にこの digest を併せて永続化する —
    # `inputs_digest` は生成系の同一性判定であり `observation` 節を意図的に
    # 除外しているため、report にとっての直接の入力である `observation` 節
    # 自体は別の pin（`RecastRunState.observation_digest`）で追跡する
    # （single source: `recast/plan.py:compute_observation_digest`）。
    current_observation_digest = compute_observation_digest(loaded.project.observation)

    # `observation.anchors`（Codex P2, #211）: 非空なら観測対象を絞る。空
    # （既定）は「絞り込みなし」— 従来どおり全 manifest anchor を観測する。
    # スコープ構築・未知 id precheck とも `observation.enabled == true` の
    # ときのみ行う（Codex P2, #211 追加指摘）: `observation.enabled: false`
    # の project は元々 collect→`generated` で停止する契約（下の
    # `if not loaded.project.observation.enabled:` 分岐）で observe 自体に
    # 進まないため、`observation.anchors` の内容は無関係のはずが、無条件に
    # 先走らせると stale/typo な `observation.anchors` が残っているだけで
    # （observation 自体は無効なのに）collect 前に fail してしまっていた。
    anchor_scope: Optional[set[str]] = None
    if loaded.project.observation.enabled:
        anchor_scope = (
            set(loaded.project.observation.anchors)
            if loaded.project.observation.anchors
            else None
        )

        # 未知 id の事前検査（Codex P2, #210 round 9 指摘11; round 10 指摘13で
        # ここ＝collect/publish/record_state より前へ前倒し）: typo/削除済み
        # anchor を含む `observation.anchors` は設定ミスであり、実行時の観測
        # 失敗（`observation_incomplete`）とは別種のエラーとして扱う。これを
        # `invoker.collect()`/`record_state("generated")` の後（旧位置）に置くと、
        # 設定 typo であっても take の収蔵（外部生成音声の take-01.wav への
        # copy）と `generated` への state 遷移が先に確定してしまい、typo 修正後に
        # `awaiting_generation` からの再 ingest ができなくなる（take はもう
        # awaiting_generation の注文に対応しない、かつ orders_digest precheck が
        # 次回 ingest を弾く）。他の precheck 群（inputs_digest/plan_sha256/
        # orders_digest）と同じ「何も書かず exit」位置へ揃えることで、typo
        # 修正後にそのまま同じ take で ingest をやり直せる。
        # `observe_generated_artifact` 自身の同型ガード（防御的多重化・
        # `svprpe observe` 単体実行など他呼び出し元向け）とは別に、ここでは
        # state を一切変更せず（`observation_incomplete` を記録しない）plain
        # な Error + exit 1 とする。manifest の実 YAML 構造が壊れている場合は
        # ここでは判定せず、通常どおり `observe_generated_artifact` 側の
        # fail-closed 検証（`observation_incomplete` 経由）に委ねる。
        if anchor_scope is not None:
            manifest_bytes_for_scope_check = loaded.identity_manifest_path.read_bytes()
            try:
                raw_manifest_for_scope_check = yaml.safe_load(manifest_bytes_for_scope_check)
            except yaml.YAMLError:
                raw_manifest_for_scope_check = None
            if isinstance(raw_manifest_for_scope_check, dict) and isinstance(
                raw_manifest_for_scope_check.get("anchors"), list
            ):
                known_anchor_ids = {
                    entry.get("id")
                    for entry in raw_manifest_for_scope_check["anchors"]
                    if isinstance(entry, dict)
                }
                unknown_anchor_ids = anchor_scope - known_anchor_ids
                if unknown_anchor_ids:
                    typer.echo(
                        "Error: project.yaml observation.anchors contains anchor id(s) "
                        f"not present in the identity manifest: {sorted(unknown_anchor_ids)} "
                        f"(known anchor ids: {sorted(known_anchor_ids)})",
                        err=True,
                    )
                    raise typer.Exit(code=1)

    return anchor_scope, current_observation_digest


def _rebuild_and_publish_plan(
    loaded: Any,
    variant: str,
    backend: str,
    run: Any,
) -> tuple[Any, str]:
    """C8+C9+C10+C11 抽出: plan 再構築（`publish=False`）→ TOCTOU 再突合 →
    packages 公開 → 正典 `recast_plan.json` 公開を 1 関数に温存する
    （fail-closed 連鎖: 突合を通過するまで packages/plan いずれも公開しない
    — `recast_ingest_cmd` docstring 参照）。"""
    import yaml
    from pydantic import ValidationError

    from svp_rpe.arrange.package import COMPILATION_REPORT_FILENAME, PERFORMANCE_PACKAGE_FILENAME
    from svp_rpe.arrange.resolver import ArrangementError
    from svp_rpe.recast import RecastError
    from svp_rpe.recast.plan import _atomic_publish_text_bundle, build_recast_plan_artifacts
    from svp_rpe.recast.run_paths import resolve_packages_dir

    run_key = f"{variant}@{backend}"

    # `publish=False`（Codex P2 review round 13, PR3 #208 指摘27; pr5 #210
    # round 3 指摘4 の同型修正 127891c と実装形を寄せてある）: 突合前に
    # `builds/packages/<variant>@<backend>/` を上書きしない。下の digest
    # 再突合が reject した場合、packages・plan・state のいずれも無変更のまま
    # exit する契約を保つ。
    try:
        artifacts = build_recast_plan_artifacts(
            loaded, variant=variant, backend=backend, publish=False
        )
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError, ArrangementError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # rebuild 後・publish/collect の前に、rebuild が実際に見た入力の digest を
    # 記録済み pin（`run.inputs_digest` — 上の precheck で `current_digest` との
    # 一致を既に確認済み）と再突合する（Codex P2 eighth round #207 指摘16:
    # precheck（上の `compute_recast_inputs_digest`）と rebuild（直上の
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
    # plan/state/packages は無傷のまま。`publish=False` にしたことで
    # packages/ もこの契約に含まれる — 指摘27）。
    if artifacts.result.inputs_digest != run.inputs_digest:
        typer.echo(
            f"Error: {run_key} inputs changed since the order files were published "
            "(detected during rebuild — stale). Re-run 'svprpe recast plan' / "
            "'svprpe recast run' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    # 突合を通過して初めて package/report を publish する（Codex P2 review
    # round 13, PR3 #208 指摘27 — pr5 #210 の同型修正 127891c と実装形を
    # 寄せてある）: `build_recast_plan_artifacts` を `publish=True` で
    # 再実行せず、上の `publish=False` 呼び出しが既に確定させた
    # `artifacts.compiled`（in-memory の compile 済み成果物 — state_reached
    # が compiled/verified のときのみ non-None）をそのまま書き出す「公開
    # 専用」の呼び出しに留める。二重コンパイルも新たな読み取り機会（＝新たな
    # TOCTOU 窓）も増やさない。
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
            loaded,
            artifacts.result.plan,
            variant=variant,
            backend=backend,
            protected_inputs=artifacts.result.protected_inputs,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    return artifacts, plan_sha256


def _resolve_and_collect_take(
    loaded: Any,
    variant: str,
    backend: str,
    artifacts: Any,
    is_reobserve: bool,
    audio: str,
) -> tuple[Any, Any]:
    """C12 抽出: invoker 解決 + `base_prepared_invocation` + take collect
    （`is_reobserve` なら既存 take を再利用）。"""
    import yaml
    from pydantic import ValidationError

    from svp_rpe.recast import RecastError
    from svp_rpe.recast.backend import (
        base_prepared_invocation,
        resolve_invoker,
        run_context_from_plan_artifacts,
    )

    try:
        # `profile` は plan 段が single-read 束で既に parse・validate 済みの
        # `artifacts.profile` をそのまま使う（Codex P2 review round 12, PR3
        # #208 指摘24 — 詳細は `recast run` 側の同型コメント参照）。
        ctx = run_context_from_plan_artifacts(
            loaded, variant=variant, backend=backend, artifacts=artifacts
        )
        invoker = resolve_invoker(artifacts.backend_ref, ctx.profile)
        # `invoker.prepare(ctx)` ではなく `base_prepared_invocation(ctx)` を
        # 直接呼ぶ（Codex P2 review round 13, PR3 #208 指摘28）: 上の
        # orders_digest 突合により disk 上の 6 ファイルが pin と一致すると
        # 既に確認済みのため、`ManualInvoker.prepare()` の無条件再公開は
        # 不要（`一致時も republish は不要` — 指示書どおり）。`prepare()` を
        # 呼ばないことで、万一ここより後で drift が生じても再公開そのものが
        # 証拠を上書きすることはない。
        prepared = base_prepared_invocation(ctx)
        # 再観測（`is_reobserve`）: 収蔵済みの take を再 collect せず、disk 上
        # の provenance（`take.json`）から同じ take を再構成する。供給された
        # `--audio` が既存 take と一致しない場合は `RecastError`（`_load_
        # existing_take_for_reobserve` docstring 参照）— 別音源をこの経路
        # 経由で忍び込ませ、orders_digest/collect の正規ガードを迂回させない。
        if is_reobserve:
            take = _load_existing_take_for_reobserve(loaded, variant, backend, Path(audio))
        else:
            take = invoker.collect(prepared, Path(audio))
    except (OSError, ValueError, ValidationError, yaml.YAMLError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    return take, prepared


def _observe_and_report(
    loaded: Any,
    variant: str,
    backend: str,
    artifacts: Any,
    take: Any,
    prepared: Any,
    package_path: Path,
    take_relative: str,
    anchor_scope: Optional[set[str]],
    plan_sha256: str,
    current_observation_digest: str,
) -> None:
    """C16+C17+C18+C19 抽出: `observe_generated_artifact` 呼び出し →
    `observed` state 記録 → `recast_report`/`recast_summary.md` 構築+公開 →
    `reported` state 記録。`observation_incomplete` 記録の重複 2 箇所
    （observe 失敗時 / report publish 失敗時）はそのまま重複を保存する
    （共通化しない）。"""
    from pydantic import ValidationError

    from svp_rpe.arrange.identity import IdentityManifestError
    from svp_rpe.arrange.observe import observe_generated_artifact
    from svp_rpe.recast import RecastError
    from svp_rpe.recast.backend import atomic_publish_bytes_bundle
    from svp_rpe.recast.experimental import collect_melody_experimental_anchors
    from svp_rpe.recast.plan import _normalize_diagnostic
    from svp_rpe.recast.report import (
        RECAST_REPORT_FILENAME,
        RECAST_SUMMARY_FILENAME,
        build_recast_report,
        render_recast_summary_markdown,
    )
    from svp_rpe.recast.run_paths import resolve_reports_dir
    from svp_rpe.recast.state import record_state

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
            anchor_scope=anchor_scope,
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
                observation_digest=current_observation_digest,
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
            observation_digest=current_observation_digest,
            protected_inputs=prepared.protected_input_paths,
        )
    except (OSError, ValueError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # M4c (Design Memo M4 §5): axis_policy 付き melody anchor があれば
    # experimental 節を組み立てる。main の anchors/coverage（上の
    # `observation.report`/`build_recast_report` 呼び出し）には一切触れない
    # ——会計分離。抽出器注入 seam（`route_runner`）は library 関数のみが
    # 露出し、CLI からは常に既定（実抽出器）を使う。
    try:
        experimental_anchors = collect_melody_experimental_anchors(
            contract=artifacts.contract,
            melody_config=loaded.project.observation.melody,
            project_dir=loaded.project_dir,
            backend_ref=artifacts.backend_ref,
            score=artifacts.derived_score,
            channel_artifact_bytes=artifacts.channel_artifact_bytes,
            take_audio_path=take.audio_path,
        )
    except (OSError, ValueError, RecastError) as exc:
        obs_note = _normalize_diagnostic(
            f"melody experimental observation failed: {exc}", loaded.project_dir
        )
        try:
            record_state(
                loaded.project_dir,
                variant,
                backend,
                "observation_incomplete",
                note=obs_note,
                inputs_digest=artifacts.result.inputs_digest,
                plan_sha256=plan_sha256,
                observation_digest=current_observation_digest,
                protected_inputs=prepared.protected_input_paths,
            )
        except (OSError, ValueError) as record_exc:
            typer.echo(f"Error: {record_exc}", err=True)
            raise typer.Exit(code=1) from record_exc
        typer.echo(f"Error: melody experimental observation failed: {exc}", err=True)
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
        experimental_anchors=experimental_anchors,
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
                observation_digest=current_observation_digest,
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
            observation_digest=current_observation_digest,
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
    must match the current inputs, the recorded `plan_sha256` must match the
    current `recast_plan.json` bytes, and the recorded `orders_digest` must
    match the current 6 order files on disk (Codex P2 review round 13, PR3
    #208 指摘28 — `recast/backends/manual.py:compute_orders_digest`; missing/
    unreadable counts as a mismatch in every case) — any mismatch is reported
    and exits 1 without touching any file. A `None` `inputs_digest`/
    `plan_sha256`/`orders_digest` (e.g. an old-schema or hand-copied state) is
    *not* trusted as "unknown, skip the check" — it is treated as stale too
    (Codex P2 review round 4, PR3 #208 指摘 9: a missing pin is exactly the
    case a stale/forged state most plausibly has). Only then does it rebuild
    the plan context via the same `build_recast_plan_artifacts` path
    `recast run` uses — with `publish=False` (Codex P2 review round 13, PR3
    #208 指摘27: publishing `builds/packages/<variant>@<backend>/` before the
    rebuild's own freshly recomputed `inputs_digest` is re-compared against
    the recorded pin let a swapped-input rebuild overwrite `packages/` even
    when the digest re-check below was about to reject the run). Before that
    rebuild's result is trusted (published to `packages/`, re-published as
    `recast_plan.json`, or used to `collect` the take), its freshly
    recomputed `inputs_digest` is re-compared against the same recorded pin
    (Codex P2 eighth round #207 指摘16: the precheck above and this rebuild
    are not atomic — inputs could be swapped in the gap between them, letting
    an old order's externally generated audio get recorded as the `generated`
    take for a *new* plan built from the swapped inputs). A mismatch here
    exits 1 without publishing `packages/`, the plan, collecting the audio, or
    touching `recast_state.json` — the old packages/plan/state are left
    untouched. Only once that re-check passes does it publish `packages/`
    (from the same in-memory compile the `publish=False` rebuild already
    produced — no recompile), re-publish `recast_plan.json` (same
    `_publish_recast_plan` single source), resolve the `ManualInvoker`, and
    call `collect(audio)` to atomically ingest the audio into
    `<builds_root>/takes/<variant>@<backend>/`. Records `generated` (with the
    freshly (re)computed `inputs_digest`/`plan_sha256`) only after that
    publish succeeds — this is the command `next_command.txt`/`order_sheet.md`
    (`ManualInvoker`) advertise.

    Order files are never re-published by this command (Codex P2 review
    round 13, PR3 #208 指摘28): `ManualInvoker.prepare()` unconditionally
    re-publishes all 6 order files, which would silently erase any manual
    edit made to them after `recast run` — an edited `prompt.json` (say) that
    the externally generated audio was actually produced from would be
    overwritten back to the rebuilt "canonical" content right before
    `collect`, letting tampered-order audio get accepted as if it came from
    the pristine, originally published order. This command instead calls
    `base_prepared_invocation` (the same field assembly `prepare()` uses
    internally, minus the publish side effect) once the `orders_digest`
    precheck above has confirmed the 6 files on disk are byte-identical to
    what was pinned at `recast run` time.

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
    未知 anchor id（identity manifest に存在しない id）を宣言した project は、
    本コマンド冒頭の他 precheck 群（inputs_digest/plan_sha256/orders_digest、
    下記参照）と同じ「collect/publish/record_state より前・何も書かず exit」
    位置で設定エラーとして拒否される（Codex P2, #210 round 9 指摘11 → round
    10 指摘13 でこの位置へ前倒し — 旧位置（collect 後）だと typo 修正後に
    `awaiting_generation` から同じ take で再 ingest できなくなっていた）。
    `recast plan`/`recast run` の plan 段はこの検証を行わない — manual
    backend が注文書公開・`awaiting_generation` まで進めることを優先し、
    観測スコープの妥当性は ingest 冒頭でのみ判定する設計。plain な Error +
    exit 1 とし、state は `awaiting_generation` のまま変更しない
    （`observation_incomplete`— 観測実行時の実測失敗用の state — は記録
    しない。設定ミスと実行時失敗を state レベルで区別する）。
    `observe_generated_artifact` 自身にも同型の防御的ガードがある
    （`svprpe observe` 単体実行など他呼び出し元向け）。

    Scope（再観測、Codex P2 review, PR #212 指摘）: `(variant, backend)` が
    `generated`/`observed`/`reported`（`_REOBSERVABLE_STATES`）のいずれかの
    場合、本コマンドは take を再 collect せず「同一 take での再観測」経路
    として動作する（`is_reobserve`）。`inputs_digest`/`plan_sha256` の
    precheck は従来どおり通過が必須だが、`orders_digest` 突合はスキップ
    する（take を再 collect しないため注文書とは無関係。local backend の
    run は元々 `orders_digest` を持たないため、この経路が無ければ local
    backend は再観測できなかった）。代わりに、供給された `--audio` の
    sha256 が disk 上の既存 take（`take.json` 経由で検証）と一致すること
    を要求する（`_load_existing_take_for_reobserve`）— 別音源をこの経路
    経由で忍び込ませることはできない。一致すれば `packages/`/正典
    `recast_plan.json` を再公開し（入力の drift を検出する既存の TOCTOU
    再チェックも維持）、observe→report を実行し直して `observed`/
    `reported` を `observation_digest`（現在の `observation` 節の digest）
    付きで再記録する。動機: `_project_identity_digest_component` が
    `inputs_digest` から `observation` 節を意図的に除外しているため
    （前指摘の回復フローを壊さないため）、`observation.enabled`/
    `observation.anchors` の変更だけでは `inputs_digest` は変化しない —
    しかし report は observation 節を直接反映するため、この再観測経路が
    無いと `recast status` の stale 表示（`observation_digest` 不一致）が
    行き止まりになってしまう。
    """
    from svp_rpe.arrange.package import PERFORMANCE_PACKAGE_FILENAME
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.run_paths import resolve_packages_dir, resolve_reports_dir
    from svp_rpe.recast.state import load_recast_state, record_state

    try:
        loaded = load_recast_project(project_yaml)
        # `--variant`/`--backend` の宣言確認（Codex P2 review round 14,
        # PR5 #210 指摘19 — `recast plan` 側の同型コメント参照）。
        _validate_variant_backend_declared(loaded, variant, backend)
        # packages/ への公開（指摘27 対応で `publish=False` 再ビルド後の
        # 直接 `_atomic_publish_text_bundle` 呼び出し）前の preflight
        # （Codex P2 review round 15, PR3 #208 指摘30 — `recast plan` 側の
        # 同型コメント参照）。
        _preflight_reject_plan_state_output_collision(loaded, variant, backend)
        state_file = load_recast_state(loaded.project_dir)
    except (OSError, ValueError, RecastError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    run_key = f"{variant}@{backend}"
    run = state_file.runs.get(run_key)
    # 再観測（Codex P2 review, PR #212 指摘）: `generated`/`observed`/
    # `reported` の run は、収蔵済みの take を collect し直さず observation
    # 設定だけを再評価して report を更新する経路として ingest を再利用できる
    # （下記 `--audio` 一致検査を通過した場合のみ — `_REOBSERVABLE_STATES`
    # docstring 参照）。
    is_reobserve = run is not None and run.state in _REOBSERVABLE_STATES
    if run is None or (run.state != "awaiting_generation" and not is_reobserve):
        current_state = run.state if run is not None else "draft"
        typer.echo(
            f"Error: {run_key} is at state {current_state!r}, not 'awaiting_generation' "
            f"(or one of {sorted(_REOBSERVABLE_STATES)} for re-observation with the same "
            "take). Run 'svprpe recast run' first to publish order files for a manual "
            "backend.",
            err=True,
        )
        raise typer.Exit(code=1)

    _ingest_prechecks(loaded, variant, backend, run, is_reobserve)

    anchor_scope, current_observation_digest = _precheck_observation_anchors(loaded)

    artifacts, plan_sha256 = _rebuild_and_publish_plan(loaded, variant, backend, run)

    take, prepared = _resolve_and_collect_take(
        loaded, variant, backend, artifacts, is_reobserve, audio
    )

    if is_reobserve:
        console.print(
            f"[cyan]Re-observing existing take: {take.audio_path} "
            f"(sha256={take.sha256})[/cyan]"
        )
    else:
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
        # 観測無効化時の再観測（Codex P2 review 4 巡目, PR #212 指摘）:
        # `is_reobserve` かつ `observation.enabled: false` の場合、ここで
        # 単に案内を出して exit するだけだと、state が `observed`/
        # `reported` のまま（古い `observation_digest` pin を抱えたまま）
        # 残り続ける。`recast status` は observed/reported の run にだけ
        # `observation_digest` 突合を課すため、state が変わらない限り
        # 恒久的に stale 表示 → ingest 再実行 → この early-exit → 何も
        # 変わらない、という無限ループになる。observation を無効化した
        # 時点で「この report はもう現在の観測方針を反映していない」と
        # みなし、state を `generated` へ撤回する（`_REOBSERVABLE_STATES`
        # に generated が含まれるため、以降 observation.enabled を true へ
        # 戻せば同じ take でそのまま再観測できる）。既存の report/summary
        # ファイル自体は disk から削除しない（証跡保全 — 他の recast 公開
        # サイトと同じ「無条件削除しない」規律）。
        if is_reobserve:
            revert_note = "観測が無効化されたため report を撤回（generated へ復帰）"
            try:
                record_state(
                    loaded.project_dir,
                    variant,
                    backend,
                    "generated",
                    note=revert_note,
                    inputs_digest=artifacts.result.inputs_digest,
                    plan_sha256=plan_sha256,
                    observation_digest=current_observation_digest,
                    protected_inputs=prepared.protected_input_paths,
                )
            except (OSError, ValueError) as exc:
                typer.echo(f"Error: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            console.print(
                f"[yellow]{revert_note}。"
                f"{resolve_reports_dir(loaded, variant, backend)} 配下の既存 "
                "report/summary は削除されていません（証跡として残ります）が、"
                "この run の現在の状態はもう表しません。[/yellow]"
            )
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

    _observe_and_report(
        loaded,
        variant,
        backend,
        artifacts,
        take,
        prepared,
        package_path,
        take_relative,
        anchor_scope,
        plan_sha256,
        current_observation_digest,
    )


@recast_app.command("status")
def recast_status_cmd(
    project_yaml: str = typer.Argument(..., help="Path to RecastProject YAML"),
) -> None:
    """Show `recast_state.json`'s current state per (variant, backend) run + next step.

    入力（score/identity_manifest/arrangement spec/capability profile/
    mode_overrides/device profile）が記録済み run の `inputs_digest` と一致しない
    場合、その run は stale（`recast plan` 再実行が必要）として表示する — 旧
    state をそのまま信用して次の一手（例: 生成に進む）を勧めない（Codex P2
    #207）。加えて、記録済み `plan_sha256` を、正典 per-run plan ファイル
    （`<builds_root>/plans/<variant>@<backend>/recast_plan.json` —
    `resolve_plans_dir`）の現在の bytes（不在/読取失敗を含む）と (variant,
    backend) ごとに突き合わせる — publish 後に plan 成果物が削除・破損した
    場合も同様に stale 表示へ倒す（Codex P2 fourth round #207: fail-closed）。
    この突合は per-run ファイルへ向けるため、別の (variant, backend) に
    対する `recast plan`/`run` を実行しても無関係の run は stale 化しない
    （Codex P2 review, PR #212 指摘: 従来 project 直下の単一 `recast_plan.json`
    を突合対象にしていたため、別 run の plan 実行だけで正当な run が
    stale 誤判定されていた）。`inputs_digest`/`plan_sha256` のいずれかが
    `None`（旧形式/手動コピーされた state 等）の場合も「未確認だからスキップ」
    ではなく stale（pin なし）として表示する（Codex P2 review round 4, PR3
    #208 指摘 9）。

    `awaiting_generation` の manual backend run については、加えて
    `orders_digest` も検査する（Codex P2 review round 10 指摘12）:
    `recast run` が発行した注文書（`order_sheet.md`/`prompt.json` 等 6
    ファイル）が発行後に編集・削除されていても、従来は次の一手として
    無条件に `svprpe recast ingest` を案内していた — 注文書どおりに生成
    していない（または生成すらできない）音声に対して外部生成を無駄撃ち
    させる誘因になる。`resolve_orders_dir` 配下から現在の
    `compute_orders_digest` を再計算し、`run.orders_digest` との不一致・
    `run.orders_digest` 自体が `None`（pin なし）・orders ディレクトリ不在の
    いずれかであれば stale として表示し、ingest 案内を出さない（既存の
    inputs_digest/plan_sha256 stale 群と同型）。`recast ingest` 自身は元々
    同じ突合を collect 前に行っているため、この表示は「実行すれば起きる
    こと」の先出しに過ぎない（挙動追加ではない）。

    `observed`/`reported` の run については、加えて `observation_digest`
    （project.yaml `observation` 節の canonical digest、
    `recast.plan.compute_observation_digest`）を検査する（Codex P2 review,
    PR #212 指摘）: `inputs_digest` は意図的に `observation` 節を除外して
    いる（生成系＝注文書・音源の同一性には無関係なため）が、report にとって
    `observation` 節は直接の入力（D-1 coverage の絞り込み対象）であり、
    report 生成後に設定が変わると report だけが古い設定を反映したまま fresh
    表示され続けてしまう。`run.observation_digest` が現在の `observation`
    設定から再計算した digest と一致しない（または pin 自体が `None`）
    場合は stale（observation 設定が report 生成時から変更）と表示し、
    `reported`/`observed` の次の一手（summary 確認案内）を出さず、代わりに
    同一 take での再観測（`recast_ingest_cmd` の再実行 — `awaiting_
    generation` 以外の状態でも take が既存 pin と一致すれば再観測のみを
    行う、下記 docstring 参照）を案内する。
    """
    from svp_rpe.recast import RecastError, load_recast_project
    from svp_rpe.recast.plan import (
        RECAST_PLAN_FILENAME,
        compute_observation_digest,
        compute_recast_inputs_digest,
    )
    from svp_rpe.recast.run_paths import resolve_plans_dir
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
    # project 全体で単一の observation 節（variant/backend に依存しない）
    # のためループ外で 1 回だけ計算する（`current_digest`/
    # `current_plan_sha256` は (variant, backend) ごとに異なるためループ
    # 内で計算する既存規約とは対照的）。
    current_observation_digest = compute_observation_digest(loaded.project.observation)
    reobserve_step = (
        "svprpe recast ingest <project.yaml> --variant <variant> --backend <backend> "
        "--audio <builds_root>/takes/<variant>@<backend>/take-01.* "
        "（同一 take で再観測。observation 設定変更後の report 更新）"
    )
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
                # 正典 per-run plan ファイルへ突合する（(variant, backend)
                # ごとに独立 — Codex P2 review, PR #212 指摘参照）。
                current_plan_sha256 = _read_plan_sha256(
                    resolve_plans_dir(loaded, variant, backend) / RECAST_PLAN_FILENAME
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
                elif (
                    state == "awaiting_generation"
                    and loaded.project.backends[backend].invocation == "manual"
                    and not _orders_digest_matches(loaded, variant, backend, run.orders_digest)
                ):
                    note = (
                        "stale（注文書が発行後に変更/欠落）— "
                        "svprpe recast run 再実行が必要"
                    )
                    next_step = _NEXT_STEP_BY_STATE["verified"]
                elif state in ("observed", "reported") and (
                    run.observation_digest is None
                    or run.observation_digest != current_observation_digest
                ):
                    note = (
                        "stale（observation 設定が report 生成時から変更）— "
                        "再観測（ingest の再実行）が必要"
                    )
                    next_step = reobserve_step
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
    import shlex
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
    # Codex P2 review round 13 (#210 指摘18): `--project-dir` は空白/シェル
    # メタ文字を含み得るため、生パス補間ではなく `shlex.join`（`recast/
    # backends/manual.py:_next_command_text` と同型）で copy-paste 実行可能な
    # コマンド文字列を組み立てる。
    next_command = shlex.join(
        [
            "svprpe", "recast", "plan", str(dest_dir / "project.yaml"),
            "--variant", "default", "--backend", "suno",
        ]
    )
    console.print(f"Next step: {next_command}")
