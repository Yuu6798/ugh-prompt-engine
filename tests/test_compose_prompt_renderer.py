"""Composition Score prompt renderer and CLI tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose import (
    CompositionScore,
    ExternalPromptAdapter,
    GeneratedPrompt,
    load_composition_score,
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
    assert "Dark, introspective night drive atmosphere." in prompt.text
    assert "deep_house / ambient track." in prompt.text
    assert "128 BPM." in prompt.text
    assert "C minor." in prompt.text
    assert "Brightness dark; wide stereo;" in prompt.text
    assert "Avoid: bright festival EDM; comic vocal delivery." in prompt.text
    assert prompt.text.index("intro:") < prompt.text.index("verse:")
    assert prompt.text.index("verse:") < prompt.text.index("chorus:")
    assert prompt.text.index("chorus:") < prompt.text.index("bridge:")


def test_external_prompt_adapter_compresses_low_priority_segments_first() -> None:
    score = load_composition_score(SAMPLE_PATH)

    prompt = ExternalPromptAdapter().render(score, max_chars=180)

    assert len(prompt.text) <= 180
    assert prompt.dropped_elements[:2] == ["physical.optional", "semantic.avoid"]
    assert "bright festival EDM" not in prompt.text
    assert "active rate" not in prompt.text
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
    assert result.output.startswith("Dark, introspective night drive atmosphere.")
    assert "128 BPM." in result.output
    assert "Brightness dark; wide stereo;" in result.output
    assert "Avoid: bright festival EDM; comic vocal delivery." in result.output
    assert '"backend"' not in result.output


def test_compose_cli_outputs_json_and_max_chars_override() -> None:
    result = CliRunner().invoke(
        app,
        ["compose", str(SAMPLE_PATH), "--format", "json", "--max-chars", "180"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["backend"] == "external"
    assert len(payload["text"]) <= 180
    assert payload["dropped_elements"][:2] == ["physical.optional", "semantic.avoid"]
    assert payload["tags"] == ["deep_house", "ambient", "dark", "wide_stereo"]
    assert payload["negative_tags"] == ["bright festival EDM", "comic vocal delivery"]


def test_compose_cli_writes_output_file(tmp_path: Path) -> None:
    output_path = tmp_path / "generated_prompt.json"

    result = CliRunner().invoke(
        app,
        ["compose", str(SAMPLE_PATH), "--format", "json", "-o", str(output_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["backend"] == "external"
    assert payload["text"].startswith("Dark, introspective night drive atmosphere.")
    assert "Composition prompt saved" in result.output


def test_generated_prompt_examples_match_renderer() -> None:
    prompt = ExternalPromptAdapter().render(load_composition_score(SAMPLE_PATH))

    assert GENERATED_TEXT_PATH.read_text(encoding="utf-8") == prompt.text
    assert json.loads(GENERATED_JSON_PATH.read_text(encoding="utf-8")) == prompt.model_dump(
        mode="json"
    )
