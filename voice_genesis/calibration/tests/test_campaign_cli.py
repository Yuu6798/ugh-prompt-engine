"""`campaign/cli.py` のテスト: 武装プロトコル（`--armed` + env + Gate 1 承認）
と各サブコマンドの分岐。武装 render/measure を伴う経路のみ `@pytest.mark.slow`。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import cli, holdout_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.vocab import MeterId

from ._campaign_fixture import build_tiny_campaign, small_matrix_subset, write_gate1_approval


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
