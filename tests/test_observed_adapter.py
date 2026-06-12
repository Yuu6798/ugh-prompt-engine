"""Tests for RPEBundle -> ObservedRPE adaptation."""
from __future__ import annotations

from svp_rpe.rpe.models import (
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SpectralProfile,
    StereoProfile,
)
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.compose.convert import composition_to_target_svp
from svp_rpe.compose.models import CompositionScore
from svp_rpe.semantic_ci import rpe_bundle_to_observed
from svp_rpe.semantic_ci.core import compare_expected_observed, generate_expected_rpe


def _make_bundle(
    *,
    stereo_profile: StereoProfile | None = None,
    spectral_centroid: float = 900.0,
) -> RPEBundle:
    physical = PhysicalRPE(
        bpm=120.0,
        key="C",
        mode="major",
        duration_sec=10.0,
        sample_rate=44100,
        time_signature="4/4",
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=10.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.5,
        valley_depth=0.05,
        thickness=1.0,
        spectral_centroid=spectral_centroid,
        spectral_profile=SpectralProfile(
            centroid=spectral_centroid,
            low_ratio=0.2,
            mid_ratio=0.7,
            high_ratio=0.1,
            brightness=0.1,
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


def test_rpe_bundle_to_observed_preserves_raw_metrics_and_source() -> None:
    bundle = _make_bundle(stereo_profile=StereoProfile(width=0.8, correlation=0.2))

    observed = rpe_bundle_to_observed(bundle, id="midnight-fixture")

    assert observed.model_dump(mode="json") == {
        "schema_version": "1.0",
        "id": "midnight-fixture",
        "domain": "music",
        "signals": [
            "a dark, spacious, mid-focused sonic character",
            "dark",
            "flat",
            "mid-focused",
            "spacious",
            "wide-field",
        ],
        "metrics": {
            "bpm": 120.0,
            "key": "C major",
            "mode": "major",
            "time_signature": "4/4",
            "active_rate": 0.5,
            "active_rate_target": 0.5,
            "valley_depth": 0.05,
            "valley_depth_target": 0.05,
            "brightness": 0.1,
            "spectral_centroid": 900.0,
            "stereo_width": 0.8,
        },
        "source": "rpe_extract",
    }


def test_rpe_bundle_to_observed_allows_missing_stereo_sensor() -> None:
    observed = rpe_bundle_to_observed(_make_bundle(stereo_profile=None), id="mono")

    assert observed.metrics["stereo_width"] is None
    assert "stereo_width" in observed.metrics
    assert observed.source == "rpe_extract"


def test_observed_key_matches_composition_target_key_metric() -> None:
    score = CompositionScore.model_validate(
        {
            "meta": {"title": "Key Match", "version": 0.1},
            "semantic": {
                "core": "mid-focused",
                "grv": {"primary": "mid-focused", "secondary": ""},
                "delta_e": {"overall": "flat"},
                "avoid": [],
            },
            "physical": {
                "bpm": 120,
                "key": "C major",
                "time_signature": "4/4",
                "active_rate_target": "0.40-0.60",
                "valley_depth_target": "0.00-0.10",
                "brightness": "dark",
                "stereo_width": "wide",
            },
            "structure": [
                {
                    "section": "full",
                    "bars": 8,
                    "role": "fixture",
                    "physical": "steady",
                }
            ],
            "rendering": {
                "target_backend": "external",
                "prompt_max_chars": 500,
                "priority": [],
            },
        }
    )
    expected = generate_expected_rpe(composition_to_target_svp(score))
    observed = rpe_bundle_to_observed(
        _make_bundle(stereo_profile=StereoProfile(width=0.8, correlation=0.2)),
        id="key-match",
    )

    diff = compare_expected_observed(expected, observed)
    metric_diffs = {metric.name: metric for metric in diff.metric_diffs}

    assert metric_diffs["key"].expected == "C major"
    assert metric_diffs["key"].observed == "C major"
    assert metric_diffs["key"].passed is True
    assert metric_diffs["active_rate_target"].passed is True
    assert metric_diffs["valley_depth_target"].passed is True
    assert metric_diffs["brightness"].passed is True
    assert metric_diffs["stereo_width"].passed is True


def test_brightness_target_prefers_centroid_sensor_over_band_ratio() -> None:
    """bright ターゲットは band-ratio（0.x）でなく spectral_centroid と比較される。

    rpe_bundle_to_observed の metrics は legacy "brightness"（帯域比）と
    "spectral_centroid" の両方を含む。lookup が exact key を先に返すと
    Hz 帯境界（>=2500）に対して 0.1 を比較して誤 fail する（回帰ガード）。
    """
    score = CompositionScore.model_validate(
        {
            "meta": {"title": "Bright Match", "version": 0.1},
            "semantic": {
                "core": "bright lead",
                "grv": {"primary": "bright", "secondary": ""},
                "delta_e": {"overall": "flat"},
                "avoid": [],
            },
            "physical": {
                "bpm": 120,
                "key": "C major",
                "time_signature": "4/4",
                "active_rate_target": "0.40-0.60",
                "valley_depth_target": "0.00-0.10",
                "brightness": "bright",
                "stereo_width": "wide",
            },
            "structure": [
                {"section": "full", "bars": 8, "role": "fixture", "physical": "steady"}
            ],
            "rendering": {
                "target_backend": "external",
                "prompt_max_chars": 500,
                "priority": [],
            },
        }
    )
    expected = generate_expected_rpe(composition_to_target_svp(score))
    observed = rpe_bundle_to_observed(
        _make_bundle(
            stereo_profile=StereoProfile(width=0.8, correlation=0.2),
            spectral_centroid=2600.0,
        ),
        id="bright-match",
    )
    assert observed.metrics["brightness"] == 0.1  # legacy 帯域比は低いまま

    diff = compare_expected_observed(expected, observed)
    metric_diffs = {metric.name: metric for metric in diff.metric_diffs}

    assert metric_diffs["brightness"].observed == 2600.0
    assert metric_diffs["brightness"].passed is True
