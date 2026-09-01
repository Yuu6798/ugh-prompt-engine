from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}")
    p.write_text(text.replace(old, new, 1))


GATES = "voice_genesis/calibration/gates.py"
TEST_GATES = "voice_genesis/calibration/tests/test_gates.py"
TRANSITION = "voice_genesis/calibration/candidates/impl/transition.py"
TEST_ADAPTERS = "voice_genesis/calibration/tests/test_adapters.py"

replace_once(
    GATES,
    '''def _all_finite(values: Sequence[float]) -> bool:\n    """`values` の全要素が有限 (`math.isfinite`) か。NaN/inf/-inf のいずれかを\n    含めば `False`（gates.py:226 P1 finding: NaN の margins/aggregate が\n    `> 0`/`<= 0` の比較を素通りしてしまう対策の共通ヘルパー）。"""\n    return all(math.isfinite(v) for v in values)\n''',
    '''def _all_finite(values: Sequence[float]) -> bool:\n    """`values` の全要素が有限 (`math.isfinite`) か。NaN/inf/-inf のいずれかを\n    含めば `False`（gates.py:226 P1 finding: NaN の margins/aggregate が\n    `> 0`/`<= 0` の比較を素通りしてしまう対策の共通ヘルパー）。"""\n    return all(math.isfinite(v) for v in values)\n\n\ndef _nonnegative_finite(value: float) -> bool:\n    """Uncertainty/error budgets are finite and physically nonnegative."""\n    return math.isfinite(value) and value >= 0.0\n\n\ndef _positive_finite(value: float) -> bool:\n    """E_use is a finite, strictly positive acceptance budget."""\n    return math.isfinite(value) and value > 0.0\n\n\ndef _same_nonzero_sign(a: float, b: float) -> bool:\n    """Derive directional sign agreement from measured deltas, never caller metadata."""\n    return math.isfinite(a) and math.isfinite(b) and a != 0.0 and b != 0.0 and ((a > 0) == (b > 0))\n''',
)

replace_once(
    GATES,
    '''    reasons: list[str] = []\n\n    primary_ids = [i.instance_id for i in primary]\n    duplicate_instance_ids = sorted({iid for iid in primary_ids if primary_ids.count(iid) > 1})\n\n    gate1 = all(i.eligible for i in primary) and not duplicate_instance_ids\n''',
    '''    reasons: list[str] = []\n\n    primary_ids = [i.instance_id for i in primary]\n    duplicate_instance_ids = sorted({iid for iid in primary_ids if primary_ids.count(iid) > 1})\n\n    global_uncertainty_valid = _nonnegative_finite(u_rep) and _nonnegative_finite(u_proc)\n    invalid_primary_budget_ids = sorted(\n        i.instance_id\n        for i in primary\n        if not (\n            _nonnegative_finite(i.ae)\n            and _nonnegative_finite(i.u_gt)\n            and _nonnegative_finite(i.u_num)\n            and _positive_finite(i.e_use)\n        )\n    )\n    budget_inputs_valid = global_uncertainty_valid and not invalid_primary_budget_ids\n    if not global_uncertainty_valid:\n        reasons.append("gate budgets: U_rep/U_proc must be finite and nonnegative")\n    if invalid_primary_budget_ids:\n        reasons.append(\n            "gate budgets: PRIMARY AE/U_GT/U_num must be finite/nonnegative and E_use > 0: "\n            + ", ".join(invalid_primary_budget_ids)\n        )\n\n    gate1 = all(i.eligible for i in primary) and not duplicate_instance_ids\n''',
)

replace_once(
    GATES,
    '''    eligible_primary = [i for i in primary if i.eligible]\n    g_values = tuple(i.ae + i.u_gt + i.u_num + u_rep + u_proc - i.e_use for i in eligible_primary)\n    g_values_finite = _all_finite(g_values)\n''',
    '''    eligible_primary = [i for i in primary if i.eligible]\n    g_values = (\n        tuple(i.ae + i.u_gt + i.u_num + u_rep + u_proc - i.e_use for i in eligible_primary)\n        if budget_inputs_valid\n        else ()\n    )\n    g_values_finite = _all_finite(g_values)\n''',
)

replace_once(
    GATES,
    '''        gate3_inputs_finite = (\n            _all_finite(e_values)\n            and _all_finite(u_gt_num_values)\n            and _all_finite(e_use_values)\n            and math.isfinite(u_rep)\n            and math.isfinite(u_proc)\n        )\n''',
    '''        gate3_inputs_finite = (\n            budget_inputs_valid\n            and _all_finite(e_values)\n            and _all_finite(u_gt_num_values)\n            and _all_finite(e_use_values)\n        )\n''',
)

replace_once(
    GATES,
    '''        if len(pairs) < 5:\n            gate4 = False\n            reasons.append(f"gate4': axis {axis} has <5 pairs")\n            continue\n        margins = [p.ds + u_rep + u_proc - min(p.e_use_i0, p.e_use_ia) for p in pairs]\n''',
    '''        if len(pairs) < 5:\n            gate4 = False\n            reasons.append(f"gate4': axis {axis} has <5 pairs")\n            continue\n        invalid_budget_pair_ids = sorted(\n            p.pair_id\n            for p in pairs\n            if not (\n                _nonnegative_finite(p.ds)\n                and _positive_finite(p.e_use_i0)\n                and _positive_finite(p.e_use_ia)\n            )\n        )\n        if not global_uncertainty_valid or invalid_budget_pair_ids:\n            gate4 = False\n            if invalid_budget_pair_ids:\n                reasons.append(\n                    f"gate4': axis {axis} has invalid nonnegative/positive budget pair(s): "\n                    + ", ".join(invalid_budget_pair_ids)\n                )\n            continue\n        margins = [p.ds + u_rep + u_proc - min(p.e_use_i0, p.e_use_ia) for p in pairs]\n''',
)

replace_once(
    GATES,
    '''    passed = gate1 and gate2 and gate_max and gate3 and gate4 and gate5\n''',
    '''    passed = budget_inputs_valid and gate1 and gate2 and gate_max and gate3 and gate4 and gate5\n''',
)

replace_once(
    GATES,
    '''    reasons: list[str] = []\n\n    seen_pair_ids: set[str] = set()\n''',
    '''    reasons: list[str] = []\n\n    global_uncertainty_valid = _nonnegative_finite(u_rep) and _nonnegative_finite(u_proc)\n    invalid_pair_budget_ids = sorted(\n        {\n            p.pair_id\n            for p in pairs\n            if not all(\n                _nonnegative_finite(v)\n                for v in (p.u_gt_i, p.u_num_i, p.u_gt_j, p.u_num_j)\n            )\n        }\n    )\n    budget_inputs_valid = global_uncertainty_valid and not invalid_pair_budget_ids\n    if not global_uncertainty_valid:\n        reasons.append("directional budgets: U_rep/U_proc must be finite and nonnegative")\n    if invalid_pair_budget_ids:\n        reasons.append(\n            "directional budgets: pair uncertainty terms must be finite and nonnegative: "\n            + ", ".join(invalid_pair_budget_ids)\n        )\n\n    seen_pair_ids: set[str] = set()\n''',
)

replace_once(
    GATES,
    '''    resolvable: list[DirectionalPair] = []\n    for p in pairs:\n        r_truth = (p.u_gt_i + p.u_num_i) + (p.u_gt_j + p.u_num_j)\n        truth_resolvable = p.delta_truth > r_truth\n        output_significant = abs(p.delta_output) > 2 * (u_rep + u_proc)\n        ok = truth_resolvable and output_significant\n        if units_commensurate:\n            r_combined = r_truth + 2 * (u_rep + u_proc)\n            ok = ok and (p.delta_truth > r_combined)\n        if ok:\n            resolvable.append(p)\n''',
    '''    resolvable: list[DirectionalPair] = []\n    if budget_inputs_valid:\n        for p in pairs:\n            r_truth = (p.u_gt_i + p.u_num_i) + (p.u_gt_j + p.u_num_j)\n            truth_resolvable = p.delta_truth > r_truth\n            output_significant = abs(p.delta_output) > 2 * (u_rep + u_proc)\n            ok = truth_resolvable and output_significant\n            if units_commensurate:\n                r_combined = r_truth + 2 * (u_rep + u_proc)\n                ok = ok and (p.delta_truth > r_combined)\n            if ok:\n                resolvable.append(p)\n''',
)

replace_once(
    GATES,
    '''    adjacent_resolvable = [p for p in resolvable if p.is_adjacent]\n    all_correct = all(p.correct_sign for p in adjacent_resolvable)\n    if not all_correct:\n        reasons.append("not all resolvable adjacent pairs have correct sign")\n\n    reversals = sum(1 for p in adjacent_resolvable if not p.correct_sign)\n''',
    '''    adjacent_resolvable = [p for p in resolvable if p.is_adjacent]\n    all_correct = all(_same_nonzero_sign(p.delta_truth, p.delta_output) for p in adjacent_resolvable)\n    if not all_correct:\n        reasons.append("not all resolvable adjacent pairs have correct measured delta sign")\n\n    reversals = sum(\n        1 for p in adjacent_resolvable if not _same_nonzero_sign(p.delta_truth, p.delta_output)\n    )\n''',
)

replace_once(
    GATES,
    '''    passed = (\n        every_sweep_meets_minimum\n''',
    '''    passed = (\n        budget_inputs_valid\n        and every_sweep_meets_minimum\n''',
)

replace_once(
    TRANSITION,
    '''    join_time_s = float((peak_idx + 1) * hop / sr)\n    return join_time_s, magnitude\n''',
    '''    # flux_values[k] compares frames k and k+1. Timestamp the event at the\n    # midpoint of those two frame centers, not at the second frame start.\n    join_time_s = float(((peak_idx + 0.5) * hop + frame_len / 2.0) / sr)\n    return join_time_s, magnitude\n''',
)

replace_once(
    TEST_GATES,
    '''    # 1 件だけ符号反転\n    pairs[0] = _pair("p0", delta_truth=1.0, delta_output=1.0, is_adjacent=True, correct_sign=False)\n''',
    '''    # 1 件だけ measured delta を符号反転。caller metadata は故意に True のままにし、\n    # gate が boolean を信頼せず recorded deltas から符号を導出することを固定する。\n    pairs[0] = _pair("p0", delta_truth=1.0, delta_output=-1.0, is_adjacent=True, correct_sign=True)\n''',
)

replace_once(
    TEST_GATES,
    '''    nonadjacent_wrong = _pair(\n        "nonadj-wrong", delta_truth=1.0, delta_output=1.0, is_adjacent=False, correct_sign=False\n    )\n''',
    '''    nonadjacent_wrong = _pair(\n        "nonadj-wrong", delta_truth=1.0, delta_output=-1.0, is_adjacent=False, correct_sign=True\n    )\n''',
)

append_gates = '''\n\n@pytest.mark.parametrize(\n    ("field", "value"),\n    [("ae", -1.0), ("u_gt", -1.0), ("u_num", -1.0), ("e_use", 0.0)],\n)\ndef test_absolute_gates_reject_invalid_primary_budget_values(field: str, value: float) -> None:\n    values = dict(ae=0.1, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)\n    values[field] = value\n    instance = _instance("invalid-budget", **values)\n    result = absolute_gates(\n        [instance],\n        u_rep=0.0,\n        u_proc=0.0,\n        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},\n        declared_invariance_axes={"axis1"},\n        fdr0=0.0,\n        fnr1=0.0,\n        min_count_met=True,\n    )\n    assert result.passed is False\n    assert any("gate budgets" in reason for reason in result.failure_reasons)\n\n\n@pytest.mark.parametrize(("u_rep", "u_proc"), [(-0.1, 0.0), (0.0, -0.1)])\ndef test_absolute_gates_reject_negative_global_uncertainty(u_rep: float, u_proc: float) -> None:\n    result = absolute_gates(\n        [_instance("i1", ae=0.1, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)],\n        u_rep=u_rep,\n        u_proc=u_proc,\n        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},\n        declared_invariance_axes={"axis1"},\n        fdr0=0.0,\n        fnr1=0.0,\n        min_count_met=True,\n    )\n    assert result.passed is False\n    assert any("U_rep/U_proc" in reason for reason in result.failure_reasons)\n\n\ndef test_absolute_gates_negative_uncertainty_cannot_cancel_large_error() -> None:\n    exploit = _instance("exploit", ae=10.0, e=0.0, u_gt=-100.0, u_num=0.0, e_use=1.0)\n    result = absolute_gates(\n        [exploit],\n        u_rep=0.0,\n        u_proc=0.0,\n        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},\n        declared_invariance_axes={"axis1"},\n        fdr0=0.0,\n        fnr1=0.0,\n        min_count_met=True,\n    )\n    assert result.passed is False\n    assert result.gate2_q95 is False\n\n\ndef test_directional_gates_reject_negative_uncertainty_budget() -> None:\n    pairs = [\n        _pair(\n            f"p{i}",\n            delta_truth=1.0,\n            delta_output=1.0,\n            u_gt_i=-100.0,\n            is_adjacent=True,\n        )\n        for i in range(3)\n    ]\n    result = directional_gates(\n        pairs,\n        u_rep=0.0,\n        u_proc=0.0,\n        expected_sweep_ids={"default"},\n        negative_control_failures=0,\n        positive_control_failures=0,\n        units_commensurate=False,\n    )\n    assert result.passed is False\n    assert result.resolvable_count == 0\n    assert any("directional budgets" in reason for reason in result.failure_reasons)\n\n\ndef test_directional_sign_is_derived_from_measured_deltas_not_flag() -> None:\n    pairs = [\n        _pair(\n            f"p{i}",\n            delta_truth=1.0,\n            delta_output=-1.0,\n            correct_sign=True,\n            is_adjacent=True,\n        )\n        for i in range(3)\n    ]\n    result = directional_gates(\n        pairs,\n        u_rep=0.0,\n        u_proc=0.0,\n        expected_sweep_ids={"default"},\n        negative_control_failures=0,\n        positive_control_failures=0,\n        units_commensurate=False,\n    )\n    assert result.resolvable_count == 3\n    assert result.all_resolvable_correct_sign is False\n    assert result.adjacent_reversal_rate == 1.0\n    assert result.passed is False\n'''
Path(TEST_GATES).write_text(Path(TEST_GATES).read_text() + append_gates)

replace_once(
    TEST_ADAPTERS,
    '''def test_spectral_flux_fires_on_step_and_silent_on_steady() -> None:\n    steady, step = _steady_and_step_signals()\n    for frame_len in (512, 1024):\n        for norm in ("l1", "l2"):\n            _, mag_steady = transition.spectral_flux(steady, SR, frame_len=frame_len, norm=norm)\n            _, mag_step = transition.spectral_flux(step, SR, frame_len=frame_len, norm=norm)\n            assert mag_step > mag_steady * 5.0\n''',
    '''def test_spectral_flux_fires_on_step_and_silent_on_steady() -> None:\n    steady, step = _steady_and_step_signals()\n    for frame_len in (512, 1024):\n        for norm in ("l1", "l2"):\n            _, mag_steady = transition.spectral_flux(steady, SR, frame_len=frame_len, norm=norm)\n            _, mag_step = transition.spectral_flux(step, SR, frame_len=frame_len, norm=norm)\n            assert mag_step > mag_steady * 5.0\n\n\ndef test_spectral_flux_timestamps_frame_pair_midpoint() -> None:\n    frame_len = 512\n    hop = frame_len // 2\n    # Exactly two overlapping frames -> exactly one flux value (k=0). The event\n    # time is the midpoint of frame-center 0 and frame-center 1.\n    signal = np.concatenate([np.zeros(hop), np.ones(frame_len)])\n    join_time_s, magnitude = transition.spectral_flux(\n        signal, SR, frame_len=frame_len, norm="l1"\n    )\n    expected_s = ((0.5 * hop) + frame_len / 2.0) / SR\n    assert magnitude > 0.0\n    assert join_time_s == pytest.approx(expected_s)\n''',
)
