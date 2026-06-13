"""Tests for draft CompositionScore transcription."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose import load_composition_score
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.rpe.models import (
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SpectralProfile,
    StereoProfile,
)
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.semantic_ci.audit import RANGE_PATTERN, _parse_numeric_range
from svp_rpe.transcribe import (
    TODO_AUTHOR_INPUT,
    TODO_BPM_UNDETECTED,
    TODO_BRIGHTNESS_NEUTRAL,
    TODO_KEY_UNDETECTED,
    TODO_SENTINEL_PREFIX,
    TODO_STEREO_BAND_UNDEFINED,
    TODO_STEREO_UNMEASURED,
    TODO_TIME_SIGNATURE_UNDETECTED,
    draft_score,
    render_draft_score_yaml,
)
from svp_rpe.transcribe.score_draft import _bars_for_section, _format_fixed_range

runner = CliRunner()

SYNTH_AUDIO = (
    Path("examples/sample_input/synth_01_slow_pad_c_major.wav"),
    Path("examples/sample_input/synth_02_minor_pulse_a_minor.wav"),
    Path("examples/sample_input/synth_03_mid_groove_g_major.wav"),
    Path("examples/sample_input/synth_04_waltz_fsharp_minor.wav"),
    Path("examples/sample_input/synth_05_fast_bright_d_major.wav"),
)
SYNTH_DRAFT_SNAPSHOTS = (
    (
        Path("examples/sample_input/synth_01_slow_pad_c_major.wav"),
        Path("examples/transcribe/synth_01_slow_pad_c_major_draft_score.yaml"),
    ),
    (
        Path("examples/sample_input/synth_04_waltz_fsharp_minor.wav"),
        Path("examples/transcribe/synth_04_waltz_fsharp_minor_draft_score.yaml"),
    ),
)

_BUNDLE_CACHE: dict[Path, RPEBundle] = {}


def test_fixed_range_format_round_trips_through_audit_parser() -> None:
    assert _format_fixed_range(0.9837) == "0.96-1.00"
    assert _parse_numeric_range(_format_fixed_range(0.9837)) == (0.96, 1.0)
    assert _format_fixed_range(0.0) == "0.00-0.02"
    assert _format_fixed_range(1.0) == "0.98-1.00"
    assert RANGE_PATTERN.match(_format_fixed_range(0.42)) is not None


def test_todo_sentinel_constants_are_pinned() -> None:
    assert TODO_SENTINEL_PREFIX == "TODO(transcribe):"
    assert TODO_AUTHOR_INPUT == "TODO(transcribe): author input required"
    assert TODO_BPM_UNDETECTED == "TODO(transcribe): bpm undetected"
    assert TODO_BRIGHTNESS_NEUTRAL == "TODO(transcribe): brightness neutral band"
    assert TODO_KEY_UNDETECTED == "TODO(transcribe): key undetected"
    assert TODO_STEREO_BAND_UNDEFINED == "TODO(transcribe): stereo band undefined"
    assert TODO_STEREO_UNMEASURED == "TODO(transcribe): stereo unmeasured"
    assert TODO_TIME_SIGNATURE_UNDETECTED == "TODO(transcribe): time signature undetected"


def test_bars_for_section_uses_time_signature_numerator() -> None:
    marker = SectionMarker(label="fixture", start_sec=0.0, end_sec=12.0)

    assert _bars_for_section(marker, bpm=60, time_signature="3/4") == 4
    assert _bars_for_section(marker, bpm=60, time_signature="4/4") == 3


def test_all_synth_drafts_are_loader_valid(tmp_path: Path) -> None:
    for audio_path in SYNTH_AUDIO:
        score = draft_score(_extract_synth(audio_path))
        yaml_text = render_draft_score_yaml(score)
        score_path = tmp_path / f"{audio_path.stem}.yaml"
        score_path.write_text(yaml_text, encoding="utf-8")

        loaded = load_composition_score(score_path)

        assert loaded == score
        assert loaded.semantic.core == TODO_AUTHOR_INPUT
        assert loaded.semantic.grv.primary == TODO_AUTHOR_INPUT
        assert loaded.semantic.grv.secondary == TODO_AUTHOR_INPUT
        assert loaded.semantic.delta_e.overall == TODO_AUTHOR_INPUT
        assert loaded.semantic.avoid == []
        assert loaded.physical.brightness in {"dark", "bright"}
        assert loaded.physical.stereo_width == TODO_STEREO_UNMEASURED
        assert _parse_numeric_range(loaded.physical.active_rate_target) is not None
        assert _parse_numeric_range(loaded.physical.valley_depth_target) is not None
        assert all(section.role == TODO_AUTHOR_INPUT for section in loaded.structure)
        assert all(section.physical == TODO_AUTHOR_INPUT for section in loaded.structure)
        assert TODO_SENTINEL_PREFIX in yaml_text


def test_draft_score_maps_t0_measurements_to_physical_layer() -> None:
    score = draft_score(_extract_synth(SYNTH_AUDIO[0]))

    assert score.physical.bpm == 117
    assert score.physical.key == "C major"
    assert score.physical.time_signature == "4/4"
    assert score.physical.brightness == "dark"
    assert score.physical.active_rate_target == "0.96-1.00"
    assert score.physical.valley_depth_target == "0.16-0.20"


def test_draft_score_marks_measured_stereo_as_band_undefined() -> None:
    score = draft_score(_make_bundle(stereo_width=0.72))

    assert score.physical.stereo_width == TODO_STEREO_BAND_UNDEFINED


def test_draft_score_marks_neutral_brightness_as_todo(tmp_path: Path) -> None:
    score = draft_score(_make_bundle(spectral_centroid=1800.0))
    yaml_text = render_draft_score_yaml(score)
    score_path = tmp_path / "neutral_brightness.yaml"
    score_path.write_text(yaml_text, encoding="utf-8")

    assert score.physical.brightness == TODO_BRIGHTNESS_NEUTRAL
    assert load_composition_score(score_path).physical.brightness == TODO_BRIGHTNESS_NEUTRAL


def test_draft_score_marks_missing_bpm_as_todo(tmp_path: Path) -> None:
    score = draft_score(_make_bundle(bpm=None))
    yaml_text = render_draft_score_yaml(score)
    score_path = tmp_path / "missing_bpm.yaml"
    score_path.write_text(yaml_text, encoding="utf-8")

    assert score.physical.bpm == TODO_BPM_UNDETECTED
    assert [section.bars for section in score.structure] == [1]
    assert load_composition_score(score_path).physical.bpm == TODO_BPM_UNDETECTED


def test_draft_score_marks_missing_key_as_todo(tmp_path: Path) -> None:
    score = draft_score(_make_bundle(key=None, mode=None))
    yaml_text = render_draft_score_yaml(score)
    score_path = tmp_path / "missing_key.yaml"
    score_path.write_text(yaml_text, encoding="utf-8")

    assert score.physical.key == TODO_KEY_UNDETECTED
    assert load_composition_score(score_path).physical.key == TODO_KEY_UNDETECTED


def test_draft_score_marks_zero_confidence_bpm_as_todo(tmp_path: Path) -> None:
    score = draft_score(_make_bundle(bpm=128.2, bpm_confidence=0.0))
    yaml_text = render_draft_score_yaml(score)
    score_path = tmp_path / "zero_confidence_bpm.yaml"
    score_path.write_text(yaml_text, encoding="utf-8")

    assert score.physical.bpm == TODO_BPM_UNDETECTED
    assert [section.bars for section in score.structure] == [1]
    assert load_composition_score(score_path).physical.bpm == TODO_BPM_UNDETECTED


def test_draft_score_marks_zero_confidence_time_signature_as_todo(tmp_path: Path) -> None:
    score = draft_score(
        _make_bundle(
            bpm=128.2,
            bpm_confidence=0.9,
            time_signature="4/4",
            time_signature_confidence=0.0,
        )
    )
    yaml_text = render_draft_score_yaml(score)
    score_path = tmp_path / "zero_confidence_time_signature.yaml"
    score_path.write_text(yaml_text, encoding="utf-8")

    assert score.physical.bpm == 128
    assert score.physical.time_signature == TODO_TIME_SIGNATURE_UNDETECTED
    assert [section.bars for section in score.structure] == [1]
    assert (
        load_composition_score(score_path).physical.time_signature
        == TODO_TIME_SIGNATURE_UNDETECTED
    )


def test_waltz_draft_uses_three_four_bars() -> None:
    score = draft_score(_extract_synth(Path("examples/sample_input/synth_04_waltz_fsharp_minor.wav")))

    assert score.physical.time_signature == "3/4"
    assert [section.bars for section in score.structure] == [5, 6, 6, 6, 6, 5]


def test_draft_score_snapshots_are_stable() -> None:
    for audio_path, expected_path in SYNTH_DRAFT_SNAPSHOTS:
        score = draft_score(_extract_synth(audio_path))
        yaml_text = render_draft_score_yaml(score)

        assert yaml_text == expected_path.read_text(encoding="utf-8")
        assert yaml_text == render_draft_score_yaml(score)


def test_transcribe_cli_writes_loader_valid_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "draft_score.yaml"

    def fake_extract(path: str) -> RPEBundle:
        assert path == "fixture.wav"
        return _make_bundle()

    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", fake_extract)

    result = runner.invoke(app, ["transcribe", "fixture.wav", "--output", str(output_path)])

    assert result.exit_code == 0
    assert "Draft CompositionScore saved" in result.output
    loaded = load_composition_score(output_path)
    assert loaded.meta.title == "fixture"
    assert loaded.physical.active_rate_target == "0.71-0.75"
    assert loaded.physical.valley_depth_target == "0.16-0.20"


def test_transcribe_cli_stdout_is_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", lambda _: _make_bundle())

    result = runner.invoke(app, ["transcribe", "fixture.wav"])

    assert result.exit_code == 0
    assert result.output.startswith("meta:\n")
    assert "TODO(transcribe):" in result.output
    assert "Draft CompositionScore saved" not in result.output


def test_transcribe_cli_help() -> None:
    result = runner.invoke(app, ["transcribe", "--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "transcribe" in result.output
    assert "draft CompositionScore YAML" in result.output


def _extract_synth(path: Path) -> RPEBundle:
    if path not in _BUNDLE_CACHE:
        _BUNDLE_CACHE[path] = extract_rpe_from_file(str(path))
    return _BUNDLE_CACHE[path]


def _make_bundle(
    stereo_width: float | None = None,
    spectral_centroid: float = 900.0,
    bpm: float | None = 128.2,
    bpm_confidence: float | None = None,
    key: str | None = "C",
    mode: str | None = "minor",
    time_signature: str = "4/4",
    time_signature_confidence: float = 0.3,
) -> RPEBundle:
    physical = PhysicalRPE(
        bpm=bpm,
        bpm_confidence=bpm_confidence,
        key=key,
        mode=mode,
        duration_sec=8.0,
        sample_rate=44100,
        time_signature=time_signature,
        time_signature_confidence=time_signature_confidence,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=8.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.73,
        valley_depth=0.18,
        thickness=1.0,
        spectral_centroid=spectral_centroid,
        spectral_profile=SpectralProfile(
            centroid=spectral_centroid,
            low_ratio=0.4,
            mid_ratio=0.5,
            high_ratio=0.1,
            brightness=0.1,
        ),
        stereo_profile=(
            None
            if stereo_width is None
            else StereoProfile(width=stereo_width, correlation=0.25)
        ),
        onset_density=2.0,
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
