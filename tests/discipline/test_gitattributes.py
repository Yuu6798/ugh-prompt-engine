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
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    attributes = subprocess.run(
        ["git", "check-attr", "--stdin", "-z", "text", "eol"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        input=tracked,
    ).stdout.decode("utf-8").split("\0")
    by_path: dict[str, dict[str, str]] = {}
    for index in range(0, len(attributes) - 2, 3):
        relative, attribute, value = attributes[index : index + 3]
        by_path.setdefault(relative, {})[attribute] = value

    stale: list[str] = []
    for relative, attrs in by_path.items():
        if attrs != {"text": "auto", "eol": "lf"}:
            continue
        content = (ROOT / relative).read_bytes()
        # Git's text=auto binary heuristic treats a NUL in the first 8 KiB as
        # binary. Do the same so CRLF-shaped bytes inside WAV/etc. are ignored.
        if b"\0" not in content[:8000] and b"\r\n" in content:
            stale.append(relative)

    assert not stale, (
        "tracked text files are still checked out as CRLF, so content-addressed "
        "sha256 pins may fail. This worktree predates the LF policy; create a fresh "
        "clone or a fresh worktree after pulling this commit. Examples:\n"
        + "\n".join(stale[:10])
    )
