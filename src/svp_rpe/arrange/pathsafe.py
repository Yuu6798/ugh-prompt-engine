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

`validate_relative_locator`'s lexical check treats **both** `/` and `\\` as
segment separators (PR #184 review round 2, Thread A): a locator is checked
on whatever platform runs this code, and a bare `PurePosixPath` split never
sees `\\` as a separator, so `..\\outside.md` would otherwise pass through
as a single opaque path component instead of being recognized as upward
traversal. The normalization mirrors `package.py`'s
`ChannelArtifactReference._validate_artifact_path` precedent
(`value.replace("\\", "/")` before splitting). One consequence: on POSIX,
`\\` is legal inside a filename, so a locator containing a literal backslash
that isn't meant as a separator is conservatively treated as one and may be
rejected — the safe-by-default direction for a confinement check.
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


def is_absolute_path(value: str) -> bool:
    """Cross-platform absolute-path check (posix root or Windows drive/root).

    Public on its own (not just as an internal step of
    ``validate_relative_locator``/``resolve_confined``) for callers whose
    locator convention rejects absolute paths but intentionally allows a
    relative path's ``..`` segments to reach outside a base directory —
    e.g. ``roundtrip/identity_rank.py``'s WI2 references, where a
    reference's ``score_path``/``progression_path`` legitimately points at
    a sibling or ancestor directory (a shared ``derived/`` tree, an
    ``identity/`` anchor two levels up) rather than staying confined below
    the references file the way an ``IdentityManifest`` anchor must.
    ``validate_relative_locator``'s stricter net-upward-traversal check
    would reject those same paths, so such a caller uses only this
    narrower absolute-path check, not the full lexical/physical
    confinement below.
    """
    windows_path = PureWindowsPath(value)
    return bool(PurePosixPath(value).is_absolute() or windows_path.drive or windows_path.root)


def validate_relative_locator(value: str) -> None:
    """Base-independent lexical confinement check.

    Does not touch the filesystem, so it can run even when no confinement
    base is known yet (e.g. `evidence_base=None` in `capabilities.py`).
    Rejects absolute paths and any locator whose `..` segments net-traverse
    above its own root; `a/../b` cancels internally and is allowed. Both `/`
    and `\\` are treated as segment separators (see module docstring) —
    `..\\outside.md` and `a/..\\..\\b` are traversal exactly like their `/`
    equivalents.
    """
    if is_absolute_path(value):
        raise PathConfinementError(
            f"path must be relative: {value!r}", value=value, reason="absolute"
        )
    normalized = value.replace("\\", "/")
    depth = 0
    for part in PurePosixPath(normalized).parts:
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
    `validate_relative_locator` cannot see. No `\\`-normalization is needed
    here (unlike `validate_relative_locator`): this is real filesystem
    resolution, and `\\` is not a path separator to any filesystem this
    process runs on, so a `\\`-containing locator either resolves as one
    literal (non-existent, on POSIX) path component or is rejected by
    `is_relative_to` — it can never resolve *outside* `base_dir` by way of a
    literal backslash the way a lexical `..` split could be fooled.
    """
    if is_absolute_path(value):
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
