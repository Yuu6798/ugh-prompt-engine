"""VT-1 破綻チェック: voice_A / voice_B を MIDI 36-96（C2-C7）の全半音で生成し、
非有限値・無音・過大クリップがないことを確認する。WAV は代表数点のみ保存する。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from voice_r0 import NOTE_DUR_SEC, SR, render_note, voice_a, voice_b

WORK = Path(__file__).parent
RESULTS = WORK / "results"
RESULTS.mkdir(exist_ok=True)
WAV_DIR = RESULTS / "sample_wav"
WAV_DIR.mkdir(exist_ok=True)

SAMPLE_MIDIS = {36, 48, 60, 72, 84, 96}


def check_voice(name: str, genome) -> dict:
    per_note = []
    ok = True
    for midi in range(36, 97):
        sig = render_note(genome, midi)
        finite = bool(np.isfinite(sig).all())
        peak = float(np.max(np.abs(sig))) if finite else float("nan")
        rms = float(np.sqrt(np.mean(sig**2))) if finite else float("nan")
        clipped = bool(peak >= 0.999) if finite else True
        silent = bool(rms < 1e-6) if finite else True
        note_ok = finite and not clipped and not silent
        ok = ok and note_ok
        per_note.append(
            {
                "midi": midi,
                "finite": finite,
                "peak": round(peak, 6) if finite else None,
                "rms": round(rms, 6) if finite else None,
                "clipped": clipped,
                "silent": silent,
                "ok": note_ok,
            }
        )
        if midi in SAMPLE_MIDIS:
            sf.write(str(WAV_DIR / f"{name}_midi{midi}.wav"), sig.astype(np.float32), SR)
    return {"voice": name, "all_ok": ok, "n_notes": len(per_note), "notes": per_note}


def main() -> None:
    t0 = time.time()
    result = {
        "sr": SR,
        "note_dur_sec": NOTE_DUR_SEC,
        "midi_range": [36, 96],
        "voices": [],
    }
    for name, genome in (("voice_A", voice_a()), ("voice_B", voice_b())):
        result["voices"].append(check_voice(name, genome))

    result["all_pass"] = all(v["all_ok"] for v in result["voices"])
    result["elapsed_sec"] = round(time.time() - t0, 3)

    with open(RESULTS / "vt1_check.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"VT-1: all_pass={result['all_pass']} elapsed={result['elapsed_sec']}s")
    for v in result["voices"]:
        n_fail = sum(1 for n in v["notes"] if not n["ok"])
        print(f"  {v['voice']}: {v['n_notes']} notes, {n_fail} failing")


if __name__ == "__main__":
    main()
