"""F1b donor selection: quick pyworld.harvest scan of vocadito candidate clips.

Candidates chosen from vocadito_metadata.csv average_pitch (MIDI-ish, closer to
MIDI 64 ~= 330Hz target) to bias toward the sakura score f0 median before
doing an actual pyworld measurement (metadata alone is not proof).
"""
from __future__ import annotations

import sys
import numpy as np
import soundfile as sf
import pyworld as pw

CANDIDATES = [2, 6, 7, 9, 15, 16, 22, 24, 25, 30, 34, 37, 38, 39, 40]
ROOT = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1b/vocadito"

def analyze(track_id: int):
    path = f"{ROOT}/Audio/vocadito_{track_id}.wav"
    x, sr = sf.read(path)
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = np.ascontiguousarray(x.astype(np.float64))
    f0, t = pw.harvest(x, sr, frame_period=5.0)
    voiced = f0[f0 > 0]
    voicing_rate = len(voiced) / len(f0) if len(f0) else 0.0
    if len(voiced) == 0:
        return dict(track_id=track_id, sr=sr, dur=len(x) / sr, f0_median=0.0,
                    voicing_rate=0.0, semitone_diff_330=None)
    f0_median = float(np.median(voiced))
    semitone_diff = 12 * np.log2(f0_median / 330.0)
    return dict(track_id=track_id, sr=sr, dur=len(x) / sr, f0_median=f0_median,
                voicing_rate=voicing_rate, semitone_diff_330=float(semitone_diff))

if __name__ == "__main__":
    results = []
    for tid in CANDIDATES:
        try:
            r = analyze(tid)
        except Exception as e:  # noqa: BLE001
            print(f"track {tid}: ERROR {e}", file=sys.stderr)
            continue
        results.append(r)
        print(f"track_id={r['track_id']:3d} sr={r['sr']} dur={r['dur']:.1f}s "
              f"f0_median={r['f0_median']:.1f}Hz voicing_rate={r['voicing_rate']:.3f} "
              f"semitone_vs_330={r['semitone_diff_330']}")
