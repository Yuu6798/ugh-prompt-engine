"""tests/test_semantic_section_axes.py — CLAP section-level ("emotional arc") sensor tests.

Reuses the fake `laion_clap` backend (`_install_fake_clap` / `_write_wav` /
`_make_bundle` / `_force_clap_unavailable`) from `tests.test_clap_adapter`
rather than duplicating it — see that module's docstring for the fake's
contract. `_install_fake_clap`'s shared `audio_vector` fake produces a
mean-pooled embedding that is always a positive scalar multiple of
`audio_vector` regardless of chunk count, so it L2-normalizes to the same
direction no matter how a waveform is sliced — good for structural
assertions (axis count, section labels, serialization), but useless for
proving `embed_audio_segments` actually reads different segment content.
For that one property this module installs its own tiny length-aware fake
(`_install_length_aware_fake_clap`) whose output is tied to the real
per-chunk sample count, so two differently-sized segments are guaranteed
(deterministically, not by floating-point coincidence) to embed to
different directions.
"""
from __future__ import annotations

import json
import sys
import types

import numpy as np
import pytest
from typer.testing import CliRunner

import svp_rpe.rpe.extractor as extractor
from svp_rpe.cli import app
from svp_rpe.rpe.learned import LearnedModelUnavailable, attach_learned_annotations
from svp_rpe.rpe.learned.clap_adapter import embed_audio_segments
from svp_rpe.rpe.models import LearnedSemanticAxis, LearnedSemanticSection, SectionMarker
from svp_rpe.svp.generator import generate_svp
from tests.test_clap_adapter import (
    _force_clap_unavailable,
    _install_fake_clap,
    _make_bundle,
    _write_wav,
)

runner = CliRunner()


def _install_length_aware_fake_clap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake `laion_clap` whose audio embedding depends on the
    ACTUAL per-chunk sample count (`[n_samples, index + 1]` per row), unlike
    `_install_fake_clap`'s scalar-multiple-of-a-fixed-vector fake. Used only
    to prove `embed_audio_segments` slices real, differently-sized regions
    of the shared waveform rather than always embedding the same content.
    """

    class FakeCLAPModule:
        def __init__(self, **kwargs):
            pass

        def load_ckpt(self, checkpoint=None):
            pass

        def get_audio_embedding_from_data(self, x, use_tensor=False):
            chunks = list(x)
            rows = [[float(chunk.shape[0]), float(index + 1)] for index, chunk in enumerate(chunks)]
            return np.asarray(rows, dtype=np.float64)

        def get_text_embedding(self, texts, use_tensor=False):
            return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float64)

    fake_root = types.ModuleType("laion_clap")
    fake_root.CLAP_Module = FakeCLAPModule
    monkeypatch.setitem(sys.modules, "laion_clap", fake_root)

    from svp_rpe.rpe.learned import clap_adapter

    def _package_not_found(name: str) -> str:
        raise clap_adapter._pkg_metadata.PackageNotFoundError(name)

    monkeypatch.setattr(clap_adapter._pkg_metadata, "version", _package_not_found)


def _make_multi_section_bundle():
    """A bundle whose `structure` has two contiguous SectionMarkers
    (intro/verse) instead of `_make_bundle`'s single whole-track section —
    exercises the section-level sensor's per-section loop.
    """
    bundle = _make_bundle()
    return bundle.model_copy(
        update={
            "physical": bundle.physical.model_copy(
                update={
                    "structure": [
                        SectionMarker(label="intro", start_sec=0.0, end_sec=1.0),
                        SectionMarker(label="verse", start_sec=1.0, end_sec=2.5),
                    ]
                }
            )
        }
    )


# ---------------------------------------------------------------------------
# embed_audio_segments
# ---------------------------------------------------------------------------


class TestEmbedAudioSegments:
    def test_returns_one_vector_per_segment(self, monkeypatch, tmp_path):
        _install_length_aware_fake_clap(monkeypatch)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        vectors = embed_audio_segments(str(audio), [(0.0, 1.0), (1.0, 2.5)])

        assert len(vectors) == 2
        assert all(isinstance(vector, list) for vector in vectors)
        assert all(len(vector) == 2 for vector in vectors)

    def test_different_length_segments_yield_different_vectors(self, monkeypatch, tmp_path):
        _install_length_aware_fake_clap(monkeypatch)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        vectors = embed_audio_segments(str(audio), [(0.0, 1.0), (1.0, 2.5)])

        assert vectors[0] != vectors[1]

    def test_reproduces_embed_audio_file_for_whole_track_segment(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.clap_adapter import embed_audio_file

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)  # single chunk, well under the CLAP window
        whole_track = embed_audio_file(str(audio))
        [segment_vector] = embed_audio_segments(str(audio), [(0.0, 0.05)])

        assert segment_vector == pytest.approx(whole_track.embedding.vector)

    def test_degenerate_slice_does_not_crash(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.5)
        # (1.0, 0.5): end <= start. (10.0, 20.0): entirely past the 0.5s track.
        vectors = embed_audio_segments(str(audio), [(1.0, 0.5), (10.0, 20.0)])

        assert len(vectors) == 2
        assert all(len(vector) == 2 for vector in vectors)

    def test_unavailable_raises_with_semantic_embed_hint(self, monkeypatch):
        _force_clap_unavailable(monkeypatch)

        with pytest.raises(LearnedModelUnavailable, match="semantic-embed"):
            embed_audio_segments("x.wav", [(0.0, 1.0)])


# ---------------------------------------------------------------------------
# extract_clap_semantic_section_axes
# ---------------------------------------------------------------------------


class TestExtractClapSemanticSectionAxes:
    def test_returns_whole_track_superset_and_per_section_axes(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        sections = [
            {"section": "intro", "start_sec": 0.0, "end_sec": 1.0},
            {"section": "verse", "start_sec": 1.0, "end_sec": 2.5},
        ]
        result = extract_clap_semantic_section_axes(str(audio), sections)

        assert result.embedding is not None
        assert len(result.semantic_axes) == 5  # whole-track superset
        assert len(result.semantic_axis_sections) == 2

        for section_result, expected in zip(result.semantic_axis_sections, sections):
            assert isinstance(section_result, LearnedSemanticSection)
            assert section_result.section == expected["section"]
            assert section_result.start_sec == expected["start_sec"]
            assert section_result.end_sec == expected["end_sec"]
            assert len(section_result.axes) == 5
            for axis in section_result.axes:
                assert isinstance(axis, LearnedSemanticAxis)
                assert isinstance(axis.contrast_fit, float)

        assert result.inference_config["n_semantic_axis_sections"] == 2
        # embed_audio_file-derived keys must survive the augmentation.
        assert result.inference_config["source"] == "laion_clap"

    def test_custom_axes_override_applies_to_every_section(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=1.0)
        sections = [{"section": "only", "start_sec": 0.0, "end_sec": 1.0}]
        custom_axes = [{"name": "custom_axis", "positive": ["a"], "negative": ["b"]}]
        result = extract_clap_semantic_section_axes(str(audio), sections, axes=custom_axes)

        assert len(result.semantic_axes) == 1
        assert len(result.semantic_axis_sections) == 1
        assert len(result.semantic_axis_sections[0].axes) == 1
        assert result.semantic_axis_sections[0].axes[0].axis == "custom_axis"

    def test_empty_sections_list_yields_no_sections(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.5)
        result = extract_clap_semantic_section_axes(str(audio), [])

        assert result.semantic_axis_sections == []
        assert result.inference_config["n_semantic_axis_sections"] == 0
        # whole-track axes are still populated (superset semantics hold at n=0).
        assert len(result.semantic_axes) == 5

    def test_unavailable_raises_with_semantic_embed_hint(self, monkeypatch):
        _force_clap_unavailable(monkeypatch)

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        with pytest.raises(LearnedModelUnavailable, match="semantic-embed"):
            extract_clap_semantic_section_axes(
                "x.wav", [{"section": "a", "start_sec": 0.0, "end_sec": 1.0}]
            )


# ---------------------------------------------------------------------------
# Isolation: section axes stay out of rule-based RPE / SVP / scoring
# ---------------------------------------------------------------------------


class TestIsolation:
    def test_attach_leaves_semantic_and_physical_unchanged(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        bundle = _make_multi_section_bundle()
        sections = [
            {"section": marker.label, "start_sec": marker.start_sec, "end_sec": marker.end_sec}
            for marker in bundle.physical.structure
        ]
        annotations = extract_clap_semantic_section_axes(str(audio), sections)
        enriched = attach_learned_annotations(bundle, annotations)

        assert enriched.semantic.model_dump() == bundle.semantic.model_dump()
        assert enriched.physical.model_dump() == bundle.physical.model_dump()

    def test_generate_svp_output_identical_with_and_without_section_axes(
        self, monkeypatch, tmp_path
    ):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        bundle = _make_multi_section_bundle()
        sections = [
            {"section": marker.label, "start_sec": marker.start_sec, "end_sec": marker.end_sec}
            for marker in bundle.physical.structure
        ]
        enriched = attach_learned_annotations(
            bundle, extract_clap_semantic_section_axes(str(audio), sections)
        )

        assert generate_svp(bundle).model_dump() == generate_svp(enriched).model_dump()


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


class TestSerializer:
    def test_bundle_dump_includes_semantic_axis_sections(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])

        from svp_rpe.rpe.learned.semantic_axes import extract_clap_semantic_section_axes

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=1.0)
        sections = [{"section": "only", "start_sec": 0.0, "end_sec": 1.0}]
        bundle = attach_learned_annotations(
            _make_bundle(), extract_clap_semantic_section_axes(str(audio), sections)
        )
        dumped = bundle.model_dump()

        assert "learned_annotations" in dumped
        assert dumped["learned_annotations"]["semantic_axis_sections"][0]["axes"][0]["axis"]
        assert dumped["learned_annotations"]["semantic_axis_sections"][0]["section"] == "only"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_extract_with_clap_sections_flag_attaches_semantic_axis_sections(
        self, monkeypatch, tmp_path
    ):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])
        monkeypatch.setattr(
            extractor,
            "extract_rpe_from_file",
            lambda path, **kwargs: _make_multi_section_bundle(),
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        output = tmp_path / "out.json"

        result = runner.invoke(app, ["extract", str(audio), "--clap-sections", "-o", str(output)])

        assert result.exit_code == 0, result.stdout
        dumped = json.loads(output.read_text(encoding="utf-8"))
        assert "learned_annotations" in dumped
        assert len(dumped["learned_annotations"]["semantic_axis_sections"]) == 2
        assert len(dumped["learned_annotations"]["semantic_axes"]) == 5

    def test_extract_with_both_flags_takes_the_sections_path(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0])
        monkeypatch.setattr(
            extractor,
            "extract_rpe_from_file",
            lambda path, **kwargs: _make_multi_section_bundle(),
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        output = tmp_path / "out.json"

        result = runner.invoke(
            app,
            ["extract", str(audio), "--clap-semantic", "--clap-sections", "-o", str(output)],
        )

        assert result.exit_code == 0, result.stdout
        dumped = json.loads(output.read_text(encoding="utf-8"))
        assert len(dumped["learned_annotations"]["semantic_axis_sections"]) == 2

    def test_extract_without_clap_flags_omits_learned_annotations(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            extractor,
            "extract_rpe_from_file",
            lambda path, **kwargs: _make_multi_section_bundle(),
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)
        output = tmp_path / "out.json"

        result = runner.invoke(app, ["extract", str(audio), "-o", str(output)])

        assert result.exit_code == 0, result.stdout
        dumped = json.loads(output.read_text(encoding="utf-8"))
        assert "learned_annotations" not in dumped

    def test_extract_clap_sections_unavailable_fails_fast_before_extraction(
        self, monkeypatch, tmp_path
    ):
        _force_clap_unavailable(monkeypatch)
        calls: list[str] = []

        def _recording_extract(path, **kwargs):
            calls.append(path)
            return _make_multi_section_bundle()

        monkeypatch.setattr(extractor, "extract_rpe_from_file", _recording_extract)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=2.5)

        result = runner.invoke(app, ["extract", str(audio), "--clap-sections"])

        assert result.exit_code == 1
        assert calls == []  # base extraction never ran
        assert ".[semantic-embed]" in result.stdout
