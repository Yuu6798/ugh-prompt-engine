from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# gates.py — authenticate adjacency from a separately frozen closed-set input.
# ---------------------------------------------------------------------------
gates_path = Path("voice_genesis/calibration/gates.py")
gates = gates_path.read_text(encoding="utf-8")

gates = replace_once(
    gates,
    "    expected_sweep_ids: Collection[str],\n    negative_control_failures: int,\n",
    "    expected_sweep_ids: Collection[str],\n"
    "    expected_adjacent_pair_ids: Mapping[str, Collection[str]] | None = None,\n"
    "    negative_control_failures: int,\n",
    "directional_gates signature",
)

old_logic = '''    sweep_ids = sorted(set(expected_sweep_ids))
    sweep_resolvable_counts: dict[str, int] = {s: 0 for s in sweep_ids}
    for p in resolvable:
        if p.sweep_id in sweep_resolvable_counts:
            sweep_resolvable_counts[p.sweep_id] += 1

    sweeps_below_minimum = tuple(s for s in sweep_ids if sweep_resolvable_counts[s] < 3)
    sweeps_with_warning = tuple(s for s in sweep_ids if sweep_resolvable_counts[s] == 3)
    every_sweep_meets_minimum = bool(sweep_ids) and not sweeps_below_minimum
    three_pair_warning = bool(sweeps_with_warning)

    if not sweep_ids:
        reasons.append("no expected sweep declared")
    if sweeps_below_minimum:
        reasons.append("resolvable pair count < 3 in sweep(s): " + ", ".join(sweeps_below_minimum))

    adjacent_resolvable = [p for p in resolvable if p.is_adjacent]
    all_correct = all(_same_nonzero_sign(p.delta_truth, p.delta_output) for p in adjacent_resolvable)
    if not all_correct:
        reasons.append("not all resolvable adjacent pairs have correct measured delta sign")

    reversals = sum(
        1 for p in adjacent_resolvable if not _same_nonzero_sign(p.delta_truth, p.delta_output)
    )
    adjacent_reversal_rate = (reversals / len(adjacent_resolvable)) if adjacent_resolvable else 0.0
    if adjacent_reversal_rate != 0.0:
        reasons.append("adjacent_reversal_rate != 0")
'''

new_logic = '''    sweep_ids = sorted(set(expected_sweep_ids))
    sweep_resolvable_counts: dict[str, int] = {s: 0 for s in sweep_ids}
    for p in resolvable:
        if p.sweep_id in sweep_resolvable_counts:
            sweep_resolvable_counts[p.sweep_id] += 1

    sweeps_below_minimum = tuple(s for s in sweep_ids if sweep_resolvable_counts[s] < 3)
    sweeps_with_warning = tuple(s for s in sweep_ids if sweep_resolvable_counts[s] == 3)
    every_sweep_meets_minimum = bool(sweep_ids) and not sweeps_below_minimum
    three_pair_warning = bool(sweeps_with_warning)

    if not sweep_ids:
        reasons.append("no expected sweep declared")
    if sweeps_below_minimum:
        reasons.append("resolvable pair count < 3 in sweep(s): " + ", ".join(sweeps_below_minimum))

    # `is_adjacent` is caller metadata and must not decide which measured signs are
    # gated.  The authority boundary is a separately frozen sweep -> adjacent
    # pair-id declaration.  The runtime flag is checked only as an assertion.
    adjacency_inputs_valid = True
    frozen_adjacent_by_sweep: dict[str, set[str]] = {s: set() for s in sweep_ids}
    if expected_adjacent_pair_ids is None:
        adjacency_inputs_valid = False
        reasons.append("no frozen adjacent-pair declaration")
    else:
        declared_sweeps = set(expected_adjacent_pair_ids)
        if declared_sweeps != set(sweep_ids):
            adjacency_inputs_valid = False
            reasons.append("frozen adjacent-pair sweep set does not match expected_sweep_ids")

        declared_pair_owner: dict[str, str] = {}
        for sweep in sweep_ids:
            raw_ids = list(expected_adjacent_pair_ids.get(sweep, ()))
            if any(not isinstance(pair_id, str) or not pair_id for pair_id in raw_ids):
                adjacency_inputs_valid = False
                reasons.append(f"frozen adjacent-pair declaration has invalid pair_id in sweep {sweep}")
                continue
            if len(raw_ids) != len(set(raw_ids)):
                adjacency_inputs_valid = False
                reasons.append(f"frozen adjacent-pair declaration has duplicate pair_id in sweep {sweep}")
            ids = set(raw_ids)
            if not ids:
                adjacency_inputs_valid = False
                reasons.append(f"no frozen adjacent pair declared for sweep {sweep}")
            frozen_adjacent_by_sweep[sweep] = ids
            for pair_id in ids:
                owner = declared_pair_owner.get(pair_id)
                if owner is not None and owner != sweep:
                    adjacency_inputs_valid = False
                    reasons.append(
                        f"frozen adjacent pair_id {pair_id} is declared in multiple sweeps: "
                        f"{owner}, {sweep}"
                    )
                declared_pair_owner[pair_id] = sweep

        observed_by_id = {p.pair_id: p for p in pairs}
        missing_or_misowned: list[str] = []
        for sweep, pair_ids in frozen_adjacent_by_sweep.items():
            for pair_id in sorted(pair_ids):
                observed = observed_by_id.get(pair_id)
                if observed is None or observed.sweep_id != sweep:
                    missing_or_misowned.append(f"{sweep}:{pair_id}")
        if missing_or_misowned:
            adjacency_inputs_valid = False
            reasons.append(
                "frozen adjacent pair(s) missing or assigned to wrong sweep: "
                + ", ".join(missing_or_misowned)
            )

        flag_mismatches = sorted(
            p.pair_id
            for p in pairs
            if p.sweep_id in frozen_adjacent_by_sweep
            and p.is_adjacent != (p.pair_id in frozen_adjacent_by_sweep[p.sweep_id])
        )
        if flag_mismatches:
            adjacency_inputs_valid = False
            reasons.append(
                "runtime is_adjacent disagrees with frozen adjacency: "
                + ", ".join(flag_mismatches)
            )

    adjacent_resolvable = [
        p
        for p in resolvable
        if p.sweep_id in frozen_adjacent_by_sweep
        and p.pair_id in frozen_adjacent_by_sweep[p.sweep_id]
    ]
    has_authenticated_adjacent_evidence = adjacency_inputs_valid and bool(adjacent_resolvable)
    if not has_authenticated_adjacent_evidence:
        reasons.append("no authenticated resolvable adjacent evidence")

    all_correct = has_authenticated_adjacent_evidence and all(
        _same_nonzero_sign(p.delta_truth, p.delta_output) for p in adjacent_resolvable
    )
    if not all_correct:
        reasons.append("not all resolvable adjacent pairs have correct measured delta sign")

    reversals = sum(
        1 for p in adjacent_resolvable if not _same_nonzero_sign(p.delta_truth, p.delta_output)
    )
    adjacent_reversal_rate = (reversals / len(adjacent_resolvable)) if adjacent_resolvable else 0.0
    if adjacent_reversal_rate != 0.0:
        reasons.append("adjacent_reversal_rate != 0")
'''

gates = replace_once(gates, old_logic, new_logic, "directional adjacency logic")
gates = replace_once(
    gates,
    "        and every_sweep_meets_minimum\n        and not duplicate_pair_ids\n        and all_correct\n",
    "        and every_sweep_meets_minimum\n"
    "        and not duplicate_pair_ids\n"
    "        and adjacency_inputs_valid\n"
    "        and has_authenticated_adjacent_evidence\n"
    "        and all_correct\n",
    "directional passed predicate",
)
gates_path.write_text(gates, encoding="utf-8")


# ---------------------------------------------------------------------------
# provenance.py — bind every split-driving RowInput attribute to frozen matrix.
# ---------------------------------------------------------------------------
prov_path = Path("voice_genesis/calibration/provenance.py")
prov = prov_path.read_text(encoding="utf-8")
old_prov = '''        canonical_matrix = build_matrix()
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
'''
new_prov = '''        canonical_matrix = build_matrix()
        canonical_by_id = {row.row_id: row for row in canonical_matrix}
        canonical_row_ids = set(canonical_by_id)
        verification_row_ids = [row.row_id for row in split_verification_rows]
        if (
            len(verification_row_ids) != len(set(verification_row_ids))
            or set(verification_row_ids) != canonical_row_ids
            or set(realized_split_map.assignment) != canonical_row_ids
        ):
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)

        canonical_split_inputs = {
            matrix_row.row_id: RowInput(
                row_id=matrix_row.row_id,
                family=matrix_row.row.family,
                stratum={},
                truth_level=matrix_row.row.block,
                generator_impl=matrix_row.row.generator_impl,
                boundary_class=matrix_row.domain.value,
            )
            for matrix_row in canonical_matrix
        }
        for supplied in split_verification_rows:
            expected = canonical_split_inputs[supplied.row_id]
            if (
                supplied.family != expected.family
                or dict(supplied.stratum) != dict(expected.stratum)
                or supplied.truth_level != expected.truth_level
                or supplied.generator_impl != expected.generator_impl
                or supplied.boundary_class != expected.boundary_class
            ):
                return LeakageCheckResult(
                    blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0
                )
        if tuple(realized_split_map.stratum_factor_names) != ():
            return LeakageCheckResult(blocked=BlockedCode.BLOCKED_LEAKAGE, control_excluded_count=0)
'''
prov = replace_once(prov, old_prov, new_prov, "canonical split-row attribute binding")
prov_path.write_text(prov, encoding="utf-8")


# ---------------------------------------------------------------------------
# test_gates.py — test-only declaration adapter + two regressions.
# ---------------------------------------------------------------------------
tg_path = Path("voice_genesis/calibration/tests/test_gates.py")
tg = tg_path.read_text(encoding="utf-8")
pair_helper_end = '''    return DirectionalPair(
        pair_id=pair_id,
        delta_truth=delta_truth,
        delta_output=delta_output,
        u_gt_i=u_gt_i,
        u_num_i=u_num_i,
        u_gt_j=u_gt_j,
        u_num_j=u_num_j,
        correct_sign=correct_sign,
        is_adjacent=is_adjacent,
        sweep_id=sweep_id,
    )


'''
wrapper = pair_helper_end + '''_directional_gates_impl = directional_gates


def directional_gates(pairs, **kwargs):
    """Test adapter: make each fixture's intended adjacency an explicit declaration."""
    if "expected_adjacent_pair_ids" not in kwargs:
        expected_sweeps = set(kwargs.get("expected_sweep_ids", ()))
        kwargs["expected_adjacent_pair_ids"] = {
            sweep: tuple(
                sorted(
                    p.pair_id
                    for p in pairs
                    if p.sweep_id == sweep and p.is_adjacent
                )
            )
            for sweep in expected_sweeps
        }
    return _directional_gates_impl(pairs, **kwargs)


'''
tg = replace_once(tg, pair_helper_end, wrapper, "test directional wrapper")

# These two tests explicitly assert a PASS and therefore need real authenticated
# adjacent evidence rather than the old vacuous all-nonadjacent path.
tg = replace_once(
    tg,
    '''    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-A")
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"sweep-A"},
''',
    '''    pairs = [
        _pair(f"p{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, sweep_id="sweep-A")
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.02,
        u_proc=0.01,
        expected_sweep_ids={"sweep-A"},
''',
    "single-sweep pass fixture",
)
tg = replace_once(
    tg,
    '''    pairs = [
        _pair(f"a{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-A")
        for i in range(3)
    ] + [
        _pair(f"b{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=False, sweep_id="sweep-B")
        for i in range(4)
    ]
''',
    '''    pairs = [
        _pair(f"a{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, sweep_id="sweep-A")
        for i in range(3)
    ] + [
        _pair(f"b{i}", delta_truth=1.0, delta_output=1.0, is_adjacent=True, sweep_id="sweep-B")
        for i in range(4)
    ]
''',
    "multi-sweep pass fixture",
)

if "def test_directional_caller_cannot_hide_reversals_with_nonadjacent_flags" in tg:
    raise SystemExit("adjacency regression already present")
tg += '''


def test_directional_caller_cannot_hide_reversals_with_nonadjacent_flags() -> None:
    pairs = [
        _pair(
            f"hide-{i}",
            delta_truth=1.0,
            delta_output=-1.0,
            correct_sign=True,
            is_adjacent=False,
        )
        for i in range(3)
    ]
    result = directional_gates(
        pairs,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"default"},
        expected_adjacent_pair_ids={"default": {"hide-0", "hide-1", "hide-2"}},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.resolvable_count == 3
    assert result.adjacent_reversal_rate == 1.0
    assert result.all_resolvable_correct_sign is False
    assert result.passed is False
    assert any("is_adjacent" in reason for reason in result.failure_reasons)


def test_directional_missing_frozen_adjacency_declaration_fails_closed() -> None:
    pairs = [_pair(f"decl-{i}", delta_truth=1.0, delta_output=1.0) for i in range(3)]
    result = _directional_gates_impl(
        pairs,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"default"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert any("frozen adjacent-pair declaration" in reason for reason in result.failure_reasons)
'''
tg_path.write_text(tg, encoding="utf-8")


# ---------------------------------------------------------------------------
# test_provenance.py — append forged RowInput-attribute regression robustly.
# ---------------------------------------------------------------------------
tp_path = Path("voice_genesis/calibration/tests/test_provenance.py")
tp = tp_path.read_text(encoding="utf-8")
if "def test_check_leakage_rejects_forged_split_row_attributes_even_with_matching_freeze" in tp:
    raise SystemExit("split-row regression already present")
tp += '''


def test_check_leakage_rejects_forged_split_row_attributes_even_with_matching_freeze() -> None:
    """A self-consistent split from forged split-driving row metadata is not authoritative."""
    from voice_genesis.calibration.vocab import Split

    canonical_rows, secret, canonical_realized = _canonical_split_material()
    target = canonical_rows[0]
    forged_variants = (
        {"stratum": {"forged": "value"}},
        {"truth_level": "FORGED_TRUTH"},
        {"generator_impl": "FORGED_IMPL"},
        {"boundary_class": "FORGED_BOUNDARY"},
    )

    for overrides in forged_variants:
        forged_rows = list(canonical_rows)
        values = {
            "row_id": target.row_id,
            "family": target.family,
            "stratum": dict(target.stratum),
            "truth_level": target.truth_level,
            "generator_impl": target.generator_impl,
            "boundary_class": target.boundary_class,
        }
        values.update(overrides)
        forged_rows[0] = RowInput(**values)
        forged_realized = realize_split(
            forged_rows, secret, canonical_realized.stratum_factor_names
        )
        split_payload = {
            "kind": "split_frozen",
            "realized_split_map_hash": forged_realized.realized_sha,
            "seal_commitment": hashlib.sha256(secret).hexdigest(),
        }
        split_entry = LedgerEntry(
            seq=0,
            prev_sha=GENESIS_PREV_SHA,
            entry_sha=_entry_sha(0, GENESIS_PREV_SHA, split_payload),
            payload=split_payload,
        )
        holdout = [
            row_id
            for row_id, split in forged_realized.assignment.items()
            if split == Split.HOLDOUT
        ]
        result = Ledger.check_leakage(
            [split_entry],
            holdout_row_ids=holdout,
            unseal_seq=None,
            realized_split_map=forged_realized,
            split_verification_rows=forged_rows,
            split_secret=secret,
        )
        assert result.blocked == BlockedCode.BLOCKED_LEAKAGE, overrides
'''
tp_path.write_text(tp, encoding="utf-8")
