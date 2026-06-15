"""Roundtrip preservation diagnostics."""
from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from svp_rpe.compose import load_composition_score
from svp_rpe.compose.models import CompositionScore
from svp_rpe.perform import FAITHFUL_TAKE, perform, wav_bytes
from svp_rpe.roundtrip import GripRecord, diagnose_roundtrip, load_grip_map, run_roundtrip
from svp_rpe.transcribe import TODO_BRIGHTNESS_NEUTRAL, TODO_STEREO_UNMEASURED

ROOT = Path(__file__).resolve().parents[1]
ROUNDTRIP_DIR = ROOT / "examples" / "roundtrip"
K1_GRIP_FIXTURE = ROOT / "examples" / "control" / "k1" / "expected_grip.json"


def _load_source(name: str = "synth_01_source.yaml") -> CompositionScore:
    return load_composition_score(ROUNDTRIP_DIR / name)


def _with_physical(score: CompositionScore, **updates: Any) -> CompositionScore:
    data = score.model_dump(mode="json")
    data["physical"].update(updates)
    return CompositionScore.model_validate(data)


def _grips(**classes: str) -> dict[str, GripRecord]:
    records = {
        "bpm": "tight",
        "key": "tight",
        "brightness": "tight",
        "active_rate_target": "dead",
        "valley_depth_target": "dead",
    }
    records.update(classes)
    return {
        name: GripRecord(knob=name, sensor=name, grip=1.0, classification=classification)
        for name, classification in records.items()
    }


def _field(report, name: str):
    return next(item for item in report.fields if item.field == name)


def _assert_no_outcome_keys(value: Any) -> None:
    if isinstance(value, dict):
        for forbidden in ("verdict", "passed", "pass", "failed", "fail", "ok", "loss"):
            assert forbidden not in value
        for item in value.values():
            _assert_no_outcome_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_outcome_keys(item)


def test_diagnose_roundtrip_branches_are_descriptive_only():
    source = _load_source()
    transcribed = _with_physical(
        source,
        key="D major",
        brightness=TODO_BRIGHTNESS_NEUTRAL,
        active_rate_target="0.98-1.00",
        valley_depth_target="0.30-0.40",
        stereo_width=TODO_STEREO_UNMEASURED,
    )

    report = diagnose_roundtrip(source, transcribed, grip_map=_grips())

    assert _field(report, "bpm").diagnosis == "preserved"
    assert _field(report, "key").diagnosis == "calibration_disagreement"
    assert _field(report, "brightness").diagnosis == "sensor_blind"
    assert _field(report, "active_rate_target").diagnosis == "knob_dead"
    assert _field(report, "valley_depth_target").diagnosis == "preserved"
    assert _field(report, "stereo_width").diagnosis == "sensor_blind"
    _assert_no_outcome_keys(report.model_dump(mode="json"))


def test_diagnosis_uses_grip_map_data():
    source = _load_source()
    transcribed = _with_physical(source, active_rate_target="0.98-1.00")

    dead_report = diagnose_roundtrip(
        source,
        transcribed,
        grip_map=_grips(active_rate_target="dead"),
    )
    tight_report = diagnose_roundtrip(
        source,
        transcribed,
        grip_map=_grips(active_rate_target="tight"),
    )

    assert _field(dead_report, "active_rate_target").diagnosis == "knob_dead"
    assert _field(tight_report, "active_rate_target").diagnosis == "calibration_disagreement"


def test_default_grip_map_is_packaged_resource():
    resource = files("svp_rpe.roundtrip.data").joinpath("expected_grip.json")

    assert resource.is_file()
    assert json.loads(resource.read_text(encoding="utf-8")) == json.loads(
        K1_GRIP_FIXTURE.read_text(encoding="utf-8")
    )
    records = load_grip_map()

    assert records["bpm"].classification == "tight"
    assert records["active_rate_target"].classification == "dead"


def test_bpm_roundtrip_uses_five_bpm_tolerance():
    source = _load_source()

    within_tolerance = diagnose_roundtrip(
        source,
        _with_physical(source, bpm=source.physical.bpm + 5),
        grip_map=_grips(),
    )
    beyond_tolerance = diagnose_roundtrip(
        source,
        _with_physical(source, bpm=source.physical.bpm + 6),
        grip_map=_grips(),
    )

    assert _field(within_tolerance, "bpm").diagnosis == "preserved"
    assert _field(beyond_tolerance, "bpm").diagnosis == "calibration_disagreement"


def test_key_roundtrip_normalizes_enharmonic_spellings():
    source = _with_physical(_load_source(), key="Eb major")
    transcribed = _with_physical(source, key="D# major")
    unicode_source = _with_physical(_load_source(), key="F\u266f minor")
    unicode_transcribed = _with_physical(unicode_source, key="F# minor")

    flat_report = diagnose_roundtrip(source, transcribed, grip_map=_grips())
    sharp_report = diagnose_roundtrip(
        unicode_source, unicode_transcribed, grip_map=_grips()
    )

    assert _field(flat_report, "key").diagnosis == "preserved"
    assert _field(sharp_report, "key").diagnosis == "preserved"


def test_performer_roundtrip_source_is_byte_deterministic():
    score = _load_source()

    first = wav_bytes(perform(score, FAITHFUL_TAKE))
    second = wav_bytes(perform(score, FAITHFUL_TAKE))

    assert first == second


def test_roundtrip_snapshots_match_expected_reports():
    for source_path in sorted(ROUNDTRIP_DIR.glob("*_source.yaml")):
        expected_path = ROUNDTRIP_DIR / source_path.name.replace("_source.yaml", "_roundtrip.json")
        score = load_composition_score(source_path)
        actual = run_roundtrip(score).model_dump(mode="json")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        assert actual == expected
        _assert_no_outcome_keys(actual)
