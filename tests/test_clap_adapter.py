"""tests/test_clap_adapter.py — laion_clap CLAP adapter tests.

The real laion_clap package is NOT required to run these tests. We
monkeypatch `sys.modules["laion_clap"]` with a fake module mirroring the
real wheel's surface area:

- `laion_clap.CLAP_Module(enable_fusion=False).load_ckpt(checkpoint)`
- `.get_audio_embedding_from_data(x=[chunk, ...], use_tensor=False)` ->
  `(n_chunks, dim)` numpy array, one row per input chunk
- `.get_text_embedding(texts, use_tensor=False)` -> `(len(texts), dim)`
  numpy array

`embed_audio_file` now decodes audio itself via `librosa.load` (see the
module's determinism-by-construction docstring) rather than delegating
decoding to laion_clap, so most tests here write small REAL WAV files
via `soundfile` — `librosa.load` cannot decode placeholder bytes.
`sys.modules["laion_clap"]` is still faked; only the audio decode is
real. Tests that only exercise "missing method" / "unavailable"
branches (which raise before `librosa.load` is ever reached) may keep
using a placeholder path/bytes.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest
import soundfile as sf

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
# WAV fixture helper
# ---------------------------------------------------------------------------


def _write_wav(path, *, seconds: float, sample_rate: int = 48000) -> None:
    """Write a tiny real mono WAV file that `librosa.load` can decode.

    A short sine burst rather than silence, so accidental all-zero
    handling elsewhere can't mask a bug.
    """
    n_samples = max(1, int(round(seconds * sample_rate)))
    t = np.linspace(0, seconds, n_samples, endpoint=False)
    y = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    sf.write(str(path), y, sample_rate)


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
    """Install a fake `laion_clap` module mirroring the real >=1.1 wheel.

    `get_audio_embedding_from_data` returns one row per input chunk: row
    `i` is `resolved_audio_vector * (i + 1)`. A single-chunk call
    therefore reproduces the exact vector callers pass in via
    `audio_vector` (keeping single-chunk assertions simple / backward
    compatible with the pre-chunking adapter), while multi-chunk calls
    get distinct, predictable rows whose mean-pooled result is easy to
    compute in assertions.
    """
    captured: dict = {}
    resolved_audio_vector = audio_vector if audio_vector is not None else [3.0, 4.0]

    class FakeCLAPModule:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = dict(kwargs)

        def load_ckpt(self, checkpoint=None):
            captured["checkpoint"] = checkpoint

        def get_audio_embedding_from_data(self, x, use_tensor=False):
            chunks = list(x)
            captured["audio_chunks"] = chunks
            captured["audio_chunk_count"] = len(chunks)
            captured["audio_use_tensor"] = use_tensor
            if audio_embedding_shape is not None:
                return np.zeros(audio_embedding_shape, dtype=np.float64)
            base = np.asarray(resolved_audio_vector, dtype=np.float64)
            rows = [base * (index + 1) for index in range(len(chunks))]
            return np.asarray(rows, dtype=np.float64)

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
        del FakeCLAPModule.get_audio_embedding_from_data
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


def _patch_tiny_clap_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scale CLAP's window/sample-rate/min-partial constants down by 1/10
    time (1/48 sample rate, 1/480 window samples), preserving the real
    480000:48000:1 window:sample-rate:min-partial ratio so chunk-count
    math stays representative while WAV fixtures stay tiny and fast.
    """
    import svp_rpe.rpe.learned.clap_adapter as clap_adapter

    monkeypatch.setattr(clap_adapter, "_CLAP_SAMPLE_RATE", 1000)
    monkeypatch.setattr(clap_adapter, "_CLAP_WINDOW_SAMPLES", 1000)
    monkeypatch.setattr(clap_adapter, "_CLAP_MIN_PARTIAL_SAMPLES", 100)


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

        # laion_clap is unavailable, so load_clap_model raises before
        # librosa.load is ever reached — placeholder bytes are fine.
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

        # `get_audio_embedding_from_data` is missing, so the hasattr
        # check raises before librosa.load is reached — the path need
        # not exist.
        with pytest.raises(LearnedModelIncompatible):
            embed_audio_file("audio.wav")

    def test_missing_text_embedding_method(self, monkeypatch):
        _install_fake_clap(monkeypatch, has_text_method=False)

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_texts, load_clap_model

        model = load_clap_model()
        with pytest.raises(LearnedModelIncompatible):
            embed_texts(["a"], model=model)

    def test_audio_embedding_wrong_ndim_rejected(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_embedding_shape=(4,))

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        with pytest.raises(LearnedModelIncompatible):
            embed_audio_file(str(audio))

    def test_audio_embedding_wrong_row_count_rejected(self, monkeypatch, tmp_path):
        # A single short chunk expects exactly 1 row back; 2 is wrong.
        _install_fake_clap(monkeypatch, audio_embedding_shape=(2, 4))

        from svp_rpe.rpe.learned import LearnedModelIncompatible
        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        with pytest.raises(LearnedModelIncompatible):
            embed_audio_file(str(audio))

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
        _write_wav(audio, seconds=0.05)
        result = embed_audio_file(str(audio))
        assert result.enabled_models[0].version == "1.1.7"

    def test_version_none_when_unavailable(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        result = embed_audio_file(str(audio))
        assert result.enabled_models[0].version is None
        assert result.enabled_models[0].name == "laion_clap"


class TestEmbedAudioFile:
    def test_populates_embedding_and_model_info(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)  # well under the 10s CLAP window -> 1 chunk
        result = embed_audio_file(str(audio), checkpoint="ckpt.pt")

        assert isinstance(result, LearnedAudioAnnotations)
        assert result.embedding is not None
        assert result.embedding.dimensions == 2
        # single chunk -> mean == that chunk; L2-normalized: [3,4]/5 = [0.6,0.8]
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
        assert result.inference_config["amodel"] is None
        assert result.inference_config["enable_fusion"] is False
        assert result.inference_config["source"] == "laion_clap"
        assert result.inference_config["sample_rate"] == 48000
        assert result.inference_config["window_samples"] == 480000
        assert result.inference_config["chunking"] == "consecutive_mean"
        assert result.inference_config["n_chunks"] == 1
        assert "laion_clap" in result.license_metadata

    def test_reuses_provided_model_without_reloading(self, monkeypatch, tmp_path):
        captured = _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file, load_clap_model

        model = load_clap_model()
        captured.pop("checkpoint", None)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        embed_audio_file(str(audio), model=model)
        # embed_audio_file(model=...) must not re-trigger load_ckpt.
        assert "checkpoint" not in captured

    def test_zero_vector_embedding_is_not_normalized_away(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[0.0, 0.0])

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        result = embed_audio_file(str(audio))
        assert result.embedding.vector == [0.0, 0.0]


class TestAmodel:
    """`amodel` selects the audio-encoder architecture; the `music_*`
    checkpoint family (e.g. `music_audioset_epoch_15_esc_90.14.pt`)
    requires `amodel="HTSAT-base"` per the upstream README — see
    `docs/learned_models_policy.md`.
    """

    def test_load_clap_model_default_omits_amodel_kwarg(self, monkeypatch):
        captured = _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import load_clap_model

        load_clap_model()
        assert captured["init_kwargs"] == {"enable_fusion": False}
        assert "amodel" not in captured["init_kwargs"]

    def test_load_clap_model_forwards_amodel(self, monkeypatch):
        captured = _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import load_clap_model

        load_clap_model(amodel="HTSAT-base")
        assert captured["init_kwargs"] == {"enable_fusion": False, "amodel": "HTSAT-base"}

    def test_embed_audio_file_records_none_amodel_by_default(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        result = embed_audio_file(str(audio))
        assert result.inference_config["amodel"] is None

    def test_embed_audio_file_forwards_and_records_amodel(self, monkeypatch, tmp_path):
        captured = _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        result = embed_audio_file(str(audio), amodel="HTSAT-base")
        assert captured["init_kwargs"] == {"enable_fusion": False, "amodel": "HTSAT-base"}
        assert result.inference_config["amodel"] == "HTSAT-base"

    def test_embed_audio_file_records_amodel_when_model_provided(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file, load_clap_model

        model = load_clap_model(amodel="HTSAT-base")
        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        # amodel is recorded for provenance even though `model` is
        # already constructed and no fresh load_clap_model call happens.
        result = embed_audio_file(str(audio), model=model, amodel="HTSAT-base")
        assert result.inference_config["amodel"] == "HTSAT-base"


class TestDeterministicChunking:
    """Chunk-count math for `_chunk_waveform`, exercised end-to-end
    through `embed_audio_file` (real `librosa.load` decode, not
    monkeypatched) against tiny real WAV fixtures. See
    `_patch_tiny_clap_constants` for the scaling rationale.
    """

    def test_full_windows_plus_kept_partial(self, monkeypatch, tmp_path):
        _patch_tiny_clap_constants(monkeypatch)
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        # 2.5s @ 1000Hz (scaled 10s CLAP window -> 1000 samples) ->
        # windows of 1000 + 1000 + 500 samples; 500 >= min-partial (100)
        # so the trailing partial window is kept -> 3 chunks.
        _write_wav(audio, seconds=2.5, sample_rate=1000)
        result = embed_audio_file(str(audio))
        assert result.inference_config["n_chunks"] == 3

    def test_short_trailing_partial_is_dropped(self, monkeypatch, tmp_path):
        _patch_tiny_clap_constants(monkeypatch)
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        # 1.05s @ 1000Hz -> windows of 1000 + 50 samples; 50 < the
        # min-partial (100) threshold and there is a preceding full
        # chunk, so the trailing partial is dropped -> 1 chunk.
        _write_wav(audio, seconds=1.05, sample_rate=1000)
        result = embed_audio_file(str(audio))
        assert result.inference_config["n_chunks"] == 1

    def test_lone_short_chunk_is_kept(self, monkeypatch, tmp_path):
        # Default (unscaled) constants: a track far shorter than the
        # CLAP window is still embedded as a single chunk — the
        # "drop if short" rule only applies to a TRAILING partial
        # window that follows at least one full window.
        _install_fake_clap(monkeypatch)

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.6)
        result = embed_audio_file(str(audio))
        assert result.inference_config["n_chunks"] == 1


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
        _write_wav(audio, seconds=0.05)
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
        _write_wav(audio, seconds=0.05)
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
        _write_wav(audio, seconds=0.05)
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
        _write_wav(audio, seconds=0.05)
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
        _write_wav(audio, seconds=0.05)
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
        _write_wav(audio, seconds=0.05)
        bundle = _make_bundle()
        enriched = attach_learned_annotations(
            bundle, embed_audio_file(str(audio), checkpoint=sentinel)
        )
        svp = generate_svp(enriched)

        assert all(sentinel not in tag for tag in svp.svp_for_generation.style_tags)
        assert all(sentinel not in tag for tag in svp.analysis_rpe.por_surface)
        assert sentinel not in svp.svp_for_generation.prompt_text
        assert all(sentinel not in label.label for label in enriched.semantic.por_surface)
