"""VT-1 v2: plausibility 不変条件（design memo §D）。

全ノート検査に追加: フレーム periodicity ratio r の median が r>=0.35 を
下回るノートは plausibility violation としてフラグする。voice_A / voice_B
x MIDI 36-96 の 122 ノートで違反 0 が gate。

[UNDERSPEC-v2] メモ §D はどのレンダラ（v0 R0 か R0.1 か）を検査対象にする
か明記していない。本スクリプトは両方を計測して併記する:
  - R0.1（renderer=voice_r0_1）を正式ゲート対象とする（§C のレンダラ改修が
    本 gate の直接の目的であるため）
  - v0 R0（renderer=voice_r0）も参考として同時計測し、「v1 のクリップ/無音
    検査では検出できなかった縮退を、この不変条件が実際に検出できるか」を
    before/after で示す（VT-1 v1 で発見した breathiness 縮退は既に
    voice_r0.py 側でも修正済みなので、ここでの v0 は「その場しのぎの
    クランプ済み v0」であり、対策前の生の v0 ではない点に注意）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


import measure_v2 as m2
import voice_r0 as r0
import voice_r0_1 as r01

WORK = Path(__file__).parent
RESULTS = WORK / "results_v2"
RESULTS.mkdir(exist_ok=True)

R_THRESHOLD = 0.35
MIDI_RANGE = range(36, 97)


def check_renderer(label: str, render_fn, voice_fn) -> dict:
    per_note = []
    n_violations = 0
    for midi in MIDI_RANGE:
        genome = voice_fn()
        sig = render_fn(genome, midi)
        ptrack = m2.periodicity_track(sig, sr=r0.SR)
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
        "configs": [],
    }

    for renderer_label, mod in (("R0.1", r01), ("R0_v0", r0)):
        for voice_label, voice_fn in (("voice_A", mod.voice_a), ("voice_B", mod.voice_b)):
            print(f"=== {renderer_label} / {voice_label} ===")
            r = check_renderer(f"{renderer_label}/{voice_label}", mod.render_note, voice_fn)
            result["configs"].append(r)
            print(f"  n_violations={r['n_violations']} gate_pass={r['gate_pass']}")

    # 正式 gate = R0.1 の voice_A + voice_B 合算で違反 0
    r01_configs = [c for c in result["configs"] if c["renderer"].startswith("R0.1")]
    total_r01_violations = sum(c["n_violations"] for c in r01_configs)
    total_r01_notes = sum(c["n_notes"] for c in r01_configs)

    r0_configs = [c for c in result["configs"] if c["renderer"].startswith("R0_v0")]
    total_r0_violations = sum(c["n_violations"] for c in r0_configs)
    total_r0_notes = sum(c["n_notes"] for c in r0_configs)

    result["gate_summary"] = {
        "R0.1": {
            "n_notes": total_r01_notes,
            "n_violations": total_r01_violations,
            "gate_pass": total_r01_violations == 0,
        },
        "R0_v0_reference": {
            "n_notes": total_r0_notes,
            "n_violations": total_r0_violations,
            "gate_pass": total_r0_violations == 0,
        },
    }
    result["elapsed_sec"] = round(time.time() - t0, 3)

    with open(RESULTS / "vt1_plausibility.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\nVT-1 v2 elapsed={result['elapsed_sec']}s")
    print("gate (R0.1, primary):", result["gate_summary"]["R0.1"])
    print("reference (R0 v0):", result["gate_summary"]["R0_v0_reference"])


if __name__ == "__main__":
    main()
