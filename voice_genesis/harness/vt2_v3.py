"""VT-2 v3: F0 推定器強化のゲート再判定（design memo v031 §B）。

R0.1 高音域回帰（MIDI 93/96）は「音響は正当・推定器の短周期域の脆弱性」と
判定されたため、レンダラは変更せず推定器を強化する（放物線補間は
measure.py に既存・スペクトル櫛によるオクターブ確定を measure_v3.py に
新規実装）。

gate: **v0 と R0.1 の両レンダラ**で、コア帯域 C2-C7 全ノート |err|<=100cent
（オクターブ誤り 0）かつ median<=20cent、カナリア PASS。
旧推定器（measure.py 単体）で通っていた v0 側を落とさないこと（非退行）。

探索範囲・カナリア構成・ゲート数値は v0.3 §E のまま凍結
（MIDI 36-99, fmax>=2800Hz, 4点純音カナリア, 許容誤差 30cent）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

import measure as m
import measure_v3 as m3
import voice_r0 as r0
import voice_r0_1 as r01

WORK = Path(__file__).parent
RESULTS = WORK / "results_v3"
RESULTS.mkdir(exist_ok=True)

BENCH_MIDIS = list(range(36, 100, 3))  # 36..99, 22 notes
CORE_BAND_MAX_MIDI = 96
FMIN = 55.0
FMAX = 2900.0
CANARY_FREQS_HZ = [82.4, 261.63, 987.77, 2489.0]
CANARY_TOLERANCE_CENTS = 30.0  # v0.3 §E 継承


def run_canary(sr: int) -> dict:
    duration = 1.5
    n = int(duration * sr)
    t = np.arange(n) / sr
    rows = []
    all_pass = True
    for f_true in CANARY_FREQS_HZ:
        sig = 0.2 * np.sin(2 * np.pi * f_true * t)
        fade_n = int(0.03 * sr)
        env = np.ones(n)
        env[:fade_n] = np.linspace(0, 1, fade_n)
        env[-fade_n:] = np.linspace(1, 0, fade_n)
        sig = sig * env
        est = m3.estimate_f0_v3(sig, sr=sr, fmin=FMIN, fmax=FMAX)
        err = m.cents_error(est, f_true)
        ok = bool(np.isfinite(err) and abs(err) <= CANARY_TOLERANCE_CENTS)
        all_pass = all_pass and ok
        rows.append({"true_f0": f_true, "est_f0": est, "cents_err": err, "pass": ok})
    return {"canary_pass": all_pass, "tolerance_cents": CANARY_TOLERANCE_CENTS, "tones": rows}


def run_bench(render_fn, voice_fn, sr: int, label: str) -> dict:
    genome = voice_fn()
    rows = []
    for midi in BENCH_MIDIS:
        true_f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        sig = render_fn(genome, midi)
        est = m3.estimate_f0_v3(sig, sr=sr, fmin=FMIN, fmax=FMAX)
        err = m.cents_error(est, true_f0)
        is_core = midi <= CORE_BAND_MAX_MIDI
        rows.append(
            {
                "midi": midi,
                "band": "core" if is_core else "margin",
                "true_f0": round(true_f0, 4),
                "est_f0": None if not np.isfinite(est) else round(est, 4),
                "cents_err": None if not np.isfinite(err) else round(err, 2),
                "octave_error": bool(np.isfinite(err) and abs(err) > 600.0),
            }
        )

    core_rows = [r for r in rows if r["band"] == "core"]
    margin_rows = [r for r in rows if r["band"] == "margin"]
    core_errs = [abs(r["cents_err"]) for r in core_rows if r["cents_err"] is not None]
    margin_errs = [abs(r["cents_err"]) for r in margin_rows if r["cents_err"] is not None]

    n_octave_errors_core = sum(1 for r in core_rows if r["octave_error"])
    max_abs_core = max(core_errs) if core_errs else None
    median_abs_core = float(np.median(core_errs)) if core_errs else None

    gate_no_octave_errors = n_octave_errors_core == 0 and all(
        (r["cents_err"] is not None and abs(r["cents_err"]) <= 100.0) for r in core_rows
    )
    gate_median = median_abs_core is not None and median_abs_core <= 20.0

    return {
        "renderer": label,
        "notes": rows,
        "core_band": {
            "n_notes": len(core_rows),
            "n_octave_errors": n_octave_errors_core,
            "max_abs_cents_err": round(max_abs_core, 2) if max_abs_core is not None else None,
            "median_abs_cents_err": round(median_abs_core, 2) if median_abs_core is not None else None,
            "gate_no_octave_errors_le100c": gate_no_octave_errors,
            "gate_median_le20c": gate_median,
            "gate_pass": bool(gate_no_octave_errors and gate_median),
        },
        "margin_band": {
            "n_notes": len(margin_rows),
            "max_abs_cents_err": round(max(margin_errs), 2) if margin_errs else None,
            "median_abs_cents_err": round(float(np.median(margin_errs)), 2) if margin_errs else None,
            "note": "measured のみ。gate 対象外（§E）",
        },
    }


def main() -> None:
    t0 = time.time()

    canary = run_canary(r01.SR)
    print(f"canary: pass={canary['canary_pass']}")
    for row in canary["tones"]:
        print(f"  f={row['true_f0']:8.2f} est={row['est_f0']:8.2f} err={row['cents_err']:7.2f}c pass={row['pass']}")

    bench_results = {}
    for label, mod in (("R0.1", r01), ("v0", r0)):
        print(f"\n--- bench renderer={label} ---")
        res = run_bench(mod.render_note, mod.voice_a, mod.SR, label)
        bench_results[label] = res
        cb = res["core_band"]
        print(f"  core_band: n_octave_errors={cb['n_octave_errors']} max_abs={cb['max_abs_cents_err']} median_abs={cb['median_abs_cents_err']} gate_pass={cb['gate_pass']}")
        for row in res["notes"]:
            print(f"    midi={row['midi']:3d} band={row['band']:6s} true={row['true_f0']:8.2f} est={row['est_f0']} err={row['cents_err']}")

    overall_gate_pass = bool(
        canary["canary_pass"] and bench_results["R0.1"]["core_band"]["gate_pass"] and bench_results["v0"]["core_band"]["gate_pass"]
    )

    # 非退行チェック: v0.3 時点で v0 レンダラは旧推定器で全数 <=100c だった（オクターブ誤りなし）
    v0_core_errs = [abs(r["cents_err"]) for r in bench_results["v0"]["notes"] if r["band"] == "core" and r["cents_err"] is not None]
    non_regression_v0_all_le100c = all(e <= 100.0 for e in v0_core_errs)

    summary = {
        "bench_midis": BENCH_MIDIS,
        "core_band_midi_range": [36, CORE_BAND_MAX_MIDI],
        "margin_band_midi_range": [CORE_BAND_MAX_MIDI + 1, 99],
        "search_fmin": FMIN,
        "search_fmax": FMAX,
        "estimator": "measure_v3 (parabolic interpolation [measure.py 既存] + spectral comb octave resolution [新規])",
        "canary": canary,
        "bench_by_renderer": bench_results,
        "gate_definition": "canary PASS and (v0 core gate) and (R0.1 core gate)",
        "overall_gate_pass": overall_gate_pass,
        "non_regression_checks": {
            "v0_core_band_all_le_100c": non_regression_v0_all_le100c,
            "v0.3_R0.1_had_octave_errors_at_midi_93_96": True,
            "v3_R0.1_octave_errors_remaining": bench_results["R0.1"]["core_band"]["n_octave_errors"],
        },
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "bench_f0_v3.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\nVT-2 v3 elapsed={summary['elapsed_sec']}s")
    print(f"overall_gate_pass={overall_gate_pass}")
    print(f"non_regression: v0 all core <=100c: {non_regression_v0_all_le100c}")


if __name__ == "__main__":
    main()
