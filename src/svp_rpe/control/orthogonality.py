"""K3 直交性行列の純関数層。

ツマミ i の A/B コントラストがセンサー j に立てる効果量（既存 grip_effect_size と同じ
符号付き Cohen's d）を N×N に並べ、DCI (Eastwood & Williams 2018) の
disentanglement / completeness を feature importance の代わりに効果量絶対値で
再定義する。MIG 類似量は「効果量ギャップ」として実装する
（相互情報量そのものではない — 名前で正直に区別する）。
"""
from __future__ import annotations

import math
from typing import Literal, Sequence

import numpy as np

from .grip import GRIP_EPSILON, GRIP_LOOSE_MIN, GRIP_TIGHT_MIN

INTERFERENCE_WEAK_MIN = GRIP_LOOSE_MIN
INTERFERENCE_STRONG_MIN = GRIP_TIGHT_MIN
# ノイズフロア: dead 閾値未満の効果量はエントロピー計算で 0 扱いにする
IMPORTANCE_FLOOR = GRIP_LOOSE_MIN
# 飽和センチネル(±999)や巨大 d が正規化を支配しないよう |d| を 10 pooled SD で頭打ちにする
IMPORTANCE_CAP = 10.0

InterferenceClass = Literal["clean", "weak", "strong"]


def classify_interference(effect: float) -> InterferenceClass:
    """符号非依存でオフダイアゴナル効果量を分類する。"""

    magnitude = abs(float(effect))
    if magnitude >= INTERFERENCE_STRONG_MIN:
        return "strong"
    if magnitude >= INTERFERENCE_WEAK_MIN:
        return "weak"
    return "clean"


def normalized_entropy(weights: Sequence[float]) -> float | None:
    """非負の重み列から正規化エントロピー H/ln(n) を返す。

    重みの合計が ``GRIP_EPSILON`` 未満なら分布が定義できないため ``None``。
    要素数 1 の分布はエントロピー 0（ln(1)=0 割り算を避けるための特例）。
    """

    arr = np.asarray(list(weights), dtype=float)
    total = float(np.sum(arr))
    if total < GRIP_EPSILON:
        return None
    if arr.size == 1:
        return 0.0

    probabilities = arr / total
    entropy = 0.0
    for p in probabilities:
        if p > 0.0:
            entropy -= float(p) * math.log(float(p))
    # 一様分布で H/ln(n) が浮動小数点誤差により 1 を僅かに超え、
    # 1 - H_norm が -0.0 になるのを防ぐ（値クランプ規約）
    return float(max(0.0, min(1.0, entropy / math.log(arr.size))))


def importance_matrix(
    effects: Sequence[Sequence[float]],
    *,
    floor: float = IMPORTANCE_FLOOR,
    cap: float = IMPORTANCE_CAP,
) -> np.ndarray:
    """効果量行列から絶対値・フロア・キャップ済みの importance 行列を作る。"""

    rows = list(effects)
    if not rows:
        raise ValueError("effects must be a non-empty matrix")
    row_lengths = {len(list(row)) for row in rows}
    if len(row_lengths) != 1 or 0 in row_lengths:
        raise ValueError("effects must be a rectangular, non-empty matrix")

    arr = np.abs(np.asarray([[float(v) for v in row] for row in rows], dtype=float))
    arr = np.where(arr < floor, 0.0, arr)
    arr = np.clip(arr, 0.0, cap)
    return arr


def disentanglement_scores(importance: np.ndarray) -> list[float | None]:
    """行（ツマミ）ごとの disentanglement スコア = 1 - normalized_entropy(row)。

    ツマミ i が単一センサーだけを動かすなら 1、全センサーを均等に汚すなら 0。
    行の合計が ``GRIP_EPSILON`` 未満（=行列上死んでいるツマミ）なら ``None``。
    """

    scores: list[float | None] = []
    for row in importance:
        row_sum = float(np.sum(row))
        if row_sum < GRIP_EPSILON:
            scores.append(None)
            continue
        entropy = normalized_entropy(row.tolist())
        scores.append(None if entropy is None else float(1.0 - entropy))
    return scores


def completeness_scores(importance: np.ndarray) -> list[float | None]:
    """列（センサー）ごとの completeness スコア = 1 - normalized_entropy(column)。

    センサー j が単一ツマミにのみ支配されるなら 1。
    列の合計が ``GRIP_EPSILON`` 未満（=誰も触れていないセンサー）なら ``None``。
    """

    return disentanglement_scores(importance.T)


def mass_weighted_overall(
    scores: Sequence[float | None],
    masses: Sequence[float],
) -> float | None:
    """スコア列の質量（行/列の importance 合計）加重平均。

    DCI 論文の ρ_i 重み付けに対応する。非 None のスコアが無い、あるいは
    総質量が ``GRIP_EPSILON`` 未満なら ``None``。
    """

    scores_list = list(scores)
    masses_list = list(masses)
    if len(scores_list) != len(masses_list):
        raise ValueError("scores and masses must have the same length")

    total_mass = 0.0
    weighted_sum = 0.0
    for score, mass in zip(scores_list, masses_list):
        if score is None:
            continue
        total_mass += float(mass)
        weighted_sum += float(score) * float(mass)

    if total_mass < GRIP_EPSILON:
        return None
    return float(weighted_sum / total_mass)


def effect_size_gap(importance: np.ndarray) -> list[float | None]:
    """列（センサー）ごとの MIG 類似量: (top1 - top2) / top1 ∈ [0, 1]。

    センサー j の支配ツマミがどれだけ独走しているかを表す。top1（最大の
    importance）が ``GRIP_EPSILON`` 未満なら ``None``。行が 1 つしかない場合は
    top2 = 0.0 として扱う。
    """

    gaps: list[float | None] = []
    for column in importance.T:
        sorted_values = np.sort(column)[::-1]
        top1 = float(sorted_values[0])
        top2 = float(sorted_values[1]) if sorted_values.size > 1 else 0.0
        if top1 < GRIP_EPSILON:
            gaps.append(None)
            continue
        gaps.append(float((top1 - top2) / top1))
    return gaps


def noise_ceiling(null_values: Sequence[float]) -> float | None:
    """既知 dead 行（=配線が無いことがコードで既知のツマミ）から集めた効果量の
    経験的ヌル分布の天井 = |d| の最大値。

    ``null_values`` は生の効果量（符号付き d）をそのまま渡す — floor/cap 済みの
    importance ではない。空なら ``None``（既知 dead 行がない fixture では有意性を
    主張できない、というハーネスの誠実な自己申告）。

    センチネル ±999（ゼロ分散飽和）が dead 行に紛れ込むと天井が 999 に張り付き、
    以降の全セルが unresolved 判定になる。これはバグではなく fail-safe な挙動と
    位置づける（誤って「有意」と主張するより、判定不能と申告する方が安全）。
    """

    values = [abs(float(v)) for v in null_values]
    if not values:
        return None
    return max(values)


def noise_margin(effect: float, ceiling: float | None) -> float | None:
    """効果量とノイズ天井の比 |effect| / ceiling。

    天井が ``None``（既知 dead 行がない）、または ``GRIP_EPSILON`` 未満（実質ゼロ）
    なら比較が定義できないため ``None``。
    """

    if ceiling is None or ceiling < GRIP_EPSILON:
        return None
    return float(abs(float(effect)) / ceiling)
