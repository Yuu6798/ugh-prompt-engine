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


def test_load_device_profile_musicgen_schema() -> None:
    """K2-seg（2026-07-05）: 5 欄追記後の musicgen device profile スキーマ固定。"""
    profile = load_device_profile("musicgen")

    assert profile is not None
    assert profile.schema_version == "1.0"
    assert profile.generator == "musicgen"
    assert set(profile.control_defaults) == {
        "bpm",
        "brightness",
        "active_rate_target",
        "valley_depth_target",
        "semantic.avoid",
        "semantic.core",
        "time_signature",
    }
    assert profile.control_defaults["active_rate_target"].grip_class == "loose"
    assert profile.control_defaults["valley_depth_target"].grip_class == "dead"
    assert profile.control_defaults["semantic.avoid"].grip_class == "dead"
    # semantic_avoid の d=+1.10 は符号逆（Avoid が意図と逆方向に効く）のため grip キーには
    # 入れない honesty ルール（quirk 側にのみ記録）。
    assert profile.control_defaults["semantic.avoid"].grip is None
    assert profile.control_defaults["semantic.core"].grip_class == "loose"
    assert profile.control_defaults["semantic.core"].sensor == "clap:energy"
    assert profile.control_defaults["time_signature"].grip_class == "dead"
    assert len(profile.knob_quirks) == 6
    assert len(profile.cross_couplings) == 0
    assert len(profile.spectral_biases) == 0


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


def test_musicgen_k2_seg_defaults_demote_time_signature_and_semantic_core() -> None:
    """K2-seg（2026-07-05）: musicgen device defaults に `time_signature`（dead）/
    `semantic.core`（loose）が加わったことで、この 2 セグメントは
    unprofiled fallback tier から advisory tier（loose/dead 同待遇）へ格下げされる。

    サンプル score・max_chars=180 では、この 2 セグメントは K2-seg 追記**前**は
    truncation を生き残っていたが（fallback tier で priority 順が有利だった）、
    追記**後**は真っ先に落ちる側へ回る — dead/loose 追加が実際に drop 順を
    変える（意図どおりの挙動変化、docs/musicgen_backend.md §7.6 参照）。
    """
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["rendering"]["target_backend"] = "musicgen"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    assert "time_signature" in prompt.dropped_elements
    assert "semantic.core" in prompt.dropped_elements
    assert "4/4 time." not in prompt.text
    assert "atmosphere." not in prompt.text
    # tight な brightness は K2-seg 追記後も不変で先頭へ昇格し続ける。
    assert "Brightness dark." in prompt.text
    assert "brightness" not in prompt.dropped_elements


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


def test_musicgen_nonempty_avoid_fires_attractor_advisory() -> None:
    """musicgen backend + 非空 `semantic.avoid` は Avoid=attractor 警告を発火する。

    Codex P2 指摘（#152）: `semantic.avoid` quirk は制約（`applies_to_values` /
    数値閾値）を持たないため従来の `_quirk_matches` は常に不成立、加えて
    `_field_value_for_quirk` の `getattr(score.semantic, "semantic.avoid", None)`
    もドット付きフィールド名を解決できず None 固定だった＝二重の理由で一度も
    発火しなかった。両方を fix したことをここで pin する。
    """

    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["rendering"]["target_backend"] = "musicgen"
    assert data["semantic"]["avoid"]  # サンプルは非空 avoid を持つ前提
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score)

    assert any("引き寄せる" in advisory for advisory in prompt.advisories)
    assert any("Avoid" in advisory for advisory in prompt.advisories)
    # 既存契約: advisory の発火は本文 / tags / negative_tags を変えない（自動補正しない）。
    # text 側の "Avoid: ..." セグメントは semantic.avoid 自体の描画（既存契約・不変）で、
    # advisories フィールドとは独立に計算される。
    assert "Avoid: bright festival EDM; comic vocal delivery." in prompt.text
    assert prompt.negative_tags == data["semantic"]["avoid"]
    assert prompt.tags == ["deep_house", "ambient", "dark", "wide_stereo"]


def test_musicgen_empty_avoid_does_not_fire_attractor_advisory() -> None:
    """`semantic.avoid` が空なら musicgen の Avoid=attractor 警告は発火しない。"""

    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["semantic"]["avoid"] = []
    data["rendering"]["target_backend"] = "musicgen"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score)

    assert not any("引き寄せる" in advisory for advisory in prompt.advisories)
    assert "Avoid:" not in prompt.text
    assert prompt.negative_tags == []


def test_musicgen_avoid_advisory_fix_does_not_alter_suno_bpm_brightness_advisories() -> None:
    """suno backend の既存 advisory 挙動（bpm applies_below / brightness
    applies_to_values）は今回の quirk-matching 拡張で不変（回帰ゼロ）。"""

    score = load_composition_score(SAMPLE_PATH)  # target_backend: external -> suno
    prompt = ExternalPromptAdapter().render(score)

    assert any("dark" in advisory for advisory in prompt.advisories)

    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["physical"]["bpm"] = 90
    data["physical"]["brightness"] = "bright"
    low_bpm_score = CompositionScore.model_validate(data)
    low_bpm_prompt = ExternalPromptAdapter().render(low_bpm_score)
    assert any(
        "低 bpm" in advisory or "prior" in advisory for advisory in low_bpm_prompt.advisories
    )


def test_advisory_null_constraintless_quirk_never_fires() -> None:
    """advisory が null の制約なし quirk（例: musicgen `time_signature`）は、解決値が
    非空でも発火しない（advisory 非 null が新分岐の前提条件であることを固定）。"""

    profile = load_device_profile("musicgen")
    assert profile is not None
    time_signature_quirk = next(q for q in profile.knob_quirks if q.field == "time_signature")
    assert time_signature_quirk.advisory is None
    assert not time_signature_quirk.applies_to_values
    assert time_signature_quirk.applies_below is None
    assert time_signature_quirk.applies_above is None

    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["rendering"]["target_backend"] = "musicgen"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score)

    assert not any(
        "4/4" in advisory or "拍子" in advisory or "抽出器の 4/4 バイアス" in advisory
        for advisory in prompt.advisories
    )


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
