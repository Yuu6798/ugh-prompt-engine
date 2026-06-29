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
        "brightness": "locked",
        "stereo_width": "locked",
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


def test_fixity_stale_state_is_rejected() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["fixity"] = {
        "bpm": "unlocked",
        "key": "locked",
        "time_signature": "locked",
        "active_rate_target": "locked",
        "valley_depth_target": "locked",
        "brightness": "locked",
        "stereo_width": "locked",
    }

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_fixity_unlocked_matches_todo_state() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = "TODO(transcribe): bpm undetected"
    data["fixity"] = {
        "bpm": "unlocked",
        "key": "locked",
        "time_signature": "locked",
        "active_rate_target": "locked",
        "valley_depth_target": "locked",
        "brightness": "locked",
        "stereo_width": "locked",
    }

    score = CompositionScore.model_validate(data)

    assert score.fixity["bpm"] == "unlocked"


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


# --- control_profile (PR1) ---------------------------------------------------

_SUNO_PROFILE = {
    "suno": {
        "bpm": {
            "grip_class": "tight",
            "grip": 1.61,
            "sensor": "bpm",
            "evidence": "examples/control/k2/expected_grip.json",
        },
        "brightness": {
            "grip_class": "tight",
            "grip": 0.86,
            "sensor": "spectral_centroid",
            "evidence": "examples/control/k2/expected_grip.json",
        },
    }
}


def test_sample_score_carries_k2_suno_control_profile() -> None:
    """初期データ: example Score に K2 由来の suno プロファイルが載り参照が解決可能。"""
    score = load_composition_score(SAMPLE_PATH)

    assert score.control_profile is not None
    suno = score.control_profile["suno"]
    assert set(suno) == {"bpm", "brightness"}
    assert suno["bpm"].grip_class == "tight"
    assert suno["bpm"].grip == pytest.approx(1.61)
    assert suno["brightness"].sensor == "spectral_centroid"
    # evidence 参照が実在ファイルを指す（解決可能）。
    assert Path(suno["bpm"].evidence).exists()


def test_control_profile_roundtrips_invariant() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = _SUNO_PROFILE

    score = CompositionScore.model_validate(data)
    dumped = score.model_dump(mode="json")

    # load -> serialize -> load が不変。
    assert CompositionScore.model_validate(dumped).model_dump(mode="json") == dumped
    assert dumped["control_profile"] == _SUNO_PROFILE


def test_control_profile_sparse_map_is_accepted() -> None:
    """疎なプロファイル（bpm のみ）が受理される＝fixity の網羅必須を引き継がない。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = {"suno": {"bpm": {"grip_class": "tight"}}}

    score = CompositionScore.model_validate(data)

    assert set(score.control_profile["suno"]) == {"bpm"}
    grip = score.control_profile["suno"]["bpm"]
    assert grip.grip_class == "tight"
    # 未指定の optional は serialize から除外される（空なら非 serialize）。
    assert grip.model_dump(mode="json") == {"grip_class": "tight"}


def test_control_profile_unknown_field_is_rejected() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = {"suno": {"tempo": {"grip_class": "tight"}}}

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_control_profile_invalid_grip_class_is_rejected() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = {"suno": {"bpm": {"grip_class": "strong"}}}

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_control_profile_unknown_grip_subkey_is_rejected() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = {
        "suno": {"bpm": {"grip_class": "tight", "confidence": 0.9}}
    }

    with pytest.raises(ValidationError):
        CompositionScore.model_validate(data)


def test_control_profile_absent_is_not_serialized() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)

    score = CompositionScore.model_validate(data)

    assert score.control_profile is None
    assert "control_profile" not in score.model_dump(mode="json")


def test_control_profile_allows_multiple_generators() -> None:
    """生成器キー駆動（同一楽譜が複数生成器プロファイルを持てる）。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = {
        "suno": {"bpm": {"grip_class": "tight"}},
        "musicgen": {"brightness": {"grip_class": "loose"}},
    }

    score = CompositionScore.model_validate(data)

    assert set(score.control_profile) == {"suno", "musicgen"}
    assert score.control_profile["musicgen"]["brightness"].grip_class == "loose"
