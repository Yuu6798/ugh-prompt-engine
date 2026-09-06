"""`campaign/selection_stage.py` のテスト: C3a/C3b selection freeze の
event 構造・prerequisite entry_sha 相互参照（合成 criteria、real
measurement 不要のため高速）。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pytest

from voice_genesis.calibration.campaign import holdout_stage, measure_stage, selection_stage
from voice_genesis.calibration.campaign.state import load_frozen_campaign
from voice_genesis.calibration.candidates.adapter import MeterOutput
from voice_genesis.calibration.candidates.registry import candidate_by_id, candidates_for_meter
from voice_genesis.calibration.fixtures import controls as controls_module
from voice_genesis.calibration.fixtures.matrix import build_matrix
from voice_genesis.calibration.selection import CandidateCriteria, select_across_ceilings
from voice_genesis.calibration.vocab import ClaimCeiling, Domain, MeterId, MissingReason, Split

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
    value: float = 220.0,
) -> measure_stage.MeasurementRecord:
    output = (
        MeterOutput(values={"f0_hz": value})
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


# ---------------------------------------------------------------------------
# round 17 finding #1 (`[UNDERSPEC-CAL-D37]`): negative_controls_incomplete —
# mirrors round 13 finding #1's `positive_rows_absent` on the negative side.
# A declared (non-empty) negative-control population must have a record for
# EVERY declared row, not merely a non-empty intersection — this is what lets
# `workunits.c3a_f0_selection_instances()`/`c3b_family_selection_instances()`
# widening the C3 measurement set (round 17 finding #1) actually matter: a
# candidate that false-fires only on a control row homed outside SELECTION
# (e.g. HOLDOUT) is now measured and can be caught.
# ---------------------------------------------------------------------------


def test_candidate_false_fires_on_holdout_homed_negative_control_is_rejected() -> None:
    """A negative control row conceptually homed in HOLDOUT (its split is
    not tracked by `candidate_fail_filter_report()` itself — that's
    `workunits`' job, covered by `test_campaign_workunits.py` — but the
    round 17 finding #1 fix ensures such a row's instance is actually
    measured at C3, producing a record here) that the candidate fires on
    (a false positive on sweep-truth-free fixture) must reject the
    candidate via `negative_control_false_fire`, exactly like a
    SELECTION-homed one would."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [
        _record("row-negctl-selection-homed", 0, detected=False),
        _record("row-negctl-holdout-homed", 0, detected=True),  # false fire
    ]
    negative_ids = frozenset({"row-negctl-selection-homed", "row-negctl-holdout-homed"})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=negative_ids,
    )
    assert report["negative_control_false_fire"] is True
    assert report["negative_controls_incomplete"] is False  # both declared rows have records
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_missing_negative_control_record_is_reported_as_incomplete() -> None:
    """If the declared negative-control population is non-empty but at
    least one declared row has no matching record (e.g. its home split
    wasn't included in the measurement set — the exact bug round 17
    finding #1 fixed at the `workunits` layer), the candidate must be
    reported ineligible via `negative_controls_incomplete` — not silently
    treated as "no evidence, no failure" (fail-closed, mirrors
    `positive_rows_absent`)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-negctl-present", 0, detected=False)]
    negative_ids = frozenset({"row-negctl-present", "row-negctl-missing"})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=negative_ids,
    )
    assert report["negative_controls_incomplete"] is True
    assert report["negative_control_false_fire"] is False  # the one present record didn't fire
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_empty_declared_negative_population_is_not_a_failure() -> None:
    """Distinguish "no negative-control population declared for this
    family" (a legitimate no-op) from a declared-but-incomplete population
    (above)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-some-other-instance", 0, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset(),
    )
    assert report["negative_controls_incomplete"] is False
    assert report["negative_control_false_fire"] is False


def test_all_negative_control_records_present_is_complete() -> None:
    """Complete coverage (every declared row has >=1 record) must not
    trigger `negative_controls_incomplete`, regardless of detection
    outcome."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [
        _record("row-a", 0, detected=False),
        _record("row-b", 0, detected=False),
    ]
    negative_ids = frozenset({"row-a", "row-b"})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=negative_ids,
    )
    # only the two filters under test — not full eligibility, since
    # `within_fresh_process_mismatch` needs its own dedicated within+fresh
    # pairing per instance (covered elsewhere) and is orthogonal to negative
    # control coverage.
    assert report["negative_controls_incomplete"] is False
    assert report["negative_control_false_fire"] is False


# ---------------------------------------------------------------------------
# v1.1 §V1 (Design Memo AC5): F0_CONTROL's C3a negative control fail filter
# splits by control class. Deterministic-degenerate classes (SILENCE/
# TOO_SHORT/INVALID_SR) keep the any-fire zero-tolerance filter; NOISE_ONLY
# is excluded from it and instead reported as a rate (v1.0 §8's declared
# "voiced false detection" lexicographic ranking criterion) via the new
# `noise_only_control_row_ids` parameter.
# ---------------------------------------------------------------------------


def test_v11_zero_tolerance_class_any_fire_still_rejects_with_noise_only_split() -> None:
    """AC5(a): a false fire on a deterministic-degenerate row (SILENCE here,
    passed via `negative_control_row_ids`) must still zero-tolerance reject
    the candidate exactly as before, even when `noise_only_control_row_ids`
    is also supplied (i.e. splitting the population does not weaken the
    zero-tolerance side)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [
        _record("row-silence", 0, detected=True),  # false fire on SILENCE
        _record("row-noise-only", 0, detected=False),
    ]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence"}),
        noise_only_control_row_ids=frozenset({"row-noise-only"}),
    )
    assert report["negative_control_false_fire"] is True
    assert report["negative_controls_incomplete"] is False
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_v11_noise_only_false_fire_does_not_reject_but_reports_rate() -> None:
    """AC5(b): a candidate that false-fires only on the NOISE_ONLY row(s)
    must remain eligible (NOISE_ONLY is excluded from the any-fire
    `negative_control_false_fire` population) while the detection rate is
    exposed via the new audit-only keys for the caller to wire into the
    ranking criteria."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    field = measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY[candidate.algorithm_family]
    # `_instance_records` gives each instance a matched within+fresh pair
    # (consistent detected/missing status on both sides) so this test
    # isolates the NOISE_ONLY split, without also tripping
    # `within_fresh_process_mismatch` (`[UNDERSPEC-CAL-D67]`).
    records = _instance_records("row-silence", 0, candidate.candidate_id, field=field, missing=True)
    noise_only_detected = (True, True, False, False, False)  # 2/5 false fires
    for probe_index, detected in enumerate(noise_only_detected):
        records += _instance_records(
            "row-noise-only",
            probe_index,
            candidate.candidate_id,
            field=field,
            missing=not detected,
        )
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence"}),
        noise_only_control_row_ids=frozenset({"row-noise-only"}),
    )
    assert report["negative_control_false_fire"] is False
    assert report["negative_controls_incomplete"] is False
    assert report["noise_only_instances_total"] == 5
    assert report["noise_only_instances_detected"] == 2
    assert report["noise_only_false_detection_rate"] == pytest.approx(0.4)
    assert selection_stage.eligible_after_fail_filters(report) is True


def test_v11_noise_only_missing_records_still_reported_as_incomplete() -> None:
    """The NOISE_ONLY exemption is from the *any-fire* filter only — record
    completeness (`negative_controls_incomplete`) must still cover the
    NOISE_ONLY population, via the union of `negative_control_row_ids` and
    `noise_only_control_row_ids`, so a home-split gap on the NOISE_ONLY row
    is not silently invisible."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-silence", 0, detected=False)]  # NOISE_ONLY row absent entirely
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence"}),
        noise_only_control_row_ids=frozenset({"row-noise-only"}),
    )
    assert report["negative_controls_incomplete"] is True
    assert report["noise_only_instances_total"] == 0
    assert report["noise_only_false_detection_rate"] is None
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_v11_noise_only_split_is_a_no_op_when_not_declared() -> None:
    """AC5(d) (unit-level guard): omitting `noise_only_control_row_ids`
    (the C3b call shape, family selection unrelated to F0_CONTROL) leaves
    `negative_control_false_fire`/`negative_controls_incomplete` computed
    over `negative_control_row_ids` alone, exactly as before this change,
    and reports the new keys as empty/`None`."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-a", 0, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-a"}),
    )
    assert report["negative_control_false_fire"] is True
    assert report["noise_only_instances_total"] == 0
    assert report["noise_only_instances_detected"] == 0
    assert report["noise_only_false_detection_rate"] is None


def test_v11_noise_only_rate_decides_lexicographic_tie_on_error_terms() -> None:
    """AC5(c): with `primary_normalized_mae`/`signed_bias`/`primary_q95_ae`
    tied between two ABSOLUTE candidates, the criterion consuming the
    NOISE_ONLY false-detection rate (`nuisance_sensitivity_max` — the
    existing ranking-vector slot immediately after the error terms and
    before `missing_failure_rate`, reused per v1.1 §V1 since `selection.py`
    is out of this WP's scope) must decide the ranking, exactly matching
    v1.0 §8's declared order (cents error -> octave-error rate -> voiced
    false detection rate -> process reproducibility)."""
    lower_rate = CandidateCriteria(
        candidate_id="F0-LOWER-NOISE-RATE",
        ceiling=ClaimCeiling.ABSOLUTE,
        primary_normalized_mae=0.01,
        signed_bias=0.001,
        primary_q95_ae=0.02,
        nuisance_sensitivity_max=0.2,  # 2/5 NOISE_ONLY false-detection rate
    )
    higher_rate = CandidateCriteria(
        candidate_id="F0-HIGHER-NOISE-RATE",
        ceiling=ClaimCeiling.ABSOLUTE,
        primary_normalized_mae=0.01,  # tied
        signed_bias=0.001,  # tied
        primary_q95_ae=0.02,  # tied
        nuisance_sensitivity_max=0.8,  # 4/5 NOISE_ONLY false-detection rate
    )
    outcome = select_across_ceilings([lower_rate, higher_rate])
    assert outcome.outcome == "SELECTED"
    assert outcome.selected_candidate_id == "F0-LOWER-NOISE-RATE"


def test_v11_f0_selection_frozen_payload_records_noise_only_breakdown(tmp_path: Path) -> None:
    """AC5(e): `f0_selection_frozen`'s `fail_filters_by_candidate` payload
    must carry the machine-readable control-class breakdown — the
    zero-tolerance any-fire verdict and the NOISE_ONLY rate/counts — exactly
    as `candidate_fail_filter_report()` returns them (no re-summarization
    that would lose the split)."""
    campaign_dir, secret_root = build_tiny_campaign(tmp_path)
    campaign = load_frozen_campaign(campaign_dir, secret_root)

    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [
        _record("row-silence", 0, detected=False),
        _record("row-noise-only", 0, detected=True),
        _record("row-noise-only", 1, detected=False),
        _record("row-noise-only", 2, detected=False),
        _record("row-noise-only", 3, detected=False),
        _record("row-noise-only", 4, detected=False),
    ]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence"}),
        noise_only_control_row_ids=frozenset({"row-noise-only"}),
    )
    criteria = [
        CandidateCriteria(
            candidate_id="F0-B0-CURRENT",
            ceiling=ClaimCeiling.ABSOLUTE,
            primary_normalized_mae=0.01,
            signed_bias=0.001,
            primary_q95_ae=0.02,
            nuisance_sensitivity_max=report["noise_only_false_detection_rate"],
        )
    ]
    result = selection_stage.run_c3a_f0_selection(
        campaign, criteria, fail_filter_reports={"F0-B0-CURRENT": report}
    )
    entry = next(
        e for e in campaign.ledger.entries if e.entry_sha == result.f0_selection_frozen_entry_sha
    )
    recorded = entry.payload["fail_filters_by_candidate"]["F0-B0-CURRENT"]
    assert recorded["negative_control_false_fire"] is False
    assert recorded["noise_only_instances_total"] == 5
    assert recorded["noise_only_instances_detected"] == 1
    assert recorded["noise_only_false_detection_rate"] == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`) "Count rejected F0 instances as
# missing coverage" → round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`)
# → round 2 #344 ADOPT (`[UNDERSPEC-CAL-D71]`, amends D68): `coverage_
# incomplete` — instance-granular completion of `negative_controls_
# incomplete`/`positive_rows_absent`. Skipping an F0-dependent candidate on
# an F0-unusable instance (`[UNDERSPEC-CAL-D61]`) removes that instance from
# `records` entirely (no `MeasurementRecord`, no `meter_call` ledger event —
# a `measurement_missing` skip event stands in its place, `[UNDERSPEC-CAL-
# D64]`/`[UNDERSPEC-CAL-D65]`); `build_candidate_criteria()` only ever sees
# `records`, so this gap was previously invisible to selection. D68 widened
# the expected population (TRUTH_CORE + CONFOUND, see `tests/test_
# controls.py` for the domain-population tests) — kept as-is by D71 — but
# also made the check value-aware (a present-but-`missing_reason`-explained
# record no longer counted as covered). D71 reverts the value-aware part:
# DESIGN_VG_METER_CAL_DEBT_v1.0.md §9 (~L300-305) lists "missing/failure
# rate" as a lexicographic *ranking* criterion for selection, not a hard
# eligibility gate, so a candidate whose only fault is one legitimately
# recorded, explained `OUTPUT_MISSING` on a PRIMARY instance must stay
# eligible and compete via `missing_failure_rate` — see
# `test_present_but_missing_valued_record_is_still_coverage_complete` below
# (renamed from `..._is_still_coverage_incomplete`, the direct regression
# test for D71, superseding self-review round 30 MAJOR finding #1's
# "縮小母集団で勝つ" scenario for the *explained*-miss case — the *absent*-call
# case that scenario also covers stays fail-closed via `coverage_
# incomplete`, see `test_missing_expected_instance_record_is_reported_as_
# coverage_incomplete` above).
# ---------------------------------------------------------------------------


def test_missing_expected_instance_record_is_reported_as_coverage_incomplete() -> None:
    """A declared (non-empty) expected instance population with at least one
    instance lacking any record — the exact shape an F0-unusable skip leaves
    behind — must reject the candidate via `coverage_incomplete`, fail-closed
    (mirrors `positive_rows_absent`/`negative_controls_incomplete`, but at
    `(row_id, probe_index)` granularity, not row_id — a row with *some*
    probe_index records present is not "seen" for the specific missing
    probe_index)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-a", 0, detected=True)]
    expected = frozenset({("row-a", 0), ("row-a", 1)})  # probe_index 1 never measured
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        expected_coverage_instances=expected,
    )
    assert report["coverage_incomplete"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_all_expected_instances_present_with_finite_values_is_coverage_complete() -> None:
    """Complete instance-level coverage — every expected instance has at
    least one record with a finite primary value — must not trigger
    `coverage_incomplete`."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [
        _record("row-a", 0, detected=True),
        _record("row-a", 1, detected=True),
    ]
    expected = frozenset({("row-a", 0), ("row-a", 1)})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        expected_coverage_instances=expected,
    )
    assert report["coverage_incomplete"] is False


def test_present_but_missing_valued_record_is_still_coverage_complete() -> None:
    """round 2 #344 ADOPT (`[UNDERSPEC-CAL-D71]`, amends D68) regression:
    renamed from `..._is_still_coverage_incomplete` (round 30 self-review
    finding #1's original assertion — since reverted). A record that
    *exists* for an expected instance but carries no finite primary value
    (here `detected=False` -> `MissingReason.OUTPUT_MISSING`, a legitimately
    recorded, explained miss — as opposed to the instance never being
    called at all) must still count as covered: `coverage_incomplete`
    detects ABSENT calls only (see the D71 block comment above). The
    candidate must stay eligible, and the explained miss instead shows up in
    `build_candidate_criteria()`'s `missing_failure_rate`
    (DESIGN_VG_METER_CAL_DEBT_v1.0.md §9 ~L300-305: missing/failure rate is
    a lexicographic *ranking* criterion, not a hard eligibility gate)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    field = measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY[candidate.algorithm_family]
    # `_instance_records` gives each instance a matched within+fresh pair
    # (consistent missing-status on both sides) so this test isolates
    # `coverage_incomplete` alone, without also tripping
    # `within_fresh_process_mismatch` (`[UNDERSPEC-CAL-D67]`).
    records = _instance_records(
        "row-a", 0, candidate.candidate_id, field=field, value=220.0
    ) + _instance_records(
        "row-a", 1, candidate.candidate_id, field=field, missing=True  # present, no finite value
    )
    expected = frozenset({("row-a", 0), ("row-a", 1)})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        expected_coverage_instances=expected,
    )
    assert report["coverage_incomplete"] is False
    assert selection_stage.eligible_after_fail_filters(report) is True

    truth_by_instance = {("row-a", 0): 220.0, ("row-a", 1): 220.0}
    criteria = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)
    assert criteria.eligible is True
    assert criteria.missing_failure_rate == pytest.approx(0.5)  # half the records are missing


def test_empty_expected_instance_population_is_not_a_coverage_failure() -> None:
    """Distinguish "no expected-instance population declared" (a legitimate
    no-op — the caller passing the default `frozenset()`) from a declared-
    but-incomplete population (above)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-a", 0, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        expected_coverage_instances=frozenset(),
    )
    assert report["coverage_incomplete"] is False


def _field_record(
    row_id: str,
    probe_index: int,
    candidate_id: str,
    *,
    field: str,
    missing: bool = False,
    value: float = 1.0,
    repeat_kind: str = "within",
    repeat_index: int = 0,
    process_id: str = "p0",
) -> measure_stage.MeasurementRecord:
    output = (
        MeterOutput(missing_reason=MissingReason.OUTPUT_MISSING)
        if missing
        else MeterOutput(values={field: value})
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


def _instance_records(
    row_id: str,
    probe_index: int,
    candidate_id: str,
    *,
    field: str,
    missing: bool = False,
    value: float = 1.0,
) -> list[measure_stage.MeasurementRecord]:
    """1 within-process + 1 fresh-process record for `(row_id, probe_index)`
    with a consistent missing-status/value on both sides (so
    `adapter.within_fresh_process_mismatch()` does not itself flag the
    instance — `[UNDERSPEC-CAL-D67]`: consistent missing across every
    within/fresh call is not a mismatch)."""
    return [
        _field_record(
            row_id,
            probe_index,
            candidate_id,
            field=field,
            missing=missing,
            value=value,
            repeat_kind="within",
            process_id="within-p0",
        ),
        _field_record(
            row_id,
            probe_index,
            candidate_id,
            field=field,
            missing=missing,
            value=value,
            repeat_kind="fresh",
            process_id="fresh-p0",
        ),
    ]


# ---------------------------------------------------------------------------
# round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`) → round 2 #344
# ADOPT (`[UNDERSPEC-CAL-D71]`, amends D68): the real-matrix, real-domain-
# population regression the self-review finding asked for directly (test gap
# 5(b) in the self-review). D68 originally asserted that a candidate
# consistently (but *explained*-ly) missing on a hard CONFOUND row became
# ineligible via `coverage_incomplete`; D71 reverts that — an explained miss
# is a measured outcome (a present `meter_call` record), not an absent call,
# so it stays eligible and instead shows up in `missing_failure_rate` —
# identically to the pre-existing BOUNDARY-only behaviour below (BOUNDARY
# missing was already design-sanctioned, §1 D2). TRUTH_CORE behaviour is
# unchanged in outcome (still ineligible — now solely via the pre-existing
# `positive_control_non_fire`, since `coverage_incomplete` no longer fires
# on a present-but-explained-miss record either).
# ---------------------------------------------------------------------------

_D68_FAMILY = "APERIODICITY_GT"


def _d68_candidate_and_field():
    candidate = next(
        c
        for c in candidates_for_meter(MeterId.M2_APERIODICITY)
        if c.algorithm_family == "HARMONIC_RESIDUAL"
    )
    field = measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY[candidate.algorithm_family]
    return candidate, field


def test_confound_row_consistent_explained_missing_is_eligible() -> None:
    """round 2 #344 ADOPT (`[UNDERSPEC-CAL-D71]`, amends D68, renamed from
    `..._is_ineligible_via_coverage_incomplete`): a candidate that
    consistently returns a legitimately recorded, explained `OUTPUT_MISSING`
    for every probe of a hard CONFOUND row — every `(row_id, probe_index)`
    still has a present `meter_call`-shaped record — is no longer made
    ineligible by `coverage_incomplete` (D68's value-aware check over-tightened
    past the frozen contract, DESIGN_VG_METER_CAL_DEBT_v1.0.md §9 ~L300-305:
    missing/failure rate is a ranking criterion, not a hard gate). It stays
    eligible; the miss instead surfaces in `missing_failure_rate`."""
    rows = build_matrix()
    # force every row of the family home to SELECTION (deterministic, no
    # dependence on split-secret randomness reaching a CONFOUND row) — the
    # instance-set filtering under test is entirely a function of `row.block`
    # /`domain` + `assignment`, not of how the assignment was produced.
    assignment = {mr.row_id: Split.SELECTION for mr in rows if mr.row.family == _D68_FAMILY}
    expected = controls_module.non_boundary_selection_instances(
        rows, assignment, Split.SELECTION, family=_D68_FAMILY
    )
    row_by_id = {mr.row_id: mr for mr in rows}
    confound_row_id = next(
        row_id for row_id, _p in sorted(expected) if row_by_id[row_id].row.block == "CONFOUND"
    )

    candidate, field = _d68_candidate_and_field()
    records = [
        r
        for row_id, probe_index in sorted(expected)
        for r in _instance_records(
            row_id,
            probe_index,
            candidate.candidate_id,
            field=field,
            missing=(row_id == confound_row_id),
        )
    ]

    report = selection_stage.candidate_fail_filter_report(
        candidate, records, expected_coverage_instances=expected
    )
    assert report["coverage_incomplete"] is False
    assert selection_stage.eligible_after_fail_filters(report) is True

    truth_by_instance = {
        (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
        for mr in rows
        if mr.row.family == _D68_FAMILY
        for p in range(controls_module.PROBE_REPEATS)
    }
    truth_by_instance = {k: v for k, v in truth_by_instance.items() if v is not None}
    criteria = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)
    assert criteria.eligible is True
    assert criteria.missing_failure_rate > 0.0


def test_boundary_row_consistent_missing_stays_eligible_via_missing_failure_rate() -> None:
    rows = build_matrix()
    assignment = {mr.row_id: Split.SELECTION for mr in rows if mr.row.family == _D68_FAMILY}
    expected = controls_module.non_boundary_selection_instances(
        rows, assignment, Split.SELECTION, family=_D68_FAMILY
    )
    boundary_rows = [
        mr for mr in rows if mr.row.family == _D68_FAMILY and mr.row.block == "BOUNDARY"
    ]
    assert boundary_rows, "test setup needs >=1 BOUNDARY row for the family"
    boundary_row_id = boundary_rows[0].row_id
    assert (boundary_row_id, 0) not in expected, (
        "BOUNDARY rows must be exempt from the non-BOUNDARY expected-coverage population"
    )

    candidate, field = _d68_candidate_and_field()
    covered_records = [
        r
        for row_id, probe_index in sorted(expected)
        for r in _instance_records(row_id, probe_index, candidate.candidate_id, field=field)
    ]
    boundary_missing_records = [
        r
        for probe_index in range(controls_module.PROBE_REPEATS)
        for r in _instance_records(
            boundary_row_id, probe_index, candidate.candidate_id, field=field, missing=True
        )
    ]
    records = covered_records + boundary_missing_records

    report = selection_stage.candidate_fail_filter_report(
        candidate, records, expected_coverage_instances=expected
    )
    assert report["coverage_incomplete"] is False
    assert selection_stage.eligible_after_fail_filters(report) is True

    # the BOUNDARY-row missing records are still counted (as missing) in
    # `missing_failure_rate` — they are simply not what `coverage_incomplete`
    # itself polices (§1 D2: BOUNDARY missing is design-sanctioned).
    truth_by_instance = {
        (mr.row_id, p): selection_stage.truth_value_for_row(mr.row)
        for mr in rows
        if mr.row.family == _D68_FAMILY
        for p in range(controls_module.PROBE_REPEATS)
    }
    truth_by_instance = {k: v for k, v in truth_by_instance.items() if v is not None}
    criteria = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)
    assert criteria.missing_failure_rate > 0.0


def test_truth_core_row_consistent_missing_behaviour_is_unchanged() -> None:
    """The pre-existing guarantee (verified NOT broken by either D68 or its
    round 2 #344 revert `[UNDERSPEC-CAL-D71]`): a TRUTH_CORE row with
    consistent *explained* missing still makes the candidate ineligible —
    solely via `positive_control_non_fire` (as before D68). `coverage_
    incomplete` no longer independently fires here (D71: an explained-miss
    record is present, not absent), but the overall eligibility outcome is
    unchanged."""
    rows = build_matrix()
    assignment = {mr.row_id: Split.SELECTION for mr in rows if mr.row.family == _D68_FAMILY}
    expected = controls_module.non_boundary_selection_instances(
        rows, assignment, Split.SELECTION, family=_D68_FAMILY
    )
    pos_instances = controls_module.positive_detection_instances(
        rows, assignment, Split.SELECTION, family=_D68_FAMILY
    )
    pos_ids = frozenset(row_id for row_id, _p in pos_instances)
    row_by_id = {mr.row_id: mr for mr in rows}
    truth_core_row_id = next(
        row_id for row_id, _p in sorted(expected) if row_by_id[row_id].row.block == "TRUTH_CORE"
    )

    candidate, field = _d68_candidate_and_field()
    records = [
        r
        for row_id, probe_index in sorted(expected)
        for r in _instance_records(
            row_id,
            probe_index,
            candidate.candidate_id,
            field=field,
            missing=(row_id == truth_core_row_id),
        )
    ]

    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        positive_control_row_ids=pos_ids,
        expected_coverage_instances=expected,
    )
    assert report["coverage_incomplete"] is False
    assert report["positive_control_non_fire"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_family_where_every_candidate_has_one_explained_miss_does_not_fail_closed() -> None:
    """round 2 #344 ADOPT (`[UNDERSPEC-CAL-D71]`) — the false-terminal-outcome
    half of the finding: under D68's value-aware `coverage_incomplete`, a
    family where *every* candidate legitimately misses (explained
    `OUTPUT_MISSING`, present record) on just one PRIMARY instance would have
    made every candidate ineligible, driving `select_across_ceilings()` to
    `SELECTION_FAILED_CLOSED` for the whole family even though each
    candidate's error/bias/q95 vector is otherwise perfectly rankable. D71
    fixes this: an explained miss stays coverage-complete, so this scenario
    must select one of the two candidates, not fail closed."""
    candidate_a = candidate_by_id("F0-B0-CURRENT")
    candidate_b = candidate_by_id("F0-PYIN-FRAME2048-HOP256")
    field = measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY[candidate_a.algorithm_family]
    assert (
        measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY[candidate_b.algorithm_family]
        == field
    )
    expected = frozenset({("row-a", 0), ("row-a", 1)})
    truth_by_instance = {("row-a", 0): 220.0, ("row-a", 1): 220.0}

    def _criteria(candidate, *, detected_value: float, miss_probe_index: int):
        records = [
            r
            for p in (0, 1)
            for r in _instance_records(
                "row-a",
                p,
                candidate.candidate_id,
                field=field,
                missing=(p == miss_probe_index),
                value=detected_value,
            )
        ]
        report = selection_stage.candidate_fail_filter_report(
            candidate, records, expected_coverage_instances=expected
        )
        assert report["coverage_incomplete"] is False, candidate.candidate_id
        base = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)
        eligible = base.eligible and selection_stage.eligible_after_fail_filters(report)
        return dataclasses.replace(base, eligible=eligible)

    criteria_a = _criteria(candidate_a, detected_value=220.0, miss_probe_index=1)
    criteria_b = _criteria(candidate_b, detected_value=221.0, miss_probe_index=0)
    assert criteria_a.eligible is True
    assert criteria_b.eligible is True
    assert criteria_a.missing_failure_rate > 0.0
    assert criteria_b.missing_failure_rate > 0.0

    outcome = select_across_ceilings([criteria_a, criteria_b])
    assert outcome.outcome != "SELECTION_FAILED_CLOSED"
    assert outcome.selected_candidate_id in {
        candidate_a.candidate_id,
        candidate_b.candidate_id,
    }


# ---------------------------------------------------------------------------
# round 30 ADOPT (`[UNDERSPEC-CAL-D67]`, Codex round 30 PR #343 finding #2
# 「Allow stable negative-control non-detections」採用): candidate_fail_
# filter_report() を通じて `adapter.within_fresh_process_mismatch()` の
# missing-status 整合判定が正しく配線されていることを確認する（predicate
# 自体の網羅ケースは test_adapters.py 側。ここは selection の他 filter
# （negative_control_false_fire / positive_control_non_fire）との役割分担が
# 崩れていないことの統合確認）。
# ---------------------------------------------------------------------------


def _within_fresh_records(
    row_id: str,
    probe_index: int,
    *,
    candidate_id: str = "F0-B0-CURRENT",
    within_detected: Sequence[bool],
    fresh_detected: Sequence[bool],
) -> list[measure_stage.MeasurementRecord]:
    """1 instance 分の within-process/fresh-process repeat 群を組み立てる
    （`_record()` の単一 repeat 版を repeat_kind/repeat_index ごとに束ねる）。"""
    records = [
        _record(
            row_id,
            probe_index,
            candidate_id=candidate_id,
            detected=detected,
            repeat_kind="within",
            repeat_index=i,
            process_id="p-within",
        )
        for i, detected in enumerate(within_detected)
    ]
    records += [
        _record(
            row_id,
            probe_index,
            candidate_id=candidate_id,
            detected=detected,
            repeat_kind="fresh",
            repeat_index=i,
            process_id=f"p-fresh-{i}",
        )
        for i, detected in enumerate(fresh_detected)
    ]
    return records


def test_negative_control_consistent_missing_stays_eligible() -> None:
    """A candidate that correctly returns `OUTPUT_MISSING` on every within
    call and every fresh call for a negative control instance (e.g. silence)
    must not be penalized by `within_fresh_process_mismatch` — a consistent
    non-detection is the CORRECT negative-control outcome, not a mismatch,
    and must not trip `negative_control_false_fire` either."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = _within_fresh_records(
        "row-negctl-silence",
        0,
        within_detected=[False, False, False],
        fresh_detected=[False, False, False],
    )
    negative_ids = frozenset({"row-negctl-silence"})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=negative_ids,
    )
    assert report["within_fresh_process_mismatch"] is False
    assert report["negative_control_false_fire"] is False
    assert selection_stage.eligible_after_fail_filters(report) is True


def test_negative_control_one_process_reporting_value_is_a_mismatch() -> None:
    """If even one within/fresh call reports a finite value while the rest of
    the calls for the same instance consistently report missing, the
    missing-status itself is inconsistent across processes — a real
    within/fresh mismatch, unlike the fully-consistent case above."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = _within_fresh_records(
        "row-negctl-silence",
        0,
        within_detected=[False, False, False],
        fresh_detected=[False, True, False],  # one fresh call reports a value
    )
    negative_ids = frozenset({"row-negctl-silence"})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=negative_ids,
    )
    assert report["within_fresh_process_mismatch"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_positive_row_consistent_missing_is_non_fire_not_mismatch() -> None:
    """A positive-control (TRUTH_CORE) row where the candidate consistently
    fails to detect across every within/fresh call must be rejected via
    `positive_control_non_fire` (the existing, correct filter for this
    outcome) — the same missing-status consistency semantics apply here too,
    so this must NOT also (mis)fire `within_fresh_process_mismatch`."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = _within_fresh_records(
        "row-positive-anchor",
        0,
        within_detected=[False, False, False],
        fresh_detected=[False, False, False],
    )
    positive_ids = frozenset({"row-positive-anchor"})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        positive_control_row_ids=positive_ids,
    )
    assert report["within_fresh_process_mismatch"] is False
    assert report["positive_control_non_fire"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


# ---------------------------------------------------------------------------
# round 19 finding #1 (`[UNDERSPEC-CAL-D43]`): build_candidate_criteria must
# aggregate repeats with the frozen §10.1 two-stage median, not a flat mean
# ("Aggregate repeats with the frozen two-stage median" — Codex round 19
# PR #343 finding #1).
# ---------------------------------------------------------------------------


def test_build_candidate_criteria_uses_two_stage_median_not_mean() -> None:
    """design quote (§10.1): `m[i] = median_p( median_r( x_hat[i,p,r] ) )`
    （二段 median。process 間 repeat 不均等時の支配を防ぐ）. An outlier
    fresh-process repeat (1000.0, vs. every other repeat at 100.0) must be
    fully suppressed: within-process median=100.0, fresh-process-0/1
    medians=100.0, fresh-process-2 median=1000.0 -> outer median over
    [100.0, 100.0, 100.0, 1000.0] = 100.0, exactly matching truth. A flat
    mean over the same 6 raw values would instead be
    (100*3 + 100 + 100 + 1000) / 6 = 250.0 -- a 150.0 gap from truth that
    must not leak into signed_bias / q95(AE) / normalized MAE."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    row_id, probe_index = "row-a", 0
    records = [
        _record(
            row_id, probe_index, repeat_kind="within", repeat_index=0,
            process_id="within-process", value=100.0,
        ),
        _record(
            row_id, probe_index, repeat_kind="within", repeat_index=1,
            process_id="within-process", value=100.0,
        ),
        _record(
            row_id, probe_index, repeat_kind="within", repeat_index=2,
            process_id="within-process", value=100.0,
        ),
        _record(
            row_id, probe_index, repeat_kind="fresh", repeat_index=0,
            process_id="fresh-process-0", value=100.0,
        ),
        _record(
            row_id, probe_index, repeat_kind="fresh", repeat_index=1,
            process_id="fresh-process-1", value=100.0,
        ),
        _record(
            row_id, probe_index, repeat_kind="fresh", repeat_index=2,
            process_id="fresh-process-2", value=1000.0,
        ),
    ]
    truth_by_instance = {(row_id, probe_index): 100.0}
    criteria = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)
    assert criteria.eligible is True
    # mean-based aggregation would have produced signed_bias/q95(AE) ~150.0;
    # the two-stage median suppresses the outlier entirely.
    assert criteria.signed_bias == pytest.approx(0.0, abs=1e-9)
    assert criteria.primary_q95_ae == pytest.approx(0.0, abs=1e-9)
    assert criteria.primary_normalized_mae == pytest.approx(0.0, abs=1e-9)


def test_selection_aggregation_matches_holdout_two_stage_median() -> None:
    """C3 selection and C4 holdout must agree on the aggregated per-instance
    observation for identical repeat data (both are §10.1 `m[i]` consumers):
    selection's `signed_bias`/`primary_q95_ae` (raw-error based, round 19
    finding #2) must equal the `e`/`ae` that
    `holdout_stage.build_instance_margins` derives from the exact same
    `per_process_repeats`."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    row_id, probe_index = "row-a", 0
    per_process = {
        "within-process": [101.0, 99.0, 100.0],
        "fresh-process-0": [102.0],
        "fresh-process-1": [98.0],
        "fresh-process-2": [500.0],  # outlier fresh repeat
    }
    truth = 100.0
    records = [
        _record(
            row_id, probe_index, repeat_kind="within", repeat_index=i,
            process_id="within-process", value=v,
        )
        for i, v in enumerate(per_process["within-process"])
    ] + [
        _record(
            row_id, probe_index, repeat_kind="fresh", repeat_index=i,
            process_id=f"fresh-process-{i}", value=per_process[f"fresh-process-{i}"][0],
        )
        for i in range(3)
    ]
    truth_by_instance = {(row_id, probe_index): truth}
    criteria = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)

    obs = holdout_stage.RawInstanceObservation(
        instance_id=f"{row_id}:{probe_index}",
        domain=Domain.PRIMARY,
        truth=truth,
        per_process_repeats=per_process,
        u_gt=0.0,
        u_num=0.0,
        e_use=1.0,
    )
    margin = holdout_stage.build_instance_margins([obs])[0]

    assert criteria.signed_bias == pytest.approx(margin.e, abs=1e-9)
    assert criteria.primary_q95_ae == pytest.approx(margin.ae, abs=1e-9)


# ---------------------------------------------------------------------------
# round 19 finding #2 (`[UNDERSPEC-CAL-D44]`): BIAS/q95(AE) must use raw
# error `e[i]`/`AE[i]` per §10.1, not the relative error `RE[i]` reused from
# normalized MAE ("Compute bias and q95 from absolute errors" — Codex round
# 19 PR #343 finding #2).
# ---------------------------------------------------------------------------


def test_build_candidate_criteria_bias_and_q95_use_raw_not_relative_error() -> None:
    """Hand-computed unit test for all three ABSOLUTE statistics, 1 candidate
    x 2 instances (truth=1000, truth=1):

    - relative errors: RE = (+0.5, -0.5) -> normalized MAE = mean(|RE|)
      = (0.5 + 0.5) / 2 = 0.5
    - raw errors: e = RE * truth = (+500.0, -0.5) -> raw BIAS = mean(e)
      = (500.0 + (-0.5)) / 2 = 249.75
    - raw AE = (500.0, 0.5); q95 (numpy method="linear", n=2): sorted =
      [0.5, 500.0], index = 0.95 * (2-1) = 0.95 ->
      0.5 + 0.95 * (500.0 - 0.5) = 475.025

    The pre-fix implementation reused the relative-error array for all three
    stats: it would have reported signed_bias == mean(RE) == 0.0 and
    primary_q95_ae == q95(|RE|) == 0.5 -- both wrong under the ruling.
    """
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [
        _record("row-big", 0, process_id="p", value=1500.0),  # truth=1000, e=+500.0
        _record("row-small", 0, process_id="p", value=0.5),  # truth=1, e=-0.5
    ]
    truth_by_instance = {("row-big", 0): 1000.0, ("row-small", 0): 1.0}
    criteria = selection_stage.build_candidate_criteria(candidate, records, truth_by_instance)

    assert criteria.primary_normalized_mae == pytest.approx(0.5, abs=1e-9)
    assert criteria.signed_bias == pytest.approx(249.75, abs=1e-9)
    assert criteria.primary_q95_ae == pytest.approx(475.025, abs=1e-6)
    # the pre-fix relative-error reuse this replaces would have produced
    # these instead -- confirm the fix actually diverges from it.
    assert criteria.signed_bias != pytest.approx(0.0, abs=1e-6)
    assert criteria.primary_q95_ae != pytest.approx(0.5, abs=1e-6)


def test_selection_winner_flips_between_raw_and_relative_bias() -> None:
    """Two candidates tied on normalized MAE (lexicographic item 1, §9) but
    disagreeing on raw-vs-relative BIAS (item 2) -- the ruling (raw BIAS)
    must decide the winner, not a reuse of the relative error already tied
    for item 1.

    Candidate P: truth=1000 -> RE=+0.5 (e=+500.0); truth=1 -> RE=-0.5
    (e=-0.5). mean(|RE|) = 0.5. raw BIAS = mean(500.0, -0.5) = 249.75.

    Candidate Q: truth=1000 -> RE=+0.01 (e=+10.0); truth=1 -> RE=+0.99
    (e=+0.99). mean(|RE|) = (0.01 + 0.99) / 2 = 0.5 (tied with P).
    raw BIAS = mean(10.0, 0.99) = 5.495.

    Under the pre-fix relative-error reuse: |mean(RE)_P| = |0.0| = 0.0 <
    |mean(RE)_Q| = |0.5| -> P would have won. Under the ruling (raw BIAS):
    |raw_BIAS_P| = 249.75 > |raw_BIAS_Q| = 5.495 -> Q wins. The frozen
    winner is Q.
    """
    candidate = candidate_by_id("F0-B0-CURRENT")
    p_records = [
        _record("row-big", 0, process_id="p", value=1500.0),  # truth=1000, e=+500.0
        _record("row-small", 0, process_id="p", value=0.5),  # truth=1, e=-0.5
    ]
    truth_by_instance = {("row-big", 0): 1000.0, ("row-small", 0): 1.0}
    p_criteria = selection_stage.build_candidate_criteria(candidate, p_records, truth_by_instance)

    q_records = [
        _record("row-big", 0, process_id="p", value=1010.0),  # truth=1000, e=+10.0
        _record("row-small", 0, process_id="p", value=1.99),  # truth=1, e=+0.99
    ]
    q_criteria = selection_stage.build_candidate_criteria(candidate, q_records, truth_by_instance)

    # item 1 (normalized MAE) tied exactly -- the flip must come from item 2.
    assert p_criteria.primary_normalized_mae == pytest.approx(0.5, abs=1e-9)
    assert q_criteria.primary_normalized_mae == pytest.approx(0.5, abs=1e-9)

    assert p_criteria.signed_bias == pytest.approx(249.75, abs=1e-9)
    assert q_criteria.signed_bias == pytest.approx(5.495, abs=1e-9)

    p_named = dataclasses.replace(p_criteria, candidate_id="cand-P")
    q_named = dataclasses.replace(q_criteria, candidate_id="cand-Q")
    outcome = select_across_ceilings([p_named, q_named])
    assert outcome.selected_candidate_id == "cand-Q"


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.2 WP1: fire 判定の一本化 (`fixtures.controls.detected()`) +
# sanctioned abstention ((SILENCE, "F0_UNUSABLE") のみ) — c3b_failclosed_
# analysis.md §5.1「実装バグ疑い」の是正。`coverage_incomplete` が BOUNDARY
# -domain 行（negative control 行を含む）を design-sanctioned な欠測として
# 除外しているのに、`negative_controls_incomplete` は同じ SILENCE 行への
# record 皆無をゼロ許容で ineligible 化していた（F0 依存候補は SILENCE 行で
# `F0_UNUSABLE` により一切呼ばれず record が皆無になるため、品質に関係なく
# 100% 発火する決定論的デッドロック）。
# ---------------------------------------------------------------------------

_D71_APERIODICITY_HARMONIC_RESIDUAL_ID = "M2A-HARMONIC-RESIDUAL-K8-WINHANN-BANDBROADBAND"
_D71_APERIODICITY_D4C_ID = "M2A-D4C-BAND-BROADBAND"


def test_v12_sanctioned_silence_f0_unusable_abstention_resolves_incomplete_and_false_fire() -> None:  # noqa: E501
    """(a)+(d): a SILENCE negative-control row an F0-dependent candidate was
    never called on (`F0_UNUSABLE` skip -> zero own records for the row) must
    no longer trip `negative_controls_incomplete`, nor (since the row now
    contributes `False`/non-fire to the any-fire population instead of being
    absent from it) `negative_control_false_fire`. `coverage_incomplete`
    (BOUNDARY-row exempt population) must stay non-conflicting on the same
    fixture — the SILENCE row is simply outside `expected_coverage_
    instances`, as it always was, regardless of this fix."""
    candidate = candidate_by_id(_D71_APERIODICITY_HARMONIC_RESIDUAL_ID)
    records = [_record("row-primary", 0, candidate_id=candidate.candidate_id, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence"}),
        expected_coverage_instances=frozenset({("row-primary", 0)}),
        control_class_by_negative_row_id={"row-silence": "SILENCE"},
        missing_reason_by_negative_row_id={"row-silence": "F0_UNUSABLE"},
    )
    assert report["negative_controls_incomplete"] is False
    assert report["negative_control_false_fire"] is False
    assert report["coverage_incomplete"] is False


def test_v12_silence_row_missing_for_unrelated_reason_stays_incomplete() -> None:
    """(b): the exemption is keyed to `missing_reason == "F0_UNUSABLE"`
    specifically — a SILENCE row missing for a different reason (here
    `OUTPUT_MISSING`, a genuinely absent record unrelated to the F0-skip
    cause) must remain fail-closed."""
    candidate = candidate_by_id(_D71_APERIODICITY_HARMONIC_RESIDUAL_ID)
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        [],
        negative_control_row_ids=frozenset({"row-silence"}),
        control_class_by_negative_row_id={"row-silence": "SILENCE"},
        missing_reason_by_negative_row_id={"row-silence": "OUTPUT_MISSING"},
    )
    assert report["negative_controls_incomplete"] is True


def test_v12_noise_only_control_class_f0_unusable_stays_incomplete() -> None:
    """(c): `SANCTIONED_ABSTENTIONS` is the closed vocabulary
    `{(SILENCE, "F0_UNUSABLE")}` only — a NOISE_ONLY-classed row missing for
    the very same `F0_UNUSABLE` reason is not exempted."""
    candidate = candidate_by_id(_D71_APERIODICITY_HARMONIC_RESIDUAL_ID)
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        [],
        negative_control_row_ids=frozenset({"row-noise-only"}),
        control_class_by_negative_row_id={"row-noise-only": "NOISE_ONLY"},
        missing_reason_by_negative_row_id={"row-noise-only": "F0_UNUSABLE"},
    )
    assert report["negative_controls_incomplete"] is True


def test_v12_sanctioned_abstention_does_not_mask_false_fire_on_other_row() -> None:
    """The sanctioned SILENCE row contributes `False` (non-fire) to the
    any-fire population — it must not suppress a genuine false fire on a
    different negative-control row declared in the same population."""
    candidate = candidate_by_id(_D71_APERIODICITY_HARMONIC_RESIDUAL_ID)
    records = [_record("row-noise-only", 0, candidate_id=candidate.candidate_id, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence", "row-noise-only"}),
        control_class_by_negative_row_id={"row-silence": "SILENCE"},
        missing_reason_by_negative_row_id={"row-silence": "F0_UNUSABLE"},
    )
    assert report["negative_controls_incomplete"] is False
    assert report["negative_control_false_fire"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_v12_omitted_sanctioned_abstention_args_preserve_prior_behaviour() -> None:
    """Backward compatibility: omitting `control_class_by_negative_row_id`/
    `missing_reason_by_negative_row_id` (both default `None`) must reproduce
    the exact pre-v1.2 fail-closed outcome for a SILENCE row missing entirely
    — the same fixture as (a) above, minus the new kwargs."""
    candidate = candidate_by_id(_D71_APERIODICITY_HARMONIC_RESIDUAL_ID)
    records = [_record("row-primary", 0, candidate_id=candidate.candidate_id, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence"}),
        expected_coverage_instances=frozenset({("row-primary", 0)}),
    )
    assert report["negative_controls_incomplete"] is True


# ---------------------------------------------------------------------------
# real-data-derived minimal fixture: reproduces the exact APERIODICITY_GT
# HARMONIC_RESIDUAL / D4C shape captured in RUN10-CAL-20260905-410b25f2's
# `selection_frozen` ledger event (`c3b_failclosed_analysis.md` §4 / §5) —
# a SILENCE row fully F0_UNUSABLE (0 records) and a NOISE_ONLY row partially
# measured (probes 3/4 present, 0/1/2 F0_UNUSABLE). Not the full ledger — a
# minimal synthetic recreation of just the negative-control shape that drove
# `negative_controls_incomplete` for these 15 candidates.
# ---------------------------------------------------------------------------


def test_v12_aperiodicity_harmonic_residual_incomplete_resolves_but_stays_ineligible() -> None:
    """The 12 `HARMONIC_RESIDUAL` candidates in the real ledger: SILENCE row
    entirely F0_UNUSABLE (sanctioned -> `negative_controls_incomplete`
    resolves to False) but NOISE_ONLY row's measured probes (3, 4) genuinely
    false-fire (`negative_control_false_fire` stays True, untouched by this
    fix) -> candidate remains ineligible overall, now for the correct
    (real false-fire) reason instead of the mis-diagnosed incomplete flag."""
    candidate = candidate_by_id(_D71_APERIODICITY_HARMONIC_RESIDUAL_ID)
    records = [
        _record(
            "row-noise-only", probe_index, candidate_id=candidate.candidate_id, detected=True
        )
        for probe_index in (3, 4)
    ]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence", "row-noise-only"}),
        control_class_by_negative_row_id={
            "row-silence": "SILENCE",
            "row-noise-only": "NOISE_ONLY",
        },
        missing_reason_by_negative_row_id={
            "row-silence": "F0_UNUSABLE",
            "row-noise-only": "F0_UNUSABLE",
        },
    )
    assert report["negative_controls_incomplete"] is False
    assert report["negative_control_false_fire"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_v12_aperiodicity_d4c_incomplete_resolves_but_stays_ineligible_via_pyworld_absence() -> None:  # noqa: E501
    """The 3 `D4C_WORLD` candidates in the real ledger: same SILENCE-row
    sanctioned abstention as HARMONIC_RESIDUAL, but the NOISE_ONLY row's
    measured probes come back `ineligible=True` (pyworld absent) rather than
    a false fire, and the positive-control (TRUTH_CORE) instances are
    likewise all ineligible -> `positive_control_non_fire` fires instead.
    `negative_controls_incomplete` resolves to False either way; overall
    eligibility stays False, now attributable to the real (environmental,
    design-sanctioned) pyworld-absence cause rather than the incomplete
    mis-diagnosis."""
    candidate = candidate_by_id(_D71_APERIODICITY_D4C_ID)
    ineligible_output = MeterOutput(ineligible=True, ineligible_reason="INELIGIBLE_DEPENDENCY_ABSENT")
    records = [
        measure_stage.MeasurementRecord(
            row_id="row-noise-only",
            probe_index=probe_index,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=0,
            process_id="p0",
            output=ineligible_output,
        )
        for probe_index in (3, 4)
    ] + [
        measure_stage.MeasurementRecord(
            row_id="row-truth-core",
            probe_index=0,
            candidate_id=candidate.candidate_id,
            repeat_kind="within",
            repeat_index=0,
            process_id="p0",
            output=ineligible_output,
        )
    ]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        negative_control_row_ids=frozenset({"row-silence", "row-noise-only"}),
        positive_control_row_ids=frozenset({"row-truth-core"}),
        control_class_by_negative_row_id={
            "row-silence": "SILENCE",
            "row-noise-only": "NOISE_ONLY",
        },
        missing_reason_by_negative_row_id={
            "row-silence": "F0_UNUSABLE",
            "row-noise-only": "F0_UNUSABLE",
        },
    )
    assert report["negative_controls_incomplete"] is False
    assert report["negative_control_false_fire"] is False
    assert report["positive_control_non_fire"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


# ---------------------------------------------------------------------------
# RUN10-CAL-v1.2 WP1 (3): registry `Candidate.detection_predicate` folds into
# `candidate_space_sha`'s canonical serialization (the C0 candidate-space
# freeze this campaign re-checks at C3).
# ---------------------------------------------------------------------------


def test_candidate_space_sha_changes_when_a_candidate_declares_a_detection_predicate() -> None:
    base_candidate = candidate_by_id("F0-B0-CURRENT")
    without_predicate = (base_candidate,)
    with_predicate = (
        dataclasses.replace(
            base_candidate,
            detection_predicate=controls_module.DetectionPredicate(
                field="f0_hz", min_value=1.0
            ),
        ),
    )
    sha_without = selection_stage.candidate_space_sha(without_predicate)
    sha_with = selection_stage.candidate_space_sha(with_predicate)
    assert sha_without != sha_with


def test_candidate_space_sha_unchanged_for_existing_undeclared_candidates() -> None:
    """All 99 registered candidates leave `detection_predicate` at its
    default `None` in this revision — `candidate_space_sha()` over the real
    `ALL_CANDIDATES` pool must equal the sha of a pool built by explicitly
    setting every candidate's `detection_predicate` to `None` (i.e. declaring
    the field changes nothing for candidates that don't use it)."""
    from voice_genesis.calibration.candidates.registry import ALL_CANDIDATES

    explicit_none_pool = tuple(
        dataclasses.replace(c, detection_predicate=None) for c in ALL_CANDIDATES
    )
    assert selection_stage.candidate_space_sha() == selection_stage.candidate_space_sha(
        explicit_none_pool
    )
