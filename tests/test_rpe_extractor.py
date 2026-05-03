"""tests/test_rpe_extractor.py — RPE physical extraction tests."""
from __future__ import annotations

import numpy as np

from svp_rpe.rpe import extractor as extractor_module
from svp_rpe.rpe.extractor import extract_physical, extract_physical_from_file
from svp_rpe.rpe.physical_features import (
    compute_active_rate,
    compute_bpm,
    compute_crest_factor,
    compute_key,
    compute_onset_density,
    compute_rms_mean,
)
from svp_rpe.rpe.structure import detect_sections
from svp_rpe.io.audio_loader import load_audio


class TestPhysicalFeatures:
    def test_rms_mean_positive(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rms = compute_rms_mean(audio.y_mono, audio.sr)
        assert rms > 0

    def test_active_rate_in_range(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rate = compute_active_rate(audio.y_mono, audio.sr)
        assert 0.0 <= rate <= 1.0

    def test_crest_factor_positive(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        cf = compute_crest_factor(audio.y_mono)
        assert cf > 0

    def test_crest_factor_silence(self):
        y = np.zeros(1000, dtype=np.float32)
        assert compute_crest_factor(y) == 0.0

    def test_valley_depth_non_negative(self, sine_wave_mono):
        from svp_rpe.rpe.models import SectionMarker
        from svp_rpe.rpe.valley import compute_valley_depth
        audio = load_audio(sine_wave_mono)
        sections = [SectionMarker(label="s1", start_sec=0.0, end_sec=3.0)]
        vd, diag = compute_valley_depth(audio.y_mono, audio.sr, sections)
        assert vd >= 0.0
        assert diag.method == "hybrid"

    def test_onset_density_non_negative(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        od = compute_onset_density(audio.y_mono, audio.sr)
        assert od >= 0.0

    def test_bpm_returns_tuple(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        bpm, conf = compute_bpm(audio.y_mono, audio.sr)
        assert bpm is None or bpm >= 0  # 0.0 possible for short synthetic audio
        assert conf is None or 0.0 <= conf <= 1.0

    def test_key_returns_tuple(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        key, mode, conf = compute_key(audio.y_mono, audio.sr)
        if key is not None:
            assert key in ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
            assert mode in ["major", "minor"]
            assert 0.0 <= conf <= 1.0


class TestStructure:
    def test_at_least_one_section(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        sections = detect_sections(audio.y_mono, audio.sr)
        assert len(sections) >= 1
        assert sections[0].label in ("section_01", "Full", "Intro")
        assert sections[0].start_sec == 0.0

    def test_short_audio_single_section(self):
        sr = 22050
        y = np.zeros(int(sr * 2), dtype=np.float32)  # 2 sec
        sections = detect_sections(y, sr, min_section_sec=5.0)
        assert len(sections) == 1


class TestExtractor:
    def test_extract_physical_returns_valid(self, sine_wave_mono):
        audio = load_audio(sine_wave_mono)
        rpe, valley_diag, section_features = extract_physical(audio)
        assert rpe.duration_sec > 0
        assert len(rpe.structure) >= 1
        assert rpe.rms_mean >= 0
        assert rpe.spectral_profile.centroid >= 0

    def test_extract_from_file(self, sine_wave_mono):
        rpe = extract_physical_from_file(sine_wave_mono)
        assert rpe.sample_rate == 22050

    def test_extract_rpe_from_file_reuses_preloaded_audio(self, monkeypatch, sine_wave_mono):
        audio = load_audio(sine_wave_mono)

        def fail_load_audio(path):
            raise AssertionError(f"unexpected second audio load for {path}")

        monkeypatch.setattr(extractor_module, "load_audio", fail_load_audio)

        bundle = extractor_module.extract_rpe_from_file(
            sine_wave_mono,
            preloaded_audio=audio,
        )

        assert bundle.audio_sample_rate == 22050

    def test_deterministic(self, sine_wave_mono):
        rpe1 = extract_physical_from_file(sine_wave_mono)
        rpe2 = extract_physical_from_file(sine_wave_mono)
        assert rpe1.model_dump() == rpe2.model_dump()

    def test_stereo_has_stereo_profile(self, sine_wave_stereo):
        audio = load_audio(sine_wave_stereo)
        rpe, _, _ = extract_physical(audio)
        assert rpe.stereo_profile is not None
        assert 0.0 <= rpe.stereo_profile.width <= 1.0
