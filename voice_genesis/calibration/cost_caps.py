"""Phase D1: cost cap / stop rule の loader と超過判定（設計正本 §14, §18 Gate 1）。

3 値（compute 秒数・storage bytes・課金 budget 単位）は Gate 1 承認ファイル
（`approvals.ApprovalRecord`, gate=GATE1_CAMPAIGN_EXECUTION）の
`cost_caps` payload から得る。フィールド名は `c0_validate.COST_CAPS_REQUIRED_KEYS`
（`("compute", "storage", "budget")`）と一致させる（PR レビュー第 2 巡採用:
以前案の `compute_seconds`/`storage_bytes`/`budget_units` は validator 正本の
キー名と不一致だった）。単位: `compute` = 秒（float, CPU-seconds）、
`storage` = bytes（int）、`budget` = 課金 budget 単位（float, ユーザー環境の
通貨/クレジット単位。具体的な通貨は承認ファイル記入時にユーザーが宣言する）。

本モジュールは cap 値の生成・実行判断は一切行わない（設計正本 §0 授権境界:
cap の値の決定と実行 Go はユーザー判断）。ここにあるのは (1) 承認済み値を
frozen dataclass として保持する型、(2) 累積カウンタ、(3) 超過判定のみ。

round 13 finding #3 (`[UNDERSPEC-CAL-D27]`): `CostCaps.budget` の 3 値
loader/超過判定は元から存在したが、`budget_used` を実際に積み上げる
会計規則がどこにも実装されておらず `budget` cap は常に死んでいた
（`render_stage.py`/`measure_stage.py` は `compute`/`storage` のみ
`cap_counters.add()` していた）。`budget_accounting_mode` を cost cap
宣言自体の一部（closed vocabulary、Gate 1 承認 payload に必須）として
凍結し、会計規則を明示的に宣言させる:

- `"local_zero_cost"`: 本キャンペーンはローカル計算資源のみで課金対象の
  外部リソースを一切使わない、という宣言。各 work unit の budget charge は
  常に 0（budget cap は non-binding — stop/plan 出力にその旨を明記する）。
- `"per_unit_fixed"`: `budget_unit_cost`（正の float）を必須とし、render・
  measurement の各 work unit が一律この額を `budget_used` へ加算する。

mode が欠落・未知語彙なら `BudgetAccountingUndeclaredError`
（`CODE = "BUDGET_ACCOUNTING_UNDECLARED"`）で fail-closed する——`budget`
cap を暗黙に non-binding とみなして黙って動き続けることを防ぐ（cost caps
セクション自体が manifest に無い場合＝ Gate 1 未承認は本エラーの対象外。
その場合は従来通り `campaign.caps.cost_caps_from_manifest()` が `None` を
返し「cap 未凍結」として扱う）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: closed vocabulary（round 13 finding #3）。事後追加は本ファイルの
#: docstring 改訂を伴う設計判断とする。
BUDGET_ACCOUNTING_LOCAL_ZERO_COST = "local_zero_cost"
BUDGET_ACCOUNTING_PER_UNIT_FIXED = "per_unit_fixed"
BUDGET_ACCOUNTING_MODES: frozenset[str] = frozenset(
    {BUDGET_ACCOUNTING_LOCAL_ZERO_COST, BUDGET_ACCOUNTING_PER_UNIT_FIXED}
)


class BudgetAccountingUndeclaredError(ValueError):
    """round 13 finding #3: `cost_caps.budget_accounting_mode` が欠落/閉語彙
    外の場合の fail-closed error。呼び出し側（`campaign/cli.py`）はこれを
    捕捉し、`CODE` を dispatch 拒否の distinct reason として ledger
    `stop_event`/CLI 出力へそのまま使う。"""

    CODE = "BUDGET_ACCOUNTING_UNDECLARED"


@dataclass(frozen=True)
class CostCaps:
    """§14 の 3 値 + round 13 finding #3 の budget accounting 宣言。Gate 1
    承認ファイルの `cost_caps` payload と同じキー
    (`compute`/`storage`/`budget`/`budget_accounting_mode`/
    `budget_unit_cost`) を持つ（`compute`/`storage`/`budget` は
    `c0_validate.COST_CAPS_REQUIRED_KEYS` と一致）。"""

    compute: float
    """CPU-seconds 換算の compute 上限。"""
    storage: int
    """bytes 換算の storage 上限。"""
    budget: float
    """課金 budget 単位の上限（通貨はユーザー宣言。本モジュールは無次元数として扱う）。"""
    budget_accounting_mode: str
    """`BUDGET_ACCOUNTING_MODES` の閉語彙。budget_used への加算規則を宣言する。"""
    budget_unit_cost: float | None = None
    """`budget_accounting_mode="per_unit_fixed"` の場合のみ必須（正の float）。
    `"local_zero_cost"` では `None` でなければならない（値を持たせても
    無視されず不整合として拒否する——曖昧な二重定義を避けるため）。"""

    def __post_init__(self) -> None:
        if not (math.isfinite(self.compute) and self.compute > 0.0):
            raise ValueError("CostCaps.compute must be a finite positive number of seconds")
        if not (isinstance(self.storage, int) and not isinstance(self.storage, bool) and self.storage > 0):
            raise ValueError("CostCaps.storage must be a positive int (bytes)")
        if not (math.isfinite(self.budget) and self.budget > 0.0):
            raise ValueError("CostCaps.budget must be a finite positive budget value")
        if self.budget_accounting_mode not in BUDGET_ACCOUNTING_MODES:
            raise BudgetAccountingUndeclaredError(
                "CostCaps.budget_accounting_mode must be one of "
                f"{sorted(BUDGET_ACCOUNTING_MODES)}, got {self.budget_accounting_mode!r}"
            )
        if self.budget_accounting_mode == BUDGET_ACCOUNTING_PER_UNIT_FIXED:
            if self.budget_unit_cost is None or not (
                math.isfinite(self.budget_unit_cost) and self.budget_unit_cost > 0.0
            ):
                raise ValueError(
                    "CostCaps.budget_unit_cost must be a finite positive number when "
                    f"budget_accounting_mode={BUDGET_ACCOUNTING_PER_UNIT_FIXED!r}"
                )
        elif self.budget_unit_cost is not None:
            raise ValueError(
                "CostCaps.budget_unit_cost must be unset (None) when "
                f"budget_accounting_mode={BUDGET_ACCOUNTING_LOCAL_ZERO_COST!r}"
            )

    def as_dict(self) -> dict[str, float | int | str | None]:
        return {
            "compute": self.compute,
            "storage": self.storage,
            "budget": self.budget,
            "budget_accounting_mode": self.budget_accounting_mode,
            "budget_unit_cost": self.budget_unit_cost,
        }

    def budget_charge_per_work_unit(self) -> float:
        """1 work unit（render 呼び出し 1 回、または measurement 呼び出し
        1 回。`cap_counters.add()` の既存 compute/storage 会計と同じ粒度）
        あたりの budget charge。`local_zero_cost` は常に 0.0、
        `per_unit_fixed` は `budget_unit_cost`（`__post_init__` が正の値を
        保証済み）。"""
        if self.budget_accounting_mode == BUDGET_ACCOUNTING_PER_UNIT_FIXED:
            assert self.budget_unit_cost is not None  # enforced by __post_init__
            return self.budget_unit_cost
        return 0.0


def cost_caps_from_mapping(payload: dict[str, object]) -> CostCaps:
    """Gate 1 承認ファイルの `cost_caps` JSON object から `CostCaps` を構築する。

    欠落・型不正はここで `KeyError`/`ValueError`/`TypeError` として fail-closed する
    （承認ファイル loader 側 `approvals.py` が shape 検証済みの mapping を渡す想定だが、
    本関数単体でも防御的に検証する）。`budget_accounting_mode` の欠落/閉語彙外は
    `BudgetAccountingUndeclaredError`（`ValueError` のサブクラス）として区別する
    （round 13 finding #3）。
    """
    mode = payload.get("budget_accounting_mode")
    if not isinstance(mode, str) or mode not in BUDGET_ACCOUNTING_MODES:
        raise BudgetAccountingUndeclaredError(
            "cost_caps.budget_accounting_mode is missing or unknown: "
            f"{mode!r} (must be one of {sorted(BUDGET_ACCOUNTING_MODES)})"
        )
    raw_unit_cost = payload.get("budget_unit_cost")
    budget_unit_cost = None if raw_unit_cost is None else float(raw_unit_cost)
    return CostCaps(
        compute=float(payload["compute"]),
        storage=int(payload["storage"]),
        budget=float(payload["budget"]),
        budget_accounting_mode=mode,
        budget_unit_cost=budget_unit_cost,
    )


@dataclass
class CapCounters:
    """実行時に累積するカウンタ（mutable accumulator）。D2 runner が更新する想定だが、
    D1 の責務は型と `check()` の提供のみ（実際の累積は本 Phase の範囲外）。"""

    compute_used: float = 0.0
    storage_used: int = 0
    budget_used: float = 0.0

    def add(self, *, compute: float = 0.0, storage: int = 0, budget: float = 0.0) -> None:
        self.compute_used += compute
        self.storage_used += storage
        self.budget_used += budget

    def as_dict(self) -> dict[str, float | int]:
        return {
            "compute_used": self.compute_used,
            "storage_used": self.storage_used,
            "budget_used": self.budget_used,
        }


@dataclass(frozen=True)
class StopDecision:
    """cap 超過を検出した際の stop event payload（設計正本 §14: 超過で stop event、
    fail-closed、結果不完全のまま閉鎖）。"""

    exceeded_dims: tuple[str, ...]
    detail: str
    event_payload: dict[str, object] = field(default_factory=dict)


def check(counters: CapCounters, caps: CostCaps) -> StopDecision | None:
    """`counters` が `caps` のいずれかの次元を超過していれば `StopDecision` を返す。
    超過なしなら `None`（呼び出し側はキャンペーンを続行してよい）。

    比較は `>` ではなく `>=` を使わない（境界値 = ちょうど cap は超過ではない。
    `gates.py` の `<=0` PASS 境界規約と対称に、caps 消費側は「cap を厳密に超えた
    ときのみ超過」とする — キャンペーンが cap をちょうど使い切って正常終了する
    正当なケースを誤って fail-closed にしないため）。
    """
    exceeded: list[str] = []
    if counters.compute_used > caps.compute:
        exceeded.append("compute")
    if counters.storage_used > caps.storage:
        exceeded.append("storage")
    if counters.budget_used > caps.budget:
        exceeded.append("budget")
    if not exceeded:
        return None
    detail = "; ".join(
        f"{dim}: used={getattr(counters, dim + '_used')!r} > cap={getattr(caps, dim)!r}"
        for dim in exceeded
    )
    return StopDecision(
        exceeded_dims=tuple(exceeded),
        detail=detail,
        event_payload={
            "kind": "stop_event",
            "reason": "COST_CAP_EXCEEDED",
            "exceeded_dims": list(exceeded),
            "counters": counters.as_dict(),
            "caps": caps.as_dict(),
            "detail": detail,
        },
    )


__all__ = [
    "CostCaps",
    "cost_caps_from_mapping",
    "CapCounters",
    "StopDecision",
    "check",
    "BUDGET_ACCOUNTING_LOCAL_ZERO_COST",
    "BUDGET_ACCOUNTING_PER_UNIT_FIXED",
    "BUDGET_ACCOUNTING_MODES",
    "BudgetAccountingUndeclaredError",
]
