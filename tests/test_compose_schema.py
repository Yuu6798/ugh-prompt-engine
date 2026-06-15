"""Composition Score schema and TargetSVP conversion tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.compose import (
    CompositionScore,
    composition_to_target_svp,
    field_fixity,
    load_composition_score,
)
from svp_rpe.semantic_ci import TargetSVP, stable_hash


SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")


def test_midnight_signal_yaml_model_validates() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))

    score = CompositionScore.model_validate(data)

    assert score.meta.title == "Midnight Signal"
    assert score.meta.version == "0.1"
    assert score.semantic.grv.primary == "deep_house"
    assert score.rendering.target_backend == "external"
    assert score.rendering.prompt_max_chars == 650
    assert score.rendering.priority[-1] == "physical.optional"


def test_load_composition_score_reads_yaml() -> None:
    score = load_composition_score(SAMPLE_PATH)

    assert isinstance(score, CompositionScore)
    assert score.fixity is None
    assert "fixity" not in score.model_dump(mode="json")
    assert score.physical.bpm == 128
    assert len(score.structure) == 4


def test_numeric_bpm_string_normalizes_to_int() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = "128"

    score = CompositionScore.model_validate(data)
    target = composition_to_target_svp(score)

    assert score.physical.bpm == 128
    assert target.metric_targets["bpm"] == 128


def test_transcribe_todo_bpm_sentinel_is_allowed() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = "TODO(transcribe): bpm undetected"

    score = CompositionScore.model_validate(data)

    assert score.physical.bpm == "TODO(transcribe): bpm undetected"


@pytest.mark.parametrize("bpm", ["128 bpm", "fast", "TODO: bpm undetected"])
def test_arbitrary_bpm_strings_are_rejected(bpm: str) -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = bpm

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_valid_fixity_is_accepted() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["fixity"] = {
        "bpm": "locked",
        "key": "locked",
        "time_signature": "locked",
        "active_rate_target": "locked",
        "valley_depth_target": "locked",
        "brightness": "unlocked",
        "stereo_width": "unlocked",
    }

    score = CompositionScore.model_validate(data)

    assert score.fixity == data["fixity"]
    assert field_fixity(score) == data["fixity"]


def test_fixity_unknown_key_is_rejected() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["fixity"] = {"section": "locked"}

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_fixity_unknown_value_is_rejected() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["fixity"] = {"bpm": "measured"}

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_fixity_partial_map_is_rejected() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["fixity"] = {"bpm": "locked"}

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_field_fixity_derives_from_todo_sentinels_when_absent() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = "TODO(transcribe): bpm undetected"
    data["physical"]["stereo_width"] = "TODO(transcribe): stereo unmeasured"

    fixity = field_fixity(CompositionScore.model_validate(data))

    assert fixity["bpm"] == "unlocked"
    assert fixity["key"] == "locked"
    assert fixity["stereo_width"] == "unlocked"


def test_composition_to_target_svp_maps_required_fields() -> None:
    target = composition_to_target_svp(load_composition_score(SAMPLE_PATH))

    assert isinstance(target, TargetSVP)
    assert target.id == "midnight-signal"
    assert target.core == "introspective night drive"
    assert target.grv == ["ambient", "deep_house"]
    assert target.delta_e_profile == "gradual build from solitude to release"
    assert target.avoid == ["bright festival edm", "comic vocal delivery"]
    assert target.metric_targets == {
        "bpm": 128,
        "key": "C minor",
        "time_signature": "4/4",
        "active_rate_target": "0.90-0.93",
        "valley_depth_target": "0.15-0.25",
        "brightness": "dark",
        "stereo_width": "wide",
    }
    assert target.notes == [
        "bridge(8bars): role=near silence and reflection | "
        "physical=no kick, no bass, minimal texture",
        "chorus(16bars): role=emotional release | "
        "physical=full energy, wide stereo, focused layers",
        "intro(8bars): role=establish loneliness | physical=low density, sub bass only",
        "verse(16bars): role=restrained movement | "
        "physical=sparse drums, short phrases, clear rests",
    ]


def test_missing_required_field_raises_validation_error(tmp_path: Path) -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    del data["physical"]["bpm"]
    path = tmp_path / "missing_bpm.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_composition_score(path)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["semantic"]["unexpected"] = "not canonical"
    path = tmp_path / "extra_field.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_composition_score(path)


def test_same_yaml_produces_same_target_svp() -> None:
    first = composition_to_target_svp(load_composition_score(SAMPLE_PATH))
    second = composition_to_target_svp(load_composition_score(SAMPLE_PATH))

    assert first == second
    assert stable_hash(first) == stable_hash(second)
