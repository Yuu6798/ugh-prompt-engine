"""Canonical TODO(transcribe): sentinel for CompositionScore physical fields.

転写 (transcribe) 層はセンサーが盲目なとき physical フィールドへ
``TODO(transcribe): ...`` 文字列を書き込む。fixity / roundtrip / audit の
各下流層はこのセンチネルの検出規約に一致している必要があるため、prefix と
判定ヘルパの **single source of truth** を本モジュールに集約する。

依存を持たない leaf モジュールとして保ち、compose / transcribe / roundtrip /
semantic_ci のどこからでも循環なしに import できるようにする。
"""
from __future__ import annotations

from typing import Any

TODO_SENTINEL_PREFIX = "TODO(transcribe):"


def is_todo_sentinel(value: Any) -> bool:
    """``value`` が転写 TODO センチネル文字列かどうかを返す。"""

    return isinstance(value, str) and value.startswith(TODO_SENTINEL_PREFIX)
