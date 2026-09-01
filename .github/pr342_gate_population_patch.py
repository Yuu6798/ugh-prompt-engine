from pathlib import Path

GATES = Path("voice_genesis/calibration/gates.py")
TESTS = Path("voice_genesis/calibration/tests/test_gates.py")

text = GATES.read_text(encoding="utf-8")

old = '''    invariance_pairs_by_axis: Mapping[str, Sequence[InvariancePair]],
    declared_invariance_axes: Collection[str],
    fdr0: float,
'''
new = '''    invariance_pairs_by_axis: Mapping[str, Sequence[InvariancePair]],
    declared_invariance_axes: Collection[str],
    expected_primary_instance_ids: Collection[str] | None = None,
    fdr0: float,
'''
assert old in text
text = text.replace(old, new, 1)

old = '''    `per_instance` 中の PRIMARY `InstanceMargin` は `instance_id` で重複がない
    ことも検査する（Codex レビュー 2026-09-01 P1: 従来は identity を見ずに
'''
new = '''    `expected_primary_instance_ids` は C0 / sealed holdout plan から渡される
    PRIMARY instance の凍結 closed set であり、実測 `per_instance` 内の PRIMARY
    ID 集合と完全一致しなければ gate1 を FAIL させ、G/BIAS 集計自体を行わない。
    これにより failing PRIMARY instance の欠落や BOUNDARY 等への再ラベルで
    gate 母集団を縮小する経路を fail-closed にする。

    `per_instance` 中の PRIMARY `InstanceMargin` は `instance_id` で重複がない
    ことも検査する（Codex レビュー 2026-09-01 P1: 従来は identity を見ずに
'''
assert old in text
text = text.replace(old, new, 1)

old = '''    reasons: list[str] = []

    primary_ids = [i.instance_id for i in primary]
    duplicate_instance_ids = sorted({iid for iid in primary_ids if primary_ids.count(iid) > 1})

    global_uncertainty_finite = math.isfinite(u_rep) and math.isfinite(u_proc)
'''
new = '''    reasons: list[str] = []

    primary_ids = [i.instance_id for i in primary]
    duplicate_instance_ids = sorted({iid for iid in primary_ids if primary_ids.count(iid) > 1})

    primary_population_valid = True
    observed_primary_ids = set(primary_ids)
    if expected_primary_instance_ids is None:
        primary_population_valid = False
        reasons.append("gate1: no frozen PRIMARY instance declaration")
    else:
        raw_expected_primary_ids = list(expected_primary_instance_ids)
        valid_expected_primary_ids = [
            iid for iid in raw_expected_primary_ids if isinstance(iid, str) and iid
        ]
        invalid_expected_primary_ids = [
            repr(iid)
            for iid in raw_expected_primary_ids
            if not isinstance(iid, str) or not iid
        ]
        duplicate_expected_primary_ids = sorted(
            {
                iid
                for iid in valid_expected_primary_ids
                if valid_expected_primary_ids.count(iid) > 1
            }
        )
        expected_primary_ids = set(valid_expected_primary_ids)
        if invalid_expected_primary_ids:
            primary_population_valid = False
            reasons.append(
                "gate1: frozen PRIMARY declaration has invalid instance_id(s): "
                + ", ".join(invalid_expected_primary_ids)
            )
        if duplicate_expected_primary_ids:
            primary_population_valid = False
            reasons.append(
                "gate1: frozen PRIMARY declaration has duplicate instance_id(s): "
                + ", ".join(duplicate_expected_primary_ids)
            )
        if observed_primary_ids != expected_primary_ids:
            primary_population_valid = False
            missing_primary_ids = sorted(expected_primary_ids - observed_primary_ids)
            unexpected_primary_ids = sorted(observed_primary_ids - expected_primary_ids)
            detail: list[str] = []
            if missing_primary_ids:
                detail.append("missing=" + ",".join(missing_primary_ids))
            if unexpected_primary_ids:
                detail.append("unexpected=" + ",".join(unexpected_primary_ids))
            reasons.append(
                "gate1: observed PRIMARY instance set does not match frozen declaration"
                + (": " + "; ".join(detail) if detail else "")
            )

    global_uncertainty_finite = math.isfinite(u_rep) and math.isfinite(u_proc)
'''
assert old in text
text = text.replace(old, new, 1)

old = '''    budget_inputs_valid = (
        global_uncertainty_valid and not invalid_primary_budget_ids and not ae_mismatch_ids
    )
'''
new = '''    budget_inputs_valid = (
        global_uncertainty_valid
        and primary_population_valid
        and not invalid_primary_budget_ids
        and not ae_mismatch_ids
    )
'''
assert old in text
text = text.replace(old, new, 1)

old = '''    gate1 = (
        all(i.eligible for i in primary)
        and not duplicate_instance_ids
        and not ae_mismatch_ids
    )
'''
new = '''    gate1 = (
        primary_population_valid
        and all(i.eligible for i in primary)
        and not duplicate_instance_ids
        and not ae_mismatch_ids
    )
'''
assert old in text
text = text.replace(old, new, 1)

old = '''    sweep_ids = sorted(set(expected_sweep_ids))
    sweep_resolvable_counts: dict[str, int] = {s: 0 for s in sweep_ids}
'''
new = '''    sweep_ids = sorted(set(expected_sweep_ids))
    observed_sweep_ids = {p.sweep_id for p in pairs}
    undeclared_sweep_ids = sorted(observed_sweep_ids - set(sweep_ids))
    if undeclared_sweep_ids:
        reasons.append(
            "observed directional pair(s) from undeclared sweep(s): "
            + ", ".join(undeclared_sweep_ids)
        )

    sweep_resolvable_counts: dict[str, int] = {s: 0 for s in sweep_ids}
'''
assert old in text
text = text.replace(old, new, 1)

old = '''        budget_inputs_valid
        and every_sweep_meets_minimum
        and not duplicate_pair_ids
'''
new = '''        budget_inputs_valid
        and every_sweep_meets_minimum
        and not undeclared_sweep_ids
        and not duplicate_pair_ids
'''
assert old in text
text = text.replace(old, new, 1)

GATES.write_text(text, encoding="utf-8")

text = TESTS.read_text(encoding="utf-8")
old = '''    absolute_gates,
'''
new = '''    absolute_gates as _absolute_gates_impl,
'''
assert old in text
text = text.replace(old, new, 1)

anchor = '''\n\ndef test_absolute_gates_g_zero_passes_boundary() -> None:\n'''
insert = '''\n\ndef absolute_gates(per_instance, **kwargs):
    """Test adapter: ordinary arithmetic tests declare their supplied PRIMARY set as frozen."""
    kwargs.setdefault(
        "expected_primary_instance_ids",
        tuple(i.instance_id for i in per_instance if i.domain == Domain.PRIMARY),
    )
    return _absolute_gates_impl(per_instance, **kwargs)


def test_absolute_gates_g_zero_passes_boundary() -> None:
'''
assert anchor in text
text = text.replace(anchor, insert, 1)

append = '''

# ---------------------------------------------------------------------------
# Review regressions: frozen gate populations
# ---------------------------------------------------------------------------


def test_absolute_gates_fails_when_frozen_primary_instance_is_relabelled_boundary() -> None:
    good = _instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)
    relabelled = InstanceMargin(
        instance_id="i2",
        domain=Domain.BOUNDARY,
        eligible=False,
        ae=100.0,
        e=100.0,
        u_gt=0.0,
        u_num=0.0,
        e_use=1.0,
    )
    result = absolute_gates(
        [good, relabelled],
        expected_primary_instance_ids={"i1", "i2"},
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate1_all_eligible is False
    assert result.g_values == ()
    assert result.passed is False
    assert any("PRIMARY instance set" in reason and "i2" in reason for reason in result.failure_reasons)


def test_absolute_gates_without_frozen_primary_declaration_fails_closed() -> None:
    result = _absolute_gates_impl(
        [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)],
        u_rep=0.0,
        u_proc=0.0,
        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},
        declared_invariance_axes={"axis1"},
        fdr0=0.0,
        fnr1=0.0,
        min_count_met=True,
    )
    assert result.gate1_all_eligible is False
    assert result.g_values == ()
    assert result.passed is False
    assert any("no frozen PRIMARY" in reason for reason in result.failure_reasons)


def test_directional_gates_rejects_observations_from_undeclared_sweep() -> None:
    declared = [
        _pair(
            f"declared-{i}",
            delta_truth=1.0,
            delta_output=1.0,
            sweep_id="declared",
            is_adjacent=True,
        )
        for i in range(3)
    ]
    undeclared = [
        _pair(
            f"hidden-{i}",
            delta_truth=1.0,
            delta_output=-1.0,
            sweep_id="hidden",
            is_adjacent=True,
        )
        for i in range(3)
    ]
    result = directional_gates(
        declared + undeclared,
        u_rep=0.0,
        u_proc=0.0,
        expected_sweep_ids={"declared"},
        negative_control_failures=0,
        positive_control_failures=0,
        units_commensurate=False,
    )
    assert result.passed is False
    assert any("undeclared sweep" in reason and "hidden" in reason for reason in result.failure_reasons)
'''
if "test_absolute_gates_fails_when_frozen_primary_instance_is_relabelled_boundary" not in text:
    text += append
TESTS.write_text(text, encoding="utf-8")
