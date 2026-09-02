from __future__ import annotations

from voice_genesis.calibration.selection import CandidateCriteria, select_across_ceilings
from voice_genesis.calibration.vocab import ClaimCeiling


def test_successful_selection_preserves_none_ceiling_candidate_vector() -> None:
    selected = CandidateCriteria(
        candidate_id="abs-selected",
        ceiling=ClaimCeiling.ABSOLUTE,
        primary_normalized_mae=0.2,
        signed_bias=0.0,
        primary_q95_ae=0.3,
    )
    invalid_baseline = CandidateCriteria(
        candidate_id="M2T-B0-CURRENT-HYBRID",
        ceiling=ClaimCeiling.NONE,
        primary_normalized_mae=0.9,
        signed_bias=0.1,
        primary_q95_ae=1.1,
    )

    outcome = select_across_ceilings([selected, invalid_baseline])

    assert outcome.selected_candidate_id == "abs-selected"
    assert outcome.ranked_candidate_ids == ("abs-selected",)
    assert "M2T-B0-CURRENT-HYBRID" in outcome.raw_vectors
    assert "M2T-B0-CURRENT-HYBRID" in outcome.rounded_vectors
    assert (
        "M2T-B0-CURRENT-HYBRID",
        "different_ceiling_pool",
    ) in outcome.ineligible_candidates


def test_failed_selection_accounts_for_none_ceiling_without_criteria() -> None:
    invalid_baseline = CandidateCriteria(
        candidate_id="none-no-criteria",
        ceiling=ClaimCeiling.NONE,
    )

    outcome = select_across_ceilings([invalid_baseline])

    assert outcome.outcome == "SELECTION_FAILED_CLOSED"
    assert outcome.selected_candidate_id is None
    assert outcome.ranked_candidate_ids == ()
    assert "none-no-criteria" not in outcome.raw_vectors
    assert (
        "none-no-criteria",
        "criteria_payload_absent",
    ) in outcome.ineligible_candidates
