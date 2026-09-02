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
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
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
    wrong = ledger.append({"kind": "meter_call", "row_id": "selection-row"})
    commitments["selection_rule_sha"] = wrong.entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
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
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
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
    malformed = ledger.append({"kind": "selected_candidate", "artifact_sha": "e" * 64})
    commitments["selected_candidate_sha"] = malformed.entry_sha
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})

    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=unseal.seq,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
