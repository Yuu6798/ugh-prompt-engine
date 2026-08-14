"""VT-1 v3: plausibility 不変条件の非退行確認（design memo v031 §D）。

「最終採用レンダラ（R0.1 または R0.2）で 122 ノート plausibility 再確認」。
本サイクルは §C-2（R0.2）が発動しなかった（vt3_v3.py の config (b) で
breathiness 軸が A+B のみで gate 到達）ため、最終採用レンダラは R0.1 の
まま。periodicity 判定自体は measure_v3 の強化推定器（放物線補間 + スペクト
ル櫛オクターブ確定、ノート単位）で再計算し、v0.3 の結果（0/122 違反）を
落としていないことを確認する。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import measure_v3 as m3
import voice_r0 as r0
import voice_r0_1 as r01

WORK = Path(__file__).parent
RESULTS = WORK / "results_v3"
RESULTS.mkdir(exist_ok=True)

R_THRESHOLD = 0.35
MIDI_RANGE = range(36, 97)
FMIN, FMAX = 55.0, 2900.0


def check_renderer(label: str, render_fn, voice_fn) -> dict:
    per_note = []
    n_violations = 0
    for midi in MIDI_RANGE:
        genome = voice_fn()
        sig = render_fn(genome, midi)
        ptrack = m3.periodicity_track_v3(sig, sr=r0.SR, fmin=FMIN, fmax=FMAX)
        violation = bool(ptrack.r_median < R_THRESHOLD)
        if violation:
            n_violations += 1
        per_note.append(
            {
                "midi": midi,
                "r_median": round(ptrack.r_median, 4),
                "n_voiced_frames": ptrack.n_voiced_frames,
                "n_frames": ptrack.n_frames,
                "violation": violation,
            }
        )
    return {
        "renderer": label,
        "r_threshold": R_THRESHOLD,
        "n_notes": len(per_note),
        "n_violations": n_violations,
        "gate_pass": n_violations == 0,
        "notes": per_note,
    }


def main() -> None:
    t0 = time.time()
    result = {
        "r_threshold": R_THRESHOLD,
        "midi_range": [36, 96],
        "voices": ["voice_A", "voice_B"],
        "primary_gate_renderer": "R0.1",
        "estimator": "measure_v3 (enhanced: parabolic interpolation + spectral comb octave resolution, note-level)",
        "configs": [],
    }

    for renderer_label, mod in (("R0.1", r01), ("R0_v0", r0)):
        for voice_label, voice_fn in (("voice_A", mod.voice_a), ("voice_B", mod.voice_b)):
            print(f"=== {renderer_label} / {voice_label} ===")
            r = check_renderer(f"{renderer_label}/{voice_label}", mod.render_note, voice_fn)
            result["configs"].append(r)
            print(f"  n_violations={r['n_violations']} gate_pass={r['gate_pass']}")

    r01_configs = [c for c in result["configs"] if c["renderer"].startswith("R0.1")]
    total_r01_violations = sum(c["n_violations"] for c in r01_configs)
    total_r01_notes = sum(c["n_notes"] for c in r01_configs)

    r0_configs = [c for c in result["configs"] if c["renderer"].startswith("R0_v0")]
    total_r0_violations = sum(c["n_violations"] for c in r0_configs)
    total_r0_notes = sum(c["n_notes"] for c in r0_configs)

    result["gate_summary"] = {
        "R0.1": {"n_notes": total_r01_notes, "n_violations": total_r01_violations, "gate_pass": total_r01_violations == 0},
        "R0_v0_reference": {"n_notes": total_r0_notes, "n_violations": total_r0_violations, "gate_pass": total_r0_violations == 0},
    }
    result["non_regression_vs_v0.3"] = {
        "v0.3_R0.1_n_violations": 0,
        "v3_R0.1_n_violations": total_r01_violations,
        "regression": bool(total_r01_violations > 0),
    }
    result["elapsed_sec"] = round(time.time() - t0, 3)

    with open(RESULTS / "vt1_plausibility_v3.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\nVT-1 v3 elapsed={result['elapsed_sec']}s")
    print("gate (R0.1, primary):", result["gate_summary"]["R0.1"])
    print("reference (R0 v0):", result["gate_summary"]["R0_v0_reference"])


if __name__ == "__main__":
    main()
