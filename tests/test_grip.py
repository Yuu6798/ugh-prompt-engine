"""K0 grip harness tests."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts.measure_grip import analyze_fixture, load_fixture, main
from svp_rpe.control import GRIP_SATURATED, classify_grip, grip_effect_size

FIXTURE_PATH = Path("examples/control/k0/musicgen_rpe_fixture.json")
EXPECTED_PATH = Path("examples/control/k0/expected_grip.json")


def test_grip_effect_size_uses_pooled_sd() -> None:
    assert grip_effect_size([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(3.0)
    assert grip_effect_size([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 3.0, 4.0, 5.0, 6.0]) == pytest.approx(
        0.632455532
    )


def test_grip_effect_size_zero_variance_rules_are_finite() -> None:
    assert grip_effect_size([1.0, 1.0], [1.0, 1.0]) == 0.0
    assert grip_effect_size([1.0, 1.0], [2.0, 2.0]) == GRIP_SATURATED
    assert grip_effect_size([2.0, 2.0], [1.0, 1.0]) == -GRIP_SATURATED

    for value in (
        grip_effect_size([1.0, 1.0], [1.0, 1.0]),
        grip_effect_size([1.0, 1.0], [2.0, 2.0]),
        grip_effect_size([2.0, 2.0], [1.0, 1.0]),
    ):
        assert math.isfinite(value)


def test_classify_grip_thresholds_and_sign() -> None:
    assert classify_grip(0.8, expected_sign=1) == "tight"
    assert classify_grip(0.2, expected_sign=1) == "loose"
    assert classify_grip(0.199999, expected_sign=1) == "dead"
    assert classify_grip(-0.3, expected_sign=1) == "dead"
    assert classify_grip(-GRIP_SATURATED, expected_sign=-1) == "tight"
    assert classify_grip(GRIP_SATURATED, expected_sign=-1) == "dead"


def test_classify_grip_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        classify_grip(1.0, expected_sign=0)
    with pytest.raises(ValueError):
        classify_grip(float("nan"), expected_sign=1)


def test_k0_fixture_snapshot() -> None:
    report = analyze_fixture(load_fixture(FIXTURE_PATH))
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["bpm"]["grip"] > 0.8
    assert by_knob["brightness"]["classification"] == "dead"


def test_measure_grip_cli_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixture", str(FIXTURE_PATH), "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def test_measure_grip_cli_knob_filter(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--fixture", str(FIXTURE_PATH), "--json", "--knob", "bpm"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [result["knob"] for result in payload["results"]] == ["bpm"]
    assert payload["summary"] == {"tight": 1, "loose": 0, "dead": 0}
