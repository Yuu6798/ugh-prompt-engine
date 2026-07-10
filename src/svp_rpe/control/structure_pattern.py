"""control/structure_pattern.py — 比例分割 RMS 符号パターン計器（K2-seg バッチ 2）。

compose が送出する structure セクション記述（"intro: ...; role=..."）が生成音源で
「構造」（quiet-loud-quiet 等の区間エネルギー・パターン）として実現されたかを
測るための薄い比較器。**計器であって verdict なし** — tight/loose/dead の判定や
固定結論・narrative はこのモジュールの責務外で、処方パターンとの一致率のみを
返す（判定は order_sheet / plan.yaml の事前登録規約を設計側が適用する）。

`examples/control/k2_suno_segments/structure_plan.yaml` /
`docs/controllability_poc.md` K2-seg バッチ 2 節を参照。
scratchpad 版 `measure_structure_batch2.py`（K2-seg バッチ 2 発注）の比較器部分を
repo へ昇格したもの（AGENTS.md §8 デモ昇格チェックリスト適用）。
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

# dBFS 変換のフロア（無音区間で log(0) にならないようにするガード）。
_RMS_DB_FLOOR = 1e-6


def split_section_rms(y: np.ndarray, n_sections: int) -> list[float]:
    """トラックをサンプル数で `n_sections` 等分し、各区間の一括 RMS を dB で返す。

    絶対秒でなく相対位置での等分（曲長が生成器依存で統制不能なため、
    比例分割で吸収する）。
    """
    if n_sections <= 0:
        raise ValueError("n_sections must be positive")

    sections = np.array_split(np.asarray(y, dtype=np.float64), n_sections)
    values: list[float] = []
    for section in sections:
        if section.size == 0:
            values.append(_rms_to_db(0.0))
            continue
        rms = float(np.sqrt(np.mean(np.square(section))))
        values.append(_rms_to_db(rms))
    return values


def _rms_to_db(rms: float) -> float:
    return round(20.0 * float(np.log10(max(rms, _RMS_DB_FLOOR))), 4)


def sign_pattern(rms_db: Sequence[float]) -> list[str]:
    """区間 dB 値を全区間の算術平均と比較し high/low に符号化する。"""

    values = list(rms_db)
    if not values:
        raise ValueError("rms_db must be non-empty")
    average = sum(values) / len(values)
    return ["high" if value >= average else "low" for value in values]


def pattern_match_rate(observed: Sequence[str], prescribed: Sequence[str]) -> float:
    """観測パターンと処方パターンの一致率（一致区間数 / 総区間数）。

    tight/loose/dead へのラベル付けは行わない（閾値適用は呼び出し側の責務）。
    """

    observed_list = list(observed)
    prescribed_list = list(prescribed)
    if not prescribed_list:
        raise ValueError("prescribed must be non-empty")
    if len(observed_list) != len(prescribed_list):
        raise ValueError(
            f"observed length {len(observed_list)} != prescribed length {len(prescribed_list)}"
        )
    matches = sum(
        1 for observed_v, target in zip(observed_list, prescribed_list) if observed_v == target
    )
    return matches / len(prescribed_list)
