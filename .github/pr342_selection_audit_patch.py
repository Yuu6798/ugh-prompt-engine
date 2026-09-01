from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected patch anchor not found: {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "voice_genesis/calibration/selection.py"
insert_anchor = "\n\ndef select_across_ceilings(candidates: Sequence[CandidateCriteria]) -> SelectionOutcome:\n"
helper = '''\n\ndef _merge_unranked_pool_audit(\n    outcome: SelectionOutcome,\n    pool: Sequence[CandidateCriteria],\n    family: SelectionFamily,\n) -> SelectionOutcome:\n    """選抜対象外 ceiling を canonical family で監査し、ranking は変えずに\n    criterion vectors と除外理由だけを outcome へ統合する。"""\n    audit = select(pool, family)\n    raw_vectors = dict(outcome.raw_vectors)\n    raw_vectors.update(audit.raw_vectors)\n    rounded_vectors = dict(outcome.rounded_vectors)\n    rounded_vectors.update(audit.rounded_vectors)\n\n    canonical_reasons = dict(audit.ineligible_candidates)\n    unranked_reasons = tuple(\n        (c.candidate_id, canonical_reasons.get(c.candidate_id, "different_ceiling_pool"))\n        for c in pool\n    )\n    return replace(\n        outcome,\n        raw_vectors=raw_vectors,\n        rounded_vectors=rounded_vectors,\n        ineligible_candidates=outcome.ineligible_candidates + unranked_reasons,\n    )\n\n\ndef select_across_ceilings(candidates: Sequence[CandidateCriteria]) -> SelectionOutcome:\n'''
replace_once(path, insert_anchor, helper)

replace_once(
    path,
    '''    if _has_selectable(absolute_pool, SelectionFamily.ABSOLUTE):\n        outcome = select(absolute_pool, SelectionFamily.ABSOLUTE)\n        other_pool_audit = tuple(\n            (c.candidate_id, _other_pool_audit_reason(c, SelectionFamily.ABSOLUTE))\n            for c in directional_pool\n        )\n        if other_pool_audit:\n            outcome = replace(\n                outcome,\n                ineligible_candidates=outcome.ineligible_candidates + other_pool_audit,\n            )\n        return outcome\n\n''',
    '''    if _has_selectable(absolute_pool, SelectionFamily.ABSOLUTE):\n        outcome = select(absolute_pool, SelectionFamily.ABSOLUTE)\n        return _merge_unranked_pool_audit(\n            outcome, directional_pool, SelectionFamily.DIRECTIONAL\n        )\n\n''',
)

replace_once(
    path,
    '''    if _has_selectable(directional_pool, SelectionFamily.DIRECTIONAL):\n        outcome = select(directional_pool, SelectionFamily.DIRECTIONAL)\n        other_pool_audit = tuple(\n            (c.candidate_id, _other_pool_audit_reason(c, SelectionFamily.DIRECTIONAL))\n            for c in absolute_pool\n        )\n        if other_pool_audit:\n            outcome = replace(\n                outcome,\n                ineligible_candidates=outcome.ineligible_candidates + other_pool_audit,\n            )\n        return outcome\n\n''',
    '''    if _has_selectable(directional_pool, SelectionFamily.DIRECTIONAL):\n        outcome = select(directional_pool, SelectionFamily.DIRECTIONAL)\n        return _merge_unranked_pool_audit(\n            outcome, absolute_pool, SelectionFamily.ABSOLUTE\n        )\n\n''',
)

p = Path("voice_genesis/calibration/tests/test_selection.py")
text = p.read_text(encoding="utf-8")
marker = "def test_successful_absolute_selection_preserves_directional_audit_vectors()"
if marker not in text:
    text += '''\n\ndef test_successful_absolute_selection_preserves_directional_audit_vectors() -> None:\n    absolute = CandidateCriteria(\n        candidate_id="abs-selected",\n        ceiling=ClaimCeiling.ABSOLUTE,\n        primary_normalized_mae=0.2,\n        signed_bias=0.0,\n        primary_q95_ae=0.3,\n    )\n    directional = CandidateCriteria(\n        candidate_id="dir-audited",\n        ceiling=ClaimCeiling.DIRECTIONAL,\n        kendall_tau=0.9,\n        adjacent_reversal_rate=0.0,\n    )\n\n    outcome = select_across_ceilings([absolute, directional])\n\n    assert outcome.family == SelectionFamily.ABSOLUTE\n    assert outcome.selected_candidate_id == "abs-selected"\n    assert outcome.ranked_candidate_ids == ("abs-selected",)\n    assert "dir-audited" in outcome.raw_vectors\n    assert "dir-audited" in outcome.rounded_vectors\n    assert ("dir-audited", "different_ceiling_pool") in outcome.ineligible_candidates\n\n\ndef test_successful_directional_selection_preserves_absolute_audit_vectors() -> None:\n    absolute_flagged = CandidateCriteria(\n        candidate_id="abs-audited",\n        ceiling=ClaimCeiling.ABSOLUTE,\n        eligible=False,\n        primary_normalized_mae=0.2,\n        signed_bias=0.0,\n        primary_q95_ae=0.3,\n    )\n    directional = CandidateCriteria(\n        candidate_id="dir-selected",\n        ceiling=ClaimCeiling.DIRECTIONAL,\n        kendall_tau=0.9,\n        adjacent_reversal_rate=0.0,\n    )\n\n    outcome = select_across_ceilings([absolute_flagged, directional])\n\n    assert outcome.family == SelectionFamily.DIRECTIONAL\n    assert outcome.selected_candidate_id == "dir-selected"\n    assert outcome.ranked_candidate_ids == ("dir-selected",)\n    assert "abs-audited" in outcome.raw_vectors\n    assert "abs-audited" in outcome.rounded_vectors\n    assert ("abs-audited", "flagged_ineligible") in outcome.ineligible_candidates\n'''
    p.write_text(text, encoding="utf-8")
