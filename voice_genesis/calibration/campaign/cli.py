"""`python -m voice_genesis.calibration.campaign <subcommand> --campaign-dir ...
--secret-dir ... [--armed] [--workers N]`（IMPLEMENTATION_MAP_v1.md §6.4）。

サブコマンド: `plan`（既定 dry-run。work unit 件数 vs 設計値/caps 照合表を
出力するのみ、副作用なし）/ `c1-fixtures` / `c2-baseline` /
`c3a-f0-selection` / `c3b-selection` / `unseal` / `c4-holdout` / `close`。

**武装プロトコル**（`plan` を除く全サブコマンド）: `--armed` フラグ AND
環境変数 `VG_CAL_CAMPAIGN_AUTHORIZED=1` AND 有効な Gate 1 承認ファイル
（`approvals.check_armed(Gate.GATE1_CAMPAIGN_EXECUTION, ...)`）が揃わなければ
`AUTHORIZATION_REQUIRED` を返し副作用ゼロで終了する。`--armed` を渡さない
場合は当該 stage の work-unit 計画のみを表示して正常終了する（他の 2 要素は
検査しない — 「まだ実行するつもりがない」ことを表明する経路であり拒否理由の
提示は不要なため）。

三要素武装が揃った後、キャンペーンを読み込んでからさらに 3 段の fail-closed
検査を通す（第 8/9 巡採用）:

- **canonical path 照合**（`_canonical_path_violations`。finding #7,
  第 9 巡採用）: 凍結 manifest の `candidates.{meter,generator,schema,
  test}_paths_sha256` に列挙された全 path について、**現在のファイル bytes**
  の sha256 を独立に再計算し manifest 記載値と照合する。1 件でも不一致・
  欠落があれば `BLOCKED_CANONICAL_MUTATION_REQUIRED`（設計正本 §3.3）
  ledger `stop_event` を記帳し、副作用を増やさず fail-closed 終了する。
  `hashlib`/`Path.read_bytes()` のみで完結し、`matrix`/`generator`/
  `registry`/`impl` モジュールを import・使用しない（確認対象のコードを
  import して確認する自己言及を避ける）。ただし本モジュール自身は
  `candidates.registry`/`fixtures.matrix` を **モジュール先頭で** import
  している（`_run_c3a` 等が使うため）— Python の import はプロセス内で
  1 度だけ実行され、この import 自体は `main()` 呼び出しより前（`cli.py`
  自身のロード時）に既に完了しているため、「それらの import がこの照合の
  後に来る」ことは本モジュールの現在の構造では実現できない。本照合が
  実際に保証するのは「照合が通らない限り、それらのモジュールが提供する
  **実行時の測定・生成ロジックを呼び出さない**」こと（`build_matrix()`
  呼び出し・stage dispatch は本照合の後に置く）である
  （`[UNDERSPEC-CAL-D23]`）。
- **Gate 1 承認の凍結 manifest への束縛**（`_gate1_frozen_binding_violation`）:
  現在ロードした Gate 1 承認ファイルの content sha256 / `authorization_nonce`
  が、このキャンペーンを凍結した時点で manifest に刻まれた
  `approvals.gate1_sha256`/`authorization_nonce` と一致することを要求する。
  不一致（＝凍結後に承認ファイルが差し替えられた）は
  `AUTHORIZATION_REQUIRED`（理由 `gate1_not_frozen_approval`）で拒否する。
- **手続 phase 順序の強制**（`_phase_order_violation`）: 各 subcommand は
  `state.CampaignPhase` 上の直前提条件 phase が到達済みであることを要求し、
  かつ（render 系の resume 対応 subcommand を除き）自身が生成する phase に
  既に到達済みなら再実行を拒否する（`PHASE_ORDER_VIOLATION`）。cap 強制
  （`campaign.caps` 経由の `CostCaps`/`CapCounters`。finding #1）もこの後、
  各 stage dispatch の直前に読み込む。

secret/approval dir の既定解決（`VG_CAL_SECRET_DIR`/`VG_CAL_APPROVAL_DIR`）は
`c0_freeze.py`/`approvals.py` と同じ規約だが、他 agent が並行編集中の
`c0_freeze.py` には依存せず本モジュールで独立に再定義する
（`approvals.default_approval_dir` は import してよい — 本パッケージが
所有しないファイルではない）。
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from voice_genesis.calibration.approvals import ArmingDecision, Gate, check_armed, default_approval_dir
from voice_genesis.calibration.campaign import (
    baseline_stage,
    close as close_stage,
    holdout_stage,
    measure_stage,
    render_stage,
    selection_stage,
    unseal as unseal_stage,
    workunits,
)
from voice_genesis.calibration.campaign.caps import cost_caps_from_manifest, load_cap_counters
from voice_genesis.calibration.campaign.state import (
    CampaignPhase,
    CampaignStateError,
    FrozenCampaign,
    load_frozen_campaign,
)
from voice_genesis.calibration.candidates.registry import candidate_by_id, candidates_for_meter
from voice_genesis.calibration.cost_caps import (
    BudgetAccountingUndeclaredError,
    CapCounters,
    CostCaps,
    StopDecision,
)
from voice_genesis.calibration.cost_caps import check as cost_caps_check
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.controls import (
    negative_control_row_ids,
    positive_detection_instances,
)
from voice_genesis.calibration.fixtures.matrix import build_matrix
from voice_genesis.calibration.observables import two_stage_median
from voice_genesis.calibration.vocab import (
    CLAIM_CRITICAL_SET,
    BlockedCode,
    ClaimCeiling,
    MeterId,
    MissingReason,
    Split,
    TerminalStatus,
)

#: `cli.py` から 3 階層上が repo root（`voice_genesis/calibration/campaign/cli.py`）。
#: finding #7 の canonical path 照合が `manifest["candidates"].*_paths_sha256`
#: の相対 path を解決するのに使う。`c0_freeze._REPO_ROOT` と同じ意味だが、
#: 他 agent が並行編集中の `c0_freeze.py` には依存せず本モジュールで独立に
#: 再定義する（モジュール docstring の既存方針と同じ）。
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: `c0_freeze.SECRET_DIR_ENV_VAR`/`DEFAULT_SECRET_DIR` と同一規約の独立定義
#: （モジュール docstring 参照）。
SECRET_DIR_ENV_VAR = "VG_CAL_SECRET_DIR"
DEFAULT_SECRET_DIR = Path.home() / ".vg_cal" / "secrets"

CAMPAIGN_ARMED_ENV_VAR = "VG_CAL_CAMPAIGN_AUTHORIZED"

SUBCOMMANDS: tuple[str, ...] = (
    "plan",
    "c1-fixtures",
    "c2-baseline",
    "c3a-f0-selection",
    "c3b-selection",
    "unseal",
    "c4-holdout",
    "close",
)

MUTATING_SUBCOMMANDS: frozenset[str] = frozenset(SUBCOMMANDS) - {"plan"}


def default_secret_dir(env: Mapping[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    override = source.get(SECRET_DIR_ENV_VAR)
    return Path(override) if override else DEFAULT_SECRET_DIR


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m voice_genesis.calibration.campaign")
    parser.add_argument("subcommand", choices=SUBCOMMANDS)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--secret-dir", type=Path, default=None)
    parser.add_argument("--approval-dir", type=Path, default=None)
    parser.add_argument("--armed", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--reveal-split-secret",
        action="store_true",
        help="close サブコマンド専用（[UNDERSPEC-CAL-D09]）: CAMPAIGN_CLOSED 後に "
        "split_secret の commit-reveal event を追加で記帳する。",
    )
    return parser


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


def _load_campaign_or_none(campaign_dir: Path, secret_dir: Path) -> FrozenCampaign | None:
    try:
        return load_frozen_campaign(campaign_dir, secret_dir)
    except CampaignStateError:
        return None


def build_plan_report(
    campaign_dir: Path, secret_dir: Path, *, stage: str | None = None
) -> dict[str, Any]:
    """全体（設計値）計画 + （読み込めれば）realized split 上の実件数。
    `stage` 指定時はその stage の work unit 件数のみを追加で報告する。"""
    design = workunits.plan_counts()
    report: dict[str, Any] = {
        "design_totals": {
            "instances_total": design.instances_total,
            "renders_total": design.renders_total,
            "meter_calls_per_implementation": design.meter_calls_per_implementation,
            "selection_order_of_magnitude": design.selection_order_of_magnitude,
        }
    }
    campaign = _load_campaign_or_none(campaign_dir, secret_dir)
    if campaign is None:
        report["campaign_state"] = "UNAVAILABLE"
        return report

    matrix_rows = build_matrix()
    assignment = campaign.realized_split.assignment
    realized = workunits.realized_plan(matrix_rows, assignment)
    report["campaign_state"] = "OK"
    report["campaign_id"] = campaign.campaign_id
    report["phases_passed"] = sorted(p.value for p in campaign.phases_passed())
    report["realized"] = {
        "c1_render_instances": realized.c1_render_instances,
        "c4_render_instances": realized.c4_render_instances,
        "c2_baseline_instances": realized.c2_baseline_instances,
        "c3a_instances": realized.c3a_instances,
        "c3b_instances_by_family": dict(realized.c3b_instances_by_family),
    }
    if stage is not None:
        report["stage"] = stage
    return report


# ---------------------------------------------------------------------------
# canonical path 照合（finding #7, 第 9 巡採用）
# ---------------------------------------------------------------------------

#: 凍結 manifest `candidates` 節の 4 カテゴリキー（`c0_freeze._path_hash_maps`
#: が生成する形状。値は `{相対 path: sha256 hex}` の mapping）。
_CANONICAL_PATH_CATEGORIES: tuple[str, ...] = (
    "meter_paths_sha256",
    "generator_paths_sha256",
    "schema_paths_sha256",
    "test_paths_sha256",
)


def _canonical_path_violations(campaign: FrozenCampaign, repo_root: Path) -> tuple[str, ...]:
    """finding #7: 凍結 manifest の `candidates.<category>`（4 カテゴリ）に
    列挙された全 path について、`repo_root` 上の **現在のファイル bytes** の
    sha256 を独立に再計算し、manifest 記載値と照合する。1 件でも不一致・
    欠落があれば、違反 path 1 件につき 1 行（`"<category>:<path>: <detail>"`
    形式）の tuple を返す。全て一致すれば空 tuple。

    `matrix`/`generator`/`registry`/`impl` の import には一切依存しない
    （`hashlib`/`Path.read_bytes()` のみ）— 確認対象のコードを import して
    確認する自己言及を避ける（モジュール docstring の `[UNDERSPEC-CAL-D23]`
    参照）。
    """
    candidates_section = campaign.manifest.get("candidates")
    if not isinstance(candidates_section, Mapping):
        return ("manifest is missing a candidates section",)

    violations: list[str] = []
    for category in _CANONICAL_PATH_CATEGORIES:
        paths = candidates_section.get(category)
        if not isinstance(paths, Mapping):
            violations.append(f"{category}: section missing from manifest")
            continue
        for rel_path, expected_sha in sorted(paths.items()):
            if not isinstance(rel_path, str) or not isinstance(expected_sha, str):
                violations.append(f"{category}: malformed entry {rel_path!r}")
                continue
            file_path = repo_root / rel_path
            if not file_path.is_file():
                violations.append(f"{category}:{rel_path}: file missing on disk")
                continue
            actual_sha = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                violations.append(
                    f"{category}:{rel_path}: sha256 mismatch "
                    f"(manifest={expected_sha!r}, actual={actual_sha!r})"
                )
    return tuple(violations)


# ---------------------------------------------------------------------------
# stage dispatch
# ---------------------------------------------------------------------------


def _selected_candidates_by_family(campaign: FrozenCampaign) -> dict[str, str | None]:
    """ledger の最新 `selection_frozen` payload から `selected_by_family` を
    読む（`selection_stage.run_c3b_selection` が記帳したもの）。"""
    result: dict[str, str | None] = {}
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "selection_frozen":
            selected = payload.get("selected_by_family")
            if isinstance(selected, Mapping):
                result = {str(k): (str(v) if v else None) for k, v in selected.items()}
    return result


#: 1 row あたりの probe repeat 数（`fixtures.controls.PROBE_REPEATS` と同値。
#: 本モジュールは truth_by_instance 構築で `range(...)` を直書きしていた
#: 既存の慣例を named constant へ揃えた）。
_PROBE_REPEATS = 5


def _latest_f0_selection(campaign: FrozenCampaign) -> tuple[bool, str | None]:
    """`f0_selection_frozen` event の有無と、あれば最新 payload の
    `selected_candidate_id` を返す（finding #2: C3b/C4 はこの event を
    必須の前提とする — fixture の truth F0 は一切使わない）。1 件も無ければ
    `(False, None)`。event はあるが selection 自体が `SELECTION_FAILED_CLOSED`
    等で候補未選出なら `(True, None)`。"""
    found = False
    selected: str | None = None
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "f0_selection_frozen":
            found = True
            sid = payload.get("selected_candidate_id")
            selected = str(sid) if isinstance(sid, str) else None
    return found, selected


def _process_id_for_repeat(repeat_kind: str, repeat_index: int) -> str:
    """`measure_stage.run_within_process_calls`/`run_fresh_process_calls` が
    実際に割り当てる `MeasurementRecord.process_id` と同じ規約（within は
    単一 process、fresh は repeat_index ごとに別 process）を、ledger へ既に
    記帳済みの `meter_call` payload（`process_id` 自体は保持しない — kind/
    row_id/probe_index/candidate_id/repeat_kind/repeat_index のみ）から
    再構築するための独立ミラー。"""
    return "within-process" if repeat_kind == "within" else f"fresh-process-{repeat_index}"


def _reusable_f0_values_by_process(
    campaign: FrozenCampaign, candidate_id: str, row_id: str, probe_index: int
) -> dict[str, list[float]] | None:
    """finding #2「(row_id, probe_index) が既に測定済みならその出力を再利用し、
    二重測定しない」: ledger 上に当該 (candidate_id, row_id, probe_index) の
    within `WITHIN_PROCESS_REPEATS` 回 + fresh `FRESH_PROCESS_REPEATS` 回が
    過不足なく記帳済みなら `f0_hz` 値を process 単位でまとめて返す
    （`observables.two_stage_median` の入力形）。1 件でも欠けていれば
    `None`（呼び出し側は改めて実測する）。"""
    by_process: dict[str, list[float]] = {}
    seen: set[tuple[str, int]] = set()
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if not isinstance(payload, Mapping) or payload.get("kind") != "meter_call":
            continue
        if (
            payload.get("candidate_id") != candidate_id
            or payload.get("row_id") != row_id
            or payload.get("probe_index") != probe_index
        ):
            continue
        repeat_kind = payload.get("repeat_kind")
        repeat_index = payload.get("repeat_index")
        if repeat_kind not in ("within", "fresh") or not isinstance(repeat_index, int):
            continue
        values = payload.get("values")
        f0 = values.get("f0_hz") if isinstance(values, Mapping) else None
        if not isinstance(f0, (int, float)) or isinstance(f0, bool):
            continue
        seen.add((repeat_kind, repeat_index))
        by_process.setdefault(
            _process_id_for_repeat(repeat_kind, repeat_index), []
        ).append(float(f0))
    expected = {("within", i) for i in range(measure_stage.WITHIN_PROCESS_REPEATS)} | {
        ("fresh", i) for i in range(measure_stage.FRESH_PROCESS_REPEATS)
    }
    if not expected.issubset(seen):
        return None
    return by_process


def _build_f0_by_instance(
    campaign: FrozenCampaign,
    instances: Sequence[tuple[str, int]],
    f0_candidate_id: str,
    sr_by_row: Mapping[str, int],
    *,
    max_workers: int,
    cap_counters: CapCounters | None,
    cost_caps: CostCaps | None,
) -> dict[tuple[str, int], float]:
    """finding #2: 選択済み F0 candidate を `instances` の各 instance 上で
    測定し（ledger に within3+fresh3 が既に揃っていれば再測定しない）、
    `observables.two_stage_median` で instance ごとに 1 スカラーへ集約する。
    F0_CONTROL 以外の family の instance（TILT_GT 等の実音源）に対して、
    その audio 自体から F0 を検出する — fixture の truth F0 は使わない。"""
    f0_candidate = candidate_by_id(f0_candidate_id)
    result: dict[tuple[str, int], float] = {}
    for row_id, probe_index in sorted(set(instances)):
        by_process = _reusable_f0_values_by_process(
            campaign, f0_candidate_id, row_id, probe_index
        )
        if by_process is None:
            records = measure_stage.run_measurement_for_instance(
                campaign,
                f0_candidate,
                row_id=row_id,
                probe_index=probe_index,
                sr_hz=sr_by_row[row_id],
                cap_counters=cap_counters,
                cost_caps=cost_caps,
                max_workers=max_workers,
            )
            by_process = {}
            for r in records:
                f0 = r.output.values.get("f0_hz")
                if f0 is None:
                    continue
                by_process.setdefault(r.process_id, []).append(float(f0))
        if by_process:
            result[(row_id, probe_index)] = two_stage_median(by_process)
    return result


def _positive_row_ids_for_selection(
    rows: Sequence[Any], assignment: Mapping[str, Any], family: str
) -> frozenset[str]:
    """round 13 finding #1: positive evidence = every TRUTH_CORE row of the
    evaluated SELECTION split for `family` (`fixtures.controls.
    positive_detection_instances()`, DESIGN RULING per `fixtures/controls.py`
    module docstring), not just the 2 designated anchors
    (`positive_control_row_ids()`). The 2-anchor row_id set under-covers:
    each anchor's home split is HMAC-derived and may not include SELECTION at
    all, in which case `candidate_fail_filter_report()` silently treated the
    positive-control filter as inapplicable instead of ineligible
    (`[UNDERSPEC-CAL-D25]`)."""
    instances = positive_detection_instances(rows, assignment, Split.SELECTION, family=family)
    return frozenset(row_id for row_id, _ in instances)


def _criteria_with_fail_filters(
    candidate: Any,
    records: Sequence[Any],
    truth_by_instance: Mapping[tuple[str, int], float],
    *,
    negative_control_ids: frozenset[str],
    positive_control_ids: frozenset[str],
    max_claim_scope: frozenset[str],
) -> tuple[Any, dict[str, bool], dict[str, object]]:
    """finding #8: `build_candidate_criteria()`（有限値の有無のみ）に加えて
    `candidates.adapter` 共通 5 fail filter を適用し、いずれか 1 つでも
    発火していれば `eligible=False` へ落とす。finding #11: さらに
    `max_claim_scope` 外の construct なら ceiling を capping する
    （`select_across_ceilings` へ渡す前に反映 — capping 済み ceiling で
    ABSOLUTE pool から除外される）。`(criteria, fail_filter_report,
    claim_scope_report)` を返す — 呼び出し元はこれらを `run_c3a_f0_selection`/
    `run_c3b_selection` の対応する `*_reports*` へ積み上げて SELECTION_FROZEN
    payload に記録する。"""
    base = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=negative_control_ids,
        positive_control_row_ids=positive_control_ids,
    )
    eligible = base.eligible and selection_stage.eligible_after_fail_filters(report)
    capped, scope_report = selection_stage.claim_scope_report(candidate, max_claim_scope)
    criteria = dataclasses.replace(base, eligible=eligible, ceiling=capped)
    return criteria, report, scope_report


def _run_c1(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> dict[str, Any]:
    outcomes = render_stage.run_render_stage(
        campaign, matrix_rows, stage="c1", cap_counters=cap_counters, cost_caps=cost_caps
    )
    return {
        "result": "OK",
        "instances": len({(o.row_id, o.probe_index) for o in outcomes}),
        "rendered": sum(1 for o in outcomes if o.status == "rendered"),
        "skipped_resume": sum(1 for o in outcomes if o.status == "skipped_resume"),
    }


def _run_c2(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> dict[str, Any]:
    result = baseline_stage.run_baseline_stage(
        campaign,
        matrix_rows,
        max_workers=workers,
        cap_counters=cap_counters,
        cost_caps=cost_caps,
    )
    return {"result": "OK", "baseline_audit_sha": result["baseline_audit_sha"]}


def _run_c3a(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> dict[str, Any]:
    # finding #11: claim scope must be frozen before any selection runs.
    try:
        max_claim_scope = selection_stage.max_claim_scope_from_manifest(campaign.manifest)
    except selection_stage.ClaimScopeError as exc:
        return {"result": "ERROR", "detail": str(exc)}

    assignment = campaign.realized_split.assignment
    instances = workunits.c3a_f0_selection_instances(matrix_rows, assignment)
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    truth_by_instance = {
        (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
        for mr in matrix_rows
        if mr.row.family == FixtureFamily.F0_CONTROL.value
        for p in range(_PROBE_REPEATS)
    }
    candidates = candidates_for_meter(MeterId.F0_CONTROL)

    records = measure_stage.run_measure_stage(
        campaign,
        instances,
        candidates,
        sr_by_row=sr_by_row,
        max_workers=workers,
        cap_counters=cap_counters,
        cost_caps=cost_caps,
    )
    neg_ids = negative_control_row_ids(matrix_rows)
    pos_ids = _positive_row_ids_for_selection(
        matrix_rows, assignment, FixtureFamily.F0_CONTROL.value
    )
    known_truth_by_instance = {k: v for k, v in truth_by_instance.items() if v is not None}
    criteria: list[Any] = []
    fail_filter_reports: dict[str, dict[str, bool]] = {}
    claim_scope_reports: dict[str, dict[str, object]] = {}
    for c in candidates:
        candidate_criteria, report, scope_report = _criteria_with_fail_filters(
            c,
            records,
            known_truth_by_instance,
            negative_control_ids=neg_ids,
            positive_control_ids=pos_ids,
            max_claim_scope=max_claim_scope,
        )
        criteria.append(candidate_criteria)
        fail_filter_reports[c.candidate_id] = report
        claim_scope_reports[c.candidate_id] = scope_report
    result = selection_stage.run_c3a_f0_selection(
        campaign,
        criteria,
        fail_filter_reports=fail_filter_reports,
        claim_scope_reports=claim_scope_reports,
    )
    return {
        "result": "OK",
        "selected_candidate_id": result.outcome.selected_candidate_id,
        "outcome": result.outcome.outcome,
    }


def _run_c3b(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> dict[str, Any]:
    # finding #11: claim scope must be frozen before any selection runs.
    try:
        max_claim_scope = selection_stage.max_claim_scope_from_manifest(campaign.manifest)
    except selection_stage.ClaimScopeError as exc:
        return {"result": "ERROR", "detail": str(exc)}

    baseline_audit_entry_sha = None
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "baseline_audit":
            baseline_audit_entry_sha = entry.entry_sha
    if baseline_audit_entry_sha is None:
        return {"result": "ERROR", "detail": "no baseline_audit event found; run c2-baseline first"}

    # finding #2: C3b requires C3a's frozen F0 selection — F0-dependent
    # candidates (harmonic-tilt/harmonic-residual/D4C) must receive the
    # *selected* F0 candidate's own per-instance output, never fixture truth.
    f0_found, f0_selected_id = _latest_f0_selection(campaign)
    if not f0_found:
        return {
            "result": "ERROR",
            "detail": "no f0_selection_frozen event found; run c3a-f0-selection first",
        }

    assignment = campaign.realized_split.assignment
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}

    instances_by_family: dict[str, tuple[tuple[str, int], ...]] = {}
    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        if not _candidates_for_family(family):
            continue
        instances_by_family[family.value] = workunits.c3b_family_selection_instances(
            matrix_rows, assignment, family.value
        )

    f0_by_instance: dict[tuple[str, int], float] = {}
    if f0_selected_id is not None:
        all_instances = sorted({inst for insts in instances_by_family.values() for inst in insts})
        f0_by_instance = _build_f0_by_instance(
            campaign,
            all_instances,
            f0_selected_id,
            sr_by_row,
            max_workers=workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
        )

    criteria_by_family: dict[str, list] = {}
    fail_filter_reports_by_family: dict[str, dict[str, dict[str, bool]]] = {}
    claim_scope_reports_by_family: dict[str, dict[str, dict[str, object]]] = {}
    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        meter_candidates = _candidates_for_family(family)
        if not meter_candidates:
            continue
        instances = instances_by_family[family.value]
        truth_by_instance = {
            (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
            for mr in matrix_rows
            if mr.row.family == family.value
            for p in range(_PROBE_REPEATS)
        }
        truth_by_instance = {k: v for k, v in truth_by_instance.items() if v is not None}
        records = measure_stage.run_measure_stage(
            campaign,
            instances,
            meter_candidates,
            sr_by_row=sr_by_row,
            f0_by_instance=f0_by_instance,
            max_workers=workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
        )
        family_rows = [mr for mr in matrix_rows if mr.row.family == family.value]
        neg_ids = negative_control_row_ids(family_rows)
        pos_ids = _positive_row_ids_for_selection(family_rows, assignment, family.value)
        family_criteria: list[Any] = []
        family_fail_filter_reports: dict[str, dict[str, bool]] = {}
        family_claim_scope_reports: dict[str, dict[str, object]] = {}
        for c in meter_candidates:
            candidate_criteria, report, scope_report = _criteria_with_fail_filters(
                c,
                records,
                truth_by_instance,
                negative_control_ids=neg_ids,
                positive_control_ids=pos_ids,
                max_claim_scope=max_claim_scope,
            )
            family_criteria.append(candidate_criteria)
            family_fail_filter_reports[c.candidate_id] = report
            family_claim_scope_reports[c.candidate_id] = scope_report
        criteria_by_family[family.value] = family_criteria
        fail_filter_reports_by_family[family.value] = family_fail_filter_reports
        claim_scope_reports_by_family[family.value] = family_claim_scope_reports

    result = selection_stage.run_c3b_selection(
        campaign,
        criteria_by_family,
        baseline_audit_entry_sha=baseline_audit_entry_sha,
        fail_filter_reports_by_family=fail_filter_reports_by_family,
        claim_scope_reports_by_family=claim_scope_reports_by_family,
    )
    return {
        "result": "OK",
        "selected_by_family": {
            family: outcome.selected_candidate_id
            for family, outcome in result.outcomes_by_family.items()
        },
    }


_FAMILY_TO_METER: Mapping[FixtureFamily, MeterId] = {
    FixtureFamily.FORMANT_GT: MeterId.M3_FORMANTS,
    FixtureFamily.TILT_GT: MeterId.M2_SPECTRAL_TILT,
    FixtureFamily.APERIODICITY_GT: MeterId.M2_APERIODICITY,
    FixtureFamily.RESONANCE_GT: MeterId.M4_RESONANCE,
    FixtureFamily.TRANSITION_GT: MeterId.M5_TRANSITION,
}


def _candidates_for_family(family: FixtureFamily) -> tuple[Any, ...]:
    meter = _FAMILY_TO_METER.get(family)
    if meter is None:
        return ()
    return candidates_for_meter(meter)


def _run_unseal(campaign: FrozenCampaign, approval_dir: Path) -> dict[str, Any]:
    try:
        result = unseal_stage.unseal_campaign(campaign, approval_dir=approval_dir)
    except unseal_stage.UnsealError as exc:
        return {"result": "UNSEAL_REFUSED", "detail": str(exc)}
    return {
        "result": "OK",
        "holdout_unseal_entry_sha": result.holdout_unseal_entry_sha,
    }


def _run_c4(
    campaign: FrozenCampaign,
    matrix_rows: Sequence[Any],
    workers: int,
    *,
    cap_counters: CapCounters | None = None,
    cost_caps: CostCaps | None = None,
) -> dict[str, Any]:
    # finding #11: claim scope must be frozen before holdout runs too (the
    # capping fact is recorded per candidate below; see the note at the
    # per-family loop for why the CLI's DIAGNOSTIC_ONLY placeholder ceiling
    # itself is not swapped for the capped value — that would misreport an
    # ABSOLUTE claim no real gate ever evaluated, which [UNDERSPEC-CAL-D17]
    # already forbids).
    try:
        max_claim_scope = selection_stage.max_claim_scope_from_manifest(campaign.manifest)
    except selection_stage.ClaimScopeError as exc:
        return {"result": "ERROR", "detail": str(exc)}

    # finding #2: C4 also feeds F0-dependent candidates the selected F0
    # candidate's own per-instance output (never fixture truth).
    f0_found, f0_selected_id = _latest_f0_selection(campaign)
    if not f0_found:
        return {
            "result": "ERROR",
            "detail": "no f0_selection_frozen event found; run c3a-f0-selection first",
        }

    selected = _selected_candidates_by_family(campaign)
    candidates_by_family: dict[str, tuple[Any, ...]] = {}
    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        pool = _candidates_for_family(family)
        b0 = tuple(c for c in pool if "-B0-" in c.candidate_id)
        selected_id = selected.get(family.value)
        selected_candidate = tuple(c for c in pool if c.candidate_id == selected_id)
        combined = tuple({c.candidate_id: c for c in (*b0, *selected_candidate)}.values())
        if combined:
            candidates_by_family[family.value] = combined

    assignment = campaign.realized_split.assignment
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    f0_by_instance: dict[tuple[str, int], float] = {}
    if f0_selected_id is not None:
        all_instances = sorted(
            {
                inst
                for family in candidates_by_family
                for inst in workunits.c4_holdout_instances(matrix_rows, assignment, family=family)
            }
        )
        f0_by_instance = _build_f0_by_instance(
            campaign,
            all_instances,
            f0_selected_id,
            sr_by_row,
            max_workers=workers,
            cap_counters=cap_counters,
            cost_caps=cost_caps,
        )

    records_by_family = holdout_stage.render_and_measure_holdout(
        campaign,
        matrix_rows,
        candidates_by_family=candidates_by_family,
        max_workers=workers,
        f0_by_instance=f0_by_instance,
        cap_counters=cap_counters,
        cost_caps=cost_caps,
    )

    # finding #11: candidate_id -> Candidate lookup, for annotating gate_detail
    # with claim_scope_report() below (covers every pool this stage touches).
    candidate_by_candidate_id: dict[str, Any] = {
        c.candidate_id: c for pool in candidates_by_family.values() for c in pool
    }

    # finding #10: `results` must cover exactly the 7 `vocab.MeterId` values
    # (holdout_stage.run_holdout_stage now enforces this itself, fail-closed).
    results: list[holdout_stage.MeterHoldoutResult] = []
    for family, meter in _FAMILY_TO_METER.items():
        if meter is MeterId.M4_RESONANCE:
            # M4 always closes DIAGNOSTIC_ONLY regardless of selection
            # (§16-1, handled below via diagnostic_only_close) — it must
            # NOT also go through the generic per-family branch below (that
            # was finding #10's "M4 の二重追加" bug: this family used to
            # produce a *second*, generic MeterHoldoutResult here in
            # addition to the diagnostic_only_close() appended after the
            # loop, silently colliding in run_holdout_stage's old
            # unvalidated per_meter dict).
            continue
        if family.value not in records_by_family:
            results.append(holdout_stage.selection_failed_closed_meter(meter.value))
            continue
        selected_id = selected.get(family.value)
        if selected_id is None:
            results.append(holdout_stage.selection_failed_closed_meter(meter.value))
            continue
        # finding #11: record the claim-scope capping fact for this meter's
        # selected candidate (§b/§c). The placeholder `ceiling` below stays
        # DIAGNOSTIC_ONLY regardless — swapping it for the capped ceiling
        # would misreport an ABSOLUTE claim no real gate ever evaluated
        # ([UNDERSPEC-CAL-D17]); real gate assembly (out of CLI scope) is
        # where `evaluate_absolute_meter`/`evaluate_directional_meter`
        # would receive the capped ceiling directly as their `ceiling` arg.
        selected_candidate_obj = candidate_by_candidate_id.get(selected_id)
        claim_scope_detail: dict[str, object] = {}
        if selected_candidate_obj is not None:
            _capped, claim_scope_detail = selection_stage.claim_scope_report(
                selected_candidate_obj, max_claim_scope
            )
        results.append(
            holdout_stage.MeterHoldoutResult(
                meter_id=meter.value,
                terminal_status="DIAGNOSTIC_ONLY",
                reason_code=None,
                ceiling=ClaimCeiling.DIAGNOSTIC_ONLY.value,
                selected_candidate_id=selected_id,
                gate_detail={
                    "note": (
                        "[UNDERSPEC-CAL-D17] full E_use-bound absolute/directional gate "
                        "assembly from CLI is out of D2 infra scope; holdout_stage "
                        "evaluate_absolute_meter/evaluate_directional_meter building "
                        "blocks are exercised directly in tests with real gate wiring."
                    ),
                    "claim_scope": claim_scope_detail,
                },
            )
        )

    # M4_RESONANCE (§16-1: always DIAGNOSTIC_ONLY, selection not gate-tested).
    results.append(
        holdout_stage.diagnostic_only_close(
            MeterId.M4_RESONANCE.value,
            selected_candidate_id=selected.get(FixtureFamily.RESONANCE_GT.value),
        )
    )

    # F0_CONTROL (finding #10): the upstream control's own terminal status,
    # derived from the C3a f0_selection_frozen outcome. F0_CONTROL feeds
    # other meters' params["f0_hz"] per instance (finding #2) rather than
    # being independently gate-evaluated in C4.
    if f0_selected_id is None:
        results.append(holdout_stage.selection_failed_closed_meter(MeterId.F0_CONTROL.value))
    else:
        results.append(
            holdout_stage.diagnostic_only_close(
                MeterId.F0_CONTROL.value,
                selected_candidate_id=f0_selected_id,
                reason=(
                    "F0_CONTROL feeds other meters' params['f0_hz'] per-instance "
                    "(finding #2); not independently gate-evaluated in C4."
                ),
            )
        )

    # M6_IDENTITY (finding #10): evaluated only when every claim-critical
    # meter (vocab.CLAIM_CRITICAL_SET) reached ABSOLUTE ceiling; otherwise
    # NOT_EVALUABLE. Under the current D2 CLI scope the per-family branch
    # above always assigns DIAGNOSTIC_ONLY (never ABSOLUTE — see the D17
    # note), so this correctly resolves to NOT_EVALUABLE today; the check
    # itself is real (not hardcoded) so it lights up once real gate
    # assembly lands upstream.
    critical_ceilings = {r.meter_id: r.ceiling for r in results}
    all_critical_absolute = all(
        critical_ceilings.get(m.value) == ClaimCeiling.ABSOLUTE.value for m in CLAIM_CRITICAL_SET
    )
    if all_critical_absolute:
        results.append(
            holdout_stage.MeterHoldoutResult(
                meter_id=MeterId.M6_IDENTITY.value,
                terminal_status=TerminalStatus.DIAGNOSTIC_ONLY.value,
                reason_code=None,
                ceiling=ClaimCeiling.DIAGNOSTIC_ONLY.value,
                selected_candidate_id=None,
                gate_detail={
                    "note": (
                        "[UNDERSPEC-CAL-D17] full M6 identity-preservation gate "
                        "assembly is out of D2 infra scope; all claim-critical "
                        "meters reached ABSOLUTE ceiling (necessary precondition "
                        "satisfied)."
                    )
                },
            )
        )
    else:
        results.append(
            holdout_stage.MeterHoldoutResult(
                meter_id=MeterId.M6_IDENTITY.value,
                terminal_status=TerminalStatus.NOT_EVALUABLE.value,
                reason_code=MissingReason.OUTPUT_NOT_EVALUABLE.value,
                ceiling=ClaimCeiling.NONE.value,
                selected_candidate_id=None,
                gate_detail={
                    "reason": "not all claim-critical meters reached ABSOLUTE ceiling",
                },
            )
        )

    entry = holdout_stage.run_holdout_stage(campaign, results)
    return {"result": "OK", "holdout_executed_valid_entry_sha": entry.entry_sha}


def _run_close(campaign: FrozenCampaign, *, reveal: bool) -> dict[str, Any]:
    holdout_payload = None
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "holdout_executed_valid":
            holdout_payload = payload
    if holdout_payload is None:
        return {"result": "ERROR", "detail": "no holdout_executed_valid event found"}
    try:
        result = close_stage.close_campaign(campaign, holdout_payload)
    except close_stage.CampaignNotClosableError as exc:
        return {"result": "NOT_CLOSABLE", "detail": str(exc)}
    out: dict[str, Any] = {
        "result": "OK",
        "campaign_closed_entry_sha": result.campaign_closed_entry_sha,
        "debt_discharged": result.debt_discharged,
    }
    if reveal:
        reveal_entry = close_stage.reveal_split_secret(campaign)
        out["split_secret_revealed_entry_sha"] = reveal_entry.entry_sha
    return out


_STAGE_DISPATCH_NEEDS_MATRIX = {"c1-fixtures", "c2-baseline", "c3a-f0-selection", "c3b-selection", "c4-holdout"}


# ---------------------------------------------------------------------------
# Gate 1 承認の凍結 manifest への束縛（finding #5, 第 8 巡採用）
# ---------------------------------------------------------------------------


def _gate1_frozen_binding_violation(arming: ArmingDecision, campaign: FrozenCampaign) -> str | None:
    """`check_armed(GATE1)` が `armed=True` を返した後にさらに要求する束縛:
    現在ロードした Gate 1 承認ファイルの content sha256 / `authorization_nonce`
    が、このキャンペーンを凍結した時点で manifest に刻まれた
    `approvals.gate1_sha256`/`authorization_nonce`（`c0_freeze.armed_freeze()`
    が freeze 時点の承認ファイルから埋め込んだもの）と一致することを要求する。
    凍結後に承認ファイルが差し替えられていれば（同じ 3 要素武装が揃っていても）
    拒否する。一致すれば `None`、不一致なら `missing_factors` へ載せる 1 行を
    返す。"""
    approvals_section = campaign.manifest.get("approvals")
    frozen_gate1_sha = (
        approvals_section.get("gate1_sha256") if isinstance(approvals_section, Mapping) else None
    )
    if arming.approval_content_sha256 != frozen_gate1_sha:
        return "gate1_not_frozen_approval"
    frozen_nonce = campaign.manifest.get("authorization_nonce")
    approval_nonce = arming.approval.authorization_nonce if arming.approval is not None else None
    if approval_nonce != frozen_nonce:
        return "gate1_not_frozen_approval"
    return None


# ---------------------------------------------------------------------------
# 手続 phase 順序の強制（finding #6, 第 8 巡採用）
# ---------------------------------------------------------------------------

#: subcommand -> 実行前に到達済みであることを要求する直前提条件 phase。
_SUBCOMMAND_PREREQUISITE_PHASE: Mapping[str, CampaignPhase] = {
    "c1-fixtures": CampaignPhase.PREPARATION_VALID,
    "c2-baseline": CampaignPhase.FIXTURE_VALID,
    "c3a-f0-selection": CampaignPhase.BASELINE_AUDITED,
    "c3b-selection": CampaignPhase.F0_SELECTION_FROZEN,
    "unseal": CampaignPhase.SELECTION_FROZEN,
    "c4-holdout": CampaignPhase.UNSEALED,
    "close": CampaignPhase.HOLDOUT_EXECUTED_VALID,
}

#: subcommand -> 成功時に新規到達する phase。
_SUBCOMMAND_PRODUCES_PHASE: Mapping[str, CampaignPhase] = {
    "c1-fixtures": CampaignPhase.FIXTURE_VALID,
    "c2-baseline": CampaignPhase.BASELINE_AUDITED,
    "c3a-f0-selection": CampaignPhase.F0_SELECTION_FROZEN,
    "c3b-selection": CampaignPhase.SELECTION_FROZEN,
    "unseal": CampaignPhase.UNSEALED,
    "c4-holdout": CampaignPhase.HOLDOUT_EXECUTED_VALID,
    "close": CampaignPhase.CAMPAIGN_CLOSED,
}

#: これらの subcommand は render_stage の resume（sha 一致する既存 instance を
#: skip し pcm/ledger を re-append しない — `render_stage.render_instance` の
#: resume 判定）により、自身の `_SUBCOMMAND_PRODUCES_PHASE` 到達後の再実行が
#: 安全（`tests/test_campaign_render.py::test_c1_render_determinism_and_resume`
#: が module 単位で実証済み）。それ以外の subcommand は produces phase 到達後の
#: 再実行を一律拒否する。
_RESUMABLE_SUBCOMMANDS: frozenset[str] = frozenset({"c1-fixtures", "c4-holdout"})


def _phase_order_violation(subcommand: str, campaign: FrozenCampaign) -> str | None:
    """`subcommand` を今このキャンペーンに対して実行してよいかを、
    `state.CampaignPhase` の到達済み集合だけから判定する（finding #6）。
    違反理由（`missing_factors` へ載せる 1 行）を返す。問題なければ `None`。

    `[UNDERSPEC-CAL-D22]` 「完了済み段の再実行は pin 済み結果と同一なら
    no-op」という厳密な byte-identical 判定は本 fix の範囲外とした（各 stage
    の pin 済み結果を再構築して比較する専用ロジックが要る一般化困難な作業で
    あり、誤って「同一」と判定すれば fail-closed の逆になる）。本実装は
    `_RESUMABLE_SUBCOMMANDS`（render 系。resume 機構により再実行そのものが
    安全と既に個別実証済み）以外について、produces phase 到達後の再実行を
    一律拒否する — より安全な過剰拒否側に倒した近似を採用する。"""
    prerequisite = _SUBCOMMAND_PREREQUISITE_PHASE.get(subcommand)
    produces = _SUBCOMMAND_PRODUCES_PHASE.get(subcommand)
    if prerequisite is None or produces is None:
        return None
    passed = campaign.phases_passed()
    if prerequisite not in passed:
        return f"phase_order:{subcommand}_requires_{prerequisite.value}"
    if subcommand not in _RESUMABLE_SUBCOMMANDS and produces in passed:
        return f"phase_order:{subcommand}_already_{produces.value}"
    return None


def _refuse_if_caps_already_breached(
    campaign: FrozenCampaign,
    cost_caps: CostCaps | None,
    cap_counters: CapCounters,
) -> StopDecision | None:
    """round 13 finding #2: `counters.json` is reloaded on every subcommand
    invocation, but the frozen-cap check only ran *inside* the previous
    stage's per-unit loop (`render_stage`/`measure_stage`). A retry after a
    breach reloaded already-over-limit counters and let dispatch proceed
    anyway, charging one more work unit per retry. Run the same
    `cost_caps.check()` immediately after loading counters, before any stage
    dispatch — if already breached, refuse to dispatch (`[UNDERSPEC-CAL-D26]`).

    Idempotent: `stop_event` recording is append-only and this guard can run
    on every invocation while the campaign sits in a breached state, so it
    must not append a duplicate `stop_event` when the last ledger entry
    already records this exact breach (same reason/counters/caps) — it still
    refuses dispatch either way.
    """
    if cost_caps is None:
        return None
    decision = cost_caps_check(cap_counters, cost_caps)
    if decision is None:
        return None
    entries = campaign.ledger.entries
    last_payload = entries[-1].payload if entries else None
    already_recorded = (
        isinstance(last_payload, Mapping)
        and last_payload.get("kind") == "stop_event"
        and last_payload.get("reason") == decision.event_payload.get("reason")
        and last_payload.get("counters") == decision.event_payload.get("counters")
        and last_payload.get("caps") == decision.event_payload.get("caps")
    )
    if not already_recorded:
        campaign.ledger.append(decision.event_payload)
    return decision


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    secret_dir = args.secret_dir or default_secret_dir()
    approval_dir = args.approval_dir or default_approval_dir()

    if args.subcommand == "plan":
        _print(build_plan_report(args.campaign_dir, secret_dir))
        return 0

    if not args.armed:
        _print(
            {
                "result": "PLAN_ONLY",
                "note": "pass --armed to execute; showing plan for this stage only",
                **build_plan_report(args.campaign_dir, secret_dir, stage=args.subcommand),
            }
        )
        return 0

    arming = check_armed(
        Gate.GATE1_CAMPAIGN_EXECUTION, args.armed, os.environ, approval_dir
    )
    if not arming.armed:
        _print(
            {
                "result": "AUTHORIZATION_REQUIRED",
                "missing_factors": list(arming.missing_factors),
            }
        )
        return 1

    try:
        campaign = load_frozen_campaign(args.campaign_dir, secret_dir)
    except CampaignStateError as exc:
        _print({"result": "CAMPAIGN_STATE_ERROR", "detail": str(exc)})
        return 1

    # finding #7: verify the pinned meter/generator/schema/test source bytes
    # match the frozen manifest *before* touching anything that would use
    # them (build_matrix()/stage dispatch below).
    canonical_violations = _canonical_path_violations(campaign, _REPO_ROOT)
    if canonical_violations:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": BlockedCode.BLOCKED_CANONICAL_MUTATION_REQUIRED.value,
                "paths": list(canonical_violations),
            }
        )
        _print(
            {
                "result": "BLOCKED_CANONICAL_MUTATION_REQUIRED",
                "paths": list(canonical_violations),
            }
        )
        return 1

    # finding #5: the 3-factor arming above proves *a* valid, currently-armed
    # Gate 1 approval exists — it does not prove it is the *same* approval
    # this campaign was frozen against. Bind it explicitly.
    binding_violation = _gate1_frozen_binding_violation(arming, campaign)
    if binding_violation is not None:
        _print({"result": "AUTHORIZATION_REQUIRED", "missing_factors": [binding_violation]})
        return 1

    # finding #6: refuse subcommands out of procedural order (skip-ahead, or
    # re-running a non-resumable stage that already pinned its result).
    phase_violation = _phase_order_violation(args.subcommand, campaign)
    if phase_violation is not None:
        _print({"result": "PHASE_ORDER_VIOLATION", "detail": phase_violation})
        return 1

    # finding #1: frozen cost caps, loaded from the manifest Gate 1 embedded
    # at freeze time, and cumulative counters persisted across subcommands.
    # round 13 finding #3: a *declared* cost_caps section with a missing/
    # unknown budget_accounting_mode fails closed with a distinct code
    # rather than silently falling back to "no caps" (which would let the
    # dead `budget` dimension stay dead).
    try:
        cost_caps_obj = cost_caps_from_manifest(campaign.manifest)
    except BudgetAccountingUndeclaredError as exc:
        campaign.ledger.append(
            {
                "kind": "stop_event",
                "reason": BudgetAccountingUndeclaredError.CODE,
                "detail": str(exc),
            }
        )
        _print({"result": BudgetAccountingUndeclaredError.CODE, "detail": str(exc)})
        return 1
    cap_counters = load_cap_counters(campaign.campaign_dir)

    # round 13 finding #2: refuse dispatch immediately if the reloaded
    # counters already breach the frozen caps — do not let a retry silently
    # proceed and charge one more work unit.
    breach = _refuse_if_caps_already_breached(campaign, cost_caps_obj, cap_counters)
    if breach is not None:
        _print({"result": "COST_CAP_EXCEEDED", "detail": breach.detail})
        return 1

    matrix_rows = build_matrix() if args.subcommand in _STAGE_DISPATCH_NEEDS_MATRIX else None

    if args.subcommand == "c1-fixtures":
        out = _run_c1(campaign, matrix_rows, cap_counters=cap_counters, cost_caps=cost_caps_obj)
    elif args.subcommand == "c2-baseline":
        out = _run_c2(
            campaign, matrix_rows, args.workers, cap_counters=cap_counters, cost_caps=cost_caps_obj
        )
    elif args.subcommand == "c3a-f0-selection":
        out = _run_c3a(
            campaign, matrix_rows, args.workers, cap_counters=cap_counters, cost_caps=cost_caps_obj
        )
    elif args.subcommand == "c3b-selection":
        out = _run_c3b(
            campaign, matrix_rows, args.workers, cap_counters=cap_counters, cost_caps=cost_caps_obj
        )
    elif args.subcommand == "unseal":
        out = _run_unseal(campaign, approval_dir)
    elif args.subcommand == "c4-holdout":
        out = _run_c4(
            campaign, matrix_rows, args.workers, cap_counters=cap_counters, cost_caps=cost_caps_obj
        )
    elif args.subcommand == "close":
        out = _run_close(campaign, reveal=args.reveal_split_secret)
    else:  # pragma: no cover - argparse choices already constrains this
        out = {"result": "ERROR", "detail": f"unknown subcommand {args.subcommand!r}"}

    _print(out)
    return 0 if out.get("result") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
