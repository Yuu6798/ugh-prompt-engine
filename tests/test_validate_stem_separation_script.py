"""Tests for the manual Q3 stem validation script."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_stem_separation as script  # noqa: E402
from svp_rpe.eval.stem_validation import (  # noqa: E402
    StemBPMAlignmentValidation,
    StemReconstructionValidation,
)
from svp_rpe.io.audio_loader import AudioData, AudioMetadata  # noqa: E402
from svp_rpe.io.source_separator import STEM_NAMES, StemBundle  # noqa: E402


def test_validate_file_loads_full_mix_at_stem_sample_rate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "fixture.mp3"
    audio_path.write_bytes(b"fixture")
    stem_sample_rate = 44100
    stems = {
        name: np.zeros(stem_sample_rate, dtype=np.float32)
        for name in STEM_NAMES
    }
    stem_bundle = StemBundle(
        source_path=str(audio_path),
        model_name="fake-demucs",
        sample_rate=stem_sample_rate,
        duration_sec=1.0,
        stems=stems,
    )
    requested_sample_rates: list[int | None] = []

    def fake_load_audio(path: str, *, target_sr: int | None = 22050) -> AudioData:
        requested_sample_rates.append(target_sr)
        assert path == str(audio_path)
        return AudioData(
            metadata=AudioMetadata(
                file_path=path,
                duration_sec=1.0,
                sample_rate=target_sr or 48000,
                channels=1,
                format="mp3",
            ),
            y_mono=np.zeros(target_sr or 48000, dtype=np.float32),
            y_stereo=None,
            sr=target_sr or 48000,
        )

    def fake_extract_physical(audio: AudioData, *, stem_bundle: StemBundle):
        assert audio.sr == stem_bundle.sample_rate
        return object(), None, []

    monkeypatch.setattr(script, "separate_stems", lambda *_args, **_kwargs: stem_bundle)
    monkeypatch.setattr(script, "load_audio", fake_load_audio)
    monkeypatch.setattr(script, "extract_physical", fake_extract_physical)
    monkeypatch.setattr(
        script,
        "validate_stem_reconstruction",
        lambda *_args, threshold: StemReconstructionValidation(
            residual_ratio=0.0,
            residual_rms=0.0,
            source_rms=0.1,
            threshold=threshold,
            compared_samples=stem_sample_rate,
            length_delta_samples=0,
            passed=True,
        ),
    )
    monkeypatch.setattr(
        script,
        "validate_stem_bpm_alignment",
        lambda *_args, tolerance: StemBPMAlignmentValidation(
            full_bpm=120.0,
            stem_bpms={},
            bpm_diffs={},
            tolerance=tolerance,
            missing_stems=[],
            passed=True,
        ),
    )

    result = script.validate_file(
        audio_path,
        model="fake-demucs",
        device="cpu",
        residual_threshold=0.05,
        bpm_tolerance=5.0,
    )

    assert requested_sample_rates == [stem_sample_rate]
    assert result["passed"]
