from __future__ import annotations

import json
from pathlib import Path

from voice_genesis.calibration.authorization_guard import validate_campaign


def _campaign(tmp_path: Path, campaign_id: str = "RUN10-CAL-TEST") -> Path:
    path = tmp_path / campaign_id
    path.mkdir(parents=True)
    (path / "c0_manifest.json").write_text(
        json.dumps({"campaign_id": campaign_id}), encoding="utf-8"
    )
    return path


def test_missing_reference_fails_closed(tmp_path: Path) -> None:
    result = validate_campaign(_campaign(tmp_path))
    assert result.ok is False
    assert result.mode == "MISSING_DIRECT_REFERENCE"


def test_removed_existing_manifest_fails_closed(tmp_path: Path) -> None:
    base_campaign = _campaign(tmp_path / "base")
    head_campaign = tmp_path / "head" / base_campaign.name

    result = validate_campaign(head_campaign, base_campaign_dir=base_campaign)

    assert result.ok is False
    assert result.mode == "C0_MANIFEST_REMOVED"
    assert result.reasons == (
        "an existing frozen campaign c0_manifest.json must be preserved",
    )


def test_never_frozen_directory_without_manifest_is_not_a_campaign(tmp_path: Path) -> None:
    base_campaign = tmp_path / "base" / "RUN10-CAL-TEST"
    head_campaign = tmp_path / "head" / base_campaign.name

    result = validate_campaign(head_campaign, base_campaign_dir=base_campaign)

    assert result.ok is True
    assert result.mode == "NO_C0_MANIFEST"


def test_general_delegation_reference_rejected(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    (campaign / "direct_authorization_ref.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-direct-authorization-ref/1",
                "campaign_id": campaign.name,
                "authority_type": "DIRECT_USER_APPROVAL",
                "direct_approval_ref": "general delegation 2026-09-08",
                "approved_operations": ["C0_FREEZE", "CAMPAIGN_EXECUTION"],
                "reference_is_not_authentication": True,
            }
        ),
        encoding="utf-8",
    )
    result = validate_campaign(campaign)
    assert result.ok is False
    assert any("generalized delegation" in reason for reason in result.reasons)


def test_drive_signature_reference_rejected(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    (campaign / "direct_authorization_ref.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-direct-authorization-ref/1",
                "campaign_id": campaign.name,
                "authority_type": "DIRECT_USER_APPROVAL",
                "direct_approval_ref": "drive://signed_by/GPT",
                "approved_operations": ["C0_FREEZE", "CAMPAIGN_EXECUTION"],
                "reference_is_not_authentication": True,
            }
        ),
        encoding="utf-8",
    )
    result = validate_campaign(campaign)
    assert result.ok is False


def test_scoped_direct_reference_accepted_as_reference_only(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    (campaign / "direct_authorization_ref.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-direct-authorization-ref/1",
                "campaign_id": campaign.name,
                "authority_type": "DIRECT_USER_APPROVAL",
                "direct_approval_ref": "USER-DIRECT-RUN10-CAL-TEST-20260909",
                "approved_operations": ["C0_FREEZE", "CAMPAIGN_EXECUTION"],
                "reference_is_not_authentication": True,
            }
        ),
        encoding="utf-8",
    )
    result = validate_campaign(campaign)
    assert result.ok is True
    assert result.mode == "DIRECT_REFERENCE"


def test_quarantine_marker_blocks_claim_and_debt_use(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    (campaign / "authorization_quarantine.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-quarantine/1",
                "campaign_id": campaign.name,
                "status": "QUARANTINED",
                "claimable": False,
                "debt_discharge_eligible": False,
                "run11_eligible": False,
                "reason_codes": ["AUTHORIZATION_BOUNDARY_NOT_VERIFIED"],
            }
        ),
        encoding="utf-8",
    )
    result = validate_campaign(campaign)
    assert result.ok is True
    assert result.mode == "QUARANTINE"
