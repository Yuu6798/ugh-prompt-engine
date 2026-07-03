"""tests/test_bpm_octave_ambiguity.py — R2-2 BPM half-fold (×2) detection.

Verifies `detect_bpm_octave_ambiguity` flags the halving pathology
(roundtrip_case_studies.md §4: a true tempo reported at half) while leaving
correctly-estimated, ordinarily-subdivided audio unflagged — in particular the
four Q1-3 synth fixtures, so the confidence penalty never fires on them and the
`tests/test_bpm_confidence.py` contract is preserved.
"""
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import pytest

from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_rpe
from svp_rpe.rpe import extractor as extractor_mod
from svp_rpe.rpe.physical_features import (
    BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP,
    BPM_OCTAVE_HOP_LENGTH,
    BPM_OCTAVE_NEIGHBORHOOD,
    BPM_OCTAVE_RATIO_THRESHOLD,
    BpmOctaveAmbiguity,
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


def _two_tempo_train(
    strong_bpm: float,
    weak_bpm: float,
    *,
    strong_amp: float = 1.0,
    weak_amp: float = 0.2,
    sr: int = SR,
    dur: float = 16.0,
) -> np.ndarray:
    """A dominant pulse train at `strong_bpm` plus a weaker one at `weak_bpm`.

    Models a sub-octave collapse: the slower grid point (`weak_bpm`) carries some
    onset support — so the prior can pick it — but the true faster beat
    (`strong_bpm`) dominates. The weak train gives the reported (slower) lag a
    *stable positive* autocorrelation. A single-tempo impulse train would instead
    leave a degenerate near-zero trough at a 2/3 subharmonic lag (primary_strength
    ≈ 0), which is platform-unstable (float accumulation flips the ratio across
    BLAS/FFT threading) — exactly what made an earlier pure-impulse version of this
    test pass locally but fail in CI on identical library versions.
    """
    y = np.zeros(int(sr * dur), dtype=np.float32)
    for bpm, amp in ((strong_bpm, strong_amp), (weak_bpm, weak_amp)):
        period = 60.0 / bpm
        t = 0.0
        while t < dur:
            i = int(t * sr)
            if i < y.size:
                y[i] += amp
            t += period
    return y


def _old_exact_double_ratio(y: np.ndarray, reported: float) -> float:
    """Reproduce the pre-R2-2b detector's single exactly-2×bpm lag ratio.

    The old implementation looked only at ``_lag(reported * 2.0)``. This helper
    recomputes that one-lag ratio so a test can pin the grid-quantization bug:
    when the real pulse is quantized off a clean octave, that single lag misses it
    (ratio < BPM_OCTAVE_RATIO_THRESHOLD) while the neighborhood scan catches it.
    """
    hop = BPM_OCTAVE_HOP_LENGTH
    onset_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=hop)
    ac = librosa.autocorrelate(onset_env)
    n = onset_env.size

    def _lag(tempo: float) -> int:
        return int(round((60.0 / tempo) * SR / hop))

    primary_lag = _lag(reported)
    subdivision_lag = _lag(reported * 2.0)
    primary_strength = float(ac[primary_lag]) / (n - primary_lag)
    subdivision_strength = float(ac[subdivision_lag]) / (n - subdivision_lag)
    return subdivision_strength / primary_strength


@pytest.mark.parametrize("true_bpm", [170.0, 175.0])
def test_halving_error_is_flagged_with_both_candidates(true_bpm: float) -> None:
    """A signal whose beats are at `true_bpm`, reported at true_bpm/2, must be
    flagged ambiguous and enumerate the reported tempo plus a faster candidate in
    the subdivision neighborhood. The faster candidate is the *recovered* tempo
    from the dominant lag (lag-quantized), not necessarily exactly `true_bpm`."""
    y = _impulse_train(true_bpm)
    reported = round(true_bpm / 2.0, 2)
    lo, hi = BPM_OCTAVE_NEIGHBORHOOD

    result = detect_bpm_octave_ambiguity(y, SR, reported)

    assert result.is_ambiguous is True
    assert len(result.candidates) == 2
    assert reported in result.candidates
    faster = max(result.candidates)
    assert lo * reported <= faster <= hi * reported
    assert result.alt_strength_ratio >= BPM_OCTAVE_RATIO_THRESHOLD


def test_grid_quantized_halving_is_flagged() -> None:
    """R2-2b headline case (roundtrip_corpus_screen.md): a true ~172 BPM pulse
    reported at 89.1 is a 1.93× halving — off a clean 2.0× octave because the BPM
    estimate is grid-quantized. The neighborhood scan must still flag it and
    recover a candidate near the true ~172 tempo."""
    y = _impulse_train(172.3)
    result = detect_bpm_octave_ambiguity(y, SR, 89.1)

    assert result.is_ambiguous is True
    assert 89.1 in result.candidates
    faster = max(result.candidates)
    assert faster == pytest.approx(172.0, abs=3.0)


def test_neighborhood_catches_what_exact_double_misses() -> None:
    """The bug R2-2b fixes: when the real pulse is quantized off a clean octave so
    its lag bin differs from _lag(reported×2), the old single-lag-at-2× detector
    returns a sub-threshold ratio (miss) while the neighborhood scan flags it.

    reported=45.5, true pulse=89.18 (1.96×): the exactly-2× lag (28) is between
    pulses (ratio < threshold) but the true pulse lands at lag 29."""
    reported, true_bpm = 45.5, 89.18
    y = _impulse_train(true_bpm)

    # Old behavior (pinned): the single exactly-2× lag misses the off-octave pulse.
    assert _old_exact_double_ratio(y, reported) < BPM_OCTAVE_RATIO_THRESHOLD

    # New behavior: the neighborhood scan catches it.
    result = detect_bpm_octave_ambiguity(y, SR, reported)
    assert result.is_ambiguous is True
    assert reported in result.candidates


def test_subharmonic_collapse_is_flagged() -> None:
    """R2-2d: the 117.45 attractor — a true ~172 BPM beat reported at 117.45 is a
    3:2 sub-octave collapse (172.3/117.45 = 1.47×), NOT a clean ÷2, so the old
    1.8–2.2× octave window could not see it. The widened 1.4–2.2× window flags it
    and recovers a candidate near the true ~172 tempo.

    Uses a two-tempo signal (dominant 172.3 + weak 117.45 grid support) so the
    reported-lag autocorrelation is stably positive — see _two_tempo_train."""
    y = _two_tempo_train(172.3, 117.45)
    result = detect_bpm_octave_ambiguity(y, SR, 117.45)

    assert result.is_ambiguous is True
    assert 117.45 in result.candidates
    assert max(result.candidates) == pytest.approx(172.0, abs=3.0)
    # Comfortably above threshold (≈1.5–1.8), not a knife-edge at primary≈0.
    assert result.alt_strength_ratio >= 1.3

    # The newly-added 1.4–1.8× window region is what catches it: the pre-R2-2d
    # octave-only window (1.8–2.2×) would not have, since the dominant faster lag
    # sits at ~1.47× of the reported tempo.
    octave_lo = int(round((60.0 / (117.45 * 2.2)) * SR / BPM_OCTAVE_HOP_LENGTH))
    octave_hi = int(round((60.0 / (117.45 * 1.8)) * SR / BPM_OCTAVE_HOP_LENGTH))
    true_lag = int(round((60.0 / 172.3) * SR / BPM_OCTAVE_HOP_LENGTH))
    assert not (octave_lo <= true_lag <= octave_hi), (
        "true-pulse lag must fall outside the old 1.8–2.2× window for this to "
        "pin the widening; otherwise R2-2b already covered it."
    )


@pytest.mark.parametrize("bpm", [120.0, 170.0, 172.3])
def test_correct_tempo_is_not_flagged(bpm: float) -> None:
    """When the reported tempo matches the beat period, the faster-side window lags
    fall between beats → weak autocorrelation → not ambiguous. Includes 172.3 (a
    fast tempo near the attractor's true value) so the widened 1.4× window is shown
    not to false-flag a correctly-detected fast track."""
    y = _impulse_train(bpm)
    result = detect_bpm_octave_ambiguity(y, SR, bpm)
    assert result.is_ambiguous is False
    assert result.candidates == ()


def test_none_or_nonpositive_bpm_is_not_flagged() -> None:
    y = _impulse_train(120.0)
    assert detect_bpm_octave_ambiguity(y, SR, None).is_ambiguous is False
    assert detect_bpm_octave_ambiguity(y, SR, 0.0).is_ambiguous is False


def test_silence_is_not_flagged() -> None:
    silent = np.zeros(SR * 4, dtype=np.float32)
    result = detect_bpm_octave_ambiguity(silent, SR, 120.0)
    assert result.is_ambiguous is False
    assert result.candidates == ()


def test_short_clip_correct_tempo_is_not_flagged() -> None:
    """Regression: the raw autocorrelation sum is length-biased (the smaller
    subdivision lag has more overlapping terms), which on a short clip inflated
    the ratio and false-flagged a correctly estimated tempo. The overlap
    normalization must keep a 3s correct-tempo clip below the threshold."""
    audio = load_audio(str(SAMPLE_DIR / "synth_03_mid_groove_g_major.wav"))
    bpm, _ = compute_bpm(audio.y_mono, audio.sr)
    short = audio.y_mono[: audio.sr * 3]
    result = detect_bpm_octave_ambiguity(short, audio.sr, bpm)
    assert result.is_ambiguous is False, (
        f"short clip (bpm={bpm}) false-flagged (ratio={result.alt_strength_ratio}); "
        "overlap normalization regressed."
    )


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


@pytest.mark.slow
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


@pytest.mark.slow
def test_extractor_ambiguous_fixture_corrects_bpm_and_caps_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When detection flags ambiguity (R2-2c), the extractor corrects the reported
    bpm to the recovered faster tempo (max candidate), caps bpm_confidence, and
    keeps both tempi in bpm_candidates with the original halved reading as the
    minimum. Forced via monkeypatch so the branch is exercised deterministically
    regardless of the fixture's true tempo."""
    audio = load_audio(str(SAMPLE_DIR / "synth_03_mid_groove_g_major.wav"))
    raw_bpm, raw_conf = compute_bpm(audio.y_mono, audio.sr)
    # Precondition: raw confidence is above the cap, so the cap is observable.
    assert raw_conf > BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP

    recovered = round(raw_bpm * 2.0, 2)

    def fake_detect(y, sr, bpm, **kwargs):  # noqa: ANN001
        candidates = tuple(sorted({round(bpm, 2), round(bpm * 2.0, 2)}))
        return BpmOctaveAmbiguity(True, candidates, 1.3)

    monkeypatch.setattr(extractor_mod, "detect_bpm_octave_ambiguity", fake_detect)
    phys = extract_rpe(audio).physical

    assert phys.bpm_octave_ambiguous is True
    # Reported bpm is corrected to the faster recovered tempo (max candidate)…
    assert phys.bpm == recovered
    assert phys.bpm == max(phys.bpm_candidates)
    # …while the original halved reading survives as the minimum candidate.
    assert min(phys.bpm_candidates) == round(raw_bpm, 2)
    assert phys.bpm_candidates == [round(raw_bpm, 2), recovered]
    assert phys.bpm_confidence == BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP
