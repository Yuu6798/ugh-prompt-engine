"""STATUS.md の Phase セクションが単一段落であることを検証する。

複数段落が残ると過去の状況が残存し、現在の Phase が不明確になる。
"""

from __future__ import annotations

import re
from pathlib import Path

STATUS_PATH = Path(__file__).resolve().parents[2] / ".claude" / "memory" / "STATUS.md"


def _extract_phase_body(text: str) -> str:
    match = re.search(r"^## Phase\s*\n(.*?)(?=\n## |\Z)", text, re.DOTALL | re.MULTILINE)
    assert match, "STATUS.md に ## Phase セクションが見つからない"
    return match.group(1).strip()


def test_phase_section_exists():
    assert STATUS_PATH.exists(), f"{STATUS_PATH} が存在しない"
    text = STATUS_PATH.read_text(encoding="utf-8")
    body = _extract_phase_body(text)
    assert body, "## Phase セクションが空"


def test_phase_is_single_paragraph():
    text = STATUS_PATH.read_text(encoding="utf-8")
    body = _extract_phase_body(text)
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    assert len(paragraphs) == 1, (
        f"## Phase は 1 段落のみ許可。現在 {len(paragraphs)} 段落:\n"
        + "\n---\n".join(paragraphs)
    )
