"""tests/test_audio_loader.py — audio loading tests."""
from __future__ import annotations

import numpy as np
import pytest

from svp_rpe.io.audio_loader import (
    AudioData,
    DecodeError,
    UnsupportedFormatError,
    load_audio,
    normalize_audio,
    to_mono,
)


class TestLoadAudio:
    def test_load_mono_wav(self, sine_wave_mono):
        data = load_audio(sine_wave_mono)
        assert isinstance(data, AudioData)
        assert data.metadata.channels == 1
        assert data.metadata.format == "wav"
        assert data.sr == 22050
        assert isinstance(data.y_mono, np.ndarray)
        assert data.y_mono.ndim == 1
        assert data.y_stereo is None

    def test_load_stereo_wav(self, sine_wave_stereo):
        data = load_audio(sine_wave_stereo)
        assert data.metadata.channels == 2
        assert data.y_mono.ndim == 1  # mono mix
        assert data.y_stereo is not None
        assert data.y_stereo.ndim == 2  # (channels, samples)

    def test_duration_positive(self, sine_wave_mono):
        data = load_audio(sine_wave_mono)
        assert data.metadata.duration_sec > 0

    def test_load_resamples_to_target_sr(self, sine_wave_mono):
        data = load_audio(sine_wave_mono, target_sr=11025)
        assert data.sr == 11025
        assert data.metadata.sample_rate == 11025

    def test_load_keeps_native_sr_when_target_sr_is_none(self, sine_wave_mono):
        data = load_audio(sine_wave_mono, target_sr=None)
        assert data.sr == 22050
        assert data.metadata.sample_rate == 22050

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_audio(tmp_path / "nonexistent.wav")

    def test_unsupported_format(self, tmp_path):
        bad_file = tmp_path / "test.ogg"
        bad_file.write_text("fake")
        with pytest.raises(UnsupportedFormatError):
            load_audio(bad_file)

    def test_corrupt_file(self, tmp_path):
        bad_wav = tmp_path / "corrupt.wav"
        bad_wav.write_bytes(b"not a real wav file")
        with pytest.raises(DecodeError):
            load_audio(bad_wav)


class TestHelpers:
    def test_to_mono_already_mono(self):
        y = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = to_mono(y)
        assert result.ndim == 1
        np.testing.assert_array_equal(result, y)

    def test_to_mono_from_stereo(self):
        y = np.array([[1.0, 2.0, 3.0], [3.0, 4.0, 5.0]], dtype=np.float32)
        result = to_mono(y)
        assert result.ndim == 1
        np.testing.assert_array_almost_equal(result, [2.0, 3.0, 4.0])

    def test_normalize_audio(self):
        y = np.array([0.0, 0.5, -1.0, 0.25], dtype=np.float32)
        result = normalize_audio(y)
        assert np.max(np.abs(result)) == pytest.approx(1.0)
        assert result[2] == pytest.approx(-1.0)

    def test_normalize_silence(self):
        y = np.zeros(100, dtype=np.float32)
        result = normalize_audio(y)
        np.testing.assert_array_equal(result, y)
