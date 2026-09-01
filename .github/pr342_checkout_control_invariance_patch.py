from pathlib import Path

ROOT = Path.cwd()


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected patch anchor missing: {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, block: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if marker in text:
        return
    p.write_text(text.rstrip() + "\n\n\n" + block.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# C0: bind manifest commit/dirty pins to the inspected checkout.
# ---------------------------------------------------------------------------
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    "import re\nfrom collections.abc import Mapping, Sequence",
    "import re\nimport subprocess\nfrom collections.abc import Mapping, Sequence",
)
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    "`validate_c0_manifest()` は与えられた manifest mapping を\n読むだけの純関数であり、副作用を持たない。",
    "`validate_c0_manifest()` は manifest と検証対象 checkout の read-only identity\n（Git HEAD / dirty state）を読むが、書込・secret 生成などの副作用は持たない。",
)
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    "_REPO_ROOT = Path(__file__).resolve().parents[2]\n\n#: 版管理されたクローズド inventory ファイル名",
    '''_REPO_ROOT = Path(__file__).resolve().parents[2]\n\n\ndef _inspect_checkout_identity(\n    repo_root: Path | None = None,\n) -> tuple[str | None, bool | None, str | None]:\n    \"\"\"Return ``(HEAD, dirty, error)`` for the checkout being validated.\n\n    This is deliberately read-only.  C0 path hashes are computed from this checkout,\n    so accepting a caller-provided but unrelated commit SHA/dirty flag would make the\n    provenance pin describe different bytes than those actually inspected.  Git\n    inspection failure is returned as an error so the caller can fail closed.\n    \"\"\"\n    root = repo_root if repo_root is not None else _REPO_ROOT\n    try:\n        head = subprocess.run(\n            [\"git\", \"-C\", str(root), \"rev-parse\", \"HEAD\"],\n            check=False,\n            capture_output=True,\n            text=True,\n            timeout=5,\n        )\n        if head.returncode != 0:\n            detail = head.stderr.strip() or head.stdout.strip() or f\"exit {head.returncode}\"\n            return None, None, f\"git rev-parse HEAD failed: {detail}\"\n        head_sha = head.stdout.strip()\n        if re.fullmatch(r\"[0-9a-f]{40}\", head_sha) is None:\n            return None, None, f\"git rev-parse HEAD returned malformed SHA: {head_sha!r}\"\n\n        status = subprocess.run(\n            [\"git\", \"-C\", str(root), \"status\", \"--porcelain\", \"--untracked-files=all\"],\n            check=False,\n            capture_output=True,\n            text=True,\n            timeout=5,\n        )\n        if status.returncode != 0:\n            detail = status.stderr.strip() or status.stdout.strip() or f\"exit {status.returncode}\"\n            return None, None, f\"git status failed: {detail}\"\n        return head_sha, bool(status.stdout.strip()), None\n    except (OSError, subprocess.SubprocessError) as exc:\n        return None, None, f\"git checkout inspection failed: {exc}\"\n\n\ndef _check_checkout_identity(manifest: Mapping[str, object]) -> list[str]:\n    \"\"\"Bind ``repo.commit_sha``/``repo.dirty_tree`` to the bytes being inspected.\"\"\"\n    head_sha, dirty, error = _inspect_checkout_identity()\n    if error is not None or head_sha is None or dirty is None:\n        return [f\"repo.checkout_identity ({error or 'unavailable'})\"]\n\n    violations: list[str] = []\n    found_sha, declared_sha = _resolve(manifest, \"repo.commit_sha\")\n    if (\n        found_sha\n        and isinstance(declared_sha, str)\n        and re.fullmatch(r\"[0-9a-f]{40}\", declared_sha) is not None\n        and declared_sha != head_sha\n    ):\n        violations.append(\n            \"repo.commit_sha (does not match inspected checkout HEAD: \"\n            f\"declared={declared_sha}, actual={head_sha})\"\n        )\n    if dirty:\n        violations.append(\"repo.dirty_tree (inspected checkout is actually dirty)\")\n    return violations\n\n\n#: 版管理されたクローズド inventory ファイル名''',
)
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    "    missing_required = _check_required_blocking(manifest)\n    missing_required += _check_hash_maps(manifest)",
    "    missing_required = _check_required_blocking(manifest)\n    missing_required += _check_checkout_identity(manifest)\n    missing_required += _check_hash_maps(manifest)",
)

# C0 test fixture must pin the real checkout being hashed.
replace_once(
    "voice_genesis/calibration/tests/test_c0_validate.py",
    "def _classify_path(path: str) -> str:",
    '''def _current_checkout_sha() -> str:\n    head_sha, _dirty, error = c0_validate._inspect_checkout_identity()\n    assert error is None\n    assert head_sha is not None\n    return head_sha\n\n\ndef _classify_path(path: str) -> str:''',
)
replace_once(
    "voice_genesis/calibration/tests/test_c0_validate.py",
    '"commit_sha": "a" * 40,',
    '"commit_sha": _current_checkout_sha(),',
)
append_once(
    "voice_genesis/calibration/tests/test_c0_validate.py",
    "test_well_formed_unrelated_commit_sha_blocks",
    '''def test_well_formed_unrelated_commit_sha_blocks() -> None:\n    manifest = _complete_manifest()\n    actual = manifest[\"repo\"][\"commit_sha\"]\n    unrelated = \"0\" * 40 if actual != \"0\" * 40 else \"1\" * 40\n    manifest[\"repo\"][\"commit_sha\"] = unrelated\n\n    result = c0_validate.validate_c0_manifest(manifest)\n\n    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes\n    assert any(\n        key.startswith(\"repo.commit_sha (does not match inspected checkout HEAD\")\n        for key in result.missing_required_keys\n    )\n\n\ndef test_actual_dirty_checkout_blocks_even_if_manifest_claims_clean(monkeypatch) -> None:\n    manifest = _complete_manifest()\n    actual = manifest[\"repo\"][\"commit_sha\"]\n    monkeypatch.setattr(\n        c0_validate,\n        \"_inspect_checkout_identity\",\n        lambda repo_root=None: (actual, True, None),\n    )\n\n    result = c0_validate.validate_c0_manifest(manifest)\n\n    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes\n    assert \"repo.dirty_tree (inspected checkout is actually dirty)\" in result.missing_required_keys''',
)

# ---------------------------------------------------------------------------
# Provenance: caller control IDs are only assertions; authorization comes from
# the committed frozen matrix's negative-control set.
# ---------------------------------------------------------------------------
replace_once(
    "voice_genesis/calibration/provenance.py",
    "        holdout_set = set(holdout_row_ids)\n        control_set = set(control_row_ids)\n        control_excluded_count = 0",
    '''        holdout_set = set(holdout_row_ids)\n        # ``control_row_ids`` is not an authority boundary.  A caller may request\n        # exemption only for rows that the committed frozen matrix independently\n        # identifies as truth-free negative controls.  Truth-bearing/unknown rows\n        # supplied here remain sealed.\n        from voice_genesis.calibration.fixtures.controls import negative_control_row_ids\n        from voice_genesis.calibration.fixtures.matrix import build_matrix\n\n        frozen_negative_controls = negative_control_row_ids(build_matrix())\n        control_set = set(control_row_ids) & set(frozen_negative_controls)\n        control_excluded_count = 0''',
)
replace_once(
    "voice_genesis/calibration/tests/test_provenance.py",
    "from voice_genesis.calibration.vocab import BlockedCode",
    "from voice_genesis.calibration.fixtures.controls import negative_control_row_ids\nfrom voice_genesis.calibration.fixtures.matrix import build_matrix\nfrom voice_genesis.calibration.vocab import BlockedCode",
)
replace_once(
    "voice_genesis/calibration/tests/test_provenance.py",
    '''def test_check_leakage_control_row_pure_control_holdout_never_blocks() -> None:\n    entries = [\n        LedgerEntry(\n            seq=0,\n            prev_sha=\"0\" * 64,\n            entry_sha=\"a\" * 64,\n            payload={\"kind\": \"render\", \"row_id\": \"holdout-control-1\"},\n        ),\n        LedgerEntry(\n            seq=1,\n            prev_sha=\"a\" * 64,\n            entry_sha=\"b\" * 64,\n            payload={\"kind\": \"meter_call\", \"row_id\": \"holdout-control-1\"},\n        ),\n    ]\n    result = Ledger.check_leakage(\n        entries,\n        holdout_row_ids=[\"holdout-control-1\"],\n        unseal_seq=None,\n        control_row_ids=[\"holdout-control-1\"],\n    )\n    assert result.blocked is None\n    assert result.control_excluded_count == 2''',
    '''def test_check_leakage_control_row_pure_control_holdout_never_blocks() -> None:\n    control_id = sorted(negative_control_row_ids(build_matrix()))[0]\n    entries = [\n        LedgerEntry(\n            seq=0,\n            prev_sha=\"0\" * 64,\n            entry_sha=\"a\" * 64,\n            payload={\"kind\": \"render\", \"row_id\": control_id},\n        ),\n        LedgerEntry(\n            seq=1,\n            prev_sha=\"a\" * 64,\n            entry_sha=\"b\" * 64,\n            payload={\"kind\": \"meter_call\", \"row_id\": control_id},\n        ),\n    ]\n    result = Ledger.check_leakage(\n        entries,\n        holdout_row_ids=[control_id],\n        unseal_seq=None,\n        control_row_ids=[control_id],\n    )\n    assert result.blocked is None\n    assert result.control_excluded_count == 2''',
)
append_once(
    "voice_genesis/calibration/tests/test_provenance.py",
    "test_check_leakage_caller_cannot_forge_truth_row_as_control",
    '''def test_check_leakage_caller_cannot_forge_truth_row_as_control() -> None:\n    truth_row = next(\n        mr for mr in build_matrix() if mr.row.block == \"TRUTH_CORE\" and mr.row.control_class is None\n    )\n    entry = LedgerEntry(\n        seq=0,\n        prev_sha=\"0\" * 64,\n        entry_sha=\"a\" * 64,\n        payload={\"kind\": \"render\", \"row_id\": truth_row.row_id},\n    )\n\n    result = Ledger.check_leakage(\n        [entry],\n        holdout_row_ids=[truth_row.row_id],\n        unseal_seq=None,\n        control_row_ids=[truth_row.row_id],\n    )\n\n    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE\n    assert result.control_excluded_count == 0''',
)

# ---------------------------------------------------------------------------
# Gate 4: a stable pair identity may not satisfy multiple invariance axes.
# ---------------------------------------------------------------------------
replace_once(
    "voice_genesis/calibration/gates.py",
    '''    if unknown_bucket_keys:\n        gate4 = False\n        reasons.append(\n            \"gate4': invariance_pairs_by_axis has bucket key(s) not in \"\n            f\"declared_invariance_axes: {', '.join(unknown_bucket_keys)}\"\n        )\n\n    for axis in declared_invariance_axes:''',
    '''    if unknown_bucket_keys:\n        gate4 = False\n        reasons.append(\n            \"gate4': invariance_pairs_by_axis has bucket key(s) not in \"\n            f\"declared_invariance_axes: {', '.join(unknown_bucket_keys)}\"\n        )\n\n    pair_id_buckets: dict[str, set[str]] = {}\n    for bucket_axis, bucket_pairs in invariance_pairs_by_axis.items():\n        for pair in bucket_pairs:\n            pair_id_buckets.setdefault(pair.pair_id, set()).add(bucket_axis)\n    cross_axis_reuse = sorted(\n        pair_id for pair_id, buckets in pair_id_buckets.items() if len(buckets) > 1\n    )\n    if cross_axis_reuse:\n        gate4 = False\n        reasons.append(\n            \"gate4': duplicate pair_id(s) reused across invariance axes: \"\n            + \", \".join(cross_axis_reuse)\n        )\n\n    for axis in declared_invariance_axes:''',
)
append_once(
    "voice_genesis/calibration/tests/test_gates.py",
    "test_absolute_gates_invariance_pair_id_reused_across_axes_fails",
    '''def test_absolute_gates_invariance_pair_id_reused_across_axes_fails() -> None:\n    instances = [_instance(\"i1\", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]\n    shared_ids = [f\"shared-{i}\" for i in range(5)]\n    axis_a = [\n        InvariancePair(pair_id=pid, axis=\"axis-a\", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)\n        for pid in shared_ids\n    ]\n    axis_b = [\n        InvariancePair(pair_id=pid, axis=\"axis-b\", ds=0.0, e_use_i0=1.0, e_use_ia=1.0)\n        for pid in shared_ids\n    ]\n\n    result = absolute_gates(\n        instances,\n        u_rep=0.0,\n        u_proc=0.0,\n        invariance_pairs_by_axis={\"axis-a\": axis_a, \"axis-b\": axis_b},\n        declared_invariance_axes={\"axis-a\", \"axis-b\"},\n        fdr0=0.0,\n        fnr1=0.0,\n        min_count_met=True,\n    )\n\n    assert result.gate4_invariance is False\n    assert result.passed is False\n    assert any(\n        \"reused across invariance axes\" in reason and \"shared-0\" in reason\n        for reason in result.failure_reasons\n    )''',
)
