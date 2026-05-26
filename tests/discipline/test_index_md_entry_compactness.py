"""_index.md の各エントリが肥大化していないことを検証する。

エントリが長文化すると _index.md が巨大化し、セッション起動時の
コンテキスト予算を圧迫する。1 エントリ 500 文字以内に収める。
"""

from __future__ import annotations

from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parents[2] / ".claude" / "memory" / "_index.md"
MAX_ENTRY_CHARS = 500


def _extract_entries(text: str) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("- 20"):
            parts = [stripped]
            for continuation in lines[i:]:
                if continuation and not continuation[0].isspace() and not continuation.startswith("  "):
                    break
                if not continuation.strip():
                    break
                parts.append(continuation.strip())
            entries.append((i, " ".join(parts)))
    return entries


def test_index_exists():
    assert INDEX_PATH.exists(), f"{INDEX_PATH} が存在しない"


def test_all_entries_are_compact():
    text = INDEX_PATH.read_text(encoding="utf-8")
    entries = _extract_entries(text)
    violations = [
        (lineno, len(entry), entry[:80] + "...")
        for lineno, entry in entries
        if len(entry) > MAX_ENTRY_CHARS
    ]
    assert not violations, (
        f"_index.md に {MAX_ENTRY_CHARS} 文字を超えるエントリがある:\n"
        + "\n".join(f"  L{ln}: {length}文字 — {preview}" for ln, length, preview in violations)
    )
