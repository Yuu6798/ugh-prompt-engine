"""tests/test_clamp.py — `svp_rpe.utils.clamp.clamp` の直接単体テスト。"""
from __future__ import annotations

from svp_rpe.utils.clamp import clamp


def test_below_lo_clamps_to_lo() -> None:
    assert clamp(-0.5) == 0.0


def test_above_hi_clamps_to_hi() -> None:
    assert clamp(1.5) == 1.0


def test_inside_range_is_unchanged() -> None:
    assert clamp(0.42) == 0.42


def test_boundary_lo_is_unchanged() -> None:
    assert clamp(0.0) == 0.0


def test_boundary_hi_is_unchanged() -> None:
    assert clamp(1.0) == 1.0


def test_default_bounds_are_zero_and_one() -> None:
    assert clamp(-10.0) == 0.0
    assert clamp(10.0) == 1.0


def test_custom_bounds_below_lo_clamps_to_lo() -> None:
    assert clamp(-5.0, lo=-1.0, hi=1.0) == -1.0


def test_custom_bounds_above_hi_clamps_to_hi() -> None:
    assert clamp(5.0, lo=-1.0, hi=1.0) == 1.0


def test_custom_bounds_inside_range_is_unchanged() -> None:
    assert clamp(0.5, lo=-1.0, hi=1.0) == 0.5


def test_custom_bounds_boundary_equality() -> None:
    assert clamp(-1.0, lo=-1.0, hi=1.0) == -1.0
    assert clamp(1.0, lo=-1.0, hi=1.0) == 1.0
