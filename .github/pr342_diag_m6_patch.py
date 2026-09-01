from pathlib import Path

SELECTION = Path("voice_genesis/calibration/selection.py")
TEST_SELECTION = Path("voice_genesis/calibration/tests/test_selection.py")
M6 = Path("voice_genesis/calibration/m6_identity.py")
TEST_M6 = Path("voice_genesis/calibration/tests/test_m6.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {text.count(old)}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# selection: preserve DIAGNOSTIC_ONLY audit data without changing ranking.
# ---------------------------------------------------------------------------
selection = SELECTION.read_text()
marker = "\n\ndef select_across_ceilings(candidates: Sequence[CandidateCriteria]) -> SelectionOutcome:\n"
helper = '''\n\ndef _merge_diagnostic_pool_audit(\n    outcome: SelectionOutcome, pool: Sequence[CandidateCriteria]\n) -> SelectionOutcome:\n    \"\"\"DIAGNOSTIC_ONLY candidates are audit-only: preserve any canonical\n    criterion vector that is actually present, but never add them to ranking.\n\n    CandidateCriteria does not carry a separate selection-family tag, so the\n    existing payload shape is the only non-speculative discriminator. A complete\n    ABSOLUTE payload is preferred; otherwise a complete DIRECTIONAL payload is\n    used. Candidates with neither payload remain accounted as\n    criteria_payload_absent.\n    \"\"\"\n    absolute_pool = [\n        c for c in pool if _criteria_payload_present(c, SelectionFamily.ABSOLUTE)\n    ]\n    absolute_ids = {c.candidate_id for c in absolute_pool}\n    directional_pool = [\n        c\n        for c in pool\n        if c.candidate_id not in absolute_ids\n        and _criteria_payload_present(c, SelectionFamily.DIRECTIONAL)\n    ]\n    audited_ids = absolute_ids | {c.candidate_id for c in directional_pool}\n\n    raw_vectors = dict(outcome.raw_vectors)\n    rounded_vectors = dict(outcome.rounded_vectors)\n    reasons = list(outcome.ineligible_candidates)\n\n    for audit_pool, family in (\n        (absolute_pool, SelectionFamily.ABSOLUTE),\n        (directional_pool, SelectionFamily.DIRECTIONAL),\n    ):\n        if not audit_pool:\n            continue\n        audit = select(audit_pool, family)\n        raw_vectors.update(audit.raw_vectors)\n        rounded_vectors.update(audit.rounded_vectors)\n        canonical_reasons = dict(audit.ineligible_candidates)\n        reasons.extend(\n            (\n                c.candidate_id,\n                canonical_reasons.get(c.candidate_id, \"different_ceiling_pool\"),\n            )\n            for c in audit_pool\n        )\n\n    reasons.extend(\n        (c.candidate_id, \"criteria_payload_absent\")\n        for c in pool\n        if c.candidate_id not in audited_ids\n    )\n    return replace(\n        outcome,\n        raw_vectors=raw_vectors,\n        rounded_vectors=rounded_vectors,\n        ineligible_candidates=tuple(reasons),\n    )\n'''
selection = replace_once(selection, marker, helper + marker, "selection helper insertion")
selection = replace_once(
    selection,
    "    absolute_pool = _pool(ClaimCeiling.ABSOLUTE)\n    directional_pool = _pool(ClaimCeiling.DIRECTIONAL)\n",
    "    absolute_pool = _pool(ClaimCeiling.ABSOLUTE)\n    directional_pool = _pool(ClaimCeiling.DIRECTIONAL)\n    diagnostic_pool = _pool(ClaimCeiling.DIAGNOSTIC_ONLY)\n",
    "diagnostic pool declaration",
)
selection = replace_once(
    selection,
    """    if _has_selectable(absolute_pool, SelectionFamily.ABSOLUTE):\n        outcome = select(absolute_pool, SelectionFamily.ABSOLUTE)\n        return _merge_unranked_pool_audit(\n            outcome, directional_pool, SelectionFamily.DIRECTIONAL\n        )\n\n    if _has_selectable(directional_pool, SelectionFamily.DIRECTIONAL):\n        outcome = select(directional_pool, SelectionFamily.DIRECTIONAL)\n        return _merge_unranked_pool_audit(\n            outcome, absolute_pool, SelectionFamily.ABSOLUTE\n        )\n""",
    """    if _has_selectable(absolute_pool, SelectionFamily.ABSOLUTE):\n        outcome = select(absolute_pool, SelectionFamily.ABSOLUTE)\n        outcome = _merge_unranked_pool_audit(\n            outcome, directional_pool, SelectionFamily.DIRECTIONAL\n        )\n        return _merge_diagnostic_pool_audit(outcome, diagnostic_pool)\n\n    if _has_selectable(directional_pool, SelectionFamily.DIRECTIONAL):\n        outcome = select(directional_pool, SelectionFamily.DIRECTIONAL)\n        outcome = _merge_unranked_pool_audit(\n            outcome, absolute_pool, SelectionFamily.ABSOLUTE\n        )\n        return _merge_diagnostic_pool_audit(outcome, diagnostic_pool)\n""",
    "successful selection audit merge",
)
SELECTION.write_text(selection)

# Add regression tests for successful audits. Use one diagnostic with ABSOLUTE
# criteria and one with DIRECTIONAL criteria so both canonical shapes are covered.
test_selection = TEST_SELECTION.read_text()
if "test_successful_selection_preserves_diagnostic_only_audit_vectors" in test_selection:
    raise RuntimeError("selection regression already present")
test_selection += '''\n\ndef test_successful_selection_preserves_diagnostic_only_audit_vectors() -> None:\n    absolute = CandidateCriteria(\n        candidate_id=\"abs-selected\",\n        ceiling=ClaimCeiling.ABSOLUTE,\n        primary_normalized_mae=0.2,\n        signed_bias=0.0,\n        primary_q95_ae=0.3,\n    )\n    diagnostic_absolute = CandidateCriteria(\n        candidate_id=\"diag-abs\",\n        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,\n        primary_normalized_mae=0.4,\n        signed_bias=0.1,\n        primary_q95_ae=0.6,\n    )\n    diagnostic_directional = CandidateCriteria(\n        candidate_id=\"diag-dir\",\n        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,\n        kendall_tau=0.7,\n        adjacent_reversal_rate=0.1,\n    )\n\n    outcome = select_across_ceilings(\n        [absolute, diagnostic_absolute, diagnostic_directional]\n    )\n\n    assert outcome.selected_candidate_id == \"abs-selected\"\n    assert outcome.ranked_candidate_ids == (\"abs-selected\",)\n    assert \"diag-abs\" in outcome.raw_vectors\n    assert \"diag-abs\" in outcome.rounded_vectors\n    assert \"diag-dir\" in outcome.raw_vectors\n    assert \"diag-dir\" in outcome.rounded_vectors\n    assert (\"diag-abs\", \"different_ceiling_pool\") in outcome.ineligible_candidates\n    assert (\"diag-dir\", \"different_ceiling_pool\") in outcome.ineligible_candidates\n\n\ndef test_successful_selection_accounts_for_diagnostic_without_criteria() -> None:\n    directional = CandidateCriteria(\n        candidate_id=\"dir-selected\",\n        ceiling=ClaimCeiling.DIRECTIONAL,\n        kendall_tau=0.9,\n        adjacent_reversal_rate=0.0,\n    )\n    diagnostic = CandidateCriteria(\n        candidate_id=\"diag-no-criteria\",\n        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,\n    )\n\n    outcome = select_across_ceilings([directional, diagnostic])\n\n    assert outcome.selected_candidate_id == \"dir-selected\"\n    assert \"diag-no-criteria\" not in outcome.raw_vectors\n    assert (\n        \"diag-no-criteria\",\n        \"criteria_payload_absent\",\n    ) in outcome.ineligible_candidates\n'''
TEST_SELECTION.write_text(test_selection)

# ---------------------------------------------------------------------------
# M6: validate critical component and normalization domains before division.
# ---------------------------------------------------------------------------
m6 = M6.read_text()
old = '''    critical_ids = sorted(CLAIM_CRITICAL_SET, key=lambda m: m.value)\n    contributions: list[ComponentContribution] = []\n    normalized_diffs: list[float] = []\n    for cid in critical_ids:\n        diff_norm = (components_a[cid] - components_b[cid]) / e_use[cid]\n'''
new = '''    critical_ids = sorted(CLAIM_CRITICAL_SET, key=lambda m: m.value)\n    valid_operands = all(\n        math.isfinite(components_a[cid])\n        and math.isfinite(components_b[cid])\n        and math.isfinite(e_use[cid])\n        and e_use[cid] > 0\n        for cid in critical_ids\n    )\n    if not valid_operands:\n        return M6Result(status=TerminalStatus.NOT_EVALUABLE, distance=None, components=())\n\n    contributions: list[ComponentContribution] = []\n    normalized_diffs: list[float] = []\n    for cid in critical_ids:\n        diff_norm = (components_a[cid] - components_b[cid]) / e_use[cid]\n'''
m6 = replace_once(m6, old, new, "M6 operand validation")
M6.write_text(m6)

test_m6 = TEST_M6.read_text()
if "test_m6_distance_invalid_normalization_operands_are_not_evaluable" in test_m6:
    raise RuntimeError("M6 regression already present")
test_m6 += '''\n\n@pytest.mark.parametrize(\n    (\"field\", \"bad_value\"),\n    [\n        (\"a\", float(\"nan\")),\n        (\"a\", float(\"inf\")),\n        (\"b\", float(\"-inf\")),\n        (\"e_use\", 0.0),\n        (\"e_use\", -1.0),\n        (\"e_use\", float(\"nan\")),\n        (\"e_use\", float(\"inf\")),\n    ],\n)\ndef test_m6_distance_invalid_normalization_operands_are_not_evaluable(\n    field: str, bad_value: float\n) -> None:\n    components_a = {m: 1.0 for m in CLAIM_CRITICAL_SET}\n    components_b = {m: 2.0 for m in CLAIM_CRITICAL_SET}\n    e_use = {m: 1.0 for m in CLAIM_CRITICAL_SET}\n    target = next(iter(CLAIM_CRITICAL_SET))\n\n    if field == \"a\":\n        components_a[target] = bad_value\n    elif field == \"b\":\n        components_b[target] = bad_value\n    else:\n        e_use[target] = bad_value\n\n    result = m6_distance(\n        components_a, components_b, e_use, member_status=_ALL_ABSOLUTE, norm=\"L1\"\n    )\n    assert result.status == TerminalStatus.NOT_EVALUABLE\n    assert result.distance is None\n    assert result.components == ()\n'''
TEST_M6.write_text(test_m6)
