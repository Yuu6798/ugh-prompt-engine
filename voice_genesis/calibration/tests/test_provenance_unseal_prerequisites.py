import pytest

from voice_genesis.calibration.provenance import Ledger, _verified_holdout_unseal_seq
from voice_genesis.calibration.tests.test_provenance import _check_leakage
from voice_genesis.calibration.vocab import BlockedCode


_PREREQUISITES = (
    ("baseline_audit_sha", "baseline_audit"),
    ("candidate_space_sha", "candidate_space"),
    ("selection_rule_sha", "selection_rule"),
    ("selected_candidate_sha", "selected_candidate"),
)


def _prerequisite_payload(kind: str) -> dict[str, str]:
    artifact_markers = {
        "baseline_audit": "a",
        "candidate_space": "b",
        "selection_rule": "c",
        "selected_candidate": "d",
    }
    payload = {"kind": kind, "artifact_sha": artifact_markers[kind] * 64}
    if kind == "selected_candidate":
        payload["candidate_id"] = "candidate-test"
    return payload


def _append_prerequisites(ledger: Ledger) -> dict[str, str]:
    refs: dict[str, str] = {}
    for key, kind in _PREREQUISITES:
        refs[key] = ledger.append(_prerequisite_payload(kind)).entry_sha
    return refs


def _append_gate3_accepted(ledger: Ledger) -> str:
    """Append a well-formed `gate3_accepted` event (round 22 ADOPT,
    `UNDERSPEC-CAL-D50`: `holdout_unseal` must reference one of these by
    entry-SHA). Mirrors the payload shape `campaign/unseal.py` emits."""
    return ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-02T00:00:00Z",
        }
    ).entry_sha


def test_forged_64hex_prerequisites_do_not_unseal_holdout(tmp_path) -> None:
    """A synthetic selection freeze cannot authorize holdout with bare digests."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = {
        "baseline_audit_sha": "1" * 64,
        "candidate_space_sha": "2" * 64,
        "selection_rule_sha": "3" * 64,
        "selected_candidate_sha": "4" * 64,
    }
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
        }
    )
    ledger.append({"kind": "meter_call", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_prior_prerequisite_events_allow_normal_unseal_path(tmp_path) -> None:
    """Only prior canonical events -> selection freeze -> unseal can authorize access."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    gate3_sha = _append_gate3_accepted(ledger)
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=unseal.seq,
    )
    assert result.blocked is None


def test_wrong_prerequisite_event_kind_fails_closed(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    gate3_sha = _append_gate3_accepted(ledger)
    wrong = ledger.append({"kind": "meter_call", "row_id": "selection-row"})
    commitments["selection_rule_sha"] = wrong.entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE

@pytest.mark.parametrize("hollow_kind", [kind for _key, kind in _PREREQUISITES])
def test_hollow_prerequisite_event_payload_fails_closed(tmp_path, hollow_kind: str) -> None:
    """A kind-only prerequisite event is not sufficient to authorize unseal."""
    ledger = Ledger(tmp_path / f"ledger-{hollow_kind}.jsonl")
    commitments: dict[str, str] = {}
    for key, kind in _PREREQUISITES:
        payload = {"kind": kind} if kind == hollow_kind else _prerequisite_payload(kind)
        commitments[key] = ledger.append(payload).entry_sha
    gate3_sha = _append_gate3_accepted(ledger)
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=unseal.seq,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_selected_candidate_prerequisite_requires_candidate_id(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger-selected-candidate-id.jsonl")
    commitments = _append_prerequisites(ledger)
    gate3_sha = _append_gate3_accepted(ledger)
    malformed = ledger.append({"kind": "selected_candidate", "artifact_sha": "e" * 64})
    commitments["selected_candidate_sha"] = malformed.entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=unseal.seq,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


# ---------------------------------------------------------------------------
# round 22 ADOPT (`UNDERSPEC-CAL-D50`): `holdout_unseal` must also reference a
# prior chain-valid `gate3_accepted` event via `gate3_accepted_sha`. Before
# this fix, `_verified_holdout_unseal_seq` never inspected the field at all,
# so a crafted/legacy `holdout_unseal` row with correct selection-chain
# references but a missing or arbitrary Gate 3 reference was treated by
# `Ledger.check_leakage` as an authorized boundary.
# ---------------------------------------------------------------------------


def test_unseal_missing_gate3_accepted_sha_fails_closed_with_distinct_reason(tmp_path) -> None:
    """A `holdout_unseal` row with correct selection-chain references but no
    `gate3_accepted_sha` at all must not authorize holdout access, and
    `check_leakage` must report the distinct `UNSEAL_GATE3_UNVERIFIED` reason
    rather than the generic `BLOCKED_LEAKAGE` (which alone does not
    distinguish this from any other leakage failure)."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            # gate3_accepted_sha intentionally omitted.
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.reason == "UNSEAL_GATE3_UNVERIFIED"


def test_unseal_gate3_accepted_sha_pointing_to_non_gate3_event_fails_closed(tmp_path) -> None:
    """`gate3_accepted_sha` referencing a real, prior, chain-valid event that
    is not `kind == "gate3_accepted"` must not authorize holdout access."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    not_gate3 = ledger.append({"kind": "meter_call", "row_id": "not-gate3"})
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": not_gate3.entry_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.reason == "UNSEAL_GATE3_UNVERIFIED"


def test_unseal_gate3_accepted_sha_pointing_to_event_after_unseal_fails_closed(tmp_path) -> None:
    """`gate3_accepted_sha` referencing an event that appears *after* the
    `holdout_unseal` event must not authorize holdout access -- ordering, not
    merely existence-somewhere-in-the-ledger, is required.

    Because `entry_sha` chains forward (each entry's hash covers `prev_sha`,
    which recursively covers every earlier entry's payload), a `holdout_unseal`
    payload can never be constructed to literally embed the real `entry_sha`
    of an event appended *after* it without a SHA-256 preimage break: the
    later event's hash causally depends on the unseal payload itself. So this
    is tested the only way it is actually reachable in practice -- a
    genuinely well-formed `gate3_accepted` event exists later in the same
    chain, but the `gate3_accepted_sha` reference does not (cannot) match it,
    which is exactly what `_references_prior_gate3_acceptance`'s
    `prior_entries_by_sha` lookup is designed to reject.
    """
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": "9" * 64,
        }
    )
    late_gate3 = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-02T00:00:00Z",
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})
    assert late_gate3.entry_sha != "9" * 64

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.reason == "UNSEAL_GATE3_UNVERIFIED"


# ---------------------------------------------------------------------------
# round 23 ADOPT (3) (`[UNDERSPEC-CAL-D53]`): `_valid_gate3_accepted_payload`
# must validate the producer's minimum approval envelope exactly as
# `unseal_campaign()` emits it (`approval_content_sha256`, `approver`,
# `approved_at_utc`, `seal_protection_level_accepted`) -- not just `kind` +
# `seal_protection_level_accepted: True`. Before this, a `gate3_accepted`
# event carrying only those two fields (no approver, no approval-content
# binding, no timestamp) satisfied `_references_prior_gate3_acceptance()`.
# ---------------------------------------------------------------------------


def test_unseal_gate3_accepted_sha_referencing_minimal_envelope_fails_closed(tmp_path) -> None:
    """A `gate3_accepted` event carrying only `kind` +
    `seal_protection_level_accepted: True` -- what round 22's verifier alone
    accepted -- must not authorize holdout access: `unseal_campaign()` (the
    sole legitimate emitter) always supplies `approval_content_sha256`/
    `approver`/`approved_at_utc` too, so a row missing them can only be a
    crafted/legacy forgery."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    minimal_gate3 = ledger.append(
        {
            "kind": "gate3_accepted",
            "seal_protection_level_accepted": True,
        }
    )
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": minimal_gate3.entry_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.reason == "UNSEAL_GATE3_UNVERIFIED"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"approval_content_sha256": "g" * 63}, id="short_content_sha"),
        pytest.param({"approval_content_sha256": "G" * 64}, id="uppercase_content_sha"),
        pytest.param({"approver": ""}, id="blank_approver"),
        pytest.param({"approver": "   "}, id="whitespace_only_approver"),
        pytest.param({"approved_at_utc": "2026-09-02 00:00:00"}, id="no_utc_offset"),
        pytest.param({"approved_at_utc": "2026-09-02T00:00:00+09:00"}, id="non_utc_offset"),
        pytest.param({"approved_at_utc": "not-a-timestamp"}, id="malformed_timestamp"),
    ],
)
def test_unseal_gate3_accepted_sha_referencing_malformed_envelope_field_fails_closed(
    tmp_path, overrides: dict[str, str]
) -> None:
    """Each individual envelope field the round 23 verifier newly checks must
    independently gate acceptance -- an otherwise-complete `gate3_accepted`
    event with just one malformed field must not authorize holdout access."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    payload = {
        "kind": "gate3_accepted",
        "approval_content_sha256": "f" * 64,
        "seal_protection_level_accepted": True,
        "approver": "test-approver",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        **overrides,
    }
    malformed_gate3 = ledger.append(payload)
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": malformed_gate3.entry_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.reason == "UNSEAL_GATE3_UNVERIFIED"


def test_unseal_gate3_accepted_sha_referencing_production_envelope_passes(tmp_path) -> None:
    """The full production envelope `unseal_campaign()` actually emits
    (`_append_gate3_accepted()` -- `approval_content_sha256`/`approver`/
    `approved_at_utc`/`seal_protection_level_accepted: True`) must still
    authorize holdout access after the round 23 tightening."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    gate3_sha = _append_gate3_accepted(ledger)
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=unseal.seq,
    )
    assert result.blocked is None


# ---------------------------------------------------------------------------
# `[UNDERSPEC-CAL-D88]`(a): replay-verifier parity for the D85 freeze-
# ordering check `campaign.unseal.unseal_campaign()` already enforces live
# (`freeze_time < gate3_time`, `unseal.py:166-183`) -- `_verified_holdout_
# unseal_detail()` must independently re-derive the same ordering from the
# ledger's own `c0_freeze`/`gate3_accepted`/`holdout_unseal` timestamps when
# entry 0 is a `c0_freeze` event (the shape every real campaign ledger has,
# `campaign/state.py::load_frozen_campaign`). These tests build the ledger
# directly (not via `_check_leakage()`'s wrapper, which always prepends its
# own synthetic `split_frozen` entry 0 for the unrelated split-authentication
# checks -- entry 0 must be literally `c0_freeze` for this new check to
# engage at all) and assert against `_verified_holdout_unseal_seq()` itself.
# ---------------------------------------------------------------------------


def test_replay_verifier_rejects_gate3_predating_c0_freeze(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T10:00:00+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            # predates the c0_freeze event above -- must be rejected even
            # though the 4-prerequisite/commitment linkage is otherwise
            # entirely valid.
            "approved_at_utc": "2026-09-04T09:00:00+00:00",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            "event_time_utc": "2026-09-04T11:00:00+00:00",
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) is None


def test_replay_verifier_accepts_gate3_after_c0_freeze(tmp_path) -> None:
    """Companion to the rejection test above: the same ledger shape, but
    with `gate3_accepted.approved_at_utc` strictly after `c0_freeze.event_
    time_utc` and at or before `holdout_unseal.event_time_utc` -- the
    ordering holds, so the unseal boundary is accepted exactly as it would
    be without this check (D88 must not introduce a false failure on a
    genuinely valid, freeze-after ledger)."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T10:00:00+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-04T10:30:00+00:00",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            "event_time_utc": "2026-09-04T11:00:00+00:00",
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) == unseal.seq


def test_replay_verifier_accepts_gate3_30s_ahead_of_holdout_unseal(tmp_path) -> None:
    """Design revision (Codex review, adopted): the upper bound carries the
    same 60s clock-skew tolerance `unseal.py`'s own `_CLOCK_SKEW_TOLERANCE_
    SECONDS` allows Gate 3's `approved_at_utc` relative to the live check-
    time clock -- `unseal_campaign()` accepts a Gate 3 approval up to 60s
    ahead of "now" and then stamps `holdout_unseal.event_time_utc` moments
    later with the *local* clock, so a strict `gate3_time <= unseal_time`
    would falsely reject this entirely normal-path shape. Gate3 30s ahead
    of `holdout_unseal.event_time_utc` (well within the 60s tolerance) must
    verify as a valid unseal boundary."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T10:00:00+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            # 30s AFTER the holdout_unseal event_time_utc below -- within
            # the 60s tolerance.
            "approved_at_utc": "2026-09-04T11:00:30+00:00",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            "event_time_utc": "2026-09-04T11:00:00+00:00",
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) == unseal.seq


def test_replay_verifier_rejects_gate3_61s_ahead_of_holdout_unseal(tmp_path) -> None:
    """Companion boundary test: 61s ahead of `holdout_unseal.event_time_
    utc` -- 1s past the 60s tolerance -- must be rejected (fixes the
    tolerance's value at exactly 60s from both sides, matching `unseal.py`'s
    own `_CLOCK_SKEW_TOLERANCE_SECONDS`)."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T10:00:00+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-04T11:01:01+00:00",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            "event_time_utc": "2026-09-04T11:00:00+00:00",
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) is None


def test_replay_verifier_accepts_legacy_holdout_unseal_missing_event_time_utc(tmp_path) -> None:
    """R7 P2 fix (Codex PR #346 round 7 finding #2, `[UNDERSPEC-CAL-D79]`):
    `holdout_unseal.event_time_utc` did not exist before v1.1 -- `campaign.
    unseal.unseal_campaign()` only started stamping it once D88(a) needed a
    local unseal-side timestamp to bound Gate 3's `approved_at_utc` against.
    A pre-v1.1 ledger's `holdout_unseal` payload therefore lacks the field
    entirely (not merely an unparseable value -- see the malformed-value
    boundary test below, still fail-closed) -- this is exactly the shape of
    the real closed campaign `RUN10-CAL-20260904-862dec28`'s ledger. Before
    this fix, a genuinely missing field was treated identically to an
    unparseable one and failed closed unconditionally, falsely invalidating
    replay/audit of every campaign closed before v1.1 even when its
    `freeze_time < gate3_time` ordering genuinely holds. The fix falls back
    to the pre-v1.1 lower-bound-only check (no local unseal-side clock
    reading exists to bound the upper side against) instead of
    unconditionally rejecting."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T10:00:00+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-04T10:30:00+00:00",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            # no event_time_utc -- legacy pre-v1.1 shape.
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) == unseal.seq


def test_replay_verifier_rejects_legacy_holdout_unseal_when_gate3_predates_freeze(
    tmp_path,
) -> None:
    """Companion to the acceptance test above: the legacy fallback still
    enforces the lower-bound ordering -- a `gate3_accepted` that predates
    `c0_freeze` remains rejected even when `holdout_unseal.event_time_utc`
    is entirely absent (the legacy fallback narrows the check, it does not
    disable it)."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T10:00:00+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            # predates the c0_freeze event above.
            "approved_at_utc": "2026-09-04T09:00:00+00:00",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            # no event_time_utc.
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) is None


def test_replay_verifier_rejects_holdout_unseal_malformed_event_time_utc(tmp_path) -> None:
    """Distinguishes a genuinely MISSING `event_time_utc` (legacy fallback,
    see the acceptance test above) from a PRESENT but unparseable one --
    the latter must still fail closed exactly as before this fix (the
    legacy fallback engages only on the field's literal absence, per
    `payload.get(...) is None`, not on any falsy/invalid value)."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T10:00:00+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-04T10:30:00+00:00",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            "event_time_utc": "not-a-timestamp",
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) is None


def test_replay_verifier_accepts_real_862dec28_campaign_shape(tmp_path) -> None:
    """R7 P2 fix regression pin: mirrors the exact `c0_freeze.event_time_
    utc`, `gate3_accepted.approved_at_utc`, and the field-for-field absence
    of `holdout_unseal.event_time_utc` found in the real closed campaign
    `RUN10-CAL-20260904-862dec28`'s own ledger (`campaigns/RUN10-CAL-
    20260904-862dec28/ledger.jsonl`, verified via `grep` for the `c0_
    freeze`/`gate3_accepted`/`holdout_unseal` rows) -- its replay must
    verify as valid rather than fail closed on a check introduced after
    this campaign closed."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "c0_freeze", "event_time_utc": "2026-09-04T08:19:55.278186+00:00"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-04T08:20:24Z",
        }
    ).entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            # no event_time_utc -- the real campaign's ledger row has none.
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) == unseal.seq


def test_replay_verifier_ignores_ordering_check_when_entry_zero_is_not_c0_freeze(
    tmp_path,
) -> None:
    """The D88(a) ordering check only engages when `ledger_entries[0]` is
    itself a `c0_freeze` event (see `_c0_freeze_ordering_violation()`
    docstring) -- a ledger that does not start with one (every other test
    in this file) is unaffected, exactly as before this fix. This directly
    exercises the same guard `test_prior_prerequisite_events_allow_normal_
    unseal_path` (no `c0_freeze` at all) already relies on implicitly, this
    time with a non-`c0_freeze` entry occupying position 0 instead."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "meter_call", "row_id": "unrelated"})
    commitments = _append_prerequisites(ledger)
    gate3_sha = _append_gate3_accepted(ledger)
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3_sha,
            # no event_time_utc either -- must not matter since the check
            # does not engage without a c0_freeze entry 0.
        }
    )

    assert _verified_holdout_unseal_seq(ledger.entries) == unseal.seq


def test_unseal_gate3_accepted_sha_referencing_declined_gate3_fails_closed(tmp_path) -> None:
    """A `gate3_accepted` event whose own payload declares
    `seal_protection_level_accepted: False` is chain-valid and precedes the
    unseal event, but cannot authorize unseal -- `unseal_campaign` (the sole
    legitimate emitter) never appends such an event (it raises `UnsealError`
    first), so this row can only be a crafted/legacy forgery."""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = _append_prerequisites(ledger)
    declined_gate3 = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": False,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-02T00:00:00Z",
        }
    )
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": declined_gate3.entry_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.reason == "UNSEAL_GATE3_UNVERIFIED"
