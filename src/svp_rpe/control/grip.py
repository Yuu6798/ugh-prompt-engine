"""Grip effect-size calculation for controllability experiments."""
from __future__ import annotations

from typing import Literal, Sequence

import numpy as np

GRIP_EPSILON = 1e-9
GRIP_SATURATED = 999.0
GRIP_TIGHT_MIN = 0.8
GRIP_LOOSE_MIN = 0.2

# カテゴリツマミ（key 等）の一致率閾値。mir_eval weighted score のランダム期待値は
# 24 キー一様で約 0.08 なので、0.3 はチャンス水準の ~4 倍を「ぼんやり効く」下限、
# 0.7 を「操作パネルに残せる」下限とする（controllability_poc.md §3 / K1 決定）。
MATCH_TIGHT_MIN = 0.7
MATCH_LOOSE_MIN = 0.3

GripClass = Literal["tight", "loose", "dead"]


def grip_effect_size(low: Sequence[float], high: Sequence[float]) -> float:
    """Return the signed pooled-SD effect size from low to high samples.

    The sign is positive when the high level has a larger observed mean. When
    both within-group variances are effectively zero, the function returns a
    finite deterministic sentinel instead of inf/NaN:
    equal means -> 0.0, different means -> +/-GRIP_SATURATED.
    """

    low_arr = _as_finite_array(low, name="low")
    high_arr = _as_finite_array(high, name="high")

    mean_delta = float(np.mean(high_arr) - np.mean(low_arr))
    pooled_sd = _pooled_standard_deviation(low_arr, high_arr)

    if pooled_sd < GRIP_EPSILON:
        if abs(mean_delta) < GRIP_EPSILON:
            return 0.0
        return float(np.sign(mean_delta) * GRIP_SATURATED)

    return float(mean_delta / pooled_sd)


def classify_grip(d: float, expected_sign: int) -> GripClass:
    """Classify a grip effect size as tight, loose, or dead."""

    if expected_sign not in (-1, 1):
        raise ValueError("expected_sign must be 1 or -1")
    if not np.isfinite(d):
        raise ValueError("d must be finite")

    magnitude = abs(float(d))
    sign = _sign(float(d))
    if sign != 0 and sign != expected_sign and magnitude >= GRIP_LOOSE_MIN:
        return "dead"
    if sign == expected_sign and magnitude >= GRIP_TIGHT_MIN:
        return "tight"
    if sign == expected_sign and magnitude >= GRIP_LOOSE_MIN:
        return "loose"
    return "dead"


def match_rate(scores: Sequence[float]) -> float:
    """カテゴリツマミの grip: per-sample 一致スコア（∈[0,1]）の平均。

    効果量（pooled SD 除算）はカテゴリ観測に乗らないため、
    controllability_poc.md §3 の規定どおり一致率で測る。
    """

    arr = _as_finite_array(scores, name="scores")
    if np.any(arr < 0.0) or np.any(arr > 1.0):
        raise ValueError("scores must be within [0, 1]")
    return float(np.mean(arr))


def classify_match_grip(rate: float) -> GripClass:
    """一致率を tight / loose / dead に分類する。"""

    if not np.isfinite(rate) or rate < 0.0 or rate > 1.0:
        raise ValueError("rate must be a finite value within [0, 1]")
    if rate >= MATCH_TIGHT_MIN:
        return "tight"
    if rate >= MATCH_LOOSE_MIN:
        return "loose"
    return "dead"


def _as_finite_array(values: Sequence[float], *, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional sequence")
    if arr.size == 0:
        raise ValueError(f"{name} must contain at least one value")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must contain only finite values")
    return arr


def _pooled_standard_deviation(low: np.ndarray, high: np.ndarray) -> float:
    if low.size + high.size <= 2:
        return 0.0

    low_var = float(np.var(low, ddof=1)) if low.size > 1 else 0.0
    high_var = float(np.var(high, ddof=1)) if high.size > 1 else 0.0
    denominator = low.size + high.size - 2
    pooled_var = ((low.size - 1) * low_var + (high.size - 1) * high_var) / denominator
    return float(np.sqrt(max(pooled_var, 0.0)))


def _sign(value: float) -> int:
    if abs(value) < GRIP_EPSILON:
        return 0
    return 1 if value > 0.0 else -1
