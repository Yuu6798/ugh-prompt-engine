"""tests/test_panns_adapter.py — panns_inference adapter tests.

The real panns_inference package is NOT required to run these tests. We
monkeypatch `sys.modules["panns_inference"]` with a fake module that mirrors
the real wheel's surface area:

- `panns_inference.AudioTagging(checkpoint_path=None, device=...).inference(batch)`
  returns `(clipwise_output, embedding)`.
- `panns_inference.labels` is an *attribute* on the root module — a list,
  re-exported from `panns_inference.config`. It is NOT a submodule.

Tests can opt into the config-fallback path or the unavailable / incompat
paths via the `labels_source` kwarg on `_install_fake_panns`.
"""
from __future__ import annotations

import sys
import types
from typing import Literal

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
    labels_source: Literal["root", "config", "none"] = "root",
) -> dict:
    """Install a fake `panns_inference` module mirroring the real 0.1.x wheel.

    The real wheel exposes `labels` as an *attribute* on the root module
    (re-exported from `panns_inference.config`); it is NOT a submodule. Tests
    default to this layout. `labels_source="config"` simulates a future
    upstream that drops the root re-export but keeps the config submodule.
    `"none"` simulates an incompatible install where the labels list is gone
    entirely.
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

    if labels_source == "root":
        fake_root.labels = list(label_names)
    elif labels_source == "config":
        fake_config = types.ModuleType("panns_inference.config")
        fake_config.labels = list(label_names)
        fake_root.config = fake_config
        monkeypatch.setitem(sys.modules, "panns_inference.config", fake_config)
    elif labels_source == "none":
        # Don't expose labels anywhere — simulates incompatible upstream.
        # Also block the config-submodule fallback so the adapter raises.
        monkeypatch.setitem(sys.modules, "panns_inference.config", None)

    monkeypatch.setitem(sys.modules, "panns_inference", fake_root)
    return captured


def _force_panns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # A None entry in sys.modules makes import_module raise ImportError per
    # documented Python semantics. We don't touch panns_inference.labels
    # because it isn't a submodule in the real wheel.
    monkeypatch.setitem(sys.modules, "panns_inference", None)


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


class TestAdapterIncompatible:
    """Pin that API-shape mismatches raise the more specific Incompatible error.

    LearnedModelIncompatible is a subclass of LearnedModelUnavailable so any
    existing broad catcher still works, but adapters use the child class to
    distinguish "extra installed but upstream API moved" from "extra missing".
    """

    def test_raises_incompatible_when_labels_attribute_missing_everywhere(
        self, monkeypatch
    ):
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
            labels_source="none",
        )

        from svp_rpe.rpe.learned import (
            LearnedModelIncompatible,
            LearnedModelUnavailable,
        )
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(LearnedModelIncompatible):
            extract_panns_annotations(np.zeros(1024), 32000, top_k=1)
        # Subclass relationship: a broad except still catches.
        assert issubclass(LearnedModelIncompatible, LearnedModelUnavailable)

    def test_raises_incompatible_when_label_count_mismatches_clipwise(
        self, monkeypatch
    ):
        # 3 columns of clipwise output, only 2 label names — adapter must
        # surface this as Incompatible, not silently truncate.
        _install_fake_panns(
            monkeypatch,
            label_names=["only_two", "labels"],
            posterior=[0.1, 0.2],
        )
        # Override the fake's inference to emit a 3-wide clipwise. We do this
        # inline because _install_fake_panns assumes len(posterior) == len(labels).
        fake_root = sys.modules["panns_inference"]

        class WideAudioTagging:
            def __init__(self, **kwargs):
                pass

            def inference(self, batch):
                return np.array([[0.1, 0.2, 0.3]]), np.zeros((1, 8))

        fake_root.AudioTagging = WideAudioTagging

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        with pytest.raises(LearnedModelIncompatible, match="label count mismatch"):
            extract_panns_annotations(np.zeros(1024), 32000, top_k=2)


class TestAdapterLabelsLoading:
    """Pin the labels-loading path against the real upstream layout.

    panns_inference 0.1.x exposes `labels` as a root attribute, with the
    underlying definition in `panns_inference.config`. The adapter must
    work for both shapes so a future re-export change doesn't break it.
    """

    def test_labels_read_from_root_attribute_default(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music", "Speech"],
            posterior=[0.8, 0.2],
            labels_source="root",
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(np.zeros(32000), 32000, top_k=2)
        assert {lbl.label for lbl in result.labels} == {"Music", "Speech"}

    def test_labels_fall_back_to_config_submodule(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["Music", "Speech"],
            posterior=[0.8, 0.2],
            labels_source="config",
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(np.zeros(32000), 32000, top_k=2)
        assert {lbl.label for lbl in result.labels} == {"Music", "Speech"}

    def test_no_panns_inference_dot_labels_submodule_is_imported(self, monkeypatch):
        # Real wheel has no submodule at this path. The adapter must not
        # leave a sys.modules entry there even after a successful call.
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
            labels_source="root",
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        # Sanity: confirm no stale fake submodule is set by the helper.
        assert "panns_inference.labels" not in sys.modules

        extract_panns_annotations(np.zeros(1024), 32000, top_k=1)

        # Adapter did not import a non-existent submodule.
        assert "panns_inference.labels" not in sys.modules


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
        _install_fake_panns(
            monkeypatch,
            label_names=["zebra", "alpha", "music"],
            posterior=[0.5, 0.5, 0.9],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(
            np.zeros(32000, dtype=np.float32), 32000, top_k=3
        )

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


class TestAdapterResample:
    """Pin that audio is resampled to 32 kHz before reaching AudioTagging.

    Cnn14 is hard-coded to expect 32 kHz mono. Without resampling, callers
    passing 44.1 / 48 kHz audio would feed an out-of-distribution signal
    silently. The adapter records both the input sample_rate and the
    target_sample_rate in inference_config for provenance.
    """

    def test_resamples_when_sample_rate_differs_from_32000(self, monkeypatch):
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        resample_calls: list[dict] = []

        def fake_resample(y, *, orig_sr, target_sr, **_kwargs):
            resample_calls.append(
                {"shape": tuple(y.shape), "orig_sr": orig_sr, "target_sr": target_sr}
            )
            new_len = int(round(len(y) * target_sr / orig_sr))
            return np.zeros(new_len, dtype=y.dtype)

        import librosa

        monkeypatch.setattr(librosa, "resample", fake_resample)

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        extract_panns_annotations(
            np.zeros(44100, dtype=np.float32), 44100, top_k=1
        )

        assert len(resample_calls) == 1
        assert resample_calls[0]["orig_sr"] == 44100
        assert resample_calls[0]["target_sr"] == 32000
        # Batch reaching AudioTagging.inference is the resampled signal.
        assert captured["batch_shape"] == (1, 32000)

    def test_no_resample_when_sample_rate_already_32000(self, monkeypatch):
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        resample_calls: list = []

        def fake_resample(*args, **kwargs):
            resample_calls.append((args, kwargs))
            return args[0]

        import librosa

        monkeypatch.setattr(librosa, "resample", fake_resample)

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        extract_panns_annotations(
            np.zeros(32000, dtype=np.float32), 32000, top_k=1
        )

        assert resample_calls == []
        assert captured["batch_shape"] == (1, 32000)

    def test_stereo_input_is_downmixed_then_resampled(self, monkeypatch):
        # Order of operations: stereo -> mono -> resample -> batch.
        captured = _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        resample_calls: list[dict] = []

        def fake_resample(y, *, orig_sr, target_sr, **_kwargs):
            resample_calls.append(
                {"shape": tuple(y.shape), "orig_sr": orig_sr, "target_sr": target_sr}
            )
            new_len = int(round(len(y) * target_sr / orig_sr))
            return np.zeros(new_len, dtype=y.dtype)

        import librosa

        monkeypatch.setattr(librosa, "resample", fake_resample)

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        stereo = np.zeros((2, 44100), dtype=np.float32)
        extract_panns_annotations(stereo, 44100, top_k=1)

        # Resample sees the mono-downmixed 1D signal, NOT the stereo array.
        assert resample_calls[0]["shape"] == (44100,)
        assert captured["batch_shape"] == (1, 32000)

    def test_target_sample_rate_recorded_in_provenance(self, monkeypatch):
        _install_fake_panns(
            monkeypatch,
            label_names=["a"],
            posterior=[0.5],
        )

        from svp_rpe.rpe.learned.panns_adapter import extract_panns_annotations

        result = extract_panns_annotations(np.zeros(32000), 32000, top_k=1)

        # Both the caller-supplied rate and the rate fed to the model are
        # recorded — the latter is the one the model actually saw.
        assert result.inference_config["sample_rate"] == 32000
        assert result.inference_config["target_sample_rate"] == 32000


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
        assert info.weights_license is None

        assert result.inference_config["top_k"] == 1
        assert result.inference_config["model_name"] == "Cnn14"
        assert result.inference_config["sample_rate"] == 32000
        assert result.inference_config["target_sample_rate"] == 32000
        assert result.inference_config["device"] == "cpu"
        assert result.inference_config["source"] == "panns_inference"

        license_text = result.license_metadata["panns_inference"]
        assert "MIT" in license_text
        assert "weights" in license_text.lower()

    def test_mono_batch_shape(self, monkeypatch):
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

        assert any(
            lbl.label == sentinel
            for lbl in enriched.learned_annotations.labels
        )
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
