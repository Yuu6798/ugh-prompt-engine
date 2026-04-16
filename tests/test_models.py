"""tests/test_models.py — Pydantic model validation tests."""
from __future__ import annotations

import pytest

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    PhysicalRPE,
    SectionMarker,
    SemanticRPE,
    SpectralProfile,
)
from svp_rpe.svp.models import (
    AnalysisRPE,
    DataLineage,
    EvaluationCriteria,
    MinimalSVP,
    SVPBundle,
    SVPForGeneration,
)
from svp_rpe.eval.models import IntegratedScore, RPEScore, UGHerScore


def _make_section():
    return SectionMarker(label="section_01", start_sec=0.0, end_sec=10.0)


def _make_spectral():
    return SpectralProfile(
        centroid=3000.0, low_ratio=0.3, mid_ratio=0.5, high_ratio=0.2, brightness=0.28
    )


def _make_physical():
    return PhysicalRPE(
        duration_sec=180.0, sample_rate=44100,
        structure=[_make_section()],
        rms_mean=0.3, peak_amplitude=0.9, crest_factor=3.0,
        active_rate=0.85, valley_depth=0.2, thickness=2.0,
        spectral_centroid=3000.0, spectral_profile=_make_spectral(),
        onset_density=4.5,
    )


class TestPhysicalRPE:
    def test_valid_construction(self):
        p = _make_physical()
        assert p.duration_sec == 180.0
        assert len(p.structure) == 1

    def test_empty_structure_rejected(self):
        with pytest.raises(ValueError, match="at least one section"):
            PhysicalRPE(
                duration_sec=10.0, sample_rate=22050, structure=[],
                rms_mean=0.1, peak_amplitude=0.5, crest_factor=5.0,
                active_rate=0.5, valley_depth=0.1, thickness=1.0,
                spectral_centroid=2000.0, spectral_profile=_make_spectral(),
                onset_density=2.0,
            )

    def test_optional_fields_default(self):
        p = _make_physical()
        assert p.bpm is None
        assert p.key is None
        assert p.stereo_profile is None
        assert p.time_signature == "4/4"


class TestSemanticRPE:
    def test_valid_construction(self):
        s = SemanticRPE(
            por_core="energetic driving track",
            por_surface=["energetic", "driving"],
            grv_anchor=GrvAnchor(primary="bass-heavy"),
            delta_e_profile=DeltaEProfile(
                transition_type="gradual_build", intensity=0.7,
                description="Builds from sparse to dense",
            ),
            cultural_context=["electronic"],
            instrumentation_summary="synths and drums",
            production_notes=["compressed mix"],
            confidence_notes=["rule: bpm > 140"],
        )
        assert s.por_core == "energetic driving track"
        assert s.estimation_disclaimer.startswith("semantic層")


class TestSVPBundle:
    def test_valid_construction(self):
        bundle = SVPBundle(
            data_lineage=DataLineage(source_audio="test.wav"),
            analysis_rpe=AnalysisRPE(
                por_core="test", por_surface=["a"], grv_primary="bass",
                duration_sec=60.0, structure_summary="1 section",
            ),
            svp_for_generation=SVPForGeneration(
                prompt_text="Create...", constraints=["bpm>120"],
                style_tags=["electronic"],
            ),
            evaluation_criteria=EvaluationCriteria(
                por_check="match core", grv_check="match anchor",
                delta_e_check="match transition", physical_checks=["bpm"],
            ),
            minimal_svp=MinimalSVP(c="test", g=["bpm>120"], de="gradual"),
        )
        assert bundle.schema_version == "1.0"


class TestEvalModels:
    def test_rpe_score(self):
        s = RPEScore(
            rms_score=0.8, active_rate_score=0.9, crest_factor_score=0.7,
            valley_score=0.6, thickness_score=0.85, overall=0.77,
        )
        assert 0.0 <= s.overall <= 1.0

    def test_ugher_score(self):
        s = UGHerScore(
            por_similarity=0.8, grv_consistency=0.7,
            delta_e_assessment=0.6, physical_accuracy=0.9, overall=0.75,
        )
        assert 0.0 <= s.overall <= 1.0

    def test_integrated_score(self):
        s = IntegratedScore(
            ugher_score=0.75, rpe_score=0.77, integrated_score=0.76,
        )
        assert s.ugher_weight == 0.5
        assert s.rpe_weight == 0.5
