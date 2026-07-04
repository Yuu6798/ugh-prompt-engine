"""tests/test_semantic_axes.py — CLAP extraction-stage semantic-axis sensor tests.

Reuses the fake `laion_clap` backend from `tests.test_clap_adapter`
(`_install_fake_clap` / `_write_wav` / `_make_bundle`) rather than
duplicating it — see that module's docstring for the fake's contract.
"""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import svp_rpe.rpe.extractor as extractor
from svp_rpe.cli import app
from svp_rpe.rpe.learned import LearnedModelUnavailable, attach_learned_annotations
from svp_rpe.rpe.learned.clap_adapter import embed_audio_file, embed_texts, load_clap_model
from svp_rpe.rpe.learned.similarity import contrast_fit
from svp_rpe.rpe.models import LearnedSemanticAxis
from svp_rpe.svp.generator import generate_svp
from tests.test_clap_adapter import _force_clap_unavailable, _install_fake_clap, _make_bundle, _write_wav

runner = CliRunner()

_VOCAL_TEXT_VECTORS = {
    "a song with lead vocals": [1.0, 0.0],
    "singing with lyrics": [1.0, 0.0],
    "an instrumental track with no vocals": [0.0, 1.0],
    "instrumental music": [0.0, 1.0],
}


class TestExtractClapSemanticAxes:
    def test_default_battery_returns_five_axes(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        result = extract_clap_semantic_axes(str(audio))

        assert result.embedding is not None
        assert len(result.semantic_axes) == 5
        names = {axis.axis for axis in result.semantic_axes}
        assert names == {"vocal_presence", "brightness", "energy", "acousticness", "warmth"}
        for axis in result.semantic_axes:
            assert isinstance(axis, LearnedSemanticAxis)
            assert isinstance(axis.contrast_fit, float)

    def test_vocal_presence_contrast_fit_matches_direct_computation(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        result = extract_clap_semantic_axes(str(audio))

        vocal_axis = next(a for a in result.semantic_axes if a.axis == "vocal_presence")

        # Recompute independently against the same fake backend for a known value.
        model = load_clap_model()
        audio_annotations = embed_audio_file(str(audio), model=model)
        audio_vec = audio_annotations.embedding.vector
        pos_emb = embed_texts(
            ["a song with lead vocals", "singing with lyrics"], model=model
        )
        neg_emb = embed_texts(
            ["an instrumental track with no vocals", "instrumental music"], model=model
        )
        expected = round(contrast_fit(audio_vec, pos_emb, neg_emb), 6)

        assert vocal_axis.contrast_fit == pytest.approx(expected)
        # Pin the concrete value given the fixed fake vectors above:
        # audio=[0.6,0.8], positive probes normalize to [1,0], negative to [0,1]
        # -> mean(cos)=0.6 for positive, 0.8 for negative -> contrast = -0.2
        assert vocal_axis.contrast_fit == pytest.approx(-0.2)
        assert vocal_axis.positive_probes == ["a song with lead vocals", "singing with lyrics"]
        assert vocal_axis.negative_probes == [
            "an instrumental track with no vocals",
            "instrumental music",
        ]
        assert vocal_axis.source_model == "laion_clap:CLAP_Module"

    def test_custom_axes_override_battery(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        custom_axes = [
            {
                "name": "custom_axis",
                "positive": ["a song with lead vocals"],
                "negative": ["instrumental music"],
            }
        ]
        result = extract_clap_semantic_axes(str(audio), axes=custom_axes)

        assert len(result.semantic_axes) == 1
        assert result.semantic_axes[0].axis == "custom_axis"
        assert result.inference_config["n_semantic_axes"] == 1

    def test_inference_config_augmented_with_axes_metadata(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        result = extract_clap_semantic_axes(str(audio))

        assert result.inference_config["n_semantic_axes"] == 5
        assert result.inference_config["semantic_axes_config_version"] == "1.0"
        # original embed_audio_file inference_config keys must survive the augmentation
        assert result.inference_config["source"] == "laion_clap"

    def test_unavailable_raises_with_semantic_embed_hint(self, monkeypatch):
        _force_clap_unavailable(monkeypatch)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        with pytest.raises(LearnedModelUnavailable, match="semantic-embed"):
            extract_clap_semantic_axes("x.wav")


class TestLoadSemanticAxes:
    def test_returns_five_axes_with_required_keys(self):
        from svp_rpe.rpe.learned.semantic_axes import load_semantic_axes

        axes = load_semantic_axes()
        assert len(axes) == 5
        for axis in axes:
            assert axis["name"]
            assert axis["positive"]
            assert axis["negative"]

    def test_rejects_axis_missing_name(self, monkeypatch):
        import svp_rpe.rpe.learned.semantic_axes as semantic_axes_module

        monkeypatch.setattr(
            semantic_axes_module,
            "load_config",
            lambda name: {"axes": [{"positive": ["a"], "negative": ["b"]}]},
        )
        with pytest.raises(ValueError, match="name"):
            semantic_axes_module.load_semantic_axes()


class TestIsolation:
    def test_attach_leaves_semantic_and_physical_unchanged(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        bundle = _make_bundle()
        annotations = extract_clap_semantic_axes(str(audio))
        enriched = attach_learned_annotations(bundle, annotations)

        assert enriched.semantic.model_dump() == bundle.semantic.model_dump()
        assert enriched.physical.model_dump() == bundle.physical.model_dump()

    def test_generate_svp_output_identical_with_and_without_semantic_axes(
        self, monkeypatch, tmp_path
    ):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        bundle = _make_bundle()
        enriched = attach_learned_annotations(bundle, extract_clap_semantic_axes(str(audio)))

        assert generate_svp(bundle).model_dump() == generate_svp(enriched).model_dump()


class TestSerializer:
    def test_bundle_with_semantic_axes_serializes(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        bundle = attach_learned_annotations(_make_bundle(), extract_clap_semantic_axes(str(audio)))
        dumped = bundle.model_dump()

        assert "learned_annotations" in dumped
        assert dumped["learned_annotations"]["semantic_axes"][0]["axis"]
        assert dumped["learned_annotations"]["inference_config"]["n_semantic_axes"] == 5


class TestCli:
    def test_extract_with_clap_semantic_flag_attaches_semantic_axes(
        self, monkeypatch, tmp_path
    ):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)
        monkeypatch.setattr(extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle())

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        output = tmp_path / "out.json"

        result = runner.invoke(
            app, ["extract", str(audio), "--clap-semantic", "-o", str(output)]
        )

        assert result.exit_code == 0, result.stdout
        dumped = json.loads(output.read_text(encoding="utf-8"))
        assert "learned_annotations" in dumped
        assert len(dumped["learned_annotations"]["semantic_axes"]) == 5

    def test_extract_without_clap_semantic_flag_omits_learned_annotations(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle())

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        output = tmp_path / "out.json"

        result = runner.invoke(app, ["extract", str(audio), "-o", str(output)])

        assert result.exit_code == 0, result.stdout
        dumped = json.loads(output.read_text(encoding="utf-8"))
        assert "learned_annotations" not in dumped

    def test_extract_clap_semantic_unavailable_fails_fast_before_extraction(
        self, monkeypatch, tmp_path
    ):
        # When the semantic-embed extra is absent, --clap-semantic must fail
        # fast BEFORE the base extraction runs (Codex P2), and the install hint
        # must survive Rich markup (`.[semantic-embed]` is not parsed as a tag).
        _force_clap_unavailable(monkeypatch)
        calls: list[str] = []

        def _recording_extract(path, **kwargs):
            calls.append(path)
            return _make_bundle()

        monkeypatch.setattr(extractor, "extract_rpe_from_file", _recording_extract)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)

        result = runner.invoke(app, ["extract", str(audio), "--clap-semantic"])

        assert result.exit_code == 1
        assert calls == []  # base extraction never ran
        assert '.[semantic-embed]' in result.stdout
