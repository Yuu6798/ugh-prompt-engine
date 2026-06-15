"""Roundtrip corpus manifest and batch tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from svp_rpe.perform import sha256_bytes
from svp_rpe.roundtrip import classify_take, load_manifest, run_corpus_batch
from svp_rpe.roundtrip.manifest import RoundtripManifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "examples" / "roundtrip" / "corpus" / "manifest.yaml"
SNAPSHOT_PATH = ROOT / "examples" / "roundtrip" / "corpus" / "batch_report.json"
SYNTH_AUDIO = ROOT / "examples" / "sample_input" / "synth_05_fast_bright_d_major.wav"


def _assert_no_outcome_keys(value: Any) -> None:
    if isinstance(value, dict):
        for forbidden in ("verdict", "passed", "pass", "failed", "fail", "ok", "loss"):
            assert forbidden not in value
        for item in value.values():
            _assert_no_outcome_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_outcome_keys(item)


def test_manifest_loads_observation_logs_and_calibratable_take():
    manifest = load_manifest(MANIFEST_PATH)

    assert manifest.schema_version == "1.0"
    assert [take.id for take in manifest.takes] == [
        "C-orig",
        "C-gen",
        "J-rock",
        "J-ebm",
        "synth-05-fast-bright-d-major",
    ]
    assert classify_take(manifest.takes[-1], repo_root=ROOT) == "calibratable"
    assert classify_take(manifest.takes[0], repo_root=ROOT) == "observation_log"


def test_manifest_rejects_unknown_intent_field():
    data = {
        "schema_version": "1.0",
        "takes": [
            {
                "id": "bad",
                "generator": "synth",
                "prompt": "fixture",
                "intent": {
                    "unknown": {"value": 1, "send_form": "numeric_knob"},
                },
                "notes": [],
            }
        ],
    }

    with pytest.raises(ValidationError, match="unknown"):
        RoundtripManifest.model_validate(data)


def test_manifest_rejects_invalid_send_form():
    data = {
        "schema_version": "1.0",
        "takes": [
            {
                "id": "bad",
                "generator": "synth",
                "prompt": "fixture",
                "intent": {
                    "bpm": {"value": 120, "send_form": "literal_text"},
                },
                "notes": [],
            }
        ],
    }

    with pytest.raises(ValidationError, match="literal"):
        RoundtripManifest.model_validate(data)


def test_classify_take_demotes_missing_or_hash_mismatched_audio():
    manifest = load_manifest(MANIFEST_PATH)
    synth_take = manifest.takes[-1]
    data = synth_take.model_dump(mode="json")

    data["audio_hash"] = "0" * 64
    mismatched = type(synth_take).model_validate(data)
    assert classify_take(mismatched, repo_root=ROOT) == "observation_log"

    data["audio_hash"] = sha256_bytes(SYNTH_AUDIO.read_bytes())
    data["audio_locator"] = ""
    missing_locator = type(synth_take).model_validate(data)
    assert classify_take(missing_locator, repo_root=ROOT) == "observation_log"


def test_corpus_batch_snapshot_is_deterministic_and_descriptive_only():
    manifest = load_manifest(MANIFEST_PATH)
    actual = run_corpus_batch(manifest, repo_root=ROOT).model_dump(mode="json")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert actual == expected
    _assert_no_outcome_keys(actual)

    synth = next(take for take in actual["takes"] if take["id"].startswith("synth-05"))
    assert synth["layer"] == "calibratable"
    assert synth["regenerated"] is True
    assert {item["field"] for item in synth["comparisons"]} == {
        "bpm",
        "key",
        "time_signature",
    }

    c_orig = next(take for take in actual["takes"] if take["id"] == "C-orig")
    assert c_orig["layer"] == "observation_log"
    assert c_orig["regenerated"] is False
    assert c_orig["comparisons"][0]["measured"] == 83
