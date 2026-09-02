"""`campaign/selection_stage.py` のテスト: C3a/C3b selection freeze の
event 構造・prerequisite entry_sha 相互参照（合成 criteria、real
measurement 不要のため高速）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import measure_stage, selection_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import candidate_by_id
from voice_genesis.calibration.selection import CandidateCriteria, select_across_ceilings
from voice_genesis.calibration.vocab import ClaimCeiling, MissingReason

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


# ---------------------------------------------------------------------------
# finding #11 (第 11 巡採用): max_claim_scope capping
# ---------------------------------------------------------------------------


def test_max_claim_scope_from_manifest_missing_is_fail_closed() -> None:
    with pytest.raises(selection_stage.ClaimScopeError):
        selection_stage.max_claim_scope_from_manifest({})
    with pytest.raises(selection_stage.ClaimScopeError):
        selection_stage.max_claim_scope_from_manifest({"frozen_design": {}})
    with pytest.raises(selection_stage.ClaimScopeError):
        # wrong type (not a list[str]) is refused the same way as absent.
        selection_stage.max_claim_scope_from_manifest(
            {"frozen_design": {"max_claim_scope": "not-a-list"}}
        )
    assert selection_stage.max_claim_scope_from_manifest(
        {"frozen_design": {"max_claim_scope": ["a", "b"]}}
    ) == frozenset({"a", "b"})


def test_capped_ceiling_downgrades_out_of_scope_absolute_to_directional() -> None:
    scope = frozenset({"in_scope_construct"})
    capped, was_capped = selection_stage.capped_ceiling("out_of_scope", ClaimCeiling.ABSOLUTE, scope)
    assert capped == ClaimCeiling.DIRECTIONAL
    assert was_capped is True

    # already in scope: untouched.
    capped, was_capped = selection_stage.capped_ceiling(
        "in_scope_construct", ClaimCeiling.ABSOLUTE, scope
    )
    assert capped == ClaimCeiling.ABSOLUTE
    assert was_capped is False

    # DIAGNOSTIC_ONLY/NONE are already weaker than DIRECTIONAL -> unaffected
    # even out of scope (nothing to cap further down for this rule).
    for ceiling in (ClaimCeiling.DIAGNOSTIC_ONLY, ClaimCeiling.NONE):
        capped, was_capped = selection_stage.capped_ceiling("out_of_scope", ceiling, scope)
        assert capped == ceiling
        assert was_capped is False


def test_claim_scope_report_records_capping_fact() -> None:
    candidate = candidate_by_id("F0-B0-CURRENT")
    scope_excluding_it = frozenset({"some_other_construct"})
    capped, report = selection_stage.claim_scope_report(candidate, scope_excluding_it)
    assert report["construct"] == candidate.construct
    assert report["original_ceiling"] == candidate.claim_ceiling.value
    assert report["capped_ceiling"] == capped.value
    assert report["capped"] is (capped != candidate.claim_ceiling)


def test_out_of_scope_absolute_candidate_excluded_from_absolute_pool() -> None:
    """finding #11 regression: scope 外の construct を持つ ABSOLUTE 宣言
    候補は、selection 前に ceiling が DIRECTIONAL へ capping され、
    `select_across_ceilings` は ABSOLUTE pool ではなく DIRECTIONAL pool で
    扱う。ABSOLUTE pool が非空なら常にそちらが優先されるため、capping 無し
    なら（数値上はるかに良い）scope 外候補が誤って ABSOLUTE で選ばれて
    しまう — capping がその誤選択を防ぐことを確認する。"""
    out_of_scope_candidate = candidate_by_id("F0-B0-CURRENT")
    max_claim_scope = frozenset({"some_other_construct"})  # excludes its construct
    capped, report = selection_stage.claim_scope_report(out_of_scope_candidate, max_claim_scope)
    assert capped == ClaimCeiling.DIRECTIONAL
    assert report["capped"] is True

    in_scope_criteria = CandidateCriteria(
        candidate_id="in-scope-cand",
        ceiling=ClaimCeiling.ABSOLUTE,
        primary_normalized_mae=0.5,  # deliberately much worse numerically
        signed_bias=0.4,
        primary_q95_ae=0.6,
    )
    capped_criteria = CandidateCriteria(
        candidate_id=out_of_scope_candidate.candidate_id,
        ceiling=capped,  # DIRECTIONAL, post-capping (was ABSOLUTE)
        primary_normalized_mae=0.001,  # far better numerically, but out of scope
        signed_bias=0.0001,
        primary_q95_ae=0.002,
        kendall_tau=0.99,
        adjacent_reversal_rate=0.0,
    )
    outcome = select_across_ceilings([in_scope_criteria, capped_criteria])
    # the numerically worse *in-scope* ABSOLUTE candidate wins: pool
    # membership (decided by capped ceiling) outranks accuracy comparison.
    assert outcome.selected_candidate_id == "in-scope-cand"


# ---------------------------------------------------------------------------
# round 13 finding #1 (`[UNDERSPEC-CAL-D25]`): positive evidence = every
# TRUTH_CORE row of the evaluated split, not just the 2 designated anchors.
# ---------------------------------------------------------------------------


def _record(
    row_id: str,
    probe_index: int,
    *,
    candidate_id: str = "F0-B0-CURRENT",
    detected: bool = True,
    repeat_kind: str = "within",
    repeat_index: int = 0,
    process_id: str = "p0",
) -> measure_stage.MeasurementRecord:
    output = (
        MeterOutput(values={"f0_hz": 220.0})
        if detected
        else MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)
    )
    return measure_stage.MeasurementRecord(
        row_id=row_id,
        probe_index=probe_index,
        candidate_id=candidate_id,
        repeat_kind=repeat_kind,
        repeat_index=repeat_index,
        process_id=process_id,
        output=output,
    )


def test_candidate_fails_on_non_designated_truth_core_row_is_rejected() -> None:
    """A row that is a TRUTH_CORE row for the family but is *not* one of the
    2 legacy `positive_control=True` designated anchors must still be able to
    reject a candidate that fails to fire on it — the positive evidence
    population is the full per-split TRUTH_CORE set (round 13 finding #1)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    # "row-anchor-designated" would be a legacy 2-anchor row; here the
    # candidate fires fine on it, but fails (non-fire) on a *different*
    # TRUTH_CORE row that is still part of the expanded positive population.
    records = [
        _record("row-anchor-designated", 0, detected=True),
        _record("row-other-truth-core", 0, detected=False),
    ]
    positive_ids = frozenset({"row-anchor-designated", "row-other-truth-core"})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        positive_control_row_ids=positive_ids,
    )
    assert report["positive_control_non_fire"] is True
    assert report["positive_rows_absent"] is False
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_designated_anchors_absent_from_selection_is_reported_as_failure() -> None:
    """If the declared positive-row population is non-empty but no record in
    this evaluation matches any of those rows (e.g. the designated anchors'
    home split doesn't include SELECTION), the candidate must be reported
    ineligible via a distinct `positive_rows_absent` reason — not silently
    treated as "no evidence, no failure" (round 13 finding #1, fail-closed)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-some-other-instance", 0, detected=True)]
    positive_ids = frozenset({"row-anchor-designated"})  # never appears in records
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        positive_control_row_ids=positive_ids,
    )
    assert report["positive_rows_absent"] is True
    assert report["positive_control_non_fire"] is False  # no detections to be non-fired
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_empty_declared_positive_population_is_not_a_failure() -> None:
    """Distinguish "no positive population declared for this family" (still
    a legitimate no-op, per the module docstring) from a declared-but-absent
    population (finding #1, above)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-some-other-instance", 0, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        positive_control_row_ids=frozenset(),
    )
    assert report["positive_rows_absent"] is False
    assert report["positive_control_non_fire"] is False
