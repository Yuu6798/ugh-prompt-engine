"""cost_caps.py のテスト（設計正本 §14, §18 Gate 1）。"""

from __future__ import annotations

import pytest

from voice_genesis.calibration.cost_caps import CapCounters, CostCaps, check, cost_caps_from_mapping


def test_cost_caps_field_names_match_validator_required_keys() -> None:
    from voice_genesis.calibration import c0_validate

    caps = CostCaps(compute=1.0, storage=1, budget=1.0)
    assert set(caps.as_dict()) == set(c0_validate.COST_CAPS_REQUIRED_KEYS)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"compute": 0.0, "storage": 1, "budget": 1.0},
        {"compute": 1.0, "storage": 0, "budget": 1.0},
        {"compute": 1.0, "storage": 1, "budget": 0.0},
        {"compute": -1.0, "storage": 1, "budget": 1.0},
        {"compute": float("nan"), "storage": 1, "budget": 1.0},
        {"compute": float("inf"), "storage": 1, "budget": 1.0},
    ],
)
def test_cost_caps_rejects_non_positive_or_nonfinite(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CostCaps(**kwargs)  # type: ignore[arg-type]


def test_cost_caps_from_mapping() -> None:
    caps = cost_caps_from_mapping({"compute": 10.0, "storage": 100, "budget": 5.0})
    assert caps == CostCaps(compute=10.0, storage=100, budget=5.0)


def test_cost_caps_from_mapping_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        cost_caps_from_mapping({"compute": 10.0, "storage": 100})


def test_check_no_exceedance_returns_none() -> None:
    caps = CostCaps(compute=100.0, storage=1000, budget=10.0)
    counters = CapCounters(compute_used=50.0, storage_used=500, budget_used=5.0)
    assert check(counters, caps) is None


def test_check_exact_cap_is_not_exceedance() -> None:
    caps = CostCaps(compute=100.0, storage=1000, budget=10.0)
    counters = CapCounters(compute_used=100.0, storage_used=1000, budget_used=10.0)
    assert check(counters, caps) is None


def test_check_compute_exceedance() -> None:
    caps = CostCaps(compute=100.0, storage=1000, budget=10.0)
    counters = CapCounters(compute_used=100.1, storage_used=500, budget_used=5.0)
    decision = check(counters, caps)
    assert decision is not None
    assert decision.exceeded_dims == ("compute",)
    assert decision.event_payload["reason"] == "COST_CAP_EXCEEDED"
    assert decision.event_payload["kind"] == "stop_event"


def test_check_multiple_dims_exceeded() -> None:
    caps = CostCaps(compute=100.0, storage=1000, budget=10.0)
    counters = CapCounters(compute_used=200.0, storage_used=2000, budget_used=20.0)
    decision = check(counters, caps)
    assert decision is not None
    assert decision.exceeded_dims == ("compute", "storage", "budget")


def test_cap_counters_add_accumulates() -> None:
    counters = CapCounters()
    counters.add(compute=1.0, storage=10, budget=0.5)
    counters.add(compute=2.0, storage=20, budget=0.5)
    assert counters.compute_used == 3.0
    assert counters.storage_used == 30
    assert counters.budget_used == 1.0
