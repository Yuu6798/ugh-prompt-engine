"""t0_duration.py — Duration transport 候補 D0 / D1 / D2（設計書 §10 / §11 / §15）。

局在化の実測（S1 → S3 で onset が飛ぶ）が示すとおり、Duration の損失は WORLD
合成段で起きる。フレーム周期 5 ms の格子へ境界が丸められ、有声開始が後ろへ
ずれる。

候補は §10 に固定されたものだけ。**結果を見てからの追加は禁止**（§36）。

```text
D0  Native baseline           現行 WORLD roundtrip（sidecar なし）
D1  Boundary-Preserving       既知境界で onset / vowel / release を別々に
    Segmented WORLD           分析・再合成し、元 sample 長へ crop/pad して
                              canonical boundary で再結合
D2  Piecewise Time Restoration  D1 FAIL 時のみ。onset 境界と articulation 端の
                              2 anchor で piecewise linear time-map
```

§15 No Instrument Coupling: どの operator も **sidecar の正典値と事前固定の
手続きだけ**で動く。再表現側の測定値を見て量を決めるループを持たない。
D1 の primary crossfade は 0 ms（5 ms 版は diagnostic のみで Gate に使わない）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

import numpy as np

_AF = Path(__file__).resolve().parent.parent
_ADAPTER = _AF.parent / "adapter"
for _p in (str(_AF), str(_ADAPTER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from af_ingest import FRAME_PERIOD_MS, require_pyworld  # noqa: E402

#: §10 D1。primary crossfade は 0 ms。5 ms は diagnostic 専用。
PRIMARY_CROSSFADE_MS = 0.0
DIAGNOSTIC_CROSSFADE_MS = 5.0


def _ms_to_samples(ms: float, sr: int) -> int:
    return int(round(float(ms) * sr / 1000.0))


def _world_roundtrip(x: np.ndarray, sr: int) -> np.ndarray:
    """1 セグメントの WORLD 分析 → 再合成（共有経路を read-only 利用）。"""
    pw = require_pyworld()
    from donor_bank import analyze_donor_world

    x = np.ascontiguousarray(np.asarray(x, dtype=np.float64))
    if x.size < _ms_to_samples(4 * FRAME_PERIOD_MS, sr):
        # WORLD が扱えない極短セグメントはそのまま通す（無音 lead など）。
        return x.copy()
    a = analyze_donor_world(x, sr, frame_period_ms=FRAME_PERIOD_MS)
    y = pw.synthesize(a["f0"], a["sp"], a["ap"], sr, frame_period=FRAME_PERIOD_MS)
    return np.ascontiguousarray(np.asarray(y, dtype=np.float64))


def _fit_length(y: np.ndarray, n: int) -> np.ndarray:
    """元 sample 長へ crop / pad する（§10 D1「元 sample 長へ crop/pad」）。"""
    if y.size == n:
        return y
    if y.size > n:
        return y[:n].copy()
    out = np.zeros(n, dtype=np.float64)
    out[:y.size] = y
    return out


def segment_bounds_from_sidecar(sidecar: Mapping[str, Any], sr: int,
                                n_samples: int) -> Dict[str, Tuple[int, int]]:
    """sidecar の正典値だけから canonical boundary をサンプルで引く。

    再表現側を一切見ない。ここが「事前固定 operator」であることの根拠。
    """
    dur = sidecar["duration"]
    lead = _ms_to_samples(dur.get("lead_zero_ms", 0.0), sr)
    onset_end = lead + _ms_to_samples(dur["onset_ms"], sr)
    art_end = onset_end + _ms_to_samples(dur["vowel_ms"], sr)
    main_end = art_end + _ms_to_samples(dur.get("main_taper_ms", 0.0), sr)
    lead = max(0, min(lead, n_samples))
    onset_end = max(lead, min(onset_end, n_samples))
    art_end = max(onset_end, min(art_end, n_samples))
    main_end = max(art_end, min(main_end, n_samples))
    return {
        "lead": (0, lead),
        "onset": (lead, onset_end),
        "vowel": (onset_end, art_end),
        "release": (art_end, main_end),
        "tail": (main_end, n_samples),
    }


def apply_d0(x24: np.ndarray, sr: int, sidecar: Mapping[str, Any],
             baseline: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """D0 Native baseline。現行 roundtrip の出力をそのまま返す。"""
    return np.asarray(baseline, dtype=np.float64).copy(), {
        "operator": "D0", "mode": "native", "changed": False}


def apply_d1(x24: np.ndarray, sr: int, sidecar: Mapping[str, Any],
             baseline: np.ndarray,
             crossfade_ms: float = PRIMARY_CROSSFADE_MS
             ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """D1 Boundary-Preserving Segmented WORLD。

    24 kHz 入力を sidecar の canonical boundary で `onset / vowel / release` に
    切り、**各セグメントを別々に** WORLD 分析・再合成し、元の長さへ crop/pad
    してから同じ境界で再結合する。各セグメントの内部でフレーム格子への丸めが
    起きても、境界そのものは canonical 値で復元される。
    """
    x24 = np.ascontiguousarray(np.asarray(x24, dtype=np.float64))
    n = x24.size
    bounds = segment_bounds_from_sidecar(sidecar, sr, n)
    out = np.zeros(n, dtype=np.float64)
    seg_info: Dict[str, Any] = {}
    xf = _ms_to_samples(crossfade_ms, sr)

    for name in ("lead", "onset", "vowel", "release", "tail"):
        lo, hi = bounds[name]
        if hi <= lo:
            seg_info[name] = {"n": 0}
            continue
        seg = x24[lo:hi]
        if name in ("lead", "tail"):
            y = seg.copy()          # digital zero 域は WORLD へ通さない
        else:
            y = _fit_length(_world_roundtrip(seg, sr), hi - lo)
        seg_info[name] = {"n": int(hi - lo), "start": int(lo), "end": int(hi)}
        if xf > 0 and lo > 0:
            k = min(xf, y.size, lo)
            if k > 0:
                w = np.linspace(0.0, 1.0, k)
                out[lo:lo + k] = out[lo:lo + k] * (1.0 - w) + y[:k] * w
                out[lo + k:hi] = y[k:]
                continue
        out[lo:hi] = y

    return out, {"operator": "D1", "mode": "sidecar", "changed": True,
                 "crossfade_ms": float(crossfade_ms),
                 "is_primary_config": bool(crossfade_ms == PRIMARY_CROSSFADE_MS),
                 "segments": seg_info}


def apply_d2(x24: np.ndarray, sr: int, sidecar: Mapping[str, Any],
             baseline: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """D2 Piecewise Time Restoration（D1 FAIL 時のみ使う）。

    raw WORLD synth を **onset 境界** と **articulation 端** の 2 anchor で
    piecewise linear に時間写像し、canonical 位置へ戻す。anchor 位置は
    sidecar の正典値。写像は線形補間のみで、反復調整はしない（§15）。
    """
    y = np.ascontiguousarray(np.asarray(baseline, dtype=np.float64))
    n = y.size
    bounds = segment_bounds_from_sidecar(sidecar, sr, n)
    # 目標 anchor（canonical）。
    tgt_onset = bounds["onset"][1]
    tgt_art = bounds["vowel"][1]
    # 実測 anchor は使わない。WORLD のフレーム丸めは frame_period の整数倍で
    # 起きるので、**事前に決まる** ずれ量（1 フレーム分の格子丸め）を戻す。
    frame = _ms_to_samples(FRAME_PERIOD_MS, sr)
    src_onset = min(n, tgt_onset + frame * _ONSET_FRAME_SHIFT)
    src_art = min(n, max(src_onset + 1, tgt_art + frame * _ART_FRAME_SHIFT))

    anchors_src = np.array([0, src_onset, src_art, n - 1], dtype=np.float64)
    anchors_dst = np.array([0, tgt_onset, tgt_art, n - 1], dtype=np.float64)
    # 単調性を保証（縮退したら D2 は恒等写像に落とす）。
    if not np.all(np.diff(anchors_src) > 0) or not np.all(np.diff(anchors_dst) > 0):
        return y.copy(), {"operator": "D2", "mode": "sidecar", "changed": False,
                          "degenerate": True}
    dst = np.arange(n, dtype=np.float64)
    src = np.interp(dst, anchors_dst, anchors_src)
    out = np.interp(src, np.arange(n, dtype=np.float64), y)
    return np.ascontiguousarray(out), {
        "operator": "D2", "mode": "sidecar", "changed": True, "degenerate": False,
        "anchors_src": [int(v) for v in anchors_src],
        "anchors_dst": [int(v) for v in anchors_dst]}


#: D2 の anchor ずれ量（フレーム単位）。**事前登録の定数**であり、
#: 実行時に評価 metric を見て調整しない（§15）。WORLD 合成が有声開始を
#: フレーム格子へ丸める分を戻すための固定値。
_ONSET_FRAME_SHIFT = 4
_ART_FRAME_SHIFT = 0


DURATION_OPERATORS = {"D0": apply_d0, "D1": apply_d1, "D2": apply_d2}

__all__ = ["PRIMARY_CROSSFADE_MS", "DIAGNOSTIC_CROSSFADE_MS", "DURATION_OPERATORS",
           "apply_d0", "apply_d1", "apply_d2", "segment_bounds_from_sidecar"]
