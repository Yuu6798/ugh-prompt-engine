"""planb/pb_world.py — WORLD 解析 / 合成の薄いラッパ（決定論）。

Plan B の分離境界（プラン §4）では

- Identity Track = spectral envelope (`sp`) + aperiodicity (`ap`)
- Performance Track = f0 / duration / energy / release（**1 次元の制御系列のみ**）

の 2 経路しか存在しない。本モジュールはその 2 経路が共有する唯一の
解析器であり、WORLD の 3 出力（f0 / sp / ap）を 1 つの値オブジェクトへ
束ねる。以降のモジュールは `WorldAnalysis` から **どのフィールドを取るか**
だけで Identity / Performance の帰属が決まる（`pb_gates` が機械検査する）。

決定論契約: 同一 wav + 同一パラメータ -> 同一 f0/sp/ap（WORLD の
harvest/cheaptrick/d4c はいずれも乱数を使わない）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyworld as pw

FRAME_PERIOD_MS = 5.0
F0_FLOOR_HZ = 60.0
F0_CEIL_HZ = 900.0


@dataclass(frozen=True)
class WorldAnalysis:
    """WORLD 解析結果（1 発話ぶん）。"""

    sr: int
    frame_period_ms: float
    f0: np.ndarray  # (T,)   Hz、無声は 0.0
    sp: np.ndarray  # (T, F) スペクトル包絡（線形パワー）
    ap: np.ndarray  # (T, F) 非周期性指標 [0,1]

    @property
    def n_frames(self) -> int:
        return int(self.f0.shape[0])

    @property
    def times_s(self) -> np.ndarray:
        return np.arange(self.n_frames, dtype=np.float64) * (self.frame_period_ms / 1000.0)


def analyze(
    wav: np.ndarray,
    sr: int,
    *,
    frame_period_ms: float = FRAME_PERIOD_MS,
    f0_floor: float = F0_FLOOR_HZ,
    f0_ceil: float = F0_CEIL_HZ,
) -> WorldAnalysis:
    """wav -> WORLD 解析（harvest + stonemask + cheaptrick + d4c）。"""
    x = np.ascontiguousarray(np.asarray(wav, dtype=np.float64))
    f0_raw, t = pw.harvest(x, sr, f0_floor=f0_floor, f0_ceil=f0_ceil, frame_period=frame_period_ms)
    f0 = pw.stonemask(x, f0_raw, t, sr)
    sp = pw.cheaptrick(x, f0, t, sr)
    ap = pw.d4c(x, f0, t, sr)
    return WorldAnalysis(
        sr=sr, frame_period_ms=frame_period_ms,
        f0=np.asarray(f0, dtype=np.float64),
        sp=np.asarray(sp, dtype=np.float64),
        ap=np.asarray(ap, dtype=np.float64),
    )


def synthesize(f0: np.ndarray, sp: np.ndarray, ap: np.ndarray, sr: int,
               frame_period_ms: float = FRAME_PERIOD_MS) -> np.ndarray:
    """f0/sp/ap -> 波形（WORLD synthesis）。"""
    y = pw.synthesize(
        np.ascontiguousarray(np.asarray(f0, dtype=np.float64)),
        np.ascontiguousarray(np.asarray(sp, dtype=np.float64)),
        np.ascontiguousarray(np.asarray(ap, dtype=np.float64)),
        sr, frame_period_ms,
    )
    return np.asarray(y, dtype=np.float64)


def log_spectral_distance_db(a: np.ndarray, b: np.ndarray, *, eps: float = 1e-12) -> float:
    """2 つのスペクトル包絡（線形パワー、同 shape）の対数スペクトル距離 [dB]。

    Plan B の identity 距離・TRF drift 軸の共通尺度。フレーム軸を持つ場合は
    全要素の RMS を取る（呼び出し側で平均包絡へ潰してから渡すのが既定）。
    """
    la = 10.0 * np.log10(np.maximum(np.asarray(a, dtype=np.float64), eps))
    lb = 10.0 * np.log10(np.maximum(np.asarray(b, dtype=np.float64), eps))
    return float(np.sqrt(np.mean((la - lb) ** 2)))
