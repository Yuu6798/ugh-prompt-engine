from voice_genesis.calibration.provenance import Ledger
from voice_genesis.calibration.vocab import BlockedCode


_PREREQUISITES = (
    ("baseline_audit_sha", "baseline_audit"),
    ("candidate_space_sha", "candidate_space"),
    ("selection_rule_sha", "selection_rule"),
    ("selected_candidate_sha", "selected_candidate"),
)


def _append_prerequisites(ledger: Ledger) -> dict[str, str]:
    refs: dict[str, str] = {}
    for key, kind in _PREREQUISITES:
        refs[key] = ledger.append({"kind": kind}).entry_sha
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

    result = Ledger.check_leakage(
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

    result = Ledger.check_leakage(
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

    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
