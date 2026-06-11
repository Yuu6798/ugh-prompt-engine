"""CLAUDE.md が行数キャップ以内に収まっていることを検証する。

CLAUDE.md は毎ターン project instructions としてコンテキストに注入される
always-loaded policy doc であり、行数は固定コストかつ指示遵守劣化リスク。
ドキュメント管理ポリシー（CLAUDE.md §ドキュメント管理ポリシー）の
「目標: 400 行以内」を enforced ceiling として固定する。reference detail は
docs/ や skill（wrap-up / new-brief）に逃がしてポインタ化する。
"""

from __future__ import annotations

import pytest

from ._helpers import REPO_ROOT

CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
CLAUDE_MD_LINE_CAP = 400


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _assert_line_cap(text: str, *, source: str, cap: int) -> None:
    count = _line_count(text)
    assert count <= cap, (
        f"{source} が {count} 行で {cap} 行キャップを超過。\n"
        "CLAUDE.md は always-loaded policy: reference detail（ツリー、長い表、"
        "多段手順）は docs/ または skill に移し、ポインタだけ残すこと。"
    )


def test_claude_md_within_line_cap():
    _assert_line_cap(
        CLAUDE_MD.read_text(encoding="utf-8"),
        source="CLAUDE.md",
        cap=CLAUDE_MD_LINE_CAP,
    )


def test_line_cap_detects_overflow():
    overflow = "\n".join(f"line {i}" for i in range(CLAUDE_MD_LINE_CAP + 1))
    assert _line_count(overflow) == CLAUDE_MD_LINE_CAP + 1
    with pytest.raises(AssertionError, match="キャップを超過"):
        _assert_line_cap(overflow, source="fixture", cap=CLAUDE_MD_LINE_CAP)
