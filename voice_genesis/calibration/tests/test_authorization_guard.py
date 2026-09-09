from __future__ import annotations

import json
import shutil
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


def test_drive_url_reference_rejected(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    (campaign / "direct_authorization_ref.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-direct-authorization-ref/1",
                "campaign_id": campaign.name,
                "authority_type": "DIRECT_USER_APPROVAL",
                "direct_approval_ref": "https://drive.google.com/file/d/example/view",
                "approved_operations": ["C0_FREEZE", "CAMPAIGN_EXECUTION"],
                "reference_is_not_authentication": True,
            }
        ),
        encoding="utf-8",
    )

    result = validate_campaign(campaign)

    assert result.ok is False
    assert any("repository/Drive URLs" in reason for reason in result.reasons)


def test_github_url_reference_rejected(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    (campaign / "direct_authorization_ref.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-direct-authorization-ref/1",
                "campaign_id": campaign.name,
                "authority_type": "DIRECT_USER_APPROVAL",
                "direct_approval_ref": "https://github.com/example/repo/pull/1",
                "approved_operations": ["C0_FREEZE", "CAMPAIGN_EXECUTION"],
                "reference_is_not_authentication": True,
            }
        ),
        encoding="utf-8",
    )

    result = validate_campaign(campaign)

    assert result.ok is False
    assert any("repository/Drive URLs" in reason for reason in result.reasons)


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


def test_quarantine_preserves_all_base_evidence_bytes(tmp_path: Path) -> None:
    base_campaign = _campaign(tmp_path / "base")
    (base_campaign / "ledger.jsonl").write_text("original\n", encoding="utf-8")
    head_campaign = tmp_path / "head" / base_campaign.name
    shutil.copytree(base_campaign, head_campaign)
    (head_campaign / "authorization_quarantine.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-quarantine/1",
                "campaign_id": head_campaign.name,
                "status": "QUARANTINED",
                "claimable": False,
                "debt_discharge_eligible": False,
                "run11_eligible": False,
                "reason_codes": ["AUTHORIZATION_BOUNDARY_NOT_VERIFIED"],
            }
        ),
        encoding="utf-8",
    )

    result = validate_campaign(head_campaign, base_campaign_dir=base_campaign)

    assert result.ok is True
    assert result.mode == "QUARANTINE"


def test_quarantine_rejects_modified_base_evidence(tmp_path: Path) -> None:
    base_campaign = _campaign(tmp_path / "base")
    (base_campaign / "ledger.jsonl").write_text("original\n", encoding="utf-8")
    head_campaign = tmp_path / "head" / base_campaign.name
    shutil.copytree(base_campaign, head_campaign)
    (head_campaign / "ledger.jsonl").write_text("rewritten\n", encoding="utf-8")
    (head_campaign / "authorization_quarantine.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-quarantine/1",
                "campaign_id": head_campaign.name,
                "status": "QUARANTINED",
                "claimable": False,
                "debt_discharge_eligible": False,
                "run11_eligible": False,
                "reason_codes": ["AUTHORIZATION_BOUNDARY_NOT_VERIFIED"],
            }
        ),
        encoding="utf-8",
    )

    result = validate_campaign(head_campaign, base_campaign_dir=base_campaign)

    assert result.ok is False
    assert result.reasons == ("base evidence file modified: ledger.jsonl",)


def test_existing_quarantine_cannot_be_replaced_by_direct_reference(tmp_path: Path) -> None:
    base_campaign = _campaign(tmp_path / "base")
    (base_campaign / "ledger.jsonl").write_text("original\n", encoding="utf-8")
    (base_campaign / "authorization_quarantine.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-quarantine/1",
                "campaign_id": base_campaign.name,
                "status": "QUARANTINED",
                "claimable": False,
                "debt_discharge_eligible": False,
                "run11_eligible": False,
                "reason_codes": ["AUTHORIZATION_BOUNDARY_NOT_VERIFIED"],
            }
        ),
        encoding="utf-8",
    )
    head_campaign = tmp_path / "head" / base_campaign.name
    shutil.copytree(base_campaign, head_campaign)
    (head_campaign / "authorization_quarantine.json").unlink()
    (head_campaign / "ledger.jsonl").write_text("rewritten\n", encoding="utf-8")
    (head_campaign / "direct_authorization_ref.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-direct-authorization-ref/1",
                "campaign_id": head_campaign.name,
                "authority_type": "DIRECT_USER_APPROVAL",
                "direct_approval_ref": "USER-DIRECT-RUN10-CAL-TEST-20260909",
                "approved_operations": ["C0_FREEZE", "CAMPAIGN_EXECUTION"],
                "reference_is_not_authentication": True,
            }
        ),
        encoding="utf-8",
    )

    result = validate_campaign(head_campaign, base_campaign_dir=base_campaign)

    assert result.ok is False
    assert result.mode == "QUARANTINE"
    assert result.reasons == ("an existing quarantine marker must be preserved",)
