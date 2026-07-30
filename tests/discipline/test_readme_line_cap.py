"""README.md が行数キャップ以内に収まっていることを検証する。

README.md は入口情報に限定する運用ポリシー（CLAUDE.md § README 管理ポリシー）の
「hard limit: 350 行」を enforced ceiling として固定する。単一 section が
肥大化したら `docs/<topic>.md` へ抽出し、README にはリンク + 要約のみ残す
（詳細は `docs/README.md` の索引と各 docs/*.md）。
"""

from __future__ import annotations

import pytest

from ._helpers import REPO_ROOT

README_MD = REPO_ROOT / "README.md"
README_MD_LINE_CAP = 350


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _assert_line_cap(text: str, *, source: str, cap: int) -> None:
    count = _line_count(text)
    assert count <= cap, (
        f"{source} が {count} 行で {cap} 行キャップを超過。\n"
        "README.md は入口情報のみ: 30 行超の section は docs/<topic>.md へ抽出し、"
        "README にはリンク + 2-3 行の要約だけ残すこと。"
    )


def test_readme_within_line_cap():
    _assert_line_cap(
        README_MD.read_text(encoding="utf-8"),
        source="README.md",
        cap=README_MD_LINE_CAP,
    )


def test_line_cap_detects_overflow():
    overflow = "\n".join(f"line {i}" for i in range(README_MD_LINE_CAP + 1))
    assert _line_count(overflow) == README_MD_LINE_CAP + 1
    with pytest.raises(AssertionError, match="キャップを超過"):
        _assert_line_cap(overflow, source="fixture", cap=README_MD_LINE_CAP)
