"""tests/test_utils_version_probe.py — `rpe/learned/version_probe` の単体テスト。

7 アダプタで重複していた「インストール済みバージョン検出」フォールバック
チェーン（`sys.modules[<pkg>].__version__` → `importlib.metadata.version` →
`None`）を一本化した `detect_installed_version` を直接検証する。
"""
from __future__ import annotations

import sys
import types

import pytest

from svp_rpe.rpe.learned.version_probe import _pkg_metadata, detect_installed_version


def test_detect_installed_version_from_module_dunder(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sys.modules[module_name].__version__` が非空文字列なら最優先で採用する。"""
    fake = types.ModuleType("fake_probe_pkg")
    fake.__version__ = "1.2.3"
    monkeypatch.setitem(sys.modules, "fake_probe_pkg", fake)

    assert detect_installed_version("fake_probe_pkg") == "1.2.3"


def test_detect_installed_version_falls_back_to_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """import 済みモジュールに `__version__` が無ければ importlib.metadata へフォールバックする。"""
    fake = types.ModuleType("fake_probe_pkg_no_dunder")
    monkeypatch.setitem(sys.modules, "fake_probe_pkg_no_dunder", fake)

    def fake_version(name: str) -> str:
        assert name == "fake-probe-dist"
        return "9.9.9-from-metadata"

    monkeypatch.setattr(_pkg_metadata, "version", fake_version)

    result = detect_installed_version("fake_probe_pkg_no_dunder", dist_name="fake-probe-dist")
    assert result == "9.9.9-from-metadata"


def test_detect_installed_version_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """モジュールが import されておらず配布メタデータも見つからなければ None。"""
    monkeypatch.delitem(sys.modules, "fake_probe_pkg_missing", raising=False)

    def _not_found(name: str) -> str:
        raise _pkg_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(_pkg_metadata, "version", _not_found)

    assert detect_installed_version("fake_probe_pkg_missing") is None
