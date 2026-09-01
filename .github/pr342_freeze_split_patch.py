from pathlib import Path

ROOT = Path.cwd()


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    if old not in s:
        raise SystemExit(f"anchor missing: {path}: {old[:120]!r}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, block: str) -> None:
    p = ROOT / path
    s = p.read_text(encoding="utf-8")
    if marker in s:
        return
    p.write_text(s.rstrip() + "\n\n\n" + block.strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# C0: claim-critical set is an immutable closed set, not merely non-hollow.
# ---------------------------------------------------------------------------
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    '''def _check_hash_maps(manifest: Mapping[str, object]) -> list[str]:''',
    '''def _check_claim_critical_set(manifest: Mapping[str, object]) -> list[str]:
    """Require the C0 claim-critical declaration to equal the frozen D1 set.

    Presence/non-hollowness is insufficient: shrinking or extending this set would
    change which meter evidence later claim gates require.  The declaration is a
    list for manifest schema stability, but membership is a closed set: duplicate,
    missing, unknown, or non-string members all fail closed.
    """
    key = "frozen_design.claim_critical_set"
    found, value = _resolve(manifest, key)
    if not found or value is None or _is_hollow(value):
        return []  # required-key validation already reports absence/hollowness
    if not isinstance(value, list):
        return [f"{key}: type (must be a list, got {type(value).__name__})"]
    if any(not isinstance(member, str) for member in value):
        return [f"{key}: members (every member must be a meter-id string)"]

    expected = {meter.value for meter in vocab.CLAIM_CRITICAL_SET}
    declared = set(value)
    violations: list[str] = []
    if len(value) != len(declared):
        duplicates = sorted({member for member in value if value.count(member) > 1})
        violations.append(f"{key}: duplicate member(s) {duplicates!r}")
    missing = sorted(expected - declared)
    extra = sorted(declared - expected)
    if missing:
        violations.append(f"{key}: missing frozen meter(s) {missing!r}")
    if extra:
        violations.append(f"{key}: unknown/extra meter(s) {extra!r}")
    return violations


def _check_hash_maps(manifest: Mapping[str, object]) -> list[str]:''',
)
replace_once(
    "voice_genesis/calibration/c0_validate.py",
    '''    missing_required = _check_required_blocking(manifest)\n    missing_required += _check_checkout_identity(manifest)''',
    '''    missing_required = _check_required_blocking(manifest)\n    missing_required += _check_claim_critical_set(manifest)\n    missing_required += _check_checkout_identity(manifest)''',
)
append_once(
    "voice_genesis/calibration/tests/test_c0_validate.py",
    "test_claim_critical_set_must_exactly_match_frozen_set",
    '''@pytest.mark.parametrize(
    "declared",
    [
        ["M3_FORMANTS", "M2_SPECTRAL_TILT"],
        ["M3_FORMANTS", "M2_SPECTRAL_TILT", "M2_APERIODICITY", "M4_RESONANCE"],
        ["M3_FORMANTS", "M2_SPECTRAL_TILT", "M2_APERIODICITY", "M3_FORMANTS"],
        "M3_FORMANTS",
    ],
)
def test_claim_critical_set_must_exactly_match_frozen_set(declared) -> None:
    manifest = _complete_manifest()
    manifest["frozen_design"]["claim_critical_set"] = declared
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.is_blocked is True
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert any("frozen_design.claim_critical_set" in item for item in result.missing_required_keys)


def test_claim_critical_set_accepts_same_members_in_different_order() -> None:
    manifest = _complete_manifest()
    manifest["frozen_design"]["claim_critical_set"] = [
        "M2_APERIODICITY",
        "M3_FORMANTS",
        "M2_SPECTRAL_TILT",
    ]
    result = c0_validate.validate_c0_manifest(manifest)
    assert result.is_blocked is False''',
)


# ---------------------------------------------------------------------------
# Provenance: bind split verification to canonical matrix + frozen commitments.
# ---------------------------------------------------------------------------
replace_once(
    "voice_genesis/calibration/provenance.py",
    '''import fcntl\nimport json\nimport os''',
    '''import fcntl\nimport hashlib\nimport json\nimport os''',
)
replace_once(
    "voice_genesis/calibration/provenance.py",
    '''class Ledger:\n    """append-only JSONL 台帳。''',
    '''def _verified_split_freeze_commitment(
    ledger_entries: Sequence[LedgerEntry],
) -> tuple[str, str] | None:
    """Return the unique pre-measurement split-freeze commitments, fail closed.

    The ledger is the existing provenance authority boundary.  A valid
    ``split_frozen`` event must be in a fully valid chain, occur before any render
    or meter call, and carry both the realized-map hash and the SHA-256 commitment
    of the runtime split secret.  Multiple/ill-shaped freeze declarations are
    ambiguous and therefore rejected.
    """
    prev_sha = GENESIS_PREV_SHA
    for expected_seq, entry in enumerate(ledger_entries):
        if entry.seq != expected_seq or entry.prev_sha != prev_sha:
            return None
        if entry.entry_sha != _entry_sha(entry.seq, entry.prev_sha, entry.payload):
            return None
        prev_sha = entry.entry_sha

    frozen: tuple[str, str] | None = None
    for entry in ledger_entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        kind = payload.get("kind")
        if kind in ("render", "meter_call") and frozen is None:
            return None
        if kind != "split_frozen":
            continue
        if frozen is not None:
            return None
        realized_hash = payload.get("realized_split_map_hash")
        seal_commitment = payload.get("seal_commitment")
        if not _is_sha256_hex(realized_hash) or not _is_sha256_hex(seal_commitment):
            return None
        frozen = (realized_hash, seal_commitment)
    return frozen


class Ledger:\n    """append-only JSONL 台帳。''',
)
replace_once(
    "voice_genesis/calibration/provenance.py",
    '''        The protected row set is derived only after the supplied `RealizedSplitMap`\n        has been mechanically recomputed and verified from its split rows and the\n        split secret (`splitter.verify_split`).  A raw caller-provided assignment is\n        therefore not an authority boundary.  `holdout_row_ids` is only an equality\n        assertion against the verified map and cannot shrink the seal.''',
    '''        The protected row set is derived only after four independent checks agree:
        (1) the verification rows contain the complete canonical frozen matrix row-id
        set, (2) the realized map covers that same closed set, (3) `verify_split`
        mechanically reproduces the realized map, and (4) a valid pre-measurement
        `split_frozen` ledger event binds both `realized_sha` and SHA-256(split_secret).
        Thus neither caller-supplied rows, secret, nor a self-consistent reduced split
        can shrink the seal.  `holdout_row_ids` is only an equality assertion against
        the authenticated map.''',
)
replace_once(
    "voice_genesis/calibration/provenance.py",
    '''        try:\n            split_verified = verify_split(\n                split_verification_rows,\n                split_secret,\n                realized_split_map,\n            )\n        except (KeyError, TypeError, ValueError):\n            split_verified = False\n        if not split_verified:\n            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)\n\n        from voice_genesis.calibration.vocab import Split\n\n        authenticated_holdout_set = {''',
    '''        from voice_genesis.calibration.fixtures.matrix import build_matrix

        canonical_matrix = build_matrix()
        canonical_by_id = {row.row_id: row for row in canonical_matrix}
        canonical_row_ids = set(canonical_by_id)
        verification_row_ids = [row.row_id for row in split_verification_rows]
        if (
            len(verification_row_ids) != len(set(verification_row_ids))
            or set(verification_row_ids) != canonical_row_ids
            or set(realized_split_map.assignment) != canonical_row_ids
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)
        if any(
            row.family != canonical_by_id[row.row_id].row.family
            for row in split_verification_rows
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        try:
            split_verified = verify_split(
                split_verification_rows,
                split_secret,
                realized_split_map,
            )
        except (KeyError, TypeError, ValueError):
            split_verified = False
        if not split_verified:
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        frozen_split = _verified_split_freeze_commitment(ledger_entries)
        if frozen_split is None:
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)
        frozen_map_hash, frozen_secret_commitment = frozen_split
        if (
            frozen_map_hash != realized_split_map.realized_sha
            or frozen_secret_commitment != hashlib.sha256(split_secret).hexdigest()
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        from voice_genesis.calibration.vocab import Split

        authenticated_holdout_set = {''',
)

# Test helpers: authenticate legacy leakage-unit payloads against one canonical split.
replace_once(
    "voice_genesis/calibration/tests/test_provenance.py",
    '''import json\n\nimport pytest''',
    '''import hashlib\nimport json\nfrom functools import lru_cache\n\nimport pytest''',
)
replace_once(
    "voice_genesis/calibration/tests/test_provenance.py",
    '''    LedgerTruncatedTailError,\n    ProvenanceRecord,\n    provenance_record_to_dict,''',
    '''    LedgerTruncatedTailError,\n    ProvenanceRecord,\n    GENESIS_PREV_SHA,\n    _entry_sha,\n    provenance_record_to_dict,''',
)

p = ROOT / "voice_genesis/calibration/tests/test_provenance.py"
s = p.read_text(encoding="utf-8")
start = s.index("def _verified_split_material(")
end = s.index("def test_provenance_record_serializes_nested_dataclasses()")
helper_block = r'''@lru_cache(maxsize=1)
def _canonical_split_material():
    from voice_genesis.calibration.vocab import Split

    matrix = build_matrix()
    rows = tuple(
        RowInput(
            row_id=matrix_row.row_id,
            family=matrix_row.row.family,
            stratum={},
            truth_level=matrix_row.row.block,
            generator_impl=matrix_row.row.generator_impl,
            boundary_class=matrix_row.domain.value,
        )
        for matrix_row in matrix
    )
    negative_ids = set(negative_control_row_ids(matrix))
    truth_ids = {
        matrix_row.row_id for matrix_row in matrix if matrix_row.row.block == "TRUTH_CORE"
    }
    for nonce in range(64):
        secret = hashlib.sha256(f"canonical-split-test-{nonce}".encode("utf-8")).digest()
        realized = realize_split(rows, secret, ())
        holdout = {
            row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
        }
        if len(holdout & negative_ids) >= 2 and len(holdout & truth_ids) >= 3:
            return rows, secret, realized
    raise AssertionError("could not construct canonical test split with required holdout classes")


def _requested_row_map(requested_holdout_ids):
    rows, secret, realized = _canonical_split_material()
    matrix = build_matrix()
    matrix_by_id = {row.row_id: row for row in matrix}
    negative_ids = set(negative_control_row_ids(matrix))
    holdout = sorted(
        row_id
        for row_id, split in realized.assignment.items()
        if split.value == "HOLDOUT"
    )
    holdout_negative = [row_id for row_id in holdout if row_id in negative_ids]
    holdout_truth = [
        row_id
        for row_id in holdout
        if matrix_by_id[row_id].row.block == "TRUTH_CORE" and row_id not in negative_ids
    ]
    holdout_general = [row_id for row_id in holdout if row_id not in negative_ids]

    mapping = {}
    used = set()
    for requested in dict.fromkeys(requested_holdout_ids):
        if requested in holdout:
            chosen = requested
        elif requested in negative_ids:
            pool = holdout_negative
            chosen = next(row_id for row_id in pool if row_id not in used)
        elif requested in matrix_by_id and matrix_by_id[requested].row.block == "TRUTH_CORE":
            pool = holdout_truth
            chosen = next(row_id for row_id in pool if row_id not in used)
        else:
            pool = holdout_general
            chosen = next(row_id for row_id in pool if row_id not in used)
        mapping[requested] = chosen
        used.add(chosen)
    return mapping, tuple(holdout), rows, secret, realized


def _remap_payload(value, row_map, prior_sha_map):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "row_id" and isinstance(item, str) and item in row_map:
                out[key] = row_map[item]
            else:
                out[key] = _remap_payload(item, row_map, prior_sha_map)
        return out
    if isinstance(value, list):
        return [_remap_payload(item, row_map, prior_sha_map) for item in value]
    if isinstance(value, tuple):
        return tuple(_remap_payload(item, row_map, prior_sha_map) for item in value)
    if isinstance(value, str) and value in prior_sha_map:
        return prior_sha_map[value]
    return value


def _authenticated_entries(entries, row_map, realized, secret):
    rebuilt = []
    split_payload = {
        "kind": "split_frozen",
        "realized_split_map_hash": realized.realized_sha,
        "seal_commitment": hashlib.sha256(secret).hexdigest(),
    }
    split_sha = _entry_sha(0, GENESIS_PREV_SHA, split_payload)
    rebuilt.append(
        LedgerEntry(
            seq=0,
            prev_sha=GENESIS_PREV_SHA,
            entry_sha=split_sha,
            payload=split_payload,
        )
    )
    prior_sha_map = {}
    prev_sha = split_sha
    for old in entries:
        payload = _remap_payload(dict(old.payload), row_map, prior_sha_map)
        seq = len(rebuilt)
        entry_sha = _entry_sha(seq, prev_sha, payload)
        rebuilt.append(
            LedgerEntry(seq=seq, prev_sha=prev_sha, entry_sha=entry_sha, payload=payload)
        )
        prior_sha_map[old.entry_sha] = entry_sha
        prev_sha = entry_sha
    return tuple(rebuilt)


def _check_leakage(*args, **kwargs):
    if "holdout_row_ids" in kwargs:
        requested_holdout = tuple(kwargs["holdout_row_ids"])
    elif len(args) >= 2:
        requested_holdout = tuple(args[1])
    else:
        raise AssertionError("holdout_row_ids required by test wrapper")

    row_map, full_holdout, rows, secret, realized = _requested_row_map(requested_holdout)
    mutable_args = list(args)
    entries = kwargs.get("ledger_entries", mutable_args[0] if mutable_args else ())
    rebuilt = _authenticated_entries(entries, row_map, realized, secret)
    if mutable_args:
        mutable_args[0] = rebuilt
    else:
        kwargs["ledger_entries"] = rebuilt

    if len(mutable_args) >= 2:
        mutable_args[1] = full_holdout
    else:
        kwargs["holdout_row_ids"] = full_holdout

    if len(mutable_args) >= 3 and isinstance(mutable_args[2], int):
        mutable_args[2] += 1
    elif isinstance(kwargs.get("unseal_seq"), int):
        kwargs["unseal_seq"] += 1

    if "control_row_ids" in kwargs:
        kwargs["control_row_ids"] = tuple(
            row_map.get(row_id, row_id) for row_id in kwargs["control_row_ids"]
        )
    kwargs.setdefault("realized_split_map", realized)
    kwargs.setdefault("split_verification_rows", rows)
    kwargs.setdefault("split_secret", secret)
    return Ledger.check_leakage(*mutable_args, **kwargs)


'''
s = s[:start] + helper_block + s[end:]
s = s.replace(
    'payload={"kind": "split_frozen", "row_id": "holdout-1"}',
    'payload={"kind": "split_metadata", "row_id": "holdout-1"}',
    1,
)

# Replace the three direct split-auth tests at EOF with canonical, commitment-bound cases.
marker = "def test_check_leakage_rejects_incomplete_declared_holdout_set()"
idx = s.index(marker)
new_tail = r'''def _append_split_frozen(ledger, realized, secret):
    return ledger.append(
        {
            "kind": "split_frozen",
            "realized_split_map_hash": realized.realized_sha,
            "seal_commitment": hashlib.sha256(secret).hexdigest(),
        }
    )


def test_check_leakage_rejects_incomplete_declared_holdout_set(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    ledger.append({"kind": "render", "row_id": holdout[-1]})
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout[:-1],
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_requires_verified_realized_split_map() -> None:
    result = Ledger.check_leakage([], holdout_row_ids=[], unseal_seq=None)
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_rejects_tampered_realized_split_map(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    tampered_assignment = dict(realized.assignment)
    tampered_assignment[holdout[0]] = Split.CALIBRATION
    tampered = RealizedSplitMap(
        stratum_factor_names=realized.stratum_factor_names,
        assignment=tampered_assignment,
        swaps=realized.swaps,
        realized_sha=realized.realized_sha,
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=tampered,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_rejects_self_consistent_reduced_matrix_split(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    full_matrix = build_matrix()
    family = full_matrix[0].row.family
    subset_matrix = [row for row in full_matrix if row.row.family == family]
    subset_rows = tuple(
        RowInput(
            row_id=row.row_id,
            family=row.row.family,
            stratum={},
            truth_level=row.row.block,
            generator_impl=row.row.generator_impl,
            boundary_class=row.domain.value,
        )
        for row in subset_matrix
    )
    secret = hashlib.sha256(b"reduced-self-consistent-split").digest()
    realized = realize_split(subset_rows, secret, ())
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    assert holdout
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    ledger.append({"kind": "render", "row_id": holdout[0]})
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=subset_rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_rejects_split_secret_commitment_mismatch(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        {
            "kind": "split_frozen",
            "realized_split_map_hash": realized.realized_sha,
            "seal_commitment": hashlib.sha256(b"different-secret").hexdigest(),
        }
    )
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_accepts_full_canonical_split_with_matching_commitment(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked is None
'''
s = s[:idx] + new_tail
p.write_text(s, encoding="utf-8")
