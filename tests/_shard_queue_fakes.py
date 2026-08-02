"""C6 キュー機構テスト専用の picklable top-level fake（`run_melody_accuracy` を import しない）。

`multiprocessing`（spawn context）は task/measure_fn を unpickle するために
`measure_fn.__module__` を child プロセスで import し直す。fake がこのファイルではなく
`tests/test_m2_accuracy_harness.py`（`run_melody_accuracy` を import 済み）に居ると、
子プロセスの起動ごとに重い import 連鎖（本環境で実測 ≈24s/回）を再生する。
キュー/許可式/打ち切りの機構テストは実測定を一切必要としないため、依存を stdlib
`time` だけに切り離す。
"""
from __future__ import annotations

import time
from typing import Any, Dict


def ok(task: "Dict[str, Any]") -> "Dict[str, Any]":
    """何も測らず即座に完了する fake。"""
    return {"resumed": False, "measured": True, "mismatches": [], "outcome": "measured"}


def sleep(task: "Dict[str, Any]") -> "Dict[str, Any]":
    """`task['actual_duration_s']`（無ければ `task['cost']`）秒だけ実際に sleep する fake。"""
    duration = float(task.get("actual_duration_s", task["cost"]))
    time.sleep(duration)
    return {"resumed": False, "measured": True, "mismatches": [], "outcome": "measured"}


def raise_error(task: "Dict[str, Any]") -> "Dict[str, Any]":
    """常に失敗する fake（例外伝播テスト用）。"""
    raise RuntimeError("fake shard worker failure")
