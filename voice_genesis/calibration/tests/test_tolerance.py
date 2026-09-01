from __future__ import annotations

import math

import pytest

from voice_genesis.calibration.tolerance import (
    TOLERANCE_FLOOR_LIMITED,
    derive_floor,
    pooled_dispersion,
    tolerance,
    tolerance_floor_limited,
    unstable_cell_flags,
)


def test_tolerance_floor_dominates_when_sd_tiny() -> None:
    assert tolerance(pooled_sd=1e-9, k=3.0, floor=0.05) == pytest.approx(0.05)


def test_tolerance_k_sd_dominates_when_sd_large() -> None:
    assert tolerance(pooled_sd=1.0, k=3.0, floor=0.05) == pytest.approx(3.0)


def test_tolerance_boundary_equal_values() -> None:
    # k*sd == floor -> max は floor 側 (同値なのでどちらでも同じ)
    assert tolerance(pooled_sd=1.0, k=0.05, floor=0.05) == pytest.approx(0.05)


def test_pooled_dispersion_hand_computed_oracle() -> None:
    # 手計算: cell A = [1,2,3] (mean=2, var(ddof=1)=((1-2)^2+(2-2)^2+(3-2)^2)/2=1.0)
    # cell B = [10,12] (mean=11, var(ddof=1)=((10-11)^2+(12-11)^2)/1=2.0)
    # pooled var = ((3-1)*1.0 + (2-1)*2.0) / ((3-1)+(2-1)) = (2.0+2.0)/3 = 4/3
    # pooled sd = sqrt(4/3) = 1.1547005383792515
    values_by_cell = {"cellA": [1.0, 2.0, 3.0], "cellB": [10.0, 12.0]}
    result = pooled_dispersion(values_by_cell, pool_key=lambda _: "pool1")
    assert result["pool1"] == pytest.approx(math.sqrt(4 / 3), rel=1e-12)


def test_pooled_dispersion_separates_pools() -> None:
    values_by_cell = {
        "a1": [1.0, 2.0, 3.0],
        "a2": [10.0, 12.0],
        "b1": [100.0, 100.0, 100.0],
    }

    def pool_key(cell: str) -> str:
        return cell[0]

    result = pooled_dispersion(values_by_cell, pool_key)
    assert result["b"] == pytest.approx(0.0)
    assert result["a"] == pytest.approx(math.sqrt(4 / 3), rel=1e-12)


def test_pooled_dispersion_singleton_cells_contribute_nothing() -> None:
    values_by_cell = {"only_one": [5.0]}
    result = pooled_dispersion(values_by_cell, pool_key=lambda _: "p")
    assert result["p"] == 0.0


def test_derive_floor_returns_value_and_formula_string() -> None:
    value, formula = derive_floor(
        pcm_quantization_step=2.0 / 65536,  # 16-bit PCM full scale 2.0
        float_eps_bound=1e-7,
        meter_declared_resolution=0.01,
    )
    assert value == pytest.approx(0.01)  # meter_declared_resolution dominates
    assert isinstance(formula, str)
    assert "floor = max(" in formula


def test_derive_floor_pcm_step_dominates() -> None:
    value, formula = derive_floor(
        pcm_quantization_step=1.0,  # huge, dominates
        float_eps_bound=1e-7,
        meter_declared_resolution=0.001,
    )
    assert value == pytest.approx(0.5)
    assert "pcm_quantization_half_step" in formula


def test_derive_floor_handles_none_resolution() -> None:
    value, _formula = derive_floor(
        pcm_quantization_step=0.0, float_eps_bound=1e-9, meter_declared_resolution=None
    )
    assert value == pytest.approx(1e-9)


def test_unstable_cell_flags_flags_high_dispersion_cell() -> None:
    per_cell_values = {
        "stable": [1.0, 1.01, 0.99, 1.0, 1.0],
        "unstable": [1.0, 5.0, -3.0, 8.0, 0.5],
    }
    pooled_sd = 0.02
    flagged = unstable_cell_flags(per_cell_values, pooled_sd, threshold_factor=5.0)
    assert "unstable" in flagged
    assert "stable" not in flagged


def test_unstable_cell_flags_ignores_singleton_cells() -> None:
    per_cell_values = {"only_one": [999.0]}
    flagged = unstable_cell_flags(per_cell_values, pooled_sd=0.01, threshold_factor=1.0)
    assert flagged == set()


def test_tolerance_floor_limited_true_when_all_zero() -> None:
    assert tolerance_floor_limited(0.0, [0.0, 0.0, 0.0]) is True


def test_tolerance_floor_limited_false_when_pooled_nonzero() -> None:
    assert tolerance_floor_limited(0.5, [0.0, 0.0]) is False


def test_tolerance_floor_limited_false_when_a_cell_nonzero() -> None:
    assert tolerance_floor_limited(0.0, [0.0, 0.3]) is False


def test_tolerance_floor_limited_marker_constant() -> None:
    assert TOLERANCE_FLOOR_LIMITED == "TOLERANCE_FLOOR_LIMITED"
