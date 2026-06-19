"""R4-3 chord progression roundtrip comparison and fixity tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.compose.models import CompositionScore, PhysicalLayer
from svp_rpe.roundtrip import GripRecord, run_roundtrip
from svp_rpe.roundtrip.compare import (
    chord_sequence_match_rate,
    repeated_chord_sequence_match_rate,
)
from svp_rpe.roundtrip.diagnose import (
    CHORD_MATCH_THRESHOLD,
    _diagnose_chord_progression,
)
from svp_rpe.rpe.models import (
    ChordEvent,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SpectralProfile,
)
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.transcribe import draft_score

SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")
PROGRESSION = [("C", "major"), ("F", "major"), ("G", "major"), ("C", "major")]


def _score(progression: list[tuple[str, str]] | None) -> CompositionScore:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"].update(
        {
            "bpm": 120,
            "key": "C major",
            "time_signature": "4/4",
            "brightness": "neutral",
            "stereo_width": "wide",
        }
    )
    data["structure"] = [
        {
            "section": "main",
            "bars": 16,
            "role": "sparse chord bed",
            "physical": "sparse drums, steady chord blocks",
        }
    ]
    if progression is None:
        data.pop("events", None)
    else:
        data["events"] = {
            "chord_progression": [
                {"root": root, "quality": quality} for root, quality in progression
            ]
        }
    return CompositionScore.model_validate(data)


def _with_structure_sections(score: CompositionScore, count: int) -> CompositionScore:
    data = score.model_dump(mode="json")
    data["structure"] = [
        {
            "section": f"section_{index + 1}",
            "bars": 16,
            "role": "sparse chord bed",
            "physical": "sparse drums, steady chord blocks",
        }
        for index in range(count)
    ]
    return CompositionScore.model_validate(data)


def _physical_fixity() -> dict[str, str]:
    return {field: "locked" for field in PhysicalLayer.model_fields}


def test_chord_sequence_match_rate() -> None:
    assert chord_sequence_match_rate(PROGRESSION, PROGRESSION) == 1.0
    assert chord_sequence_match_rate(
        PROGRESSION,
        [("C", "major"), ("F", "major"), ("A", "minor"), ("C", "major")],
    ) == 0.75
    assert chord_sequence_match_rate([], []) == 1.0
    assert chord_sequence_match_rate(PROGRESSION, []) == 0.0
    assert chord_sequence_match_rate([], PROGRESSION) == 0.0


def test_repeated_chord_sequence_match_rate_handles_section_repeats() -> None:
    repeated_with_merged_boundary = [
        ("C", "major"),
        ("F", "major"),
        ("G", "major"),
        ("C", "major"),
        ("F", "major"),
        ("G", "major"),
        ("C", "major"),
    ]

    assert chord_sequence_match_rate(PROGRESSION, repeated_with_merged_boundary) < 0.75
    assert repeated_chord_sequence_match_rate(
        PROGRESSION,
        repeated_with_merged_boundary,
    ) == 1.0


def test_diagnose_chord_progression_branches() -> None:
    source = _score(PROGRESSION)
    preserved = _diagnose_chord_progression(source, _score(PROGRESSION), {})
    assert preserved.field == "chord_progression"
    assert preserved.diagnosis == "preserved"
    assert preserved.sensor == "compute_chord_events"
    assert preserved.sensor_state == "working"
    assert preserved.grip is None
    assert preserved.grip_class is None
    assert preserved.source_value == [
        {"root": root, "quality": quality} for root, quality in PROGRESSION
    ]
    assert preserved.transcribed_value == preserved.source_value
    assert f"threshold={CHORD_MATCH_THRESHOLD}" in str(preserved.note)

    disagreement = _diagnose_chord_progression(
        source,
        _score([("A", "minor"), ("D", "minor"), ("E", "minor"), ("A", "minor")]),
        {},
    )
    assert disagreement.diagnosis == "calibration_disagreement"

    blind = _diagnose_chord_progression(source, _score(None), {})
    assert blind.diagnosis == "sensor_blind"
    assert blind.sensor_state == "blind"

    dead = _diagnose_chord_progression(
        source,
        _score(PROGRESSION),
        {
            "chord_progression": GripRecord(
                knob="chord_progression",
                sensor="compute_chord_events",
                grip=0.0,
                classification="dead",
            )
        },
    )
    assert dead.diagnosis == "knob_dead"
    assert dead.grip == 0.0
    assert dead.grip_class == "dead"


def test_diagnose_chord_progression_preserves_repeated_sections() -> None:
    source = _with_structure_sections(_score(PROGRESSION), 2)
    transcribed = _score(
        [
            ("C", "major"),
            ("F", "major"),
            ("G", "major"),
            ("C", "major"),
            ("F", "major"),
            ("G", "major"),
            ("C", "major"),
        ]
    )

    field = _diagnose_chord_progression(source, transcribed, {})

    assert field.diagnosis == "preserved"
    assert "match rate=1.0" in str(field.note)


def test_run_roundtrip_reports_preserved_chord_progression() -> None:
    report = run_roundtrip(_score(PROGRESSION))

    field = next(item for item in report.fields if item.field == "chord_progression")

    assert field.diagnosis == "preserved"
    assert field.sensor == "compute_chord_events"
    assert field.sensor_state == "working"
    assert "match rate=" in str(field.note)


def test_run_roundtrip_without_events_keeps_physical_fields_only() -> None:
    report = run_roundtrip(_score(None))

    assert [field.field for field in report.fields] == [
        "bpm",
        "key",
        "time_signature",
        "brightness",
        "active_rate_target",
        "valley_depth_target",
        "stereo_width",
    ]


def test_chord_progression_fixity_is_optional_but_validated() -> None:
    data = _score(PROGRESSION).model_dump(mode="json")
    data["fixity"] = {**_physical_fixity(), "chord_progression": "locked"}

    score = CompositionScore.model_validate(data)

    assert score.fixity is not None
    assert score.fixity["chord_progression"] == "locked"

    data["fixity"]["chord_progression"] = "unlocked"
    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_draft_score_recovers_chord_events_and_marks_fixity_locked() -> None:
    score = draft_score(_bundle_with_chords(PROGRESSION))

    assert score.events is not None
    assert [
        (chord.root, chord.quality)
        for chord in score.events.chord_progression
    ] == PROGRESSION
    assert score.fixity is not None
    assert score.fixity["chord_progression"] == "locked"


def _bundle_with_chords(chords: list[tuple[str, str]]) -> RPEBundle:
    chord_events = [
        ChordEvent(
            chord=f"{root} {quality}",
            root=root,
            quality=quality,
            start_sec=float(index),
            end_sec=float(index + 1),
            confidence=0.9,
        )
        for index, (root, quality) in enumerate(chords)
    ]
    physical = PhysicalRPE(
        bpm=120.0,
        bpm_confidence=0.9,
        key="C",
        mode="major",
        key_confidence=0.9,
        duration_sec=8.0,
        sample_rate=44100,
        time_signature="4/4",
        time_signature_confidence=0.9,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=8.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.73,
        valley_depth=0.18,
        thickness=1.0,
        spectral_centroid=900.0,
        spectral_profile=SpectralProfile(
            centroid=900.0,
            low_ratio=0.4,
            mid_ratio=0.5,
            high_ratio=0.1,
            brightness=0.1,
        ),
        stereo_profile=None,
        onset_density=2.0,
        chord_events=chord_events,
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file="fixture.wav",
        audio_duration_sec=8.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )
