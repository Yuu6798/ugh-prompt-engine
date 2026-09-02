"""`campaign/unseal.py` のテスト: §7 の 5-sha 相互参照検査 + Gate 3 束縛。
合成 selection_frozen chain のみを使うため高速。
"""

from __future__ import annotations

from pathlib import Path

from voice_genesis.calibration.campaign import selection_stage, unseal as unseal_module
from voice_genesis.calibration.campaign.state import (
    CampaignPhase,
    load_frozen_campaign,
)
from voice_genesis.calibration.provenance import _verified_holdout_unseal_seq
from voice_genesis.calibration.selection import CandidateCriteria
from voice_genesis.calibration.vocab import ClaimCeiling

from ._campaign_fixture import build_tiny_campaign, write_gate3_approval


def _freeze_baseline_and_selection(campaign) -> None:
    baseline_entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "1" * 64, "payload": {}}
    )
    campaign.ledger.append(
        {"kind": "baseline_audited", "baseline_audit_sha": baseline_entry.entry_sha}
    )
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
        baseline_audit_entry_sha=baseline_entry.entry_sha,
    )


def test_unseal_refuses_without_selection_frozen_event(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    approval_dir = tmp_path / "approvals"
    write_gate3_approval(approval_dir)

    try:
        unseal_module.unseal_campaign(campaign, approval_dir=approval_dir)
        raise AssertionError("expected UnsealError: no selection_frozen event")
    except unseal_module.UnsealError as exc:
        assert "selection_frozen" in str(exc)


def test_unseal_refuses_without_gate3_approval(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _freeze_baseline_and_selection(campaign)

    approval_dir = tmp_path / "approvals-empty"
    try:
        unseal_module.unseal_campaign(campaign, approval_dir=approval_dir)
        raise AssertionError("expected UnsealError: gate3 not approved")
    except unseal_module.UnsealError as exc:
        assert "Gate 3" in str(exc)

    # fail-closed: no partial events written
    assert not any(
        e.payload.get("kind") in ("gate3_accepted", "holdout_unseal")
        for e in campaign.ledger.entries
    )


def test_unseal_refuses_when_gate3_not_accepted(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _freeze_baseline_and_selection(campaign)

    approval_dir = tmp_path / "approvals-declined"
    write_gate3_approval(approval_dir, accepted=False)
    try:
        unseal_module.unseal_campaign(campaign, approval_dir=approval_dir)
        raise AssertionError("expected UnsealError: seal_protection_level_accepted=False")
    except unseal_module.UnsealError:
        pass


def test_unseal_succeeds_with_valid_chain_and_gate3(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    _freeze_baseline_and_selection(campaign)

    approval_dir = tmp_path / "approvals-ok"
    write_gate3_approval(approval_dir)

    result = unseal_module.unseal_campaign(campaign, approval_dir=approval_dir)
    assert result.holdout_unseal_entry_sha
    assert result.gate3_accepted_entry_sha

    assert CampaignPhase.UNSEALED in campaign.phases_passed()

    # the ledger's own cryptographic authority (provenance.py) recognizes it
    verified_seq = _verified_holdout_unseal_seq(campaign.ledger.entries)
    assert verified_seq is not None
    unseal_entry = next(
        e for e in campaign.ledger.entries if e.entry_sha == result.holdout_unseal_entry_sha
    )
    assert campaign.ledger.entries[verified_seq].entry_sha == unseal_entry.entry_sha

    # unseal is idempotent-refusable, not idempotent-repeatable: a second call
    # still succeeds mechanically (it does not special-case "already unsealed"),
    # but produces a second, equally valid holdout_unseal event referencing the
    # same selection_frozen chain (campaign-level dedup is a cli/state concern).
    second = unseal_module.unseal_campaign(campaign, approval_dir=approval_dir)
    assert second.holdout_unseal_entry_sha != result.holdout_unseal_entry_sha
