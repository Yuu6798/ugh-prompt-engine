"""tests/test_cli.py — CLI smoke tests."""
from __future__ import annotations

from typer.testing import CliRunner

from svp_rpe.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "SVP-RPE" in result.output


def test_extract_with_real_audio(sine_wave_mono):
    result = runner.invoke(app, ["extract", sine_wave_mono])
    assert result.exit_code == 0
    assert "RPE" in result.output or "schema_version" in result.output


def test_run_with_real_audio(sine_wave_mono):
    result = runner.invoke(app, ["run", sine_wave_mono, "--no-save"])
    assert result.exit_code == 0
    assert "Integrated Score" in result.output


def test_evaluate_accepts_baseline_profile(sine_wave_mono):
    result = runner.invoke(app, ["evaluate", "--audio", sine_wave_mono, "--baseline", "edm"])
    assert result.exit_code == 0
    assert '"baseline_profile": "edm"' in result.output


def test_extract_missing_file():
    result = runner.invoke(app, ["extract", "nonexistent.wav"])
    assert result.exit_code != 0
