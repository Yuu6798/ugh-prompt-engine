"""adapter/joins.py — VG-F1: 単位接合（WORLD パラメータ領域の 30ms クロスフェード）。

設計書 §2 joins.py に対応する。f0 はここでは扱わない（ドナー由来を使わず、
全区間 perf_genes が生成する = F2 軸の単離）。

配置方式: 各 note を対応する resolve 済み frame 長でそのまま [start,end) に
書き込み（総フレーム数は timeline 由来でそのまま保存・overlap-add で縮めない）、
フレーズ内で連続する note 境界（`has_join_to_prev=True`）だけに、境界直前の
crossfade_frames // 2 x2 個のフレームを対象として log-sp 線形 + ap 線形の
クロスフェードを適用する（直前 unit の実フレームを、直後 unit の先頭フレーム
へ向けて上書きブレンドする。境界そのもの（次 unit の最初のフレーム）は
無改変のため、ブレンド終端と自然に連続する）。無声ギャップ（フレーズ間ブレス
等、どの note にも配置されない frame）は直前の有効フレームで carry-forward
する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np

CROSSFADE_MS = 30.0
EPS = 1e-8


@dataclass(frozen=True)
class NotePlacement:
    start_frame: int
    end_frame: int  # exclusive
    sp: np.ndarray  # (n_frames, n_bins)  resolve 済み
    ap: np.ndarray  # (n_frames, n_bins)
    has_join_to_prev: bool  # True: 直前 note と同一フレーズ内で連続（breath を挟まない）


def _carry_forward_gaps(
    sp_seq: np.ndarray, ap_seq: np.ndarray, placements: Sequence[NotePlacement], n_total_frames: int
) -> int:
    covered = np.zeros(n_total_frames, dtype=bool)
    for p in placements:
        covered[p.start_frame:p.end_frame] = True
    if not covered.any():
        return n_total_frames
    first_idx = int(np.argmax(covered))
    if first_idx > 0:
        sp_seq[:first_idx] = sp_seq[first_idx]
        ap_seq[:first_idx] = ap_seq[first_idx]
    n_gap_frames = int((~covered).sum())
    last = first_idx
    for i in range(first_idx, n_total_frames):
        if covered[i]:
            last = i
        else:
            sp_seq[i] = sp_seq[last]
            ap_seq[i] = ap_seq[last]
    return n_gap_frames


def assemble(
    n_total_frames: int,
    n_bins: int,
    placements: Sequence[NotePlacement],
    frame_period_ms: float = 5.0,
    crossfade_ms: float = CROSSFADE_MS,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    sp_seq = np.zeros((n_total_frames, n_bins), dtype=np.float64)
    ap_seq = np.zeros((n_total_frames, n_bins), dtype=np.float64)

    for p in placements:
        sp_seq[p.start_frame:p.end_frame] = p.sp
        ap_seq[p.start_frame:p.end_frame] = p.ap

    n_gap_frames = _carry_forward_gaps(sp_seq, ap_seq, placements, n_total_frames)

    crossfade_frames = max(1, int(round(crossfade_ms / frame_period_ms)))
    n_joins_applied = 0
    n_joins_skipped_short = 0

    for i in range(1, len(placements)):
        cur = placements[i]
        prev = placements[i - 1]
        if not cur.has_join_to_prev:
            continue
        boundary = cur.start_frame
        n = min(crossfade_frames, prev.end_frame - prev.start_frame, cur.end_frame - cur.start_frame)
        if n < 2:
            n_joins_skipped_short += 1
            continue
        w = np.linspace(0.0, 1.0, n)[:, None]
        tail_log = np.log(sp_seq[boundary - n:boundary] + EPS)
        head_log = np.log(cur.sp[:n] + EPS)
        sp_seq[boundary - n:boundary] = np.exp(tail_log * (1.0 - w) + head_log * w)
        ap_seq[boundary - n:boundary] = ap_seq[boundary - n:boundary] * (1.0 - w) + cur.ap[:n] * w
        n_joins_applied += 1

    stats = dict(
        crossfade_frames=crossfade_frames,
        n_joins_applied=n_joins_applied,
        n_joins_skipped_short=n_joins_skipped_short,
        n_gap_frames=n_gap_frames,
        n_placements=len(placements),
    )
    return sp_seq, ap_seq, stats
