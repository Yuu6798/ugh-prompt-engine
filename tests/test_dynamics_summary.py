"""tests/test_dynamics_summary.py — Track-level dynamics aggregation."""
from __future__ import annotations

import numpy as np
import pytest

from svp_rpe.rpe.dynamics_summary import compute_dynamics_summary
from svp_rpe.rpe.models import DynamicsSummary


def test_empty_curve_returns_neutral_summary() -> None:
    summary = compute_dynamics_summary(np.array([]))
    assert isinstance(summary, DynamicsSummary)
    assert summary.peak_novelty == 0.0
    assert summary.mean_novelty == 0.0
    assert summary.std_novelty == 0.0
    assert summary.event_count == 0
    assert summary.temporal_balance == 1.0


def test_flat_curve_has_no_events_and_balanced() -> None:
    """Constant novelty → no peaks, balanced halves."""
    summary = compute_dynamics_summary(np.full(100, 0.3))
    assert summary.peak_novelty == pytest.approx(0.3, abs=1e-4)
    assert summary.mean_novelty == pytest.approx(0.3, abs=1e-4)
    assert summary.std_novelty == pytest.approx(0.0, abs=1e-4)
    assert summary.event_count == 0
    assert summary.temporal_balance == pytest.approx(1.0, abs=1e-4)


def test_isolated_peaks_are_counted() -> None:
    """Three isolated peaks above (mean + 0.5*std) → event_count=3."""
    curve = np.zeros(60)
    curve[10] = 1.0
    curve[30] = 1.0
    curve[50] = 1.0
    summary = compute_dynamics_summary(curve)
    assert summary.event_count == 3
    assert summary.peak_novelty == 1.0


def test_front_loaded_curve_balance_above_one() -> None:
    """Energy concentrated in first half → temporal_balance > 1.0."""
    curve = np.concatenate([np.full(50, 1.0), np.full(50, 0.1)])
    summary = compute_dynamics_summary(curve)
    assert summary.temporal_balance > 1.0


def test_back_loaded_curve_balance_below_one() -> None:
    """Energy concentrated in second half → temporal_balance < 1.0."""
    curve = np.concatenate([np.full(50, 0.1), np.full(50, 1.0)])
    summary = compute_dynamics_summary(curve)
    assert summary.temporal_balance < 1.0


def test_deterministic_output() -> None:
    """Same input → same output (no random state)."""
    rng = np.random.default_rng(seed=42)
    curve = rng.random(200)
    summary_a = compute_dynamics_summary(curve)
    summary_b = compute_dynamics_summary(curve)
    assert summary_a == summary_b


def test_different_inputs_produce_different_summaries() -> None:
    """Discrimination check: a flat track and a spiky track must differ."""
    flat = np.full(100, 0.3)
    spiky = np.zeros(100)
    spiky[10::20] = 1.0  # 5 peaks

    flat_summary = compute_dynamics_summary(flat)
    spiky_summary = compute_dynamics_summary(spiky)

    assert flat_summary.event_count == 0
    assert spiky_summary.event_count >= 4
    assert spiky_summary.std_novelty > flat_summary.std_novelty
