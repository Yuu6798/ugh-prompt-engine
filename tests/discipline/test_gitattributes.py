"""Repository text checkout policy for content-addressed fixtures."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_text_checkouts_are_normalized_to_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    rules = {line.strip() for line in attributes if line.strip() and not line.startswith("#")}

    assert "* text=auto eol=lf" in rules
