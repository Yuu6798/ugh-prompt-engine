from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from voice_genesis.calibration.authorization_guard import _campaign_dir, validate_campaign


def _campaign(tmp_path: Path, campaign_id: str = "RUN10-CAL-TEST") -> Path:
    path = tmp_path / campaign_id
    path.mkdir(parents=True)
    (path / "c0_manifest.json").write_text(
        json.dumps({"campaign_id": campaign_id}), encoding="utf-8"
    )
    return path


def _write_direct_reference(campaign: Path, operations: list[str]) -> None:
    (campaign / "direct_authorization_ref.json").write_text(
        json.dumps(
            {
                "schema": "vgcal-direct-authorization-ref/1",
                "campaign_id": campaign.name,
                "authority_type": "DIRECT_USER_APPROVAL",
                "direct_approval_ref": "USER-DIRECT-RUN10-CAL-TEST-20260909",
                "approved_operations": operations,
                "reference_is_not_authentication": True,
            }
        ),
        encoding="utf-8",
    )


def _append_ledger_payload(campaign: Path, payload: dict[str, object]) -> None:
    with (campaign / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"payload": payload}) + "\n")


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


def test_symlink_replacement_for_manifest_fails_closed(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    target = tmp_path / "other.json"
    target.write_text(json.dumps({"campaign_id": campaign.name}), encoding="utf-8")
    (campaign / "c0_manifest.json").unlink()
    (campaign / "c0_manifest.json").symlink_to(target)
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])

    result = validate_campaign(campaign)

    assert result.ok is False
    assert result.mode == "UNSAFE_CAMPAIGN_PATH"
    assert result.reasons == ("c0_manifest.json must not be a symlink",)


def test_campaign_directory_symlink_fails_closed(tmp_path: Path) -> None:
    target = _campaign(tmp_path / "target")
    campaign = tmp_path / "RUN10-CAL-LINK"
    campaign.symlink_to(target, target_is_directory=True)

    result = validate_campaign(campaign)

    assert result.ok is False
    assert result.mode == "UNSAFE_CAMPAIGN_PATH"


def test_campaign_dir_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one non-blank path component"):
        _campaign_dir(tmp_path, "../outside")


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


def test_scheme_less_repository_and_drive_references_rejected(tmp_path: Path) -> None:
    for index, ref in enumerate(
        (
            "github.com/example/repo/pull/1",
            "drive.google.com/file/d/example/view",
            "DRIVE.google.com/file/d/example/view",
        )
    ):
        campaign = _campaign(tmp_path, f"RUN10-CAL-TEST-{index}")
        _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])
        data = json.loads((campaign / "direct_authorization_ref.json").read_text())
        data["direct_approval_ref"] = ref
        (campaign / "direct_authorization_ref.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

        result = validate_campaign(campaign)

        assert result.ok is False
        assert any("repository/Drive URLs" in reason for reason in result.reasons)


def test_unicode_signature_reference_rejected(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])
    data = json.loads((campaign / "direct_authorization_ref.json").read_text())
    data["direct_approval_ref"] = "signed\u00a0by\u200bGPT"
    (campaign / "direct_authorization_ref.json").write_text(
        json.dumps(data), encoding="utf-8"
    )

    result = validate_campaign(campaign)

    assert result.ok is False
    assert any("generalized delegation" in reason for reason in result.reasons)


def test_scoped_direct_reference_accepted_as_reference_only(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])
    result = validate_campaign(campaign)
    assert result.ok is True
    assert result.mode == "DIRECT_REFERENCE"


def test_post_seal_evidence_requires_gate3_seal_acceptance(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _append_ledger_payload(campaign, {"kind": "holdout_unseal"})
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])

    result = validate_campaign(campaign)

    assert result.ok is False
    assert result.reasons == (
        "approved_operations missing required operations: GATE3_SEAL_ACCEPTANCE",
    )


def test_gate3_scoped_reference_accepts_post_seal_evidence(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _append_ledger_payload(campaign, {"kind": "holdout_executed_valid"})
    _write_direct_reference(
        campaign,
        ["C0_FREEZE", "CAMPAIGN_EXECUTION", "GATE3_SEAL_ACCEPTANCE"],
    )

    result = validate_campaign(campaign)

    assert result.ok is True
    assert result.mode == "DIRECT_REFERENCE"


def test_c4_stage_requires_gate3_even_without_unseal_event(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _append_ledger_payload(campaign, {"kind": "stage_summary", "stage": "c4-holdout"})
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])

    result = validate_campaign(campaign)

    assert result.ok is False
    assert "GATE3_SEAL_ACCEPTANCE" in result.reasons[0]


def test_holdout_render_valid_requires_gate3_even_without_stage(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _append_ledger_payload(campaign, {"kind": "holdout_render_valid"})
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])

    result = validate_campaign(campaign)

    assert result.ok is False
    assert "GATE3_SEAL_ACCEPTANCE" in result.reasons[0]


def test_split_secret_reveal_requires_gate3_even_without_stage(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _append_ledger_payload(campaign, {"kind": "split_secret_revealed"})
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])

    result = validate_campaign(campaign)

    assert result.ok is False
    assert "GATE3_SEAL_ACCEPTANCE" in result.reasons[0]


def test_holdout_split_requires_gate3_even_for_unknown_event_kind(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    _append_ledger_payload(campaign, {"kind": "future_event", "split": "HOLDOUT"})
    _write_direct_reference(campaign, ["C0_FREEZE", "CAMPAIGN_EXECUTION"])

    result = validate_campaign(campaign)

    assert result.ok is False
    assert "GATE3_SEAL_ACCEPTANCE" in result.reasons[0]


def test_malformed_ledger_fails_closed_for_direct_reference(tmp_path: Path) -> None:
    campaign = _campaign(tmp_path)
    (campaign / "ledger.jsonl").write_text("not-json\n", encoding="utf-8")
    _write_direct_reference(
        campaign,
        ["C0_FREEZE", "CAMPAIGN_EXECUTION", "GATE3_SEAL_ACCEPTANCE"],
    )

    result = validate_campaign(campaign)

    assert result.ok is False
    assert result.reasons[0].startswith(
        "cannot derive authorization scope from ledger.jsonl: line 1:"
    )


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


def test_existing_quarantine_rejects_new_files(tmp_path: Path) -> None:
    base_campaign = _campaign(tmp_path / "base")
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
    (head_campaign / "results_corrected_v2.json").write_text(
        json.dumps({"debt_discharged": True}), encoding="utf-8"
    )

    result = validate_campaign(head_campaign, base_campaign_dir=base_campaign)

    assert result.ok is False
    assert result.reasons == (
        "new file added to quarantined campaign: results_corrected_v2.json",
    )


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


def test_workflow_uses_trusted_base_guard_and_keeps_rename_sources() -> None:
    workflow = (
        Path(__file__).parents[3]
        / ".github"
        / "workflows"
        / "vg-campaign-authorization-guard.yml"
    ).read_text(encoding="utf-8")

    assert "pull_request_target:" in workflow
    assert "persist-credentials: false" in workflow
    assert "python trusted-base/voice_genesis/calibration/authorization_guard.py" in workflow
    assert "--repo-root candidate" in workflow
    assert "--base-repo-root trusted-base" in workflow
    assert '"https://github.com/${BASE_REPOSITORY}.git" "$BASE_SHA"' in workflow
    assert "git -C candidate diff --no-renames --name-only" in workflow
