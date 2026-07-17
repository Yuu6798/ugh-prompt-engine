"""Path confinement primitives shared by `identity.py` / `capabilities.py` loaders.

`validate_relative_locator` is the **base-independent** lexical check (no
filesystem access): reject absolute paths, and reject a locator whose literal
`..` segments net-traverse above its own root (`a/../b` internally cancels
and is allowed; `../a` does not). `resolve_confined` is the
**base-dependent** physical check: reject absolute paths, then `resolve()`
the locator against `base_dir` and reject anything that lands outside it —
this additionally catches symlink escapes that a lexical check cannot see.

Both raise `PathConfinementError` carrying a `reason` (`"absolute"` |
`"traversal"` | `"escape"`) plus whatever detail (`base`, `resolved`) the
caller needs to reconstruct its own domain-specific message. This module
deliberately knows nothing about identity manifests or capability
profiles — callers catch `PathConfinementError` and re-raise their own
error type with their own message wording (AR-provenance PR: item 15,
migrated from `identity.py:_resolve_confined` and
`capabilities.py:_validate_evidence_form` / `_resolve_confined_evidence`,
which keep their existing exception types and message text verbatim).
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Optional

PathConfinementReason = Literal["absolute", "traversal", "escape"]


class PathConfinementError(ValueError):
    """A relative locator/path failed lexical or physical confinement.

    Attributes:
        value: the offending locator/path string, as given by the caller.
        reason: ``"absolute"`` (the value is an absolute path — posix or
            Windows drive/root), ``"traversal"`` (lexical net-upward `..`
            traversal, `validate_relative_locator` only), or ``"escape"``
            (resolved outside the confinement base, e.g. via a symlink,
            `resolve_confined` only).
        base: the resolved confinement base directory, when known
            (``"escape"`` only).
        resolved: the fully resolved path that escaped ``base``, when known
            (``"escape"`` only).
    """

    def __init__(
        self,
        message: str,
        *,
        value: str,
        reason: PathConfinementReason,
        base: Optional[Path] = None,
        resolved: Optional[Path] = None,
    ) -> None:
        super().__init__(message)
        self.value = value
        self.reason = reason
        self.base = base
        self.resolved = resolved


def _is_absolute(value: str) -> bool:
    """Cross-platform absolute-path check (posix root or Windows drive/root)."""
    windows_path = PureWindowsPath(value)
    return bool(PurePosixPath(value).is_absolute() or windows_path.drive or windows_path.root)


def validate_relative_locator(value: str) -> None:
    """Base-independent lexical confinement check.

    Does not touch the filesystem, so it can run even when no confinement
    base is known yet (e.g. `evidence_base=None` in `capabilities.py`).
    Rejects absolute paths and any locator whose `..` segments net-traverse
    above its own root; `a/../b` cancels internally and is allowed.
    """
    if _is_absolute(value):
        raise PathConfinementError(
            f"path must be relative: {value!r}", value=value, reason="absolute"
        )
    depth = 0
    for part in PurePosixPath(value).parts:
        if part == "..":
            depth -= 1
        elif part != ".":
            depth += 1
        if depth < 0:
            raise PathConfinementError(
                f"path must not traverse above its root: {value!r}",
                value=value,
                reason="traversal",
            )


def resolve_confined(value: str, base_dir: Path) -> Path:
    """Base-dependent physical confinement check; returns the resolved path.

    Rejects absolute paths, then resolves `value` against `base_dir` and
    rejects anything landing outside it. `resolve()` follows both `../` and
    symlinks, so this additionally catches symlink escapes that
    `validate_relative_locator` cannot see.
    """
    if _is_absolute(value):
        raise PathConfinementError(
            f"path must be relative: {value!r}", value=value, reason="absolute"
        )
    base = base_dir.resolve()
    resolved = (base / value).resolve()
    if not resolved.is_relative_to(base):
        raise PathConfinementError(
            f"path escapes the confinement base {base}: resolved to {resolved}",
            value=value,
            reason="escape",
            base=base,
            resolved=resolved,
        )
    return resolved
