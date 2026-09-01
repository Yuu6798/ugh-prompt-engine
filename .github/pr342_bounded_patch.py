from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected patch anchor not found: {path}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) C0 commit pin must identify a full SHA-1 object id for this repository.
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    '''        if key == "repo.dirty_tree" and value is not False:\n            missing.append(f"{key} (must be exactly false, got {value!r})")\n            continue\n        container_kind = _CONTAINER_TYPE_KEYS.get(key)\n''',
    '''        if key == "repo.dirty_tree" and value is not False:\n            missing.append(f"{key} (must be exactly false, got {value!r})")\n            continue\n        if key == "repo.commit_sha" and (\n            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None\n        ):\n            missing.append(\n                f"{key}: shape (must be a full 40-character lowercase hex commit SHA, "\n                f"got {value!r})"\n            )\n            continue\n        container_kind = _CONTAINER_TYPE_KEYS.get(key)\n''',
)

# 2) Determinism evidence must be bound to the canonical fixture identity.
replace_once(
    "voice_genesis/calibration/fixtures/determinism.py",
    '''from dataclasses import dataclass\n\nfrom voice_genesis.calibration.fixtures.generators import common, render_row\n''',
    '''from dataclasses import dataclass\n\nfrom voice_genesis.calibration.canonical import row_id as compute_row_id\nfrom voice_genesis.calibration.fixtures.generators import common, render_row\n''',
)
replace_once(
    "voice_genesis/calibration/fixtures/determinism.py",
    '''    row_dict = json.loads(row_canonical_json)\n    row = _row_from_canonical_dict(row_dict)\n    secret = bytes.fromhex(secret_hex)\n''',
    '''    row_dict = json.loads(row_canonical_json)\n    row = _row_from_canonical_dict(row_dict)\n    canonical_row_id = compute_row_id(row.to_canonical_dict())\n    if row_id != canonical_row_id:\n        raise ValueError(\n            f"determinism: supplied row_id {row_id!r} does not match canonical "\n            f"row_id {canonical_row_id!r}"\n        )\n    if family != row.family:\n        raise ValueError(\n            f"determinism: supplied family {family!r} does not match row family "\n            f"{row.family!r}"\n        )\n    secret = bytes.fromhex(secret_hex)\n''',
)

# 3) A failed across-ceiling selection must retain the candidate audit trail.
replace_once(
    "voice_genesis/calibration/selection.py",
    '''    return select([], SelectionFamily.ABSOLUTE)\n''',
    '''    absolute_audit = select(absolute_pool, SelectionFamily.ABSOLUTE)\n    directional_audit = select(directional_pool, SelectionFamily.DIRECTIONAL)\n\n    raw_vectors = dict(absolute_audit.raw_vectors)\n    raw_vectors.update(directional_audit.raw_vectors)\n    rounded_vectors = dict(absolute_audit.rounded_vectors)\n    rounded_vectors.update(directional_audit.rounded_vectors)\n\n    ineligible = list(absolute_audit.ineligible_candidates)\n    ineligible.extend(directional_audit.ineligible_candidates)\n    accounted_ids = {candidate_id for candidate_id, _reason in ineligible}\n    accounted_ids.update(raw_vectors)\n    for candidate in candidates:\n        if candidate.candidate_id not in accounted_ids:\n            ineligible.append((candidate.candidate_id, "different_ceiling_pool"))\n\n    return SelectionOutcome(\n        family=SelectionFamily.ABSOLUTE,\n        selected_candidate_id=None,\n        ranked_candidate_ids=(),\n        raw_vectors=raw_vectors,\n        rounded_vectors=rounded_vectors,\n        outcome="SELECTION_FAILED_CLOSED",\n        ineligible_candidates=tuple(ineligible),\n    )\n''',
)

# Regression: malformed commit pins.
p = Path("voice_genesis/calibration/tests/test_c0_validate.py")
text = p.read_text(encoding="utf-8")
if "def test_repo_commit_sha_requires_full_lowercase_hex()" not in text:
    text += '''\n\ndef test_repo_commit_sha_requires_full_lowercase_hex() -> None:\n    for bad in ("x", "a" * 39, "A" * 40, "g" * 40):\n        manifest = _complete_manifest()\n        repo = manifest["repo"]\n        assert isinstance(repo, dict)\n        repo["commit_sha"] = bad\n        result = c0_validate.validate_c0_manifest(manifest)\n        assert result.is_blocked\n        assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes\n        assert any("repo.commit_sha" in item for item in result.missing_required_keys)\n'''
    p.write_text(text, encoding="utf-8")

# Regression: canonical row/family binding, and update old arbitrary IDs.
p = Path("voice_genesis/calibration/tests/test_determinism.py")
text = p.read_text(encoding="utf-8")
if "from voice_genesis.calibration.canonical import row_id as compute_row_id\n" not in text:
    text = text.replace(
        "import pytest\n\n",
        "import pytest\n\nfrom voice_genesis.calibration.canonical import row_id as compute_row_id\n",
        1,
    )
text = text.replace('row_id="row-det-1",', 'row_id=compute_row_id(row.to_canonical_dict()),')
text = text.replace('row_id="row-det-2",', 'row_id=compute_row_id(row.to_canonical_dict()),')
text = text.replace('row_id="row-det-3",', 'row_id=compute_row_id(row.to_canonical_dict()),')
if "def test_determinism_rejects_noncanonical_row_id()" not in text:
    text += '''\n\ndef test_determinism_rejects_noncanonical_row_id() -> None:\n    row = _short_f0_row()\n    with pytest.raises(ValueError, match="canonical row_id"):\n        check_determinism_in_process(\n            row,\n            SECRET,\n            campaign_id="RUN10-CAL",\n            family=row.family,\n            split="CALIBRATION",\n            row_id="0" * 64,\n        )\n\n\ndef test_determinism_rejects_family_mismatch_before_render() -> None:\n    row = _short_f0_row()\n    with pytest.raises(ValueError, match="does not match row family"):\n        check_determinism_in_process(\n            row,\n            SECRET,\n            campaign_id="RUN10-CAL",\n            family="APERIODICITY_GT",\n            split="CALIBRATION",\n            row_id=compute_row_id(row.to_canonical_dict()),\n        )\n'''
p.write_text(text, encoding="utf-8")

# Regression: total failure retains audit evidence for every candidate.
p = Path("voice_genesis/calibration/tests/test_selection.py")
text = p.read_text(encoding="utf-8")
if "def test_select_across_ceilings_total_failure_preserves_audit_data()" not in text:
    text += '''\n\ndef test_select_across_ceilings_total_failure_preserves_audit_data() -> None:\n    absolute = CandidateCriteria(\n        candidate_id="abs-flagged",\n        ceiling=ClaimCeiling.ABSOLUTE,\n        eligible=False,\n        primary_normalized_mae=0.1,\n        signed_bias=0.0,\n        primary_q95_ae=0.2,\n    )\n    directional = CandidateCriteria(\n        candidate_id="dir-flagged",\n        ceiling=ClaimCeiling.DIRECTIONAL,\n        eligible=False,\n        kendall_tau=0.9,\n        adjacent_reversal_rate=0.0,\n    )\n    diagnostic = CandidateCriteria(\n        candidate_id="diag-only",\n        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,\n    )\n    outcome = select_across_ceilings([absolute, directional, diagnostic])\n    assert outcome.outcome == "SELECTION_FAILED_CLOSED"\n    assert outcome.selected_candidate_id is None\n    assert outcome.ranked_candidate_ids == ()\n    assert set(outcome.raw_vectors) == {"abs-flagged", "dir-flagged"}\n    assert set(outcome.rounded_vectors) == {"abs-flagged", "dir-flagged"}\n    assert set(outcome.ineligible_candidates) == {\n        ("abs-flagged", "flagged_ineligible"),\n        ("dir-flagged", "flagged_ineligible"),\n        ("diag-only", "different_ceiling_pool"),\n    }\n\n\ndef test_select_across_ceilings_total_failure_accounts_for_criteria_absent_candidates() -> None:\n    absolute_missing = CandidateCriteria(\n        candidate_id="abs-no-criteria",\n        ceiling=ClaimCeiling.ABSOLUTE,\n    )\n    diagnostic = CandidateCriteria(\n        candidate_id="diag-only",\n        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,\n    )\n    outcome = select_across_ceilings([absolute_missing, diagnostic])\n    assert outcome.outcome == "SELECTION_FAILED_CLOSED"\n    assert set(outcome.ineligible_candidates) == {\n        ("abs-no-criteria", "criteria_payload_absent"),\n        ("diag-only", "different_ceiling_pool"),\n    }\n'''
    p.write_text(text, encoding="utf-8")
