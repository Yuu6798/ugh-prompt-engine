"""VT-3 v6: sweep 内計器凍結による grip 完成・最終試行（design memo v06 §A, §B）。

v5 で判明した共通根因: sweep 系列の途中で joint fit のモデル構造
（1peak/2peak 選択・ピーク位置）が切り替わり、特徴量の定義自体が不連続に
変わる。本サイクルは各 (axis, probe) の sweep 系列につき、**系列中央
sweep 点（index 2）で 1 度だけ** v4 の既存規則（BIC + F2/F1 妥当性ゲート、
無改変）でモデル構造を決定し、系列内の全 sweep 点と σ_meas 反復をその
凍結構造（次数固定・アンカー ±30% 局所窓固定・モデル選択再実行なし）で
再フィットする（measure_v6.py、measure_v4.py は無改変）。

band 割当（v5 のまま）: formant_scale / spectral_tilt は低音域 suite
{G2,C3,E3,G3,C4}。breathiness / vibrato_depth は v4/v5 で PASS 済みのため
re-state。免除表規約・gate 条件は v0.4/v0.5 のまま。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List

import numpy as np

import measure_v3 as m3
import measure_v6 as m6
import voice_r0_1 as r01
from vt3_v3 import AXES, MID_SWEEP_INDEX, REPEAT_SEED_OFFSETS, _with_seed_offset
from vt3_v4 import AXIS_MECHANISM_NOTES as V4_MECHANISM_NOTES
from vt3_v5 import BAND_ASSIGNMENT, OUT_OF_BAND_PROBES, PROBE_MIDIS_LOW

WORK = Path(__file__).parent
RESULTS = WORK / "results_v6"
RESULTS.mkdir(exist_ok=True)
RESULTS_V4 = WORK / "results_v4"

FEATURE_NAMES = m6.FEATURE_NAMES_V6
REF_SCALE = m6.REF_SCALE_V6
FMIN, FMAX = 55.0, 2900.0


def extract_config_frozen(sig: np.ndarray, sr: int, frozen: m6.FrozenStructure) -> Dict:
    feats = m6.extract_features_frozen(sig, sr, frozen, fmin=FMIN, fmax=FMAX)
    transformed = {name: m6.transformed_value(feats, name) for name in FEATURE_NAMES}
    return {
        "transformed": transformed,
        "caveat": feats.measured_with_caveat,
        "vibrato_reject_rate": feats.vibrato_reject_rate,
        "fit_mode": feats.fit_mode,
        "fit_residual_rms": feats.fit_residual_rms,
        "n_harmonics_used": feats.n_harmonics_used,
    }


def determine_frozen_structures_per_probe(axis_spec: dict, probe_midis: Dict[str, int]) -> Dict[str, m6.FrozenStructure]:
    """probe ごとに、sweep 中央点（index2）のレンダから凍結構造を1回だけ決定する。"""
    sweep_values = axis_spec["sweep_values"]
    mid_value = sweep_values[MID_SWEEP_INDEX]
    base_genome = r01.voice_a()
    mid_genome = axis_spec["apply"](base_genome, mid_value)

    frozen_per_probe: Dict[str, m6.FrozenStructure] = {}
    for probe_name, midi in probe_midis.items():
        sig_mid = r01.render_note(mid_genome, midi)
        f0_mid = m3.estimate_f0_v3(sig_mid, sr=r01.SR, fmin=FMIN, fmax=FMAX)
        frozen_per_probe[probe_name] = m6.determine_frozen_structure(sig_mid, r01.SR, f0_mid)
    return frozen_per_probe


def build_axis_data(axis_name: str, axis_spec: dict, probe_midis: Dict[str, int]) -> dict:
    sweep_values = axis_spec["sweep_values"]
    n_sweep, n_probe = len(sweep_values), len(probe_midis)

    frozen_per_probe = determine_frozen_structures_per_probe(axis_spec, probe_midis)

    matrices = {feat: np.full((n_sweep, n_probe), np.nan) for feat in FEATURE_NAMES}
    caveat_matrix = np.zeros((n_sweep, n_probe), dtype=bool)
    fit_mode_matrix = [["" for _ in range(n_probe)] for _ in range(n_sweep)]
    fit_residual_matrix = np.full((n_sweep, n_probe), np.nan)
    n_harmonics_matrix = np.full((n_sweep, n_probe), np.nan)

    base_genome = r01.voice_a()
    for si, v in enumerate(sweep_values):
        genome = axis_spec["apply"](base_genome, v)
        for pi, (probe_name, midi) in enumerate(probe_midis.items()):
            sig = r01.render_note(genome, midi)
            frozen = frozen_per_probe[probe_name]
            out = extract_config_frozen(sig, r01.SR, frozen)
            for feat in FEATURE_NAMES:
                matrices[feat][si, pi] = out["transformed"][feat]
            caveat_matrix[si, pi] = out["caveat"]
            fit_mode_matrix[si][pi] = out["fit_mode"]
            fit_residual_matrix[si, pi] = out["fit_residual_rms"]
            n_harmonics_matrix[si, pi] = out["n_harmonics_used"]

    mid_value = sweep_values[MID_SWEEP_INDEX]
    mid_genome = axis_spec["apply"](base_genome, mid_value)
    sigma_meas_per_probe: Dict[str, Dict[str, float]] = {}
    for probe_name, midi in probe_midis.items():
        frozen = frozen_per_probe[probe_name]
        reps = {feat: [] for feat in FEATURE_NAMES}
        for offset in REPEAT_SEED_OFFSETS:
            g_rep = _with_seed_offset(mid_genome, offset)
            sig = r01.render_note(g_rep, midi)
            out = extract_config_frozen(sig, r01.SR, frozen)
            for feat in FEATURE_NAMES:
                reps[feat].append(out["transformed"][feat])
        sigma_meas_per_probe[probe_name] = {feat: float(np.std(reps[feat], ddof=1)) for feat in FEATURE_NAMES}

    frozen_report = {
        probe_name: {
            "n_peaks": fs.n_peaks,
            "anchor_peaks_hz": [round(f, 2) for f in fs.anchor_peaks],
            "source_fit_mode_at_mid": fs.source_fit_mode,
            "source_residual_rms_at_mid": round(fs.source_residual_rms, 4) if np.isfinite(fs.source_residual_rms) else None,
            "source_n_harmonics_used_at_mid": fs.source_n_harmonics_used,
        }
        for probe_name, fs in frozen_per_probe.items()
    }

    return {
        "axis": axis_name,
        "matrices": matrices,
        "caveat_matrix": caveat_matrix,
        "fit_mode_matrix": fit_mode_matrix,
        "fit_residual_matrix": fit_residual_matrix,
        "n_harmonics_matrix": n_harmonics_matrix,
        "sigma_meas_per_probe": sigma_meas_per_probe,
        "sweep_values": sweep_values,
        "probe_midis": probe_midis,
        "frozen_structures": frozen_report,
    }


def _E_and_deltas(matrices, feature_names) -> Dict[str, Dict]:
    n_sweep = matrices[feature_names[0]].shape[0]
    out = {}
    for feat in feature_names:
        raw_deltas = matrices[feat][n_sweep - 1, :] - matrices[feat][0, :]
        E_note = np.abs(raw_deltas) / REF_SCALE[feat]
        out[feat] = {"raw_deltas": raw_deltas, "E_note": E_note, "E": float(np.nanmedian(E_note))}
    return out


def _direction_consistency(mat: np.ndarray) -> float:
    n_sweep, n_probe = mat.shape
    matches, total = 0, 0
    for pi in range(n_probe):
        col = mat[:, pi]
        if not np.all(np.isfinite(col)):
            continue
        overall_sign = np.sign(col[-1] - col[0])
        if overall_sign == 0:
            continue
        for d in np.diff(col):
            total += 1
            if np.sign(d) == overall_sign or d == 0:
                matches += 1
    return matches / total if total > 0 else float("nan")


def fit_mode_distribution(fit_mode_matrix: List[List[str]]) -> Dict[str, int]:
    dist: Dict[str, int] = {}
    for row in fit_mode_matrix:
        for mode in row:
            dist[mode] = dist.get(mode, 0) + 1
    return dist


def evaluate_axis(axis_name: str, axis_spec: dict, axis_data: dict) -> dict:
    intended = axis_spec["intended_b"]
    side_features = [f for f in FEATURE_NAMES if f != intended]
    matrices = axis_data["matrices"]
    stats = _E_and_deltas(matrices, FEATURE_NAMES)

    E_intended = stats[intended]["E"]
    E_side = {f: stats[f]["E"] for f in side_features}
    max_side_all = max(E_side.values()) if E_side else 0.0
    dominant_side = max(E_side, key=E_side.get) if E_side else None

    grip_no_exemption = E_intended / max(max_side_all, 1.0)
    direction_consistency = _direction_consistency(matrices[intended])

    gate_grip = grip_no_exemption >= 3.0
    gate_direction = np.isfinite(direction_consistency) and direction_consistency >= 0.90
    gate_effect = E_intended >= 2.0
    pass_no_exemption = bool(gate_grip and gate_direction and gate_effect)

    sigma_meas_per_probe = axis_data["sigma_meas_per_probe"]
    below_resolution_per_probe = {}
    for pi, probe_name in enumerate(axis_data["probe_midis"].keys()):
        delta_f_raw = abs(stats[intended]["raw_deltas"][pi])
        sigma = sigma_meas_per_probe[probe_name][intended]
        below_resolution_per_probe[probe_name] = bool(delta_f_raw < 3.0 * sigma) if np.isfinite(sigma) else None
    any_below_resolution = any(v for v in below_resolution_per_probe.values() if v is not None)

    fit_dist = fit_mode_distribution(axis_data["fit_mode_matrix"])
    n_cells = sum(fit_dist.values())
    n_2peak = sum(v for k, v in fit_dist.items() if k == "frozen_2peak")
    n_1peak = n_cells - n_2peak

    result = {
        "axis": axis_name,
        "band": BAND_ASSIGNMENT[axis_name]["band"],
        "band_rationale": BAND_ASSIGNMENT[axis_name]["note"],
        "probe_notes": axis_data["probe_midis"],
        "out_of_band_probes": OUT_OF_BAND_PROBES.get(axis_name, {}),
        "frozen_structures_per_probe": axis_data["frozen_structures"],
        "intended_feature": intended,
        "E_intended": round(E_intended, 4),
        "E_side": {f: round(v, 4) for f, v in E_side.items()},
        "dominant_side_feature": dominant_side,
        "direction_consistency": round(direction_consistency, 4) if np.isfinite(direction_consistency) else None,
        "no_exemption": {
            "grip_ratio": round(grip_no_exemption, 4),
            "gate_grip_ge_3.0": bool(gate_grip),
            "gate_direction_ge_0.90": bool(gate_direction),
            "gate_effect_ge_2.0": bool(gate_effect),
            "overall_gate_pass": pass_no_exemption,
        },
        "below_instrument_resolution_per_probe": below_resolution_per_probe,
        "any_below_instrument_resolution": any_below_resolution,
        "n_caveat_cells": int(axis_data["caveat_matrix"].sum()),
        "n_cells_total": n_cells,
        "fit_mode_distribution": fit_dist,
        "n_2peak_cells": n_2peak,
        "n_1peak_cells": n_1peak,
        "fit_mode_matrix": axis_data["fit_mode_matrix"],
        "fit_residual_rms_matrix": axis_data["fit_residual_matrix"].round(4).tolist(),
        "n_harmonics_matrix": axis_data["n_harmonics_matrix"].tolist(),
        "raw_feature_matrices": {feat: matrices[feat].round(4).tolist() for feat in FEATURE_NAMES},
        "sweep_values": axis_data["sweep_values"],
    }

    exemption = None
    if not pass_no_exemption and dominant_side is not None:
        declared = dominant_side
        declared_deltas = stats[declared]["raw_deltas"]
        expected_sign = float(np.sign(np.nanmedian(declared_deltas)))
        n_probe = len(declared_deltas)
        n_match = int(np.sum(np.sign(declared_deltas) == expected_sign)) if expected_sign != 0 else 0
        sign_consistency_declared = n_match / n_probe if n_probe else float("nan")
        sign_ok = bool(expected_sign != 0 and n_match == n_probe)

        E_declared = stats[declared]["E"]
        ratio_ok = bool(E_declared <= 0.5 * E_intended)

        undeclared_sides = [f for f in side_features if f != declared]
        E_undeclared = {f: stats[f]["E"] for f in undeclared_sides}
        max_side_undeclared = max(E_undeclared.values()) if E_undeclared else 0.0
        grip_declared = E_intended / max(max_side_undeclared, 1.0)

        eligible = bool(sign_ok and ratio_ok)
        gate_grip_v3 = grip_declared >= 3.0
        pass_with_exemption = bool(eligible and gate_grip_v3 and gate_direction and gate_effect)

        exemption = {
            "declared_side_feature": declared,
            "expected_sign": expected_sign,
            "sign_consistency_declared": round(sign_consistency_declared, 4) if np.isfinite(sign_consistency_declared) else None,
            "sign_ok_all_probes": sign_ok,
            "E_declared": round(E_declared, 4),
            "E_declared_le_half_E_intended": ratio_ok,
            "eligibility_evidence": (
                f"dominant_side after frozen-structure fit remains '{declared}' with sign consistency "
                f"{n_match}/{n_probe}; E({declared})={round(E_declared,4)} <= 0.5*E({intended})="
                f"{round(0.5*E_intended,4)}: {ratio_ok}"
            ),
            "mechanism_1line": V4_MECHANISM_NOTES.get(axis_name, "（機序説明未記載）"),
            "eligible": eligible,
            "grip_declared": round(grip_declared, 4),
            "gate_grip_declared_ge_3.0": bool(gate_grip_v3),
            "overall_gate_pass": pass_with_exemption,
        }

    result["exemption"] = exemption
    result["final_gate_pass"] = bool(pass_no_exemption or (exemption is not None and exemption["overall_gate_pass"]))
    result["gate_path"] = "no_exemption" if pass_no_exemption else ("exemption" if (exemption is not None and exemption["overall_gate_pass"]) else "fail")
    return result


def restate_from_v4(axis_name: str) -> dict:
    with open(RESULTS_V4 / "grip_report_v4.json") as f:
        v4_report = json.load(f)
    v4_axis = next(r for r in v4_report["results"] if r["axis"] == axis_name)
    restated = dict(v4_axis)
    restated["band"] = BAND_ASSIGNMENT[axis_name]["band"]
    restated["band_rationale"] = BAND_ASSIGNMENT[axis_name]["note"]
    restated["restated_from"] = "results_v4/grip_report_v4.json (v4で既にPASS。再測せずre-state。sweep内計器凍結は該当軸に影響しない)"
    restated["final_gate_pass"] = v4_axis.get("final_gate_pass", v4_axis["no_exemption"]["overall_gate_pass"])
    restated["gate_path"] = v4_axis.get("gate_path", "no_exemption")
    return restated


def main() -> None:
    t0 = time.time()
    axis_results = []

    for axis_name in ["breathiness", "vibrato_depth"]:
        print(f"=== axis: {axis_name} (re-stated from v4) ===")
        r = restate_from_v4(axis_name)
        axis_results.append(r)
        print(f"  RESTATED: gate_pass={r['final_gate_pass']} grip={r['no_exemption']['grip_ratio']}")

    for axis_name in ["formant_scale", "spectral_tilt"]:
        axis_spec = AXES[axis_name]
        probe_midis = PROBE_MIDIS_LOW
        print(f"=== axis: {axis_name} (band=low_register, frozen instrument) ===")
        axis_data = build_axis_data(axis_name, axis_spec, probe_midis)
        r = evaluate_axis(axis_name, axis_spec, axis_data)
        axis_results.append(r)
        print(f"  frozen structures: {axis_data['frozen_structures']}")
        print(
            f"  no_exemption: grip={r['no_exemption']['grip_ratio']} dir={r['direction_consistency']} gate={r['no_exemption']['overall_gate_pass']} "
            f"dominant_side={r['dominant_side_feature']} fit_mode_dist={r['fit_mode_distribution']}"
        )
        if r["exemption"] is not None:
            e = r["exemption"]
            print(f"  exemption: declared={e['declared_side_feature']} eligible={e['eligible']} grip_declared={e['grip_declared']} gate={e['overall_gate_pass']}")
        print(f"  FINAL: gate_pass={r['final_gate_pass']} (path={r['gate_path']})")

    report = {
        "band_assignment": {
            axis: {"band": v["band"], "probes": v["probes"], "rationale": v["note"]}
            for axis, v in BAND_ASSIGNMENT.items()
        },
        "out_of_band_probes": OUT_OF_BAND_PROBES,
        "feature_names": FEATURE_NAMES,
        "ref_scale": REF_SCALE,
        "gate_definition_no_exemption": "grip=E(intended)/max(E(all 5 sides),1.0)>=3.0 and dir>=0.90 and E(intended)>=2.0",
        "gate_definition_v3_with_exemption": (
            "grip_declared=E(intended)/max(E(undeclared sides),1.0)>=3.0 and "
            "declared-side sign matches expected in all probes and E(declared)<=0.5*E(intended) "
            "and dir>=0.90 and E(intended)>=2.0"
        ),
        "frozen_instrument_principle": (
            "For formant_scale/spectral_tilt, model structure (n_peaks, anchor peak "
            "positions) is decided once per probe at the mid sweep point (index 2) using "
            "unmodified measure_v4.joint_source_filter_fit (BIC + F2/F1 plausibility gate), "
            "then frozen across all 5 sweep points and the 3 sigma_meas repeats: fixed order, "
            "local search fixed at +/-30% of the frozen anchor, no re-run of model selection."
        ),
        "results": axis_results,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "grip_report_v6.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nVT-3 v6 elapsed={report['elapsed_sec']}s")
    n_pass = sum(1 for r in axis_results if r["final_gate_pass"])
    for r in axis_results:
        print(f"{r['axis']:16s} FINAL gate_pass={r['final_gate_pass']} (path={r['gate_path']})")
    print(f"total: {n_pass}/4 axes PASS")


if __name__ == "__main__":
    main()
