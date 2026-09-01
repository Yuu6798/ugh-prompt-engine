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


def _has_required_criteria(criteria: CandidateCriteria, family: SelectionFamily) -> bool:
    """`family` が要求する criteria フィールドが揃っているか（Codex レビュー
    2026-09-01 P1: `_vector_for()` が全候補に対して無条件に呼ばれており、
    criteria payload が欠けた候補（例: pyworld 未導入時の D4C 系候補で
    測定基準がそもそも存在しない）に対して ValueError を送出し、
    fail-closed/ranking パスに到達する前に選抜全体を壊していた）。"""
    if family is SelectionFamily.ABSOLUTE:
        return (
            criteria.primary_normalized_mae is not None
            and criteria.signed_bias is not None
            and criteria.primary_q95_ae is not None
        )
    return criteria.kendall_tau is not None and criteria.adjacent_reversal_rate is not None


def _ineligibility_reason(
    criteria: CandidateCriteria, family: SelectionFamily, *, has_criteria: bool
) -> str | None:
    """`(candidate_id, reason)` として `SelectionOutcome.ineligible_candidates`
    に記録する理由文字列。eligible かつ criteria が揃っている候補には `None`
    を返す（`[UNDERSPEC-CAL]` 設計正本は ineligible 理由の具体的な語彙までは
    規定しないため、「flagged so」/「criteria payload absent」の 2 値をその
    まま機械可読な定数へ落とした最も単純な選択）。criteria 欠落を flagged
    ineligible より優先して報告する（欠落の方がより具体的な診断情報のため）。
    """
    if not has_criteria:
        return "criteria_payload_absent"
    if not criteria.eligible:
        return "flagged_ineligible"
    return None


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
    #: eligible ではなかった候補の `(candidate_id, reason)`（Codex レビュー
    #: 2026-09-01 P1）。reason は `"criteria_payload_absent"` または
    #: `"flagged_ineligible"` のいずれか（`_ineligibility_reason` 参照）。
    ineligible_candidates: tuple[tuple[str, str], ...] = ()


def _check_unique_candidate_ids(candidates: Sequence[CandidateCriteria]) -> None:
    """candidate_id 重複を検出したら即 raise する（Codex レビュー 2026-09-01
    採用）: dict comprehension で candidate_id をキーにすると重複時に一方が
    黙って上書きされ、selection の再現性が壊れる。"""
    seen: set[str] = set()
    duplicates: list[str] = []
    for c in candidates:
        if c.candidate_id in seen and c.candidate_id not in duplicates:
            duplicates.append(c.candidate_id)
        seen.add(c.candidate_id)
    if duplicates:
        raise ValueError(
            "selection: duplicate candidate_id(s): " + ", ".join(sorted(duplicates))
        )


def select(
    candidates: Sequence[CandidateCriteria], family: SelectionFamily
) -> SelectionOutcome:
    """selection split のみから、族別 lexicographic 比較で 1 候補を選ぶ。

    比較は **丸め後の vector** で行う（有効数字 3 桁 / 0.001 刻み / complexity は
    整数のまま）。丸め前後の vector を両方とも `SelectionOutcome` に記録する
    （`SELECTION_FROZEN` event 用）。ranking vector は **criteria payload が
    揃っている候補**についてのみ構築する（Codex レビュー 2026-09-01 P1:
    従来は `eligible` フラグに関わらず全候補に対して無条件に vector を構築
    しており、criteria payload がそもそも欠けた候補（例: pyworld 未導入時の
    D4C 系候補）で `ValueError` を送出し、fail-closed/ranking パスに到達する
    前に選抜全体を壊していた）。候補は次のいずれかに該当すると ineligible
    として扱う（`_ineligibility_reason` 参照）: (1) `criteria payload absent`
    — family が要求する criteria フィールドが欠けている、(2) `flagged so`
    — `eligible=False` と明示されている（criteria 自体は揃っていてもよく、
    この場合は監査目的で vector を構築する）。ineligible な候補は
    `(candidate_id, reason)` として `SelectionOutcome.ineligible_candidates`
    に記録し、ranking プールからは除外する。

    eligible な候補が 1 件もなければ `SELECTION_FAILED_CLOSED`（候補選択
    なし・meter ceiling は上限 NOT_EVALUABLE として呼び出し側が扱う）。
    candidate_id が重複する候補が含まれる場合は `raw_vectors`/
    `rounded_vectors` の dict キーが黙って上書きされるのを防ぐため
    `ValueError` を送出する（Codex レビュー 2026-09-01 採用）。
    """
    _check_unique_candidate_ids(candidates)

    raw_vectors: dict[str, tuple[float | int | str, ...]] = {}
    rounded_vectors: dict[str, tuple[float | int | str, ...]] = {}
    eligible: list[CandidateCriteria] = []
    ineligible: list[tuple[str, str]] = []

    for c in candidates:
        has_criteria = _has_required_criteria(c, family)
        if has_criteria:
            # criteria が揃っている限り、eligible フラグに関わらず vector を
            # 構築する（SELECTION_FROZEN event の全候補監査要件。ineligible
            # だが criteria を持つ候補も vector 自体は記録対象）。
            raw_vectors[c.candidate_id] = _vector_for(c, family, rounded=False)
            rounded_vectors[c.candidate_id] = _vector_for(c, family, rounded=True)
        reason = _ineligibility_reason(c, family, has_criteria=has_criteria)
        if reason is None:
            eligible.append(c)
        else:
            ineligible.append((c.candidate_id, reason))

    if not eligible:
        return SelectionOutcome(
            family=family,
            selected_candidate_id=None,
            ranked_candidate_ids=(),
            raw_vectors=raw_vectors,
            rounded_vectors=rounded_vectors,
            outcome="SELECTION_FAILED_CLOSED",
            ineligible_candidates=tuple(ineligible),
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
        ineligible_candidates=tuple(ineligible),
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

    candidate_id が入力全体で重複する場合は `select()` と同様に `ValueError`
    を送出する（pool 分割前の全候補で一意性を保証する）。
    """
    _check_unique_candidate_ids(candidates)
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
