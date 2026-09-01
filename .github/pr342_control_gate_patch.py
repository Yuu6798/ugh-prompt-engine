from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"anchor missing in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "voice_genesis/calibration/gates.py",
    '''    gate5 = (control_gate == "NOT_APPLICABLE") or (min_count_met and fdr0 == 0.0 and fnr1 == 0.0)\n    if not gate5:\n        reasons.append("gate5: FDR0/FNR1 not both zero, or min-count not met")\n''',
    '''    # The frozen campaign declaration in fixtures.controls is APPLICABLE for every\n    # fixture family.  `control_gate` is retained for API compatibility/audit input,\n    # but a runtime NOT_APPLICABLE value is not an authority boundary and cannot\n    # bypass the detection gate.  Supporting a genuine NOT_APPLICABLE construct\n    # requires an authenticated frozen declaration, which this campaign does not have.\n    control_gate_authorized = control_gate == "APPLICABLE"\n    gate5 = control_gate_authorized and min_count_met and fdr0 == 0.0 and fnr1 == 0.0\n    if not control_gate_authorized:\n        reasons.append(\n            "gate5: runtime control_gate exemption not authorized by frozen APPLICABLE declaration"\n        )\n    elif not gate5:\n        reasons.append("gate5: FDR0/FNR1 not both zero, or min-count not met")\n''',
)

replace_once(
    "voice_genesis/calibration/gates.py",
    '''    gate 5: FDR0 == 0 かつ FNR1 == 0（最小数条件付き。または control_gate\n            NOT_APPLICABLE で通過）\n''',
    '''    gate 5: FDR0 == 0 かつ FNR1 == 0（最小数条件付き）。current frozen\n            campaign は全 fixture family が APPLICABLE のため、runtime の\n            control_gate=NOT_APPLICABLE は bypass 権限として扱わない。\n''',
)

replace_once(
    "voice_genesis/calibration/tests/test_gates.py",
    '''def test_absolute_gates_gate5_not_applicable_passthrough() -> None:\n    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]\n    result = absolute_gates(\n        instances,\n        u_rep=0.0,\n        u_proc=0.0,\n        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},\n        declared_invariance_axes={"axis1"},\n        fdr0=0.9,\n        fnr1=0.9,\n        min_count_met=False,\n        control_gate="NOT_APPLICABLE",\n    )\n    assert result.gate5_detection is True\n''',
    '''def test_absolute_gates_runtime_not_applicable_cannot_bypass_frozen_controls() -> None:\n    \"\"\"All frozen fixture families declare control_gate=APPLICABLE.  A runtime\n    NOT_APPLICABLE argument must therefore fail closed instead of bypassing FDR/FNR\n    and minimum-count checks.\"\"\"\n    instances = [_instance("i1", ae=0.0, e=0.0, u_gt=0.0, u_num=0.0, e_use=1.0)]\n    result = absolute_gates(\n        instances,\n        u_rep=0.0,\n        u_proc=0.0,\n        invariance_pairs_by_axis={"axis1": _inv_pairs("axis1", 5)},\n        declared_invariance_axes={"axis1"},\n        fdr0=0.9,\n        fnr1=0.9,\n        min_count_met=False,\n        control_gate="NOT_APPLICABLE",\n    )\n    assert result.gate5_detection is False\n    assert result.passed is False\n    assert any("not authorized" in reason for reason in result.failure_reasons)\n''',
)
