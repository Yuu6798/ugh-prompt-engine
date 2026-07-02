"""tests/test_clap_adapter.py — laion_clap CLAP adapter tests.

The real laion_clap package is NOT required to run these tests. We
monkeypatch `sys.modules["laion_clap"]` with a fake module mirroring the
real wheel's surface area:

- `laion_clap.CLAP_Module(enable_fusion=False).load_ckpt(checkpoint)`
- `.get_audio_embedding_from_filelist(paths, use_tensor=False)` ->
  `(1, dim)` numpy array
- `.get_text_embedding(texts, use_tensor=False)` -> `(len(texts), dim)`
  numpy array
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from svp_rpe.rpe.models import (
    DeltaEProfile,
    GrvAnchor,
    LearnedAudioAnnotations,
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


def _install_fake_clap(
    monkeypatch: pytest.MonkeyPatch,
    *,
    audio_vector: list[float] | None = None,
    text_vectors: dict[str, list[float]] | None = None,
    version: str | None = None,
    audio_embedding_shape: tuple[int, ...] | None = None,
    text_embedding_row_count: int | None = None,
    has_clap_module: bool = True,
    has_load_ckpt: bool = True,
    has_audio_method: bool = True,
    has_text_method: bool = True,
) -> dict:
    """Install a fake `laion_clap` module mirroring the real >=1.1 wheel."""
    captured: dict = {}
    resolved_audio_vector = audio_vector if audio_vector is not None else [3.0, 4.0]

    class FakeCLAPModule:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def load_ckpt(self, checkpoint=None):
            captured["checkpoint"] = checkpoint

        def get_audio_embedding_from_filelist(self, paths, use_tensor=False):
            captured["audio_paths"] = list(paths)
            captured["audio_use_tensor"] = use_tensor
            if audio_embedding_shape is not None:
                return np.zeros(audio_embedding_shape, dtype=np.float64)
            return np.asarray([resolved_audio_vector], dtype=np.float64)

        def get_text_embedding(self, texts, use_tensor=False):
            captured["texts"] = list(texts)
            captured["text_use_tensor"] = use_tensor
            if text_embedding_row_count is not None:
                return np.zeros((text_embedding_row_count, 4), dtype=np.float64)
            vectors = text_vectors or {}
            return np.asarray(
                [vectors.get(text, [1.0, 0.0]) for text in texts], dtype=np.float64
            )

    if not has_load_ckpt:
        del FakeCLAPModule.load_ckpt
    if not has_audio_method:
        del FakeCLAPModule.get_audio_embedding_from_filelist
    if not has_text_method:
        del FakeCLAPModule.get_text_embedding

    fake_root = types.ModuleType("laion_clap")
    if has_clap_module:
        fake_root.CLAP_Module = FakeCLAPModule
    if version is not None:
        fake_root.__version__ = version

    monkeypatch.setitem(sys.modules, "laion_clap", fake_root)
    return captured


def _force_clap_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "laion_clap", None)


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
    def test_load_clap_model_raises_with_install_hint(self, monkeypatch):
        _force_clap_unavailable(monkeypatch)

        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.clap_adapter import load_clap_model

        with pytest.raises(LearnedModelUnavailable, match="laion_clap"):
            load_clap_model()

    def test_embed_audio_file_raises_with_install_hint(self, monkeypatch, tmp_path):
        _force_clap_unavailable(monkeypatch)

        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        with pytest.raises(LearnedModelUnavailable, match="semantic-embed"):
            embed_audio_file(str(audio))

    def test_embed_texts_raises_with_install_hint(self, monkeypatch):
        _force_clap_unavailable(monkeypatch)

        from svp_rpe.rpe.learned import LearnedModelUnavailable
        from svp_rpe.rpe.learned.clap_adapter import embed_texts

        with pytest.raises(LearnedModelUnavailable, match="semantic-embed"):
            embed_texts(["bright"])


class TestAdapterIncompatible:
    """Pin that API-shape mismatches raise the more specific Incompatible error."""

    def test_missing_clap_module_class(self, monkeypatch):
        _install_fake_clap(monkeypatch, has_clap_module=False)

        from svp_rpe.rpe.learned import LearnedModelIncompatible, LearnedModelUnavailable
        from svp_rpe.rpe.learned.clap_adapter import load_clap_model

        with pytest.raises(LearnedModelIncompatible):
            load_clap_model()
        assert issubclass(LearnedModelIncompatible, LearnedModelUnavailable)

    def test_missing_load_ckpt(self, monkeypatch):
        _install_fake_clap(monkeypatch, has_load_ckpt=False)

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import load_clap_model

        with pytest.raises(LearnedModelIncompatible):
            load_clap_model()

    def test_missing_audio_embedding_method(self, monkeypatch):
        _install_fake_clap(monkeypatch, has_audio_method=False)

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        with pytest.raises(LearnedModelIncompatible):
            embed_audio_file("audio.wav")

    def test_missing_text_embedding_method(self, monkeypatch):
        _install_fake_clap(monkeypatch, has_text_method=False)

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_texts, load_clap_model

        model = load_clap_model()
        with pytest.raises(LearnedModelIncompatible):
            embed_texts(["a"], model=model)

    def test_audio_embedding_wrong_ndim_rejected(self, monkeypatch):
        _install_fake_clap(monkeypatch, audio_embedding_shape=(4,))

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        with pytest.raises(LearnedModelIncompatible):
            embed_audio_file("audio.wav")

    def test_audio_embedding_wrong_row_count_rejected(self, monkeypatch):
        _install_fake_clap(monkeypatch, audio_embedding_shape=(2, 4))

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        with pytest.raises(LearnedModelIncompatible):
            embed_audio_file("audio.wav")

    def test_text_embedding_wrong_row_count_rejected(self, monkeypatch):
        _install_fake_clap(monkeypatch, text_embedding_row_count=1)

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_texts, load_clap_model

        model = load_clap_model()
        with pytest.raises(LearnedModelIncompatible):
            embed_texts(["a", "b"], model=model)


class TestErrorClassUnification:
    """Pin that all adapters share one LearnedModelUnavailable class."""

    def test_unified_error_class_with_panns_adapter(self):
        from svp_rpe.rpe.learned import LearnedModelUnavailable as Unified
        from svp_rpe.rpe.learned.clap_adapter import LearnedModelUnavailable as FromClap
        from svp_rpe.rpe.learned.panns_adapter import LearnedModelUnavailable as FromPanns

        assert FromClap is Unified
        assert FromPanns is Unified


class TestVersionDetection:
    def test_version_from_module_attribute(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, version="1.1.7")

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        result = embed_audio_file(str(audio))
        assert result.enabled_models[0].version == "1.1.7"

    def test_version_none_when_unavailable(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        result = embed_audio_file(str(audio))
        assert result.enabled_models[0].version is None
        assert result.enabled_models[0].name == "laion_clap"


class TestEmbedAudioFile:
    def test_populates_embedding_and_model_info(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        result = embed_audio_file(str(audio), checkpoint="ckpt.pt")

        assert isinstance(result, LearnedAudioAnnotations)
        assert result.embedding is not None
        assert result.embedding.dimensions == 2
        # L2-normalized: [3, 4] / 5 = [0.6, 0.8]
        assert result.embedding.vector == pytest.approx([0.6, 0.8])
        assert result.embedding.source_model == "laion_clap:CLAP_Module"

        assert len(result.enabled_models) == 1
        info = result.enabled_models[0]
        assert info.name == "laion_clap"
        assert info.task == "embedding"
        assert info.provider == "LAION-AI/CLAP"
        assert info.license is not None
        assert info.weights_license is not None

        assert result.inference_config["checkpoint"] == "ckpt.pt"
        assert result.inference_config["enable_fusion"] is False
        assert result.inference_config["source"] == "laion_clap"
        assert "laion_clap" in result.license_metadata

    def test_reuses_provided_model_without_reloading(self, monkeypatch, tmp_path):
        captured = _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file, load_clap_model

        model = load_clap_model()
        captured.pop("checkpoint", None)

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        embed_audio_file(str(audio), model=model)
        # embed_audio_file(model=...) must not re-trigger load_ckpt.
        assert "checkpoint" not in captured

    def test_zero_vector_embedding_is_not_normalized_away(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[0.0, 0.0])

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        result = embed_audio_file(str(audio))
        assert result.embedding.vector == [0.0, 0.0]


class TestEmbedTexts:
    def test_returns_l2_normalized_vectors(self, monkeypatch):
        _install_fake_clap(
            monkeypatch,
            text_vectors={"bright": [3.0, 4.0], "dark": [0.0, 2.0]},
        )

        from svp_rpe.rpe.learned.clap_adapter import embed_texts, load_clap_model

        model = load_clap_model()
        vectors = embed_texts(["bright", "dark"], model=model)
        assert vectors[0] == pytest.approx([0.6, 0.8])
        assert vectors[1] == pytest.approx([0.0, 1.0])

    def test_loads_default_model_when_none_provided(self, monkeypatch):
        captured = _install_fake_clap(monkeypatch, text_vectors={"x": [1.0, 0.0]})

        from svp_rpe.rpe.learned.clap_adapter import embed_texts

        embed_texts(["x"])
        assert captured["texts"] == ["x"]


class TestSerializerRegression:
    def test_bundle_without_learned_annotations_omits_field(self):
        bundle = _make_bundle()
        assert "learned_annotations" not in bundle.model_dump()

    def test_bundle_with_clap_embedding_includes_field(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        annotations = embed_audio_file(str(audio))
        bundle = attach_learned_annotations(_make_bundle(), annotations)
        dumped = bundle.model_dump()
        assert "learned_annotations" in dumped
        assert dumped["learned_annotations"]["embedding"]["dimensions"] == 2


# ---------------------------------------------------------------------------
# Isolation tests: CLAP embeddings stay out of rule-based RPE / SVP / scoring
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_attach_does_not_mutate_input_bundle(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        bundle = _make_bundle()
        annotations = embed_audio_file(str(audio))
        enriched = attach_learned_annotations(bundle, annotations)

        assert bundle.learned_annotations is None
        assert enriched.learned_annotations is not None
        assert enriched is not bundle

    def test_semantic_rpe_unchanged_by_clap_attach(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        bundle = _make_bundle()
        enriched = attach_learned_annotations(bundle, embed_audio_file(str(audio)))
        assert enriched.semantic.model_dump() == bundle.semantic.model_dump()
        assert enriched.physical.model_dump() == bundle.physical.model_dump()

    def test_generate_svp_output_identical_with_and_without_clap_embedding(
        self, monkeypatch, tmp_path
    ):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        bundle = _make_bundle()
        enriched = attach_learned_annotations(bundle, embed_audio_file(str(audio)))
        assert generate_svp(bundle).model_dump() == generate_svp(enriched).model_dump()

    def test_sentinel_checkpoint_does_not_leak_into_svp_serialization(
        self, monkeypatch, tmp_path
    ):
        sentinel = "__CLAP_LEAK_SENTINEL__"
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle, embed_audio_file(str(audio), checkpoint=sentinel)
        )

        assert enriched.learned_annotations.inference_config["checkpoint"] == sentinel
        svp_json = generate_svp(enriched).model_dump_json()
        assert sentinel not in svp_json

    def test_sentinel_does_not_leak_into_semantic_or_style(self, monkeypatch, tmp_path):
        sentinel = "__CLAP_STYLE_LEAK__"
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned import attach_learned_annotations
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        audio.write_bytes(b"fake-audio")
        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle, embed_audio_file(str(audio), checkpoint=sentinel)
        )
        svp = generate_svp(enriched)

        assert all(sentinel not in tag for tag in svp.svp_for_generation.style_tags)
        assert all(sentinel not in tag for tag in svp.analysis_rpe.por_surface)
        assert sentinel not in svp.svp_for_generation.prompt_text
        assert all(sentinel not in label.label for label in enriched.semantic.por_surface)
