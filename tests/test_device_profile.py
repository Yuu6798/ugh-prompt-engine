"""tests/test_device_profile.py — generator device profile (PR3 後半)。

`compose/device_profile.py` のスキーマ検証 / config フォールバック、および
`ExternalPromptAdapter` への 2 経路配線（control_defaults merge / advisories 発火）を
検証する。プロンプト本文・tags が advisory によって変わらないことも固定する。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from svp_rpe.compose import CompositionScore, ExternalPromptAdapter, load_composition_score
from svp_rpe.compose.device_profile import (
    CrossCoupling,
    DeviceProfile,
    KnobQuirk,
    SpectralBias,
    load_device_profile,
)

SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")


# --- schema --------------------------------------------------------------


def test_load_device_profile_suno_schema() -> None:
    profile = load_device_profile("suno")

    assert profile is not None
    assert profile.schema_version == "1.0"
    assert profile.generator == "suno"
    assert set(profile.control_defaults) == {"bpm", "brightness", "lyrics_presence"}
    assert profile.control_defaults["bpm"].grip_class == "tight"
    assert profile.control_defaults["brightness"].grip_class == "tight"
    assert profile.control_defaults["lyrics_presence"].grip_class == "loose"
    assert len(profile.knob_quirks) == 3
    assert len(profile.cross_couplings) == 6
    assert len(profile.spectral_biases) == 4
    assert profile.notes is not None


def test_load_device_profile_missing_generator_returns_none() -> None:
    assert load_device_profile("nonexistent_generator_xyz") is None


def test_device_profile_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValidationError):
        DeviceProfile.model_validate(
            {
                "schema_version": "1.0",
                "generator": "suno",
                "unknown_key": "boom",
            }
        )


def test_device_profile_control_defaults_rejects_unknown_field_key() -> None:
    with pytest.raises(ValidationError):
        DeviceProfile.model_validate(
            {
                "schema_version": "1.0",
                "generator": "suno",
                "control_defaults": {
                    "not_a_real_field": {"grip_class": "tight"},
                },
            }
        )


def test_knob_quirk_and_cross_coupling_and_spectral_bias_construct() -> None:
    quirk = KnobQuirk(
        field="bpm",
        description="d",
        status="observed",
        evidence="e",
        applies_below=100,
        advisory="warn",
    )
    assert quirk.applies_to_values == []
    assert quirk.advisory == "warn"

    coupling = CrossCoupling(
        knob="bpm", sensor="spectral_centroid", effect=2.33, status="unresolved", evidence="e"
    )
    assert coupling.effect == pytest.approx(2.33)

    bias = SpectralBias(
        name="over_brightening", description="d", direction="up", status="directional",
        evidence="e",
    )
    assert bias.status == "directional"


# --- config sync (packaged copy) ------------------------------------------


@pytest.mark.parametrize("profile_name", ["suno.yaml", "musicgen.yaml"])
def test_device_profiles_packaged_copy_matches_repo_copy(profile_name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    repo_copy = (root / "config" / "device_profiles" / profile_name).read_text(encoding="utf-8")
    packaged_copy = (
        root / "src" / "svp_rpe" / "config" / "device_profiles" / profile_name
    ).read_text(encoding="utf-8")
    assert repo_copy == packaged_copy


# --- merge: control_defaults completes control_profile --------------------


def _score_without_control_profile() -> CompositionScore:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    return CompositionScore.model_validate(data)


def test_unprofiled_score_uses_device_defaults_for_tight_tier() -> None:
    """control_profile が無くても suno device defaults の bpm/brightness が tight tier に
    昇格し、アグレッシブな truncation でも最後まで残る。"""

    score = _score_without_control_profile()

    prompt = ExternalPromptAdapter().render(score, max_chars=12)

    assert "128 BPM." in prompt.text
    assert "bpm" not in prompt.dropped_elements


def test_score_declared_control_profile_wins_over_device_defaults() -> None:
    """score が明示的に dead を宣言したら、device defaults の tight より score が勝つ。"""

    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = {"suno": {"brightness": {"grip_class": "dead"}}}
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    assert prompt.dropped_elements[0] == "brightness"
    assert "Brightness dark." not in prompt.text


def test_unknown_backend_has_no_device_profile_and_falls_back_as_before() -> None:
    """未知 backend（device profile 無し）は従来どおり rendering.priority フォールバック。"""

    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["rendering"]["target_backend"] = "udio"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    assert prompt.dropped_elements[:3] == [
        "valley_depth_target",
        "active_rate_target",
        "stereo_width",
    ]
    assert prompt.advisories == []


# --- advisories -------------------------------------------------------------


def test_dark_brightness_fires_advisory() -> None:
    score = load_composition_score(SAMPLE_PATH)  # brightness: dark
    assert score.physical.brightness == "dark"

    prompt = ExternalPromptAdapter().render(score)

    assert any("dark" in advisory for advisory in prompt.advisories)


def test_bright_brightness_does_not_fire_dark_advisory() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["brightness"] = "bright"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score)

    assert prompt.advisories == []


def test_low_bpm_fires_advisory_and_high_bpm_does_not() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = 90
    data["physical"]["brightness"] = "bright"  # isolate the bpm quirk
    low_score = CompositionScore.model_validate(data)

    low_prompt = ExternalPromptAdapter().render(low_score)
    assert any("低 bpm" in advisory or "prior" in advisory for advisory in low_prompt.advisories)

    data["physical"]["bpm"] = 128
    high_score = CompositionScore.model_validate(data)
    high_prompt = ExternalPromptAdapter().render(high_score)
    assert high_prompt.advisories == []


def test_todo_sentinel_bpm_is_skipped_by_numeric_quirk() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = "TODO(transcribe): bpm undetected"
    data["physical"]["brightness"] = "bright"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score)

    assert prompt.advisories == []


def test_lyrics_presence_none_is_not_advisory_target() -> None:
    score = load_composition_score(SAMPLE_PATH)
    assert score.semantic.lyrics_presence is None

    prompt = ExternalPromptAdapter().render(score)

    # lyrics_presence には advisory が定義されていないため、値の有無にかかわらず
    # advisories に lyrics 関連の文言が含まれないことだけを確認する。
    assert not any("lyrics" in advisory.lower() for advisory in prompt.advisories)


def test_advisories_do_not_alter_prompt_text_or_tags() -> None:
    """advisory が発火しても text / tags / negative_tags / dropped_elements は不変。"""

    score = load_composition_score(SAMPLE_PATH)
    baseline_data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    baseline_data.pop("control_profile", None)
    baseline_score = CompositionScore.model_validate(baseline_data)

    with_profile = ExternalPromptAdapter().render(score)
    without_profile = ExternalPromptAdapter().render(baseline_score)

    assert with_profile.advisories != []
    assert with_profile.text == without_profile.text
    assert with_profile.tags == without_profile.tags
    assert with_profile.negative_tags == without_profile.negative_tags
    assert with_profile.dropped_elements == without_profile.dropped_elements


def test_cross_couplings_never_produce_advisories() -> None:
    """cross_couplings は記録専用で advisory を出さない（unresolved のため）。"""

    profile = load_device_profile("suno")
    assert profile is not None
    assert all(coupling.status == "unresolved" for coupling in profile.cross_couplings)

    # knob_quirks に含まれない cross_coupling 由来の knob 名では advisory が発火しない
    # ことを、既存 quirk と重ならない値（brightness=bright, bpm=128）で確認する。
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["physical"]["brightness"] = "bright"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score)

    assert prompt.advisories == []


def test_generated_prompt_advisories_default_empty_list() -> None:
    from svp_rpe.compose import GeneratedPrompt

    prompt = GeneratedPrompt(
        backend="external", text="t", tags=[], negative_tags=[], dropped_elements=[]
    )
    assert prompt.advisories == []


def test_midnight_signal_json_fixture_includes_advisories() -> None:
    generated_json_path = Path("examples/composition/midnight_signal/generated_prompt.json")
    score = load_composition_score(SAMPLE_PATH)

    prompt = ExternalPromptAdapter().render(score)

    assert json.loads(generated_json_path.read_text(encoding="utf-8")) == prompt.model_dump(
        mode="json"
    )
