"""Tests for score-centric per-field measurement reports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.rpe.models import (
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SpectralProfile,
    StereoProfile,
)
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.transcribe import SCORE_FIELDS, measure_fields, parse_field_filter
from svp_rpe.transcribe.measure import render_measurement_json

runner = CliRunner()

SYNTH_MEASUREMENT_SNAPSHOTS = (
    (
        Path("examples/sample_input/synth_01_slow_pad_c_major.wav"),
        Path("examples/transcribe/synth_01_slow_pad_c_major_measurement.json"),
    ),
    (
        Path("examples/sample_input/synth_02_minor_pulse_a_minor.wav"),
        Path("examples/transcribe/synth_02_minor_pulse_a_minor_measurement.json"),
    ),
    (
        Path("examples/sample_input/synth_03_mid_groove_g_major.wav"),
        Path("examples/transcribe/synth_03_mid_groove_g_major_measurement.json"),
    ),
    (
        Path("examples/sample_input/synth_04_waltz_fsharp_minor.wav"),
        Path("examples/transcribe/synth_04_waltz_fsharp_minor_measurement.json"),
    ),
    (
        Path("examples/sample_input/synth_05_fast_bright_d_major.wav"),
        Path("examples/transcribe/synth_05_fast_bright_d_major_measurement.json"),
    ),
)


def _make_bundle(
    *,
    spectral_centroid: float = 2600.0,
    band_brightness: float = 0.1,
    stereo_profile: StereoProfile | None = None,
) -> RPEBundle:
    physical = PhysicalRPE(
        bpm=119.6,
        key="C",
        mode="major",
        duration_sec=10.0,
        sample_rate=44100,
        time_signature="4/4",
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=10.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.73,
        valley_depth=0.18,
        thickness=1.0,
        spectral_centroid=spectral_centroid,
        spectral_profile=SpectralProfile(
            centroid=spectral_centroid,
            low_ratio=0.2,
            mid_ratio=0.7,
            high_ratio=0.1,
            brightness=band_brightness,
        ),
        stereo_profile=stereo_profile,
        onset_density=2.0,
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file="fixture.wav",
        audio_duration_sec=10.0,
        audio_sample_rate=44100,
        audio_channels=2,
        audio_format="wav",
    )


def _measurement_by_field(bundle: RPEBundle) -> dict[str, Any]:
    report = measure_fields(bundle, list(SCORE_FIELDS))
    return {item.score_field: item for item in report.measurements}


def test_measure_fields_maps_all_score_fields() -> None:
    bundle = _make_bundle(stereo_profile=StereoProfile(width=0.82, correlation=0.1))

    report = measure_fields(bundle, list(SCORE_FIELDS))
    by_field = {item.score_field: item for item in report.measurements}

    assert report.sample_id == "fixture"
    assert report.duration_sec == 10.0
    assert [item.score_field for item in report.measurements] == list(SCORE_FIELDS)
    assert by_field["bpm"].raw_value == 119.6
    assert by_field["bpm"].score_value == 120
    assert by_field["key"].raw_value == "C major"
    assert by_field["time_signature"].raw_value == "4/4"
    assert by_field["active_rate_target"].raw_value == 0.73
    assert by_field["active_rate_target"].score_value is None
    assert by_field["valley_depth_target"].raw_value == 0.18
    assert by_field["valley_depth_target"].score_value is None
    assert by_field["stereo_width"].raw_value == 0.82
    assert all(item.calibration for item in report.measurements)


def test_brightness_reads_spectral_centroid_not_legacy_band_ratio() -> None:
    bundle = _make_bundle(spectral_centroid=2600.0, band_brightness=0.0)
    brightness = _measurement_by_field(bundle)["brightness"]

    assert brightness.sensor == "physical.spectral_centroid"
    assert brightness.raw_value == 2600.0
    assert brightness.score_value == "bright"


@pytest.mark.parametrize(
    ("centroid", "expected"),
    [
        (1200.0, "dark"),
        (1200.1, None),
        (2499.9, None),
        (2500.0, "bright"),
    ],
)
def test_brightness_thresholds_are_stable(centroid: float, expected: str | None) -> None:
    brightness = _measurement_by_field(_make_bundle(spectral_centroid=centroid))["brightness"]

    assert brightness.score_value == expected


def test_missing_stereo_profile_is_null() -> None:
    stereo_width = _measurement_by_field(_make_bundle(stereo_profile=None))["stereo_width"]

    assert stereo_width.raw_value is None
    assert stereo_width.score_value is None


def test_field_filter_parsing_and_unknown_validation() -> None:
    assert parse_field_filter(None) == list(SCORE_FIELDS)
    assert parse_field_filter("bpm,key") == ["bpm", "key"]

    with pytest.raises(ValueError, match="unknown field"):
        parse_field_filter("bpm,unknown")


def test_measure_cli_rejects_unknown_field_before_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_extract(*_: Any, **__: Any) -> RPEBundle:
        raise AssertionError("extractor should not run for invalid field filters")

    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", fail_extract)

    result = runner.invoke(app, ["measure", "fixture.wav", "--fields", "bpm,unknown"])

    assert result.exit_code != 0
    assert "unknown field" in result.output


def test_measure_cli_writes_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out_path = tmp_path / "measurement.json"

    def fake_extract(path: str) -> RPEBundle:
        assert path == "fixture.wav"
        return _make_bundle(stereo_profile=StereoProfile(width=0.82, correlation=0.1))

    monkeypatch.setattr("svp_rpe.rpe.extractor.extract_rpe_from_file", fake_extract)

    result = runner.invoke(
        app,
        ["measure", "fixture.wav", "--fields", "bpm,key", "--output", str(out_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert [item["score_field"] for item in payload["measurements"]] == ["bpm", "key"]


# 各 synth 曲で実抽出を回す唯一の重いテスト。他は合成 bundle / monkeypatch で高速。
@pytest.mark.slow
def test_synth_measurement_snapshots_are_stable() -> None:
    for audio_path, expected_path in SYNTH_MEASUREMENT_SNAPSHOTS:
        bundle = extract_rpe_from_file(str(audio_path))
        measurement_json = _measurement_json_for(bundle)

        assert measurement_json == expected_path.read_text(encoding="utf-8")
        assert measurement_json == _measurement_json_for(bundle)


def _measurement_json_for(bundle: RPEBundle) -> str:
    report = measure_fields(bundle, list(SCORE_FIELDS))
    return render_measurement_json(report)
