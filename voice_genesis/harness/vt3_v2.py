"""VT-3 v2: Grip Matrix v2（design memo §A, §F）。

2 構成で測定し、計器修復・grip 再定義の寄与とレンダラ改修の寄与を分離する:
  (1) baseline再測: R0 (v0, 無改変)   x 修復済み計器 (measure_v2) x grip v2
  (2) 改修後:       R0.1              x 修復済み計器 (measure_v2) x grip v2

grip v2（§A）:
  - E_note(f) = |value(sweep_max) - value(sweep_min)| / ref_scale(f)
    （value は §A-1 の単位系に変換済みの特徴量。§A-1 の凍結定数表を使用）
  - E(f) = median over probe notes of E_note(f)
  - grip(axis) = E(intended) / max(max_j E(side_j), 1.0)
  - gate: grip>=3.0 かつ direction_consistency>=0.90 かつ E(intended)>=2.0
  - 計器分解能開示（§A-4）: 各 probe x 中央 sweep 点で Genome 固定・
    jitter/noise seed のみ変えた 3 反復レンダから σ_meas を推定し、
    |ΔF| < 3σ_meas の intended 効果に below_instrument_resolution を付す。
    F0 トラックのオクターブ跳躍系外れ値棄却が目立つセルには
    measured_with_caveat を付す。
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Dict

import numpy as np

import measure_v2 as m2
import voice_r0 as r0
import voice_r0_1 as r01

WORK = Path(__file__).parent
RESULTS = WORK / "results_v2"
RESULTS.mkdir(exist_ok=True)

PROBE_MIDIS = {"C3": 48, "E4": 64, "A4": 69, "C5": 72, "C6": 84}
FEATURE_NAMES = ["mean_f0", "spectral_centroid", "spectral_tilt", "periodicity", "rms", "vibrato_depth"]
TRANSFORM_KEY = m2.FEATURE_TRANSFORM_KEYS
REF_SCALE = m2.REF_SCALE

# v0.2 と同一の sweep 値域（design memo §A-2: 「軸ごとに sweep 5 点（v0.2 と同一の値域）」）
AXES = {
    "breathiness": {
        "intended_feature": "periodicity",
        "sweep_values": [0.0, 0.1, 0.2, 0.35, 0.5],
        "apply": lambda genome, v: _set_noise(genome, base_breathiness=v),
    },
    "formant_scale": {
        "intended_feature": "spectral_centroid",
        "sweep_values": [0.85, 0.925, 1.0, 1.075, 1.15],
        "apply": lambda genome, v: _set_resonance(genome, formant_scale=v),
    },
    "spectral_tilt": {
        "intended_feature": "spectral_tilt",
        "sweep_values": [-18.0, -14.0, -10.0, -6.0, -2.0],
        "apply": lambda genome, v: _set_source(genome, base_tilt_db_per_oct=v),
    },
    "vibrato_depth": {
        "intended_feature": "vibrato_depth",
        "sweep_values": [0.0, 25.0, 50.0, 75.0, 100.0],
        "apply": lambda genome, v: _set_micro(genome, vibrato_depth_cents=v),
    },
}

MID_SWEEP_INDEX = 2
N_REPEATS = 3
REPEAT_SEED_OFFSETS = [0, 1009, 2003]


def _set_noise(genome, **kwargs):
    g = copy.deepcopy(genome)
    g.noise = replace(g.noise, **kwargs)
    return g


def _set_resonance(genome, **kwargs):
    g = copy.deepcopy(genome)
    g.resonance = replace(g.resonance, **kwargs)
    return g


def _set_source(genome, **kwargs):
    g = copy.deepcopy(genome)
    g.source = replace(g.source, **kwargs)
    return g


def _set_micro(genome, **kwargs):
    g = copy.deepcopy(genome)
    g.microprosody = replace(g.microprosody, **kwargs)
    return g


def _with_seed_offset(genome, offset: int):
    g = copy.deepcopy(genome)
    g.microprosody = replace(g.microprosody, jitter_seed=g.microprosody.jitter_seed + offset)
    return g


def _transformed_dict(feat: m2.FeaturesV2) -> Dict[str, float]:
    d = m2.features_v2_to_dict(feat)
    return {name: d[TRANSFORM_KEY[name]] for name in FEATURE_NAMES}


def build_axis_data(config_label: str, render_fn, voice_fn, axis_name: str, axis_spec: dict, sr: int) -> dict:
    sweep_values = axis_spec["sweep_values"]
    n_sweep, n_probe = len(sweep_values), len(PROBE_MIDIS)

    # 主 sweep（各セル 1 レンダ、シード変更なし）
    matrices = {feat: np.full((n_sweep, n_probe), np.nan) for feat in FEATURE_NAMES}
    caveat_matrix = np.zeros((n_sweep, n_probe), dtype=bool)

    base_genome = voice_fn()
    for si, v in enumerate(sweep_values):
        genome = axis_spec["apply"](base_genome, v)
        for pi, (probe_name, midi) in enumerate(PROBE_MIDIS.items()):
            sig = render_fn(genome, midi)
            feats = m2.extract_all_features_v2(sig, sr=sr)
            td = _transformed_dict(feats)
            for feat in FEATURE_NAMES:
                matrices[feat][si, pi] = td[feat]
            caveat_matrix[si, pi] = feats.measured_with_caveat

    # 計器分解能: 中央 sweep 点で probe ごとに 3 反復レンダ
    mid_value = sweep_values[MID_SWEEP_INDEX]
    mid_genome = axis_spec["apply"](base_genome, mid_value)
    sigma_meas_per_probe: Dict[str, Dict[str, float]] = {}
    for probe_name, midi in PROBE_MIDIS.items():
        reps = {feat: [] for feat in FEATURE_NAMES}
        for offset in REPEAT_SEED_OFFSETS:
            g_rep = _with_seed_offset(mid_genome, offset)
            sig = render_fn(g_rep, midi)
            feats = m2.extract_all_features_v2(sig, sr=sr)
            td = _transformed_dict(feats)
            for feat in FEATURE_NAMES:
                reps[feat].append(td[feat])
        sigma_meas_per_probe[probe_name] = {feat: float(np.std(reps[feat], ddof=1)) for feat in FEATURE_NAMES}

    return {
        "config": config_label,
        "axis": axis_name,
        "matrices": matrices,
        "caveat_matrix": caveat_matrix,
        "sigma_meas_per_probe": sigma_meas_per_probe,
        "sweep_values": sweep_values,
    }


def compute_grip(axis_data: dict, axis_spec: dict) -> dict:
    matrices = axis_data["matrices"]
    intended = axis_spec["intended_feature"]
    side_features = [f for f in FEATURE_NAMES if f != intended]
    n_sweep = matrices[intended].shape[0]

    # E_note / E(f)
    E_note = {feat: (matrices[feat][n_sweep - 1, :] - matrices[feat][0, :]) for feat in FEATURE_NAMES}
    abs_E_note = {feat: np.abs(v) / REF_SCALE[feat] for feat, v in E_note.items()}
    E = {feat: float(np.median(abs_E_note[feat])) for feat in FEATURE_NAMES}

    E_intended = E[intended]
    E_side = {f: E[f] for f in side_features}
    max_side = max(E_side.values()) if E_side else 0.0
    dominant_side = max(E_side, key=E_side.get) if E_side else None

    grip_ratio = E_intended / max(max_side, 1.0)

    # 方向一致率（probe ごとの符号一致、intended feature、transform 済み値ベース）
    mat = matrices[intended]
    matches, total = 0, 0
    for pi in range(mat.shape[1]):
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
    direction_consistency = matches / total if total > 0 else float("nan")

    gate_grip = grip_ratio >= 3.0
    gate_direction = np.isfinite(direction_consistency) and direction_consistency >= 0.90
    gate_effect = E_intended >= 2.0
    overall_gate_pass = bool(gate_grip and gate_direction and gate_effect)

    # 計器分解能開示（§A-4）
    sigma_meas_per_probe = axis_data["sigma_meas_per_probe"]
    below_resolution_per_probe = {}
    for pi, probe_name in enumerate(PROBE_MIDIS.keys()):
        delta_f_raw = abs(matrices[intended][n_sweep - 1, pi] - matrices[intended][0, pi])
        sigma = sigma_meas_per_probe[probe_name][intended]
        below_resolution_per_probe[probe_name] = bool(delta_f_raw < 3.0 * sigma) if np.isfinite(sigma) else None
    any_below_resolution = any(v for v in below_resolution_per_probe.values() if v is not None)

    caveat_matrix = axis_data["caveat_matrix"]
    n_caveat_cells = int(caveat_matrix.sum())

    return {
        "config": axis_data["config"],
        "axis": axis_data["axis"],
        "intended_feature": intended,
        "side_features": side_features,
        "E_intended": round(E_intended, 4),
        "E_side": {f: round(v, 4) for f, v in E_side.items()},
        "dominant_side_feature": dominant_side,
        "grip_ratio": round(grip_ratio, 4),
        "direction_consistency": round(direction_consistency, 4) if np.isfinite(direction_consistency) else None,
        "gate_grip_ge_3.0": bool(gate_grip),
        "gate_direction_ge_0.90": bool(gate_direction),
        "gate_effect_ge_2.0": bool(gate_effect),
        "overall_gate_pass": overall_gate_pass,
        "below_instrument_resolution_per_probe": below_resolution_per_probe,
        "any_below_instrument_resolution": any_below_resolution,
        "sigma_meas_per_probe": {p: {f: round(v, 5) for f, v in d.items()} for p, d in sigma_meas_per_probe.items()},
        "n_caveat_cells_of_25": n_caveat_cells,
        "caveat_matrix": caveat_matrix.tolist(),
        "raw_feature_matrices": {feat: axis_data["matrices"][feat].round(4).tolist() for feat in FEATURE_NAMES},
        "sweep_values": axis_data["sweep_values"],
        "probe_notes": PROBE_MIDIS,
    }


def main() -> None:
    t0 = time.time()
    configs = [
        ("config1_v0_repaired", r0.render_note, r0.voice_a, r0.SR),
        ("config2_R0.1_repaired", r01.render_note, r01.voice_a, r01.SR),
    ]

    all_results = []
    for config_label, render_fn, voice_fn, sr in configs:
        print(f"\n########## {config_label} ##########")
        for axis_name, axis_spec in AXES.items():
            print(f"=== axis: {axis_name} (intended={axis_spec['intended_feature']}) ===")
            axis_data = build_axis_data(config_label, render_fn, voice_fn, axis_name, axis_spec, sr)
            grip = compute_grip(axis_data, axis_spec)
            all_results.append(grip)
            print(
                f"  E(intended)={grip['E_intended']} grip_ratio={grip['grip_ratio']} "
                f"direction={grip['direction_consistency']} gate_pass={grip['overall_gate_pass']} "
                f"dominant_side={grip['dominant_side_feature']} caveat_cells={grip['n_caveat_cells_of_25']}/25"
            )

    report = {
        "probe_suite": PROBE_MIDIS,
        "feature_names": FEATURE_NAMES,
        "ref_scale": REF_SCALE,
        "gate_definition": "grip_ratio>=3.0 and direction_consistency>=0.90 and E(intended)>=2.0",
        "n_repeats_for_sigma_meas": N_REPEATS,
        "configs": ["config1_v0_repaired", "config2_R0.1_repaired"],
        "results": all_results,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "grip_report_v2.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nVT-3 v2 elapsed={report['elapsed_sec']}s")
    for r in all_results:
        print(f"{r['config']:24s} {r['axis']:16s} grip={r['grip_ratio']:>8} gate={r['overall_gate_pass']}")


if __name__ == "__main__":
    main()
