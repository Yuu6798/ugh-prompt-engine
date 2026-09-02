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
from voice_genesis.calibration.candidates.registry import candidate_by_id
from voice_genesis.calibration.selection import CandidateCriteria, select_across_ceilings
from voice_genesis.calibration.vocab import ClaimCeiling, Domain, MissingReason

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
# round 28 ADOPT (2) (`[UNDERSPEC-CAL-D64]`) "Count rejected F0 instances as
# missing coverage": `coverage_incomplete` — instance-granular completion of
# `negative_controls_incomplete`/`positive_rows_absent`. Skipping an
# F0-dependent candidate on an F0-unusable instance (`[UNDERSPEC-CAL-D61]`)
# removes that instance from `records` entirely; `build_candidate_criteria()`
# only ever sees `records`, so the gap was previously invisible to selection.
# ---------------------------------------------------------------------------


def test_missing_expected_instance_record_is_reported_as_coverage_incomplete() -> None:
    """A declared (non-empty) expected TRUTH_CORE instance population with
    at least one instance lacking any record — the exact shape an F0-
    unusable skip leaves behind — must reject the candidate via
    `coverage_incomplete`, fail-closed (mirrors `positive_rows_absent`/
    `negative_controls_incomplete`, but at `(row_id, probe_index)`
    granularity, not row_id — a row with *some* probe_index records present
    is not "seen" for the specific missing probe_index)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-a", 0, detected=True)]
    expected = frozenset({("row-a", 0), ("row-a", 1)})  # probe_index 1 never measured
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        expected_truth_core_instances=expected,
    )
    assert report["coverage_incomplete"] is True
    assert selection_stage.eligible_after_fail_filters(report) is False


def test_all_expected_instances_present_is_coverage_complete() -> None:
    """Complete instance-level coverage must not trigger
    `coverage_incomplete`, regardless of the records' detection outcome."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [
        _record("row-a", 0, detected=True),
        _record("row-a", 1, detected=False),
    ]
    expected = frozenset({("row-a", 0), ("row-a", 1)})
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        expected_truth_core_instances=expected,
    )
    assert report["coverage_incomplete"] is False


def test_empty_expected_instance_population_is_not_a_coverage_failure() -> None:
    """Distinguish "no expected-instance population declared" (a legitimate
    no-op — the caller passing the default `frozenset()`) from a declared-
    but-incomplete population (above)."""
    candidate = candidate_by_id("F0-B0-CURRENT")
    records = [_record("row-a", 0, detected=True)]
    report = selection_stage.candidate_fail_filter_report(
        candidate,
        records,
        expected_truth_core_instances=frozenset(),
    )
    assert report["coverage_incomplete"] is False


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
