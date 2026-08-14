"""VT-2: Phase 0 Measurement Bench ゲート模擬。

設計書 §9 Phase 0 ゲート「推定器が C2-C7 全域で合成 GT に対し許容誤差内」を
模擬する。3 半音刻み (MIDI 36,39,...,96 の 21 ノート) x voice_A で合成 GT を
生成し、(a) 自前 F0 推定器 (measure.py, YIN 式差分関数)、(b) librosa.pyin を
fmin=50/fmax=1000（設計書 §9 が例示する既存分析系の帯域）、(c) librosa.pyin
を fmin=60/fmax=2200（全帯域）の 3 通りで比較する。
"""
from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import librosa
import numpy as np

from measure import cents_error, estimate_f0_hps
from voice_r0 import SR, render_note, voice_a

WORK = Path(__file__).parent
RESULTS = WORK / "results"
RESULTS.mkdir(exist_ok=True)

BENCH_MIDIS = list(range(36, 97, 3))  # 36, 39, ..., 96 (21 notes)


def pyin_estimate(sig: np.ndarray, sr: int, fmin: float, fmax: float) -> float:
    """librosa.pyin のフレーム毎推定を有声区間の中央値に集約する。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        f0_track, voiced_flag, voiced_prob = librosa.pyin(
            sig,
            fmin=fmin,
            fmax=fmax,
            sr=sr,
            frame_length=2048,
        )
    valid = f0_track[voiced_flag & np.isfinite(f0_track)]
    if len(valid) == 0:
        return float("nan")
    return float(np.median(valid))


def main() -> None:
    t0 = time.time()
    genome = voice_a()
    rows = []
    for midi in BENCH_MIDIS:
        true_f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        sig = render_note(genome, midi)

        custom_est = estimate_f0_hps(sig, sr=SR, fmin=55.0, fmax=2200.0)
        custom_err = cents_error(custom_est, true_f0)

        pyin_lim_est = pyin_estimate(sig, SR, fmin=50.0, fmax=1000.0)
        pyin_lim_err = cents_error(pyin_lim_est, true_f0)

        pyin_full_est = pyin_estimate(sig, SR, fmin=60.0, fmax=2200.0)
        pyin_full_err = cents_error(pyin_full_est, true_f0)

        rows.append(
            {
                "midi": midi,
                "note_name": librosa.midi_to_note(midi),
                "true_f0": round(true_f0, 4),
                "custom_est": None if not np.isfinite(custom_est) else round(custom_est, 4),
                "custom_cents_err": None if not np.isfinite(custom_err) else round(custom_err, 2),
                "pyin_limited_est": None if not np.isfinite(pyin_lim_est) else round(pyin_lim_est, 4),
                "pyin_limited_cents_err": None if not np.isfinite(pyin_lim_err) else round(pyin_lim_err, 2),
                "pyin_full_est": None if not np.isfinite(pyin_full_est) else round(pyin_full_est, 4),
                "pyin_full_cents_err": None if not np.isfinite(pyin_full_err) else round(pyin_full_err, 2),
            }
        )
        print(
            f"midi={midi:3d} true={true_f0:8.2f} "
            f"custom_err={rows[-1]['custom_cents_err']} "
            f"pyin_lim_err={rows[-1]['pyin_limited_cents_err']} "
            f"pyin_full_err={rows[-1]['pyin_full_cents_err']}"
        )

    def _abs_or_none(v):
        return abs(v) if v is not None else None

    custom_errs = [abs(r["custom_cents_err"]) for r in rows if r["custom_cents_err"] is not None]
    pyin_lim_errs = [abs(r["pyin_limited_cents_err"]) for r in rows if r["pyin_limited_cents_err"] is not None]
    pyin_full_errs = [abs(r["pyin_full_cents_err"]) for r in rows if r["pyin_full_cents_err"] is not None]

    n_pyin_lim_nan = sum(1 for r in rows if r["pyin_limited_est"] is None)
    n_pyin_lim_above_c6 = sum(
        1 for r in rows if r["midi"] > 84 and r["pyin_limited_est"] is None
    )  # C6 = MIDI 84

    summary = {
        "bench_midis": BENCH_MIDIS,
        "sr": SR,
        "voice": "voice_A",
        "gate_tolerance_cents_examples": [20, 50, 100],
        "custom": {
            "n_valid": len(custom_errs),
            "n_total": len(rows),
            "median_abs_cents_err": round(float(np.median(custom_errs)), 2) if custom_errs else None,
            "max_abs_cents_err": round(float(np.max(custom_errs)), 2) if custom_errs else None,
            "pass_at_20c": sum(1 for e in custom_errs if e <= 20),
            "pass_at_50c": sum(1 for e in custom_errs if e <= 50),
            "pass_at_100c": sum(1 for e in custom_errs if e <= 100),
        },
        "pyin_limited_50_1000": {
            "n_valid": len(pyin_lim_errs),
            "n_total": len(rows),
            "n_nan_or_unvoiced": n_pyin_lim_nan,
            "n_nan_above_c6_midi84": n_pyin_lim_above_c6,
            "median_abs_cents_err": round(float(np.median(pyin_lim_errs)), 2) if pyin_lim_errs else None,
            "max_abs_cents_err": round(float(np.max(pyin_lim_errs)), 2) if pyin_lim_errs else None,
            "pass_at_20c": sum(1 for e in pyin_lim_errs if e <= 20),
            "pass_at_50c": sum(1 for e in pyin_lim_errs if e <= 50),
            "pass_at_100c": sum(1 for e in pyin_lim_errs if e <= 100),
        },
        "pyin_full_60_2200": {
            "n_valid": len(pyin_full_errs),
            "n_total": len(rows),
            "median_abs_cents_err": round(float(np.median(pyin_full_errs)), 2) if pyin_full_errs else None,
            "max_abs_cents_err": round(float(np.max(pyin_full_errs)), 2) if pyin_full_errs else None,
            "pass_at_20c": sum(1 for e in pyin_full_errs if e <= 20),
            "pass_at_50c": sum(1 for e in pyin_full_errs if e <= 50),
            "pass_at_100c": sum(1 for e in pyin_full_errs if e <= 100),
        },
        "notes": rows,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "bench_f0.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\nVT-2 elapsed={summary['elapsed_sec']}s")
    print(f"custom: median_abs={summary['custom']['median_abs_cents_err']} max_abs={summary['custom']['max_abs_cents_err']}")
    print(
        f"pyin_limited(50-1000): median_abs={summary['pyin_limited_50_1000']['median_abs_cents_err']} "
        f"max_abs={summary['pyin_limited_50_1000']['max_abs_cents_err']} "
        f"n_nan_above_C6={summary['pyin_limited_50_1000']['n_nan_above_c6_midi84']}"
    )
    print(
        f"pyin_full(60-2200): median_abs={summary['pyin_full_60_2200']['median_abs_cents_err']} "
        f"max_abs={summary['pyin_full_60_2200']['max_abs_cents_err']}"
    )


if __name__ == "__main__":
    main()
