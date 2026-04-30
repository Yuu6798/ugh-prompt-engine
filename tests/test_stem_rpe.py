"""Q3-2 per-stem PhysicalRPE integration tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import svp_rpe.rpe.extractor as extractor
from svp_rpe.io.audio_loader import load_audio
from svp_rpe.io.source_separator import REQUIRED_STEMS, STEM_NAMES, StemBundle
from svp_rpe.rpe.extractor import extract_physical, extract_rpe_from_file
from svp_rpe.rpe.models import PhysicalRPE

pytestmark = [
    pytest.mark.filterwarnings("ignore:n_fft=.*too large.*:UserWarning"),
    pytest.mark.filterwarnings("ignore:Trying to estimate tuning.*:UserWarning"),
]


def _fake_stem_bundle(source_path: str = "fixture.wav") -> StemBundle:
    sample_rate = 22050
    duration_sec = 1.0
    t = np.linspace(0.0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    frequencies = {
        "vocals": 440.0,
        "drums": 110.0,
        "bass": 55.0,
        "other": 660.0,
    }
    stems = {
        name: (0.2 * np.sin(2 * np.pi * frequencies[name] * t)).astype(np.float32)
        for name in STEM_NAMES
    }
    return StemBundle(
        source_path=source_path,
        model_name="fake-demucs",
        sample_rate=sample_rate,
        duration_sec=duration_sec,
        stems=stems,
    )


def test_empty_stem_rpe_is_omitted_from_model_dump(sine_wave_mono: str) -> None:
    audio = load_audio(sine_wave_mono)
    physical, _, _ = extract_physical(audio)

    assert physical.stem_rpe == {}
    assert "stem_rpe" not in physical.model_dump()


def test_extract_physical_populates_non_recursive_stem_rpe(sine_wave_mono: str) -> None:
    audio = load_audio(sine_wave_mono)
    stem_bundle = _fake_stem_bundle(sine_wave_mono)

    physical, _, _ = extract_physical(audio, stem_bundle=stem_bundle)

    assert set(physical.stem_rpe) == REQUIRED_STEMS
    dumped = physical.model_dump()
    assert set(dumped["stem_rpe"]) == REQUIRED_STEMS
    for stem_name, stem_physical in physical.stem_rpe.items():
        assert isinstance(stem_physical, PhysicalRPE)
        assert stem_physical.sample_rate == stem_bundle.sample_rate
        assert stem_physical.duration_sec == stem_bundle.duration_sec
        assert stem_physical.stereo_profile is None
        assert stem_physical.stem_rpe == {}, stem_name
        assert "stem_rpe" not in dumped["stem_rpe"][stem_name]


def test_extract_rpe_from_file_include_stems_uses_separator(
    monkeypatch,
    sine_wave_mono: str,
) -> None:
    calls: list[tuple[Path, str, str]] = []

    def fake_separator(path: Path, *, model: str, device: str) -> StemBundle:
        calls.append((path, model, device))
        return _fake_stem_bundle(str(path))

    monkeypatch.setattr(extractor, "separate_audio_stems", fake_separator)

    bundle = extract_rpe_from_file(
        sine_wave_mono,
        include_stems=True,
        separation_model="fake-model",
        separation_device="cpu",
    )

    assert calls == [(Path(sine_wave_mono), "fake-model", "cpu")]
    assert set(bundle.physical.stem_rpe) == REQUIRED_STEMS
    assert bundle.semantic.por_core


def test_extract_rpe_from_file_default_does_not_call_separator(
    monkeypatch,
    sine_wave_mono: str,
) -> None:
    def unexpected_separator(path: Path, *, model: str, device: str) -> StemBundle:
        raise AssertionError("separator should be opt-in")

    monkeypatch.setattr(extractor, "separate_audio_stems", unexpected_separator)

    bundle = extract_rpe_from_file(sine_wave_mono)

    assert bundle.physical.stem_rpe == {}
