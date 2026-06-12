"""Tests for composition audit control-panel reports."""
from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import yaml
from conftest import assert_no_outcome_keys as _assert_no_outcome_keys
from typer.testing import CliRunner

from svp_rpe.cli import app
from svp_rpe.compose import CompositionScore
from svp_rpe.rpe.models import (
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SpectralProfile,
    StereoProfile,
)
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.semantic_ci.audit import build_audit_report, render_audit_text

runner = CliRunner()


def _make_score() -> CompositionScore:
    return CompositionScore.model_validate(
        {
            "meta": {"title": "Needle Test", "version": 0.1},
            "semantic": {
                "core": "mid focused steady pulse",
                "grv": {"primary": "mid-focused", "secondary": "wide-field"},
                "delta_e": {"overall": "flat"},
                "avoid": [],
            },
            "physical": {
                "bpm": 128,
                "key": "C major",
                "time_signature": "4/4",
                "active_rate_target": "0.60-0.70",
                "valley_depth_target": "0.10-0.20",
                "brightness": "bright",
                "stereo_width": "wide",
            },
            "structure": [
                {
                    "section": "intro",
                    "bars": 4,
                    "role": "establish",
                    "physical": "steady",
                }
            ],
            "rendering": {
                "target_backend": "external",
                "prompt_max_chars": 500,
                "priority": ["semantic.core"],
            },
        }
    )


def _make_bundle(
    *,
    brightness: float = 0.5,
    valley_depth: float = 0.05,
    time_signature: str = "4/4",
    stereo_width: float | None = None,
) -> RPEBundle:
    physical = PhysicalRPE(
        bpm=130.0,
        key="C",
        mode="major",
        duration_sec=12.0,
        sample_rate=44100,
        time_signature=time_signature,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=12.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.8,
        valley_depth=valley_depth,
        thickness=1.0,
        spectral_centroid=2200.0,
        spectral_profile=SpectralProfile(
            centroid=2200.0,
            low_ratio=0.2,
            mid_ratio=max(0.0, 0.8 - brightness),
            high_ratio=brightness,
            brightness=brightness,
        ),
        stereo_profile=(
            StereoProfile(width=stereo_width, correlation=0.1)
            if stereo_width is not None
            else None
        ),
        onset_density=2.0,
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file="fixture.wav",
        audio_duration_sec=12.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


def _needle(report: Any, layer: str, name: str) -> Any:
    needles = getattr(report, layer)
    return next(item for item in needles if item.name == name)


def test_audit_report_returns_needles_without_outcome_keys() -> None:
    report = build_audit_report(_make_score(), _make_bundle(), observed_id="fixture-rpe")

    bpm = _needle(report, "physical", "bpm")
    key = _needle(report, "physical", "key")
    time_signature = _needle(report, "physical", "time_signature")
    active_rate = _needle(report, "physical", "active_rate")
    valley_depth = _needle(report, "physical", "valley_depth")
    brightness = _needle(report, "physical", "brightness")
    stereo_width = _needle(report, "physical", "stereo_width")

    assert bpm.deviation == 2.0
    assert key.score == 1.0
    assert time_signature.score == 1.0
    assert active_rate.deviation == 0.1
    assert active_rate.observed_band is None
    assert valley_depth.deviation == -0.05
    assert valley_depth.observed_band is None
    assert brightness.target_band == "spectral_centroid >= 2500"
    assert brightness.deviation == -300.0
    assert brightness.observed_band is None
    assert brightness.sensor == "PhysicalRPE.spectral_centroid"
    assert stereo_width.note == "sensor missing"
    assert _needle(report, "semantic", "delta_e").score is not None

    dumped = report.model_dump(mode="json")
    _assert_no_outcome_keys(dumped)


def test_audit_text_snapshot_is_stable() -> None:
    report = build_audit_report(_make_score(), _make_bundle(), observed_id="fixture-rpe")

    assert render_audit_text(report) == (
        "# Composition Audit Control Panel\n"
        "\n"
        "- score_id: needle-test\n"
        "- observed_id: fixture-rpe\n"
        "- source: rpe_extract\n"
        "- observed_signals: "
        "a continuous, wall-like sonic character, bright, continuous, flat, wall-like\n"
        "\n"
        "## Physical Needles\n"
        "\n"
        "| knob | target | observed | band | deviation | score | note |\n"
        "|---|---:|---:|---|---:|---:|---|\n"
        "| bpm | 128 | 130 |  | 2 |  |  |\n"
        "| key | C major | C major |  | 0 | 1 |  |\n"
        "| time_signature | 4/4 | 4/4 |  | 0 | 1 |  |\n"
        "| active_rate | 0.60-0.70 | 0.8 |  | 0.1 |  |  |\n"
        "| valley_depth | 0.10-0.20 | 0.05 |  | -0.05 |  |  |\n"
        "| brightness | bright | 2200 |  | -300 |  |  |\n"
        "| stereo_width | wide |  |  |  |  | sensor missing |\n"
        "\n"
        "## Semantic Needles\n"
        "\n"
        "| knob | target | observed | band | deviation | score | note |\n"
        "|---|---:|---:|---|---:|---:|---|\n"
        "| core | mid focused steady pulse | "
        "A continuous, wall-like sonic character |  | 1 | 0 |  |\n"
        "| grv | mid-focused, wide-field | bright |  | 0.3333 | 0.6667 |  |\n"
        "| delta_e | flat | flat |  | 0.09 | 0.91 |  |\n"
    )


def test_audit_maps_wide_target_to_spacious_stereo_rule() -> None:
    report = build_audit_report(
        _make_score(),
        _make_bundle(stereo_width=0.8),
        observed_id="fixture-rpe",
    )

    stereo_width = _needle(report, "physical", "stereo_width")

    assert stereo_width.target == "wide"
    assert stereo_width.observed == 0.8
    assert stereo_width.observed_band == "spacious"
    assert stereo_width.target_band == "stereo_width >= 0.7"
    assert stereo_width.deviation == 0.0


def test_audit_reports_time_signature_mismatch_as_needle() -> None:
    report = build_audit_report(
        _make_score(),
        _make_bundle(time_signature="3/4"),
        observed_id="fixture-rpe",
    )

    time_signature = _needle(report, "physical", "time_signature")

    assert time_signature.target == "4/4"
    assert time_signature.observed == "3/4"
    assert time_signature.score == 0.0
    assert time_signature.deviation == 1.0


def test_audit_key_needle_falls_back_without_mir_eval(monkeypatch: Any) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mir_eval.key":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    score = _make_score()
    score.physical.key = "C Major"
    report = build_audit_report(score, _make_bundle(), observed_id="fixture-rpe")
    key = _needle(report, "physical", "key")

    assert key.target == "C Major"
    assert key.observed == "C major"
    assert key.score == 1.0
    assert key.deviation == 0.0
    assert key.sensor == "exact_key_match"


def test_audit_maps_dark_brightness_to_explicit_dark_rule() -> None:
    score = _make_score()
    score.physical.brightness = "dark"

    report = build_audit_report(
        score,
        _make_bundle(brightness=0.5),
        observed_id="fixture-rpe",
    )

    brightness = _needle(report, "physical", "brightness")

    assert brightness.target == "dark"
    # 正規センサーは spectral_centroid。perc.dark の明示帯（<= 1200）に対し、
    # fixture の centroid 2200 は帯外 → 正の deviation が出る
    assert brightness.target_band == "spectral_centroid <= 1200"
    assert brightness.deviation == 1000.0


def test_audit_canonicalizes_freeform_delta_e_target() -> None:
    score = _make_score()
    score.semantic.delta_e.overall = "gradual build from solitude to release"

    report = build_audit_report(
        score,
        _make_bundle(valley_depth=0.18),
        observed_id="fixture-rpe",
    )

    delta_e = _needle(report, "semantic", "delta_e")

    assert delta_e.target == "gradual build from solitude to release"
    assert delta_e.observed == "gradual_build"
    assert delta_e.score is not None and delta_e.score >= 0.9
    assert delta_e.note == "canonical target=gradual_build"


def test_audit_cli_json_exits_zero_and_has_no_outcome_keys(tmp_path: Path) -> None:
    score_path = tmp_path / "composition_score.yaml"
    rpe_path = tmp_path / "rpe.json"
    score_path.write_text(
        yaml.safe_dump(_make_score().model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    rpe_path.write_text(
        json.dumps(_make_bundle().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["audit", str(score_path), str(rpe_path), "--format", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    _assert_no_outcome_keys(payload)
    assert payload["physical"][0]["name"] == "bpm"


def test_audit_cli_audio_input_uses_extractor(monkeypatch: Any, tmp_path: Path) -> None:
    score_path = tmp_path / "composition_score.yaml"
    audio_path = tmp_path / "generated.wav"
    calls: dict[str, Any] = {}
    score_path.write_text(
        yaml.safe_dump(_make_score().model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    audio_path.write_bytes(b"not real audio; extractor is monkeypatched")

    def fake_extract_rpe_from_file(
        path: str,
        *,
        valley_method: str = "hybrid",
        **_: Any,
    ) -> RPEBundle:
        calls["path"] = path
        calls["valley_method"] = valley_method
        return _make_bundle()

    monkeypatch.setattr(
        "svp_rpe.rpe.extractor.extract_rpe_from_file",
        fake_extract_rpe_from_file,
    )

    result = runner.invoke(
        app,
        [
            "audit",
            str(score_path),
            str(audio_path),
            "--format",
            "json",
            "--valley-method",
            "section_ar",
        ],
    )

    assert result.exit_code == 0
    assert calls == {"path": str(audio_path), "valley_method": "section_ar"}
    payload = json.loads(result.output)
    _assert_no_outcome_keys(payload)
    assert payload["observed_id"] == "generated"
