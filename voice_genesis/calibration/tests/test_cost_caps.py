"""cost_caps.py のテスト（設計正本 §14, §18 Gate 1）。"""

from __future__ import annotations

import pytest

from voice_genesis.calibration.cost_caps import (
    BUDGET_ACCOUNTING_LOCAL_ZERO_COST,
    BUDGET_ACCOUNTING_PER_UNIT_FIXED,
    BudgetAccountingUndeclaredError,
    CapCounters,
    CostCaps,
    check,
    cost_caps_from_mapping,
)


def _caps(**overrides: object) -> CostCaps:
    """`local_zero_cost` 既定の `CostCaps` を組み立てる test ヘルパー
    （round 13 finding #3 で `budget_accounting_mode` が必須フィールドに
    なったため、既存テストの大半はこのモードで固定して差分を最小化する）。"""
    kwargs: dict[str, object] = {
        "compute": 1.0,
        "storage": 1,
        "budget": 1.0,
        "budget_accounting_mode": BUDGET_ACCOUNTING_LOCAL_ZERO_COST,
    }
    kwargs.update(overrides)
    return CostCaps(**kwargs)  # type: ignore[arg-type]


def test_cost_caps_field_names_match_validator_required_keys() -> None:
    from voice_genesis.calibration import c0_validate

    caps = _caps(compute=1.0, storage=1, budget=1.0)
    # round 13 finding #3: CostCaps.as_dict() now carries 2 additional
    # budget-accounting keys beyond the frozen COST_CAPS_REQUIRED_KEYS
    # triple (`[UNDERSPEC-CAL-C17]`) — the validator's required-key set is
    # unchanged, but it must remain a *subset* of what CostCaps produces.
    assert set(c0_validate.COST_CAPS_REQUIRED_KEYS) <= set(caps.as_dict())
    assert set(caps.as_dict()) == {
        "compute",
        "storage",
        "budget",
        "budget_accounting_mode",
        "budget_unit_cost",
    }


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
        _caps(**kwargs)


def test_cost_caps_from_mapping() -> None:
    caps = cost_caps_from_mapping(
        {
            "compute": 10.0,
            "storage": 100,
            "budget": 5.0,
            "budget_accounting_mode": BUDGET_ACCOUNTING_LOCAL_ZERO_COST,
        }
    )
    assert caps == _caps(compute=10.0, storage=100, budget=5.0)


def test_cost_caps_from_mapping_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        cost_caps_from_mapping(
            {
                "compute": 10.0,
                "storage": 100,
                "budget_accounting_mode": BUDGET_ACCOUNTING_LOCAL_ZERO_COST,
            }
        )


def test_check_no_exceedance_returns_none() -> None:
    caps = _caps(compute=100.0, storage=1000, budget=10.0)
    counters = CapCounters(compute_used=50.0, storage_used=500, budget_used=5.0)
    assert check(counters, caps) is None


def test_check_exact_cap_is_not_exceedance() -> None:
    caps = _caps(compute=100.0, storage=1000, budget=10.0)
    counters = CapCounters(compute_used=100.0, storage_used=1000, budget_used=10.0)
    assert check(counters, caps) is None


def test_check_compute_exceedance() -> None:
    caps = _caps(compute=100.0, storage=1000, budget=10.0)
    counters = CapCounters(compute_used=100.1, storage_used=500, budget_used=5.0)
    decision = check(counters, caps)
    assert decision is not None
    assert decision.exceeded_dims == ("compute",)
    assert decision.event_payload["reason"] == "COST_CAP_EXCEEDED"
    assert decision.event_payload["kind"] == "stop_event"


def test_check_multiple_dims_exceeded() -> None:
    caps = _caps(compute=100.0, storage=1000, budget=10.0)
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


# ---------------------------------------------------------------------------
# round 13 finding #3 (`[UNDERSPEC-CAL-D27]`): budget_accounting_mode.
# ---------------------------------------------------------------------------


def test_local_zero_cost_charges_zero_per_work_unit() -> None:
    caps = _caps(budget_accounting_mode=BUDGET_ACCOUNTING_LOCAL_ZERO_COST)
    assert caps.budget_unit_cost is None
    assert caps.budget_charge_per_work_unit() == 0.0


def test_per_unit_fixed_charges_declared_unit_cost() -> None:
    caps = _caps(budget_accounting_mode=BUDGET_ACCOUNTING_PER_UNIT_FIXED, budget_unit_cost=0.02)
    assert caps.budget_charge_per_work_unit() == pytest.approx(0.02)


def test_per_unit_fixed_requires_positive_unit_cost() -> None:
    with pytest.raises(ValueError):
        _caps(budget_accounting_mode=BUDGET_ACCOUNTING_PER_UNIT_FIXED, budget_unit_cost=None)
    with pytest.raises(ValueError):
        _caps(budget_accounting_mode=BUDGET_ACCOUNTING_PER_UNIT_FIXED, budget_unit_cost=0.0)
    with pytest.raises(ValueError):
        _caps(budget_accounting_mode=BUDGET_ACCOUNTING_PER_UNIT_FIXED, budget_unit_cost=-1.0)


def test_local_zero_cost_rejects_a_stray_unit_cost() -> None:
    """`local_zero_cost` と `budget_unit_cost` の同時指定は曖昧な二重定義
    として拒否する（`local_zero_cost` は常に 0 charge であり、値があっても
    無視して黙って進めない）。"""
    with pytest.raises(ValueError):
        _caps(budget_accounting_mode=BUDGET_ACCOUNTING_LOCAL_ZERO_COST, budget_unit_cost=1.0)


def test_missing_budget_accounting_mode_raises_distinct_error() -> None:
    # `budget_accounting_mode` is a required dataclass field (no default),
    # so an omitted keyword is a plain TypeError; the distinct fail-closed
    # error is for a *present-but-empty/unknown* value, which is exactly
    # what cost_caps_from_mapping() constructs when the JSON key is absent
    # (see test_cost_caps_from_mapping_missing_mode_raises_distinct_error).
    with pytest.raises(BudgetAccountingUndeclaredError):
        CostCaps(compute=1.0, storage=1, budget=1.0, budget_accounting_mode="")  # type: ignore[arg-type]


def test_unknown_budget_accounting_mode_raises_distinct_error() -> None:
    with pytest.raises(BudgetAccountingUndeclaredError):
        _caps(budget_accounting_mode="per_second_metered")


def test_budget_accounting_undeclared_error_is_a_value_error() -> None:
    # approvals.py's gate1 payload parser catches (KeyError, TypeError,
    # ValueError) around cost_caps_from_mapping() — this must still catch
    # BudgetAccountingUndeclaredError so it surfaces as a normal approval
    # rejection reason rather than an uncaught exception.
    assert issubclass(BudgetAccountingUndeclaredError, ValueError)
    assert BudgetAccountingUndeclaredError.CODE == "BUDGET_ACCOUNTING_UNDECLARED"


def test_cost_caps_from_mapping_missing_mode_raises_distinct_error() -> None:
    with pytest.raises(BudgetAccountingUndeclaredError):
        cost_caps_from_mapping({"compute": 10.0, "storage": 100, "budget": 5.0})


def test_cost_caps_from_mapping_unknown_mode_raises_distinct_error() -> None:
    with pytest.raises(BudgetAccountingUndeclaredError):
        cost_caps_from_mapping(
            {
                "compute": 10.0,
                "storage": 100,
                "budget": 5.0,
                "budget_accounting_mode": "not-a-real-mode",
            }
        )


def test_cost_caps_from_mapping_per_unit_fixed_round_trips() -> None:
    caps = cost_caps_from_mapping(
        {
            "compute": 10.0,
            "storage": 100,
            "budget": 5.0,
            "budget_accounting_mode": BUDGET_ACCOUNTING_PER_UNIT_FIXED,
            "budget_unit_cost": 0.01,
        }
    )
    assert caps.budget_accounting_mode == BUDGET_ACCOUNTING_PER_UNIT_FIXED
    assert caps.budget_unit_cost == pytest.approx(0.01)
    assert caps.budget_charge_per_work_unit() == pytest.approx(0.01)
