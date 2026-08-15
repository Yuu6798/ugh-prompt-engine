"""VG-F1c 前処理 1: さくらノートトラック生成（ビブラート無し + ビブラート30c版）。

`results_f1a/glue_control.py` の複製・改変。voice_genesis/singer を read-only
import して、同一 score/timeline/amplitude から f0 のみビブラート深さを変えた
2 本の npz を出力する（決定論・乱数不使用）。

- f1c_note_track.npz       : vibrato_depth_cents=0.0（マクロ音高トラック。条件(i)用）
- f1c_note_track_vib30.npz : vibrato_depth_cents=30.0（従来どおり。条件(ii)用。
  f1a_control.npz と数値的に同一になるはずだが、f1c を自己完結させるため再生成する）

ポルタメント 55ms（PORTAMENTO_MS デフォルト）は両方とも維持。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/home/user/ugh-prompt-engine/voice_genesis/singer")
sys.path.insert(0, "/home/user/ugh-prompt-engine/voice_genesis/proto1")
sys.path.insert(0, "/home/user/ugh-prompt-engine/voice_genesis/harness")

import numpy as np

import score as sc
import performance as perf

SR = 24000
OUT = "/tmp/claude-0/-home-user-ugh-prompt-engine/1c025cfe-cb3e-592b-b577-d0be9640c799/scratchpad/foundry_f1c"


def build_track(vibrato_depth_cents: float, out_path: str) -> None:
    notes = sc.build_sakura_score()
    segments, total = perf.build_timeline(notes, sr=SR)
    f0 = perf.build_f0_contour(
        segments, total, SR,
        vibrato_rate_hz=5.0, vibrato_depth_cents=vibrato_depth_cents,
    )
    amp = perf.build_amplitude_envelope(segments, total, SR)

    np.savez(
        out_path,
        f0=f0,
        amp=amp,
        sr=np.array([SR]),
        total_samples=np.array([total]),
        vibrato_depth_cents=np.array([vibrato_depth_cents]),
    )
    print(f"saved {out_path}")
    print(f"  vibrato_depth_cents={vibrato_depth_cents} total_samples={total} "
          f"({total / SR:.3f}s) n_segments={len(segments)}")
    print(f"  f0 range: {f0[f0>0].min():.2f}-{f0.max():.2f} Hz, "
          f"voiced_frac={np.mean(f0>0):.4f}, n_zero_frames={(f0==0).sum()}")


def main() -> None:
    build_track(0.0, f"{OUT}/f1c_note_track.npz")
    build_track(30.0, f"{OUT}/f1c_note_track_vib30.npz")


if __name__ == "__main__":
    main()
