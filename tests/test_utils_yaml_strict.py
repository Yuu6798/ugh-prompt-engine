"""tests/test_utils_yaml_strict.py — `utils/yaml_strict` の単体テスト。

`melody/representation.py` と 6 本の `scripts/*.py` で独立複製されていた
「重複 mapping キーを拒否する SafeLoader」構築ロジックのうち、
`melody/representation.py` 側だけを一本化した `make_no_dup_safe_loader` /
`safe_load_no_duplicate_keys` を直接検証する（`scripts/*.py` 側は generator
provenance closure に参加するため意図的に独立のまま残す）。
"""
from __future__ import annotations

import pytest
import yaml

from svp_rpe.utils.yaml_strict import make_no_dup_safe_loader, safe_load_no_duplicate_keys


def test_safe_load_no_duplicate_keys_rejects_duplicate_with_context() -> None:
    """重複キーは `context` を埋め込んだメッセージ付き ValueError で拒否される。"""
    text = "a: 1\na: 2\n"
    with pytest.raises(ValueError, match=r"duplicate YAML mapping key 'a' in my_fixture\.yaml"):
        safe_load_no_duplicate_keys(text, context="my_fixture.yaml")


def test_safe_load_no_duplicate_keys_loads_normal_mapping() -> None:
    """重複の無い通常の mapping は従来どおり読める（偽陽性を出さない）。"""
    text = "a: 1\nb: 2\nnested:\n  c: 3\n"
    result = safe_load_no_duplicate_keys(text, context="my_fixture.yaml")
    assert result == {"a": 1, "b": 2, "nested": {"c": 3}}


def test_make_no_dup_safe_loader_uses_custom_error_factory() -> None:
    """`make_error` コールバックの例外型・メッセージがそのまま使われる（サイト固有文言の再現）。"""

    class _CustomError(RuntimeError):
        pass

    def _make_error(key: object) -> _CustomError:
        return _CustomError(f"custom duplicate: {key!r}")

    loader_cls = make_no_dup_safe_loader(_make_error)
    with pytest.raises(_CustomError, match="custom duplicate: 'x'"):
        yaml.load("x: 1\nx: 2\n", Loader=loader_cls)  # noqa: S506 (dup-key 拒否付き SafeLoader)


def test_make_no_dup_safe_loader_nested_duplicate_rejected() -> None:
    """ネストした mapping 内の重複キーも拒否する。"""

    def _make_error(key: object) -> ValueError:
        return ValueError(f"dup: {key!r}")

    loader_cls = make_no_dup_safe_loader(_make_error)
    with pytest.raises(ValueError, match="dup: 'inner'"):
        yaml.load(
            "outer:\n  inner: 1\n  inner: 2\n",
            Loader=loader_cls,
        )  # noqa: S506 (dup-key 拒否付き SafeLoader)
