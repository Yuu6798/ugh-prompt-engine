"""`campaign/cli.py` のテスト: 武装プロトコル（`--armed` + env + Gate 1 承認）
と各サブコマンドの分岐。武装 render/measure を伴う経路のみ `@pytest.mark.slow`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import cli, holdout_stage, render_stage
from voice_genesis.calibration.campaign.caps import CapCounters, save_cap_counters
from voice_genesis.calibration.campaign.state import load_frozen_campaign
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
    assert len(campaign.ledger.entries) == 1  # only the c0_freeze event
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
    assert len(campaign.ledger.entries) == 1  # only the c0_freeze event


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
        captured["cap_counters"] = cap_counters
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
