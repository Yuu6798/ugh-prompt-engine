"""Markdown report tests for semantic CI fixtures."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scripts.regenerate_ci_fixtures import (
    FIXTURE_DIR,
    SCENARIOS,
    build_run,
    render_report_scenario,
)
from svp_rpe.cli import app
from svp_rpe.semantic_ci import ObservedRPE, TargetSVP, render_markdown, run_semantic_ci


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_semantic_ci_markdown_report_matches_golden(scenario: str) -> None:
    expected_path, actual_text = render_report_scenario(scenario)

    assert expected_path.is_file(), f"missing snapshot: {expected_path}"
    assert actual_text == expected_path.read_text(encoding="utf-8")


def test_render_markdown_is_deterministic() -> None:
    scenario_dir = FIXTURE_DIR / "repair_degraded"
    run = build_run(scenario_dir / "target_svp.json", scenario_dir / "observed_rpe.json")

    assert render_markdown(run) == render_markdown(run)


def test_render_markdown_escapes_table_cells() -> None:
    run = run_semantic_ci(
        TargetSVP(id="target|a\r\nb", core="core", preserve=["core"]),
        ObservedRPE(id="observed|c\r\nd", signals=["core"]),
    )

    report = render_markdown(run)

    assert "target\\|a b" in report
    assert "observed\\|c d" in report
    assert "target\\|a  b" not in report
    assert "observed\\|c  d" not in report


def test_ci_check_explicit_json_matches_existing_snapshot() -> None:
    scenario_dir = FIXTURE_DIR / "pass_perfect"
    result = CliRunner().invoke(
        app,
        [
            "ci-check",
            str(scenario_dir / "target_svp.json"),
            str(scenario_dir / "observed_rpe.json"),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    json.loads(result.output)
    assert result.output == (scenario_dir / "expected_output.json").read_text(encoding="utf-8")


def test_ci_check_cli_markdown_stdout_matches_snapshot() -> None:
    scenario_dir = FIXTURE_DIR / "repair_degraded"
    result = CliRunner().invoke(
        app,
        [
            "ci-check",
            str(scenario_dir / "target_svp.json"),
            str(scenario_dir / "observed_rpe.json"),
            "--format",
            "markdown",
        ],
    )

    assert result.exit_code == 1
    assert result.output == (scenario_dir / "expected_report.md").read_text(encoding="utf-8")


def test_ci_check_cli_markdown_output_file_matches_snapshot(tmp_path) -> None:
    scenario_dir = FIXTURE_DIR / "repair_budget_zero"
    output_path = tmp_path / "semantic_ci_report.md"
    result = CliRunner().invoke(
        app,
        [
            "ci-check",
            str(scenario_dir / "target_svp.json"),
            str(scenario_dir / "observed_rpe.json"),
            "--format",
            "markdown",
            "-o",
            str(output_path),
        ],
    )

    assert result.exit_code == 1
    assert output_path.read_text(encoding="utf-8") == (
        scenario_dir / "expected_report.md"
    ).read_text(encoding="utf-8")
