#!/usr/bin/env python3
"""reason_d.py — M2e r2 の棄却事由 (d) をフレーム単位で算出する（**閾値凍結**）。

設計: `docs/DESIGN_M2e_vremix_real_bed.md` §3.4.5 の事由 (d)、報告項目は §3.4.7。

凍結値（User 決裁 2026-08-01）:

    dropout 判定 : frame_db(t) = 20*log10(frame_rms(t) / bed_active_rms) <= -26.0 dB
                   （`bed_active_rms` = §4.3 の非無音フレーム RMS。**peak ではない**）
    フレーム化   : frame_len = 2048 / hop = 512（§4.3 と同一）
    (d) 該当     : dropout フレームが N_drop = 87 フレーム**以上連続**する

**実効境界**: 1.0 s = 44,100 サンプル。hop = 512 なので実現しうる区間長は離散で、
`N_drop = 87`（44,544 サンプル = 1.0101 s）が最小の該当値、`N_drop = 86`
（44,032 サンプル = 0.9985 s）が最大の非該当値。**監査には実効値を使う。**

改訂履歴（消さない・§0 の規律）:

- 2026-08-01 初版: 閾値 **-40.0 dB**（§4.3 の凍結 `active` 定義の補集合）で算出。
- 2026-08-01 改訂: User 決裁により閾値を **-26.0 dB** へ確定。−40 dB 版の集計は
  `r2_screening.md` §4.7 に日付つきで残す（**なぜ 2 通りの数値が存在するのかを
  後から説明できなくするため消さない**）。
- 2026-08-01 **実装訂正**（Codex 4 巡目 P1/P2）: 上記 2 版はいずれも**凍結定義と
  食い違う実装**で算出していた。訂正点は 2 つ:

  1. **基準量**: `peak_frame_rms` を分母にしていた。§3.4.6 の正は `bed_active_rms`
     であり、しかも §3.4.6 は **peak を分母に置く案を明示的に棄却している**
     （「peak は本設計のどこにも宣言されていない基準量であり、たった 1 サンプルで
     決まる」）。peak >= active_rms なので、旧実装は**カットオフを持ち上げて
     dropout を過剰に数えていた**。
  2. **連続長**: `n_drop = best + FRAME_LEN//HOP - 1`（= best + 3）としていた。
     §3.4.6 の判定は「dropout フレームが 87 **フレーム以上連続**する」であり、
     数えるのは連続フレーム数そのもの。+3 は実効ゲートを 84 連続へ緩めていた。

  どちらも **(d) 該当を過剰に出す方向**の誤りであり、緩める訂正ではない。
  旧算出（`raw/reason_d.json` / `raw/reason_d_26.json`）は無効として日付つきで残す
  （`r2_screening.md` §4.8）。

使い方:
    python reason_d.py --beds <bed wav dir> --tracks <lexical order json> --out <json>
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import numpy as np
import soundfile as sf

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("mk", REPO / "scripts" / "make_vremix_fixtures.py")
mk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mk)

# --- 凍結値 -----------------------------------------------------------------
DROPOUT_DB = -26.0        # User 決裁 2026-08-01
MIN_SECONDS = 1.0         # 設計 §3.4.5 事由 (d)
SAMPLE_RATE = 44100
N_DROP_THRESHOLD = 87     # 実効境界（= ceil(1.0*44100/512)）
EFFECTIVE_BOUND_SEC = N_DROP_THRESHOLD * mk.HOP / SAMPLE_RATE          # 1.010068 s
LARGEST_NON_HIT_SEC = (N_DROP_THRESHOLD - 1) * mk.HOP / SAMPLE_RATE    # 0.998549 s


def longest_dropout(y: np.ndarray) -> tuple[int, float, float, float]:
    """(N_drop, 区間長秒, 開始秒, bed_active_rms) を返す。

    `N_drop` は **dropout フレームの連続数そのもの**（§3.4.6 の判定量）。
    分母は §4.3 の非無音フレーム RMS（`bed_active_rms`）であり peak ではない。
    """
    frames = mk.frame_rms(np.asarray(y, dtype=np.float64))
    active = mk.active_frames(frames)     # §4.3 の −40 dB ゲート（基準量を作るため）
    bed_active_rms = float(np.sqrt(np.mean(frames[active] ** 2)))
    if bed_active_rms <= 0.0:
        raise ValueError("bed_active_rms is zero")
    below = frames <= bed_active_rms * (10.0 ** (DROPOUT_DB / 20.0))
    best = cur = 0
    best_start = start = 0
    for i, v in enumerate(below):
        if v:
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    if best == 0:
        return 0, 0.0, 0.0, bed_active_rms
    return (
        best,
        best * mk.HOP / SAMPLE_RATE,
        best_start * mk.HOP / SAMPLE_RATE,
        bed_active_rms,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--beds", required=True)
    parser.add_argument("--tracks", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    tracks = json.loads(Path(args.tracks).read_text(encoding="utf-8"))
    rows = []
    for index, track in enumerate(tracks):
        path = Path(args.beds) / f"bed_{index:02d}.wav"
        y, sample_rate = sf.read(str(path), dtype="float64", always_2d=False)
        if int(sample_rate) != SAMPLE_RATE:
            # `N_DROP_THRESHOLD` も秒換算も 44100 で凍結してある。別レートの入力を
            # 黙って受けると、87 hop が 1.0 s 未満でもゲートを通る（48 kHz なら
            # 0.928 s を 1.010 s として報告する）。
            raise ValueError(
                f"{path}: sample rate {sample_rate} != {SAMPLE_RATE}; "
                "凍結ゲートは 44100 前提であり、別レートで (d) を数えない (fail-closed)"
            )
        n_drop, seconds, start, bed_active_rms = longest_dropout(y)
        rows.append({
            "index": index, "track": track,
            "n_drop_frames": n_drop, "dropout_sec": seconds, "dropout_start_sec": start,
            "bed_active_rms": bed_active_rms,
            "d_hit": bool(n_drop >= N_DROP_THRESHOLD),
        })
        print(f"[{index:02d}] N_drop={n_drop:4d} ({seconds:6.4f}s) "
              f"{'該当' if n_drop >= N_DROP_THRESHOLD else '—':4s} {track}", flush=True)

    Path(args.out).write_text(
        json.dumps({
            "frozen": {
                "dropout_db": DROPOUT_DB, "frame_len": mk.FRAME_LEN, "hop": mk.HOP,
                "min_seconds": MIN_SECONDS, "n_drop_threshold": N_DROP_THRESHOLD,
                "effective_bound_sec": EFFECTIVE_BOUND_SEC,
                "largest_non_hit_sec": LARGEST_NON_HIT_SEC,
            },
            "rows": rows,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
