"""STATUS.md の Next-Issue Queue に完了済みアイテムが残っていないことを検証する。

マージ済みの項目が queue に残ると、次のセッションで誤った優先順位判断を招く。
"""

from __future__ import annotations

import re
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parents[2] / ".claude" / "memory" / "STATUS.md"

COMPLETED_MARKERS = re.compile(
    r"(merged|完了|done|closed|shipped)", re.IGNORECASE
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


def test_next_issue_queue_section_exists():
    assert STATUS_PATH.exists(), f"{STATUS_PATH} が存在しない"
    text = STATUS_PATH.read_text(encoding="utf-8")
    assert _extract_next_issue_section(text) is not None, (
        "STATUS.md に ## Next-Issue Queue セクションが見つからない"
    )


def test_no_completed_items_in_queue():
    assert STATUS_PATH.exists(), f"{STATUS_PATH} が存在しない"
    text = STATUS_PATH.read_text(encoding="utf-8")
    body = _extract_next_issue_section(text)
    assert body is not None, "STATUS.md に ## Next-Issue Queue セクションが見つからない"
    rows = _extract_rows(body)
    violations = [row for row in rows if COMPLETED_MARKERS.search(row)]
    assert not violations, (
        "Next-Issue Queue に完了済みアイテムが残っている:\n"
        + "\n".join(violations)
    )
