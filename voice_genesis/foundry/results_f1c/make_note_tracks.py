"""VG-F1c 前処理 1: さくらノートトラック生成（ビブラート無し + ビブラート30c版）。

`results_f1a/glue_control.py` の複製・改変。voice_genesis/singer を read-only
import して、同一 score/timeline/amplitude から f0 のみビブラート深さを変えた
2 本の npz を出力する（決定論・乱数不使用）。

- f1c_note_track.npz       : vibrato_depth_cents=0.0（マクロ音高トラック。条件(i)用）
- f1c_note_track_vib30.npz : vibrato_depth_cents=30.0（従来どおり。条件(ii)用。
  f1a_control.npz と数値的に同一になるはずだが、f1c を自己完結させるため再生成する）

ポルタメント 55ms（PORTAMENTO_MS デフォルト）は両方とも維持。

R11 で再現可能化のためパスをパラメータ化（元の実行は record 記載の環境で実施）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "singer"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "proto1"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness"))

import numpy as np

import score as sc
import performance as perf

SR = 24000


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="出力ディレクトリ（f1c_note_track*.npz を書く）")
    args = parser.parse_args()
    out_dir = args.out

    build_track(0.0, f"{out_dir}/f1c_note_track.npz")
    build_track(30.0, f"{out_dir}/f1c_note_track_vib30.npz")


if __name__ == "__main__":
    main()
