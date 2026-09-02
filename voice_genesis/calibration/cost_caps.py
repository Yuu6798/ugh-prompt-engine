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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CostCaps:
    """§14 の 3 値。Gate 1 承認ファイルの `cost_caps` payload と同じ 3 キー
    (`compute`/`storage`/`budget`) を持つ（`c0_validate.COST_CAPS_REQUIRED_KEYS`
    と一致）。"""

    compute: float
    """CPU-seconds 換算の compute 上限。"""
    storage: int
    """bytes 換算の storage 上限。"""
    budget: float
    """課金 budget 単位の上限（通貨はユーザー宣言。本モジュールは無次元数として扱う）。"""

    def __post_init__(self) -> None:
        if not (math.isfinite(self.compute) and self.compute > 0.0):
            raise ValueError("CostCaps.compute must be a finite positive number of seconds")
        if not (isinstance(self.storage, int) and not isinstance(self.storage, bool) and self.storage > 0):
            raise ValueError("CostCaps.storage must be a positive int (bytes)")
        if not (math.isfinite(self.budget) and self.budget > 0.0):
            raise ValueError("CostCaps.budget must be a finite positive budget value")

    def as_dict(self) -> dict[str, float | int]:
        return {"compute": self.compute, "storage": self.storage, "budget": self.budget}


def cost_caps_from_mapping(payload: dict[str, object]) -> CostCaps:
    """Gate 1 承認ファイルの `cost_caps` JSON object から `CostCaps` を構築する。

    欠落・型不正はここで `KeyError`/`ValueError`/`TypeError` として fail-closed する
    （承認ファイル loader 側 `approvals.py` が shape 検証済みの mapping を渡す想定だが、
    本関数単体でも防御的に検証する）。
    """
    return CostCaps(
        compute=float(payload["compute"]),
        storage=int(payload["storage"]),
        budget=float(payload["budget"]),
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
]
