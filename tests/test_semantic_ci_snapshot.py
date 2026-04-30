"""Golden snapshot tests for examples/semantic_ci fixtures."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scripts.regenerate_ci_fixtures import FIXTURE_DIR, SCENARIOS, render_scenario
from svp_rpe.cli import app


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_semantic_ci_fixture_snapshot_matches_golden(scenario: str) -> None:
    expected_path, actual_text = render_scenario(scenario)

    assert expected_path.is_file(), f"missing snapshot: {expected_path}"
    assert actual_text == expected_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ci_check_cli_matches_snapshot(scenario: str) -> None:
    scenario_dir = FIXTURE_DIR / scenario
    expected_path = scenario_dir / "expected_output.json"
    result = CliRunner().invoke(
        app,
        [
            "ci-check",
            str(scenario_dir / "target_svp.json"),
            str(scenario_dir / "observed_rpe.json"),
        ],
    )

    assert result.exit_code == 0
    assert result.output == expected_path.read_text(encoding="utf-8")


def test_regenerate_ci_fixtures_check_mode() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/regenerate_ci_fixtures.py", "--check"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "[check] OK" in result.stdout
