"""Unit tests for the R1 corpus screening comparison logic (pure, no extraction)."""
from __future__ import annotations

import scripts.screen_corpus as sc


def test_parse_key_combined_and_split() -> None:
    assert sc.parse_key("D minor") == ("D", "minor")
    assert sc.parse_key("E major") == ("E", "major")
    assert sc.parse_key("F#", "minor") == ("F#", "minor")
    assert sc.parse_key(None) == (None, None)


def test_pitch_class_handles_sharps_and_flats() -> None:
    assert sc.pitch_class("C") == 0
    assert sc.pitch_class("F#") == 6
    assert sc.pitch_class("Gb") == 6
    assert sc.pitch_class("Bb") == 10
    assert sc.pitch_class("H") is None


def test_bpm_relation_classes() -> None:
    # 紫電: 168 -> 172.27 = preserved (within ±4%)
    assert sc.bpm_relation(168.0, 172.27)["status"] == "preserved"
    # J-rock: 175 -> 136 = off (non-octave misdetection)
    off = sc.bpm_relation(175.0, 136.0)
    assert off["status"] == "off"
    assert off["error_pct"] == 22.3
    # clean octave errors
    assert sc.bpm_relation(170.0, 85.0)["status"] == "octave_half"
    assert sc.bpm_relation(85.0, 170.0)["status"] == "octave_double"
    assert sc.bpm_relation(168.0, None)["status"] == "no_detection"


def test_key_relation_classes() -> None:
    assert sc.key_relation("D", "minor", "D", "minor") == "preserved"
    # J-rock: E major stated, D major detected = off (whole step, not a clean relation)
    assert sc.key_relation("E", "major", "D", "major") == "off"
    # parallel: same root different mode
    assert sc.key_relation("D", "major", "D", "minor") == "parallel"
    # relative: C major <-> A minor
    assert sc.key_relation("C", "major", "A", "minor") == "relative"
    assert sc.key_relation("A", "minor", "C", "major") == "relative"
    assert sc.key_relation(None, None, "C", "major") == "unknown"


def test_aggregate_base_rates_and_unflagged() -> None:
    rows = [
        {
            "id": "ok",
            "detected": {"bpm_octave_ambiguous": False},
            "bpm_relation": {"status": "preserved", "ratio": 1.0},
            "key_relation": "preserved",
        },
        {
            "id": "bad",
            "detected": {"bpm_octave_ambiguous": False},
            "bpm_relation": {"status": "off", "ratio": 0.78},
            "key_relation": "off",
        },
    ]
    summary = sc.aggregate(rows)
    assert summary["n_songs"] == 2
    assert summary["bpm_preservation_rate"] == 0.5
    assert summary["key_preservation_rate"] == 0.5
    # "off" bpm error on a take the octave detector did not flag is surfaced
    assert summary["bpm_errors_unflagged_by_octave_detector"] == ["bad"]
