"""VT-3: Grip Matrix（§7.2 感度比）の well-posedness 検証。

grip(theta_i) = |Δ intended_feature| / max_j |Δ side_feature_j|
（各特徴量は probe suite 上の z-score で正規化）
gate: grip_ratio >= 3.0 かつ sweep 全域での方向一致率 >= 90%

probe suite = {C3, E4, A4, C5, C6} の 5 ノート持続母音。
sweep する Genome 軸（各 5 点、voice_A ベース）:
  - breathiness (NoiseParams.base_breathiness)   intended: HNR
  - formant_scale (ResonanceParams.formant_scale) intended: spectral centroid
  - spectral tilt (SourceParams.base_tilt_db_per_oct) intended: 計測 tilt
  - vibrato depth (MicroprosodyParams.vibrato_depth_cents) intended: F0 変調深度

z-score の母集団定義は設計書で未規定 [UNDERSPEC-14] なので、本スクリプトは
2 通り（sweep 全体でプール / probe ノートごとに個別正規化）を両方計算し、
grip_report.json に併記して定義依存性を実測する。
"""
from __future__ import annotations

import copy
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Dict

import numpy as np

from measure import Features, extract_all_features
from voice_r0 import SR, render_note, voice_a

WORK = Path(__file__).parent
RESULTS = WORK / "results"
RESULTS.mkdir(exist_ok=True)

PROBE_MIDIS = {"C3": 48, "E4": 64, "A4": 69, "C5": 72, "C6": 84}
FEATURE_NAMES = [
    "mean_f0",
    "spectral_centroid",
    "spectral_tilt_db_per_oct",
    "hnr_db",
    "rms",
    "vibrato_depth_cents",
]

AXES = {
    "breathiness": {
        "intended_feature": "hnr_db",
        "sweep_values": [0.0, 0.1, 0.2, 0.35, 0.5],
        "apply": lambda genome, v: _set_noise(genome, base_breathiness=v),
    },
    "formant_scale": {
        "intended_feature": "spectral_centroid",
        "sweep_values": [0.85, 0.925, 1.0, 1.075, 1.15],
        "apply": lambda genome, v: _set_resonance(genome, formant_scale=v),
    },
    "spectral_tilt": {
        "intended_feature": "spectral_tilt_db_per_oct",
        "sweep_values": [-18.0, -14.0, -10.0, -6.0, -2.0],
        "apply": lambda genome, v: _set_source(genome, base_tilt_db_per_oct=v),
    },
    "vibrato_depth": {
        "intended_feature": "vibrato_depth_cents",
        "sweep_values": [0.0, 25.0, 50.0, 75.0, 100.0],
        "apply": lambda genome, v: _set_micro(genome, vibrato_depth_cents=v),
    },
}


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


def _features_dict(f: Features) -> Dict[str, float]:
    return {
        "mean_f0": f.mean_f0,
        "spectral_centroid": f.spectral_centroid,
        "spectral_tilt_db_per_oct": f.spectral_tilt_db_per_oct,
        "hnr_db": f.hnr_db,
        "rms": f.rms,
        "vibrato_depth_cents": f.vibrato_depth_cents,
    }


def build_axis_matrix(axis_name: str, axis_spec: dict) -> Dict[str, np.ndarray]:
    """axis_name の sweep x probe 全レンダから特徴行列 (5 sweep x 5 probe) を返す。"""
    base = voice_a()
    sweep_values = axis_spec["sweep_values"]
    matrices = {feat: np.full((len(sweep_values), len(PROBE_MIDIS)), np.nan) for feat in FEATURE_NAMES}

    for si, v in enumerate(sweep_values):
        genome = axis_spec["apply"](base, v)
        for pi, (probe_name, midi) in enumerate(PROBE_MIDIS.items()):
            sig = render_note(genome, midi)
            feats = _features_dict(extract_all_features(sig, sr=SR))
            for feat in FEATURE_NAMES:
                matrices[feat][si, pi] = feats[feat]

    return matrices


def zscore_sweep_wide(matrices: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    """母集団定義 A: 全 25 セル (5 sweep x 5 probe) をプールして z-score。"""
    z = {}
    for feat, mat in matrices.items():
        mu = np.nanmean(mat)
        sd = np.nanstd(mat)
        z[feat] = (mat - mu) / sd if sd > 1e-12 else np.zeros_like(mat)
    return z


def zscore_per_note(matrices: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    """母集団定義 B: probe ノートごとに、そのノートの sweep 5 点だけで z-score。"""
    z = {}
    for feat, mat in matrices.items():
        out = np.full_like(mat, np.nan)
        n_sweep, n_probe = mat.shape
        for pi in range(n_probe):
            col = mat[:, pi]
            mu = np.nanmean(col)
            sd = np.nanstd(col)
            out[:, pi] = (col - mu) / sd if sd > 1e-12 else np.zeros_like(col)
        z[feat] = out
    return z


def compute_delta_z_per_feature(z: Dict[str, np.ndarray]) -> Dict[str, float]:
    """特徴ごとに、probe 毎の sweep 端点間 Δz を計算し、probe 平均を返す。"""
    out = {}
    for feat, mat in z.items():
        n_sweep = mat.shape[0]
        per_probe_delta = mat[n_sweep - 1, :] - mat[0, :]
        out[feat] = float(np.nanmean(per_probe_delta))
    return out


def compute_direction_consistency(matrices: Dict[str, np.ndarray], intended_feature: str) -> float:
    """連続 sweep 点間の intended feature の符号一致率。

    全体トレンド方向 = sign(最終点 - 最初点)（probe ごと）を期待方向とし、
    5 probe x 4 区間 = 20 個の連続差分のうち、期待方向と一致する割合を返す。
    """
    mat = matrices[intended_feature]
    n_sweep, n_probe = mat.shape
    matches = 0
    total = 0
    for pi in range(n_probe):
        col = mat[:, pi]
        if not np.all(np.isfinite(col)):
            continue
        overall_sign = np.sign(col[-1] - col[0])
        if overall_sign == 0:
            continue
        diffs = np.diff(col)
        for d in diffs:
            total += 1
            if np.sign(d) == overall_sign or d == 0:
                matches += 1
    if total == 0:
        return float("nan")
    return matches / total


def run_axis(axis_name: str, axis_spec: dict) -> dict:
    matrices = build_axis_matrix(axis_name, axis_spec)
    intended = axis_spec["intended_feature"]
    side_features = [f for f in FEATURE_NAMES if f != intended]

    results_by_def = {}
    for def_name, zfunc in (("sweep_wide", zscore_sweep_wide), ("per_note", zscore_per_note)):
        z = zfunc(matrices)
        delta_z = compute_delta_z_per_feature(z)
        intended_delta = abs(delta_z[intended])
        side_deltas = {f: abs(delta_z[f]) for f in side_features}
        max_side = max(side_deltas.values()) if side_deltas else 0.0
        grip_ratio = intended_delta / max_side if max_side > 1e-9 else float("inf")
        results_by_def[def_name] = {
            "intended_delta_z": round(delta_z[intended], 4),
            "per_feature_delta_z": {f: round(v, 4) for f, v in delta_z.items()},
            "max_side_delta_z": round(max_side, 4),
            "dominant_side_feature": max(side_deltas, key=side_deltas.get) if side_deltas else None,
            "grip_ratio": round(grip_ratio, 4) if np.isfinite(grip_ratio) else None,
            "pass_3.0_gate": bool(grip_ratio >= 3.0) if np.isfinite(grip_ratio) else False,
        }

    direction_consistency = compute_direction_consistency(matrices, intended)

    primary = results_by_def["sweep_wide"]

    return {
        "axis": axis_name,
        "intended_feature": intended,
        "sweep_values": axis_spec["sweep_values"],
        "probe_notes": PROBE_MIDIS,
        "grip_ratio": primary["grip_ratio"],
        "direction_consistency": round(direction_consistency, 4) if np.isfinite(direction_consistency) else None,
        "pass_3.0_gate": primary["pass_3.0_gate"],
        "pass_90pct_direction_gate": bool(direction_consistency >= 0.9) if np.isfinite(direction_consistency) else False,
        "overall_gate_pass": bool(
            primary["pass_3.0_gate"] and np.isfinite(direction_consistency) and direction_consistency >= 0.9
        ),
        "per_feature_delta_z": primary["per_feature_delta_z"],
        "zscore_definition_comparison": {
            "sweep_wide": results_by_def["sweep_wide"],
            "per_note": results_by_def["per_note"],
            "grip_ratio_delta_sweepwide_minus_pernote": (
                round(results_by_def["sweep_wide"]["grip_ratio"] - results_by_def["per_note"]["grip_ratio"], 4)
                if results_by_def["sweep_wide"]["grip_ratio"] is not None
                and results_by_def["per_note"]["grip_ratio"] is not None
                else None
            ),
        },
        "raw_feature_matrices": {feat: matrices[feat].round(4).tolist() for feat in FEATURE_NAMES},
    }


def main() -> None:
    t0 = time.time()
    axis_results = []
    for axis_name, axis_spec in AXES.items():
        print(f"=== axis: {axis_name} (intended={axis_spec['intended_feature']}) ===")
        r = run_axis(axis_name, axis_spec)
        axis_results.append(r)
        print(
            f"  grip_ratio(sweep_wide)={r['grip_ratio']} "
            f"grip_ratio(per_note)={r['zscore_definition_comparison']['per_note']['grip_ratio']} "
            f"direction_consistency={r['direction_consistency']} "
            f"overall_gate_pass={r['overall_gate_pass']}"
        )

    report = {
        "probe_suite": PROBE_MIDIS,
        "feature_names": FEATURE_NAMES,
        "gate_definition": "grip_ratio >= 3.0 and direction_consistency >= 0.90",
        "zscore_definitions": {
            "sweep_wide": "5 sweep点 x 5 probe = 25 セルをプールして平均・標準偏差を計算",
            "per_note": "probe ノートごとに、そのノートの sweep 5 点だけで平均・標準偏差を計算",
        },
        "axes": axis_results,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "grip_report.json", "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"\nVT-3 elapsed={report['elapsed_sec']}s")
    for r in axis_results:
        print(f"{r['axis']}: gate_pass={r['overall_gate_pass']} grip_ratio={r['grip_ratio']}")


if __name__ == "__main__":
    main()
