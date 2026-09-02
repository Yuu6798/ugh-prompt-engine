"""観測量の定義式（設計正本 §10.1）。"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import TypeVar

import numpy as np

ProcessId = TypeVar("ProcessId", bound=Hashable)


def two_stage_median(x: Mapping[ProcessId, Sequence[float]]) -> float:
    """`m[i] = median_p( median_r( x_hat[i,p,r] ) )`。

    process 間で repeat 数が不均等な場合に、repeat 数の多い process が支配的に
    ならないよう、まず process ごとに repeat の中央値を取り、それらの中央値を
    process 間でさらに中央値化する（二段 median）。
    """
    per_process_medians = [
        float(np.median(np.asarray(repeats, dtype=float)))
        for repeats in x.values()
        if len(repeats) > 0
    ]
    if not per_process_medians:
        raise ValueError("two_stage_median: no process has any repeats")
    return float(np.median(np.asarray(per_process_medians, dtype=float)))


@dataclass(frozen=True)
class ErrorTerms:
    e: float
    ae: float
    re: float


def error_terms(m: float, truth: float, zero_guard: float) -> ErrorTerms:
    """`e = m - truth`, `AE = |e|`, `RE = AE / max(|truth|, zero_guard)`。

    signed construct が 0 近傍のとき RE を PASS 判定に使わず診断専用に留めるのは
    呼び出し側の責務（ここでは式のみを提供する）。
    """
    e = m - truth
    ae = abs(e)
    re = ae / max(abs(truth), zero_guard)
    return ErrorTerms(e=e, ae=ae, re=re)


def bias(errors: Sequence[float]) -> float:
    """`BIAS = mean_i(e[i])`。"""
    return float(np.mean(np.asarray(errors, dtype=float)))


def mae(errors: Sequence[float]) -> float:
    """`MAE = mean_i(|e[i]|)`。"""
    return float(np.mean(np.abs(np.asarray(errors, dtype=float))))


def q95(values: Sequence[float]) -> float:
    """95th percentile。`numpy.quantile(..., method="linear")` 固定
    （設計正本 §10.1: q95(method=linear 固定)）。"""
    return float(np.quantile(np.asarray(values, dtype=float), 0.95, method="linear"))


def u_rep(per_instance_per_process_ranges: Mapping[Hashable, Sequence[float]]) -> float | None:
    """`U_rep = q95_{i,p}( (max_r - min_r)/2 )`。key は (instance_id, process_id)
    等、値はその (i,p) セルの repeat 系列。

    repeat 数が 2 未満の (instance, process) セル（singleton）は range が
    **未定義**（0 ではない）ため q95 の母集団から除外する。singleton を range=0
    として算入すると、6-call 構成（within-process repeat=3 × fresh-process
    repeat=... 等）で repeat が構造的に欠けたセルが U_rep を不当に希釈し、
    実際には測れていない安定性を「測って安定していた」ことにしてしまう
    （設計正本 §6 の 6-call 構成に対する構造ゼロ希釈防止）。除外は U_rep を
    大きくする方向にしか働かない、fail 側の保守的な読みである。

    いずれかの repeat 値が NaN/Inf の場合は range 計算前に fail-closed として
    `None` を返す。Python の `max()`/`min()` は入力順によって NaN を事実上
    読み飛ばし有限 range を作り得るため、非有限値を U_rep=0 等へ縮退させない。

    全セルが singleton（有効な range が 1 件もない）の場合も計算不能として
    `None` を返す。呼び出し側はこれを NOT_EVALUABLE 系の missing 理由へ
    写像すること（`OUTPUT_NOT_EVALUABLE` 等）。
    """
    ranges: list[float] = []
    for repeats in per_instance_per_process_ranges.values():
        values = np.asarray(repeats, dtype=float)
        if values.size > 0 and not bool(np.all(np.isfinite(values))):
            return None
        if len(repeats) < 2:
            continue
        ranges.append((float(np.max(values)) - float(np.min(values))) / 2.0)
    if not ranges:
        return None
    return q95(ranges)


def u_proc(per_instance_process_medians: Mapping[Hashable, Sequence[float]]) -> float:
    """`U_proc`: 2 process の場合は `q95_i(|med1 - med2|)/2`。process が 3 以上の
    場合は「全 pair 差の q95」へ一般化する（§10.1）。

    key = instance_id、value = [median_process_1, median_process_2, ...]。
    """
    diffs: list[float] = []
    for medians in per_instance_process_medians.values():
        medians = list(medians)
        if len(medians) < 2:
            continue
        for a, b in combinations(medians, 2):
            diffs.append(abs(a - b))
    if not diffs:
        raise ValueError("u_proc: no instance has >=2 process medians")
    return q95(diffs) / 2.0


def nuisance_ds(anchor_error: float, varied_error: float) -> float:
    """`dS[a,pair] = |(m[ia]-x[ia]) - (m[i0]-x[i0])| = |varied_e - anchor_e|`。"""
    return abs(varied_error - anchor_error)


@dataclass(frozen=True)
class DetectionResult:
    fdr0: float
    fnr1: float
    n_neg: int
    n_pos: int
    min_count_met: bool
    control_gate: str = "APPLICABLE"


#: `detection_rates` へ渡す keyed outcome の入力形。`Mapping[instance_id, outcome]`
#: または `(instance_id, outcome)` の `Sequence` のいずれか（Codex レビュー
#: 2026-09-01 P1）。
KeyedOutcomes = Mapping[str, bool] | Sequence[tuple[str, bool]]


class DuplicateInstanceIdError(ValueError):
    """`detection_rates` に渡された keyed outcomes 内で `instance_id` が重複した
    ときの typed failure（Codex レビュー 2026-09-01 P1: 生の bool sequence では
    同一 instance を 10 回繰り返すだけで `N>=10` を水増しできてしまっていた。
    instance_id をキーとして扱うことで distinct instance のみを数え、重複
    出現を明示的に reject する）。呼び出し側はこれを捕捉して既存語彙
    (`vocab.BlockedCode`) の fail-closed コードへ写像する想定。
    """

    def __init__(self, kind: str, duplicate_ids: Sequence[str]) -> None:
        self.kind = kind
        self.duplicate_ids = tuple(duplicate_ids)
        super().__init__(
            f"detection_rates: duplicate instance_id(s) in {kind}_outcomes: "
            f"{', '.join(duplicate_ids)}"
        )



class InvalidControlOutcomeError(ValueError):
    """Raised when a keyed control outcome is not an actual ``bool``.

    Missing/invalid meter outputs must be mapped by the caller to the documented
    failure polarity before entering ``detection_rates``; arbitrary truthy values
    must never be interpreted as successful control evidence.
    """

    def __init__(self, kind: str, instance_id: str, outcome: object) -> None:
        self.kind = kind
        self.instance_id = instance_id
        self.outcome = outcome
        super().__init__(
            "detection_rates: non-boolean control outcome in "
            f"{kind}_outcomes for {instance_id!r}: {outcome!r}"
        )

def _normalize_keyed_outcomes(outcomes: KeyedOutcomes, kind: str) -> dict[str, bool]:
    """Normalize keyed outcomes while validating identity and value shape.

    ``Mapping`` keys are unique by construction; sequence form is checked for
    duplicate instance IDs. In both forms every outcome must be an actual ``bool``
    (``type(value) is bool``). Missing/invalid upstream outputs are part of the
    failure numerator by contract, so callers must map them to ``True`` for
    negative controls and ``False`` for positive controls before this boundary.
    Truthy sentinels such as NaN or error strings are rejected fail-closed.
    """
    items = outcomes.items() if isinstance(outcomes, Mapping) else outcomes
    seen: dict[str, bool] = {}
    duplicate_ids: list[str] = []
    for instance_id, outcome in items:
        if instance_id in seen:
            duplicate_ids.append(instance_id)
            continue
        if type(outcome) is not bool:
            raise InvalidControlOutcomeError(kind, instance_id, outcome)
        seen[instance_id] = outcome
    if duplicate_ids:
        raise DuplicateInstanceIdError(kind, sorted(set(duplicate_ids)))
    return seen


def detection_rates(
    neg_outcomes: KeyedOutcomes,
    pos_outcomes: KeyedOutcomes,
    *,
    control_gate: str = "APPLICABLE",
) -> DetectionResult:
    """`neg_outcomes[instance_id]` = True は negative control が「発火した」
    ことを示す (誤検出、または missing/invalid 出力 — §10.1: missing/invalid は
    分子に算入し、分母からは除外しない）。`pos_outcomes[instance_id]` = True は
    positive control が正しく発火したこと（False が FNR1 の分子）。

    `neg_outcomes` / `pos_outcomes` は `instance_id` をキーとする
    `Mapping[str, bool]`、または `(instance_id, outcome)` の `Sequence` として
    渡す（Codex レビュー 2026-09-01 P1: 生の bool sequence では 1 instance を
    10 回繰り返すだけで `N>=10` を水増しできてしまっていた）。カウント
    (`n_neg`/`n_pos`) は **distinct instance 数**であり、同一 `instance_id` の
    重複出現は `DuplicateInstanceIdError` で reject する（silently 潰さない）。
    negative / positive の二母集団も互いに素でなければならず、同一 instance ID
    を両側へ再ラベルした場合は `kind="cross_class"` の同例外で fail-closed にする。
    outcome 値は actual `bool` のみを受理する。NaN・error string・数値 sentinel 等の
    非 bool は `InvalidControlOutcomeError` で拒否し、truthy 値を positive-control の
    成功として誤認しない。missing/invalid は caller が failure polarity に写像して渡す。

    最小数 (`N_neg>=10` かつ `N_pos>=10`) を満たさない construct は結果を
    PASS 判定に使うべきではない（`min_count_met` で呼び出し側が判定する）。
    `control_gate="NOT_APPLICABLE"` の場合は C0 で事前宣言された非該当 construct
    であることをそのまま通過させる（呼び出し側が gate5 で分岐に使う）。
    """
    neg_map = _normalize_keyed_outcomes(neg_outcomes, "neg")
    pos_map = _normalize_keyed_outcomes(pos_outcomes, "pos")

    cross_class_ids = sorted(set(neg_map).intersection(pos_map))
    if cross_class_ids:
        raise DuplicateInstanceIdError("cross_class", cross_class_ids)

    n_neg = len(neg_map)
    n_pos = len(pos_map)
    fdr0 = (sum(1 for v in neg_map.values() if v) / n_neg) if n_neg > 0 else 0.0
    fnr1 = (sum(1 for v in pos_map.values() if not v) / n_pos) if n_pos > 0 else 0.0
    min_count_met = n_neg >= 10 and n_pos >= 10
    return DetectionResult(
        fdr0=fdr0,
        fnr1=fnr1,
        n_neg=n_neg,
        n_pos=n_pos,
        min_count_met=min_count_met,
        control_gate=control_gate,
    )


def failure_boundary(
    ordered_levels: Sequence[Hashable],
    pass_flags: Sequence[bool | None],
) -> tuple[Hashable | None, Hashable | None]:
    """事前順序軸上の `[last passing level, first failing level]`。補間なし。
    `None`（missing）は fail 扱い。

    全 level が PASS なら `first_fail=None`、全 level が FAIL なら
    `last_pass=None`。
    """
    if len(ordered_levels) != len(pass_flags):
        raise ValueError("failure_boundary: ordered_levels and pass_flags length mismatch")
    last_pass: Hashable | None = None
    first_fail: Hashable | None = None
    for level, flag in zip(ordered_levels, pass_flags):
        passed = bool(flag) if flag is not None else False
        if not passed:
            first_fail = level
            break
        last_pass = level
    return last_pass, first_fail
