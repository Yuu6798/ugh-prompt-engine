"""tests/test_panns_adapter.py — panns_inference adapter tests.

The real panns_inference package is NOT required to run these tests. We
monkeypatch `sys.modules["panns_inference"]` and
`sys.modules["panns_inference.labels"]` with a fake backend so the adapter
contract (top-k determinism, no-leakage isolation, version provenance,
license metadata) can be pinned without any optional install or model
download.

The fake mirrors panns_inference's `AudioTagging(checkpoint_path=None,
device=...)` entry point. `at.inference(batch)` returns
`(clipwise_output, embedding)` where clipwise_output has shape
`(batch_size, num_labels)`.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest
from pydantic import ValidationError

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    LearnedAudioAnnotations,
    LearnedAudioLabel,
    PhysicalRPE,
    RPEBundle,
    SectionMarker,
    SemanticLabel,
    SemanticRPE,
    SpectralProfile,
)
from svp_rpe.svp.generator import generate_svp


# ---------------------------------------------------------------------------
# Fake backend installation
# ---------------------------------------------------------------------------


def _install_fake_panns(
    monkeypatch: pytest.MonkeyPatch,
    *,
    label_names: list[str],
    posterior: list[float] | None = None,
    embedding_dim: int = 8,
    version: str | None = None,
) -> dict:
    """Install a fake `panns_inference` module + `.labels` submodule.

    Returns a dict that captures init kwargs and the audio batch shape so
    tests can assert on what the adapter actually sent to the upstream API.
    """
    captured: dict = {}

    if posterior is None:
        posterior = [0.0] * len(label_names)
    assert len(posterior) == len(label_names), (
        "test fake misuse: posterior and label_names must be the same length"
    )

    class FakeAudioTagging:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def inference(self, audio_batch):
            captured["batch_shape"] = tuple(np.asarray(audio_batch).shape)
            clipwise = np.asarray([posterior], dtype=np.float64)
            embedding = np.zeros((1, embedding_dim), dtype=np.float64)
            return clipwise, embedding

    fake_root = types.ModuleType("panns_inference")
    fake_root.AudioTagging = FakeAudioTagging
    if version is not None:
        fake_root.__version__ = version

    fake_labels = types.ModuleType("panns_inference.labels")
    fake_labels.labels = list(label_names)
    fake_root.labels = fake_labels

    monkeypatch.setitem(sys.modules, "panns_inference", fake_root)
    monkeypatch.setitem(sys.modules, "panns_inference.labels", fake_labels)
    return captured


def _force_panns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "panns_inference", None)
    monkeypatch.setitem(sys.modules, "panns_inference.labels", None)


# ---------------------------------------------------------------------------
# Helpers for building a baseline RPEBundle
# ---------------------------------------------------------------------------


def _make_bundle() -> RPEBundle:
    return RPEBundle(
        physical=PhysicalRPE(
            duration_sec=180.0,
            sample_rate=44100,
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
        ),
        semantic=SemanticRPE(
            por_core="bright track",
            por_surface=[
                SemanticLabel(
                    label="bright",
                    layer="perceptual",
                    confidence=0.9,
                    evidence=["brightness=0.28"],
                    source_rule="perc.brightness",
                )
            ],
            grv_anchor=GrvAnchor(primary="bass-heavy"),
            delta_e_profile=DeltaEProfile(
                transition_type="flat",
                intensity=0.3,
                description="steady",
            ),
            cultural_context=["electronic"],
            instrumentation_summary="synths",
            production_notes=["compressed"],
            confidence_notes=["rule"],
        ),
        audio_file="test.wav",
        audio_duration_sec=180.0,
        audio_sample_rate=44100,
        audio_channels=2,
        audio_format="wav",
    )


# ---------------------------------------------------------------------------
# Adapter contract tests
# ---------------------------------------------------------------------------


class TestAdapterUnavailable:
    def test_raises_with_install_hint_when_panns_missing(self, monkeypatch):
        _force_panns_unavailable(monkeypatch)

        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(LearnedModelUnavailable, match="panns_inference"):
            extract_panns_annotations(
                np.zeros(32000, dtype=np.float32), 32000, top_k=5
            )

    def test_raises_unavailable_when_labels_module_import_fails(self, monkeypatch):
        # Install AudioTagging but make the labels submodule unimportable.
        # This simulates a broken / partial install.
        class FakeAudioTagging:
            def __init__(self, **kwargs):
                pass

            def inference(self, batch):
                return np.zeros((1, 0)), np.zeros((1, 0))

        fake_root = types.ModuleType("panns_inference")
        fake_root.AudioTagging = FakeAudioTagging
        monkeypatch.setitem(sys.modules, "panns_inference", fake_root)
        monkeypatch.setitem(sys.modules, "panns_inference.labels", None)

        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(LearnedModelUnavailable):
            extract_panns_annotations(np.zeros(1024), 32000, top_k=3)


class TestAdapterIncompatible:
    """Pin that API-shape mismatches raise the more specific Incompatible error.

    LearnedModelIncompatible is a subclass of LearnedModelUnavailable so any
    existing broad catcher still works, but adapters use the child class to
    distinguish "extra installed but upstream API moved" from "extra missing".
    """

    def test_raises_incompatible_when_labels_attribute_missing(self, monkeypatch):
        # The labels submodule imports fine but doesn't expose `labels`.
        class FakeAudioTagging:
            def __init__(self, **kwargs):
                pass

            def inference(self, batch):
                return np.zeros((1, 0)), np.zeros((1, 0))

        fake_root = types.ModuleType("panns_inference")
        fake_root.AudioTagging = FakeAudioTagging
        # Note: deliberately no `labels` attribute on the labels submodule.
        fake_labels = types.ModuleType("panns_inference.labels")
        fake_root.labels = fake_labels
        monkeypatch.setitem(sys.modules, "panns_inference", fake_root)
        monkeypatch.setitem(sys.modules, "panns_inference.labels", fake_labels)

        from svp_rpe.rpe.learned import (
            LearnedModelIncompatible,
            LearnedModelUnavailable,
        )
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(LearnedModelIncompatible):
            extract_panns_annotations(np.zeros(1024), 32000, top_k=3)
        # Subclass relationship: a broad except still catches.
        assert issubclass(LearnedModelIncompatible, LearnedModelUnavailable)

    def test_raises_incompatible_when_label_count_mismatches_clipwise(
        self, monkeypatch
    ):
        # Fake returns clipwise_output with 3 columns but only 2 label names.
        # Adapter must surface this as Incompatible, not silently truncate.
        captured: dict = {}

        class FakeAudioTagging:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

            def inference(self, batch):
                return np.array([[0.1, 0.2, 0.3]]), np.zeros((1, 8))

        fake_root = types.ModuleType("panns_inference")
        fake_root.AudioTagging = FakeAudioTagging
        fake_labels = types.ModuleType("panns_inference.labels")
        fake_labels.labels = ["only_two", "labels"]  # 2, but clipwise has 3
        fake_root.labels = fake_labels
        monkeypatch.setitem(sys.modules, "panns_inference", fake_root)
        monkeypatch.setitem(sys.modules, "panns_inference.labels", fake_labels)

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(LearnedModelIncompatible, match="label count mismatch"):
            extract_panns_annotations(np.zeros(1024), 32000, top_k=2)


class TestErrorClassUnification:
    """Pin that all adapters share one LearnedModelUnavailable class."""

    def test_unified_error_class_with_beat_this_adapter(self):
        from svp_rpe.rpe.learned import LearnedModelUnavailable as Unified
        from svp_rpe.rpe.learned.beat_this_adapter import LearnedModelUnavailable as FromBeat
        from svp_rpe.rpe.learned.panns_adapter import LearnedModelUnavailable as FromPanns

        assert FromBeat is Unified
        assert FromPanns is Unified


class TestAdapterTopKDeterminism:
    def test_top_k_orders_by_confidence_desc(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["alpha", "beta", "gamma", "delta"],
            posterior=[0.10, 0.90, 0.50, 0.30],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(
            np.zeros(32000, dtype=np.float32), 32000, top_k=3
        )

        labels_in_order = [(label.label, label.confidence) for label in result.labels]
        assert labels_in_order == [
            ("beta", 0.90),
            ("gamma", 0.50),
            ("delta", 0.30),
        ]

    def test_tie_break_by_label_ascending(self, monkeypatch):
        # Two confidences are tied at 0.5. Determinism = label asc.
        _install_fake_panns(
            monkeypatch,
            label_names=["zebra", "alpha", "music"],
            posterior=[0.5, 0.5, 0.9],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(
            np.zeros(32000, dtype=np.float32), 32000, top_k=3
        )

        # Expected order: music (0.9) > alpha (0.5, "a" < "z") > zebra (0.5)
        assert [label.label for label in result.labels] == ["music", "alpha", "zebra"]

    def test_top_k_clipped_to_label_count(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["a", "b"],
            posterior=[0.1, 0.2],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(
            np.zeros(1024), 32000, top_k=10
        )
        # Top-k larger than label set just returns all labels in deterministic order.
        assert len(result.labels) == 2
        assert [label.label for label in result.labels] == ["b", "a"]

    @pytest.mark.parametrize("bad_top_k", [0, -1, -100])
    def test_non_positive_top_k_rejected(self, bad_top_k, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(ValueError, match="top_k"):
            extract_panns_annotations(
                np.zeros(1024), 32000, top_k=bad_top_k
            )

    def test_non_integer_top_k_rejected(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(ValueError, match="top_k"):
            extract_panns_annotations(
                np.zeros(1024), 32000, top_k=1.5  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("bad_top_k", [True, False])
    def test_bool_top_k_rejected(self, bad_top_k, monkeypatch):
        # bool is a subclass of int in Python — without an explicit guard,
        # top_k=True would slip through as 1 and top_k=False as 0. Both are
        # almost certainly caller bugs, not deliberate top-1 / disabled.
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(ValueError, match="top_k"):
            extract_panns_annotations(
                np.zeros(1024), 32000, top_k=bad_top_k  # type: ignore[arg-type]
            )


class TestAdapterConfidenceValidation:
    def test_out_of_range_confidence_rejected_by_pydantic(self, monkeypatch):
        # Posterior contains 1.5 — a defective fake / model bug. Adapter must
        # not silently clamp; the underlying LearnedAudioLabel validator
        # surfaces a ValidationError.
        _install_fake_panns(
            monkeypatch,
            label_names=["bad", "good"],
            posterior=[1.5, 0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(ValidationError, match="confidence"):
            extract_panns_annotations(np.zeros(1024), 32000, top_k=2)

    def test_negative_confidence_rejected_by_pydantic(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["bad", "good"],
            posterior=[-0.1, 0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(ValidationError, match="confidence"):
            extract_panns_annotations(np.zeros(1024), 32000, top_k=2)


class TestAdapterOutputShape:
    def test_label_metadata_filled_correctly(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music", "Speech"],
            posterior=[0.8, 0.2],
            version="0.1.1",
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(
            np.zeros(32000, dtype=np.float32), 32000, top_k=2
        )

        assert isinstance(result, LearnedAudioAnnotations)
        assert all(isinstance(lbl, LearnedAudioLabel) for lbl in result.labels)
        assert all(lbl.category == "audioset" for lbl in result.labels)
        assert all(
            lbl.source_model == "panns_inference:Cnn14" for lbl in result.labels
        )

    def test_records_provenance(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music"],
            posterior=[0.8],
            version="0.1.1",
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(np.zeros(32000), 32000, top_k=1)

        assert len(result.enabled_models) == 1
        info = result.enabled_models[0]
        assert info.name == "panns_inference"
        assert info.version == "0.1.1"
        assert info.provider == "qiuqiangkong/panns_inference"
        assert info.task == "tagging"
        assert info.license == "MIT"
        # Weights license intentionally NOT asserted as MIT — see policy.
        assert info.weights_license is None

        assert result.inference_config["top_k"] == 1
        assert result.inference_config["model_name"] == "Cnn14"
        assert result.inference_config["sample_rate"] == 32000
        assert result.inference_config["device"] == "cpu"
        assert result.inference_config["source"] == "panns_inference"

        # License metadata captures the code/weights asymmetry without
        # over-claiming.
        license_text = result.license_metadata["panns_inference"]
        assert "MIT" in license_text
        assert "weights" in license_text.lower()

    def test_mono_batch_shape(self, monkeypatch):
        # 1D mono signal must reach AudioTagging.inference as (1, samples).
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        extract_panns_annotations(
            np.zeros(2048, dtype=np.float32), 32000, top_k=1
        )
        assert captured["batch_shape"] == (1, 2048)

    def test_stereo_channels_first_is_downmixed(self, monkeypatch):
        # 2D (channels, samples) — typical librosa multi-channel shape.
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        stereo = np.zeros((2, 2048), dtype=np.float32)
        extract_panns_annotations(stereo, 32000, top_k=1)
        assert captured["batch_shape"] == (1, 2048)

    def test_stereo_samples_first_is_downmixed(self, monkeypatch):
        # 2D (samples, channels) — typical soundfile shape. Heuristic picks
        # the smaller axis as channels.
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        stereo = np.zeros((2048, 2), dtype=np.float32)
        extract_panns_annotations(stereo, 32000, top_k=1)
        assert captured["batch_shape"] == (1, 2048)

    def test_device_kwarg_reaches_audio_tagging(self, monkeypatch):
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(
            np.zeros(1024), 32000, top_k=1, device="cuda"
        )
        assert captured["init_kwargs"]["device"] == "cuda"
        # And the device is recorded in inference_config for provenance.
        assert result.inference_config["device"] == "cuda"

    def test_device_default_is_cpu(self, monkeypatch):
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        extract_panns_annotations(np.zeros(1024), 32000, top_k=1)
        assert captured["init_kwargs"]["device"] == "cpu"


class TestAdapterVersionDetection:
    def test_version_from_module_attribute(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
            version="0.1.1",
        )
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations
        result = extract_panns_annotations(np.zeros(1024), 32000, top_k=1)
        assert result.enabled_models[0].version == "0.1.1"

    def test_version_none_when_unavailable(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations
        result = extract_panns_annotations(np.zeros(1024), 32000, top_k=1)
        assert result.enabled_models[0].version is None
        assert result.enabled_models[0].name == "panns_inference"


# ---------------------------------------------------------------------------
# Isolation tests: panns labels stay out of rule-based RPE / SVP / scoring
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_attach_does_not_mutate_input_bundle(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music", "Speech"],
            posterior=[0.8, 0.2],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        bundle = _make_bundle()
        annotations = extract_panns_annotations(np.zeros(32000), 32000, top_k=2)
        enriched = attach_learned_annotations(bundle, annotations)

        assert bundle.learned_annotations is None
        assert enriched.learned_annotations is not None
        assert enriched is not bundle

    def test_semantic_rpe_unchanged_by_panns_attach(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music", "Speech"],
            posterior=[0.8, 0.2],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_panns_annotations(np.zeros(32000), 32000, top_k=2),
        )
        assert enriched.semantic.model_dump() == bundle.semantic.model_dump()
        assert enriched.physical.model_dump() == bundle.physical.model_dump()

    def test_generate_svp_output_identical_with_and_without_panns_labels(
        self, monkeypatch
    ):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music", "Speech", "Drum"],
            posterior=[0.8, 0.5, 0.3],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_panns_annotations(np.zeros(32000), 32000, top_k=3),
        )
        assert generate_svp(bundle).model_dump() == generate_svp(enriched).model_dump()

    def test_sentinel_label_does_not_appear_in_svp_serialization(self, monkeypatch):
        sentinel = "__PANNS_LEAK_SENTINEL_LABEL__"
        _install_fake_panns(
            monkeypatch,
            label_names=[sentinel, "ordinary"],
            posterior=[0.99, 0.5],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_panns_annotations(np.zeros(32000), 32000, top_k=2),
        )

        # The sentinel must reach learned_annotations.labels...
        assert any(
            lbl.label == sentinel
            for lbl in enriched.learned_annotations.labels
        )
        # ...but never the SVP serialization.
        svp_json = generate_svp(enriched).model_dump_json()
        assert sentinel not in svp_json

    def test_sentinel_label_does_not_leak_into_semantic_or_style(self, monkeypatch):
        sentinel = "__PANNS_STYLE_LEAK__"
        _install_fake_panns(
            monkeypatch,
            label_names=[sentinel, "x"],
            posterior=[0.9, 0.5],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_panns_annotations(np.zeros(32000), 32000, top_k=2),
        )
        svp = generate_svp(enriched)

        assert all(sentinel not in tag for tag in svp.svp_for_generation.style_tags)
        assert all(sentinel not in tag for tag in svp.analysis_rpe.por_surface)
        assert sentinel not in svp.svp_for_generation.prompt_text
        assert all(
            sentinel not in label.label
            for label in enriched.semantic.por_surface
        )


class TestSerializerRegression:
    def test_bundle_without_learned_annotations_still_omits_field_in_dump(self):
        bundle = _make_bundle()
        assert "learned_annotations" not in bundle.model_dump()

    def test_bundle_with_panns_labels_includes_field_in_dump(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music", "Speech"],
            posterior=[0.8, 0.2],
        )

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle,
            extract_panns_annotations(np.zeros(32000), 32000, top_k=2),
        )
        dumped = enriched.model_dump()
        assert "learned_annotations" in dumped
        labels = dumped["learned_annotations"]["labels"]
        assert {label["label"] for label in labels} == {"Music", "Speech"}
        assert all(label["category"] == "audioset" for label in labels)
