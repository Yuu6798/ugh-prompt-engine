"""tests/test_time_signature.py - Q1-2 time signature detection."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_physical
from svp_rpe.rpe.physical_features import (
    _classify_time_signature_from_beat_strengths,
    compute_time_signature,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"


def _truth_rows() -> list[dict]:
    return yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "row",
    _truth_rows(),
    ids=[row["id"] for row in _truth_rows()],
)
def test_synth_samples_match_ground_truth_time_signature(row: dict) -> None:
    """Q1-2 regression: synth waltz must be 3/4 and 4/4 fixtures stay 4/4."""
    expected = str(row["time_signature"])
    audio = load_audio(str(SAMPLE_DIR / row["filename"]))

    estimated, confidence = compute_time_signature(audio.y_mono, audio.sr)

    assert estimated == expected
    assert 0.0 <= confidence <= 1.0


def test_extractor_populates_time_signature_from_detector() -> None:
    """Regression guard: PhysicalRPE must not rely on the model default "4/4"."""
    audio = load_audio(str(SAMPLE_DIR / "synth_04_waltz_fsharp_minor.wav"))

    phys, _, _ = extract_physical(audio)

    assert phys.time_signature == "3/4"
    assert phys.time_signature_confidence > 0.7


def test_short_audio_falls_back_without_false_confidence() -> None:
    """Too little beat evidence should fall back to 4/4 with zero confidence."""
    sr = 22050
    y = np.zeros(sr, dtype=np.float32)

    estimated, confidence = compute_time_signature(y, sr)

    assert estimated == "4/4"
    assert confidence == 0.0


def test_compound_six_eight_pattern_is_supported() -> None:
    """Synthetic beat-strength pattern: strong/weak/weak/medium/weak/weak."""
    strengths = np.array([1.0, 0.1, 0.1, 0.55, 0.1, 0.1] * 12, dtype=float)

    estimated, confidence = _classify_time_signature_from_beat_strengths(strengths)

    assert estimated == "6/8"
    assert confidence > 0.7
