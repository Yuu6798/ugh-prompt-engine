"""Repository text checkout policy for content-addressed fixtures."""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
CONTENT_ADDRESSED_PREFIXES = ("config/", "examples/", "src/svp_rpe/config/")


def test_text_checkouts_are_normalized_to_lf() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    rules = {line.strip() for line in attributes if line.strip() and not line.startswith("#")}

    assert "* text=auto eol=lf" in rules


def test_worktree_has_no_stale_crlf_text_checkouts() -> None:
    """Detect old Windows worktrees that predate the repository LF policy."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    tracked = result.stdout.decode("utf-8").split("\0")
    stale = [
        relative
        for relative in tracked
        if relative
        and relative.startswith(CONTENT_ADDRESSED_PREFIXES)
        and Path(relative).suffix.lower() in TEXT_SUFFIXES
        and b"\r\n" in (ROOT / relative).read_bytes()
    ]

    assert not stale, (
        "tracked text files are still checked out as CRLF, so content-addressed "
        "sha256 pins may fail. This worktree predates the LF policy; create a fresh "
        "clone or a fresh worktree after pulling this commit. Examples:\n"
        + "\n".join(stale[:10])
    )
