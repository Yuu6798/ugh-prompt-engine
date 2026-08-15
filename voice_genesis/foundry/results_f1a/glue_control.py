"""VG-F1a 共通素材生成: さくら score 由来の f0 / 振幅 / 母音タイムライン（決定論）。

voice_genesis/singer/ を read-only import して --out/f1a_control.npz を生成する。
以後の経路 A/B/C すべてがこの npz の f0/amp を共有駆動源として使う。

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="出力 npz パス（例: .../f1a_control.npz）")
    args = parser.parse_args()

    notes = sc.build_sakura_score()
    segments, total = perf.build_timeline(notes, sr=SR)
    f0 = perf.build_f0_contour(
        segments, total, SR, vibrato_rate_hz=5.0, vibrato_depth_cents=30.0
    )
    amp = perf.build_amplitude_envelope(segments, total, SR)

    # 母音区間表: (start_sample, end_sample, vowel, onset, phrase_index)
    vowel_intervals = []
    for seg in segments:
        vowel_intervals.append(
            (
                seg.start_sample,
                seg.end_sample,
                seg.note.mora.vowel,
                seg.note.mora.onset or "",
                seg.note.phrase_index,
            )
        )

    dtype = np.dtype(
        [
            ("start", np.int64),
            ("end", np.int64),
            ("vowel", "U2"),
            ("onset", "U2"),
            ("phrase", np.int64),
        ]
    )
    vowel_table = np.array(
        [(s, e, v, o, p) for (s, e, v, o, p) in vowel_intervals], dtype=dtype
    )

    out_path = args.out
    np.savez(
        out_path,
        f0=f0,
        amp=amp,
        vowel_table=vowel_table,
        sr=np.array([SR]),
        total_samples=np.array([total]),
    )
    print(f"saved {out_path}")
    print(f"total_samples={total} ({total / SR:.3f}s) n_segments={len(segments)}")
    print(f"f0 range: {f0[f0>0].min():.2f}-{f0.max():.2f} Hz, voiced frac={np.mean(f0>0):.3f}")
    print(f"amp range: {amp.min():.4f}-{amp.max():.4f}")
    for s, e, v, o, p in vowel_intervals[:5]:
        print(f"  seg [{s}:{e}] onset={o!r} vowel={v!r} phrase={p}")


if __name__ == "__main__":
    main()
