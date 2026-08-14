"""VT-3 v3: Grip Matrix v3（design memo v031 §A, §D）。

2 構成（§C-2 発動時は 3 構成）で測定する:
  (a) R0.1 + 強化推定器（measure_v3, 放物線補間+スペクトル櫛オクターブ確定）
      + v0.3 旧特徴セット（spectral_centroid・broadband tilt）
      -> 推定器強化の単独寄与を caveat セル数で確認
  (b) R0.1 + 強化推定器 + v2 新特徴セット（formant_centroid / source_tilt）
      -> 本命。4 軸 gate 判定
  (c) 条件付き: R0.2 + 強化推定器 + v2 新特徴セット（§C-2 発動時のみ、
      vt3_v3_configC.py 相当を本ファイル実行後に別途追記する）

grip v2 の定義（gate・σ_meas・caveat 規約）は v0.3 §A を継承。§A-1 の
特徴量セットのみ v2（本メモ）に差替える。
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

import measure as m
import measure_v2 as m2
import measure_v3 as m3
import voice_r0_1 as r01

WORK = Path(__file__).parent
RESULTS = WORK / "results_v3"
RESULTS.mkdir(exist_ok=True)

PROBE_MIDIS = {"C3": 48, "E4": 64, "A4": 69, "C5": 72, "C6": 84}
FMIN, FMAX = 55.0, 2900.0

# v0.3 と同一の sweep 値域（memo v031 は「値域は変更しない」前提）
AXES = {
    "breathiness": {
        "intended_a": "periodicity",
        "intended_b": "periodicity",
        "sweep_values": [0.0, 0.1, 0.2, 0.35, 0.5],
        "apply": lambda genome, v: _set_noise(genome, base_breathiness=v),
    },
    "formant_scale": {
        "intended_a": "spectral_centroid",
        "intended_b": "formant_centroid",
        "sweep_values": [0.85, 0.925, 1.0, 1.075, 1.15],
        "apply": lambda genome, v: _set_resonance(genome, formant_scale=v),
    },
    "spectral_tilt": {
        "intended_a": "spectral_tilt",
        "intended_b": "source_tilt",
        "sweep_values": [-18.0, -14.0, -10.0, -6.0, -2.0],
        "apply": lambda genome, v: _set_source(genome, base_tilt_db_per_oct=v),
    },
    "vibrato_depth": {
        "intended_a": "vibrato_depth",
        "intended_b": "vibrato_depth",
        "sweep_values": [0.0, 25.0, 50.0, 75.0, 100.0],
        "apply": lambda genome, v: _set_micro(genome, vibrato_depth_cents=v),
    },
}

FEATURE_NAMES_A = ["mean_f0", "spectral_centroid", "spectral_tilt", "periodicity", "rms", "vibrato_depth"]
FEATURE_NAMES_B = m3.FEATURE_NAMES_V3  # ["mean_f0","formant_centroid","source_tilt","periodicity","rms","vibrato_depth"]

MID_SWEEP_INDEX = 2
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


# ---------------------------------------------------------------------------
# 特徴抽出: 構成 (a) 旧セット + 強化推定器
# ---------------------------------------------------------------------------


def extract_config_a(sig: np.ndarray, sr: int) -> Dict:
    """v0.3 旧特徴セット定義そのまま + F0 依存箇所のみ強化推定器（measure_v3）に差替え。

    - spectral_centroid: F0 非依存（measure.py 無改変で再利用、影響なし）
    - spectral_tilt: 定義（全倍音回帰、measure.spectral_tilt_db_per_oct）は
      不変、渡す f0 のみ強化推定器製に差替え
    - periodicity / vibrato_depth: v0.3 と同一定義（正規化自己相関/頑健MAD）
      は measure_v3.periodicity_track_v3 / vibrato_depth_robust_v3 が
      「定義は v0.3 のまま・内部 F0 のみ強化」を体現しているのでそのまま使う
    """
    f0 = m3.estimate_f0_v3(sig, sr=sr, fmin=FMIN, fmax=FMAX)
    centroid_hz = m.spectral_centroid_hz(sig, sr=sr)
    tilt = m.spectral_tilt_db_per_oct(sig, sr=sr, f0_hz=f0)
    ptrack = m3.periodicity_track_v3(sig, sr=sr, fmin=FMIN, fmax=FMAX)
    rms = m.rms_loudness(sig)
    vib = m3.vibrato_depth_robust_v3(sig, sr=sr, fmin=FMIN, fmax=FMAX)
    caveat = bool(vib.reject_rate > 0.05)

    mean_f0_cents = 1200.0 * np.log2(f0) if np.isfinite(f0) and f0 > 0 else float("nan")
    centroid_log2 = np.log2(centroid_hz) if np.isfinite(centroid_hz) and centroid_hz > 0 else float("nan")
    rms_db = 20.0 * np.log10(max(rms, 1e-12))

    transformed = {
        "mean_f0": mean_f0_cents,
        "spectral_centroid": centroid_log2,
        "spectral_tilt": tilt,
        "periodicity": ptrack.periodicity_db_median,
        "rms": rms_db,
        "vibrato_depth": vib.depth_cents,
    }
    return {"transformed": transformed, "caveat": caveat, "vibrato_reject_rate": vib.reject_rate}


def extract_config_b(sig: np.ndarray, sr: int) -> Dict:
    feats = m3.extract_all_features_v3(sig, sr=sr, fmin=FMIN, fmax=FMAX)
    transformed = {name: m3.transformed_value(feats, name) for name in FEATURE_NAMES_B}
    ref_scales = {name: m3.ref_scale_for(feats, name) for name in FEATURE_NAMES_B}
    return {
        "transformed": transformed,
        "caveat": feats.measured_with_caveat,
        "vibrato_reject_rate": feats.vibrato_reject_rate,
        "ref_scales": ref_scales,
        "tilt_estimator": feats.tilt_estimator,
        "f1_est_fallback": feats.f1_est_fallback,
    }


# ---------------------------------------------------------------------------
# 汎用 grip 計算（v0.3 §A の gate 定義を継承）
# ---------------------------------------------------------------------------


def build_axis_data(
    config_label: str,
    render_fn: Callable,
    voice_fn: Callable,
    axis_name: str,
    axis_spec: dict,
    sr: int,
    extractor: Callable,
    feature_names: List[str],
) -> dict:
    sweep_values = axis_spec["sweep_values"]
    n_sweep, n_probe = len(sweep_values), len(PROBE_MIDIS)

    matrices = {feat: np.full((n_sweep, n_probe), np.nan) for feat in feature_names}
    caveat_matrix = np.zeros((n_sweep, n_probe), dtype=bool)
    ref_scale_matrix: Dict[str, np.ndarray] = {feat: np.full((n_sweep, n_probe), np.nan) for feat in feature_names}

    base_genome = voice_fn()
    for si, v in enumerate(sweep_values):
        genome = axis_spec["apply"](base_genome, v)
        for pi, (probe_name, midi) in enumerate(PROBE_MIDIS.items()):
            sig = render_fn(genome, midi)
            out = extractor(sig, sr)
            for feat in feature_names:
                matrices[feat][si, pi] = out["transformed"][feat]
                if "ref_scales" in out:
                    ref_scale_matrix[feat][si, pi] = out["ref_scales"][feat]
            caveat_matrix[si, pi] = out["caveat"]

    mid_value = sweep_values[MID_SWEEP_INDEX]
    mid_genome = axis_spec["apply"](base_genome, mid_value)
    sigma_meas_per_probe: Dict[str, Dict[str, float]] = {}
    for probe_name, midi in PROBE_MIDIS.items():
        reps = {feat: [] for feat in feature_names}
        for offset in REPEAT_SEED_OFFSETS:
            g_rep = _with_seed_offset(mid_genome, offset)
            sig = render_fn(g_rep, midi)
            out = extractor(sig, sr)
            for feat in feature_names:
                reps[feat].append(out["transformed"][feat])
        sigma_meas_per_probe[probe_name] = {feat: float(np.std(reps[feat], ddof=1)) for feat in feature_names}

    return {
        "config": config_label,
        "axis": axis_name,
        "matrices": matrices,
        "ref_scale_matrix": ref_scale_matrix,
        "caveat_matrix": caveat_matrix,
        "sigma_meas_per_probe": sigma_meas_per_probe,
        "sweep_values": sweep_values,
    }


def compute_grip(axis_data: dict, axis_spec: dict, intended: str, side_features: List[str], ref_scale_const: Optional[Dict[str, float]]) -> dict:
    matrices = axis_data["matrices"]
    n_sweep = matrices[intended].shape[0]
    n_probe = matrices[intended].shape[1]

    def ref_scale_for_cell(feat: str, pi: int, si: int) -> float:
        if ref_scale_const is not None and feat in ref_scale_const:
            return ref_scale_const[feat]
        # 動的 ref_scale（source_tilt の regression/h1h2 切替）: そのセルの値を使う
        v = axis_data["ref_scale_matrix"][feat][si, pi]
        return float(v) if np.isfinite(v) else 1.5  # フォールバック（通常発生しない）

    E_note = {}
    for feat in [intended] + side_features:
        vals = []
        for pi in range(n_probe):
            raw_delta = abs(matrices[feat][n_sweep - 1, pi] - matrices[feat][0, pi])
            ref = ref_scale_for_cell(feat, pi, n_sweep - 1)  # sweep_max 側の estimator mode を代表として使う
            vals.append(raw_delta / ref if ref > 0 else float("nan"))
        E_note[feat] = np.array(vals)

    E = {feat: float(np.nanmedian(v)) for feat, v in E_note.items()}
    E_intended = E[intended]
    E_side = {f: E[f] for f in side_features}
    max_side = max(E_side.values()) if E_side else 0.0
    dominant_side = max(E_side, key=E_side.get) if E_side else None
    grip_ratio = E_intended / max(max_side, 1.0)

    mat = matrices[intended]
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
    direction_consistency = matches / total if total > 0 else float("nan")

    gate_grip = grip_ratio >= 3.0
    gate_direction = np.isfinite(direction_consistency) and direction_consistency >= 0.90
    gate_effect = E_intended >= 2.0
    overall_gate_pass = bool(gate_grip and gate_direction and gate_effect)

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
        "n_caveat_cells_of_25": n_caveat_cells,
        "caveat_matrix": caveat_matrix.tolist(),
        "raw_feature_matrices": {feat: axis_data["matrices"][feat].round(4).tolist() for feat in axis_data["matrices"]},
        "sweep_values": axis_data["sweep_values"],
        "probe_notes": PROBE_MIDIS,
    }


def run_matrix(config_label: str, render_fn, voice_fn, sr: int, extractor: Callable, feature_names: List[str], intended_key: str, ref_scale_const) -> List[dict]:
    results = []
    for axis_name, axis_spec in AXES.items():
        intended = axis_spec[intended_key]
        side_features = [f for f in feature_names if f != intended]
        print(f"=== {config_label} / axis={axis_name} (intended={intended}) ===")
        axis_data = build_axis_data(config_label, render_fn, voice_fn, axis_name, axis_spec, sr, extractor, feature_names)
        grip = compute_grip(axis_data, axis_spec, intended, side_features, ref_scale_const)
        results.append(grip)
        print(
            f"  E(intended)={grip['E_intended']} grip_ratio={grip['grip_ratio']} "
            f"direction={grip['direction_consistency']} gate_pass={grip['overall_gate_pass']} "
            f"dominant_side={grip['dominant_side_feature']} caveat={grip['n_caveat_cells_of_25']}/25"
        )
    return results


def main() -> None:
    t0 = time.time()

    results_a = run_matrix(
        "config_a_R0.1_enhanced_old_features", r01.render_note, r01.voice_a, r01.SR,
        extract_config_a, FEATURE_NAMES_A, "intended_a", m2.REF_SCALE,
    )
    results_b = run_matrix(
        "config_b_R0.1_enhanced_v2_features", r01.render_note, r01.voice_a, r01.SR,
        extract_config_b, FEATURE_NAMES_B, "intended_b", None,
    )

    report = {
        "probe_suite": PROBE_MIDIS,
        "feature_names_a": FEATURE_NAMES_A,
        "feature_names_b": FEATURE_NAMES_B,
        "ref_scale_a": m2.REF_SCALE,
        "ref_scale_b": m3.REF_SCALE_V3,
        "gate_definition": "grip_ratio>=3.0 and direction_consistency>=0.90 and E(intended)>=2.0",
        "configs": ["config_a_R0.1_enhanced_old_features", "config_b_R0.1_enhanced_v2_features"],
        "results": results_a + results_b,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "grip_report_v3.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nVT-3 v3 (configs a,b) elapsed={report['elapsed_sec']}s")
    for r in results_a + results_b:
        print(f"{r['config']:36s} {r['axis']:16s} grip={r['grip_ratio']:>8} gate={r['overall_gate_pass']}")

    # breathiness (config b) gate 判定を明示的に出力（§C の手順分岐判断材料）
    breathiness_b = next(r for r in results_b if r["axis"] == "breathiness")
    print(f"\nbreathiness (config b) gate_pass={breathiness_b['overall_gate_pass']} grip_ratio={breathiness_b['grip_ratio']}")
    if not breathiness_b["overall_gate_pass"]:
        print(">>> breathiness gate NOT met with A+B alone. §C-2 (R0.2) consideration required. <<<")


if __name__ == "__main__":
    main()
