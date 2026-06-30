"""Score-adherence test (PR2): control_profile-tight 保証の準拠判定."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose import CompositionScore, load_composition_score
from svp_rpe.roundtrip import (
    ScoreAdherenceReport,
    render_score_adherence_text,
    run_score_adherence,
    score_adherence,
)
from svp_rpe.roundtrip.models import RoundtripField, RoundtripReport

SAMPLE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")


def _report(**field_diagnoses: str) -> RoundtripReport:
    """field -> diagnosis の dict から RoundtripReport を組む（音声不要）。"""
    fields = [
        RoundtripField(
            field=field,
            source_value="src",
            transcribed_value="obs",
            diagnosis=diagnosis,
            sensor=f"{field}_sensor",
            sensor_state="working",
        )
        for field, diagnosis in field_diagnoses.items()
    ]
    return RoundtripReport(source_id="midnight-signal", transcribed_id="draft", fields=fields)


def _score_with_profile(profile: dict) -> CompositionScore:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = profile
    return CompositionScore.model_validate(data)


def test_tight_fields_selected_from_control_profile() -> None:
    score = load_composition_score(SAMPLE_PATH)  # suno: bpm/brightness tight
    report = _report(bpm="preserved", brightness="preserved")

    adherence = score_adherence(score, report)

    assert isinstance(adherence, ScoreAdherenceReport)
    assert adherence.backend == "suno"
    assert [row.field for row in adherence.tight_fields] == ["bpm", "brightness"]


def test_preserved_reflects_roundtrip_diagnosis() -> None:
    score = load_composition_score(SAMPLE_PATH)
    report = _report(bpm="preserved", brightness="calibration_disagreement")

    adherence = score_adherence(score, report)

    by_field = {row.field: row for row in adherence.tight_fields}
    assert by_field["bpm"].preserved is True
    assert by_field["brightness"].preserved is False
    assert adherence.preserved_count == 1
    assert adherence.total_tight == 2


def test_tight_fields_kept_through_compile_at_normal_limit() -> None:
    """tight フィールドは妥当な max_chars でコンパイルに保持される（PR1.5 保証）。"""
    score = load_composition_score(SAMPLE_PATH)
    report = _report(bpm="preserved", brightness="preserved")

    adherence = score_adherence(score, report, prompt_max_chars=60)

    assert adherence.compiled_kept_count == adherence.total_tight == 2


def test_loose_and_dead_fields_are_excluded() -> None:
    score = _score_with_profile(
        {
            "suno": {
                "bpm": {"grip_class": "tight"},
                "brightness": {"grip_class": "loose"},
                "key": {"grip_class": "dead"},
            }
        }
    )
    report = _report(bpm="preserved", brightness="preserved", key="preserved")

    adherence = score_adherence(score, report)

    assert [row.field for row in adherence.tight_fields] == ["bpm"]


def test_no_control_profile_yields_no_tight_fields() -> None:
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data.pop("control_profile", None)
    score = CompositionScore.model_validate(data)

    adherence = score_adherence(score, _report(bpm="preserved"))

    assert adherence.total_tight == 0
    assert adherence.tight_fields == []


def test_backend_selector_picks_generator_specific_profile() -> None:
    profile = {
        "suno": {"bpm": {"grip_class": "tight"}},
        "musicgen": {"brightness": {"grip_class": "tight"}},
    }
    data = yaml.safe_load(SAMPLE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = profile
    data["rendering"]["target_backend"] = "musicgen"
    score = CompositionScore.model_validate(data)

    adherence = score_adherence(score, _report(bpm="preserved", brightness="preserved"))

    assert adherence.backend == "musicgen"
    assert [row.field for row in adherence.tight_fields] == ["brightness"]


def test_absent_roundtrip_field_is_noted() -> None:
    score = load_composition_score(SAMPLE_PATH)
    report = _report(bpm="preserved")  # brightness 欄なし

    adherence = score_adherence(score, report)

    brightness = next(row for row in adherence.tight_fields if row.field == "brightness")
    assert brightness.roundtrip_diagnosis is None
    assert brightness.preserved is False
    assert brightness.note == "field absent from roundtrip report"


def test_score_adherence_is_deterministic() -> None:
    score = load_composition_score(SAMPLE_PATH)
    report = _report(bpm="preserved", brightness="calibration_disagreement")

    assert score_adherence(score, report) == score_adherence(score, report)


def test_render_score_adherence_text_has_table() -> None:
    score = load_composition_score(SAMPLE_PATH)
    report = _report(bpm="preserved", brightness="calibration_disagreement")

    text = render_score_adherence_text(score_adherence(score, report))

    assert "# Score Adherence (suno) - midnight-signal" in text
    assert "preserved: 1/2" in text
    assert "| bpm |" in text
    assert "| brightness |" in text


@pytest.mark.slow
def test_run_score_adherence_on_example_is_deterministic() -> None:
    score = load_composition_score(SAMPLE_PATH)

    first = run_score_adherence(score)
    second = run_score_adherence(score)

    assert first == second
    assert {row.field for row in first.tight_fields} == {"bpm", "brightness"}
    # 構造の健全性（verdict ではなく計器）: コンパイル保持は tight 全件で成立。
    assert first.compiled_kept_count == first.total_tight == 2


@pytest.mark.slow
def test_score_adherence_cli_outputs_json(tmp_path: Path) -> None:
    output_path = tmp_path / "adherence.json"

    result = CliRunner().invoke(
        app,
        ["score-adherence", str(SAMPLE_PATH), "--format", "json", "-o", str(output_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["backend"] == "suno"
    assert payload["total_tight"] == 2
    assert {row["field"] for row in payload["tight_fields"]} == {"bpm", "brightness"}
