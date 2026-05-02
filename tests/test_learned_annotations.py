"""tests/test_learned_annotations.py — LearnedAudioAnnotations schema and isolation tests.

Pins three things:
1. The new schemas validate input correctly.
2. `RPEBundle` parses with or without `learned_annotations` — backward compatible.
3. `generate_svp` does not read `learned_annotations`, so learned labels cannot
   leak into `SemanticRPE` evidence layers or `SVPForGeneration` outputs.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    LearnedAudioAnnotations,
    LearnedAudioLabel,
    LearnedEmbedding,
    LearnedModelInfo,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticLabel,
    SemanticRPE,
    SpectralProfile,
)
from svp_rpe.svp.generator import generate_svp


def _physical() -> PhysicalRPE:
    return PhysicalRPE(
        duration_sec=180.0,
        sample_rate=44100,
        bpm=120.0,
        key="C",
        mode="major",
        structure=[SectionMarker(label="section_01", start_sec=0.0, end_sec=180.0)],
        rms_mean=0.3,
        peak_amplitude=0.9,
        crest_factor=3.0,
        active_rate=0.85,
        valley_depth=0.2,
        thickness=2.0,
        spectral_centroid=3000.0,
        spectral_profile=SpectralProfile(
            centroid=3000.0,
            low_ratio=0.3,
            mid_ratio=0.5,
            high_ratio=0.2,
            brightness=0.28,
        ),
        onset_density=4.5,
    )


def _semantic() -> SemanticRPE:
    return SemanticRPE(
        por_core="bright energetic track",
        por_surface=[
            SemanticLabel(
                label="bright",
                layer="perceptual",
                confidence=0.9,
                evidence=["brightness=0.28"],
                source_rule="perc.brightness",
            ),
        ],
        grv_anchor=GrvAnchor(primary="bass-heavy"),
        delta_e_profile=DeltaEProfile(
            transition_type="flat",
            intensity=0.3,
            description="steady energy",
        ),
        cultural_context=["electronic"],
        instrumentation_summary="synths and drums",
        production_notes=["compressed mix"],
        confidence_notes=["rule: brightness > 0.2"],
    )


def _bundle(learned_annotations: LearnedAudioAnnotations | None = None) -> RPEBundle:
    return RPEBundle(
        physical=_physical(),
        semantic=_semantic(),
        audio_file="test.wav",
        audio_duration_sec=180.0,
        audio_sample_rate=44100,
        audio_channels=2,
        audio_format="wav",
        learned_annotations=learned_annotations,
    )


def _annotations(label_text: str = "music") -> LearnedAudioAnnotations:
    return LearnedAudioAnnotations(
        enabled_models=[
            LearnedModelInfo(
                name="panns_inference",
                version="0.1.1",
                provider="qiuqiangkong/panns_inference",
                task="tagging",
                license="MIT",
                weights_license="research-only",
            ),
        ],
        labels=[
            LearnedAudioLabel(
                label=label_text,
                category="audioset",
                confidence=0.42,
                source_model="panns_inference:Cnn14",
                evidence=["top-k tag from AudioSet 527"],
            ),
        ],
        embedding=LearnedEmbedding(
            source_model="panns_inference:Cnn14",
            vector=[0.0, 1.0, 2.0],
            dimensions=3,
        ),
        inference_config={"top_k": 5},
        license_metadata={"panns_inference:Cnn14": "research-only weights"},
    )


class TestLearnedModelInfo:
    def test_valid_construction(self):
        info = LearnedModelInfo(name="beat_this", task="beat_downbeat")
        assert info.name == "beat_this"
        assert info.task == "beat_downbeat"
        assert info.license is None

    def test_unknown_task_rejected(self):
        with pytest.raises(ValidationError):
            LearnedModelInfo(name="x", task="foo")  # type: ignore[arg-type]


class TestLearnedAudioLabel:
    def test_valid_construction(self):
        label = LearnedAudioLabel(
            label="music",
            confidence=0.5,
            source_model="panns_inference:Cnn14",
        )
        assert label.category == "other"
        assert label.evidence == []

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError, match="confidence"):
            LearnedAudioLabel(label="x", confidence=-0.01, source_model="m")

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError, match="confidence"):
            LearnedAudioLabel(label="x", confidence=1.01, source_model="m")

    def test_unknown_category_rejected(self):
        with pytest.raises(ValidationError):
            LearnedAudioLabel(
                label="x",
                category="moodish",  # type: ignore[arg-type]
                confidence=0.5,
                source_model="m",
            )


class TestLearnedEmbedding:
    def test_valid_construction(self):
        emb = LearnedEmbedding(source_model="m", vector=[0.0, 1.0], dimensions=2)
        assert emb.dimensions == 2

    def test_dimensions_mismatch_rejected(self):
        with pytest.raises(ValidationError, match="dimensions"):
            LearnedEmbedding(source_model="m", vector=[0.0, 1.0], dimensions=3)


class TestLearnedAudioAnnotations:
    def test_default_empty_construction(self):
        ann = LearnedAudioAnnotations()
        assert ann.schema_version == "1.0"
        assert ann.enabled_models == []
        assert ann.labels == []
        assert ann.embedding is None
        assert ann.inference_config == {}
        assert ann.license_metadata == {}
        assert "estimates" in ann.estimation_disclaimer

    def test_default_collections_independent_per_instance(self):
        a = LearnedAudioAnnotations()
        b = LearnedAudioAnnotations()
        a.labels.append(
            LearnedAudioLabel(label="x", confidence=0.1, source_model="m")
        )
        a.inference_config["k"] = "v"
        a.license_metadata["m"] = "MIT"
        a.enabled_models.append(LearnedModelInfo(name="m", task="other"))
        assert b.labels == []
        assert b.inference_config == {}
        assert b.license_metadata == {}
        assert b.enabled_models == []


class TestRPEBundleBackwardCompatibility:
    def test_bundle_without_learned_annotations(self):
        bundle = _bundle()
        assert bundle.learned_annotations is None

    def test_legacy_json_without_learned_annotations_field_parses(self):
        legacy_json = _bundle().model_dump_json(exclude={"learned_annotations"})
        parsed = json.loads(legacy_json)
        assert "learned_annotations" not in parsed
        restored = RPEBundle.model_validate_json(legacy_json)
        assert restored.learned_annotations is None

    def test_bundle_with_learned_annotations_roundtrip(self):
        original = _bundle(learned_annotations=_annotations())
        restored = RPEBundle.model_validate_json(original.model_dump_json())
        assert restored.learned_annotations is not None
        assert restored.learned_annotations.labels[0].label == "music"
        assert restored.learned_annotations.embedding is not None
        assert restored.learned_annotations.embedding.dimensions == 3
        assert restored.learned_annotations.enabled_models[0].name == "panns_inference"


class TestRPEBundleSerialization:
    """Pin that the wire format stays identical for bundles without learned_annotations.

    Without the omit-None serializer, every existing RPEBundle JSON dump gains
    `"learned_annotations": null` after this PR — which would break byte-equal
    snapshot tests and add noise to anything that diffs RPE JSON.
    """

    def test_bundle_without_learned_annotations_omits_field_in_dump(self):
        bundle = _bundle()
        assert "learned_annotations" not in bundle.model_dump()
        assert "learned_annotations" not in json.loads(bundle.model_dump_json())

    def test_bundle_with_learned_annotations_includes_field_in_dump(self):
        bundle = _bundle(learned_annotations=_annotations())
        dumped = bundle.model_dump()
        assert "learned_annotations" in dumped
        assert dumped["learned_annotations"]["labels"][0]["label"] == "music"


class TestLearnedAnnotationsIsolation:
    """Pin that the existing SVP generator does not read learned_annotations.

    PR3-PR5 will start populating learned_annotations from real backends.
    These tests catch any future regression where the generator starts
    folding learned-model output back into evidence-bearing fields.
    """

    def test_generate_svp_output_identical_with_and_without_learned(self):
        without = generate_svp(_bundle())
        with_learned = generate_svp(_bundle(learned_annotations=_annotations()))
        assert without.model_dump() == with_learned.model_dump()

    def test_learned_label_does_not_leak_into_svp_serialization(self):
        sentinel = "__LEARNED_ANNOTATION_LEAK_SENTINEL__"
        bundle = _bundle(learned_annotations=_annotations(label_text=sentinel))
        svp = generate_svp(bundle)
        serialized = svp.model_dump_json()
        assert sentinel not in serialized

    def test_learned_label_does_not_leak_into_semantic_por_surface(self):
        sentinel = "__SEMANTIC_PORSURFACE_LEAK__"
        bundle = _bundle(learned_annotations=_annotations(label_text=sentinel))
        por_surface_text = " ".join(
            label.label for label in bundle.semantic.por_surface
        )
        assert sentinel not in por_surface_text

    def test_learned_label_does_not_leak_into_style_tags_or_analysis(self):
        sentinel = "__STYLE_LEAK__"
        bundle = _bundle(learned_annotations=_annotations(label_text=sentinel))
        svp = generate_svp(bundle)
        assert all(sentinel not in tag for tag in svp.svp_for_generation.style_tags)
        assert all(sentinel not in tag for tag in svp.analysis_rpe.por_surface)
        assert sentinel not in svp.svp_for_generation.prompt_text
