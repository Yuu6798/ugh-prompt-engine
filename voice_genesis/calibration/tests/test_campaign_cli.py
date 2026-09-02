"""`campaign/cli.py` のテスト: 武装プロトコル（`--armed` + env + Gate 1 承認）
と各サブコマンドの分岐。武装 render/measure を伴う経路のみ `@pytest.mark.slow`。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import cli, holdout_stage, measure_stage, render_stage
from voice_genesis.calibration.campaign.caps import (
    CapCounters,
    CostCapExceededError,
    CountersCorruptError,
    cap_counters_from_ledger,
    cost_caps_from_manifest,
    counters_path,
    save_cap_counters,
)
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import candidates_for_meter
from voice_genesis.calibration.vocab import MeterId

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

    result = cli._build_f0_by_instance(
        campaign,
        [("r1", 0)],
        candidate_id,
        {"r1": 48000},
        max_workers=1,
        cap_counters=None,
        cost_caps=None,
    )
    assert result == {("r1", 0): pytest.approx(100.0)}
    # reused, not re-measured: no new ledger entries.
    assert len(campaign.ledger.entries) == entries_before


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
