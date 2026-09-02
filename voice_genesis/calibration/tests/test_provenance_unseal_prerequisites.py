import pytest

from voice_genesis.calibration.provenance import Ledger
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
