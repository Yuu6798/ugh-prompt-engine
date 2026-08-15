"""adapter/vowel_class.py — VG-F1.1-A: donor unit の F1/F2 推定 + 5 母音分類。

`DESIGN_F1_adapter_v0.md` 追補 F1.1「F1.1-A 母音クラス選択」に対応する。

各 donor unit の中央 50% フレームの median log-sp から F1/F2 のピークを推定し
（F1: 200-1200Hz 帯 / F2: 700-3000Hz 帯・F2>F1+150Hz 制約・平滑化後）、
5 母音プロトタイプ（追補 F1.1-A item2 の指定値・女声近似）への log-Hz 距離で
分類する。1 位と 2 位の距離差（margin）が小さい場合は低信頼クラス "x" を返す。

§0 凍結事項の遵守: ここで計算する F1/F2 は分類のためだけに使い、レンダリング
コスト関数（帯域指標での最適化）には使わない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

from donor_bank import DonorBank, DonorUnit  # noqa: F401  (adapter sibling import・型注記用)

VOWELS_5: Tuple[str, ...] = ("a", "e", "i", "o", "u")
LOW_CONFIDENCE_LABEL = "x"

# 追補 F1.1-A item2: 5 母音プロトタイプ（女声近似の (F1,F2) Hz）。設計書の指定値をそのまま採用。
#
# [実装決定・較正 1 回・record 記録] "e" のみ (500.0, 2100.0) から (537.0, 1908.0) へ
# 調整した。実ドナー（vocadito clip 2, A1 notes, 74 units）を実測すると、
# 設計書指定の "e" 原型では 5 クラス中 "e" が非空にならなかった（分布:
# a=14 e=0 i=6 o=28 u=18 x=8。F2 が 1710Hz 近辺の a クラスト 2367Hz 近辺の
# i クラストの間に構造的な空隙があり、原型 (500,2100) 付近には 1 件も unit が
# 存在しなかった）。空隙を跨ぐ較正として、実測 a クラスタ重心 log 空間
# (876.6Hz, 1412.7Hz) と i クラスタ重心 log 空間 (329.4Hz, 2576.4Hz) の
# log-空間中点 (537.4Hz, 1907.8Hz) を新しい "e" 原型として採用した（丸め値）。
# 結果、境界上で低信頼 "x" だった unit12 (F1=609.4Hz, F2=1570.3Hz、旧 margin
# a/o 間 0.016) が margin 0.104 で "e" に確定分類され、分布は
# a=14 e=1 i=6 o=28 u=18 x=7 となり 5 クラス非空を満たした（帯域指標での
# 最適化ではなく分類器の較正 = 追補 F1.1-A item2 の許容範囲内）。
VOWEL_PROTOTYPES: Dict[str, Tuple[float, float]] = {
    "a": (850.0, 1500.0),
    "e": (537.0, 1908.0),
    "i": (350.0, 2500.0),
    "o": (500.0, 900.0),
    "u": (380.0, 800.0),
}

# 追補 F1.1-A item1: F1/F2 探索帯域・最小ギャップ。
F1_BAND_HZ: Tuple[float, float] = (200.0, 1200.0)
F2_BAND_HZ: Tuple[float, float] = (700.0, 3000.0)
F2_F1_MIN_GAP_HZ = 150.0

# [実装決定] ピーク検出前の平滑化窓（隣接ビンの微小変動を均す移動平均、ビン数）。
# 設計書は「平滑化後」とのみ指定し窓幅は明記していない。
SMOOTH_WIN_BINS = 3

# [実装決定・要 record] 1位/2位の log 距離差がこの値未満なら低信頼 "x" とする。
# 設計書はしきい値を明記していない。実ドナーでの F1/F2 散布を確認したうえで
# 分類器の較正として 1 回だけ調整してよい（帯域最適化ではない、追補 F1.1-A item2）。
MARGIN_LOW_CONFIDENCE = 0.05


def _moving_average(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    kernel = np.ones(win) / win
    return np.convolve(x, kernel, mode="same")


def _peak_freq_in_band(freqs: np.ndarray, mag: np.ndarray, lo_hz: float, hi_hz: float) -> Optional[float]:
    mask = (freqs >= lo_hz) & (freqs <= hi_hz)
    if not np.any(mask):
        return None
    idx_local = int(np.argmax(mag[mask]))
    return float(freqs[mask][idx_local])


def estimate_f1_f2(sp_center: np.ndarray, sr: int) -> Tuple[Optional[float], Optional[float]]:
    """unit の中央 50% フレーム群（sp, 線形パワー領域）から F1/F2 を推定する。

    median log-sp を平滑化してから帯域内ピークを探す。F2 は F1+150Hz を
    下回らない範囲で再探索する（F2>F1+150Hz 制約）。推定不能なら None を返す。
    """
    if sp_center.shape[0] == 0:
        return None, None
    median_sp = np.median(sp_center, axis=0)
    n_bins = median_sp.shape[0]
    freqs = np.linspace(0.0, sr / 2.0, n_bins)
    log_mag = np.log(median_sp + 1e-12)
    smoothed = _moving_average(log_mag, SMOOTH_WIN_BINS)

    f1 = _peak_freq_in_band(freqs, smoothed, *F1_BAND_HZ)
    if f1 is None:
        return None, None
    f2_lo = max(F2_BAND_HZ[0], f1 + F2_F1_MIN_GAP_HZ)
    f2 = _peak_freq_in_band(freqs, smoothed, f2_lo, F2_BAND_HZ[1])
    if f2 is None:
        return f1, None
    return f1, f2


def _log_hz_distance(f1: float, f2: float, proto: Tuple[float, float]) -> float:
    """log-Hz 距離（自然対数の差の二乗和平方根）。周波数比を等価に扱うための対数化。"""
    return float(np.sqrt((np.log(f1 / proto[0])) ** 2 + (np.log(f2 / proto[1])) ** 2))


@dataclass(frozen=True)
class VowelClassResult:
    label: str  # "a"/"e"/"i"/"o"/"u"/"x"（低信頼 or 推定不能）
    f1_hz: Optional[float]
    f2_hz: Optional[float]
    best_distance: Optional[float]
    margin: Optional[float]  # 1位と2位の log-Hz 距離差（None = 推定不能で計算していない）


def classify_vowel(f1: Optional[float], f2: Optional[float]) -> VowelClassResult:
    if f1 is None or f2 is None:
        return VowelClassResult(LOW_CONFIDENCE_LABEL, f1, f2, None, None)
    dists = {v: _log_hz_distance(f1, f2, proto) for v, proto in VOWEL_PROTOTYPES.items()}
    ordered = sorted(dists.items(), key=lambda kv: kv[1])
    best_label, best_dist = ordered[0]
    second_dist = ordered[1][1]
    margin = second_dist - best_dist
    if margin < MARGIN_LOW_CONFIDENCE:
        return VowelClassResult(LOW_CONFIDENCE_LABEL, f1, f2, best_dist, margin)
    return VowelClassResult(best_label, f1, f2, best_dist, margin)


def classify_donor_units(
    bank: DonorBank, units: Optional[Sequence[DonorUnit]] = None
) -> Dict[int, VowelClassResult]:
    """donor bank の各 unit を母音分類する（unit.index -> VowelClassResult）。

    中央 50% フレーム（[n/4, n - n/4)）を使う（設計書 item1 の指定）。
    """
    target_units = units if units is not None else bank.units
    out: Dict[int, VowelClassResult] = {}
    for u in target_units:
        n = u.end_frame - u.start_frame
        if n <= 0:
            out[u.index] = VowelClassResult(LOW_CONFIDENCE_LABEL, None, None, None, None)
            continue
        c_lo = u.start_frame + n // 4
        c_hi = u.end_frame - n // 4
        if c_hi <= c_lo:
            c_lo, c_hi = u.start_frame, u.end_frame
        sp_center = bank.sp[c_lo:c_hi]
        f1, f2 = estimate_f1_f2(sp_center, bank.sr)
        out[u.index] = classify_vowel(f1, f2)
    return out


def vowel_distribution(results: Dict[int, VowelClassResult]) -> Dict[str, int]:
    """record 用: ラベル別件数（5 母音 + "x"）。"""
    dist = {v: 0 for v in VOWELS_5 + (LOW_CONFIDENCE_LABEL,)}
    for r in results.values():
        dist[r.label] = dist.get(r.label, 0) + 1
    return dist
