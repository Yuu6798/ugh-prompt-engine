"""tests/test_svp_generator.py — SVP generation + evaluation tests."""
from __future__ import annotations

from svp_rpe.eval.scorer_integrated import score_integrated
from svp_rpe.eval.scorer_rpe import score_rpe
from svp_rpe.eval.scorer_ugher import score_ugher
from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_rpe
from svp_rpe.svp.generator import generate_svp
from svp_rpe.svp.render_text import render_text
from svp_rpe.svp.render_yaml import render_yaml


class TestSVPGeneration:
    def test_generate_svp_from_audio(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp = generate_svp(rpe)

        assert svp.data_lineage.source_audio == sine_wave_mono
        assert svp.analysis_rpe.por_core
        assert svp.svp_for_generation.prompt_text
        assert svp.minimal_svp.c

    def test_svp_deterministic(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp1 = generate_svp(rpe)
        svp2 = generate_svp(rpe)
        assert svp1.model_dump() == svp2.model_dump()

    def test_render_yaml_not_empty(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp = generate_svp(rpe)
        yaml_str = render_yaml(svp)
        assert len(yaml_str) > 100
        assert "schema_version" in yaml_str

    def test_render_text_has_sections(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp = generate_svp(rpe)
        text = render_text(svp)
        assert "# SVP Report" in text
        assert "## Generation Prompt" in text
        assert "## Minimal SVP" in text

    def test_por_core_preserved_in_svp(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp = generate_svp(rpe)
        # por_core from RPE semantic must appear in SVP analysis
        assert svp.analysis_rpe.por_core == rpe.semantic.por_core


class TestEvaluation:
    def test_rpe_score_in_range(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        score = score_rpe(rpe.physical)
        assert 0.0 <= score.overall <= 1.0
        assert 0.0 <= score.rms_score <= 1.0

    def test_ugher_score_in_range(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp = generate_svp(rpe)
        score = score_ugher(rpe, svp)
        assert 0.0 <= score.overall <= 1.0

    def test_integrated_score(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp = generate_svp(rpe)
        rpe_s = score_rpe(rpe.physical)
        ugher_s = score_ugher(rpe, svp)
        integrated = score_integrated(ugher_s, rpe_s)
        assert 0.0 <= integrated.integrated_score <= 1.0
        assert integrated.ugher_weight == 0.5
        assert integrated.rpe_weight == 0.5

    def test_evaluation_json_has_3_scores(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe = extract_rpe(audio)
        svp = generate_svp(rpe)
        rpe_s = score_rpe(rpe.physical)
        ugher_s = score_ugher(rpe, svp)
        integrated = score_integrated(ugher_s, rpe_s)
        # AC-06: 3 scores present
        assert hasattr(integrated, "ugher_score")
        assert hasattr(integrated, "rpe_score")
        assert hasattr(integrated, "integrated_score")


class TestFullPipeline:
    def test_end_to_end_deterministic(self, sine_wave_mono):
        """AC-04: Same input 2 runs → identical output."""
        audio = load_audio(sine_wave_mono)

        rpe1 = extract_rpe(audio)
        svp1 = generate_svp(rpe1)

        rpe2 = extract_rpe(audio)
        svp2 = generate_svp(rpe2)

        assert rpe1.model_dump() == rpe2.model_dump()
        assert svp1.model_dump() == svp2.model_dump()
