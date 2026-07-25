"""Repository text checkout policy for content-addressed fixtures."""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_text_checkouts_are_normalized_to_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    rules = {line.strip() for line in attributes if line.strip() and not line.startswith("#")}

    assert "* text=auto eol=lf" in rules


def test_worktree_has_no_stale_crlf_text_checkouts() -> None:
    """Detect old Windows worktrees that predate the repository LF policy."""
    result = subprocess.run(
        ["git", "ls-files", "--eol"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    stale = [
        line
        for line in result.stdout.splitlines()
        if "w/crlf" in line and "attr/text=auto eol=lf" in line
    ]

    assert not stale, (
        "tracked text files are still checked out as CRLF, so content-addressed "
        "sha256 pins may fail. This worktree predates the LF policy; create a fresh "
        "clone or a fresh worktree after pulling this commit. Examples:\n"
        + "\n".join(stale[:10])
    )
