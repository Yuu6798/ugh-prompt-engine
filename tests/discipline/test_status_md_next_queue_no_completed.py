"""STATUS.md の Next-Issue Queue に完了済みアイテムが残っていないことを検証する。

マージ済みの項目が queue に残ると、次のセッションで誤った優先順位判断を招く。

判定は **Title 列のみ**を対象にする（行全体ではない）。Notes 列には
「〜は closeout 済」のような部分完了の説明文（一部のサブタスクは片付いたが
アイテム全体としては残っている、という正当な記述）が現れるため、行全体を
対象にすると誤検出になる。Title 自体が完了マーカーを含む場合のみ違反とする。
"""

from __future__ import annotations

import re

import pytest

from ._helpers import FIXTURES, REPO_ROOT

STATUS_PATH = REPO_ROOT / ".claude" / "memory" / "STATUS.md"

COMPLETED_MARKERS = re.compile(
    r"(merged|完了|done|closed|shipped|済)", re.IGNORECASE
)


def _extract_next_issue_section(text: str) -> str | None:
    match = re.search(
        r"^## Next-Issue Queue\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL | re.MULTILINE
    )
    if not match:
        return None
    return match.group(1)


def _extract_rows(body: str) -> list[str]:
    rows = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("|") and not line.startswith("| ID") and not line.startswith("|---"):
            rows.append(line)
    return rows


def _row_title(row: str) -> str:
    """`| ID | Title | Priority | Notes |` 形式の行から Title 列だけを取り出す。

    列数が想定と異なる壊れた行は、判定対象を空文字にして誤検出を避ける
    （行全体ではなく Title のみを完了マーカー判定にかけるのがこの関数の目的）。
    """
    fields = row.split("|")
    if len(fields) < 3:
        return ""
    return fields[2].strip()


def _assert_no_completed_items(text: str, *, source: str) -> None:
    body = _extract_next_issue_section(text)
    assert body is not None, (
        f"{source} に ## Next-Issue Queue セクションが見つからない"
    )
    rows = _extract_rows(body)
    violations = [row for row in rows if COMPLETED_MARKERS.search(_row_title(row))]
    assert not violations, (
        f"{source} の Next-Issue Queue に完了済みアイテムが残っている（Title 列に完了マーカー）:\n"
        + "\n".join(violations)
        + "\nRecently Merged に移動して queue から削除すること。"
    )


def test_next_issue_queue_section_exists():
    assert STATUS_PATH.exists(), f"{STATUS_PATH} が存在しない"
    text = STATUS_PATH.read_text(encoding="utf-8")
    assert _extract_next_issue_section(text) is not None, (
        "STATUS.md に ## Next-Issue Queue セクションが見つからない"
    )


def test_no_completed_items_in_queue():
    _assert_no_completed_items(
        STATUS_PATH.read_text(encoding="utf-8"),
        source=".claude/memory/STATUS.md",
    )


def test_parser_detects_completed_item_fixture():
    text = (FIXTURES / "status_md_completed_in_queue.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="完了済みアイテムが残っている"):
        _assert_no_completed_items(text, source="fixture")


def test_parser_rejects_missing_section_fixture():
    text = (FIXTURES / "status_md_missing_next_queue.md").read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="セクションが見つからない"):
        _assert_no_completed_items(text, source="fixture")
