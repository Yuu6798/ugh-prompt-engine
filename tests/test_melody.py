"""Q2-3 melody contour extraction tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_physical
from svp_rpe.rpe.physical_features import compute_melody_contour

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"


def _truth_rows() -> list[dict]:
    return yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))


def test_compute_melody_contour_is_deterministic() -> None:
    sr = 22050
    t = np.arange(sr * 2, dtype=float) / sr
    y = 0.3 * np.sin(2.0 * np.pi * 440.0 * t)

    first = compute_melody_contour(y, sr)
    second = compute_melody_contour(y, sr)

    assert first is not None
    assert second is not None
    assert first.model_dump() == second.model_dump()


def test_pure_sine_pitch_is_within_50_cents() -> None:
    sr = 22050
    t = np.arange(sr * 2, dtype=float) / sr
    y = 0.3 * np.sin(2.0 * np.pi * 440.0 * t)

    contour = compute_melody_contour(y, sr)

    assert contour is not None
    voiced = [
        freq for freq, voicing in zip(contour.frequencies_hz, contour.voicing)
        if voicing >= 0.5 and freq > 0.0
    ]
    assert voiced
    cents = abs(1200.0 * np.log2(float(np.median(voiced)) / 440.0))
    assert cents <= 50.0


def test_extractor_populates_melody_contour() -> None:
    row = _truth_rows()[4]
    audio = load_audio(str(SAMPLE_DIR / row["filename"]))

    phys, _, _ = extract_physical(audio)

    assert phys.melody_contour is not None
    assert phys.melody_contour.times
    assert len(phys.melody_contour.times) == len(phys.melody_contour.frequencies_hz)
    assert len(phys.melody_contour.times) == len(phys.melody_contour.voicing)


def test_silence_has_no_melody_contour() -> None:
    sr = 22050
    y = np.zeros(sr, dtype=np.float32)

    assert compute_melody_contour(y, sr) is None


def test_short_non_silent_clip_does_not_raise() -> None:
    sr = 22050
    y = np.ones(16, dtype=np.float32) * 0.1

    contour = compute_melody_contour(y, sr)

    assert contour is None or len(contour.times) == len(contour.frequencies_hz)
