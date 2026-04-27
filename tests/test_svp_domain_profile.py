from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from svp_rpe.eval.comparison import compare_metric_values
from svp_rpe.eval.diff_models import PhysicalDiff
from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticLabel,
    SemanticRPE,
    SpectralProfile,
)
from svp_rpe.svp.domain_profile import load_domain_profile
from svp_rpe.svp import domain_profile as domain_profile_module
from svp_rpe.svp.generator import generate_svp
from svp_rpe.svp.parser import parse_svp_yaml
from svp_rpe.svp.render_yaml import render_yaml


def _make_rpe() -> RPEBundle:
    physical = PhysicalRPE(
        bpm=128.0,
        key="C",
        mode="major",
        duration_sec=90.0,
        sample_rate=44100,
        structure=[
            SectionMarker(label="Intro", start_sec=0.0, end_sec=20.0),
            SectionMarker(label="Verse", start_sec=20.0, end_sec=60.0),
            SectionMarker(label="Chorus", start_sec=60.0, end_sec=90.0),
        ],
        rms_mean=0.3,
        peak_amplitude=0.9,
        crest_factor=3.0,
        active_rate=0.85,
        valley_depth=0.25,
        thickness=2.0,
        spectral_centroid=2600.0,
        spectral_profile=SpectralProfile(
            centroid=2600.0,
            low_ratio=0.42,
            mid_ratio=0.4,
            high_ratio=0.18,
            brightness=0.18,
        ),
        onset_density=4.5,
    )
    semantic = SemanticRPE(
        por_core="energetic grounded track",
        por_surface=[
            SemanticLabel(
                label="energetic",
                layer="semantic_hypothesis",
                confidence=0.68,
                evidence=["bpm=128 >= 120", "active_rate=0.85 >= 0.8"],
                source_rule="fixture.hyp",
            ),
            SemanticLabel(
                label="grounded",
                layer="perceptual",
                confidence=0.8,
                evidence=["low_ratio=0.42 >= 0.4"],
                source_rule="fixture.perc",
            ),
        ],
        grv_anchor=GrvAnchor(primary="bass-heavy"),
        delta_e_profile=DeltaEProfile(
            transition_type="gradual_build",
            intensity=0.7,
            description="Builds over time",
        ),
        cultural_context=["electronic/dance"],
        instrumentation_summary="synth bass and drums",
        production_notes=["compressed mix"],
        confidence_notes=["fixture"],
    )
    return RPEBundle(
        physical=physical,
        semantic=semantic,
        audio_file="fixture.wav",
        audio_duration_sec=90.0,
        audio_sample_rate=44100,
        audio_channels=2,
        audio_format="wav",
    )


def test_generated_yaml_uses_source_artifact_not_source_audio() -> None:
    svp = generate_svp(_make_rpe())
    yaml_text = render_yaml(svp)

    assert svp.data_lineage.source_audio == "fixture.wav"
    assert "source_artifact:" in yaml_text
    assert "source_audio:" not in yaml_text
    assert "BPM range: 118-138" in yaml_text
    assert svp.evaluation_criteria.metric_checks["domain"] == "music"
    assert svp.minimal_svp.de == "gradual_build (0.70)"


def test_minimal_svp_preserves_extracted_delta_e_transition() -> None:
    rpe = _make_rpe()
    rpe.semantic.delta_e_profile.transition_type = "flat"

    svp = generate_svp(rpe)
    parsed = parse_svp_yaml(svp.model_dump(exclude_none=True))

    assert svp.minimal_svp.de == "flat (0.70)"
    assert parsed.delta_e_profile == "flat (0.70)"


def test_parser_extracts_new_source_and_generation_hints() -> None:
    svp = generate_svp(_make_rpe())
    parsed = parse_svp_yaml(svp.model_dump(exclude_none=True))

    assert parsed.domain == "music"
    assert parsed.source_artifact is not None
    assert parsed.source_artifact["type"] == "audio"
    assert parsed.instrumentation_notes == ["synth bass and drums", "compressed mix"]


def test_profile_defaults_are_applied_when_no_rule_matches() -> None:
    rpe = _make_rpe()
    rpe.physical.spectral_profile.low_ratio = 0.1
    rpe.physical.spectral_profile.mid_ratio = 0.1
    rpe.physical.spectral_profile.brightness = 0.1
    rpe.physical.active_rate = 0.5
    rpe.physical.valley_depth = 0.05
    svp = generate_svp(rpe)

    assert svp.analysis_rpe.por_surface == ["musical"]
    assert svp.analysis_rpe.grv_primary == "balanced"
    assert svp.svp_for_generation.style_tags == []


def test_profile_yaml_can_swap_value_vocab_without_schema_change(tmp_path: Path) -> None:
    profile_path = tmp_path / "video.yaml"
    profile_path.write_text(
        """
schema_version: "1.0"
domain: video
source_artifact_type: video
default_por_surface: [narrative]
default_grv_primary: visual-balance
prompt_template: "Video core: {por_core}"
style_tag_sources: [por_surface]
por_surface_rules:
  - condition: {color_temperature: warm}
    labels: [warm-toned, intimate]
grv_primary_vocab:
  - condition: {color_temperature: warm}
    label: warm-toned
section_label_vocab: [Opening, Body, Climax]
constraint_templates:
  - id: scene_count
    condition: {section_count_exists: true}
    template: "Scene count: {section_count}"
physical_check_templates:
  - id: rhythm
    condition: {shot_rhythm_exists: true}
    template: "Shot rhythm matches {shot_rhythm}"
minimal_constraint_templates:
  - id: rhythm
    condition: {shot_rhythm_exists: true}
    template: "rhythm={shot_rhythm}"
delta_e_vocab:
  - condition: {contrast_arc: rising}
    label: rising_contrast
diff_metrics:
  - name: shot_rhythm
    exact_match: true
""",
        encoding="utf-8",
    )
    profile = load_domain_profile(path=profile_path)
    context = {
        "por_core": "quiet reveal",
        "color_temperature": "warm",
        "section_count": 3,
        "shot_rhythm": "medium",
        "contrast_arc": "rising",
    }

    assert profile.domain == "video"
    selected = profile.select_por_surface(context)
    assert [label.label for label in selected] == ["warm-toned", "intimate"]
    assert all(label.layer == "perceptual" for label in selected)
    assert profile.select_grv_primary(context) == "warm-toned"
    assert profile.format_structure_summary(context) == "3 sections: Opening, Body, Climax"
    assert profile.render_constraints(context) == ["Scene count: 3"]
    assert profile.format_delta_e(context) == "rising_contrast"


def test_default_profile_falls_back_to_packaged_resource(monkeypatch) -> None:
    monkeypatch.setattr(domain_profile_module, "_local_profile_paths", lambda domain: [])

    profile = load_domain_profile("music")

    assert profile.domain == "music"
    assert "bpm" in profile.diff_metric_names


def test_empty_local_profile_override_is_not_replaced_by_packaged(
    monkeypatch,
    tmp_path: Path,
) -> None:
    profile_path = tmp_path / "music.yaml"
    profile_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        domain_profile_module,
        "_local_profile_paths",
        lambda domain: [profile_path],
    )

    with pytest.raises(ValidationError):
        load_domain_profile("music")


def test_generate_svp_works_with_packaged_music_profile(monkeypatch) -> None:
    monkeypatch.setattr(domain_profile_module, "_local_profile_paths", lambda domain: [])

    svp = generate_svp(_make_rpe())

    assert svp.domain == "music"
    assert svp.svp_for_generation.constraints
    assert svp.minimal_svp.de == "gradual_build (0.70)"


def test_physical_diff_keeps_legacy_fields_and_generic_metrics() -> None:
    legacy = PhysicalDiff(bpm_diff=8.0, key_match=True, rms_diff=0.02)

    assert legacy.bpm_diff == 8.0
    assert legacy.metric("bpm_diff") is not None
    assert legacy.metric("key_match").passed is True

    generic = compare_metric_values(
        {"shot_rhythm": "medium", "brightness": 0.7},
        {"shot_rhythm": "medium", "brightness": 0.62},
        metric_names=["shot_rhythm", "brightness"],
        tolerances={"brightness": 0.1},
        domain="video",
    )

    assert generic.domain == "video"
    assert generic.metric("shot_rhythm").passed is True
    assert generic.metric("brightness").passed is True


def test_numeric_metric_without_tolerance_uses_distance_score() -> None:
    generic = compare_metric_values(
        {"brightness": 0.7},
        {"brightness": 0.62},
        metric_names=["brightness"],
        domain="image",
    )

    metric = generic.metric("brightness")
    assert metric is not None
    assert metric.passed is None
    assert metric.diff == 0.07999999999999996
    assert 0.9 < generic.overall < 1.0


def test_boolean_metric_uses_categorical_scoring() -> None:
    generic = compare_metric_values(
        {"has_hook": True},
        {"has_hook": False},
        metric_names=["has_hook"],
        domain="video",
    )

    metric = generic.metric("has_hook")
    assert metric is not None
    assert metric.diff is None
    assert metric.passed is False
    assert generic.overall == 0.0


def test_generic_diff_with_no_overlapping_metrics_stays_empty() -> None:
    generic = compare_metric_values(
        {"brightness": 0.7},
        {"shot_rhythm": "medium"},
        metric_names=["brightness", "shot_rhythm"],
        domain="video",
    )

    assert generic.domain == "video"
    assert generic.metrics == {}
    assert generic.overall == 0.0


def test_explicit_empty_metric_names_disables_comparison() -> None:
    generic = compare_metric_values(
        {"brightness": 0.7},
        {"brightness": 0.7},
        metric_names=[],
        domain="video",
    )

    assert generic.domain == "video"
    assert generic.metrics == {}
    assert generic.overall == 0.0


def test_none_metric_values_are_skipped() -> None:
    generic = compare_metric_values(
        {"optional_flag": None, "brightness": 0.7},
        {"optional_flag": None, "brightness": 0.7},
        metric_names=["optional_flag"],
        domain="video",
    )

    assert generic.domain == "video"
    assert generic.metrics == {}
    assert generic.overall == 0.0
