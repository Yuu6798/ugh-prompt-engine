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


def ok_or_raise(task: "Dict[str, Any]") -> "Dict[str, Any]":
    """`task['id']` が `"raises"` で始まれば例外、そうでなければ即座に成功する fake。

    E-77 回帰テスト用: `workers=1` で「1 セル成功 → 次のセルで例外」という
    混在シナリオを決定論的に作る（`on_worker_error` フックが直前までの
    `completed` を正しく受け取ることの検証）。
    """
    if str(task.get("id", "")).startswith("raises"):
        raise RuntimeError("fake shard worker failure")
    return {"resumed": False, "measured": True, "mismatches": [], "outcome": "measured"}


def sleep_then_ok_or_raise(task: "Dict[str, Any]") -> "Dict[str, Any]":
    """`task['actual_duration_s']` 秒 sleep してから `ok_or_raise` と同じ判定をする。

    E-80 回帰テスト用: 2 セルへ同じ `actual_duration_s` を与えて同じ実時間に
    dispatch すると、ほぼ同時に ready になる——「同一ポーリングで複数の
    AsyncResult が同時に ready、うち 1 件が例外」という状況を、実プロセスの
    タイミングに頼りつつ高確率で再現する（`ok_or_raise` は即時のため、2 セルの
    ready タイミングが分散しやすく本シナリオを狙って再現しにくい）。
    """
    duration = float(task.get("actual_duration_s", task["cost"]))
    time.sleep(duration)
    if str(task.get("id", "")).startswith("raises"):
        raise RuntimeError("fake shard worker failure")
    return {"resumed": False, "measured": True, "mismatches": [], "outcome": "measured"}


def unavailable(task: "Dict[str, Any]") -> "Dict[str, Any]":
    """`outcome == "unavailable"` を返す fake（E-46 回帰テスト用）。

    `_measure_or_resume_external_clip_row` が抽出器スタック未導入セルに対して
    行う「チェックポイントを書かない」を模す——この fake もセルレコードを一切
    書かない。ワーカーとしては正常に返る（例外を投げない）点が `raise_error` との
    違い——キュー機構からは「完了」扱いになるが、`execute_m2e_shard` はこれを
    `cells_completed` から除外し `cells_unavailable` へ計上しなければならない。
    """
    return {
        "resumed": False,
        "measured": False,
        "mismatches": [],
        "outcome": "unavailable",
        "detail": "fake unavailable for E-46 regression test",
    }

