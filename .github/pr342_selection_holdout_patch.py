from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"anchor missing: {path}: {old[:100]!r}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, block: str) -> None:
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    if marker in s:
        return
    p.write_text(s.rstrip() + "\n\n\n" + block.strip() + "\n", encoding="utf-8")


# selection: finite is not enough; enforce mathematical domains.
replace_once(
    "voice_genesis/calibration/selection.py",
    '''def _has_required_criteria(criteria: CandidateCriteria, family: SelectionFamily) -> bool:\n    \"\"\"Ranking inputs must be both present and finite.\"\"\"\n    return _criteria_payload_present(criteria, family) and _criteria_values_finite(criteria, family)\n\n\ndef _ineligibility_reason(''',
    '''def _criteria_values_in_domain(criteria: CandidateCriteria, family: SelectionFamily) -> bool:\n    \"\"\"Reject finite-but-impossible ranking inputs before they can affect ordering.\"\"\"\n    if not _criteria_values_finite(criteria, family):\n        return False\n    if criteria.complexity_rank < 0:\n        return False\n    if criteria.nuisance_sensitivity_max < 0:\n        return False\n    if not 0.0 <= criteria.missing_failure_rate <= 1.0:\n        return False\n    if family is SelectionFamily.ABSOLUTE:\n        assert criteria.primary_normalized_mae is not None\n        assert criteria.primary_q95_ae is not None\n        return criteria.primary_normalized_mae >= 0.0 and criteria.primary_q95_ae >= 0.0\n    assert criteria.kendall_tau is not None\n    assert criteria.adjacent_reversal_rate is not None\n    return (\n        -1.0 <= criteria.kendall_tau <= 1.0\n        and 0.0 <= criteria.adjacent_reversal_rate <= 1.0\n    )\n\n\ndef _has_required_criteria(criteria: CandidateCriteria, family: SelectionFamily) -> bool:\n    \"\"\"Ranking inputs must be present, finite, and inside their mathematical domains.\"\"\"\n    return (\n        _criteria_payload_present(criteria, family)\n        and _criteria_values_finite(criteria, family)\n        and _criteria_values_in_domain(criteria, family)\n    )\n\n\ndef _ineligibility_reason(''',
)
replace_once(
    "voice_genesis/calibration/selection.py",
    '''    if not has_criteria:\n        return \"criteria_non_finite\"\n    if not criteria.eligible:''',
    '''    if not _criteria_values_finite(criteria, family):\n        return \"criteria_non_finite\"\n    if not _criteria_values_in_domain(criteria, family):\n        return \"criteria_out_of_domain\"\n    if not has_criteria:\n        return \"criteria_out_of_domain\"\n    if not criteria.eligible:''',
)
replace_once(
    "voice_genesis/calibration/selection.py",
    '''    if not _criteria_values_finite(criteria, family):\n        raise ValueError(\n            f\"selection: candidate {criteria.candidate_id!r} has non-finite ranking criteria\"\n        )\n    err = round_error if rounded else (lambda v: v)''',
    '''    if not _criteria_values_finite(criteria, family):\n        raise ValueError(\n            f\"selection: candidate {criteria.candidate_id!r} has non-finite ranking criteria\"\n        )\n    if not _criteria_values_in_domain(criteria, family):\n        raise ValueError(\n            f\"selection: candidate {criteria.candidate_id!r} has out-of-domain ranking criteria\"\n        )\n    err = round_error if rounded else (lambda v: v)''',
)
replace_once(
    "voice_genesis/calibration/selection.py",
    '''    return SelectionOutcome(\n        family=SelectionFamily.ABSOLUTE,\n        selected_candidate_id=None,\n        ranked_candidate_ids=(),\n        raw_vectors=raw_vectors,\n        rounded_vectors=rounded_vectors,\n        outcome=\"SELECTION_FAILED_CLOSED\",\n        ineligible_candidates=tuple(ineligible),\n    )''',
    '''    failed = SelectionOutcome(\n        family=SelectionFamily.ABSOLUTE,\n        selected_candidate_id=None,\n        ranked_candidate_ids=(),\n        raw_vectors=raw_vectors,\n        rounded_vectors=rounded_vectors,\n        outcome=\"SELECTION_FAILED_CLOSED\",\n        ineligible_candidates=tuple(ineligible),\n    )\n    return _merge_diagnostic_pool_audit(failed, diagnostic_pool)''',
)
append_once(
    "voice_genesis/calibration/tests/test_selection.py",
    "test_selection_rejects_out_of_domain_ranking_criteria",
    '''def test_selection_rejects_out_of_domain_ranking_criteria() -> None:\n    valid = CandidateCriteria(\n        candidate_id=\"valid\",\n        kendall_tau=0.9,\n        adjacent_reversal_rate=0.0,\n    )\n    impossible_tau = CandidateCriteria(\n        candidate_id=\"impossible-tau\",\n        kendall_tau=2.0,\n        adjacent_reversal_rate=0.0,\n    )\n    negative_rate = CandidateCriteria(\n        candidate_id=\"negative-rate\",\n        kendall_tau=0.95,\n        adjacent_reversal_rate=-0.1,\n    )\n    outcome = select([impossible_tau, negative_rate, valid], SelectionFamily.DIRECTIONAL)\n    assert outcome.selected_candidate_id == \"valid\"\n    assert (\"impossible-tau\", \"criteria_out_of_domain\") in outcome.ineligible_candidates\n    assert (\"negative-rate\", \"criteria_out_of_domain\") in outcome.ineligible_candidates\n\n    negative_error = CandidateCriteria(\n        candidate_id=\"negative-error\",\n        primary_normalized_mae=-1.0,\n        signed_bias=0.0,\n        primary_q95_ae=0.1,\n    )\n    negative_complexity = CandidateCriteria(\n        candidate_id=\"negative-complexity\",\n        complexity_rank=-1,\n        primary_normalized_mae=0.1,\n        signed_bias=0.0,\n        primary_q95_ae=0.1,\n    )\n    abs_outcome = select([negative_error, negative_complexity], SelectionFamily.ABSOLUTE)\n    assert abs_outcome.outcome == \"SELECTION_FAILED_CLOSED\"\n    assert all(reason == \"criteria_out_of_domain\" for _cid, reason in abs_outcome.ineligible_candidates)\n\n\ndef test_select_across_ceilings_failure_preserves_diagnostic_vectors() -> None:\n    diag_abs = CandidateCriteria(\n        candidate_id=\"diag-abs\",\n        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,\n        primary_normalized_mae=0.2,\n        signed_bias=0.1,\n        primary_q95_ae=0.3,\n    )\n    diag_dir = CandidateCriteria(\n        candidate_id=\"diag-dir\",\n        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,\n        kendall_tau=0.7,\n        adjacent_reversal_rate=0.1,\n    )\n    outcome = select_across_ceilings([diag_abs, diag_dir])\n    assert outcome.outcome == \"SELECTION_FAILED_CLOSED\"\n    assert \"diag-abs\" in outcome.raw_vectors\n    assert \"diag-dir\" in outcome.raw_vectors\n    assert {cid for cid, _reason in outcome.ineligible_candidates} == {\"diag-abs\", \"diag-dir\"}''',
)

# provenance: authenticate the complete holdout set against the realized split map.
replace_once(
    "voice_genesis/calibration/provenance.py",
    '''        control_row_ids: Collection[str] = (),\n    ) -> LeakageCheckResult:''',
    '''        control_row_ids: Collection[str] = (),\n        realized_split_assignment: Mapping[str, object] | None = None,\n    ) -> LeakageCheckResult:''',
)
replace_once(
    "voice_genesis/calibration/provenance.py",
    '''        holdout_set = set(holdout_row_ids)\n        # ``control_row_ids`` is not an authority boundary.''',
    '''        declared_holdout_set = set(holdout_row_ids)\n        if realized_split_assignment is None:\n            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)\n\n        from voice_genesis.calibration.vocab import Split\n\n        authenticated_holdout_set = {\n            row_id\n            for row_id, split in realized_split_assignment.items()\n            if split == Split.HOLDOUT or split == Split.HOLDOUT.value\n        }\n        if declared_holdout_set != authenticated_holdout_set:\n            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)\n        holdout_set = authenticated_holdout_set\n        # ``control_row_ids`` is not an authority boundary.''',
)
replace_once(
    "voice_genesis/calibration/provenance.py",
    '''        `unseal_seq` is retained only as an optional expected-sequence assertion for\n        compatibility; it can never grant access by itself.\n        \"\"\"''',
    '''        `unseal_seq` is retained only as an optional expected-sequence assertion for\n        compatibility; it can never grant access by itself.  The protected row set is\n        authenticated against `realized_split_assignment`; caller-supplied\n        `holdout_row_ids` is only an equality assertion and cannot shrink the seal.\n        \"\"\"''',
)

# Existing tests use a wrapper that authenticates their declared holdout rows. New
# negative tests call Ledger.check_leakage directly to exercise mismatch/missing maps.
p = ROOT / "voice_genesis/calibration/tests/test_provenance.py"
s = p.read_text(encoding="utf-8")
marker = "from voice_genesis.calibration.vocab import BlockedCode\n"
wrapper = '''\n\ndef _check_leakage(*args, **kwargs):\n    from voice_genesis.calibration.vocab import Split\n\n    if \"holdout_row_ids\" in kwargs:\n        holdout = kwargs[\"holdout_row_ids\"]\n    elif len(args) >= 2:\n        holdout = args[1]\n    else:\n        raise AssertionError(\"holdout_row_ids required by test wrapper\")\n    kwargs.setdefault(\n        \"realized_split_assignment\",\n        {row_id: Split.HOLDOUT for row_id in holdout},\n    )\n    return Ledger.check_leakage(*args, **kwargs)\n'''
if "def _check_leakage(*args, **kwargs):" not in s:
    if marker not in s:
        raise SystemExit("test_provenance import marker missing")
    s = s.replace(marker, marker + wrapper, 1)
s = s.replace("Ledger.check_leakage(", "_check_leakage(")
# Restore the wrapper's internal authoritative call.
s = s.replace("return _check_leakage(*args, **kwargs)", "return Ledger.check_leakage(*args, **kwargs)", 1)
p.write_text(s, encoding="utf-8")

append_once(
    "voice_genesis/calibration/tests/test_provenance.py",
    "test_check_leakage_rejects_incomplete_declared_holdout_set",
    '''def test_check_leakage_rejects_incomplete_declared_holdout_set() -> None:\n    from voice_genesis.calibration.vocab import Split\n\n    entries = [\n        LedgerEntry(\n            seq=0,\n            prev_sha=\"0\" * 64,\n            entry_sha=\"a\" * 64,\n            payload={\"kind\": \"render\", \"row_id\": \"holdout-omitted\"},\n        )\n    ]\n    realized = {\n        \"holdout-declared\": Split.HOLDOUT,\n        \"holdout-omitted\": Split.HOLDOUT,\n        \"selection-row\": Split.SELECTION,\n    }\n    result = Ledger.check_leakage(\n        entries,\n        holdout_row_ids=[\"holdout-declared\"],\n        unseal_seq=None,\n        realized_split_assignment=realized,\n    )\n    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE\n\n\ndef test_check_leakage_requires_realized_split_assignment() -> None:\n    result = Ledger.check_leakage(\n        [], holdout_row_ids=[], unseal_seq=None, realized_split_assignment=None\n    )\n    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE''',
)
