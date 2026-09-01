from pathlib import Path

selection_path = Path("voice_genesis/calibration/selection.py")
test_path = Path("voice_genesis/calibration/tests/test_selection.py")

selection = selection_path.read_text()
marker = '''def select_across_ceilings(candidates: Sequence[CandidateCriteria]) -> SelectionOutcome:\n'''
helper = '''def _merge_invalid_ceiling_pool_audit(\n    outcome: SelectionOutcome, pool: Sequence[CandidateCriteria]\n) -> SelectionOutcome:\n    \"\"\"Account for candidates whose ceiling is unset or outside the frozen vocabulary.\n\n    Such candidates never enter ranking, but any inferable canonical criterion vector is\n    preserved for audit and a ceiling-specific ineligibility reason is recorded.\n    \"\"\"\n    if not pool:\n        return outcome\n\n    audited = _merge_diagnostic_pool_audit(outcome, pool)\n    invalid_ids = {c.candidate_id for c in pool}\n    reasons = [\n        (candidate_id, reason)\n        for candidate_id, reason in audited.ineligible_candidates\n        if candidate_id not in invalid_ids\n    ]\n    reasons.extend(\n        (\n            c.candidate_id,\n            \"ceiling_unset\" if c.ceiling is None else \"ceiling_unknown\",\n        )\n        for c in pool\n    )\n    return replace(audited, ineligible_candidates=tuple(reasons))\n\n\n'''
if helper not in selection:
    if marker not in selection:
        raise SystemExit("select_across_ceilings marker not found")
    selection = selection.replace(marker, helper + marker, 1)

old_doc = '''    `ceiling == DIAGNOSTIC_ONLY` / `NONE` の候補は、たとえ `eligible=True` でも\n    **いかなる場合も選抜対象に入らない**。ただし §9 の全候補監査要件に従い、\n    保持可能な criterion vector と除外理由は audit-only として必ず残す。\n'''
new_doc = '''    `ceiling == DIAGNOSTIC_ONLY` / `NONE` の候補は、たとえ `eligible=True` でも\n    **いかなる場合も選抜対象に入らない**。ただし §9 の全候補監査要件に従い、\n    保持可能な criterion vector と除外理由は audit-only として必ず残す。\n    ceiling が未設定 (`None`) または frozen `ClaimCeiling` 語彙外でも候補を黙って\n    落とさず、ranking から除外したまま `ceiling_unset` / `ceiling_unknown` として\n    audit-only accounting する。\n'''
if old_doc in selection:
    selection = selection.replace(old_doc, new_doc, 1)
elif new_doc not in selection:
    raise SystemExit("ceiling audit doc marker not found")

old_pools = '''    absolute_pool = _pool(ClaimCeiling.ABSOLUTE)\n    directional_pool = _pool(ClaimCeiling.DIRECTIONAL)\n    diagnostic_pool = _pool(ClaimCeiling.DIAGNOSTIC_ONLY)\n    none_pool = _pool(ClaimCeiling.NONE)\n'''
new_pools = '''    absolute_pool = _pool(ClaimCeiling.ABSOLUTE)\n    directional_pool = _pool(ClaimCeiling.DIRECTIONAL)\n    diagnostic_pool = _pool(ClaimCeiling.DIAGNOSTIC_ONLY)\n    none_pool = _pool(ClaimCeiling.NONE)\n    valid_ceilings = tuple(ClaimCeiling)\n    invalid_ceiling_pool = [c for c in candidates if c.ceiling not in valid_ceilings]\n'''
if old_pools in selection:
    selection = selection.replace(old_pools, new_pools, 1)
elif new_pools not in selection:
    raise SystemExit("pool partition marker not found")

old_abs_return = '''        outcome = _merge_diagnostic_pool_audit(outcome, diagnostic_pool)\n        return _merge_diagnostic_pool_audit(outcome, none_pool)\n'''
new_abs_return = '''        outcome = _merge_diagnostic_pool_audit(outcome, diagnostic_pool)\n        outcome = _merge_diagnostic_pool_audit(outcome, none_pool)\n        return _merge_invalid_ceiling_pool_audit(outcome, invalid_ceiling_pool)\n'''
# Occurs in both ABSOLUTE and DIRECTIONAL success branches.
count = selection.count(old_abs_return)
if count:
    selection = selection.replace(old_abs_return, new_abs_return)
elif selection.count(new_abs_return) != 2:
    raise SystemExit("success return marker not found")

old_fail_return = '''    failed = _merge_diagnostic_pool_audit(failed, diagnostic_pool)\n    return _merge_diagnostic_pool_audit(failed, none_pool)\n'''
new_fail_return = '''    failed = _merge_diagnostic_pool_audit(failed, diagnostic_pool)\n    failed = _merge_diagnostic_pool_audit(failed, none_pool)\n    return _merge_invalid_ceiling_pool_audit(failed, invalid_ceiling_pool)\n'''
if old_fail_return in selection:
    selection = selection.replace(old_fail_return, new_fail_return, 1)
elif new_fail_return not in selection:
    raise SystemExit("failure return marker not found")

selection_path.write_text(selection)

tests = test_path.read_text()
regressions = '''\n\ndef test_select_across_ceilings_accounts_for_unset_ceiling_in_successful_audit() -> None:\n    selected = CandidateCriteria(\n        candidate_id=\"abs-selected\",\n        ceiling=ClaimCeiling.ABSOLUTE,\n        primary_normalized_mae=0.2,\n        signed_bias=0.0,\n        primary_q95_ae=0.3,\n    )\n    unset = CandidateCriteria(\n        candidate_id=\"unset-ceiling\",\n        primary_normalized_mae=0.4,\n        signed_bias=0.1,\n        primary_q95_ae=0.5,\n    )\n\n    outcome = select_across_ceilings([selected, unset])\n\n    assert outcome.selected_candidate_id == \"abs-selected\"\n    assert outcome.ranked_candidate_ids == (\"abs-selected\",)\n    assert \"unset-ceiling\" in outcome.raw_vectors\n    assert \"unset-ceiling\" in outcome.rounded_vectors\n    assert (\"unset-ceiling\", \"ceiling_unset\") in outcome.ineligible_candidates\n\n\ndef test_select_across_ceilings_accounts_for_unknown_ceiling_in_successful_audit() -> None:\n    selected = CandidateCriteria(\n        candidate_id=\"dir-selected\",\n        ceiling=ClaimCeiling.DIRECTIONAL,\n        kendall_tau=0.9,\n        adjacent_reversal_rate=0.0,\n    )\n    unknown = CandidateCriteria(\n        candidate_id=\"unknown-ceiling\",\n        ceiling=\"NOT_FROZEN\",  # type: ignore[arg-type]\n        kendall_tau=0.7,\n        adjacent_reversal_rate=0.1,\n    )\n\n    outcome = select_across_ceilings([selected, unknown])\n\n    assert outcome.selected_candidate_id == \"dir-selected\"\n    assert outcome.ranked_candidate_ids == (\"dir-selected\",)\n    assert \"unknown-ceiling\" in outcome.raw_vectors\n    assert \"unknown-ceiling\" in outcome.rounded_vectors\n    assert (\"unknown-ceiling\", \"ceiling_unknown\") in outcome.ineligible_candidates\n'''
if "test_select_across_ceilings_accounts_for_unset_ceiling_in_successful_audit" not in tests:
    tests = tests.rstrip() + regressions + "\n"
    test_path.write_text(tests)
