"""tests/test_transcribe_clap_advisory.py — `transcribe --clap-semantic` advisory.

`transcribe --clap-semantic` prepends CLAP semantic-axis readings of the source
audio as a YAML comment block. The block is advisory only: it MUST NOT fill the
authored semantic.* fields (DD-D), and the combined document MUST stay
loader-valid. Reuses the fake `laion_clap` backend from `tests.test_clap_adapter`.
"""
from __future__ import annotations

from typer.testing import CliRunner

import svp_rpe.rpe.extractor as extractor
from svp_rpe.cli import app
from svp_rpe.compose.loader import load_composition_score
from svp_rpe.rpe.models import LearnedAudioAnnotations, LearnedSemanticAxis
from svp_rpe.transcribe import (
    TODO_AUTHOR_INPUT,
    render_semantic_axes_advisory,
)
from tests.test_clap_adapter import _force_clap_unavailable, _install_fake_clap, _make_bundle, _write_wav

runner = CliRunner()

_VOCAL_TEXT_VECTORS = {
    "a song with lead vocals": [1.0, 0.0],
    "singing with lyrics": [1.0, 0.0],
    "an instrumental track with no vocals": [0.0, 1.0],
    "instrumental music": [0.0, 1.0],
}


class TestRenderAdvisory:
    def test_empty_axes_render_to_empty_string(self):
        assert render_semantic_axes_advisory(LearnedAudioAnnotations()) == ""

    def test_advisory_is_all_comment_lines_with_signed_values(self):
        annotations = LearnedAudioAnnotations(
            semantic_axes=[
                LearnedSemanticAxis(
                    axis="vocal_presence",
                    contrast_fit=0.2475,
                    positive_probes=["a"],
                    negative_probes=["b"],
                    source_model="laion_clap:CLAP_Module",
                ),
                LearnedSemanticAxis(
                    axis="brightness",
                    contrast_fit=-0.03,
                    positive_probes=["a"],
                    negative_probes=["b"],
                    source_model="laion_clap:CLAP_Module",
                ),
            ]
        )
        block = render_semantic_axes_advisory(annotations)

        # Every non-empty line is a YAML comment, so prepending it keeps the
        # document loader-valid.
        assert all(line.startswith("#") for line in block.splitlines() if line)
        assert "CLAP semantic sensor (advisory)" in block
        assert "vocal_presence" in block
        assert "+0.2475" in block  # signed, 4 decimals
        assert "-0.0300" in block
        assert "laion_clap:CLAP_Module" in block


class TestTranscribeCli:
    def test_clap_semantic_prepends_advisory_and_stays_loadable(self, monkeypatch, tmp_path):
        _install_fake_clap(monkeypatch, audio_vector=[3.0, 4.0], text_vectors=_VOCAL_TEXT_VECTORS)
        monkeypatch.setattr(
            extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle()
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        output = tmp_path / "score.yaml"

        result = runner.invoke(
            app, ["transcribe", str(audio), "--clap-semantic", "-o", str(output)]
        )

        assert result.exit_code == 0, result.stdout
        text = output.read_text(encoding="utf-8")
        assert "CLAP semantic sensor (advisory)" in text
        assert "vocal_presence" in text

        # Still loader-valid, and the authored semantic layer is untouched (DD-D).
        score = load_composition_score(output)
        assert score.semantic.core == TODO_AUTHOR_INPUT
        assert score.semantic.grv.primary == TODO_AUTHOR_INPUT
        assert score.semantic.delta_e.overall == TODO_AUTHOR_INPUT

    def test_without_flag_has_no_advisory(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            extractor, "extract_rpe_from_file", lambda path, **kwargs: _make_bundle()
        )

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)
        output = tmp_path / "score.yaml"

        result = runner.invoke(app, ["transcribe", str(audio), "-o", str(output)])

        assert result.exit_code == 0, result.stdout
        assert "CLAP semantic sensor" not in output.read_text(encoding="utf-8")

    def test_clap_semantic_unavailable_fails_fast_before_extraction(self, monkeypatch, tmp_path):
        _force_clap_unavailable(monkeypatch)
        calls: list[str] = []

        def _recording_extract(path, **kwargs):
            calls.append(path)
            return _make_bundle()

        monkeypatch.setattr(extractor, "extract_rpe_from_file", _recording_extract)

        audio = tmp_path / "a.wav"
        _write_wav(audio, seconds=0.05)

        result = runner.invoke(app, ["transcribe", str(audio), "--clap-semantic"])

        assert result.exit_code == 1
        assert calls == []  # base extraction never ran
        assert '.[semantic-embed]' in result.stdout
