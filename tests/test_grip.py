"""K0/K1 grip harness tests."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts.measure_grip import _key_match_score, analyze_fixture, load_fixture, main
from svp_rpe.control import (
    GRIP_SATURATED,
    classify_grip,
    classify_match_grip,
    grip_effect_size,
    match_rate,
)

FIXTURE_PATH = Path("examples/control/k0/musicgen_rpe_fixture.json")
EXPECTED_PATH = Path("examples/control/k0/expected_grip.json")
K1_FIXTURE_PATH = Path("examples/control/k1/synth_performer_rpe_fixture.json")
K1_EXPECTED_PATH = Path("examples/control/k1/expected_grip.json")


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


def test_match_rate_bounds_and_validation() -> None:
    assert match_rate([1.0, 0.0]) == pytest.approx(0.5)
    assert match_rate([1.0, 1.0, 1.0]) == 1.0
    with pytest.raises(ValueError):
        match_rate([1.2])
    with pytest.raises(ValueError):
        match_rate([])


def test_classify_match_grip_thresholds() -> None:
    assert classify_match_grip(0.7) == "tight"
    assert classify_match_grip(0.699999) == "loose"
    assert classify_match_grip(0.3) == "loose"
    assert classify_match_grip(0.299999) == "dead"
    with pytest.raises(ValueError):
        classify_match_grip(1.5)


def test_key_match_score_known_relations() -> None:
    assert _key_match_score("C major", "C major") == 1.0
    # mir_eval weighted score: relative minor = 0.3、無関係キーは 0.0
    assert _key_match_score("C major", "A minor") == pytest.approx(0.3)
    assert _key_match_score("C major", "F# minor") == 0.0


def test_k1_fixture_snapshot_spans_tight_and_dead() -> None:
    """K1 代表マップ: 決定論的演奏者に対する 5 ツマミ + 補助センサーの grip 固定。"""
    report = analyze_fixture(load_fixture(K1_FIXTURE_PATH))
    expected = json.loads(K1_EXPECTED_PATH.read_text(encoding="utf-8"))

    assert report == expected
    by_knob = {result["knob"]: result for result in report["results"]}
    assert by_knob["bpm"]["classification"] == "tight"
    assert by_knob["key"]["classification"] == "tight"
    assert by_knob["key"]["kind"] == "categorical"
    # 正規センサー（spectral_centroid）では tight。legacy の帯域比センサーは
    # HF の乏しい素材で盲目になり dead に見える — 「ツマミ死」と「センサー盲」の判別例
    assert by_knob["brightness"]["classification"] == "tight"
    assert by_knob["brightness"]["sensor"] == "spectral_centroid"
    assert by_knob["brightness_band_ratio"]["classification"] == "dead"
    # 演奏者が読まないフィールド = 繋がっていないツマミは dead と検出される
    assert by_knob["active_rate_target"]["classification"] == "dead"
    assert by_knob["valley_depth_target"]["classification"] == "dead"
