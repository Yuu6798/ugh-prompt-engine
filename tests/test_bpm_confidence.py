"""tests/test_bpm_confidence.py — Q1-3 BPM confidence redesign.

Verifies that `compute_bpm` returns confidence > 0.7 for the four synth
samples whose extracted BPM lands within ±5 BPM of ground truth, and
that edge cases (silence, single-beat, zero-mean intervals) degrade
gracefully to 0.0 confidence.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.physical_features import (
    BPM_CONFIDENCE_AC_THRESHOLD,
    compute_bpm,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"
GROUND_TRUTH = SAMPLE_DIR / "ground_truth.yaml"


def _truth_within_tolerance(tolerance: float = 5.0) -> list[tuple[str, float]]:
    """Return [(filename, gt_bpm), ...] for songs likely within ±tolerance.

    We evaluate the actual extractor first to keep the AC ('confidence > 0.7
    when within ±5 BPM') self-validating: only assert on songs the extractor
    can hit.
    """
    raw = yaml.safe_load(GROUND_TRUTH.read_text(encoding="utf-8"))
    eligible: list[tuple[str, float]] = []
    for entry in raw:
        audio = load_audio(str(SAMPLE_DIR / entry["filename"]))
        bpm, _ = compute_bpm(audio.y_mono, audio.sr)
        if bpm is not None and abs(bpm - float(entry["bpm"])) <= tolerance:
            eligible.append((entry["filename"], float(entry["bpm"])))
    return eligible


ELIGIBLE_SONGS = _truth_within_tolerance()


@pytest.mark.parametrize(
    ("filename", "gt_bpm"),
    ELIGIBLE_SONGS,
    ids=[name for name, _ in ELIGIBLE_SONGS],
)
def test_in_range_song_has_confidence_above_0_7(filename: str, gt_bpm: float) -> None:
    """AC: 真値 ±5 BPM 以内のとき confidence > 0.7."""
    audio = load_audio(str(SAMPLE_DIR / filename))
    bpm, confidence = compute_bpm(audio.y_mono, audio.sr)

    assert bpm is not None
    assert abs(bpm - gt_bpm) <= 5.0, (
        f"precondition: extractor must land within ±5 BPM of {gt_bpm}, got {bpm}"
    )
    assert confidence is not None
    assert confidence > BPM_CONFIDENCE_AC_THRESHOLD, (
        f"AC violation: {filename} extracted BPM {bpm} (gt {gt_bpm}, within ±5) "
        f"but confidence {confidence} ≤ {BPM_CONFIDENCE_AC_THRESHOLD}"
    )


def test_eligible_songs_cover_majority() -> None:
    """At least 4 of the 5 synth songs should land within ±5 BPM."""
    assert len(ELIGIBLE_SONGS) >= 4, (
        f"only {len(ELIGIBLE_SONGS)} synth song(s) within ±5 BPM; "
        "Q1-3 AC needs 4+ for the parametrized confidence test"
    )


def test_silence_returns_zero_confidence() -> None:
    """Pure silence has no detectable beats → confidence must be 0."""
    sr = 22050
    silent = np.zeros(sr * 5, dtype=np.float32)
    bpm, confidence = compute_bpm(silent, sr)
    # Either bpm is None (preferred) or confidence collapses to 0.
    if bpm is None:
        assert confidence == 0.0
    else:
        assert confidence == 0.0


def test_confidence_is_rounded_to_4_decimals() -> None:
    """Schema invariant: confidence is rounded to 4 decimal places."""
    audio = load_audio(str(SAMPLE_DIR / "synth_03_mid_groove_g_major.wav"))
    _, confidence = compute_bpm(audio.y_mono, audio.sr)
    assert confidence is not None
    # round-trip check: re-rounding should be a no-op
    assert round(confidence, 4) == confidence


def test_two_beat_scenario_does_not_yield_false_certainty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression for Codex P2: a single inter-beat interval has std == 0,
    which would naively yield CV 0 and confidence 1.0 — false certainty.
    With ≥2 intervals required, two-beat outputs degrade to confidence 0.0.
    """
    from svp_rpe.rpe import physical_features

    # Stub librosa.beat.beat_track to return exactly 2 beats. The actual
    # waveform is irrelevant once we override the tracker.
    def fake_beat_track(*, y, sr):
        return 120.0, np.array([0, sr // 2])  # 2 beat frames

    monkeypatch.setattr(physical_features.librosa.beat, "beat_track", fake_beat_track)

    sr = 22050
    bpm, confidence = compute_bpm(np.zeros(sr * 2, dtype=np.float32), sr)
    assert bpm == 120.0
    assert confidence == 0.0, (
        f"two-beat input must not yield confidence > 0 (got {confidence}); "
        "single interval has std == 0 which would otherwise be false certainty"
    )


def test_three_beat_scenario_can_compute_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three regular beats (= 2 equal intervals) gives CV ~0 → confidence 1.0,
    which is now mathematically meaningful (std is over 2 samples)."""
    from svp_rpe.rpe import physical_features

    def fake_beat_track(*, y, sr):
        return 120.0, np.array([0, sr // 2, sr])

    monkeypatch.setattr(physical_features.librosa.beat, "beat_track", fake_beat_track)

    sr = 22050
    _, confidence = compute_bpm(np.zeros(sr * 2, dtype=np.float32), sr)
    assert confidence is not None
    assert confidence > 0.7


def test_legacy_distance_from_120_formula_is_gone() -> None:
    """Regression: synth_03 (BPM ~123) should NOT yield ~0.975 from the
    legacy `1 - abs(bpm-120)/120` formula. The new formula gives ~0.88."""
    audio = load_audio(str(SAMPLE_DIR / "synth_03_mid_groove_g_major.wav"))
    bpm, confidence = compute_bpm(audio.y_mono, audio.sr)
    legacy = max(0.0, min(1.0, 1.0 - abs(bpm - 120) / 120.0))
    # The new value should differ from the legacy value by a non-trivial margin
    assert abs(confidence - legacy) > 0.05, (
        f"new confidence {confidence} too close to legacy {legacy}; "
        "may indicate the redesign was not applied"
    )
