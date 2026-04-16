"""tests/test_cli.py — CLI smoke tests."""
from __future__ import annotations

from typer.testing import CliRunner

from svp_rpe.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "SVP-RPE" in result.output


def test_extract_stub():
    result = runner.invoke(app, ["extract", "test.wav"])
    assert result.exit_code == 0
    assert "stub" in result.output


def test_run_stub():
    result = runner.invoke(app, ["run", "test.wav"])
    assert result.exit_code == 0
    assert "stub" in result.output
