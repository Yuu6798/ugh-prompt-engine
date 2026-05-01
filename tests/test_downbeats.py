"""tests/test_downbeats.py - Q2-1 downbeat extraction."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_physical
from svp_rpe.rpe.physical_features import compute_downbeat_times

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"


def _truth_rows() -> list[dict]:
    return yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))


def test_compute_downbeat_times_is_deterministic() -> None:
    row = _truth_rows()[2]
    audio = load_audio(str(SAMPLE_DIR / row["filename"]))

    first = compute_downbeat_times(audio.y_mono, audio.sr, str(row["time_signature"]))
    second = compute_downbeat_times(audio.y_mono, audio.sr, str(row["time_signature"]))

    assert first == second
    assert first
    assert first == sorted(first)
    assert all(0.0 <= t <= row["duration_sec"] for t in first)


def test_extractor_populates_downbeat_times() -> None:
    row = _truth_rows()[4]
    audio = load_audio(str(SAMPLE_DIR / row["filename"]))

    phys, _, _ = extract_physical(audio)

    assert phys.downbeat_times
    assert all(isinstance(t, float) for t in phys.downbeat_times)
    assert all(0.0 <= t <= phys.duration_sec for t in phys.downbeat_times)


def test_silence_has_no_downbeats() -> None:
    sr = 22050
    y = np.zeros(sr, dtype=np.float32)

    assert compute_downbeat_times(y, sr, "4/4") == []
