from __future__ import annotations

import hashlib
import json

import pytest

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticRPE,
    SpectralProfile,
)
from svp_rpe.rpe.semantic_rules import generate_semantic
from svp_rpe.svp.generator import generate_svp
from svp_rpe.utils.config_loader import load_config


def _make_physical() -> PhysicalRPE:
    return PhysicalRPE(
        bpm=152.0,
        key="C",
        mode="major",
        duration_sec=90.0,
        sample_rate=44100,
        structure=[SectionMarker(label="Full", start_sec=0.0, end_sec=90.0)],
        rms_mean=0.3,
        peak_amplitude=0.9,
        crest_factor=3.0,
        active_rate=0.85,
        valley_depth=0.05,
        thickness=2.0,
        spectral_centroid=3200.0,
        spectral_profile=SpectralProfile(
            centroid=3200.0,
            low_ratio=0.1,
            mid_ratio=0.18,
            high_ratio=0.72,
            brightness=0.72,
        ),
        onset_density=4.5,
    )


def _make_bundle() -> RPEBundle:
    physical = _make_physical()
    semantic = generate_semantic(physical)
    return RPEBundle(
        physical=physical,
        semantic=semantic,
        audio_file="fixture.wav",
        audio_duration_sec=90.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


def _semantic_hash(physical: PhysicalRPE) -> str:
    semantic = generate_semantic(physical)
    payload = json.dumps(semantic.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_same_physical_rpe_produces_same_semantic_hash() -> None:
    physical = _make_physical()

    assert _semantic_hash(physical) == _semantic_hash(physical)


def test_emitted_labels_include_evidence_and_source_rule() -> None:
    semantic = generate_semantic(_make_physical())

    assert semantic.schema_version == "2.0"
    assert semantic.por_surface
    for label in semantic.por_surface:
        assert label.evidence
        assert label.source_rule


def test_low_confidence_hypothesis_does_not_enter_primary_style_tags() -> None:
    svp = generate_svp(_make_bundle())

    style_tags = svp.svp_for_generation.style_tags
    candidate_labels = [
        item["label"]
        for item in svp.svp_for_generation.generation_hints["candidate_context"]
    ]
    assert "energetic" not in style_tags
    assert "driving" not in style_tags
    assert "energetic" in candidate_labels
    assert "driving" in candidate_labels


def test_low_confidence_labels_do_not_enter_por_core() -> None:
    semantic = generate_semantic(_make_physical())

    assert "energetic" not in semantic.por_core
    assert "driving" not in semantic.por_core


def test_semantic_hypothesis_rules_have_multiple_conditions() -> None:
    config = load_config("semantic_rules")

    for rule in config["semantic_hypothesis"]:
        assert len(rule["condition"]) >= 2


def test_legacy_semantic_schema_fails_fast() -> None:
    with pytest.raises(ValueError, match="schema_version 1.0"):
        SemanticRPE.model_validate(
            {
                "schema_version": "1.0",
                "por_core": "legacy",
                "por_surface": ["legacy"],
                "grv_anchor": GrvAnchor(primary="legacy").model_dump(),
                "delta_e_profile": DeltaEProfile(
                    transition_type="flat",
                    intensity=0.2,
                    description="legacy",
                ).model_dump(),
                "cultural_context": ["general"],
                "instrumentation_summary": "legacy",
                "production_notes": ["legacy"],
                "confidence_notes": ["legacy"],
            }
        )
