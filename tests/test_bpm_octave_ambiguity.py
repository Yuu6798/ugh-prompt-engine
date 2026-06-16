"""tests/test_bpm_octave_ambiguity.py — R2-2 BPM half-fold (×2) detection.

Verifies `detect_bpm_octave_ambiguity` flags the halving pathology
(roundtrip_case_studies.md §4: a true tempo reported at half) while leaving
correctly-estimated, ordinarily-subdivided audio unflagged — in particular the
four Q1-3 synth fixtures, so the confidence penalty never fires on them and the
`tests/test_bpm_confidence.py` contract is preserved.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_rpe
from svp_rpe.rpe.physical_features import (
    BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP,
    compute_bpm,
    detect_bpm_octave_ambiguity,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"

# Q1-3 fixtures (see tests/test_bpm_confidence.py): correctly estimated within
# ±5 BPM. None of these is a halving error, so none must be flagged ambiguous.
Q1_3_FIXTURES = (
    "synth_02_minor_pulse_a_minor.wav",
    "synth_03_mid_groove_g_major.wav",
    "synth_04_waltz_fsharp_minor.wav",
    "synth_05_fast_bright_d_major.wav",
)

SR = 22050


def _impulse_train(bpm: float, *, sr: int = SR, dur: float = 10.0) -> np.ndarray:
    """A clean periodic impulse train at `bpm` (one unit impulse per beat)."""
    y = np.zeros(int(sr * dur), dtype=np.float32)
    period = 60.0 / bpm
    t = 0.0
    while t < dur:
        i = int(t * sr)
        if i < y.size:
            y[i] = 1.0
        t += period
    return y


@pytest.mark.parametrize("true_bpm", [170.0, 175.0])
def test_halving_error_is_flagged_with_both_candidates(true_bpm: float) -> None:
    """A signal whose beats are at `true_bpm`, reported at true_bpm/2, must be
    flagged ambiguous and enumerate both the reported and the ×2 candidate."""
    y = _impulse_train(true_bpm)
    reported = round(true_bpm / 2.0, 2)

    result = detect_bpm_octave_ambiguity(y, SR, reported)

    assert result.is_ambiguous is True
    assert result.candidates == sorted({reported, round(true_bpm, 2)})
    assert result.alt_strength_ratio >= 1.15


def test_correct_tempo_is_not_flagged() -> None:
    """When the reported tempo matches the beat period, the ×2 subdivision lag
    falls between beats → weak autocorrelation → not ambiguous."""
    for bpm in (120.0, 170.0):
        y = _impulse_train(bpm)
        result = detect_bpm_octave_ambiguity(y, SR, bpm)
        assert result.is_ambiguous is False
        assert result.candidates == []


def test_none_or_nonpositive_bpm_is_not_flagged() -> None:
    y = _impulse_train(120.0)
    assert detect_bpm_octave_ambiguity(y, SR, None).is_ambiguous is False
    assert detect_bpm_octave_ambiguity(y, SR, 0.0).is_ambiguous is False


def test_silence_is_not_flagged() -> None:
    silent = np.zeros(SR * 4, dtype=np.float32)
    result = detect_bpm_octave_ambiguity(silent, SR, 120.0)
    assert result.is_ambiguous is False
    assert result.candidates == []


@pytest.mark.parametrize("filename", Q1_3_FIXTURES)
def test_q1_3_fixtures_are_not_flagged(filename: str) -> None:
    """Correctly-estimated fixtures must not be flagged — otherwise the
    extractor confidence cap would fire and erode the Q1-3 contract."""
    audio = load_audio(str(SAMPLE_DIR / filename))
    bpm, _ = compute_bpm(audio.y_mono, audio.sr)
    result = detect_bpm_octave_ambiguity(audio.y_mono, audio.sr, bpm)
    assert result.is_ambiguous is False, (
        f"{filename} (bpm={bpm}) falsely flagged as octave-ambiguous "
        f"(ratio={result.alt_strength_ratio}); the ×2 ratio threshold is too low."
    )


def test_extractor_unflagged_fixture_preserves_confidence() -> None:
    """An unflagged fixture keeps empty candidates and the raw compute_bpm
    confidence (no penalty applied)."""
    audio = load_audio(str(SAMPLE_DIR / "synth_03_mid_groove_g_major.wav"))
    raw_bpm, raw_conf = compute_bpm(audio.y_mono, audio.sr)
    bundle = extract_rpe(audio)
    phys = bundle.physical

    assert phys.bpm_octave_ambiguous is False
    assert phys.bpm_candidates == []
    assert phys.bpm == raw_bpm
    assert phys.bpm_confidence == raw_conf
    # Sanity: this fixture is genuinely above the cap, so an erroneous penalty
    # would be observable.
    assert raw_conf > BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP
