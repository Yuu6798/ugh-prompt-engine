"""`arrange.pathsafe` の共通 path 検証 utility の単体テスト（item 15）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from svp_rpe.arrange.pathsafe import (
    PathConfinementError,
    resolve_confined,
    validate_relative_locator,
)


# --- validate_relative_locator (base 非依存・lexical) ----------------------------


def test_validate_relative_locator_accepts_plain_relative_path() -> None:
    validate_relative_locator("docs/proof.md")


def test_validate_relative_locator_rejects_posix_absolute_path() -> None:
    with pytest.raises(PathConfinementError) as exc_info:
        validate_relative_locator("/etc/passwd")
    assert exc_info.value.reason == "absolute"
    assert exc_info.value.value == "/etc/passwd"


def test_validate_relative_locator_rejects_windows_absolute_path() -> None:
    with pytest.raises(PathConfinementError) as exc_info:
        validate_relative_locator("C:\\Windows\\System32")
    assert exc_info.value.reason == "absolute"


def test_validate_relative_locator_rejects_windows_rooted_no_drive_path() -> None:
    with pytest.raises(PathConfinementError) as exc_info:
        validate_relative_locator("\\Windows\\System32")
    assert exc_info.value.reason == "absolute"


def test_validate_relative_locator_rejects_upward_traversal() -> None:
    with pytest.raises(PathConfinementError) as exc_info:
        validate_relative_locator("../outside.md")
    assert exc_info.value.reason == "traversal"


def test_validate_relative_locator_rejects_traversal_buried_in_nested_path() -> None:
    with pytest.raises(PathConfinementError) as exc_info:
        validate_relative_locator("a/../../outside.md")
    assert exc_info.value.reason == "traversal"


def test_validate_relative_locator_allows_internally_cancelling_dotdot() -> None:
    """`docs/../docs/x.md` は正味の遡上ではないため許容する。"""
    validate_relative_locator("docs/../docs/x.md")


def test_validate_relative_locator_allows_current_dir_segments() -> None:
    validate_relative_locator("./docs/./x.md")


# --- resolve_confined (base 依存・物理解決) --------------------------------------


def test_resolve_confined_returns_resolved_path_for_plain_relative(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "proof.md").write_text("proof", encoding="utf-8")

    resolved = resolve_confined("docs/proof.md", tmp_path)

    assert resolved == (tmp_path / "docs" / "proof.md").resolve()


def test_resolve_confined_rejects_posix_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(PathConfinementError) as exc_info:
        resolve_confined("/etc/passwd", tmp_path)
    assert exc_info.value.reason == "absolute"


def test_resolve_confined_rejects_windows_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(PathConfinementError) as exc_info:
        resolve_confined("C:\\Windows\\System32", tmp_path)
    assert exc_info.value.reason == "absolute"


def test_resolve_confined_rejects_parent_escape_even_if_target_exists(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    (tmp_path / "outside.md").write_text("outside", encoding="utf-8")

    with pytest.raises(PathConfinementError) as exc_info:
        resolve_confined("../outside.md", base_dir)
    assert exc_info.value.reason == "escape"
    assert exc_info.value.base == base_dir.resolve()
    assert exc_info.value.resolved == (tmp_path / "outside.md").resolve()


def test_resolve_confined_allows_internally_cancelling_dotdot(tmp_path: Path) -> None:
    """lexical には現れる `..` も、resolve 後 base 配下に収まれば許容する。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("x", encoding="utf-8")

    resolved = resolve_confined("docs/../docs/x.md", tmp_path)

    assert resolved == (tmp_path / "docs" / "x.md").resolve()


def test_resolve_confined_rejects_symlink_escape(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    base_dir.mkdir()
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("outside", encoding="utf-8")
    (base_dir / "sneaky_link.md").symlink_to(outside_file)

    with pytest.raises(PathConfinementError) as exc_info:
        resolve_confined("sneaky_link.md", base_dir)
    assert exc_info.value.reason == "escape"


def test_resolve_confined_does_not_require_target_to_exist(tmp_path: Path) -> None:
    """`resolve_confined` はパス演算のみで実在確認は呼び出し側の責務。"""
    resolved = resolve_confined("nested/does-not-exist.md", tmp_path)

    assert resolved == (tmp_path / "nested" / "does-not-exist.md").resolve()
