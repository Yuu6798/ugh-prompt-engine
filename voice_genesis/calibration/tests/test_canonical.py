from __future__ import annotations

import json
import math

import pytest

from voice_genesis.calibration.canonical import (
    CANONICAL_FORMAT,
    canonical_json,
    manifest_sha,
    row_id,
)


def test_canonical_format_constant() -> None:
    assert CANONICAL_FORMAT == "vgcal-canon/1"


def test_key_ordering_is_codepoint_sorted() -> None:
    obj = {"b": 1, "a": 2, "Z": 3, "z": 4}
    out = canonical_json(obj)
    # codepoint 順: 'Z' (90) < 'a' (97) < 'b' (98) < 'z' (122)
    assert out == '{"Z":3,"a":2,"b":1,"z":4}'


def test_nested_dict_keys_sorted_recursively() -> None:
    obj = {"outer": {"y": 1, "x": 2}}
    out = canonical_json(obj)
    assert out == '{"outer":{"x":2,"y":1}}'


def test_separators_are_minimal() -> None:
    obj = {"a": [1, 2], "b": {"c": 3}}
    out = canonical_json(obj)
    assert " " not in out
    assert out == '{"a":[1,2],"b":{"c":3}}'


def test_float_round_trip_stability() -> None:
    value = 0.1 + 0.2
    obj = {"x": value}
    out = canonical_json(obj)
    recovered = json.loads(out)["x"]
    assert recovered == value


def test_float_shortest_repr_used() -> None:
    obj = {"x": 1.5}
    out = canonical_json(obj)
    assert out == '{"x":1.5}'


def test_negative_zero_normalized_to_zero() -> None:
    obj = {"x": -0.0}
    out = canonical_json(obj)
    assert out == '{"x":0.0}'
    assert "-0.0" not in out


def test_negative_zero_normalized_inside_list() -> None:
    obj = {"xs": [-0.0, 1.0, -2.5]}
    out = canonical_json(obj)
    assert out == '{"xs":[0.0,1.0,-2.5]}'


def test_nan_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.nan})


def test_positive_infinity_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": math.inf})


def test_negative_infinity_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": -math.inf})


def test_unicode_preserved_unescaped() -> None:
    obj = {"name": "日本語テスト"}
    out = canonical_json(obj)
    assert "日本語テスト" in out
    assert "\\u" not in out


def test_unicode_round_trips() -> None:
    obj = {"name": "日本語テスト"}
    out = canonical_json(obj)
    assert json.loads(out) == obj


def test_unsupported_type_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": {1, 2, 3}})  # set is not JSON-compatible


def test_tuple_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": (1, 2)})


def test_non_string_key_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({1: "a"})


def test_row_id_deterministic_regardless_of_input_key_order() -> None:
    row_a = {"a": 1, "b": 2}
    row_b = {"b": 2, "a": 1}
    assert row_id(row_a) == row_id(row_b)


def test_row_id_is_sha256_hex() -> None:
    out = row_id({"a": 1})
    assert len(out) == 64
    int(out, 16)  # must parse as hex


def test_row_id_differs_for_different_content() -> None:
    assert row_id({"a": 1}) != row_id({"a": 2})


def test_manifest_sha_matches_row_id_mechanism() -> None:
    obj = {"a": 1, "b": [1, 2, 3]}
    assert manifest_sha(obj) == row_id(obj)
