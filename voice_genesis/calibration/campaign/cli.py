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

secret/approval dir の既定解決（`VG_CAL_SECRET_DIR`/`VG_CAL_APPROVAL_DIR`）は
`c0_freeze.py`/`approvals.py` と同じ規約だが、他 agent が並行編集中の
`c0_freeze.py` には依存せず本モジュールで独立に再定義する
（`approvals.default_approval_dir` は import してよい — 本パッケージが
所有しないファイルではない）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from voice_genesis.calibration.approvals import Gate, check_armed, default_approval_dir
from voice_genesis.calibration.campaign import (
    baseline_stage,
    close as close_stage,
    holdout_stage,
    render_stage,
    selection_stage,
    unseal as unseal_stage,
    workunits,
)
from voice_genesis.calibration.campaign.state import (
    CampaignStateError,
    FrozenCampaign,
    load_frozen_campaign,
)
from voice_genesis.calibration.candidates.registry import candidates_for_meter
from voice_genesis.calibration.fixtures.axes import FixtureFamily
from voice_genesis.calibration.fixtures.matrix import build_matrix
from voice_genesis.calibration.vocab import ClaimCeiling, MeterId

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


def _run_c1(campaign: FrozenCampaign, matrix_rows: Sequence[Any]) -> dict[str, Any]:
    outcomes = render_stage.run_render_stage(campaign, matrix_rows, stage="c1")
    return {
        "result": "OK",
        "instances": len({(o.row_id, o.probe_index) for o in outcomes}),
        "rendered": sum(1 for o in outcomes if o.status == "rendered"),
        "skipped_resume": sum(1 for o in outcomes if o.status == "skipped_resume"),
    }


def _run_c2(campaign: FrozenCampaign, matrix_rows: Sequence[Any], workers: int) -> dict[str, Any]:
    result = baseline_stage.run_baseline_stage(campaign, matrix_rows, max_workers=workers)
    return {"result": "OK", "baseline_audit_sha": result["baseline_audit_sha"]}


def _run_c3a(campaign: FrozenCampaign, matrix_rows: Sequence[Any], workers: int) -> dict[str, Any]:
    assignment = campaign.realized_split.assignment
    instances = workunits.c3a_f0_selection_instances(matrix_rows, assignment)
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    truth_by_instance = {
        (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
        for mr in matrix_rows
        if mr.row.family == FixtureFamily.F0_CONTROL.value
        for p in range(5)
    }
    candidates = candidates_for_meter(MeterId.F0_CONTROL)
    from voice_genesis.calibration.campaign import measure_stage

    records = measure_stage.run_measure_stage(
        campaign, instances, candidates, sr_by_row=sr_by_row, max_workers=workers
    )
    criteria = [
        selection_stage.build_candidate_criteria(
            c,
            records,
            {k: v for k, v in truth_by_instance.items() if v is not None},
        )
        for c in candidates
    ]
    result = selection_stage.run_c3a_f0_selection(campaign, criteria)
    return {
        "result": "OK",
        "selected_candidate_id": result.outcome.selected_candidate_id,
        "outcome": result.outcome.outcome,
    }


def _run_c3b(campaign: FrozenCampaign, matrix_rows: Sequence[Any], workers: int) -> dict[str, Any]:
    baseline_audit_entry_sha = None
    for entry in campaign.ledger.entries:
        payload = entry.payload
        if isinstance(payload, Mapping) and payload.get("kind") == "baseline_audit":
            baseline_audit_entry_sha = entry.entry_sha
    if baseline_audit_entry_sha is None:
        return {"result": "ERROR", "detail": "no baseline_audit event found; run c2-baseline first"}

    assignment = campaign.realized_split.assignment
    sr_by_row = {mr.row_id: mr.row.sr_hz for mr in matrix_rows}
    from voice_genesis.calibration.campaign import measure_stage

    criteria_by_family: dict[str, list] = {}
    for family in FixtureFamily:
        if family is FixtureFamily.F0_CONTROL:
            continue
        meter_candidates = _candidates_for_family(family)
        if not meter_candidates:
            continue
        instances = workunits.c3b_family_selection_instances(matrix_rows, assignment, family.value)
        truth_by_instance = {
            (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
            for mr in matrix_rows
            if mr.row.family == family.value
            for p in range(5)
        }
        truth_by_instance = {k: v for k, v in truth_by_instance.items() if v is not None}
        records = measure_stage.run_measure_stage(
            campaign, instances, meter_candidates, sr_by_row=sr_by_row, max_workers=workers
        )
        criteria_by_family[family.value] = [
            selection_stage.build_candidate_criteria(c, records, truth_by_instance)
            for c in meter_candidates
        ]

    result = selection_stage.run_c3b_selection(
        campaign, criteria_by_family, baseline_audit_entry_sha=baseline_audit_entry_sha
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


def _run_c4(campaign: FrozenCampaign, matrix_rows: Sequence[Any], workers: int) -> dict[str, Any]:
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

    records_by_family = holdout_stage.render_and_measure_holdout(
        campaign, matrix_rows, candidates_by_family=candidates_by_family, max_workers=workers
    )

    results = []
    for family, meter in _FAMILY_TO_METER.items():
        if family.value not in records_by_family:
            results.append(holdout_stage.selection_failed_closed_meter(meter.value))
            continue
        selected_id = selected.get(family.value)
        if selected_id is None:
            results.append(holdout_stage.selection_failed_closed_meter(meter.value))
            continue
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
                    )
                },
            )
        )
    results.append(holdout_stage.diagnostic_only_close(MeterId.M4_RESONANCE.value))

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

    matrix_rows = build_matrix() if args.subcommand in _STAGE_DISPATCH_NEEDS_MATRIX else None

    if args.subcommand == "c1-fixtures":
        out = _run_c1(campaign, matrix_rows)
    elif args.subcommand == "c2-baseline":
        out = _run_c2(campaign, matrix_rows, args.workers)
    elif args.subcommand == "c3a-f0-selection":
        out = _run_c3a(campaign, matrix_rows, args.workers)
    elif args.subcommand == "c3b-selection":
        out = _run_c3b(campaign, matrix_rows, args.workers)
    elif args.subcommand == "unseal":
        out = _run_unseal(campaign, approval_dir)
    elif args.subcommand == "c4-holdout":
        out = _run_c4(campaign, matrix_rows, args.workers)
    elif args.subcommand == "close":
        out = _run_close(campaign, reveal=args.reveal_split_secret)
    else:  # pragma: no cover - argparse choices already constrains this
        out = {"result": "ERROR", "detail": f"unknown subcommand {args.subcommand!r}"}

    _print(out)
    return 0 if out.get("result") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
