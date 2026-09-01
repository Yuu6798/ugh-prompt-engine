"""tolerance 導出（設計正本 §6）。

per-cell n=5 (probe repeat) は分散推定に不足するため、(family × condition
class) で pooled した dispersion を tolerance の根拠に使う。cell 内 5 反復の
分散は `UNSTABLE_CELL` フラグ専用であり、tolerance そのものには決して使わない。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Mapping, Sequence
from typing import TypeVar

import numpy as np

CellKey = TypeVar("CellKey", bound=Hashable)
PoolKey = TypeVar("PoolKey", bound=Hashable)

TOLERANCE_FLOOR_LIMITED = "TOLERANCE_FLOOR_LIMITED"


def pooled_dispersion(
    values_by_cell: Mapping[CellKey, Sequence[float]],
    pool_key: Callable[[CellKey], PoolKey],
) -> dict[PoolKey, float]:
    """`pool_key(cell)`（典型的には family×condition class）でグルーピングし、
    古典的な pooled standard deviation（群ごとに中心化した分散の重み付き平均の
    平方根: `sqrt( sum((n_i-1)*s_i^2) / sum(n_i-1) )` ）を返す。

    単純に全 cell の値を連結して SD を取ると cell 間の平均差（真の信号）が
    分散に混入するため、cell ごとに中心化してからプールする（統計学の標準的な
    "pooled SD" の定義）。
    """
    grouped: dict[PoolKey, list[Sequence[float]]] = {}
    for cell, values in values_by_cell.items():
        grouped.setdefault(pool_key(cell), []).append(values)

    result: dict[PoolKey, float] = {}
    for key, cells in grouped.items():
        numerator = 0.0
        denominator = 0
        for values in cells:
            n = len(values)
            if n < 2:
                continue
            var = float(np.var(np.asarray(values, dtype=float), ddof=1))
            numerator += (n - 1) * var
            denominator += n - 1
        result[key] = math.sqrt(numerator / denominator) if denominator > 0 else 0.0
    return result


def tolerance(pooled_sd: float, k: float, floor: float) -> float:
    """`tolerance = max(k * pooled_SD, floor)`。"""
    return max(k * pooled_sd, floor)


def derive_floor(
    *,
    pcm_quantization_step: float,
    float_eps_bound: float,
    meter_declared_resolution: float | None,
) -> tuple[float, str]:
    """floor の機械導出。値と導出式の両方を返す（manifest に記載する要件）。

    [UNDERSPEC-CAL-05] 設計正本は floor の合成式（各層をどう結合するか）を明示
    していない。各層を「独立に floor を押し上げうる下限」とみなし、最悪ケースで
    保守的な `max(...)` 合成を採用する。PCM 量子化はステップ幅の中心からの最大
    丸め誤差である半ステップ (`pcm_quantization_step / 2`) を採用する。
    """
    components = {
        "pcm_quantization_half_step": pcm_quantization_step / 2.0,
        "float_eps_bound": float_eps_bound,
        "meter_declared_resolution": (
            meter_declared_resolution if meter_declared_resolution is not None else 0.0
        ),
    }
    value = max(components.values())
    winner = max(components, key=lambda k: components[k])
    formula = (
        "floor = max("
        f"pcm_quantization_step/2={components['pcm_quantization_half_step']!r}, "
        f"float_eps_bound={components['float_eps_bound']!r}, "
        f"meter_declared_resolution={components['meter_declared_resolution']!r}"
        f") = {value!r} (dominant={winner})"
    )
    return value, formula


def unstable_cell_flags(
    per_cell_values: Mapping[CellKey, Sequence[float]],
    pooled_sd: float,
    threshold_factor: float,
) -> set[CellKey]:
    """cell 内 (probe) 反復の分散が `threshold_factor * pooled_sd` を超える cell を
    `UNSTABLE_CELL` としてフラグする。tolerance の算出には使わない（§6）。"""
    flagged: set[CellKey] = set()
    threshold = threshold_factor * pooled_sd
    for cell, values in per_cell_values.items():
        if len(values) < 2:
            continue
        cell_sd = float(np.std(np.asarray(values, dtype=float), ddof=1))
        if cell_sd > threshold:
            flagged.add(cell)
    return flagged


def tolerance_floor_limited(pooled_sd: float, per_cell_sds: Sequence[float]) -> bool:
    """pooled dispersion と cell 内分散の全層がゼロのとき True
    (`TOLERANCE_FLOOR_LIMITED` を status に付記すべき状態。ゼロ分散を
    「無限に厳しい合格基準」にも「高精度の証拠」にも変換しない)。"""
    return pooled_sd == 0.0 and all(sd == 0.0 for sd in per_cell_sds)
