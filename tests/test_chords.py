"""tests/test_chords.py - Q2-2 chord event extraction."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from scripts import validate_against_truth as vat
from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_physical
from svp_rpe.rpe.physical_features import compute_chord_events

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"


def _truth_rows() -> list[dict]:
    return yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))


def test_compute_chord_events_is_deterministic() -> None:
    row = _truth_rows()[2]
    audio = load_audio(str(SAMPLE_DIR / row["filename"]))

    first = compute_chord_events(audio.y_mono, audio.sr)
    second = compute_chord_events(audio.y_mono, audio.sr)

    assert [event.model_dump() for event in first] == [
        event.model_dump() for event in second
    ]
    assert first
    assert all(event.start_sec < event.end_sec for event in first)


def test_synth_major_three_chords_are_detected() -> None:
    """Q2-2 target: the main I/IV/V-style blocks are visible with timestamps."""
    row = next(row for row in _truth_rows() if row["id"] == "synth_03_mid_groove_g_major")
    audio = load_audio(str(SAMPLE_DIR / row["filename"]))
    expected_unique = {event["chord"] for event in row["chord_events"]}

    events = compute_chord_events(audio.y_mono, audio.sr)
    observed_unique = {event.chord for event in events}

    assert expected_unique <= observed_unique
    assert len(expected_unique) >= 3


def test_extractor_populates_chord_events() -> None:
    row = _truth_rows()[4]
    audio = load_audio(str(SAMPLE_DIR / row["filename"]))

    phys, _, _ = extract_physical(audio)

    assert phys.chord_events
    assert all(0.0 <= event.confidence <= 1.0 for event in phys.chord_events)
    assert all(0.0 <= event.start_sec < event.end_sec <= phys.duration_sec for event in phys.chord_events)


def test_silence_has_no_chord_events() -> None:
    sr = 22050
    y = np.zeros(sr, dtype=np.float32)

    assert compute_chord_events(y, sr) == []


def test_chord_validation_hits_all_synth_samples() -> None:
    results = [vat.evaluate_song(song) for song in vat.load_truth()]

    assert all(r.chords.event_hit_rate >= vat.CHORD_EVENT_HIT_RATE_MIN for r in results)
    assert all(len(r.chords.unique_matched) >= 3 for r in results)
