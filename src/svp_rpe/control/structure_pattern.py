"""control/structure_pattern.py — 比例分割 RMS 符号パターン計器（K2-seg バッチ 2）。

compose が送出する structure セクション記述（"intro: ...; role=..."）が生成音源で
「構造」（quiet-loud-quiet 等の区間エネルギー・パターン）として実現されたかを
測るための薄い比較器。**計器であって verdict なし** — tight/loose/dead の判定や
固定結論・narrative はこのモジュールの責務外で、処方パターンとの一致率のみを
返す（判定は order_sheet / plan.yaml の事前登録規約を設計側が適用する）。

事前登録規約（発注書 §5 verbatim）: 各区間の **RMS（線形振幅）** を全区間 RMS の
**算術平均**と比較して high/low に符号化する。dB 域での平均比較は線形域の
幾何平均に相当する**別統計**であり、本計器では採用しない（dB は表示用のみ、
`rms_to_db` ヘルパーを呼び出し側で適用する）。

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

# knife-edge 判定の閾値（K2-seg バッチ 3 発注書 §5, 2026-07-10 事前登録）。
# バッチ 2 実測 knife-edge（low_2_1 第 1 区間・3 区間平均の ±0.06% 境界上・
# リサンプル条件で符号反転）の約 8 倍の保守マージン。区間 margin の絶対値が
# これ未満なら knife-edge（符号が計測条件で反転し得る不安定区間）とみなす。
KNIFE_EDGE_MARGIN = 0.005


def split_section_rms(y: np.ndarray, n_sections: int) -> list[float]:
    """トラックをサンプル数で `n_sections` 等分し、各区間の一括 RMS（線形）を返す。

    絶対秒でなく相対位置での等分（曲長が生成器依存で統制不能なため、
    比例分割で吸収する）。返り値は線形振幅 RMS — `sign_pattern` の入力に
    そのまま使う。表示用の dB 変換は `rms_to_db` を呼び出し側で適用する。
    """
    if n_sections <= 0:
        raise ValueError("n_sections must be positive")

    sections = np.array_split(np.asarray(y, dtype=np.float64), n_sections)
    values: list[float] = []
    for section in sections:
        if section.size == 0:
            values.append(0.0)
            continue
        values.append(float(np.sqrt(np.mean(np.square(section)))))
    return values


def rms_to_db(rms: float) -> float:
    """線形 RMS を dBFS へ変換する表示用ヘルパー（比較には使わない）。"""

    return round(20.0 * float(np.log10(max(rms, _RMS_DB_FLOOR))), 4)


def sign_pattern(rms_linear: Sequence[float]) -> list[str]:
    """線形 RMS 値を全区間の算術平均と比較し high/low に符号化する。

    事前登録規約: **線形 RMS の算術平均**比較（発注書 §5 verbatim）。
    dB 域での平均比較は線形域の幾何平均に相当する別統計であり、ここでは行わない。
    """

    values = list(rms_linear)
    if not values:
        raise ValueError("rms_linear must be non-empty")
    average = sum(values) / len(values)
    return ["high" if value >= average else "low" for value in values]


def section_margins(rms_linear: Sequence[float]) -> list[float]:
    """各区間の線形 RMS が算術平均からどれだけ離れているかを相対値で返す。

    `(value - average) / average` — `sign_pattern` の符号化を支える連続量で、
    knife-edge（符号が計測条件で反転し得る境界上の区間）を検出するための
    計器。**計器であって verdict なし** — tight/loose/dead の判定はここでは
    行わない（`KNIFE_EDGE_MARGIN` との比較は呼び出し側の責務）。

    算術平均が 0 の縮退時は全区間 0.0 を返す（`sign_pattern` のゼロ分散
    ケースに揃えた安全側の扱い — ゼロ除算を避けつつ「差なし」を表現する）。
    """

    values = list(rms_linear)
    if not values:
        raise ValueError("rms_linear must be non-empty")
    average = sum(values) / len(values)
    if average == 0.0:
        return [0.0 for _ in values]
    return [(value - average) / average for value in values]


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
