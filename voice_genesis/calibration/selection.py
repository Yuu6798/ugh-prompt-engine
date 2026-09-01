"""selection split 上の族別 lexicographic 選択規則（設計正本 §9）。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

from voice_genesis.calibration.vocab import ClaimCeiling


class SelectionFamily(str, Enum):
    ABSOLUTE = "ABSOLUTE"
    DIRECTIONAL = "DIRECTIONAL"


def round_error(x: float) -> float:
    """error 系: 有効数字 3 桁への丸め。"""
    if x == 0 or not math.isfinite(x):
        return x
    digits = 2 - int(math.floor(math.log10(abs(x))))
    return round(x, digits)


def round_rate(x: float) -> float:
    """rate/sensitivity 系: 0.001 刻みへの丸め。"""
    if not math.isfinite(x):
        return x
    return round(round(x / 0.001) * 0.001, 3)


@dataclass(frozen=True)
class CandidateCriteria:
    """1 候補の criterion 生値。ABSOLUTE 族と DIRECTIONAL 族で使うフィールドが
    異なる（select() が family に応じて必要なフィールドを検証する）。"""

    candidate_id: str
    eligible: bool = True
    complexity_rank: int = 0
    nuisance_sensitivity_max: float = 0.0
    missing_failure_rate: float = 0.0
    # ceiling 階級間裁定 (select_across_ceilings) が使う。個別 select() は
    # family を明示指定するため参照しない。
    ceiling: ClaimCeiling | None = None
    # ABSOLUTE 族
    primary_normalized_mae: float | None = None
    signed_bias: float | None = None
    primary_q95_ae: float | None = None
    # DIRECTIONAL 族
    kendall_tau: float | None = None
    adjacent_reversal_rate: float | None = None


def _vector_for(
    criteria: CandidateCriteria, family: SelectionFamily, *, rounded: bool
) -> tuple[float | int | str, ...]:
    err = round_error if rounded else (lambda v: v)
    rate = round_rate if rounded else (lambda v: v)
    if family is SelectionFamily.ABSOLUTE:
        if (
            criteria.primary_normalized_mae is None
            or criteria.signed_bias is None
            or criteria.primary_q95_ae is None
        ):
            raise ValueError(
                f"selection: candidate {criteria.candidate_id!r} missing ABSOLUTE"
                " criteria fields"
            )
        return (
            err(criteria.primary_normalized_mae),
            err(abs(criteria.signed_bias)),
            err(criteria.primary_q95_ae),
            rate(criteria.nuisance_sensitivity_max),
            rate(criteria.missing_failure_rate),
            criteria.complexity_rank,
            criteria.candidate_id,
        )
    if criteria.kendall_tau is None or criteria.adjacent_reversal_rate is None:
        raise ValueError(
            f"selection: candidate {criteria.candidate_id!r} missing DIRECTIONAL"
            " criteria fields"
        )
    return (
        rate(1 - criteria.kendall_tau),
        rate(criteria.adjacent_reversal_rate),
        rate(criteria.nuisance_sensitivity_max),
        rate(criteria.missing_failure_rate),
        criteria.complexity_rank,
        criteria.candidate_id,
    )


@dataclass(frozen=True)
class SelectionOutcome:
    family: SelectionFamily
    selected_candidate_id: str | None
    ranked_candidate_ids: tuple[str, ...]
    raw_vectors: Mapping[str, tuple[float | int | str, ...]]
    rounded_vectors: Mapping[str, tuple[float | int | str, ...]]
    outcome: str


def select(
    candidates: Sequence[CandidateCriteria], family: SelectionFamily
) -> SelectionOutcome:
    """selection split のみから、族別 lexicographic 比較で 1 候補を選ぶ。

    比較は **丸め後の vector** で行う（有効数字 3 桁 / 0.001 刻み / complexity は
    整数のまま）。丸め前後の vector を両方とも `SelectionOutcome` に記録する
    （`SELECTION_FROZEN` event 用）。eligible な候補が 1 件もなければ
    `SELECTION_FAILED_CLOSED`（候補選択なし・meter ceiling は上限
    NOT_EVALUABLE として呼び出し側が扱う）。
    """
    eligible = [c for c in candidates if c.eligible]
    raw_vectors = {c.candidate_id: _vector_for(c, family, rounded=False) for c in candidates}
    rounded_vectors = {
        c.candidate_id: _vector_for(c, family, rounded=True) for c in candidates
    }

    if not eligible:
        return SelectionOutcome(
            family=family,
            selected_candidate_id=None,
            ranked_candidate_ids=(),
            raw_vectors=raw_vectors,
            rounded_vectors=rounded_vectors,
            outcome="SELECTION_FAILED_CLOSED",
        )

    ranked = sorted(eligible, key=lambda c: rounded_vectors[c.candidate_id])
    ranked_ids = tuple(c.candidate_id for c in ranked)
    return SelectionOutcome(
        family=family,
        selected_candidate_id=ranked_ids[0],
        ranked_candidate_ids=ranked_ids,
        raw_vectors=raw_vectors,
        rounded_vectors=rounded_vectors,
        outcome="SELECTED",
    )


def select_across_ceilings(candidates: Sequence[CandidateCriteria]) -> SelectionOutcome:
    """ceiling 階級間の裁定規則（§2.6 で凍結。Codex レビュー 2026-09-01 第 4 巡採用）。

    1. eligible かつ `ceiling == ABSOLUTE` の候補プールが非空なら、**そのプール
       のみ**で ABSOLUTE 族 selection（`select()`）を行う。
    2. 空なら eligible かつ `ceiling == DIRECTIONAL` の候補プールで
       DIRECTIONAL 族 selection を行う。
    3. 両プールとも空なら `SELECTION_FAILED_CLOSED`。

    `ceiling == DIAGNOSTIC_ONLY`（または `ceiling` 未設定）の候補は、たとえ
    `eligible=True` であっても **いかなる場合も選抜対象に入らない**
    （ABSOLUTE pool が空で DIRECTIONAL pool も空なら、DIAGNOSTIC_ONLY 候補が
    どれだけ criteria 上優れていても selection は FAILED_CLOSED になる）。

    数値上 DIRECTIONAL 候補の criteria が ABSOLUTE 候補より良く見えても、
    ceiling が高い ABSOLUTE pool が非空である限りそちらを優先する
    （ceiling そのものが選抜の第一階層であり、criteria 比較より優先される）。
    """
    absolute_pool = [
        c for c in candidates if c.eligible and c.ceiling == ClaimCeiling.ABSOLUTE
    ]
    if absolute_pool:
        return select(absolute_pool, SelectionFamily.ABSOLUTE)

    directional_pool = [
        c for c in candidates if c.eligible and c.ceiling == ClaimCeiling.DIRECTIONAL
    ]
    if directional_pool:
        return select(directional_pool, SelectionFamily.DIRECTIONAL)

    return select([], SelectionFamily.ABSOLUTE)
