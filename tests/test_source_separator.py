"""Q3-1 source separation adapter tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

import svp_rpe.io.source_separator as source_separator
from svp_rpe.io.source_separator import (
    REQUIRED_STEMS,
    STEM_NAMES,
    SeparatorNotAvailableError,
    StemBundle,
    separate_stems,
)


def _stems(n_samples: int = 8) -> dict[str, np.ndarray]:
    return {
        name: np.linspace(-0.5, 0.5, n_samples, dtype=np.float32)
        for name in STEM_NAMES
    }


def test_stem_bundle_creation() -> None:
    bundle = StemBundle(
        source_path="fixture.wav",
        model_name="htdemucs_ft",
        sample_rate=44100,
        duration_sec=0.5,
        stems=_stems(),
    )

    assert bundle.source_path == "fixture.wav"
    assert bundle.model_name == "htdemucs_ft"
    assert bundle.sample_rate == 44100
    assert set(bundle.stems) == REQUIRED_STEMS


def test_stem_bundle_requires_all_expected_stems() -> None:
    stems = _stems()
    del stems["other"]

    with pytest.raises(ValueError, match="missing=\\['other'\\]"):
        StemBundle(
            source_path="fixture.wav",
            model_name="htdemucs_ft",
            sample_rate=44100,
            duration_sec=0.5,
            stems=stems,
        )


def test_stem_bundle_rejects_non_mono_stems() -> None:
    stems = _stems()
    stems["vocals"] = np.zeros((2, 8), dtype=np.float32)

    with pytest.raises(ValueError, match="mono 1D"):
        StemBundle(
            source_path="fixture.wav",
            model_name="htdemucs_ft",
            sample_rate=44100,
            duration_sec=0.5,
            stems=stems,
        )


def test_stem_bundle_rejects_non_float32_stems() -> None:
    stems = _stems()
    stems["vocals"] = np.zeros(8, dtype=np.float64)

    with pytest.raises(ValueError, match="dtype float32"):
        StemBundle(
            source_path="fixture.wav",
            model_name="htdemucs_ft",
            sample_rate=44100,
            duration_sec=0.5,
            stems=stems,
        )


def test_no_demucs_raises_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", False)
    monkeypatch.setattr(source_separator, "_DemucsAPI", None)

    with pytest.raises(SeparatorNotAvailableError, match="svp-rpe\\[separate\\]"):
        separate_stems(tmp_path / "fixture.wav")


def test_separate_stems_with_fake_demucs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeSeparator:
        samplerate = 44100

        def __init__(self, *, model: str, device: str) -> None:
            self.model = model
            self.device = device

        def separate_audio_file(self, path: Path):
            stereo = np.vstack([
                np.ones(16, dtype=np.float32),
                np.zeros(16, dtype=np.float32),
            ])
            return path, {name: stereo for name in STEM_NAMES}

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", FakeSeparator)

    bundle = separate_stems(tmp_path / "fixture.wav", model="htdemucs_ft", device="cpu")

    assert set(bundle.stems) == REQUIRED_STEMS
    assert bundle.sample_rate == 44100
    assert bundle.duration_sec == pytest.approx(16 / 44100, abs=1e-4)
    for stem in bundle.stems.values():
        assert stem.ndim == 1
        assert stem.dtype == np.float32
        np.testing.assert_allclose(stem, np.full(16, 0.5, dtype=np.float32))


def test_separate_stems_warns_when_samplerate_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSeparator:
        def __init__(self, *, model: str, device: str) -> None:
            pass

        def separate_audio_file(self, path: Path):
            return path, _stems(n_samples=16)

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", FakeSeparator)

    with pytest.warns(RuntimeWarning, match="did not expose samplerate"):
        bundle = separate_stems(tmp_path / "fixture.wav")

    assert bundle.sample_rate == source_separator.DEFAULT_SAMPLE_RATE
    assert bundle.duration_sec == pytest.approx(
        16 / source_separator.DEFAULT_SAMPLE_RATE,
        abs=1e-4,
    )


def test_separate_stems_rejects_incomplete_demucs_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSeparator:
        def __init__(self, *, model: str, device: str) -> None:
            pass

        def separate_audio_file(self, path: Path):
            return path, {"vocals": np.zeros(16, dtype=np.float32)}

    monkeypatch.setattr(source_separator, "_HAS_DEMUCS", True)
    monkeypatch.setattr(source_separator, "_DemucsAPI", FakeSeparator)

    with pytest.raises(ValueError, match="missing="):
        separate_stems(tmp_path / "fixture.wav")
