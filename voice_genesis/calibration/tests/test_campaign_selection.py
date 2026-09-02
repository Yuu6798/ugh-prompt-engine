"""`campaign/selection_stage.py` のテスト: C3a/C3b selection freeze の
event 構造・prerequisite entry_sha 相互参照（合成 criteria、real
measurement 不要のため高速）。
"""

from __future__ import annotations

from pathlib import Path

from voice_genesis.calibration.campaign import selection_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.selection import CandidateCriteria
from voice_genesis.calibration.vocab import ClaimCeiling

from ._campaign_fixture import build_tiny_campaign


def _fake_baseline_audit(campaign) -> str:
    entry = campaign.ledger.append(
        {"kind": "baseline_audit", "artifact_sha": "0" * 64, "payload": {}}
    )
    campaign.ledger.append({"kind": "baseline_audited", "baseline_audit_sha": entry.entry_sha})
    return entry.entry_sha


def test_c3a_f0_selection_records_frozen_event(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    criteria = [
        CandidateCriteria(
            candidate_id="F0-B0-CURRENT",
            ceiling=ClaimCeiling.ABSOLUTE,
            primary_normalized_mae=0.01,
            signed_bias=0.001,
            primary_q95_ae=0.02,
        ),
        CandidateCriteria(
            candidate_id="F0-PYIN-FRAME2048-HOP256",
            ceiling=ClaimCeiling.ABSOLUTE,
            primary_normalized_mae=0.02,
            signed_bias=0.002,
            primary_q95_ae=0.03,
        ),
    ]
    result = selection_stage.run_c3a_f0_selection(campaign, criteria)
    assert result.outcome.selected_candidate_id == "F0-B0-CURRENT"

    entry = next(
        e for e in campaign.ledger.entries if e.entry_sha == result.f0_selection_frozen_entry_sha
    )
    assert entry.payload["kind"] == "f0_selection_frozen"
    assert entry.payload["selected_candidate_id"] == "F0-B0-CURRENT"


def test_c3b_selection_frozen_event_has_valid_prerequisite_chain(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    baseline_audit_entry_sha = _fake_baseline_audit(campaign)

    criteria_by_family = {
        "TILT_GT": [
            CandidateCriteria(
                candidate_id="M2T-HARMONIC-OLS-K4-WINhann",
                ceiling=ClaimCeiling.ABSOLUTE,
                primary_normalized_mae=0.05,
                signed_bias=0.01,
                primary_q95_ae=0.1,
            )
        ],
        "RESONANCE_GT": [
            CandidateCriteria(
                candidate_id="M4-LOCAL-PROMINENCE-6dB-150Hz",
                ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,
                primary_normalized_mae=0.2,
                signed_bias=0.05,
                primary_q95_ae=0.3,
            )
        ],
    }
    result = selection_stage.run_c3b_selection(
        campaign, criteria_by_family, baseline_audit_entry_sha=baseline_audit_entry_sha
    )
    assert result.outcomes_by_family["TILT_GT"].selected_candidate_id == (
        "M2T-HARMONIC-OLS-K4-WINhann"
    )
    # DIAGNOSTIC_ONLY-only pool -> SELECTION_FAILED_CLOSED (never selectable)
    assert result.outcomes_by_family["RESONANCE_GT"].selected_candidate_id is None
    assert result.outcomes_by_family["RESONANCE_GT"].outcome == "SELECTION_FAILED_CLOSED"

    entries_by_sha = {e.entry_sha: e for e in campaign.ledger.entries}
    sf_entry = entries_by_sha[result.selection_frozen_entry_sha]
    assert sf_entry.payload["kind"] == "selection_frozen"
    assert sf_entry.payload["baseline_audit_sha"] == baseline_audit_entry_sha

    for key, expected_kind in (
        ("baseline_audit_sha", "baseline_audit"),
        ("candidate_space_sha", "candidate_space"),
        ("selection_rule_sha", "selection_rule"),
        ("selected_candidate_sha", "selected_candidate"),
    ):
        ref = sf_entry.payload[key]
        assert ref in entries_by_sha, f"{key} does not reference an existing ledger entry"
        assert entries_by_sha[ref].payload["kind"] == expected_kind


def test_f0_control_rejected_from_c3b(tmp_path: Path) -> None:
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)
    baseline_audit_entry_sha = _fake_baseline_audit(campaign)
    try:
        selection_stage.run_c3b_selection(
            campaign, {"F0_CONTROL": []}, baseline_audit_entry_sha=baseline_audit_entry_sha
        )
        raise AssertionError("expected ValueError for F0_CONTROL in c3b")
    except ValueError as exc:
        assert "F0_CONTROL" in str(exc)


def test_candidate_space_and_selection_rule_sha_are_stable() -> None:
    a = selection_stage.candidate_space_sha()
    b = selection_stage.candidate_space_sha()
    assert a == b
    assert len(a) == 64

    rule_a = selection_stage.selection_rule_sha()
    rule_b = selection_stage.selection_rule_sha()
    assert rule_a == rule_b
    assert len(rule_a) == 64
