"""tests/test_svp_generator.py — SVP generation + evaluation tests."""
from __future__ import annotations

from svp_rpe.eval.scorer_integrated import score_integrated
from svp_rpe.eval.scorer_rpe import score_rpe
from svp_rpe.eval.scorer_ugher import score_ugher
from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_rpe
from svp_rpe.rpe.models import PhysicalRPE, RPEBundle, SectionMarker, SpectralProfile
from svp_rpe.rpe.semantic_rules import generate_semantic
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


def _make_bundle_for_domain_profile(*, valley_depth: float, valley_depth_legacy: float | None) -> RPEBundle:
    physical = PhysicalRPE(
        bpm=100.0,
        key="C",
        mode="major",
        duration_sec=10.0,
        sample_rate=44100,
        structure=[SectionMarker(label="full", start_sec=0.0, end_sec=10.0)],
        rms_mean=0.2,
        peak_amplitude=0.8,
        crest_factor=4.0,
        active_rate=0.5,
        valley_depth=valley_depth,
        valley_depth_legacy=valley_depth_legacy,
        thickness=1.0,
        spectral_centroid=1800.0,
        spectral_profile=SpectralProfile(
            centroid=1800.0, low_ratio=0.2, mid_ratio=0.3, high_ratio=0.2, brightness=0.2,
        ),
        onset_density=2.0,
    )
    return RPEBundle(
        physical=physical,
        semantic=generate_semantic(physical),
        audio_file="fixture.wav",
        audio_duration_sec=10.0,
        audio_sample_rate=44100,
        audio_channels=1,
        audio_format="wav",
    )


class TestGenerateSvpValleyDepthLegacyScale:
    """Codex PR #188 P2 #5: generate_svp's domain-profile context must read
    the legacy valley scale, since domain_profiles/music.yaml's
    valley_depth_min/_max conditions (struct.dynamic / struct.gradual_motion
    style labels) are calibrated on the frozen pre-v2 hybrid scale."""

    def test_dynamic_style_label_does_not_fire_from_v2_valley_when_legacy_is_low(self) -> None:
        bundle = _make_bundle_for_domain_profile(valley_depth=0.5, valley_depth_legacy=0.05)

        svp = generate_svp(bundle)

        assert "dynamic" not in svp.svp_for_generation.style_tags
        assert "contrastive" not in svp.svp_for_generation.style_tags
        assert "evolving" not in svp.svp_for_generation.style_tags

    def test_dynamic_style_label_fires_from_legacy_valley_when_v2_is_low(self) -> None:
        # Converse: a low v2 valley_depth (0.05) must not suppress the
        # struct.dynamic label when valley_depth_legacy (0.4) clears the
        # frozen threshold — confirms legacy is actually consulted.
        bundle = _make_bundle_for_domain_profile(valley_depth=0.05, valley_depth_legacy=0.4)

        svp = generate_svp(bundle)

        assert "dynamic" in svp.svp_for_generation.style_tags
        assert "contrastive" in svp.svp_for_generation.style_tags

    def test_falls_back_to_valley_depth_when_legacy_absent(self) -> None:
        # Pre-v2 JSON has no valley_depth_legacy (None); valley_depth itself
        # WAS the hybrid value in that case, so context must fall back to it.
        bundle = _make_bundle_for_domain_profile(valley_depth=0.4, valley_depth_legacy=None)

        svp = generate_svp(bundle)

        assert "dynamic" in svp.svp_for_generation.style_tags
