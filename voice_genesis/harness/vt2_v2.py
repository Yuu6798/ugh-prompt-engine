"""VT-2 v2: Phase 0 ゲート数値凍結（design memo §E）。

検証帯域: MIDI 36-99（C2 - D#7 = 運用上端 C7 + 3 半音マージン）、3 半音刻み
（v1 と同一ストライド）。自前推定器（measure.py, 無改変で再利用）の探索上限
は 2800Hz 以上（本スクリプトでは fmax=2900Hz を指定）。

gate（コア帯域 C2-C7 = MIDI 36-96）:
  1. オクターブ誤り 0（全ノート |err| <= 100 cents）
  2. median |err| <= 20 cents
マージン帯（MIDI 97-99）は measured として記録するのみ（gate 対象外）。

カナリア要件（サイレント破綻対策）: レンダラ・Genome・register mixer 等の
複雑さを経由しない独立した既知信号（純音サイン波）を、bench 実行のたびに
合成 GT として複数周波数点で検証し、ハーネス自体（真値配列・比較ロジック）
が壊れていないことを機械照合する。カナリアが 1 点でも失敗したら、本 bench
の他の測定結果は信頼できないものとして扱う（`canary_pass` フラグ）。

[UNDERSPEC-v2] メモはどのレンダラ（v0 R0 か R0.1 か）で bench を行うか明記
していない。V0.3 全体の趣旨（R0.1 が R0 を置き換える改修）に従い R0.1 を
使用する。詳細は underspec_log_v2.md 参照。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

import measure as m
from voice_r0_1 import SR, render_note, voice_a

WORK = Path(__file__).parent
RESULTS = WORK / "results_v2"
RESULTS.mkdir(exist_ok=True)

BENCH_MIDIS = list(range(36, 100, 3))  # 36, 39, ..., 99 (22 notes)
CORE_BAND_MAX_MIDI = 96  # C7
FMIN = 55.0
FMAX = 2900.0  # >= 2800Hz per §E

CANARY_FREQS_HZ = [82.4, 261.63, 987.77, 2489.0]  # E2, C4, B5, D#7 相当の純音
CANARY_TOLERANCE_CENTS = 30.0  # [UNDERSPEC-v2] メモに数値なし。「桁違い(オクターブ級)の
# 破綻を確実に検知しつつ、単純ハーモニック解析器自体が持つ通常の数十cent級の系統誤差を
# 誤検知しない」水準として、VT-2 v1/v2 で観測された自前推定器のコア帯域内系統誤差の
# 実測レンジ（最大 50cent 程度）を参考に 30 cent とした。


def cents_err(est, true):
    return m.cents_error(est, true)


def run_canary() -> dict:
    """レンダラ・Genome を経由しない純音サイン波で推定器+ハーネスの自己診断を行う。"""
    duration = 1.5
    n = int(duration * SR)
    t = np.arange(n) / SR
    rows = []
    all_pass = True
    for f_true in CANARY_FREQS_HZ:
        sig = 0.2 * np.sin(2 * np.pi * f_true * t)
        # 立ち上がり/立下りフェード（VT-1 と同じ流儀）
        fade_n = int(0.03 * SR)
        env = np.ones(n)
        env[:fade_n] = np.linspace(0, 1, fade_n)
        env[-fade_n:] = np.linspace(1, 0, fade_n)
        sig = sig * env

        est = m.estimate_f0_hps(sig, sr=SR, fmin=FMIN, fmax=FMAX)
        err = cents_err(est, f_true)
        ok = bool(np.isfinite(err) and abs(err) <= CANARY_TOLERANCE_CENTS)
        all_pass = all_pass and ok
        rows.append({"true_f0": f_true, "est_f0": est, "cents_err": err, "pass": ok})
    return {"canary_pass": all_pass, "tolerance_cents": CANARY_TOLERANCE_CENTS, "tones": rows}


def main() -> None:
    t0 = time.time()

    canary = run_canary()
    print(f"canary: pass={canary['canary_pass']}")
    for row in canary["tones"]:
        print(f"  f={row['true_f0']:8.2f} est={row['est_f0']:8.2f} err={row['cents_err']:7.2f}c pass={row['pass']}")

    genome = voice_a()
    rows = []
    for midi in BENCH_MIDIS:
        true_f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)
        sig = render_note(genome, midi)
        est = m.estimate_f0_hps(sig, sr=SR, fmin=FMIN, fmax=FMAX)
        err = cents_err(est, true_f0)
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
        print(f"midi={midi:3d} band={rows[-1]['band']:6s} true={true_f0:8.2f} est={rows[-1]['est_f0']} err={rows[-1]['cents_err']}")

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

    overall_gate_pass = bool(canary["canary_pass"] and gate_no_octave_errors and gate_median)

    summary = {
        "bench_midis": BENCH_MIDIS,
        "core_band_midi_range": [36, CORE_BAND_MAX_MIDI],
        "margin_band_midi_range": [CORE_BAND_MAX_MIDI + 1, 99],
        "sr": SR,
        "voice": "voice_A",
        "renderer": "R0.1",
        "search_fmin": FMIN,
        "search_fmax": FMAX,
        "canary": canary,
        "core_band": {
            "n_notes": len(core_rows),
            "n_octave_errors": n_octave_errors_core,
            "max_abs_cents_err": round(max_abs_core, 2) if max_abs_core is not None else None,
            "median_abs_cents_err": round(median_abs_core, 2) if median_abs_core is not None else None,
            "gate_no_octave_errors_le100c": gate_no_octave_errors,
            "gate_median_le20c": gate_median,
        },
        "margin_band": {
            "n_notes": len(margin_rows),
            "max_abs_cents_err": round(max(margin_errs), 2) if margin_errs else None,
            "median_abs_cents_err": round(float(np.median(margin_errs)), 2) if margin_errs else None,
            "note": "measured のみ。gate 対象外（§E）",
        },
        "overall_gate_pass": overall_gate_pass,
        "notes": rows,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    with open(RESULTS / "bench_f0_v2.json", "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\nVT-2 v2 elapsed={summary['elapsed_sec']}s")
    print(f"core_band: n_octave_errors={n_octave_errors_core} max_abs={max_abs_core} median_abs={median_abs_core}")
    print(f"margin_band: {summary['margin_band']}")
    print(f"overall_gate_pass={overall_gate_pass}")


if __name__ == "__main__":
    main()
