"""`campaign/cli.py` のテスト: 武装プロトコル（`--armed` + env + Gate 1 承認）
と各サブコマンドの分岐。武装 render/measure を伴う経路のみ `@pytest.mark.slow`。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

import pytest

from voice_genesis.calibration.campaign import cli, holdout_stage, measure_stage, render_stage
from voice_genesis.calibration.campaign.caps import (
    CapCounters,
    CostCapExceededError,
    CountersCorruptError,
    cap_counters_from_ledger,
    cost_caps_from_manifest,
    counters_path,
    load_cap_counters,
    save_cap_counters,
)
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import candidates_for_meter
from voice_genesis.calibration.fixtures.matrix import build_matrix, declared_sweeps_by_family
from voice_genesis.calibration.vocab import MeterId, MissingReason, Split

from ._campaign_fixture import (
    _canonical_candidates_section,
    build_tiny_campaign,
    small_matrix_subset,
    write_gate1_approval,
)


def test_plan_subcommand_reports_design_totals_even_without_campaign(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(
        ["plan", "--campaign-dir", str(tmp_path / "nope"), "--secret-dir", str(tmp_path / "s")]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"instances_total": 2280' in out
    assert '"campaign_state": "UNAVAILABLE"' in out


def test_plan_subcommand_reports_realized_split_for_existing_campaign(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    exit_code = cli.main(
        ["plan", "--campaign-dir", str(campaign_dir), "--secret-dir", str(secret_root)]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"campaign_state": "OK"' in out
    assert "PREPARATION_VALID" in out


def test_mutating_subcommand_without_armed_shows_plan_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    exit_code = cli.main(
        ["c1-fixtures", "--campaign-dir", str(campaign_dir), "--secret-dir", str(secret_root)]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"result": "PLAN_ONLY"' in out


def test_mutating_subcommand_armed_without_env_or_approval_is_authorization_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    monkeypatch.delenv(cli.CAMPAIGN_ARMED_ENV_VAR, raising=False)
    approval_dir = tmp_path / "no-approvals"
    exit_code = cli.main(
        [
            "c1-fixtures",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    assert exit_code == 1
    out = capsys.readouterr().out
    assert '"result": "AUTHORIZATION_REQUIRED"' in out
    assert f"env:{cli.CAMPAIGN_ARMED_ENV_VAR}=1" in out


def test_mutating_subcommand_armed_with_env_but_no_gate1_is_authorization_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")
    approval_dir = tmp_path / "no-approvals"
    exit_code = cli.main(
        [
            "c1-fixtures",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    assert exit_code == 1
    out = capsys.readouterr().out
    assert '"result": "AUTHORIZATION_REQUIRED"' in out
    assert "approval_file:" in out


def test_unseal_and_close_dispatch_through_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "2" * 64, "payload": {}}
    )
    campaign.ledger.append(
        {"kind": "baseline_audited", "baseline_audit_sha": entry.entry_sha}
    )
    from voice_genesis.calibration.campaign import selection_stage
    from voice_genesis.calibration.selection import CandidateCriteria
    from voice_genesis.calibration.vocab import ClaimCeiling

    selection_stage.run_c3b_selection(
        campaign,
        {
            "TILT_GT": [
                CandidateCriteria(
                    candidate_id="M2T-HARMONIC-OLS-K4-WINhann",
                    ceiling=ClaimCeiling.ABSOLUTE,
                    primary_normalized_mae=0.05,
                    signed_bias=0.01,
                    primary_q95_ae=0.1,
                )
            ]
        },
        baseline_audit_entry_sha=entry.entry_sha,
    )

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    from ._campaign_fixture import write_gate3_approval

    write_gate3_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "unseal",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert '"result": "OK"' in out

    # seed a holdout_executed_valid event directly (holdout stage itself is
    # exercised at the unit level in test_campaign_holdout.py) so `close`
    # dispatch can be tested through the CLI too.
    results = [holdout_stage.diagnostic_only_close(m.value) for m in MeterId]
    holdout_stage.run_holdout_stage(campaign, results)

    exit_code = cli.main(
        [
            "close",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert '"result": "OK"' in out
    assert '"debt_discharged": false' in out


@pytest.mark.slow
def test_c1_fixtures_armed_end_to_end_via_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "c1-fixtures",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert '"result": "OK"' in out

    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "render"
    ]
    assert len(render_events) == 5  # 1 row x PROBE_REPEATS(5)


# ---------------------------------------------------------------------------
# round 30 ADOPT (`[UNDERSPEC-CAL-D67]`, Codex round 30 PR #343 finding #2
# 「Allow stable negative-control non-detections」採用) production E2E:
# real render + real `librosa.pyin` measurement (no fabricated
# `MeasurementRecord`/`MeterOutput`) on a genuine F0_CONTROL SILENCE negative
# control row, run through the real `cli._run_c3a` orchestration
# (`selection_stage.run_c3a_f0_selection` inside it, not a hand-built
# `CandidateCriteria`). Before the fix, pyin's real (deterministic, all-6-
# repeats) `OUTPUT_MISSING` on silence tripped `within_fresh_process_mismatch`
# and made F0-B0-CURRENT the only candidate ineligible, so
# `select_across_ceilings` had zero eligible candidates and C3a recorded
# `SELECTION_FAILED_CLOSED` — i.e. no candidate could ever pass a negative
# control.
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_c3a_f0_selection_passes_with_candidate_that_correctly_non_detects_on_silence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 F0_CONTROL TRUTH_CORE rows (n=3 lands exactly 1 in the SELECTION
    split under the default test split secret) + the family's 1 real SILENCE
    negative control row (included in C3a's instance set regardless of home
    split, `[UNDERSPEC-CAL-D37]`/`workunits.c3a_f0_selection_instances`).
    `librosa.pyin` genuinely finds no voiced frames on true silence and
    returns `OUTPUT_MISSING` for every within- and fresh-process repeat
    (`candidates/impl/f0_pyin.py::measure`) — deterministically, so this is
    not flaky."""
    from voice_genesis.calibration.fixtures.matrix import build_matrix

    all_rows = build_matrix()
    truth_rows = [
        mr
        for mr in all_rows
        if mr.row.family == "F0_CONTROL" and mr.row.block == "TRUTH_CORE"
    ][:3]
    silence_rows = [
        mr
        for mr in all_rows
        if mr.row.family == "F0_CONTROL" and mr.row.control_class == "SILENCE"
    ]
    assert silence_rows, "test setup requires a real F0_CONTROL SILENCE fixture row"
    subset = truth_rows + silence_rows

    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    from voice_genesis.calibration.candidates.registry import candidate_by_id

    only_b0 = (candidate_by_id("F0-B0-CURRENT"),)
    orig_candidates_for_meter = cli.candidates_for_meter

    def _trimmed_candidates_for_meter(meter):
        if meter is MeterId.F0_CONTROL:
            return only_b0
        return orig_candidates_for_meter(meter)

    monkeypatch.setattr(cli, "candidates_for_meter", _trimmed_candidates_for_meter)

    result = cli._run_c3a(campaign, subset, 1)
    assert result["result"] == "OK", result
    assert result["outcome"] == "SELECTED", result
    assert result["selected_candidate_id"] == "F0-B0-CURRENT"

    # confirm this really exercised the consistent-missing shape (not an
    # accidental finite reading on the silent row): every meter_call for the
    # SILENCE row's instances came back OUTPUT_MISSING.
    silence_row_ids = {mr.row_id for mr in silence_rows}
    meter_calls = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    silence_calls = [m for m in meter_calls if m["row_id"] in silence_row_ids]
    assert silence_calls
    assert all(m.get("missing_reason") == "OUTPUT_MISSING" for m in silence_calls)

    f0_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "f0_selection_frozen"
    ]
    assert f0_events
    fail_filters = f0_events[-1]["fail_filters_by_candidate"]["F0-B0-CURRENT"]
    assert fail_filters["within_fresh_process_mismatch"] is False
    assert fail_filters["negative_control_false_fire"] is False


# ---------------------------------------------------------------------------
# finding #5 (第 8 巡採用): Gate 1 承認の凍結 manifest への束縛
# ---------------------------------------------------------------------------


def test_gate1_approval_not_bound_to_frozen_manifest_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """凍結後に Gate 1 承認ファイルの中身だけ差し替えれば（3 要素武装は依然
    揃う）、`AUTHORIZATION_REQUIRED`（理由 `gate1_not_frozen_approval`）で
    拒否される。"""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    # tamper: same authorization_nonce (so a nonce-only check would not
    # catch this) but different content -> content sha256 diverges from the
    # frozen manifest's `approvals.gate1_sha256` pin.
    approval_path = approval_dir / "gate1_campaign_execution.json"
    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    payload["max_claim_scope"] = ["different_scope_than_frozen"]
    approval_path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")
    exit_code = cli.main(
        [
            "c1-fixtures",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert '"result": "AUTHORIZATION_REQUIRED"' in out
    assert "gate1_not_frozen_approval" in out

    # fail-closed with zero side effects: no renders/ledger growth.
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    assert len(campaign.ledger.entries) == 2  # c0_freeze + split_frozen (round 14 finding #1)
    assert not campaign.renders_dir.exists()


def test_gate1_approval_matching_frozen_manifest_is_accepted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対照実験: 差し替えなしのデフォルト承認ファイルは通る（拒否ロジックが
    過剰検出でないことの確認）。"""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "unseal",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    # phase-order (finding #6) refuses this before unseal_stage even runs
    # (SELECTION_FROZEN not reached yet) — the point here is only that it is
    # *not* refused for the gate1-binding reason.
    assert "gate1_not_frozen_approval" not in out
    assert exit_code == 1
    assert '"result": "PHASE_ORDER_VIOLATION"' in out


# ---------------------------------------------------------------------------
# finding #6 (第 8 巡採用): 手続 phase 順序の強制
# ---------------------------------------------------------------------------


def test_c2_before_c1_is_refused_by_phase_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "c2-baseline",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert '"result": "PHASE_ORDER_VIOLATION"' in out
    assert "c2-baseline_requires_FIXTURE_VALID" in out

    campaign = load_frozen_campaign(campaign_dir, secret_root)
    assert len(campaign.ledger.entries) == 2  # c0_freeze + split_frozen (round 14 finding #1)


def test_c3b_selection_after_unseal_is_refused_by_phase_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """finding #6 の明示例:「unseal 後に c3b-selection は不可」。ledger は
    拒否された呼び出しにより一切変化しない。"""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "2" * 64, "payload": {}}
    )
    campaign.ledger.append(
        {"kind": "baseline_audited", "baseline_audit_sha": entry.entry_sha}
    )
    campaign.ledger.append(
        {
            "kind": "f0_selection_frozen",
            "selected_candidate_id": "F0-B0-CURRENT",
            "outcome": "SELECTED",
        }
    )
    from voice_genesis.calibration.campaign import selection_stage
    from voice_genesis.calibration.selection import CandidateCriteria
    from voice_genesis.calibration.vocab import ClaimCeiling

    selection_stage.run_c3b_selection(
        campaign,
        {
            "TILT_GT": [
                CandidateCriteria(
                    candidate_id="M2T-HARMONIC-OLS-K4-WINhann",
                    ceiling=ClaimCeiling.ABSOLUTE,
                    primary_normalized_mae=0.05,
                    signed_bias=0.01,
                    primary_q95_ae=0.1,
                )
            ]
        },
        baseline_audit_entry_sha=entry.entry_sha,
    )

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    from ._campaign_fixture import write_gate3_approval

    write_gate3_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "unseal",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0, out

    entries_before = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)

    exit_code = cli.main(
        [
            "c3b-selection",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert '"result": "PHASE_ORDER_VIOLATION"' in out
    assert "c3b-selection_already_SELECTION_FROZEN" in out

    entries_after = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)
    assert entries_after == entries_before


# ---------------------------------------------------------------------------
# finding #1 (第 8 巡採用より前、レビュー本巡): frozen cost caps の enforcement
# ---------------------------------------------------------------------------


def test_c2_baseline_armed_wires_cost_caps_and_persisted_counters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    # C2 requires FIXTURE_VALID (phase order, finding #6) — fabricate it
    # directly rather than running a real (slow) C1.
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    captured: dict[str, object] = {}

    def _fake_run_baseline_stage(campaign, matrix_rows, *, max_workers, cap_counters, cost_caps, **_kw):
        # snapshot (not the live mutable reference): round 15 finding #5
        # (`[UNDERSPEC-CAL-D31]`) mutates this same `CapCounters` instance
        # again in `cli.main()`'s post-dispatch `finally` block (parent-side
        # CPU charge) — capturing the reference itself would let that later
        # mutation leak into these pre-dispatch assertions.
        captured["cap_counters"] = CapCounters(**cap_counters.as_dict())
        captured["cost_caps"] = cost_caps
        return {"baseline_audit_sha": "0" * 64}

    monkeypatch.setattr(cli.baseline_stage, "run_baseline_stage", _fake_run_baseline_stage)

    args = [
        "c2-baseline",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
    ]
    exit_code = cli.main(args)
    out = capsys.readouterr().out
    assert exit_code == 0, out

    # cost caps loaded from the frozen manifest's `frozen_design.cost_caps`
    # (embedded by `build_tiny_campaign()`'s default gate1_cost_caps).
    assert captured["cost_caps"] is not None
    assert captured["cost_caps"].compute == pytest.approx(36000.0)
    assert captured["cost_caps"].storage == 1_000_000_000
    # fresh campaign dir -> zero counters on first invocation.
    assert captured["cap_counters"] is not None
    assert captured["cap_counters"].compute_used == pytest.approx(0.0)

    # simulate a prior invocation having already consumed some cap budget
    # and persisted it to counters.json.
    save_cap_counters(campaign_dir, CapCounters(compute_used=5.0, storage_used=10, budget_used=0.0))

    exit_code = cli.main(args)
    out = capsys.readouterr().out
    assert exit_code == 0, out
    # counters persisted by the previous invocation are reloaded by this one.
    assert captured["cap_counters"].compute_used == pytest.approx(5.0)
    assert captured["cap_counters"].storage_used == 10


# ---------------------------------------------------------------------------
# round 13 finding #2 (`[UNDERSPEC-CAL-D26]`): persisted counters already
# over a frozen cap must refuse dispatch immediately, before any stage runs.
# ---------------------------------------------------------------------------


def test_persisted_cap_breach_refuses_dispatch_before_stage_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    # C2 requires FIXTURE_VALID (phase order, finding #6).
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    # simulate a prior invocation having already breached the frozen
    # compute cap (36000.0, `_campaign_fixture.DEFAULT_GATE1_COST_CAPS`) and
    # persisted the over-limit counters.
    save_cap_counters(
        campaign_dir, CapCounters(compute_used=999_999.0, storage_used=0, budget_used=0.0)
    )

    called = {"n": 0}

    def _fake_run_baseline_stage(*_args, **_kwargs):
        called["n"] += 1
        return {"baseline_audit_sha": "0" * 64}

    monkeypatch.setattr(cli.baseline_stage, "run_baseline_stage", _fake_run_baseline_stage)

    args = [
        "c2-baseline",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
    ]
    exit_code = cli.main(args)
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "COST_CAP_EXCEEDED"' in out
    assert called["n"] == 0  # zero renders/measurements performed

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    stop_events = [
        e
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0].payload.get("reason") == "COST_CAP_EXCEEDED"

    # a retry re-invocation must refuse again without appending a duplicate
    # stop_event (idempotent) and without performing any work.
    exit_code = cli.main(args)
    assert exit_code == 1
    assert called["n"] == 0
    reloaded2 = load_frozen_campaign(campaign_dir, secret_root)
    stop_events2 = [
        e
        for e in reloaded2.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events2) == 1


# ---------------------------------------------------------------------------
# round 20 採用 (3) (`[UNDERSPEC-CAL-D48]`): a persisted cap breach must
# still refuse a completed-stage retry (round 19 `[UNDERSPEC-CAL-D45]`
# no-op path), not silently return NOOP_ALREADY_COMPLETE.
# ---------------------------------------------------------------------------


def test_completed_stage_noop_retry_still_refuses_on_persisted_cap_breach(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """c1-fixtures retried after FIXTURE_VALID is already recorded (a true
    no-op per round 19 finding #3) must still be refused with
    COST_CAP_EXCEEDED when the persisted counters already breach the frozen
    cap — not silently return NOOP_ALREADY_COMPLETE and re-legitimize the
    breached state on every retry. Zero dispatch, zero new ledger events
    except the idempotent stop_event (not duplicated on a second retry)."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    # simulate a prior invocation having already breached the frozen
    # compute cap (36000.0, `_campaign_fixture.DEFAULT_GATE1_COST_CAPS`) and
    # persisted the over-limit counters.
    save_cap_counters(
        campaign_dir, CapCounters(compute_used=999_999.0, storage_used=0, budget_used=0.0)
    )

    called = {"n": 0}
    monkeypatch.setattr(
        render_stage,
        "run_render_stage",
        lambda *_a, **_k: called.__setitem__("n", called["n"] + 1) or [],
    )

    args = [
        "c1-fixtures",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
    ]
    entries_before = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)

    exit_code = cli.main(args)
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "COST_CAP_EXCEEDED"' in out
    assert "NOOP_ALREADY_COMPLETE" not in out
    assert called["n"] == 0  # zero dispatch: render_stage never invoked

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    new_entries = reloaded.ledger.entries[entries_before:]
    assert len(new_entries) == 1
    assert new_entries[0].payload.get("kind") == "stop_event"
    assert new_entries[0].payload.get("reason") == "COST_CAP_EXCEEDED"

    # a retry re-invocation must refuse again without appending a duplicate
    # stop_event (idempotent) — zero new events on the second retry.
    entries_before_2 = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)
    exit_code2 = cli.main(args)
    out2 = capsys.readouterr().out
    assert exit_code2 == 1, out2
    assert called["n"] == 0
    reloaded2 = load_frozen_campaign(campaign_dir, secret_root)
    assert len(reloaded2.ledger.entries) == entries_before_2


# ---------------------------------------------------------------------------
# finding #2 (レビュー本巡): 選択済み F0 の instance 単位フィード
# ---------------------------------------------------------------------------


def test_c3b_selection_without_f0_selection_frozen_is_refused(tmp_path: Path) -> None:
    subset = small_matrix_subset(1, family="APERIODICITY_GT")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "2" * 64, "payload": {}}
    )
    campaign.ledger.append(
        {"kind": "baseline_audited", "baseline_audit_sha": entry.entry_sha}
    )

    result = cli._run_c3b(campaign, subset, 1)
    assert result["result"] == "ERROR"
    assert "f0_selection_frozen" in result["detail"]

    # refusal is read-only: no selection-related events were appended.
    kinds = {e.payload.get("kind") for e in campaign.ledger.entries}
    assert "selection_frozen" not in kinds
    assert "candidate_space" not in kinds


@pytest.mark.slow
def test_c3b_selection_feeds_selected_f0_to_f0_dependent_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finding #2 の regression test: fabricated `f0_selection_frozen` event
    経由で、C3b が harmonic-residual（F0 依存候補）へ instance 単位の F0 を
    実際に渡す（fixture truth F0 ではなく、選択済み F0 candidate 自身をこの
    instance の audio 上で実測した値）。`n=2`: 既定 secret での実現済み split
    が SELECTION split の row を確実に含む最小件数（`n=1` は CALIBRATION の
    みに割り当たり C3b の対象外になる）。APERIODICITY_GT の候補プールは 24
    件と広いため、fresh-process subprocess 呼び出し数を抑えて実行時間を
    現実的に保つべく HARMONIC_RESIDUAL 1 件のみへ絞り込む（他 family は subset
    に該当 row が無いため元々 instances=() で軽い）。"""
    subset = small_matrix_subset(2, family="APERIODICITY_GT")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    baseline_entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "3" * 64, "payload": {}}
    )
    campaign.ledger.append(
        {"kind": "baseline_audited", "baseline_audit_sha": baseline_entry.entry_sha}
    )
    campaign.ledger.append(
        {
            "kind": "f0_selection_frozen",
            "selected_candidate_id": "F0-B0-CURRENT",
            "outcome": "SELECTED",
        }
    )

    from voice_genesis.calibration.fixtures.axes import FixtureFamily as _FixtureFamily

    harmonic_residual_ids = {
        c.candidate_id
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    }
    assert harmonic_residual_ids
    trimmed_pool = tuple(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if c.candidate_id in harmonic_residual_ids
    )[:1]
    orig_candidates_for_family = cli._candidates_for_family

    def _trimmed_candidates_for_family(family):
        if family is _FixtureFamily.APERIODICITY_GT:
            return trimmed_pool
        return orig_candidates_for_family(family)

    monkeypatch.setattr(cli, "_candidates_for_family", _trimmed_candidates_for_family)

    result = cli._run_c3b(campaign, subset, 1)
    assert result["result"] == "OK", result

    meter_calls = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"]
    residual_calls = [m for m in meter_calls if m.get("candidate_id") in harmonic_residual_ids]
    assert residual_calls
    # the F0-dependent candidate must have actually received f0_hz and
    # produced a real value — not OUTPUT_MISSING (what it produces when
    # params carries no usable f0_hz).
    assert all(m.get("missing_reason") != "OUTPUT_MISSING" for m in residual_calls)
    assert all("residual_fraction" in m.get("values", {}) for m in residual_calls)

    # the selected F0 candidate was actually measured on the APERIODICITY_GT
    # instance's own audio (never the fixture's truth F0, and never a
    # F0_CONTROL row — this subset contains none).
    f0_calls = [m for m in meter_calls if m.get("candidate_id") == "F0-B0-CURRENT"]
    assert f0_calls
    apgt_row_ids = {mr.row_id for mr in subset}
    assert all(m["row_id"] in apgt_row_ids for m in f0_calls)


# ---------------------------------------------------------------------------
# round 29 ADOPT (`[UNDERSPEC-CAL-D65]`): C3a SELECTION_FAILED_CLOSED (no F0
# winner) must block every F0-dependent candidate in C3b — with no F0 winner
# there is no per-instance F0 to measure or inject, so `_latest_f0_selection`
# returning `(True, None)` must not leave `f0_unusable_instances` empty (the
# round 27/28 D61/D63/D64 family closed on per-instance rejection only; this
# is the reopen exception the family terminal declaration names: "a new path
# where an unusable F0 reaches a candidate").
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_c3b_selection_blocks_f0_dependent_candidate_when_selection_failed_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C3a records `f0_selection_frozen` with `selected_candidate_id=None`
    (SELECTION_FAILED_CLOSED). C3b must then: (1) never call the
    F0-dependent candidate's `measure()` at all — zero `meter_call` events
    for it; (2) still run the non-dependent candidate in the same family
    normally; (3) record an explicit `measurement_missing` event for every
    expected dependent cell with `reason: "F0_SELECTION_FAILED"` (the round
    28 `[UNDERSPEC-CAL-D64]` mechanism, reused with a distinct reason from
    the per-instance `"F0_UNUSABLE"` case); (4) record one
    `f0_dependent_selection_blocked` provenance event; (5) make the
    dependent candidate ineligible via `coverage_incomplete` so it can never
    win `select_across_ceilings()`."""
    subset = small_matrix_subset(2, family="APERIODICITY_GT")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    render_stage.run_render_stage(campaign, subset, stage="c1")

    baseline_entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "5" * 64, "payload": {}}
    )
    campaign.ledger.append(
        {"kind": "baseline_audited", "baseline_audit_sha": baseline_entry.entry_sha}
    )
    campaign.ledger.append(
        {
            "kind": "f0_selection_frozen",
            "selected_candidate_id": None,
            "outcome": "SELECTION_FAILED_CLOSED",
        }
    )

    from voice_genesis.calibration.candidates.registry import candidate_by_id
    from voice_genesis.calibration.fixtures.axes import FixtureFamily as _FixtureFamily

    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    independent_candidate = candidate_by_id("M2A-B0-AUTOCORR-PERIODICITY")
    assert independent_candidate.algorithm_family not in measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES
    assert harmonic_residual.algorithm_family in measure_stage.F0_DEPENDENT_ALGORITHM_FAMILIES
    trimmed_pool = (harmonic_residual, independent_candidate)
    orig_candidates_for_family = cli._candidates_for_family

    def _trimmed_candidates_for_family(family):
        if family is _FixtureFamily.APERIODICITY_GT:
            return trimmed_pool
        return orig_candidates_for_family(family)

    monkeypatch.setattr(cli, "_candidates_for_family", _trimmed_candidates_for_family)

    result = cli._run_c3b(campaign, subset, 1)
    assert result["result"] == "OK", result

    meter_calls = [e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"]
    assert not any(m.get("candidate_id") == harmonic_residual.candidate_id for m in meter_calls)
    assert any(m.get("candidate_id") == independent_candidate.candidate_id for m in meter_calls)

    missing_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "measurement_missing"
    ]
    assert missing_events
    assert all(m["reason"] == "F0_SELECTION_FAILED" for m in missing_events)
    missing_candidates = {cell[2] for m in missing_events for cell in m["cells"]}
    assert missing_candidates == {harmonic_residual.candidate_id}

    blocked_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "f0_dependent_selection_blocked"
    ]
    assert len(blocked_events) == 1
    assert blocked_events[0]["stage"] == "c3b"
    assert blocked_events[0]["reason"] == "F0_SELECTION_FAILED"

    sf_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "selection_frozen"
    ]
    assert sf_events
    fail_filters = sf_events[-1]["fail_filters_by_family"]["APERIODICITY_GT"]
    assert fail_filters[harmonic_residual.candidate_id]["coverage_incomplete"] is True

    assert result["selected_by_family"]["APERIODICITY_GT"] != harmonic_residual.candidate_id


def test_c4_never_calls_f0_dependent_candidate_when_selection_failed_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth alongside C3b's guard (round 29 ADOPT,
    `[UNDERSPEC-CAL-D65]`): even if a stale/adversarial `selection_frozen`
    event already names an F0-dependent candidate as a family's
    `selected_by_family` winner (e.g. ledger state predating this fix, or a
    future regression reintroducing the C3b-side bug), C4 must still never
    let the candidate reach `measure()` — every C4 instance is named
    F0_UNUSABLE (reason `F0_SELECTION_FAILED`) and passed to
    `holdout_stage.render_and_measure_holdout()` *before* it is invoked, and
    the meter still closes through the design's `SELECTION_FAILED_CLOSED`
    vocabulary. `holdout_stage.render_and_measure_holdout()` itself is
    monkeypatched here (its real render/measure path requires the campaign
    to be through unseal with a canonical-sized matrix — exercised for a
    valid F0 winner by `test_c0_freeze.py`'s production E2E) so this test
    isolates C4's own instance-level guard, mirroring how C3b's guard is
    exercised directly above.

    Design line (`DESIGN_VG_METER_CAL_DEBT_v1.0.md` §11): 「selection 全
    fail → campaign は SELECTION_FAILED_CLOSED、meter は NOT_EVALUABLE。」"""
    subset = small_matrix_subset(4, family="APERIODICITY_GT")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    campaign.ledger.append(
        {
            "kind": "f0_selection_frozen",
            "selected_candidate_id": None,
            "outcome": "SELECTION_FAILED_CLOSED",
        }
    )

    from voice_genesis.calibration.campaign import workunits
    from voice_genesis.calibration.fixtures.axes import FixtureFamily as _FixtureFamily

    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    orig_candidates_for_family = cli._candidates_for_family

    def _trimmed_candidates_for_family(family):
        if family is _FixtureFamily.APERIODICITY_GT:
            return (harmonic_residual,)
        return orig_candidates_for_family(family)

    monkeypatch.setattr(cli, "_candidates_for_family", _trimmed_candidates_for_family)

    # The stale/adversarial `selection_frozen`: names the F0-dependent
    # candidate as the family's winner despite the failed F0 prerequisite —
    # exactly what C3b's own D65 guard prevents in the real flow (see
    # `test_c3b_selection_blocks_f0_dependent_candidate_when_selection_
    # failed_closed` above). This test isolates C4's independent guard.
    campaign.ledger.append(
        {
            "kind": "selection_frozen",
            "selected_by_family": {"APERIODICITY_GT": harmonic_residual.candidate_id},
        }
    )

    expected_instances = frozenset(
        workunits.c4_holdout_instances(
            subset, campaign.realized_split.assignment, family="APERIODICITY_GT"
        )
    )
    assert expected_instances, "test setup must realize a HOLDOUT-split APERIODICITY_GT instance"

    captured: dict[str, object] = {}

    def _capturing_render_and_measure_holdout(campaign_arg, matrix_rows_arg, **kwargs):
        captured["candidates_by_family"] = kwargs["candidates_by_family"]
        captured["f0_unusable_instances"] = kwargs["f0_unusable_instances"]
        captured["f0_missing_reason"] = kwargs["f0_missing_reason"]
        return {}

    monkeypatch.setattr(
        cli.holdout_stage, "render_and_measure_holdout", _capturing_render_and_measure_holdout
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    # the dependent candidate really is in the pool `render_and_measure_
    # holdout()` receives (proof this is the F0_UNUSABLE skip guard doing
    # the blocking, not simple absence from the candidate pool)...
    assert harmonic_residual.candidate_id in {
        c.candidate_id for c in captured["candidates_by_family"]["APERIODICITY_GT"]
    }
    # ...yet every one of its C4 instances is named unusable before the call.
    assert expected_instances <= captured["f0_unusable_instances"]
    assert captured["f0_missing_reason"] == "F0_SELECTION_FAILED"

    blocked_events = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "f0_dependent_selection_blocked"
    ]
    assert len(blocked_events) == 1
    assert blocked_events[0]["stage"] == "c4"
    assert blocked_events[0]["reason"] == "F0_SELECTION_FAILED"

    # `records_by_family` came back empty from the (mocked) measure step, so
    # every family — including APERIODICITY_GT despite its non-None
    # `selected_by_family` entry — closes through `selection_failed_closed_
    # meter()`: the design's NOT_EVALUABLE/OUTPUT_NOT_EVALUABLE vocabulary,
    # visible in the `holdout_executed_valid` event the close report reads.
    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    assert holdout_events
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "NOT_EVALUABLE"
    assert m2a_result["reason_code"] == "OUTPUT_NOT_EVALUABLE"


@pytest.mark.slow
def test_c4_f0_unusable_selected_candidate_through_real_holdout_path_closes_not_evaluable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """round 30 self-review ADOPT (finding #5(a), test gap): the production
    shape `test_c4_never_calls_f0_dependent_candidate_when_selection_failed_
    closed` above verifies via a monkeypatched `holdout_stage.
    render_and_measure_holdout` — this test drives the real function
    instead (real `render_stage.run_render_stage(stage="c4")` audio synth +
    real `measure_stage.run_measure_stage()` measurement, exactly what
    `render_and_measure_holdout()` itself calls), so the coverage check
    under test (`[UNDERSPEC-CAL-D66]`/`[UNDERSPEC-CAL-D69]`) is exercised
    against real `MeasurementRecord`s the real production function returns,
    not a hand-fabricated dict that could silently diverge from its actual
    shape.

    `render_stage._refuse_if_pre_unseal_holdout()` — the pre-unseal leakage
    guard `run_render_stage(stage="c4")` also calls — is stubbed out: that
    guard's `provenance.Ledger.check_leakage()` hard-requires the row-id set
    to equal the *full* canonical `fixtures.matrix.build_matrix()` (§7's
    "verification rows contain the complete canonical frozen matrix row-id
    set"), which a tiny per-test fixture can never satisfy, and a real
    unseal run against the full canonical matrix is far too expensive for a
    unit test — it is an orthogonal, already-dedicated-tested concern
    (`tests/test_render_stage.py`/`tests/test_provenance_unseal_
    prerequisites.py`), not what this test isolates (the CLI-side coverage
    check on real `MeasurementRecord`s)."""
    from voice_genesis.calibration.fixtures.axes import FixtureFamily as _FixtureFamily

    subset = small_matrix_subset(4, family="APERIODICITY_GT")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )

    # F0 selection genuinely failed closed (no F0 winner) — the deterministic
    # way to force every F0-dependent candidate's C4 instances F0_UNUSABLE
    # without needing a real F0_CONTROL family measurement in this tiny
    # fixture (mirrors the setup `test_c4_never_calls_f0_dependent_candidate_
    # when_selection_failed_closed` above uses).
    campaign.ledger.append(
        {
            "kind": "f0_selection_frozen",
            "selected_candidate_id": None,
            "outcome": "SELECTION_FAILED_CLOSED",
        }
    )
    campaign.ledger.append(
        {
            "kind": "selection_frozen",
            "selected_by_family": {"APERIODICITY_GT": harmonic_residual.candidate_id},
        }
    )

    trimmed_pool = (harmonic_residual, independent_candidate)
    orig_candidates_for_family = cli._candidates_for_family

    def _trimmed_candidates_for_family(family):
        if family is _FixtureFamily.APERIODICITY_GT:
            return trimmed_pool
        return orig_candidates_for_family(family)

    monkeypatch.setattr(cli, "_candidates_for_family", _trimmed_candidates_for_family)
    monkeypatch.setattr(render_stage, "_refuse_if_pre_unseal_holdout", lambda *a, **kw: None)

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    # real measurement really did run for the non-dependent B0 candidate
    # (proof this is the real, unmocked path — the family key IS present in
    # `records_by_family` via B0, unlike the fully-mocked sibling test above
    # whose `records_by_family == {}`).
    meter_calls = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "meter_call"
    ]
    assert any(m.get("candidate_id") == independent_candidate.candidate_id for m in meter_calls)
    assert not any(m.get("candidate_id") == harmonic_residual.candidate_id for m in meter_calls)

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    assert holdout_events
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "NOT_EVALUABLE"
    assert m2a_result["reason_code"] == "OUTPUT_NOT_EVALUABLE"
    assert m2a_result["selected_candidate_id"] == harmonic_residual.candidate_id
    assert "claim_scope" in m2a_result["gate_detail"]


# ---------------------------------------------------------------------------
# round 30 ADOPT (`[UNDERSPEC-CAL-D66]`, Codex round 30 PR #343 finding #1
# 「Require records from the selected holdout candidate」採用) → round 30
# self-review ADOPT (`[UNDERSPEC-CAL-D69]`/`[UNDERSPEC-CAL-D70]`, findings
# #2/#3/#4 採用): the `family.value not in records_by_family` guard in
# `_run_c4`'s per-family loop only proves *some* candidate in the family (B0
# always runs) produced a record — not that the *selected* candidate itself
# has any usable C4 output. Most of these tests fabricate
# `render_and_measure_holdout()`'s return value directly (real render/measure
# of a genuinely F0-unusable selected candidate is exercised end-to-end by
# `test_c3b_selection_blocks_f0_dependent_candidate_when_selection_failed_
# closed` above, and by `test_c4_f0_unusable_selected_candidate_through_
# real_holdout_path_closes_not_evaluable` immediately above this comment
# block (finding #5(a): drives the real C4 path, not a monkeypatched
# holdout, per the self-review's test-gap finding); this isolates the
# CLI-side coverage check itself, mirroring how `test_c4_never_calls_f0_
# dependent_candidate_when_selection_failed_closed` isolates the
# F0_UNUSABLE-instance guard).
#
# Design line (`DESIGN_VG_METER_CAL_DEBT_v1.0.md` §11): 「critical output
# 全欠損 or 最小数割れで score/gate 計算不能 → NOT_EVALUABLE/OUTPUT_NOT_
# EVALUABLE」対「score 計算可能だが PRIMARY 一部 output missing で gate
# 不通過 → DIAGNOSTIC_ONLY/OUTPUT_MISSING」——self-review round 30 MAJOR
# finding #2 が指摘したとおり、D66 は前者を「selected candidate の PRIMARY
# output が丸ごと**または一部**無い」場合の両方に適用しており、後者（部分
# 被覆）の帰結を取り違えていた。本 ADOPT は両者を分離する: 全欠損（usable
# instance が 0 件）のみ `NOT_EVALUABLE`/`OUTPUT_NOT_EVALUABLE`、部分被覆
# （1 件以上は usable だが期待集合の一部を欠く）は `DIAGNOSTIC_ONLY`/
# `OUTPUT_MISSING`（gate 1 不通過の事実を `gate_detail` に記録）。finding
# #3: 判定は record の有無ではなく**値の有無**（`measure_stage.
# primary_output_value()` + 有限性）。finding #4: `claim_scope_report()` を
# 分岐の前で計算し、NOT_EVALUABLE/DIAGNOSTIC_ONLY いずれの `gate_detail`
# にも含める。
# ---------------------------------------------------------------------------


def _c4_measurement_record(
    row_id: str, probe_index: int, candidate_id: str, *, field: str = "residual_fraction"
) -> measure_stage.MeasurementRecord:
    return measure_stage.MeasurementRecord(
        row_id=row_id,
        probe_index=probe_index,
        candidate_id=candidate_id,
        repeat_kind="within",
        repeat_index=0,
        process_id="p0",
        output=MeterOutput(values={field: 1.0}),
    )


def _c4_measurement_record_missing(
    row_id: str, probe_index: int, candidate_id: str
) -> measure_stage.MeasurementRecord:
    """finding #3: a record that *exists* (unlike the fully-skipped case
    above, where `render_and_measure_holdout()` never calls the candidate at
    all and the cell is simply absent from `records_by_family`) but carries
    no finite primary value — the shape a candidate that runs and correctly
    reports `OUTPUT_MISSING` leaves behind. The old record-presence-only
    check treated this as "usable"; the value-aware check must not."""
    return measure_stage.MeasurementRecord(
        row_id=row_id,
        probe_index=probe_index,
        candidate_id=candidate_id,
        repeat_kind="within",
        repeat_index=0,
        process_id="p0",
        output=MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING),
    )


def _aperiodicity_family_subset() -> list[Any]:
    """`APERIODICITY_GT` subset that declares **exactly one** def-A sweep
    (`fixtures.matrix.declared_sweeps_by_family()`): the chosen sweep's 6
    TRUTH_CORE rows + every non-TRUTH_CORE row of the family (CONFOUND +
    BOUNDARY + NEGATIVE_CONTROL). round 6-7 #344 ADOPT (`[UNDERSPEC-CAL-
    D76]`, supersedes D75's `_aperiodicity_single_sweep_subset()`): declared
    sweep membership is now def A (truth-core block rows sharing identical
    nuisance settings, truth level varying), not a nuisance axis
    (`nuisance_axis_family()` no longer exists) — so a single declared
    sweep is exactly "the TRUTH_CORE rows of one sweep, no other TRUTH_CORE
    rows in the subset" (any other family's/sweep's TRUTH_CORE row would
    declare a second sweep, and `gates.resolvable_pairs_possible()` demands
    *every* declared sweep clear the minimum independently — D76 ruling
    (2)). Excluding the other 9 sweeps' TRUTH_CORE rows keeps
    `expected_sweep_ids` a singleton. The non-TRUTH_CORE rows are kept
    (unlike D75's narrower 7-row subset) because split-coverage repair
    (`splitter._repair_coverage`) needs the family's fuller row population
    to find a feasible donor/victim assignment — a bare 6-row single-sweep
    subset raises `CoverageRepairInfeasible`."""
    family_rows = [mr for mr in build_matrix() if mr.row.family == "APERIODICITY_GT"]
    non_truth_core = [mr for mr in family_rows if mr.row.block != "TRUTH_CORE"]
    truth_core = [mr for mr in family_rows if mr.row.block == "TRUTH_CORE"]
    declared = declared_sweeps_by_family(family_rows)["APERIODICITY_GT"]
    sweep_id, member_row_ids = sorted(declared.items())[0]
    member_row_id_set = set(member_row_ids)
    sweep_truth_core = [mr for mr in truth_core if mr.row_id in member_row_id_set]
    assert len(sweep_truth_core) == len(member_row_ids) == 6, sweep_truth_core
    return sweep_truth_core + non_truth_core


def _force_rows_into_holdout(campaign: Any, row_ids: list[str]) -> Any:
    """Override `campaign.realized_split.assignment` so each of `row_ids` is
    unconditionally `Split.HOLDOUT`, leaving every other row's assignment as
    the (frozen-secret) stratified split already produced. `FrozenCampaign`/
    `RealizedSplitMap` are both frozen dataclasses, so this returns a new
    `campaign` object (`dataclasses.replace`) rather than mutating in place.

    Used by the DIRECTIONAL coverage tests below to deterministically place
    a chosen declared sweep's member rows into holdout — natural (50/25/25,
    `(block, domain)`-stratified) splitting of a handful of rows from one
    6-row `APERIODICITY_GT` sweep is not reliable enough (varies with which
    sweep is picked) to guarantee a specific holdout row count on demand."""
    import dataclasses

    new_assignment = dict(campaign.realized_split.assignment)
    for row_id in row_ids:
        new_assignment[row_id] = Split.HOLDOUT
    new_split = dataclasses.replace(campaign.realized_split, assignment=new_assignment)
    return dataclasses.replace(campaign, realized_split=new_split)


def _c4_setup_selected_candidate_coverage_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    select_directional: bool = False,
    subset_override: list[Any] | None = None,
) -> tuple[Any, Any, Any, frozenset[tuple[str, int]]]:
    """Shared scaffolding for the coverage tests below: a tiny campaign with
    `APERIODICITY_GT`'s HARMONIC_RESIDUAL candidate (F0-dependent, but F0
    dependency itself is irrelevant here — `render_and_measure_holdout` is
    monkeypatched directly below) and its `-B0-` sibling both in the pool.

    round 4 #344 ADOPT (`[UNDERSPEC-CAL-D74]`): `select_directional=False`
    (default, unchanged) names the ABSOLUTE-ceiling HARMONIC_RESIDUAL
    candidate as the family's selected candidate — the shape every
    pre-existing caller of this helper exercises. `select_directional=True`
    instead names the DIRECTIONAL-ceiling `-B0-` (`B0_CURRENT_HNR_APPROX`)
    candidate as selected, so the D74 DIRECTIONAL-only minimum-count branch
    can be exercised at this same CLI call site. `subset_override`
    (round 5 #344 ADOPT `[UNDERSPEC-CAL-D75]`) lets a caller pass a matrix
    subset that declares real sweeps (see `_aperiodicity_single_sweep_
    subset()`) instead of the plain TRUTH_CORE-only default — needed by the
    two `select_directional=True` DIRECTIONAL coverage tests below, since
    the minimum-count check now partitions usable instances by the real
    declared sweep set of whatever `matrix_rows` is passed to `_run_c4`.
    Returns `(campaign, subset, selected_candidate, expected_instances)`."""
    from voice_genesis.calibration.campaign import workunits
    from voice_genesis.calibration.fixtures.axes import FixtureFamily as _FixtureFamily
    from voice_genesis.calibration.vocab import ClaimCeiling

    subset = subset_override if subset_override is not None else small_matrix_subset(
        4, family="APERIODICITY_GT"
    )
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    campaign.ledger.append(
        {
            "kind": "f0_selection_frozen",
            "selected_candidate_id": "F0-B0-CURRENT",
            "outcome": "SELECTED",
        }
    )

    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    assert independent_candidate.claim_ceiling == ClaimCeiling.DIRECTIONAL
    assert harmonic_residual.claim_ceiling == ClaimCeiling.ABSOLUTE
    trimmed_pool = (harmonic_residual, independent_candidate)
    orig_candidates_for_family = cli._candidates_for_family

    def _trimmed_candidates_for_family(family):
        if family is _FixtureFamily.APERIODICITY_GT:
            return trimmed_pool
        return orig_candidates_for_family(family)

    monkeypatch.setattr(cli, "_candidates_for_family", _trimmed_candidates_for_family)

    selected_candidate = independent_candidate if select_directional else harmonic_residual
    campaign.ledger.append(
        {
            "kind": "selection_frozen",
            "selected_by_family": {"APERIODICITY_GT": selected_candidate.candidate_id},
        }
    )

    # avoid real per-instance F0 measurement (irrelevant to what this test
    # isolates — the CLI-side coverage check on `records_by_family`).
    monkeypatch.setattr(cli, "_build_f0_by_instance", lambda *a, **kw: ({}, frozenset()))

    expected_instances = frozenset(
        workunits.c4_holdout_instances(
            subset, campaign.realized_split.assignment, family="APERIODICITY_GT"
        )
    )
    assert expected_instances, "test setup must realize a HOLDOUT-split APERIODICITY_GT instance"
    return campaign, subset, selected_candidate, expected_instances


def test_c4_selected_candidate_fully_skipped_closes_not_evaluable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The selected (F0-dependent) candidate has zero holdout records for
    every expected instance — only B0 (always run) supplied any — the exact
    shape `render_and_measure_holdout()` leaves behind when
    `f0_unusable_instances` covers every C4 instance
    (`[UNDERSPEC-CAL-D61]`/`[UNDERSPEC-CAL-D65]`). The family key IS present
    in `records_by_family` (from B0), so the pre-fix `family.value not in
    records_by_family` guard alone would fall through and close
    DIAGNOSTIC_ONLY under `selected_id` — the fix must instead close
    NOT_EVALUABLE/OUTPUT_NOT_EVALUABLE."""
    campaign, subset, harmonic_residual, expected_instances = (
        _c4_setup_selected_candidate_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )

    b0_only_records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in sorted(expected_instances)
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": b0_only_records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    assert holdout_events
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "NOT_EVALUABLE"
    assert m2a_result["reason_code"] == "OUTPUT_NOT_EVALUABLE"
    assert m2a_result["selected_candidate_id"] == harmonic_residual.candidate_id
    # finding #4: claim_scope must be recorded even on the NOT_EVALUABLE
    # early-close branch (previously dropped by the early `continue`).
    assert "claim_scope" in m2a_result["gate_detail"]

    # the authoritative close report must carry the same terminal status
    # (close.close_campaign() copies `per_meter` from this event verbatim).
    close_result = cli.close_stage.close_campaign(campaign, holdout_events[-1])
    assert close_result.campaign_closed_entry_sha
    close_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "campaign_closed"
    ]
    assert close_events
    assert close_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]["terminal_status"] == (
        "NOT_EVALUABLE"
    )


def test_c4_selected_candidate_present_but_all_missing_values_closes_not_evaluable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finding #3: unlike the fully-skipped case above (candidate never
    called, cells absent), here the selected candidate *is* called on every
    expected instance and *does* produce a record for each — but every
    record is `OUTPUT_MISSING` (no finite primary value). The old
    record-presence-only check (`seen_holdout_instances = {(r.row_id,
    r.probe_index) for r in own_selected_records}`) would have treated this
    as full coverage and closed DIAGNOSTIC_ONLY; the value-aware check must
    still close NOT_EVALUABLE/OUTPUT_NOT_EVALUABLE (zero *usable* records)."""
    campaign, subset, harmonic_residual, expected_instances = (
        _c4_setup_selected_candidate_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )

    records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in sorted(expected_instances)
    ] + [
        _c4_measurement_record_missing(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in sorted(expected_instances)
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "NOT_EVALUABLE"
    assert m2a_result["reason_code"] == "OUTPUT_NOT_EVALUABLE"


def test_c4_selected_candidate_partially_covered_closes_diagnostic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 30 self-review ADOPT (finding #2, `[UNDERSPEC-CAL-D69]`): the
    selected candidate has usable records for *some* but not all of the
    expected C4 instances. Design line (`DESIGN_VG_METER_CAL_DEBT_v1.0.md`
    §11): 「score 計算可能だが PRIMARY 一部 output missing で gate 不通過 →
    DIAGNOSTIC_ONLY/OUTPUT_MISSING」——1 missing instance out of N does not by
    itself make the score uncomputable, so this must close DIAGNOSTIC_ONLY/
    OUTPUT_MISSING (with the gate-1 failure recorded in `gate_detail`), not
    NOT_EVALUABLE (that terminal status is reserved for zero usable records —
    see `test_c4_selected_candidate_fully_skipped_closes_not_evaluable`
    above)."""
    campaign, subset, harmonic_residual, expected_instances = (
        _c4_setup_selected_candidate_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    ordered_instances = sorted(expected_instances)
    partial_instances = ordered_instances[:-1]  # drop exactly one instance
    assert partial_instances, "test setup needs >=2 expected instances to show a partial gap"

    records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in ordered_instances
    ] + [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in partial_instances
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "DIAGNOSTIC_ONLY"
    assert m2a_result["reason_code"] == "OUTPUT_MISSING"
    assert m2a_result["selected_candidate_id"] == harmonic_residual.candidate_id
    # finding #4: the gate-1 coverage failure fact, and the claim_scope
    # audit fact (previously dropped on the NOT_EVALUABLE early `continue`),
    # must both be present in gate_detail.
    gate_detail = m2a_result["gate_detail"]
    assert gate_detail["expected_instance_count"] == len(expected_instances)
    assert gate_detail["seen_instance_count"] == len(partial_instances)
    assert "claim_scope" in gate_detail

    # the authoritative close report must carry the same terminal status.
    close_result = cli.close_stage.close_campaign(campaign, holdout_events[-1])
    assert close_result.campaign_closed_entry_sha
    close_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "campaign_closed"
    ]
    assert close_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]["terminal_status"] == (
        "DIAGNOSTIC_ONLY"
    )


def test_c4_absolute_partial_coverage_below_minimum_count_still_closes_diagnostic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 4 #344 ADOPT (`[UNDERSPEC-CAL-D74]`, amends `[UNDERSPEC-CAL-
    D73]`): D73 (round 3) applied the frozen minimum-count / resolvable-pair
    condition (`gates.MIN_RESOLVABLE_PAIRS_PER_SWEEP == 3`) to *every*
    meter's partial coverage, including ABSOLUTE-ceiling candidates — but
    that condition is a §10.4 DIRECTIONAL-gate concept ("resolvable pair は
    各 sweep で >= 3", design §10.4 ~L375). Design quote, §10.3 ABSOLUTE
    holdout gate (~L351-353): "gate 1: 全 PRIMARY instance が eligible
    （critical missing/undefined なし）" — §10.3 states no minimum sample
    count beyond that eligibility precondition, so an ABSOLUTE-ceiling
    candidate's MAE/BIAS/q95 (§10.1) are computable from any nonzero-usable-
    instance population. `HARMONIC_RESIDUAL` (`M2A-HARMONIC-RESIDUAL-*`) is
    an ABSOLUTE-ceiling candidate (asserted in the shared fixture below), so
    with 2 usable PRIMARY instances out of the 5 expected (the other 3
    explained by e.g. OUTPUT_MISSING misses), §11's "score 計算可能だが
    PRIMARY 一部 output missing で gate 不通過 → DIAGNOSTIC_ONLY/
    OUTPUT_MISSING" applies — not `NOT_EVALUABLE` (D73's now-corrected
    expectation for this exact fixture shape; the DIRECTIONAL-ceiling
    analogue is `test_c4_directional_partial_coverage_below_minimum_count_
    closes_not_evaluable` below)."""
    campaign, subset, harmonic_residual, expected_instances = (
        _c4_setup_selected_candidate_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    ordered_instances = sorted(expected_instances)
    usable_instances = ordered_instances[:2]  # only 2 usable -> below the D73 minimum
    assert len(expected_instances) > len(usable_instances) >= 2, (
        "test setup needs >=3 expected instances with exactly 2 usable to "
        "exercise the below-minimum branch"
    )

    records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in ordered_instances
    ] + [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in usable_instances
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "DIAGNOSTIC_ONLY"
    assert m2a_result["reason_code"] == "OUTPUT_MISSING"
    assert m2a_result["selected_candidate_id"] == harmonic_residual.candidate_id
    gate_detail = m2a_result["gate_detail"]
    assert gate_detail["expected_instance_count"] == len(expected_instances)
    assert gate_detail["seen_instance_count"] == len(usable_instances)
    # ABSOLUTE effective ceiling never hits the DIRECTIONAL-only minimum-count
    # branch, so its D73-era key must be absent here.
    assert "min_resolvable_pairs_per_sweep" not in gate_detail
    assert "claim_scope" in gate_detail

    # the authoritative close report must carry the same terminal status.
    close_result = cli.close_stage.close_campaign(campaign, holdout_events[-1])
    assert close_result.campaign_closed_entry_sha
    close_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "campaign_closed"
    ]
    assert close_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]["terminal_status"] == (
        "DIAGNOSTIC_ONLY"
    )


def test_c4_directional_partial_coverage_below_minimum_count_closes_not_evaluable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 4 #344 ADOPT (`[UNDERSPEC-CAL-D74]`): the DIRECTIONAL-ceiling
    counterpart of the ABSOLUTE test above — `M2A-B0-AUTOCORR-PERIODICITY`
    (`-B0-`, asserted DIRECTIONAL in the shared fixture) is named the
    family's selected candidate here (`select_directional=True`). §11's
    cascade (~L395-396: "critical output 全欠損 or 最小数割れで score/gate
    計算不能 → NOT_EVALUABLE/OUTPUT_NOT_EVALUABLE") applies, exactly as D73
    originally intended, now correctly scoped to a DIRECTIONAL effective
    ceiling.

    round 6-7 #344 ADOPT (`[UNDERSPEC-CAL-D76]` ruling (3), supersedes D75):
    the structural minimum-count check now counts DISTINCT TRUTH LEVELS
    (row-level, never probe repeats) among usable instances **within one
    declared sweep** (`fixtures.matrix.declared_sweeps_by_family()`, def
    A), not a raw usable-instance count. `usable_instances` below covers
    exactly 2 distinct rows of the chosen declared sweep
    (`_aperiodicity_holdout_sweep_row_ids()`) — 2 distinct truth levels, so
    at most C(2,2)=1 resolvable pair exists, below `gates.
    MIN_RESOLVABLE_PAIRS_PER_SWEEP == 3` regardless of how many probe
    instances or measured values are attached to those 2 rows."""
    campaign, subset, independent_candidate, _stale_expected_instances = (
        _c4_setup_selected_candidate_coverage_test(
            tmp_path,
            monkeypatch,
            select_directional=True,
            subset_override=_aperiodicity_family_subset(),
        )
    )
    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    from voice_genesis.calibration.campaign import workunits
    from voice_genesis.calibration.fixtures.controls import non_boundary_selection_instances
    from voice_genesis.calibration.vocab import Split as _Split

    _sweep_id, member_row_ids = sorted(declared_sweeps_by_family(subset)["APERIODICITY_GT"].items())[0]
    usable_row_ids = list(member_row_ids[:2])  # 2 distinct rows -> below minimum
    campaign = _force_rows_into_holdout(campaign, usable_row_ids)
    expected_instances = frozenset(
        workunits.c4_holdout_instances(
            subset, campaign.realized_split.assignment, family="APERIODICITY_GT"
        )
    )
    expected_primary_instances = non_boundary_selection_instances(
        subset, campaign.realized_split.assignment, _Split.HOLDOUT, family="APERIODICITY_GT"
    )
    usable_instances = [(row_id, 0) for row_id in usable_row_ids]
    assert all(inst in expected_instances for inst in usable_instances)
    ordered_instances = sorted(expected_instances)

    records = [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in ordered_instances
    ] + [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in usable_instances
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "NOT_EVALUABLE"
    assert m2a_result["reason_code"] == "OUTPUT_NOT_EVALUABLE"
    assert m2a_result["selected_candidate_id"] == independent_candidate.candidate_id
    gate_detail = m2a_result["gate_detail"]
    assert gate_detail["gate_detail_reason_code"] == "DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT"
    assert gate_detail["expected_instance_count"] == len(expected_primary_instances)
    assert gate_detail["seen_instance_count"] == len(usable_instances)
    assert gate_detail["min_resolvable_pairs_per_sweep"] == 3
    assert gate_detail["usable_truth_level_counts_by_sweep"].get(_sweep_id) == 2
    assert _sweep_id in gate_detail["sweeps_below_minimum"]
    assert gate_detail["effective_ceiling"] == "DIRECTIONAL"
    assert "claim_scope" in gate_detail

    # the authoritative close report must carry the same terminal status.
    close_result = cli.close_stage.close_campaign(campaign, holdout_events[-1])
    assert close_result.campaign_closed_entry_sha
    close_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "campaign_closed"
    ]
    assert close_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]["terminal_status"] == (
        "NOT_EVALUABLE"
    )


def test_c4_directional_sweep_with_repeated_truth_level_still_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UNDERSPEC-CAL-D76 ruling (3) — the specific defect a raw usable-
    instance count (D75's approach) would miss: multiple usable *instances*
    (probe repeats) at the SAME truth level (same row_id, several
    probe_index values) never add a second resolvable pair, because
    `Delta_truth(i,j) == 0` for same-row pairs is unresolvable by
    construction (§10.4). 5 usable probe instances, all on the SAME single
    row (1 distinct truth level) must still fail exactly like the
    below-minimum test above — not pass because "5 >= 3 usable
    instances"."""
    campaign, subset, independent_candidate, _stale_expected_instances = (
        _c4_setup_selected_candidate_coverage_test(
            tmp_path,
            monkeypatch,
            select_directional=True,
            subset_override=_aperiodicity_family_subset(),
        )
    )
    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    from voice_genesis.calibration.campaign import workunits

    _sweep_id, member_row_ids = sorted(declared_sweeps_by_family(subset)["APERIODICITY_GT"].items())[0]
    single_row_id = member_row_ids[0]
    campaign = _force_rows_into_holdout(campaign, [single_row_id])
    expected_instances = frozenset(
        workunits.c4_holdout_instances(
            subset, campaign.realized_split.assignment, family="APERIODICITY_GT"
        )
    )
    usable_instances = sorted(
        inst for inst in expected_instances if inst[0] == single_row_id
    )
    assert len(usable_instances) >= 3, (
        "test setup needs >=3 probe instances on the single chosen row",
        usable_instances,
    )
    ordered_instances = sorted(expected_instances)

    records = [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in ordered_instances
    ] + [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in usable_instances
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "NOT_EVALUABLE"
    assert m2a_result["reason_code"] == "OUTPUT_NOT_EVALUABLE"
    gate_detail = m2a_result["gate_detail"]
    assert gate_detail["gate_detail_reason_code"] == "DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT"
    # >=3 usable probe *instances*, but only 1 distinct truth level (row).
    assert gate_detail["seen_instance_count"] == len(usable_instances)
    assert gate_detail["usable_truth_level_counts_by_sweep"].get(_sweep_id) == 1


def test_c4_directional_partial_coverage_at_minimum_count_closes_diagnostic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 4 #344 ADOPT (`[UNDERSPEC-CAL-D74]`): a DIRECTIONAL effective
    ceiling with usable PRIMARY coverage that *does* structurally admit the
    frozen minimum is still partial coverage (many fewer usable instances
    than the family-wide expected set — see `missing_expected_instances`),
    so §11's "score 計算可能だが PRIMARY 一部 output missing で gate 不通過 →
    DIAGNOSTIC_ONLY/OUTPUT_MISSING" applies, mirroring the ABSOLUTE-ceiling
    control case `test_c4_selected_candidate_partially_covered_closes_
    diagnostic_only` but for a DIRECTIONAL-ceiling selected candidate.

    round 6-7 #344 ADOPT (`[UNDERSPEC-CAL-D76]` ruling (3), supersedes D75):
    `usable_instances` covers exactly 3 distinct rows of the chosen
    declared sweep (`_aperiodicity_holdout_sweep_row_ids()`) — 3 distinct
    truth levels, `C(3,2)=3 >= gates.MIN_RESOLVABLE_PAIRS_PER_SWEEP`, so the
    structural check passes and coverage completeness (family-wide, not
    sweep-scoped) is what determines DIAGNOSTIC_ONLY here."""
    campaign, subset, independent_candidate, _stale_expected_instances = (
        _c4_setup_selected_candidate_coverage_test(
            tmp_path,
            monkeypatch,
            select_directional=True,
            subset_override=_aperiodicity_family_subset(),
        )
    )
    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    from voice_genesis.calibration.campaign import workunits
    from voice_genesis.calibration.fixtures.controls import non_boundary_selection_instances
    from voice_genesis.calibration.vocab import Split as _Split

    _sweep_id, member_row_ids = sorted(declared_sweeps_by_family(subset)["APERIODICITY_GT"].items())[0]
    usable_row_ids = list(member_row_ids[:3])  # 3 distinct rows, C(3,2)=3
    campaign = _force_rows_into_holdout(campaign, usable_row_ids)
    expected_instances = frozenset(
        workunits.c4_holdout_instances(
            subset, campaign.realized_split.assignment, family="APERIODICITY_GT"
        )
    )
    expected_primary_instances = non_boundary_selection_instances(
        subset, campaign.realized_split.assignment, _Split.HOLDOUT, family="APERIODICITY_GT"
    )
    usable_instances = [(row_id, 0) for row_id in usable_row_ids]
    assert all(inst in expected_instances for inst in usable_instances)
    assert len(expected_primary_instances) - len(usable_instances) >= 1, (
        "test setup needs a genuine coverage gap to exercise partial coverage"
    )
    ordered_instances = sorted(expected_instances)

    records = [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in ordered_instances
    ] + [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in usable_instances
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "DIAGNOSTIC_ONLY"
    assert m2a_result["reason_code"] == "OUTPUT_MISSING"
    assert m2a_result["selected_candidate_id"] == independent_candidate.candidate_id
    gate_detail = m2a_result["gate_detail"]
    assert gate_detail["expected_instance_count"] == len(expected_primary_instances)
    assert gate_detail["seen_instance_count"] == len(usable_instances)
    assert "claim_scope" in gate_detail

    # the authoritative close report must carry the same terminal status.
    close_result = cli.close_stage.close_campaign(campaign, holdout_events[-1])
    assert close_result.campaign_closed_entry_sha
    close_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "campaign_closed"
    ]
    assert close_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]["terminal_status"] == (
        "DIAGNOSTIC_ONLY"
    )


def test_c4_selected_candidate_fully_covered_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control case: the selected candidate has a usable record for every
    expected C4 instance — the fix must not disturb this pre-existing
    DIAGNOSTIC_ONLY behavior ([UNDERSPEC-CAL-D17]: real gate assembly is out
    of D2 CLI scope, so a fully-covered selected candidate still closes
    DIAGNOSTIC_ONLY here, not CALIBRATED_ABSOLUTE)."""
    campaign, subset, harmonic_residual, expected_instances = (
        _c4_setup_selected_candidate_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    ordered_instances = sorted(expected_instances)

    records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in ordered_instances
    ] + [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in ordered_instances
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    per_meter = holdout_events[-1]["per_meter"]
    m2a_result = per_meter[MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "DIAGNOSTIC_ONLY"
    assert m2a_result["selected_candidate_id"] == harmonic_residual.candidate_id


# ---------------------------------------------------------------------------
# round 2 #344 ADOPT (`[UNDERSPEC-CAL-D72]`, amends D69) "Restrict holdout
# coverage failures to PRIMARY instances": the D66/D69 coverage set above was
# built from `workunits.c4_holdout_instances()`, which excludes only
# *negative-control* rows — it still includes non-control BOUNDARY-domain
# rows (boundary-axis probes). A correct, expected miss on one of those
# BOUNDARY instances therefore fell into `missing_expected_instances` and
# falsely produced a coverage-failure status, even though DESIGN_VG_METER_
# CAL_DEBT_v1.0.md §10.3 (~L351-361) scopes gate 1 to PRIMARY instances only
# ("gate 1: 全 PRIMARY instance が eligible") and treats BOUNDARY separately.
# The 3 tests below share a fixture with one PRIMARY-domain HOLDOUT row and
# one BOUNDARY-domain (non-control) HOLDOUT row for the same family, so a
# miss can be placed on exactly one domain at a time.
# ---------------------------------------------------------------------------


def _c4_setup_boundary_and_primary_coverage_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, Any, frozenset[tuple[str, int]], frozenset[tuple[str, int]]]:
    """Shared scaffolding for the 3 D72 tests below. Returns `(campaign,
    subset, harmonic_residual, primary_expected_instances,
    boundary_only_instances)` — `primary_expected_instances` is the PRIMARY-
    domain HOLDOUT population (what `non_boundary_selection_instances()`
    yields), `boundary_only_instances` is the BOUNDARY-domain (non-control)
    HOLDOUT population that `workunits.c4_holdout_instances()` includes but
    `non_boundary_selection_instances()` excludes."""
    from voice_genesis.calibration.campaign import workunits
    from voice_genesis.calibration.fixtures.axes import FixtureFamily as _FixtureFamily
    from voice_genesis.calibration.fixtures.controls import non_boundary_selection_instances
    from voice_genesis.calibration.vocab import Split

    all_rows = _cli_build_matrix()
    boundary_rows = [
        mr
        for mr in all_rows
        if mr.row.family == "APERIODICITY_GT" and mr.row.block == "BOUNDARY"
    ]
    assert boundary_rows, "test setup needs >=1 non-control BOUNDARY row for the family"
    subset = small_matrix_subset(4, family="APERIODICITY_GT") + boundary_rows
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    campaign.ledger.append(
        {
            "kind": "f0_selection_frozen",
            "selected_candidate_id": "F0-B0-CURRENT",
            "outcome": "SELECTED",
        }
    )

    harmonic_residual = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    trimmed_pool = (harmonic_residual, independent_candidate)
    orig_candidates_for_family = cli._candidates_for_family

    def _trimmed_candidates_for_family(family):
        if family is _FixtureFamily.APERIODICITY_GT:
            return trimmed_pool
        return orig_candidates_for_family(family)

    monkeypatch.setattr(cli, "_candidates_for_family", _trimmed_candidates_for_family)

    campaign.ledger.append(
        {
            "kind": "selection_frozen",
            "selected_by_family": {"APERIODICITY_GT": harmonic_residual.candidate_id},
        }
    )
    monkeypatch.setattr(cli, "_build_f0_by_instance", lambda *a, **kw: ({}, frozenset()))

    assignment = campaign.realized_split.assignment
    primary_expected = non_boundary_selection_instances(
        subset, assignment, Split.HOLDOUT, family="APERIODICITY_GT"
    )
    all_holdout = frozenset(
        workunits.c4_holdout_instances(subset, assignment, family="APERIODICITY_GT")
    )
    boundary_only = all_holdout - primary_expected
    assert primary_expected, "test setup must realize a HOLDOUT-split PRIMARY instance"
    assert boundary_only, "test setup must realize a HOLDOUT-split BOUNDARY instance"
    return campaign, subset, harmonic_residual, primary_expected, boundary_only


def _cli_build_matrix():
    from voice_genesis.calibration.fixtures.matrix import build_matrix

    return build_matrix()


def test_c4_boundary_only_miss_is_not_a_coverage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 2 #344 ADOPT P2 (`[UNDERSPEC-CAL-D72]`): the selected candidate
    fully covers every PRIMARY instance but is correctly, explainedly
    missing on the BOUNDARY-only instance — this must close exactly like the
    fully-covered control case (`test_c4_selected_candidate_fully_covered_
    is_unchanged`), with no coverage-failure `reason`/`reason_code` at all."""
    campaign, subset, harmonic_residual, primary_expected, boundary_only = (
        _c4_setup_boundary_and_primary_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    all_instances = sorted(primary_expected | boundary_only)

    records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in all_instances
    ] + [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in sorted(primary_expected)
    ] + [
        _c4_measurement_record_missing(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in sorted(boundary_only)
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    m2a_result = holdout_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "DIAGNOSTIC_ONLY"
    assert m2a_result["reason_code"] is None
    assert "reason" not in m2a_result["gate_detail"]
    assert m2a_result["selected_candidate_id"] == harmonic_residual.candidate_id


def test_c4_primary_partial_coverage_closes_diagnostic_only_boundary_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 2 #344 ADOPT P2 (`[UNDERSPEC-CAL-D72]`): one missing PRIMARY
    instance (BOUNDARY fully covered, to isolate the PRIMARY-only scoping)
    closes `DIAGNOSTIC_ONLY`/`OUTPUT_MISSING`, and `gate_detail`'s expected/
    seen counts must be PRIMARY-only (not inflated by the BOUNDARY
    instance)."""
    campaign, subset, harmonic_residual, primary_expected, boundary_only = (
        _c4_setup_boundary_and_primary_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    all_instances = sorted(primary_expected | boundary_only)
    ordered_primary = sorted(primary_expected)
    partial_primary = ordered_primary[:-1]  # drop exactly one PRIMARY instance
    assert partial_primary, "test setup needs >=2 PRIMARY instances to show a partial gap"

    records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in all_instances
    ] + [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in partial_primary
    ] + [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in sorted(boundary_only)
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    m2a_result = holdout_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "DIAGNOSTIC_ONLY"
    assert m2a_result["reason_code"] == "OUTPUT_MISSING"
    gate_detail = m2a_result["gate_detail"]
    # PRIMARY-only counts: the BOUNDARY instance's 5 probes must not be
    # folded into either count.
    assert gate_detail["expected_instance_count"] == len(primary_expected)
    assert gate_detail["seen_instance_count"] == len(partial_primary)


def test_c4_primary_all_missing_closes_not_evaluable_despite_usable_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 2 #344 ADOPT P2 (`[UNDERSPEC-CAL-D72]`): the selected candidate
    has zero usable output on every PRIMARY instance but *does* have a
    usable output on the BOUNDARY-only instance. This must still close
    `NOT_EVALUABLE`/`OUTPUT_NOT_EVALUABLE` — a usable BOUNDARY record must
    not paper over zero usable PRIMARY coverage (the `usable_holdout_
    instances` check below is not itself domain-scoped, since it also has to
    catch the plain "candidate never called at all" shape)."""
    campaign, subset, harmonic_residual, primary_expected, boundary_only = (
        _c4_setup_boundary_and_primary_coverage_test(tmp_path, monkeypatch)
    )
    independent_candidate = next(
        c for c in candidates_for_meter(MeterId.M2_APERIODICITY) if "-B0-" in c.candidate_id
    )
    all_instances = sorted(primary_expected | boundary_only)

    records = [
        _c4_measurement_record(
            row_id, probe_index, independent_candidate.candidate_id, field="hnr_db"
        )
        for row_id, probe_index in all_instances
    ] + [
        _c4_measurement_record_missing(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in sorted(primary_expected)
    ] + [
        _c4_measurement_record(row_id, probe_index, harmonic_residual.candidate_id)
        for row_id, probe_index in sorted(boundary_only)
    ]
    monkeypatch.setattr(
        cli.holdout_stage,
        "render_and_measure_holdout",
        lambda *a, **kw: {"APERIODICITY_GT": records},
    )

    result = cli._run_c4(campaign, subset, 1)
    assert result["result"] == "OK", result

    holdout_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "holdout_executed_valid"
    ]
    m2a_result = holdout_events[-1]["per_meter"][MeterId.M2_APERIODICITY.value]
    assert m2a_result["terminal_status"] == "NOT_EVALUABLE"
    assert m2a_result["reason_code"] == "OUTPUT_NOT_EVALUABLE"
    assert m2a_result["gate_detail"]["seen_instance_count"] == 0


# ---------------------------------------------------------------------------
# round 14 finding #3: F0 repeat-record reuse must reject duplicates/partial
# coverage as stale, never average them into two_stage_median().
# ---------------------------------------------------------------------------


def _fake_meter_call(
    candidate_id: str, row_id: str, probe_index: int, repeat_kind: str, repeat_index: int, value: float
) -> dict[str, object]:
    return {
        "kind": "meter_call",
        "row_id": row_id,
        "probe_index": probe_index,
        "candidate_id": candidate_id,
        "repeat_kind": repeat_kind,
        "repeat_index": repeat_index,
        **measure_stage.meter_output_to_dict(MeterOutput(values={"f0_hz": value})),
    }


def _fake_missing_meter_call(
    candidate_id: str, row_id: str, probe_index: int, repeat_kind: str, repeat_index: int
) -> dict[str, object]:
    """round 28 ADOPT (1) (`[UNDERSPEC-CAL-D63]`): a repeat where the
    selected F0 meter returned `OUTPUT_MISSING` (no `f0_hz` key in
    `values` at all — the pre-fix `.get("f0_hz")` in `_build_f0_by_
    instance()` then reads `None` and silently skips the repeat instead of
    counting it toward unusability)."""
    return {
        "kind": "meter_call",
        "row_id": row_id,
        "probe_index": probe_index,
        "candidate_id": candidate_id,
        "repeat_kind": repeat_kind,
        "repeat_index": repeat_index,
        **measure_stage.meter_output_to_dict(MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)),
    }


def test_f0_reuse_refuses_duplicated_repeat_record_as_stale(tmp_path: Path) -> None:
    """round 14 finding #3 regression: `cli._reusable_f0_values_by_process`
    (used by `_build_f0_by_instance` for C3b/C4's F0 reuse) previously did
    its own subset-containment ledger scan and unconditionally appended
    every matching value — a duplicated `meter_call` for the same
    `(repeat_kind, repeat_index)` key slipped past `_completed_meter_call_
    records()`'s duplicate-key rejection and fed an extra f0_hz value into
    `two_stage_median()`. It now delegates to that shared reconstruction
    helper directly, so the same duplicate must refuse with the stale code
    (`StaleMeasurementError`) and compute no F0 for the instance."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    candidate_id = "F0-B0-CURRENT"
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "within", i, 100.0 + i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "fresh", i, 200.0 + i))
    # duplicate ledger entry for the same (repeat_kind="within", repeat_index=0) key.
    campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "within", 0, 999.0))
    entries_before = len(campaign.ledger.entries)

    with pytest.raises(measure_stage.StaleMeasurementError):
        cli._build_f0_by_instance(
            campaign,
            [("r1", 0)],
            candidate_id,
            {"r1": 48000},
            max_workers=1,
            cap_counters=None,
            cost_caps=None,
            stage="c3b",
        )

    # refusal is read-only: no F0 was computed, and no new ledger entry was
    # appended chasing a fresh re-measurement of an already-duplicated key.
    assert len(campaign.ledger.entries) == entries_before


def test_f0_reuse_accepts_exactly_complete_non_duplicated_coverage(tmp_path: Path) -> None:
    """Companion to the duplicate-refusal test above: exactly within3+fresh3,
    no duplicates, is reused (not refused, not re-measured)."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    candidate_id = "F0-B0-CURRENT"
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "within", i, 100.0))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "fresh", i, 100.0))
    entries_before = len(campaign.ledger.entries)

    result, unusable = cli._build_f0_by_instance(
        campaign,
        [("r1", 0)],
        candidate_id,
        {"r1": 48000},
        max_workers=1,
        cap_counters=None,
        cost_caps=None,
        stage="c3b",
    )
    assert result == {("r1", 0): pytest.approx(100.0)}
    assert unusable == frozenset()
    # reused, not re-measured: no new ledger entries.
    assert len(campaign.ledger.entries) == entries_before


# ---------------------------------------------------------------------------
# round 27 ADOPT (1) (`[UNDERSPEC-CAL-D61]`) "Reject unusable F0 values
# before downstream injection": a non-finite/non-positive f0_hz repeat
# (durably round-tripped through the ledger since round 26,
# `[UNDERSPEC-CAL-D58]`) must never reach `f0_by_instance` — the instance is
# excluded and recorded as unusable (with an `f0_injection_rejected` ledger
# event) instead.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    [math.nan, math.inf, 0.0, -1.0],
    ids=["nan", "inf", "zero", "negative"],
)
def test_f0_injection_rejects_unusable_repeat(tmp_path: Path, bad_value: float) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    candidate_id = "F0-B0-CURRENT"
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "within", i, 100.0))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        # 1 of the 3 fresh repeats carries the unusable value; the rest are
        # ordinary valid F0 -- a single bad repeat must reject the whole
        # instance, not just get outvoted by the two-stage median.
        value = bad_value if i == 0 else 100.0
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "fresh", i, value))
    entries_before = len(campaign.ledger.entries)

    result, unusable = cli._build_f0_by_instance(
        campaign,
        [("r1", 0)],
        candidate_id,
        {"r1": 48000},
        max_workers=1,
        cap_counters=None,
        cost_caps=None,
        stage="c3b",
    )
    assert result == {}
    assert unusable == frozenset({("r1", 0)})
    # read-only refusal: still reused from the existing ledger coverage, no
    # re-measurement.
    assert len(campaign.ledger.entries) == entries_before + 1  # + the rejection event

    rejected = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "f0_injection_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["stage"] == "c3b"
    assert rejected[0]["reason"] == "F0_UNUSABLE"
    assert rejected[0]["instances"] == [["r1", 0]]


def test_f0_injection_accepts_instance_when_every_repeat_is_valid(tmp_path: Path) -> None:
    """Companion to the rejection test above: an instance with an entirely
    valid, finite, strictly-positive F0 repeat set is unaffected — it flows
    through to `f0_by_instance` and is never recorded as unusable."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    candidate_id = "F0-B0-CURRENT"
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "within", i, 150.0))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "fresh", i, 150.0))

    result, unusable = cli._build_f0_by_instance(
        campaign,
        [("r1", 0)],
        candidate_id,
        {"r1": 48000},
        max_workers=1,
        cap_counters=None,
        cost_caps=None,
        stage="c4",
    )
    assert result == {("r1", 0): pytest.approx(150.0)}
    assert unusable == frozenset()
    assert not [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "f0_injection_rejected"
    ]


# ---------------------------------------------------------------------------
# round 28 ADOPT (1) (`[UNDERSPEC-CAL-D63]`) "Mark empty F0 measurements
# unusable": when the selected F0 meter returns `OUTPUT_MISSING` for every
# repeat, `by_process` comes back empty and must be treated as F0_UNUSABLE
# (not silently dropped from both `result` and `unusable`).
# ---------------------------------------------------------------------------


def test_f0_injection_rejects_instance_when_every_repeat_is_output_missing(
    tmp_path: Path,
) -> None:
    """The completion-of-D61 case: all 6 repeats are `OUTPUT_MISSING` (no
    `f0_hz` at all, not merely non-finite/non-positive). Before this fix,
    `_build_f0_by_instance()`'s `if not by_process: continue` silently
    dropped the instance from both `result` and `unusable` — the dependent
    F0-consuming candidate then ran with no injected F0 and its internal
    fallback (`formant_cepstral.py`'s default lifter cutoff) produced a
    plausible result instead of being skipped."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    candidate_id = "F0-B0-CURRENT"
    for i in range(measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_missing_meter_call(candidate_id, "r1", 0, "within", i))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_missing_meter_call(candidate_id, "r1", 0, "fresh", i))
    entries_before = len(campaign.ledger.entries)

    result, unusable = cli._build_f0_by_instance(
        campaign,
        [("r1", 0)],
        candidate_id,
        {"r1": 48000},
        max_workers=1,
        cap_counters=None,
        cost_caps=None,
        stage="c3b",
    )
    assert result == {}
    assert unusable == frozenset({("r1", 0)})
    assert len(campaign.ledger.entries) == entries_before + 1  # + the rejection event

    rejected = [
        e.payload
        for e in campaign.ledger.entries
        if e.payload.get("kind") == "f0_injection_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0]["stage"] == "c3b"
    assert rejected[0]["reason"] == "F0_UNUSABLE"
    assert rejected[0]["instances"] == [["r1", 0]]


def test_f0_injection_rejects_instance_when_one_repeat_is_output_missing(
    tmp_path: Path,
) -> None:
    """A single `OUTPUT_MISSING` repeat among otherwise-valid repeats must
    also reject the whole instance — `_reusable_f0_values_by_process()`
    already returns `None` for a partially-missing set (falling through to
    `run_measurement_for_instance`'s resume, which reconstructs the same
    partial-`f0_hz` record set from the ledger), so `by_process` again comes
    back with the missing repeat simply absent from every process group,
    not merely outvoted."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    candidate_id = "F0-B0-CURRENT"
    campaign.ledger.append(_fake_missing_meter_call(candidate_id, "r1", 0, "within", 0))
    for i in range(1, measure_stage.WITHIN_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "within", i, 150.0))
    for i in range(measure_stage.FRESH_PROCESS_REPEATS):
        campaign.ledger.append(_fake_meter_call(candidate_id, "r1", 0, "fresh", i, 150.0))

    result, unusable = cli._build_f0_by_instance(
        campaign,
        [("r1", 0)],
        candidate_id,
        {"r1": 48000},
        max_workers=1,
        cap_counters=None,
        cost_caps=None,
        stage="c3b",
    )
    assert result == {}
    assert unusable == frozenset({("r1", 0)})


# ---------------------------------------------------------------------------
# finding #7 (第 9 巡採用): canonical path 照合
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subcommand", ["c1-fixtures", "c2-baseline", "unseal", "c4-holdout", "close"]
)
def test_canonical_path_mismatch_refuses_every_mutating_subcommand(
    subcommand: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """finding #7 の regression test: manifest に実ファイルの sha256 を
    埋め込んだ campaign で、1 カテゴリの 1 file の pin だけを不正な値へ
    差し替える（**実ファイルは一切変更しない** — 改竄は manifest 側の pin
    のみ）。全 subcommand が `BLOCKED_CANONICAL_MUTATION_REQUIRED` で拒否
    され、ledger に対応する `stop_event` が記帳される。"""
    tampered = {k: dict(v) for k, v in _canonical_candidates_section().items()}
    rel_path = next(iter(tampered["schema_paths_sha256"]))
    tampered["schema_paths_sha256"][rel_path] = "0" * 64

    campaign_dir, secret_root = build_tiny_campaign(
        tmp_path, canonical_candidates_section=tampered
    )
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            subcommand,
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert '"result": "BLOCKED_CANONICAL_MUTATION_REQUIRED"' in out
    assert rel_path in out

    campaign = load_frozen_campaign(campaign_dir, secret_root)
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert any(e.get("reason") == "BLOCKED_CANONICAL_MUTATION_REQUIRED" for e in stop_events)


# ---------------------------------------------------------------------------
# round 17 finding #2 (`[UNDERSPEC-CAL-D38]`): environment drift refusal
# ---------------------------------------------------------------------------


def _current_dependencies_manifest_section() -> dict[str, str]:
    import platform as _platform
    from importlib import metadata as _importlib_metadata

    section = {"python_version": _platform.python_version()}
    for key, package_name in cli._DEPENDENCY_PACKAGE_BY_MANIFEST_KEY.items():
        try:
            section[key] = _importlib_metadata.version(package_name)
        except _importlib_metadata.PackageNotFoundError:
            section[key] = "ABSENT:not_installed"
    return section


def test_environment_drift_violations_empty_when_no_dependencies_section() -> None:
    """`build_tiny_campaign()`'s default fixture manifest carries no
    `dependencies` key at all — this must not be treated as a mismatch
    (that would spuriously block every existing CLI unit test)."""
    campaign = type("_C", (), {"manifest": {}})()
    assert cli._environment_drift_violations(campaign) == ()


def test_environment_drift_violations_empty_when_manifest_matches_runtime() -> None:
    campaign = type(
        "_C", (), {"manifest": {"dependencies": _current_dependencies_manifest_section()}}
    )()
    assert cli._environment_drift_violations(campaign) == ()


def test_environment_drift_violations_detects_python_and_package_mismatch() -> None:
    dependencies = _current_dependencies_manifest_section()
    dependencies["python_version"] = "0.0.0"
    dependencies["numpy_version"] = "0.0.0"
    campaign = type("_C", (), {"manifest": {"dependencies": dependencies}})()
    violations = cli._environment_drift_violations(campaign)
    assert any(v.startswith("python_version: manifest=0.0.0") for v in violations)
    assert any(v.startswith("numpy_version: manifest=0.0.0") for v in violations)
    # untouched deps must not spuriously appear.
    assert not any(v.startswith("scipy_version") for v in violations)


def test_environment_drift_armed_dispatch_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    dependencies = _current_dependencies_manifest_section()
    dependencies["numpy_version"] = "0.0.0-drifted"
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, dependencies=dependencies)
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "c1-fixtures",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 1
    assert '"result": "BLOCKED_ENVIRONMENT_DRIFT"' in out
    assert "numpy_version" in out

    campaign = load_frozen_campaign(campaign_dir, secret_root)
    stop_events = [
        e.payload for e in campaign.ledger.entries if e.payload.get("kind") == "stop_event"
    ]
    assert any(e.get("reason") == "BLOCKED_ENVIRONMENT_DRIFT" for e in stop_events)


def test_environment_drift_matching_dependencies_is_not_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対照実験: 実行環境と一致する `dependencies` は block しない（過剰検出で
    ないことの確認）。unarmed `plan` は drift 無しを報告する。"""
    dependencies = _current_dependencies_manifest_section()
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, dependencies=dependencies)

    plan_exit = cli.main(
        ["plan", "--campaign-dir", str(campaign_dir), "--secret-dir", str(secret_root)]
    )
    out = json.loads(capsys.readouterr().out)
    assert plan_exit == 0
    assert out["environment_drift"] == []


@pytest.mark.slow
def test_canonical_path_match_is_not_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """対照実験: 差し替えなしのデフォルト（実ファイルの実 hash）は canonical
    path 照合を通る（拒否ロジックが過剰検出でないことの確認）。armed
    c1-fixtures を実際に最後まで走らせて確認するため slow（real render を
    伴う — n=1 の最小 subset で抑える）。"""
    subset = small_matrix_subset(1, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "c1-fixtures",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert "BLOCKED_CANONICAL_MUTATION_REQUIRED" not in out
    assert exit_code == 0, out


@pytest.mark.slow
def test_c1_fixtures_time_budget_partial_slice_then_resume_via_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2（design memo `design_runner_robustness.md`, `[UNDERSPEC-CAL-D79]`）
    end-to-end via the CLI: `--time-budget-seconds` (an essentially-zero
    budget) together with `--workers 3` (harmless for c1 — render has no
    worker-pool wiring — the memo's acceptance test just confirms the two
    flags coexist without error) exits 0 with a `PARTIAL_SLICE` report and
    NO `stage_summary`/`fixture_valid` ledger events; re-running the exact
    same command (no budget change needed — the flag can simply be dropped,
    but this test keeps it to also exercise "0 remaining still transitions")
    resumes and completes."""
    subset = small_matrix_subset(2, family="F0_CONTROL")
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, subset=subset)
    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    base_args = [
        "c1-fixtures",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
        "--workers",
        "3",
    ]

    exit_code = cli.main([*base_args, "--time-budget-seconds", "0.01"])
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0, out
    assert out["result"] == "PARTIAL_SLICE"
    assert out["stage"] == "c1-fixtures"
    assert out["slice"]["instances_completed_this_run"] >= 1
    assert out["slice"]["instances_remaining"] > 0
    assert out["slice"]["time_budget_seconds"] == pytest.approx(0.01)

    campaign = load_frozen_campaign(campaign_dir, secret_root)
    assert not any(e.payload.get("kind") == "stage_summary" for e in campaign.ledger.entries)
    assert not any(e.payload.get("kind") == "fixture_valid" for e in campaign.ledger.entries)
    # R2: parent CPU is still charged to counters.json even on a
    # PARTIAL_SLICE exit, so caps stay honest across slices.
    counters_after_slice = load_cap_counters(campaign_dir)
    assert counters_after_slice.compute_used >= 0.0

    # re-run: resumes and completes (the generous budget here is irrelevant
    # once every remaining unit finishes inside it — "0 remaining still
    # transitions" per the memo).
    exit_code2 = cli.main([*base_args, "--time-budget-seconds", "3600"])
    out2 = json.loads(capsys.readouterr().out)
    assert exit_code2 == 0, out2
    assert out2["result"] == "OK"

    campaign2 = load_frozen_campaign(campaign_dir, secret_root)
    stage_summary_events = [
        e.payload for e in campaign2.ledger.entries if e.payload.get("kind") == "stage_summary"
    ]
    fixture_valid_events = [
        e.payload for e in campaign2.ledger.entries if e.payload.get("kind") == "fixture_valid"
    ]
    # exactly 1 stage_summary (the completing run — PARTIAL_SLICE skipped
    # its own) and exactly 1 fixture_valid (the phase transition, once).
    assert len(stage_summary_events) == 1
    assert len(fixture_valid_events) == 1

    counters_after_completion = load_cap_counters(campaign_dir)
    assert counters_after_completion.compute_used >= counters_after_slice.compute_used


# ---------------------------------------------------------------------------
# round 15 findings #1/#3/#5 (`[UNDERSPEC-CAL-D31]`): cap-counter path —
# corrupt-counters refusal, ledger-derived reconstruction/reconciliation,
# and CLI-dispatch-path parent-side CPU charging.
# ---------------------------------------------------------------------------


def _armed_c2_args(campaign_dir: Path, secret_root: Path, approval_dir: Path) -> list[str]:
    return [
        "c2-baseline",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
    ]


def _burn_cpu(min_seconds: float = 0.05) -> None:
    """Genuinely consume CPU (not just wall-clock sleep) for at least
    `min_seconds` of process CPU time, so a `resource.getrusage` delta
    around it is reliably > 0 regardless of the platform's CPU-time
    accounting tick granularity."""
    start = time.process_time()
    while time.process_time() - start < min_seconds:
        hashlib.sha256(b"x" * 4096).digest()


@pytest.mark.parametrize(
    "bad_counters",
    [
        {"compute_used": math.nan, "storage_used": 0, "budget_used": 0.0},
        {"compute_used": 0.0, "storage_used": 0, "budget_used": -math.inf},
        {"compute_used": -1.0, "storage_used": 0, "budget_used": 0.0},
        {"compute_used": 0.0, "storage_used": True, "budget_used": 0.0},
    ],
    ids=["nan_compute", "neg_inf_budget", "negative_compute", "bool_storage"],
)
def test_corrupt_persisted_counters_refuses_dispatch_zero_work(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    bad_counters: dict,
) -> None:
    """round 15 finding #1: a structurally corrupt `counters.json`
    (NaN/-Infinity/negative/bool storage) refuses dispatch with a distinct
    `COUNTERS_CORRUPT` code — zero render/measure units run."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})

    counters_path(campaign_dir).parent.mkdir(parents=True, exist_ok=True)
    counters_path(campaign_dir).write_text(json.dumps(bad_counters), encoding="utf-8")

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    called = {"n": 0}

    def _fake_run_baseline_stage(*_args, **_kwargs):
        called["n"] += 1
        return {"baseline_audit_sha": "0" * 64}

    monkeypatch.setattr(cli.baseline_stage, "run_baseline_stage", _fake_run_baseline_stage)

    exit_code = cli.main(_armed_c2_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "COUNTERS_CORRUPT"' in out
    assert called["n"] == 0

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    stop_events = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0].get("reason") == "COUNTERS_CORRUPT"


_TINY_COST_CAPS = {
    "compute": 5.0,
    "storage": 1_000_000_000,
    "budget": 1000.0,
    "budget_accounting_mode": "local_zero_cost",
}


def test_deleted_counters_json_with_ledger_work_is_reconstructed_and_precheck_uses_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 15 finding #3: a missing `counters.json` with real prior
    `render` work already in the ledger reconstructs totals from the ledger
    (not zero) and persists them, logging a `counters_reconstructed` event
    once; the round 13 finding #2 pre-dispatch breach check then uses those
    reconstructed totals, not zero."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, gate1_cost_caps=_TINY_COST_CAPS)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})
    # fabricate prior render work whose cpu_seconds alone already exceeds
    # the tiny 5.0s compute cap above.
    campaign.ledger.append(
        {
            "kind": "render",
            "row_id": "r1",
            "family": "F0_CONTROL",
            "split": "calibration",
            "probe_index": 0,
            "sha256": "0" * 64,
            "stage": "c1",
            "wall_seconds": 10.0,
            "cpu_seconds": 10.0,
            "pcm_bytes": 20_000,
        }
    )
    assert not counters_path(campaign_dir).is_file()

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir, cost_caps=_TINY_COST_CAPS)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    called = {"n": 0}

    def _fake_run_baseline_stage(*_args, **_kwargs):
        called["n"] += 1
        return {"baseline_audit_sha": "0" * 64}

    monkeypatch.setattr(cli.baseline_stage, "run_baseline_stage", _fake_run_baseline_stage)

    exit_code = cli.main(_armed_c2_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "COST_CAP_EXCEEDED"' in out
    assert called["n"] == 0  # pre-dispatch refusal: no stage work performed

    assert counters_path(campaign_dir).is_file()
    persisted = json.loads(counters_path(campaign_dir).read_text(encoding="utf-8"))
    assert persisted["compute_used"] == pytest.approx(10.0)
    assert persisted["storage_used"] == 20_000

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    reconstructed_events = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "counters_reconstructed"
    ]
    assert len(reconstructed_events) == 1
    assert reconstructed_events[0]["counters"]["compute_used"] == pytest.approx(10.0)


def test_stale_lower_persisted_counters_ledger_derived_max_wins(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 15 finding #3: a persisted `counters.json` that undercounts
    versus the ledger (e.g. an old snapshot restored over newer work) must
    not win — the reconciled effective value handed to the stage is the
    per-dimension max, i.e. the ledger-derived (higher) one."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})
    campaign.ledger.append(
        {
            "kind": "render",
            "row_id": "r1",
            "family": "F0_CONTROL",
            "split": "calibration",
            "probe_index": 0,
            "sha256": "0" * 64,
            "stage": "c1",
            "wall_seconds": 42.0,
            "cpu_seconds": 42.0,
            "pcm_bytes": 7_000,
        }
    )
    # a stale snapshot that undercounts relative to the ledger above.
    save_cap_counters(campaign_dir, CapCounters(compute_used=1.0, storage_used=1, budget_used=0.0))

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    captured: dict[str, object] = {}

    def _fake_run_baseline_stage(campaign, matrix_rows, *, max_workers, cap_counters, cost_caps, **_kw):
        captured["cap_counters"] = CapCounters(**cap_counters.as_dict())
        return {"baseline_audit_sha": "0" * 64}

    monkeypatch.setattr(cli.baseline_stage, "run_baseline_stage", _fake_run_baseline_stage)

    exit_code = cli.main(_armed_c2_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 0, out
    seen = captured["cap_counters"]
    assert isinstance(seen, CapCounters)
    assert seen.compute_used == pytest.approx(42.0)
    assert seen.storage_used == 7_000

    persisted = json.loads(counters_path(campaign_dir).read_text(encoding="utf-8"))
    assert persisted["compute_used"] == pytest.approx(42.0)
    assert persisted["storage_used"] == 7_000


def test_parent_cpu_charged_and_persisted_on_normal_dispatch_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 15 finding #5: on a normal (non-breach) stage dispatch, the CLI
    process's own parent-side CPU is charged to compute_used, persisted to
    counters.json, and recorded on a `stage_summary` ledger event."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    def _fake_run_baseline_stage(*_args, **_kwargs):
        _burn_cpu()
        return {"baseline_audit_sha": "0" * 64}

    monkeypatch.setattr(cli.baseline_stage, "run_baseline_stage", _fake_run_baseline_stage)

    exit_code = cli.main(_armed_c2_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 0, out

    persisted = json.loads(counters_path(campaign_dir).read_text(encoding="utf-8"))
    assert persisted["compute_used"] > 0.0

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    stage_summaries = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stage_summary"
    ]
    assert len(stage_summaries) == 1
    assert stage_summaries[0]["stage"] == "c2-baseline"
    assert stage_summaries[0]["parent_cpu_seconds"] > 0.0
    assert stage_summaries[0]["parent_cpu_seconds"] == pytest.approx(persisted["compute_used"])


def test_parent_cpu_charged_and_persisted_on_breach_exception_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 15 finding #5: a mid-stage cost-cap breach (raised as
    `CostCapExceededError` from inside the stage, uncaught by `cli.main()` —
    same fail-closed contract as `render_stage`/`measure_stage`) must still
    charge and persist the parent-side CPU burned before the breach, via the
    `finally`-block exit path."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    def _fake_run_baseline_stage(*_args, **_kwargs):
        _burn_cpu()
        raise CostCapExceededError("simulated mid-stage breach")

    monkeypatch.setattr(cli.baseline_stage, "run_baseline_stage", _fake_run_baseline_stage)

    with pytest.raises(CostCapExceededError):
        cli.main(_armed_c2_args(campaign_dir, secret_root, approval_dir))

    persisted = json.loads(counters_path(campaign_dir).read_text(encoding="utf-8"))
    assert persisted["compute_used"] > 0.0

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    stage_summaries = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stage_summary"
    ]
    assert len(stage_summaries) == 1
    assert stage_summaries[0]["parent_cpu_seconds"] > 0.0


def test_counters_corrupt_error_is_subclass_of_cap_state_error() -> None:
    from voice_genesis.calibration.campaign.caps import CapStateError

    assert issubclass(CountersCorruptError, CapStateError)
    assert CountersCorruptError.CODE == "COUNTERS_CORRUPT"


# ---------------------------------------------------------------------------
# round 16 finding #2 (`[UNDERSPEC-CAL-D34]`): recheck caps after charging
# parent CPU, *before* a stage's phase-transition event is appended — a
# breach there blocks the transition — and again on the residual parent CPU
# charged in `main()`'s `finally` block.
# ---------------------------------------------------------------------------

_TINY_CLOSE_COST_CAPS = {
    "compute": 10.0,
    "storage": 1_000_000_000,
    "budget": 1000.0,
    "budget_accounting_mode": "local_zero_cost",
}


def _fake_clock(values: list[float]):
    """Deterministic stand-in for `cli._process_cpu_seconds`: returns each
    value in `values` in order on successive calls, then keeps repeating
    the last one — so a test can pin exactly which call sees which CPU
    reading without depending on real CPU-time tick-granularity timing
    (`_burn_cpu()` above is the real-clock alternative used elsewhere in
    this file; a fake clock is used here because these tests pin an exact
    *sequence* of readings across specific call sites)."""
    state = {"i": 0}

    def _fn() -> float:
        i = min(state["i"], len(values) - 1)
        state["i"] += 1
        return values[i]

    return _fn


def _seed_closable_holdout(campaign) -> None:
    """Fabricate a `holdout_executed_valid` event with every `MeterId` at a
    terminal status (matches `test_unseal_and_close_dispatch_through_cli`'s
    pattern) so `close` dispatch is reachable without a full C1-C4 run."""
    results = [holdout_stage.diagnostic_only_close(m.value) for m in MeterId]
    holdout_stage.run_holdout_stage(campaign, results)


def _armed_c3a_args(campaign_dir: Path, secret_root: Path, approval_dir: Path) -> list[str]:
    return [
        "c3a-f0-selection",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
    ]


def _armed_close_args(campaign_dir: Path, secret_root: Path, approval_dir: Path) -> list[str]:
    return [
        "close",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
    ]


def test_pre_transition_checkpoint_blocks_c3a_phase_transition_on_breach(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 16 finding #2 ordering ruling: a stage just under cap whose own
    parent CPU (charged at the pre-transition checkpoint) pushes it over
    must not append its phase-transition event (`f0_selection_frozen`) —
    the stop event is recorded, dispatch exits non-zero, and the CLI
    reports failure rather than success."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, gate1_cost_caps=_TINY_CLOSE_COST_CAPS)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "2" * 64, "payload": {}}
    )
    campaign.ledger.append({"kind": "baseline_audited", "baseline_audit_sha": entry.entry_sha})

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir, cost_caps=_TINY_CLOSE_COST_CAPS)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    from voice_genesis.calibration.campaign import selection_stage

    frozen_called = {"n": 0}
    real_run_c3a = selection_stage.run_c3a_f0_selection

    def _spy_run_c3a(*args, **kwargs):
        frozen_called["n"] += 1
        return real_run_c3a(*args, **kwargs)

    monkeypatch.setattr(cli.selection_stage, "run_c3a_f0_selection", _spy_run_c3a)
    # No render/measure work is needed to exercise this checkpoint (that
    # machinery is exercised for real elsewhere, e.g. test_campaign_measure.py)
    # — stub it out so this test stays fast and needs no rendered PCM.
    monkeypatch.setattr(cli.measure_stage, "run_measure_stage", lambda *a, **k: [])
    # call 1: dispatch-start parent_cpu_t0. call 2: the pre-transition
    # checkpoint inside _run_c3a, before run_c3a_f0_selection() -- jumps
    # far past the 10.0s cap.
    monkeypatch.setattr(cli, "_process_cpu_seconds", _fake_clock([0.0, 1_000.0]))

    exit_code = cli.main(_armed_c3a_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "COST_CAP_EXCEEDED"' in out
    assert frozen_called["n"] == 0  # blocked before the transition-appending call

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    assert not any(
        isinstance(e.payload, dict) and e.payload.get("kind") == "f0_selection_frozen"
        for e in reloaded.ledger.entries
    )
    stop_events = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0].get("reason") == "COST_CAP_EXCEEDED"


def test_close_pre_transition_checkpoint_blocks_campaign_closed_on_breach(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`close` variant of the ordering-ruling test above: the pre-transition
    checkpoint in `_run_close` fires before `close_stage.close_campaign()`
    (which appends `campaign_closed`) is ever called — a breach there
    blocks close entirely, so no `campaign_closed` event is recorded."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, gate1_cost_caps=_TINY_CLOSE_COST_CAPS)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _seed_closable_holdout(campaign)

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir, cost_caps=_TINY_CLOSE_COST_CAPS)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    close_called = {"n": 0}
    real_close_campaign = cli.close_stage.close_campaign

    def _spy_close_campaign(*args, **kwargs):
        close_called["n"] += 1
        return real_close_campaign(*args, **kwargs)

    monkeypatch.setattr(cli.close_stage, "close_campaign", _spy_close_campaign)
    # call 1: dispatch-start parent_cpu_t0. call 2: the pre-transition
    # checkpoint inside _run_close, before close_campaign() -- jumps far
    # past the 10.0s cap. No further calls should be needed (dispatch
    # returns before close_campaign or the finally block's own recheck
    # would run, but the fake clock repeats the last value regardless).
    monkeypatch.setattr(cli, "_process_cpu_seconds", _fake_clock([0.0, 1_000.0]))

    exit_code = cli.main(_armed_close_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "COST_CAP_EXCEEDED"' in out
    assert close_called["n"] == 0

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    assert not any(
        isinstance(e.payload, dict) and e.payload.get("kind") == "campaign_closed"
        for e in reloaded.ledger.entries
    )
    stop_events = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0].get("reason") == "COST_CAP_EXCEEDED"
    assert "post_close_breach" not in stop_events[0]


def test_close_post_close_residual_breach_still_records_close_but_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 16 finding #2 ordering ruling, close's documented special case:
    if the pre-transition checkpoint passes (close proceeds and
    `campaign_closed` is appended) but the small *residual* parent CPU
    charged afterwards in `main()`'s `finally` block alone breaches the
    cap, the campaign remains closed (append-only ledger — nothing
    retracts `campaign_closed`) but the dispatch still reports failure, and
    the stop event is marked `post_close_breach: True`."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path, gate1_cost_caps=_TINY_CLOSE_COST_CAPS)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _seed_closable_holdout(campaign)

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir, cost_caps=_TINY_CLOSE_COST_CAPS)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    # call 1: parent_cpu_t0. call 2: the pre-transition checkpoint (no
    # jump -- passes). call 3+: the finally block's residual charge --
    # jumps far past the cap only *after* close_campaign() has already run.
    monkeypatch.setattr(cli, "_process_cpu_seconds", _fake_clock([0.0, 0.0, 1_000.0]))

    exit_code = cli.main(_armed_close_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "COST_CAP_EXCEEDED"' in out

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    closed_events = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "campaign_closed"
    ]
    assert len(closed_events) == 1  # close remains recorded despite the failure report
    stop_events = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stop_event"
    ]
    assert len(stop_events) == 1
    assert stop_events[0].get("reason") == "COST_CAP_EXCEEDED"
    assert stop_events[0].get("post_close_breach") is True


def test_close_stage_summary_records_full_dispatch_cpu_across_checkpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """round 17 finding #3 (`[UNDERSPEC-CAL-D39]`): extends the round 15/16
    persisted-vs-ledger-derived equality check (see
    `test_parent_cpu_charged_and_persisted_on_normal_dispatch_exit`) to a
    stage with a mid-dispatch checkpoint (`close`, via
    `_checkpoint_parent_cpu_before_transition()`). Before this fix, the
    `stage_summary` ledger event recorded only the post-checkpoint residual,
    so `cap_counters_from_ledger()` under-counted relative to the persisted
    `counters.json` cache by exactly the checkpoint's own delta."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _seed_closable_holdout(campaign)

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    # call 1: dispatch-start parent_cpu_t0 = 0.0. call 2: the pre-transition
    # checkpoint inside `_run_close` (before `campaign_closed` is appended)
    # = 3.0 (checkpoint delta = 3.0). call 3: the `finally` block's residual
    # charge = 5.0 (residual = 2.0). Full dispatch delta = 5.0.
    monkeypatch.setattr(cli, "_process_cpu_seconds", _fake_clock([0.0, 3.0, 5.0]))

    exit_code = cli.main(_armed_close_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 0, out

    persisted = json.loads(counters_path(campaign_dir).read_text(encoding="utf-8"))
    assert persisted["compute_used"] == pytest.approx(5.0)

    reloaded = load_frozen_campaign(campaign_dir, secret_root)
    stage_summaries = [
        e.payload
        for e in reloaded.ledger.entries
        if isinstance(e.payload, dict) and e.payload.get("kind") == "stage_summary"
    ]
    assert len(stage_summaries) == 1
    # the FULL dispatch delta (checkpoint delta + residual), not just the
    # 2.0 post-checkpoint residual.
    assert stage_summaries[0]["parent_cpu_seconds"] == pytest.approx(5.0)

    cost_caps_obj = cost_caps_from_manifest(reloaded.manifest)
    derived = cap_counters_from_ledger(reloaded.ledger.entries, cost_caps_obj)
    assert derived.compute_used == pytest.approx(persisted["compute_used"])


# ---------------------------------------------------------------------------
# round 19 finding #3 (`[UNDERSPEC-CAL-D45]`): completed-stage retries must
# be true no-ops (zero ledger growth); CAMPAIGN_CLOSED rejects every retry.
# ---------------------------------------------------------------------------


def test_c1_fixtures_retry_after_fixture_valid_is_true_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying c1-fixtures after FIXTURE_VALID must append zero ledger
    events (no re-appended `fixture_valid` transition event, no
    `stage_summary`) and exit 0 with `NOOP_ALREADY_COMPLETE`."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    campaign.ledger.append({"kind": "fixture_valid", "instance_count": 0})

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    entries_before = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)

    exit_code = cli.main(
        [
            "c1-fixtures",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert '"result": "NOOP_ALREADY_COMPLETE"' in out
    assert '"stage": "c1-fixtures"' in out

    entries_after = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)
    assert entries_after == entries_before


def _seal_to_unsealed_for_holdout_noop_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    """Shared setup for the two tests below: build a tiny campaign through
    UNSEALED (c4-holdout's prerequisite phase) without any real render/
    measure (mirrors `test_unseal_and_close_dispatch_through_cli`)."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "2" * 64, "payload": {}}
    )
    campaign.ledger.append({"kind": "baseline_audited", "baseline_audit_sha": entry.entry_sha})

    from voice_genesis.calibration.campaign import selection_stage
    from voice_genesis.calibration.selection import CandidateCriteria
    from voice_genesis.calibration.vocab import ClaimCeiling

    selection_stage.run_c3b_selection(
        campaign,
        {
            "TILT_GT": [
                CandidateCriteria(
                    candidate_id="M2T-HARMONIC-OLS-K4-WINhann",
                    ceiling=ClaimCeiling.ABSOLUTE,
                    primary_normalized_mae=0.05,
                    signed_bias=0.01,
                    primary_q95_ae=0.1,
                )
            ]
        },
        baseline_audit_entry_sha=entry.entry_sha,
    )

    approval_dir = tmp_path / "approvals"
    write_gate1_approval(approval_dir)
    from ._campaign_fixture import write_gate3_approval

    write_gate3_approval(approval_dir)
    monkeypatch.setenv(cli.CAMPAIGN_ARMED_ENV_VAR, "1")

    exit_code = cli.main(
        [
            "unseal",
            "--campaign-dir",
            str(campaign_dir),
            "--secret-dir",
            str(secret_root),
            "--approval-dir",
            str(approval_dir),
            "--armed",
        ]
    )
    assert exit_code == 0

    return campaign_dir, secret_root, approval_dir


def _armed_c4_holdout_args(campaign_dir: Path, secret_root: Path, approval_dir: Path) -> list[str]:
    return [
        "c4-holdout",
        "--campaign-dir",
        str(campaign_dir),
        "--secret-dir",
        str(secret_root),
        "--approval-dir",
        str(approval_dir),
        "--armed",
    ]


def test_c4_holdout_retry_after_holdout_executed_valid_is_true_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying c4-holdout after HOLDOUT_EXECUTED_VALID must append zero
    ledger events (no re-appended `holdout_executed_valid` transition event,
    no `stage_summary`, no render/measure dispatch) and exit 0 with
    `NOOP_ALREADY_COMPLETE`."""
    campaign_dir, secret_root, approval_dir = _seal_to_unsealed_for_holdout_noop_tests(
        tmp_path, monkeypatch
    )
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _seed_closable_holdout(campaign)

    entries_before = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)

    exit_code = cli.main(_armed_c4_holdout_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 0, out
    assert '"result": "NOOP_ALREADY_COMPLETE"' in out
    assert '"stage": "c4-holdout"' in out

    entries_after = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)
    assert entries_after == entries_before


def test_c4_holdout_retry_after_campaign_closed_is_phase_order_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once CAMPAIGN_CLOSED is reached, retrying a resumable subcommand
    (c4-holdout) is no longer a no-op — it must be rejected as
    PHASE_ORDER_VIOLATION, distinct from the pre-close true no-op path, with
    zero ledger growth either way."""
    campaign_dir, secret_root, approval_dir = _seal_to_unsealed_for_holdout_noop_tests(
        tmp_path, monkeypatch
    )
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _seed_closable_holdout(campaign)

    exit_code = cli.main(_armed_close_args(campaign_dir, secret_root, approval_dir))
    assert exit_code == 0, capsys.readouterr().out

    entries_before = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)

    exit_code = cli.main(_armed_c4_holdout_args(campaign_dir, secret_root, approval_dir))
    out = capsys.readouterr().out
    assert exit_code == 1, out
    assert '"result": "PHASE_ORDER_VIOLATION"' in out
    assert "c4-holdout_after_campaign_closed" in out

    entries_after = len(load_frozen_campaign(campaign_dir, secret_root).ledger.entries)
    assert entries_after == entries_before
