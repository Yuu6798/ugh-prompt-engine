"""R4-2 event-layer chord progression grip tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.compose.models import ChordSpec, CompositionScore, EventLayer
from svp_rpe.perform import FAITHFUL_TAKE, perform
from svp_rpe.perform.synth import SAMPLE_RATE
from svp_rpe.rpe.physical_features import compute_chord_events

SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")


def _score(
    progression: list[tuple[str, str]] | None,
    *,
    key: str = "C major",
) -> CompositionScore:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"].update(
        {
            "bpm": 120,
            "key": key,
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


def _observed_unique(score: CompositionScore) -> set[tuple[str, str]]:
    samples = perform(score, FAITHFUL_TAKE).astype(np.float32) / 32768.0
    return {
        (event.root, event.quality)
        for event in compute_chord_events(samples, SAMPLE_RATE)
    }


def test_authored_chord_progression_is_observed_by_chord_sensor() -> None:
    progression = [("C", "major"), ("F", "major"), ("G", "major"), ("C", "major")]
    authored_unique = set(progression)

    observed_unique = _observed_unique(_score(progression))

    assert authored_unique <= observed_unique


def test_different_chord_progressions_produce_different_chord_events() -> None:
    major_progression = [("C", "major"), ("F", "major"), ("G", "major"), ("C", "major")]
    minor_progression = [("A", "minor"), ("D", "minor"), ("E", "minor"), ("A", "minor")]

    major_observed = _observed_unique(_score(major_progression))
    minor_observed = _observed_unique(_score(minor_progression))

    assert major_observed != minor_observed


def test_missing_or_empty_events_keep_key_derived_fallback_output() -> None:
    fallback = _score(None, key="C major")
    empty_events = CompositionScore.model_validate(
        {
            **fallback.model_dump(mode="json"),
            "events": EventLayer().model_dump(mode="json"),
        }
    )
    authored_fallback = _score(
        [("C", "major"), ("F", "major"), ("G", "major"), ("C", "major")],
        key="C major",
    )

    assert perform(fallback, FAITHFUL_TAKE).tobytes() == perform(
        empty_events, FAITHFUL_TAKE
    ).tobytes()
    assert perform(fallback, FAITHFUL_TAKE).tobytes() == perform(
        authored_fallback, FAITHFUL_TAKE
    ).tobytes()


def test_chord_spec_schema_validation_and_event_round_trip() -> None:
    score = _score([("C#", "major"), ("A", "minor")], key="C# major")
    dumped = score.model_dump(mode="json")

    assert "events" in dumped
    assert CompositionScore.model_validate(dumped).model_dump(mode="json") == dumped

    without_events = _score(None).model_dump(mode="json")
    assert "events" not in without_events
    assert "events" not in CompositionScore.model_validate(without_events).model_dump(
        mode="json"
    )

    with pytest.raises(ValidationError):
        ChordSpec(root="Db", quality="major")
    with pytest.raises(ValidationError):
        ChordSpec(root="C", quality="dominant")
