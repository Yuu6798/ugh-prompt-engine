"""M6 Identity Spec v2（設計正本 §12）。

M6 は独立物理 meter として扱わない。CLAIM_CRITICAL_SET の **全 member** が
CALIBRATED_ABSOLUTE の場合にのみ component vector / distance を構成する
（1 件でも非 ABSOLUTE・missing・ineligible なら、部分構成であっても distance を
一切出力せず NOT_EVALUABLE とする — Codex レビュー 2026-09-01 第 2 巡採用）。
出力は component vector / distance / 寄与 / status のみで、単一 TotalScore・
品質・人間知覚上の同一性・法的/生体認証 identity は決して主張しない。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from voice_genesis.calibration.observables import q95
from voice_genesis.calibration.vocab import (
    CLAIM_CRITICAL_SET,
    ClaimCeiling,
    MeterId,
    TerminalStatus,
)

Norm = Literal["L1", "L2"]


def component_u(u_gt: float, u_num: float, u_rep: float, u_proc: float, e_use: float) -> float:
    """`u_X[j] = (U_GT_X[j] + U_num_X[j] + U_rep_X[j] + U_proc_X[j]) / E_use[j]`。"""
    return (u_gt + u_num + u_rep + u_proc) / e_use


def _norm(values: Sequence[float], p: Norm) -> float:
    """`p` に応じた重みなしベクトルノルム（`||v||_1` または `||v||_2`）。"""
    arr = np.asarray(values, dtype=float)
    if p == "L1":
        return float(np.sum(np.abs(arr)))
    return float(np.sqrt(np.sum(arr**2)))


def _weighted_norm(values: Sequence[float], p: Norm) -> float:
    """`m6_distance` の component distance と**同一の `1/n` 等重み**を適用した
    p-norm（Codex レビュー 2026-09-01 採用: distance/uncertainty スケール
    不整合の修正）。

    `m6_distance` は `n = len(critical_ids)` として
    `weight = 1/n` を掛け、L1 は `weight * sum(|d|)`、L2 は
    `weight * sqrt(sum(d**2))` を返す（**`1/n**(1/p)` ではなく文字通り `1/n`**
    — 実装が実際に課している重みをそのまま踏襲する。標準的な p-norm の
    次元正規化 `1/n**(1/p)` ではない点に注意）。

    ここでの `_weighted_norm(v, p) = _norm(v, p) / n`（`n = len(v)`）は
    distance と全く同じ演算を pairwise uncertainty ベクトルにも適用したもの。
    `n=0`（空ベクトル）は寄与ゼロとして `0.0` を返す。
    """
    values = list(values)
    n = len(values)
    if n == 0:
        return 0.0
    return _norm(values, p) / n


def pair_uncertainty(u_a: Sequence[float], u_b: Sequence[float], p: Norm) -> float:
    """`U_obs_pair(A,B) = ||u_A||_p/n_A + ||u_B||_p/n_B`（sum-of-norms、
    `m6_distance` と同一の `1/n` 等重みを両端点それぞれに適用）。

    修正前は `_norm` が重みなし（distance に掛かる `1/n` を欠いた）p-norm を
    そのまま返していたため、`n` 成分の pair では `U_obs_pair` が `D_obs` に
    対して最大 `n` 倍過大になり得た（Codex レビュー 2026-09-01: n=3 の
    CLAIM_CRITICAL_SET で `T_null` を不当に緩めていた）。`_weighted_norm` で
    distance と同じ `1/n` を掛けることでスケールを揃える。

    整合性の導出（各終端の不確かさが component 差分の絶対値と一致する対称
    ケース）: `u_A = u_B = |d|`（`d` は `m6_distance` の `diff_normalized`
    ベクトル）のとき、
    ```
    U_obs_pair = weighted_norm(u_A,p) + weighted_norm(u_B,p)
               = ||d||_p/n + ||d||_p/n = 2 * ||d||_p/n = 2 * D_obs
    ```
    （`D_obs = weight * norm_p(d)` かつ `weight = 1/n` は `m6_distance` の式
    そのもの）。すなわち `D_obs == U_obs_pair/2` が p in {1,2} いずれでも
    成立する — スケールが揃っていることの検算式（`tests/test_m6.py` の
    consistency test で回帰検知する）。

    `||u_A + u_B||_p`（norm-of-sum）は明示的に棄却する（設計正本 §12: 三角
    不等式によりベクトルの向きが揃っていない限り `||u_A+u_B|| < ||u_A||+||u_B||`
    となり保守性を失う。この関係は両辺に同じ `1/n` 重みを掛けても不等号の
    向きは保存される）。
    """
    return _weighted_norm(u_a, p) + _weighted_norm(u_b, p)


def t_null(d_null: Sequence[float], u_null_pair: Sequence[float]) -> float:
    """`T_null = q95_k( D_null[k] + U_null_pair[k] )`。"""
    if len(d_null) != len(u_null_pair):
        raise ValueError("t_null: d_null and u_null_pair length mismatch")
    return q95([d + u for d, u in zip(d_null, u_null_pair)])


def distinct(d_obs: float, u_obs_pair: float, t_null_value: float) -> bool:
    """`distinct(A,B) <=> D_obs(A,B) - U_obs_pair(A,B) > T_null`（厳密不等号）。"""
    return (d_obs - u_obs_pair) > t_null_value


@dataclass(frozen=True)
class ComponentContribution:
    component_id: str
    value_a: float
    value_b: float
    diff_normalized: float
    contribution: float


@dataclass(frozen=True)
class M6Result:
    status: TerminalStatus
    distance: float | None
    components: tuple[ComponentContribution, ...]
    ceiling: ClaimCeiling = ClaimCeiling.DIRECTIONAL


def m6_distance(
    components_a: Mapping[MeterId, float],
    components_b: Mapping[MeterId, float],
    e_use: Mapping[MeterId, float],
    member_status: Mapping[MeterId, TerminalStatus],
    norm: Norm,
) -> M6Result:
    """[UNDERSPEC-CAL-08] 設計正本 §12 は M6 の component 識別子の型を規定しない。
    ここでは CLAIM_CRITICAL_SET が `vocab.MeterId` の frozenset であることに
    合わせ、component の key を `MeterId` に固定する（他の物理 meter の校正
    status と直接突合できる一貫性を優先）。

    CLAIM_CRITICAL_SET の全 member が `member_status` 上で CALIBRATED_ABSOLUTE
    であり、かつ各 member の値/E_use が `components_a`/`components_b`/`e_use` に
    揃っている場合にのみ distance を計算する。1 件でも欠けていれば
    **部分構成であっても component vector を含めて何も出力せず**
    `status=NOT_EVALUABLE, distance=None, components=()` を返す（設計正本 §12。
    Codex レビュー 2026-09-01 第 2 巡: 部分 ABSOLUTE 構成での distance 出力を
    明示的に禁止）。

    正規化は各 `e_use[member]`、重みは等重み（L1: 単純和 / L2: 二乗和の平方根、
    いずれも `1/n` の等重み）。重み学習は禁止（§12）。ceiling は常に
    `CALIBRATED_DIRECTIONAL`（物理量 absolute calibration を名乗らない）。
    """
    all_absolute = all(
        member_status.get(m) == TerminalStatus.CALIBRATED_ABSOLUTE for m in CLAIM_CRITICAL_SET
    )
    all_present = all(
        m in components_a and m in components_b and m in e_use for m in CLAIM_CRITICAL_SET
    )
    if not CLAIM_CRITICAL_SET or not all_absolute or not all_present:
        return M6Result(status=TerminalStatus.NOT_EVALUABLE, distance=None, components=())

    critical_ids = sorted(CLAIM_CRITICAL_SET, key=lambda m: m.value)
    contributions: list[ComponentContribution] = []
    normalized_diffs: list[float] = []
    for cid in critical_ids:
        diff_norm = (components_a[cid] - components_b[cid]) / e_use[cid]
        normalized_diffs.append(diff_norm)
        contributions.append(
            ComponentContribution(
                component_id=cid.value,
                value_a=components_a[cid],
                value_b=components_b[cid],
                diff_normalized=diff_norm,
                contribution=abs(diff_norm) if norm == "L1" else diff_norm**2,
            )
        )

    n = len(critical_ids)
    weight = 1.0 / n
    if norm == "L1":
        distance = weight * sum(abs(d) for d in normalized_diffs)
    else:
        distance = weight * math.sqrt(sum(d**2 for d in normalized_diffs))

    return M6Result(
        status=TerminalStatus.CALIBRATED_DIRECTIONAL,
        distance=distance,
        components=tuple(contributions),
    )
