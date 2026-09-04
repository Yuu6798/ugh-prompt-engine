"""R2: instance 境界でのスライス実行の共有ヘルパ
（design memo `design_runner_robustness.md` R2, IMPLEMENTATION_MAP_v1.md
§6 Phase D, `[UNDERSPEC-CAL-D79]`）。

`--time-budget-seconds N` を渡された stage は、dispatch 開始から N 秒
（wall-clock）経過したら新規 instance の dispatch を止める。既に
dispatch 済みの in-flight instance（`--workers>1` の worker pool 分含む）は
最後まで完走させる（「instance の途中では止めない」）。

`TimeBudget` は dispatch 開始時刻を基準にした経過時間の薄いラッパー
（`time.monotonic()` — wall-clock の後退・NTP 補正に影響されない）。1 回の
CLI dispatch の中で複数のサブフェーズ（例: C4 の `render_and_measure_holdout`
は render → family ごとの measure という複数ループへ分かれる）が **同一の
`TimeBudget` インスタンスを共有**することで、「予算切れ後は以降の全
サブフェーズが新規 instance を 1 件も dispatch しない」という合成が
各サブフェーズ側の変更なしに自動的に成り立つ（各サブフェーズは自分の
`expired()` を独立に見るだけでよい）。

`SliceStatus` は 1 回の instance-dispatch ループ（またはその集約）が
完走したか途中で止まったかの報告値。`instances_completed_this_run`/
`instances_remaining` はそのループが扱う instance 空間に閉じたローカルな
計数 — 複数サブフェーズを跨いだ集約はフィールド単位の合算で組み立てる
（`SliceStatus.aggregate()`）。"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TimeBudget:
    """dispatch 開始時刻を基準にした wall-clock 予算。"""

    seconds: float
    start_monotonic: float

    @classmethod
    def start_now(cls, seconds: float) -> "TimeBudget":
        if not (seconds > 0.0):
            raise ValueError(f"TimeBudget: seconds must be > 0.0, got {seconds!r}")
        return cls(seconds=seconds, start_monotonic=time.monotonic())

    def elapsed(self) -> float:
        return time.monotonic() - self.start_monotonic

    def expired(self) -> bool:
        return self.elapsed() >= self.seconds


@dataclass(frozen=True)
class SliceStatus:
    """1 回の instance-dispatch ループの完走可否レポート
    （CLI report の `slice` フィールドの型そのもの — memo R2 参照。
    `completed_all` は report には出さない内部制御用フィールドで、
    呼び出し元が phase transition を行ってよいかどうかの判定に使う）。"""

    time_budget_seconds: float
    elapsed_seconds: float
    instances_completed_this_run: int
    instances_remaining: int
    completed_all: bool

    def as_report_dict(self) -> dict[str, float | int]:
        """memo R2 の `slice: {time_budget_seconds, elapsed_seconds,
        instances_completed_this_run, instances_remaining}` そのもの
        （`completed_all` は含めない — CLI report 外の内部フィールド）。"""
        return {
            "time_budget_seconds": self.time_budget_seconds,
            "elapsed_seconds": self.elapsed_seconds,
            "instances_completed_this_run": self.instances_completed_this_run,
            "instances_remaining": self.instances_remaining,
        }

    @staticmethod
    def aggregate(statuses: Sequence["SliceStatus"]) -> "SliceStatus":
        """複数サブフェーズ（例: C4 の render + family ごとの measure）の
        `SliceStatus` を 1 つの stage-level report へ合成する。同一
        `TimeBudget` を共有しているため、予算切れ以降のサブフェーズは
        自分の instance 総数をそのまま `instances_remaining` として報告する
        （0 件しか dispatch していないので `instances_completed_this_run=0`）
        — 加算するだけで stage 全体の完走可否・進捗が正しく合成される。"""
        if not statuses:
            raise ValueError("SliceStatus.aggregate: statuses must be non-empty")
        return SliceStatus(
            time_budget_seconds=statuses[0].time_budget_seconds,
            elapsed_seconds=max(s.elapsed_seconds for s in statuses),
            instances_completed_this_run=sum(s.instances_completed_this_run for s in statuses),
            instances_remaining=sum(s.instances_remaining for s in statuses),
            completed_all=all(s.completed_all for s in statuses),
        )


__all__ = ["TimeBudget", "SliceStatus"]
