"""Composition Score prompt renderer and CLI tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose import (
    BackendDescriptor,
    CompositionScore,
    ExternalPromptAdapter,
    GeneratedPrompt,
    load_composition_score,
    resolve_backend_descriptor,
)


SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")
GENERATED_TEXT_PATH = Path("examples/composition/midnight_signal/generated_prompt.txt")
GENERATED_JSON_PATH = Path("examples/composition/midnight_signal/generated_prompt.json")


def test_external_prompt_adapter_renders_score_layers_in_order() -> None:
    score = load_composition_score(SAMPLE_PATH)

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert prompt.backend == "external"
    assert prompt.tags == ["deep_house", "ambient", "dark", "wide_stereo"]
    assert prompt.negative_tags == ["bright festival EDM", "comic vocal delivery"]
    assert prompt.dropped_elements == []
    assert "Introspective night drive atmosphere." in prompt.text
    assert "deep_house / ambient track." in prompt.text
    assert "128 BPM." in prompt.text
    assert "C minor." in prompt.text
    # physical.optional 束はフィールド粒度の独立した文へ分解される。
    assert "Brightness dark." in prompt.text
    assert "Wide stereo." in prompt.text
    assert "Active rate 0.90-0.93." in prompt.text
    assert "Valley depth 0.15-0.25." in prompt.text
    assert "Avoid: bright festival EDM; comic vocal delivery." in prompt.text
    # tight な bpm / brightness は芯として先頭へ昇格する。
    assert prompt.text.startswith("128 BPM. Brightness dark.")
    assert prompt.text.index("intro:") < prompt.text.index("verse:")
    assert prompt.text.index("verse:") < prompt.text.index("chorus:")
    assert prompt.text.index("chorus:") < prompt.text.index("bridge:")


def test_external_prompt_adapter_compresses_low_priority_segments_first() -> None:
    score = load_composition_score(SAMPLE_PATH)

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    assert len(prompt.text) <= 180
    # 未プロファイルの advisory 物理フィールドが priority 順（低優先＝高 index）で先に落ちる。
    assert prompt.dropped_elements[:3] == [
        "valley_depth_target",
        "active_rate_target",
        "stereo_width",
    ]
    # tight な bpm / brightness は最後まで残る。
    assert "bpm" not in prompt.dropped_elements
    assert "brightness" not in prompt.dropped_elements
    assert "128 BPM." in prompt.text
    assert "Brightness dark." in prompt.text
    assert "bright festival EDM" not in prompt.text
    assert "Active rate" not in prompt.text
    assert prompt.tags == ["deep_house", "ambient", "dark", "wide_stereo"]
    assert prompt.negative_tags == ["bright festival EDM", "comic vocal delivery"]


def test_external_prompt_adapter_is_deterministic() -> None:
    score = load_composition_score(SAMPLE_PATH)
    adapter = ExternalPromptAdapter()

    first = adapter.render(score, max_chars=180)
    second = adapter.render(score, max_chars=180)

    assert first == second
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_external_prompt_adapter_does_not_render_todo_bpm_as_numeric_tempo() -> None:
    score = load_composition_score(SAMPLE_PATH)
    score = score.model_copy(
        update={
            "physical": score.physical.model_copy(
                update={"bpm": "TODO(transcribe): bpm undetected"}
            )
        }
    )

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert "TODO(transcribe): bpm undetected." in prompt.text
    assert "TODO(transcribe): bpm undetected BPM." not in prompt.text


def test_external_prompt_adapter_renders_numeric_bpm_string_as_tempo() -> None:
    data = json.loads(load_composition_score(SAMPLE_PATH).model_dump_json())
    data["physical"]["bpm"] = "128"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert score.physical.bpm == 128
    assert "128 BPM." in prompt.text
    assert "128." not in prompt.text


def test_generated_prompt_rejects_legacy_char_count_field() -> None:
    with pytest.raises(ValidationError):
        GeneratedPrompt.model_validate(
            {
                "backend": "external",
                "text": "prompt",
                "tags": [],
                "negative_tags": [],
                "dropped_elements": [],
                "char_count": 6,
            }
        )


def test_compose_cli_outputs_text_by_default() -> None:
    result = CliRunner().invoke(app, ["compose", str(SAMPLE_PATH)])

    assert result.exit_code == 0
    assert result.stdout.startswith("128 BPM. Brightness dark.")
    assert "Brightness dark." in result.stdout
    assert "Wide stereo." in result.stdout
    assert "Avoid: bright festival EDM; comic vocal delivery." in result.stdout
    assert '"backend"' not in result.stdout
    # advisory はコピペ成果物である stdout を汚染してはならない。stderr にのみ出す。
    assert "Advisories" not in result.stdout
    assert "Advisories" in result.stderr


def test_compose_cli_outputs_json_and_max_chars_override() -> None:
    result = CliRunner().invoke(
        app,
        ["compose", str(SAMPLE_PATH), "--format", "json", "--max-chars", "180"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["backend"] == "external"
    assert len(payload["text"]) <= 180
    assert payload["dropped_elements"][:3] == [
        "valley_depth_target",
        "active_rate_target",
        "stereo_width",
    ]
    assert payload["tags"] == ["deep_house", "ambient", "dark", "wide_stereo"]
    assert payload["negative_tags"] == ["bright festival EDM", "comic vocal delivery"]
    # JSON モードは構造化データなので advisories はフィールドとして保持したまま。
    assert payload["advisories"] != []
    assert result.stderr == ""


def test_compose_cli_writes_output_file(tmp_path: Path) -> None:
    output_path = tmp_path / "generated_prompt.json"

    result = CliRunner().invoke(
        app,
        ["compose", str(SAMPLE_PATH), "--format", "json", "-o", str(output_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["backend"] == "external"
    assert payload["text"].startswith("128 BPM. Brightness dark.")
    assert "Composition prompt saved" in result.stdout


def test_compose_cli_text_output_file_excludes_advisories(tmp_path: Path) -> None:
    """text 出力の -o ファイルはコピペ成果物なので advisories を含んではならない。"""
    output_path = tmp_path / "generated_prompt.txt"

    result = CliRunner().invoke(
        app,
        ["compose", str(SAMPLE_PATH), "--format", "text", "-o", str(output_path)],
    )

    assert result.exit_code == 0
    file_content = output_path.read_text(encoding="utf-8")
    assert file_content.startswith("128 BPM. Brightness dark.")
    assert "Advisories" not in file_content
    assert "Advisories" in result.stderr


def test_generated_prompt_examples_match_renderer() -> None:
    prompt = ExternalPromptAdapter().render(load_composition_score(SAMPLE_PATH))

    assert GENERATED_TEXT_PATH.read_text(encoding="utf-8") == prompt.text
    assert json.loads(GENERATED_JSON_PATH.read_text(encoding="utf-8")) == prompt.model_dump(
        mode="json"
    )


# --- PR1.5: control_profile-aware compile ------------------------------------


def _score_with_profile(profile: dict[str, dict[str, dict[str, object]]]) -> CompositionScore:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = profile
    return CompositionScore.model_validate(data)


def test_backend_selector_resolves_external_to_suno() -> None:
    """`target_backend: external` が control_profile.suno を決定論的に引く。"""
    assert resolve_backend_descriptor("external") == BackendDescriptor(profile_key="suno")
    assert resolve_backend_descriptor("suno").profile_key == "suno"
    # 未知 backend は素の descriptor（未プロファイル render として正当に通る）。
    assert resolve_backend_descriptor("udio").profile_key == "udio"


def test_external_backend_uses_suno_profile_for_grip() -> None:
    """external Score が suno プロファイルの grip_class でコンパイルされる。"""
    score = load_composition_score(SAMPLE_PATH)  # target_backend: external
    assert score.rendering.target_backend == "external"

    prompt = ExternalPromptAdapter().render(score, max_chars=120)

    # suno で tight の bpm/brightness が芯として残る。
    assert "128 BPM." in prompt.text
    assert "Brightness dark." in prompt.text
    assert "bpm" not in prompt.dropped_elements
    assert "brightness" not in prompt.dropped_elements


def test_dead_field_drops_before_advisory_and_tight() -> None:
    """dead 宣言フィールドが真っ先に落ちる＝control_profile が drop 順を駆動する。"""
    score = _score_with_profile(
        {
            "suno": {
                "bpm": {"grip_class": "tight"},
                "brightness": {"grip_class": "dead"},
            }
        }
    )

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    assert prompt.dropped_elements[0] == "brightness"
    assert "Brightness dark." not in prompt.text
    # tight な bpm は残る。
    assert "128 BPM." in prompt.text
    assert "bpm" not in prompt.dropped_elements


def test_tight_field_survives_aggressive_truncation() -> None:
    """tight フィールドは max_chars を強く絞っても最後まで残る（dead/loose が先）。"""
    score = _score_with_profile({"suno": {"bpm": {"grip_class": "tight"}}})

    prompt = ExternalPromptAdapter().render(score, max_chars=12)

    assert "128 BPM." in prompt.text
    assert "bpm" not in prompt.dropped_elements


def test_control_profile_drives_per_generator_divergence() -> None:
    """生成器キーで同一楽譜が別プロンプトへ分岐する（スキーマが生成器キー駆動）。"""
    profile = {
        "suno": {"brightness": {"grip_class": "tight"}},
        "musicgen": {"brightness": {"grip_class": "dead"}},
    }
    suno_score = _score_with_profile(profile)
    musicgen_data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    musicgen_data["control_profile"] = profile
    musicgen_data["rendering"]["target_backend"] = "musicgen"
    musicgen_score = CompositionScore.model_validate(musicgen_data)

    suno_prompt = ExternalPromptAdapter().render(suno_score, max_chars=180)
    musicgen_prompt = ExternalPromptAdapter().render(musicgen_score, max_chars=180)

    # suno は brightness を tight で守り、musicgen は dead で落とす。
    # K2-seg（2026-07-05）で musicgen device defaults に active_rate_target /
    # valley_depth_target が loose/dead として加わったため、それらも同じ advisory
    # tier で brightness と drop 順を争うようになった（dropped_elements[0] が
    # brightness と断定できなくなった＝意図どおりの挙動変化、docs/musicgen_backend.md
    # §7.6）。brightness が drop される事実自体は不変なので `in` で検証する。
    assert "Brightness dark." in suno_prompt.text
    assert "brightness" in musicgen_prompt.dropped_elements
    assert "Brightness dark." not in musicgen_prompt.text
    assert suno_prompt.text != musicgen_prompt.text
    # 構造化 backend フィールドが選択 backend を反映する（誤ラベルしない）。
    assert suno_prompt.backend == "external"
    assert musicgen_prompt.backend == "musicgen"


def test_unprofiled_score_falls_back_to_priority_order() -> None:
    """control_profile 不在でも分割は効き、drop 順は rendering.priority に従う。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    # priority 末尾 physical.optional が field 展開され、低優先フィールドから落ちる。
    assert prompt.dropped_elements[:3] == [
        "valley_depth_target",
        "active_rate_target",
        "stereo_width",
    ]
    # フィールド粒度の独立文へ分割されている。
    assert "Valley depth" not in prompt.text


def test_semantic_core_preserves_intentional_casing() -> None:
    """acronym/固有名の casing を保つ（Codex P2: .capitalize() 退行の修正）。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["semantic"]["core"] = "AI EDM energy"
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert "AI EDM energy atmosphere." in prompt.text
    assert "Ai edm" not in prompt.text


def test_tight_time_signature_is_rendered_and_preserved() -> None:
    """control_profile が tight 宣言した time_signature が描画され保持される
    （Codex P2: 宣言したのに honor されない穴の修正）。"""
    score = _score_with_profile(
        {
            "suno": {
                "bpm": {"grip_class": "tight"},
                "time_signature": {"grip_class": "tight"},
            }
        }
    )

    full = ExternalPromptAdapter().render(score, max_chars=1000)
    assert "4/4 time." in full.text

    squeezed = ExternalPromptAdapter().render(score, max_chars=40)
    assert "4/4 time." in squeezed.text
    assert "time_signature" not in squeezed.dropped_elements


def test_compile_is_deterministic_with_control_profile() -> None:
    score = load_composition_score(SAMPLE_PATH)
    adapter = ExternalPromptAdapter()

    first = adapter.render(score, max_chars=200)
    second = adapter.render(score, max_chars=200)

    assert first == second


# --- SEM-1: lyrics_presence semantic control channel -------------------------


def _score_with_lyrics_presence(value: str) -> CompositionScore:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["semantic"]["lyrics_presence"] = value
    return CompositionScore.model_validate(data)


def test_lyrics_presence_absent_renders_instrumental_segment_and_tag() -> None:
    score = _score_with_lyrics_presence("absent")

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert "Instrumental, no vocals." in prompt.text
    assert prompt.tags == ["deep_house", "ambient", "dark", "wide_stereo", "instrumental"]


def test_lyrics_presence_present_renders_vocals_segment_without_instrumental_tag() -> None:
    score = _score_with_lyrics_presence("present")

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert "With vocals." in prompt.text
    assert "instrumental" not in prompt.tags


def test_lyrics_presence_segment_is_inserted_after_valley_depth_in_source_order() -> None:
    """`_segments_for` の挿入順（valley_depth_target の直後・semantic.avoid の直前）を
    tie-break（同 tier・同 priority_index 時の `order`）で確認する。tier/priority が
    異なる場合は rank が優先するため、最終描画順は tier テストで別途検証する。"""
    from svp_rpe.compose.prompt_renderer import _segments_for

    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["semantic"]["lyrics_presence"] = "absent"
    data.pop("control_profile", None)
    score = CompositionScore.model_validate(data)

    tokens = [segment.token for segment in _segments_for(score)]
    valley_index = tokens.index("valley_depth_target")
    avoid_index = tokens.index("semantic.avoid")
    lyrics_index = tokens.index("lyrics_presence")

    assert valley_index < lyrics_index < avoid_index


def test_lyrics_presence_unset_renders_no_segment_and_no_tag() -> None:
    score = load_composition_score(SAMPLE_PATH)
    assert score.semantic.lyrics_presence is None

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert "vocals" not in prompt.text.lower()
    assert "instrumental" not in prompt.tags


def test_lyrics_presence_loose_profile_drops_before_advisory_fallback() -> None:
    """loose 宣言の lyrics_presence は真っ先の削減候補（advisory tier）へ回る。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["semantic"]["lyrics_presence"] = "absent"
    data["control_profile"] = {"suno": {"lyrics_presence": {"grip_class": "loose"}}}
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=200)

    assert prompt.dropped_elements[0] == "lyrics_presence"
    assert "Instrumental, no vocals." not in prompt.text


def test_lyrics_presence_tight_profile_survives_aggressive_truncation() -> None:
    """tight 宣言の lyrics_presence は max_chars を強く絞っても最後まで残る。

    PR3 後半（device_profile）以降 suno backend は bpm/brightness の device
    control_defaults（tight）を持つため、lyrics_presence を単独の tight フィールドとして
    分離検証するには両方を明示的に score 側で dead 宣言する（score が device defaults に
    常に勝つことも同時に確認する）。
    """
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["semantic"]["lyrics_presence"] = "absent"
    data["control_profile"] = {
        "suno": {
            "bpm": {"grip_class": "dead"},
            "brightness": {"grip_class": "dead"},
            "lyrics_presence": {"grip_class": "tight"},
        }
    }
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=30)

    assert "Instrumental, no vocals." in prompt.text
    assert "lyrics_presence" not in prompt.dropped_elements


def _priority_with_lyrics_token_first(priority_token: str) -> list[str]:
    return [
        priority_token,
        "semantic.core",
        "semantic.grv",
        "physical.bpm",
        "physical.key",
        "physical.time_signature",
        "structure",
        "semantic.avoid",
        "physical.optional",
    ]


def test_lyrics_presence_dotted_priority_token_survives_truncation_unprofiled() -> None:
    """Codex P2 fix: `semantic.lyrics_presence` in `rendering.priority` was a silent
    no-op (only physical dotted tokens were aliased). It must now normalize via
    `_PRIORITY_ALIAS` so listing it early actually protects the segment under
    truncation, on the unprofiled path (no control_profile entry for the backend).

    PR3 後半（device_profile）以降 `suno`（`external` の解決先）は device
    control_defaults を持つため、"unprofiled" を再現するには device profile が
    存在しない backend へ切り替える（`bpm`/`brightness` を device 既定で
    勝手に tight 昇格させず、`rendering.priority` フォールバックだけを見る）。
    MusicGen PR B で `musicgen` も profiled になったため、profile を持たない
    `udio` を使う（未知 backend は profile_key=backend 名の素の descriptor に
    フォールバックする — `resolve_backend_descriptor` 参照）。
    """
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["semantic"]["lyrics_presence"] = "absent"
    data["rendering"]["target_backend"] = "udio"
    data["rendering"]["priority"] = _priority_with_lyrics_token_first("semantic.lyrics_presence")
    score = CompositionScore.model_validate(data)

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    assert "Instrumental, no vocals." in prompt.text
    assert "lyrics_presence" not in prompt.dropped_elements
    # a later-priority segment is dropped instead under the tight truncation.
    assert "valley_depth_target" in prompt.dropped_elements


# --- K2-seg 後始末 (#152 musicgen フォローアップ, #162 suno フォローアップ):
# musicgen / suno semantic.avoid body routing -----------------------------------


def test_backend_descriptor_omit_body_negative_flags() -> None:
    """`omit_body_negative` は musicgen / suno backend で True（K2-seg 実測が両方に
    対して確定した — musicgen: d=+1.10 #152 / suno: d=+4.03 #162）。`external` は
    Suno ルートのエイリアスだが実測は Suno 生成そのものへの実測であり汎用 external
    へ横展開はしない（#153 と同じ規律）ため False のまま。"""
    assert resolve_backend_descriptor("musicgen").omit_body_negative is True
    assert resolve_backend_descriptor("suno").omit_body_negative is True
    assert resolve_backend_descriptor("external").omit_body_negative is False
    # 未知 backend は素の descriptor（デフォルト False）へフォールバックする。
    assert resolve_backend_descriptor("udio").omit_body_negative is False


@pytest.mark.parametrize("target_backend", ["musicgen", "suno"])
def test_omits_avoid_body_segment_but_keeps_negative_tags(target_backend: str) -> None:
    """K2-seg 実測（本文 Avoid=attractor、musicgen d=+1.10 #152 / suno d=+4.03 #162）
    に基づき、musicgen / suno backend は本文へ "Avoid: ..." を送出しない。
    `negative_tags`（楽譜の意図の記録）は不変。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["rendering"]["target_backend"] = target_backend
    assert data["semantic"]["avoid"]  # サンプルは非空 avoid を持つ前提

    score = CompositionScore.model_validate(data)
    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert "Avoid:" not in prompt.text
    assert prompt.negative_tags == data["semantic"]["avoid"]
    # 送出停止は「字数超過 drop」ではなくルーティングなので dropped_elements には
    # 現れない（drop accounting との混同を避ける — Design Memo 判断 4）。
    assert "semantic.avoid" not in prompt.dropped_elements


@pytest.mark.parametrize("target_backend", ["musicgen", "suno"])
def test_avoid_never_counted_as_dropped_under_aggressive_truncation(target_backend: str) -> None:
    """字数を強く絞っても musicgen / suno の semantic.avoid は候補にすら入らないため
    `dropped_elements` に現れない（他フィールドの drop 順位には無関係）。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    data["rendering"]["target_backend"] = target_backend

    score = CompositionScore.model_validate(data)
    prompt = ExternalPromptAdapter().render(score, max_chars=20)

    assert "Avoid:" not in prompt.text
    assert "semantic.avoid" not in prompt.dropped_elements
    assert prompt.negative_tags == data["semantic"]["avoid"]


def test_external_avoid_body_segment_is_unchanged() -> None:
    """external backend は不変（実測は Suno 生成そのものに対するものであり、汎用
    external へ横展開はしない — #153 と同じ規律）。本文の "Avoid: ..." セグメントは
    従来どおり描画される。"""
    score = load_composition_score(SAMPLE_PATH)  # target_backend: external (unchanged)
    assert score.rendering.target_backend == "external"

    prompt = ExternalPromptAdapter().render(score, max_chars=1000)

    assert "Avoid: bright festival EDM; comic vocal delivery." in prompt.text
    assert prompt.negative_tags == ["bright festival EDM", "comic vocal delivery"]


def test_suno_avoid_body_segment_is_now_omitted() -> None:
    """#162 判定（K2-seg Suno バッチ 1 実測 attractor d=+4.03、事前登録規約の
    attractor 確定閾値 d>=+0.8 該当）を受け、suno backend は本文 "Avoid: ..." を
    もう送出しない。`negative_tags`（Exclude チャネル相当の記録）は従来どおり保持。"""
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["rendering"]["target_backend"] = "suno"
    suno_score = CompositionScore.model_validate(data)
    suno_prompt = ExternalPromptAdapter().render(suno_score, max_chars=1000)

    assert "Avoid:" not in suno_prompt.text
    assert suno_prompt.negative_tags == ["bright festival EDM", "comic vocal delivery"]


def test_lyrics_presence_bare_and_dotted_priority_tokens_normalize_identically() -> None:
    """The bare `lyrics_presence` spelling must behave identically to the dotted
    `semantic.lyrics_presence` spelling — both normalize to the same field token."""

    def _score(priority_token: str) -> CompositionScore:
        data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
        data.pop("control_profile", None)
        data["semantic"]["lyrics_presence"] = "absent"
        data["rendering"]["priority"] = _priority_with_lyrics_token_first(priority_token)
        return CompositionScore.model_validate(data)

    dotted = ExternalPromptAdapter().render(_score("semantic.lyrics_presence"), max_chars=180)
    bare = ExternalPromptAdapter().render(_score("lyrics_presence"), max_chars=180)

    assert dotted.text == bare.text
    assert dotted.dropped_elements == bare.dropped_elements
