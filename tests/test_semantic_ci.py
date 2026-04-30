"""Deterministic semantic CI Phase 1 core tests."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.semantic_ci import (
    ObservedRPE,
    TargetSVP,
    compare_expected_observed,
    generate_expected_rpe,
    generate_repair_svp,
    run_semantic_ci,
    stable_hash,
)


def _target(change_budget: int = 3) -> TargetSVP:
    return TargetSVP(
        id="target-001",
        domain="music",
        core="energetic driving dense",
        surface=["bright", "wide stereo"],
        grv=["bass-heavy", "148 bpm"],
        delta_e_profile="gradual build",
        preserve=["chorus lift"],
        avoid=["dark ambient"],
        lock=["148 bpm"],
        metric_targets={"bpm": 148.0, "active_rate": 0.9},
        tolerances={"bpm": 0.0, "active_rate": 0.0},
        change_budget=change_budget,
    )


def _matching_observed() -> ObservedRPE:
    return ObservedRPE(
        id="fixture-perfect",
        domain="music",
        signals=[
            "148 bpm",
            "bass-heavy",
            "bright",
            "chorus lift",
            "energetic driving dense",
            "gradual build",
            "wide stereo",
        ],
        metrics={"bpm": 148.0, "active_rate": 0.9},
    )


def test_target_svp_generates_expected_rpe_deterministically():
    target = _target()

    expected_1 = generate_expected_rpe(target)
    expected_2 = generate_expected_rpe(target)

    assert expected_1 == expected_2
    assert stable_hash(expected_1) == stable_hash(expected_2)
    assert "energetic driving dense" in expected_1.required_signals
    assert expected_1.source_hash == stable_hash(target)


def test_matching_observed_rpe_has_zero_loss():
    result = run_semantic_ci(_target(), _matching_observed())

    assert result.semantic_diff.loss == 0.0
    assert result.semantic_diff.verdict == "pass"
    assert result.semantic_diff.missing == []
    assert result.semantic_diff.over_changed == []


def test_degraded_observed_rpe_increases_loss_and_splits_diff():
    target = _target()
    observed = ObservedRPE(
        id="fixture-degraded",
        domain="music",
        signals=["148 bpm", "bass-heavy", "dark ambient", "unexpected pad"],
        metrics={"bpm": 132.0, "active_rate": 0.4},
    )

    diff = compare_expected_observed(generate_expected_rpe(target), observed)

    assert diff.loss > 0.0
    assert diff.verdict == "repair"
    assert "148 bpm" in diff.preserved
    assert "bass-heavy" in diff.preserved
    assert "bright" in diff.missing
    assert "dark ambient" in diff.over_changed
    assert "unexpected pad" in diff.over_changed
    assert all(not metric.passed for metric in diff.metric_diffs)


def test_repair_svp_separates_preserve_restore_reduce_lock():
    target = _target(change_budget=10)
    diff = compare_expected_observed(
        generate_expected_rpe(target),
        ObservedRPE(
            id="fixture-repair",
            domain="music",
            signals=["148 bpm", "unexpected pad"],
            metrics={"bpm": 140.0, "active_rate": 0.1},
        ),
    )

    repair = generate_repair_svp(target, diff)

    assert "148 bpm" in repair.preserve
    assert "148 bpm" in repair.lock
    assert "bass-heavy" in repair.restore
    assert "unexpected pad" in repair.reduce
    assert {action.op for action in repair.repair_order} == {
        "preserve",
        "restore",
        "reduce",
        "lock",
    }


def test_change_budget_limits_restore_and_reduce_edits():
    target = _target(change_budget=1)
    observed = ObservedRPE(
        id="fixture-budget",
        domain="music",
        signals=["unexpected pad", "unexpected riser"],
        metrics={},
    )

    repair = run_semantic_ci(target, observed).repair_svp
    applied_edits = [
        action for action in repair.repair_order
        if action.applied and action.op in {"restore", "reduce"}
    ]

    assert len(applied_edits) == 1
    assert repair.deferred_restore or repair.deferred_reduce


def test_roundtrip_log_records_state_transitions_and_hashes():
    result = run_semantic_ci(_target(), _matching_observed())
    log = result.roundtrip_log

    assert [step.name for step in log.transitions] == [
        "target_svp",
        "expected_rpe",
        "observed_rpe",
        "semantic_diff",
        "repair_svp",
    ]
    assert log.target_svp_hash == stable_hash(result.target_svp)
    assert log.expected_rpe_hash == stable_hash(result.expected_rpe)
    assert log.semantic_diff_hash == stable_hash(result.semantic_diff)
    assert log.final_hash


def test_same_input_produces_same_output_and_same_hash():
    first = run_semantic_ci(_target(), _matching_observed())
    second = run_semantic_ci(_target(), _matching_observed())

    assert first == second
    assert stable_hash(first) == stable_hash(second)
    assert first.roundtrip_log.final_hash == second.roundtrip_log.final_hash


def test_ci_check_cli_outputs_roundtrip_log(tmp_path):
    target_path = tmp_path / "target.json"
    observed_path = tmp_path / "observed.json"
    target_path.write_text(
        json.dumps(_target().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    observed_path.write_text(
        json.dumps(_matching_observed().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["ci-check", str(target_path), str(observed_path)])

    assert result.exit_code == 0
    assert '"roundtrip_log"' in result.output
    assert '"loss": 0.0' in result.output
