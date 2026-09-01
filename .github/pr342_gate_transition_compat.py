from pathlib import Path

p = Path("voice_genesis/calibration/gates.py")
text = p.read_text()

old = '''    global_uncertainty_valid = _nonnegative_finite(u_rep) and _nonnegative_finite(u_proc)
    invalid_primary_budget_ids = sorted(
        i.instance_id
        for i in primary
        if not (
            _nonnegative_finite(i.ae)
            and _nonnegative_finite(i.u_gt)
            and _nonnegative_finite(i.u_num)
            and _positive_finite(i.e_use)
        )
    )
    budget_inputs_valid = global_uncertainty_valid and not invalid_primary_budget_ids
'''
new = '''    global_uncertainty_finite = math.isfinite(u_rep) and math.isfinite(u_proc)
    global_uncertainty_valid = _nonnegative_finite(u_rep) and _nonnegative_finite(u_proc)
    nonfinite_primary_budget_ids = sorted(
        i.instance_id
        for i in primary
        if not _all_finite((i.ae, i.u_gt, i.u_num, i.e_use))
    )
    invalid_primary_budget_ids = sorted(
        i.instance_id
        for i in primary
        if not (
            _nonnegative_finite(i.ae)
            and _nonnegative_finite(i.u_gt)
            and _nonnegative_finite(i.u_num)
            and _positive_finite(i.e_use)
        )
    )
    budget_inputs_valid = global_uncertainty_valid and not invalid_primary_budget_ids
'''
if text.count(old) != 1:
    raise RuntimeError("absolute budget setup match failed")
text = text.replace(old, new, 1)

old = '''    if not gate2:
        if g_values and not g_values_finite:
            reasons.append("gate2': G[i] contains non-finite value(s)")
        else:
            reasons.append("gate2': q95_i(G[i]) > 0 (or no eligible instance)")
'''
new = '''    if not gate2:
        if (not global_uncertainty_finite) or nonfinite_primary_budget_ids or (
            g_values and not g_values_finite
        ):
            reasons.append("gate2': G[i] contains non-finite value(s)")
        else:
            reasons.append("gate2': q95_i(G[i]) > 0 (or no eligible instance)")
'''
if text.count(old) != 1:
    raise RuntimeError("gate2 reason match failed")
text = text.replace(old, new, 1)

old = '''    if not gate_max:
        if g_values and not g_values_finite:
            reasons.append("gate_max': G[i] contains non-finite value(s)")
        else:
            reasons.append("gate_max': max_i(G[i]) > 0 (or no eligible instance)")
'''
new = '''    if not gate_max:
        if (not global_uncertainty_finite) or nonfinite_primary_budget_ids or (
            g_values and not g_values_finite
        ):
            reasons.append("gate_max': G[i] contains non-finite value(s)")
        else:
            reasons.append("gate_max': max_i(G[i]) > 0 (or no eligible instance)")
'''
if text.count(old) != 1:
    raise RuntimeError("gate max reason match failed")
text = text.replace(old, new, 1)

old = '''        invalid_budget_pair_ids = sorted(
            p.pair_id
            for p in pairs
            if not (
                _nonnegative_finite(p.ds)
                and _positive_finite(p.e_use_i0)
                and _positive_finite(p.e_use_ia)
            )
        )
        if not global_uncertainty_valid or invalid_budget_pair_ids:
            gate4 = False
            if invalid_budget_pair_ids:
                reasons.append(
                    f"gate4': axis {axis} has invalid nonnegative/positive budget pair(s): "
                    + ", ".join(invalid_budget_pair_ids)
                )
            continue
'''
new = '''        nonfinite_budget_pair_ids = sorted(
            p.pair_id
            for p in pairs
            if not _all_finite((p.ds, p.e_use_i0, p.e_use_ia))
        )
        invalid_budget_pair_ids = sorted(
            p.pair_id
            for p in pairs
            if not (
                _nonnegative_finite(p.ds)
                and _positive_finite(p.e_use_i0)
                and _positive_finite(p.e_use_ia)
            )
        )
        if not global_uncertainty_valid or invalid_budget_pair_ids:
            gate4 = False
            if (not global_uncertainty_finite) or nonfinite_budget_pair_ids:
                reasons.append(f"gate4': axis {axis} has non-finite margin(s)")
            elif invalid_budget_pair_ids:
                reasons.append(
                    f"gate4': axis {axis} has invalid nonnegative/positive budget pair(s): "
                    + ", ".join(invalid_budget_pair_ids)
                )
            continue
'''
if text.count(old) != 1:
    raise RuntimeError("gate4 budget reason match failed")
text = text.replace(old, new, 1)

p.write_text(text)
