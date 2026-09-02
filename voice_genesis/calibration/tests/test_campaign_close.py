"""`campaign/close.py` のテスト: CAMPAIGN_CLOSED + `debt_discharged` 導出 +
M6 + split_secret reveal ゲーティング（`[UNDERSPEC-CAL-D09]`）。高速。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import close as close_module, holdout_stage
from voice_genesis.calibration.campaign.state import CampaignPhase, load_frozen_campaign
from voice_genesis.calibration.vocab import CLAIM_CRITICAL_SET, ClaimCeiling, MeterId, TerminalStatus

from ._campaign_fixture import build_tiny_campaign


def _all_meters_diagnostic_only_payload() -> dict[str, object]:
    results = [holdout_stage.diagnostic_only_close(m.value) for m in MeterId]
    return {
        "per_meter": {
            r.meter_id: {
                "terminal_status": r.terminal_status,
                "reason_code": r.reason_code,
                "ceiling": r.ceiling,
                "selected_candidate_id": r.selected_candidate_id,
                "gate_detail": dict(r.gate_detail),
            }
            for r in results
        }
    }


def _all_critical_absolute_payload() -> dict[str, object]:
    per_meter = {}
    for meter in MeterId:
        status = (
            TerminalStatus.CALIBRATED_ABSOLUTE.value
            if meter in CLAIM_CRITICAL_SET
            else TerminalStatus.DIAGNOSTIC_ONLY.value
        )
        per_meter[meter.value] = {
            "terminal_status": status,
            "reason_code": None,
            "ceiling": ClaimCeiling.ABSOLUTE.value,
            "selected_candidate_id": "FAKE",
            "gate_detail": {},
        }
    return {"per_meter": per_meter}


def test_close_campaign_debt_not_discharged_when_diagnostic_only(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    result = close_module.close_campaign(campaign, _all_meters_diagnostic_only_payload())
    assert result.debt_discharged is False
    assert result.m6 is None
    assert CampaignPhase.CAMPAIGN_CLOSED in campaign.phases_passed()


def test_close_campaign_debt_discharged_when_all_critical_calibrated(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    payload = _all_critical_absolute_payload()
    result = close_module.close_campaign(campaign, payload)
    assert result.debt_discharged is True


def test_close_campaign_computes_m6_when_components_supplied(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    payload = _all_critical_absolute_payload()
    components_a = {m: 100.0 for m in CLAIM_CRITICAL_SET}
    components_b = {m: 101.0 for m in CLAIM_CRITICAL_SET}
    e_use = {m: 1.0 for m in CLAIM_CRITICAL_SET}
    result = close_module.close_campaign(
        campaign,
        payload,
        m6_components_a=components_a,
        m6_components_b=components_b,
        m6_e_use=e_use,
        m6_norm="L1",
    )
    assert result.m6 is not None
    assert result.m6.distance is not None


def test_close_campaign_not_closable_when_meter_missing(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    partial_payload = _all_meters_diagnostic_only_payload()
    del partial_payload["per_meter"][MeterId.M6_IDENTITY.value]

    with pytest.raises(close_module.CampaignNotClosableError):
        close_module.close_campaign(campaign, partial_payload)


def test_reveal_split_secret_refused_before_close(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    with pytest.raises(close_module.RevealBeforeCloseError):
        close_module.reveal_split_secret(campaign)


def test_reveal_split_secret_succeeds_after_close(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    close_module.close_campaign(campaign, _all_meters_diagnostic_only_payload())

    entry = close_module.reveal_split_secret(campaign)
    assert entry.payload["kind"] == "split_secret_revealed"
    assert entry.payload["split_secret_hex"] == campaign.split_secret.hex()
