"""Composition Score schema and TargetSVP conversion tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.compose import (
    CompositionScore,
    composition_to_target_svp,
    load_composition_score,
)
from svp_rpe.semantic_ci import TargetSVP, stable_hash


SAMPLE_PATH = Path("examples/composition/midnight_signal.yaml")


def test_midnight_signal_yaml_model_validates() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))

    score = CompositionScore.model_validate(data)

    assert score.meta.title == "Midnight Signal"
    assert score.meta.version == "0.1"
    assert score.semantic.grv.primary == "deep_house"
    assert score.rendering.target_backend == "external"
    assert score.rendering.prompt_max_chars == 500
    assert score.rendering.priority[-1] == "physical.optional"


def test_load_composition_score_reads_yaml() -> None:
    score = load_composition_score(SAMPLE_PATH)

    assert isinstance(score, CompositionScore)
    assert score.physical.bpm == 128
    assert len(score.structure) == 4


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
