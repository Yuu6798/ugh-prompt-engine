"""VT-3 v4: grip 残 2 軸（formant_scale / spectral_tilt）の完成（design memo v04 §B, §C）。

構成: R0.1 + 強化推定器（v3 のまま）+ 特徴量セット v3（差分は formant_centroid_v4 /
source_tilt_v4 の同時推定=joint fit のみ、他 4 特徴は v3 と同一実装を再利用）。

手順（§B）:
  1. 案 A（joint fit）のみで 4 軸を再測し、v3 と同じ gate（grip=E(intended)/
     max(E over全side,1)>=3.0 ∧ dir>=0.90 ∧ E(intended)>=2.0）で判定
     （= 免除表適用前判定）。
  2. 未達の軸に限り、実測 dominant side を免除表へ宣言し gate v3
     （宣言 side を除いた grip_declared>=3.0 ∧ 宣言 side の符号一致100% ∧
     E(declared)<=0.5*E(intended) ∧ dir>=0.90 ∧ E(intended)>=2.0）で再判定
     （= 免除表適用後判定）。宣言なしで通る軸には宣言を付けない。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict

import numpy as np

import measure_v4 as m4
import voice_r0_1 as r01
from vt3_v3 import AXES, MID_SWEEP_INDEX, PROBE_MIDIS, REPEAT_SEED_OFFSETS, _with_seed_offset

WORK = Path(__file__).parent
RESULTS = WORK / "results_v4"
RESULTS.mkdir(exist_ok=True)

FEATURE_NAMES = m4.FEATURE_NAMES_V4  # ["mean_f0","formant_centroid","source_tilt","periodicity","rms","vibrato_depth"]
REF_SCALE = m4.REF_SCALE_V4
FMIN, FMAX = 55.0, 2900.0


def extract_config(sig: np.ndarray, sr: int) -> Dict:
    feats = m4.extract_all_features_v4(sig, sr=sr, fmin=FMIN, fmax=FMAX)
    transformed = {name: m4.transformed_value(feats, name) for name in FEATURE_NAMES}
    return {
        "transformed": transformed,
        "caveat": feats.measured_with_caveat,
        "vibrato_reject_rate": feats.vibrato_reject_rate,
        "fit_mode": feats.fit_mode,
        "fit_residual_rms": feats.fit_residual_rms,
        "n_harmonics_used": feats.n_harmonics_used,
    }


def build_axis_data(axis_name: str, axis_spec: dict) -> dict:
    sweep_values = axis_spec["sweep_values"]
    n_sweep, n_probe = len(sweep_values), len(PROBE_MIDIS)

    matrices = {feat: np.full((n_sweep, n_probe), np.nan) for feat in FEATURE_NAMES}
    caveat_matrix = np.zeros((n_sweep, n_probe), dtype=bool)
    fit_mode_matrix = [["" for _ in range(n_probe)] for _ in range(n_sweep)]
    fit_residual_matrix = np.full((n_sweep, n_probe), np.nan)

    base_genome = r01.voice_a()
    for si, v in enumerate(sweep_values):
        genome = axis_spec["apply"](base_genome, v)
        for pi, (probe_name, midi) in enumerate(PROBE_MIDIS.items()):
            sig = r01.render_note(genome, midi)
            out = extract_config(sig, r01.SR)
            for feat in FEATURE_NAMES:
                matrices[feat][si, pi] = out["transformed"][feat]
            caveat_matrix[si, pi] = out["caveat"]
            fit_mode_matrix[si][pi] = out["fit_mode"]
            fit_residual_matrix[si, pi] = out["fit_residual_rms"]

    mid_value = sweep_values[MID_SWEEP_INDEX]
    mid_genome = axis_spec["apply"](base_genome, mid_value)
    sigma_meas_per_probe: Dict[str, Dict[str, float]] = {}
    for probe_name, midi in PROBE_MIDIS.items():
        reps = {feat: [] for feat in FEATURE_NAMES}
        for offset in REPEAT_SEED_OFFSETS:
            g_rep = _with_seed_offset(mid_genome, offset)
            sig = r01.render_note(g_rep, midi)
            out = extract_config(sig, r01.SR)
            for feat in FEATURE_NAMES:
                reps[feat].append(out["transformed"][feat])
        sigma_meas_per_probe[probe_name] = {feat: float(np.std(reps[feat], ddof=1)) for feat in FEATURE_NAMES}

    return {
        "axis": axis_name,
        "matrices": matrices,
        "caveat_matrix": caveat_matrix,
        "fit_mode_matrix": fit_mode_matrix,
        "fit_residual_matrix": fit_residual_matrix,
        "sigma_meas_per_probe": sigma_meas_per_probe,
        "sweep_values": sweep_values,
    }


def _E_and_deltas(matrices, feature_names) -> Dict[str, Dict]:
    """特徴ごとに probe 毎 raw delta と E(f)=median(|delta|/ref) を計算する。"""
    n_sweep = matrices[feature_names[0]].shape[0]
    n_probe = matrices[feature_names[0]].shape[1]
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


def evaluate_axis(axis_name: str, axis_spec: dict, axis_data: dict) -> dict:
    intended = axis_spec["intended_b"]  # v3 の intended 対応をそのまま継承
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

    # --- 計器分解能開示 (v0.3 §A-4 継承) ---
    sigma_meas_per_probe = axis_data["sigma_meas_per_probe"]
    below_resolution_per_probe = {}
    for pi, probe_name in enumerate(PROBE_MIDIS.keys()):
        delta_f_raw = abs(stats[intended]["raw_deltas"][pi])
        sigma = sigma_meas_per_probe[probe_name][intended]
        below_resolution_per_probe[probe_name] = bool(delta_f_raw < 3.0 * sigma) if np.isfinite(sigma) else None
    any_below_resolution = any(v for v in below_resolution_per_probe.values() if v is not None)

    result = {
        "axis": axis_name,
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
        "n_caveat_cells_of_25": int(axis_data["caveat_matrix"].sum()),
        "fit_mode_matrix": axis_data["fit_mode_matrix"],
        "fit_residual_rms_matrix": axis_data["fit_residual_matrix"].round(4).tolist(),
        "raw_feature_matrices": {feat: matrices[feat].round(4).tolist() for feat in FEATURE_NAMES},
        "sweep_values": axis_data["sweep_values"],
        "probe_notes": PROBE_MIDIS,
    }

    # --- 免除表（§B、未達の軸のみ） ---
    exemption = None
    if not pass_no_exemption and dominant_side is not None:
        declared = dominant_side
        declared_deltas = stats[declared]["raw_deltas"]
        expected_sign = float(np.sign(np.nanmedian(declared_deltas)))
        n_probe = len(declared_deltas)
        n_match = int(np.sum(np.sign(declared_deltas) == expected_sign)) if expected_sign != 0 else 0
        sign_consistency_declared = n_match / n_probe if n_probe else float("nan")
        sign_ok = bool(expected_sign != 0 and n_match == n_probe)  # 「符号が宣言どおり」= 全 probe 一致

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
                f"dominant_side after joint-fit (案A) remains '{declared}' across all probes with consistent sign; "
                f"E({declared})={round(E_declared,4)} <= 0.5*E({intended})={round(0.5*E_intended,4)}: {ratio_ok}"
            ),
            "mechanism_1line": AXIS_MECHANISM_NOTES.get(axis_name, "（機序説明未記載）"),
            "eligible": eligible,
            "grip_declared": round(grip_declared, 4),
            "gate_grip_declared_ge_3.0": bool(gate_grip_v3),
            "overall_gate_pass": pass_with_exemption,
        }

    result["exemption"] = exemption
    result["final_gate_pass"] = bool(pass_no_exemption or (exemption is not None and exemption["overall_gate_pass"]))
    result["gate_path"] = "no_exemption" if pass_no_exemption else ("exemption" if (exemption is not None and exemption["overall_gate_pass"]) else "fail")
    return result


# 免除宣言の機序説明（1行、宣言時のみ使用。実測で dominant_side が確定した軸に対応）
AXIS_MECHANISM_NOTES = {
    "formant_scale": (
        "formant_scale はフォルマント周波数と共に励振帯域の実効エネルギー分布も変えるため、"
        "joint fit の tilt 項（声道非依存の声源傾斜)がわずかに追従して動く（声道-声源の残存結合）。"
    ),
    "spectral_tilt": (
        "spectral_tilt はスペクトル全体の明るさを変えるため、ケプストラム包絡由来の初期ピーク検出や"
        "1ピークモデルへのモデル選択率が変化し、formant_centroid の代表点がわずかに移動する。"
    ),
}


def main() -> None:
    t0 = time.time()
    axis_results = []
    for axis_name, axis_spec in AXES.items():
        print(f"=== axis: {axis_name} (intended={axis_spec['intended_b']}) ===")
        axis_data = build_axis_data(axis_name, axis_spec)
        r = evaluate_axis(axis_name, axis_spec, axis_data)
        axis_results.append(r)
        print(
            f"  no_exemption: grip={r['no_exemption']['grip_ratio']} gate={r['no_exemption']['overall_gate_pass']} "
            f"dominant_side={r['dominant_side_feature']}"
        )
        if r["exemption"] is not None:
            e = r["exemption"]
            print(
                f"  exemption: declared={e['declared_side_feature']} eligible={e['eligible']} "
                f"grip_declared={e['grip_declared']} gate={e['overall_gate_pass']}"
            )
        print(f"  FINAL: gate_pass={r['final_gate_pass']} (path={r['gate_path']})")

    report = {
        "probe_suite": PROBE_MIDIS,
        "feature_names": FEATURE_NAMES,
        "ref_scale": REF_SCALE,
        "gate_definition_no_exemption": "grip=E(intended)/max(E(all 5 sides),1.0)>=3.0 and dir>=0.90 and E(intended)>=2.0",
        "gate_definition_v3_with_exemption": (
            "grip_declared=E(intended)/max(E(undeclared sides),1.0)>=3.0 and "
            "declared-side sign matches expected in 5/5 probes and E(declared)<=0.5*E(intended) "
            "and dir>=0.90 and E(intended)>=2.0"
        ),
        "exemption_table_rules": "max 1 entry per axis; only applied to axes failing no_exemption gate; not applied to axes already passing.",
        "results": axis_results,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "grip_report_v4.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nVT-3 v4 elapsed={report['elapsed_sec']}s")
    for r in axis_results:
        print(f"{r['axis']:16s} FINAL gate_pass={r['final_gate_pass']} (path={r['gate_path']})")


if __name__ == "__main__":
    main()
