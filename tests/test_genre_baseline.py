"""tests/test_genre_baseline.py — Q1-4 genre baselines for scorer_rpe.

Verifies that:
  - Each Q1-4 baseline (pro / loud_pop / acoustic / edm) loads and scores
  - Default baseline keeps PR-#11 snapshot semantics (= "pro")
  - Switching baseline meaningfully changes the score (not a no-op)
  - Unknown baseline names propagate FileNotFoundError
"""
from __future__ import annotations

from pathlib import Path

import pytest

from svp_rpe.eval.scorer_rpe import (
    DEFAULT_BASELINE,
    KNOWN_BASELINES,
    score_rpe,
)
from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_physical

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_input" / "synth_03_mid_groove_g_major.wav"


@pytest.fixture(scope="module")
def synth_03_phys():
    audio = load_audio(str(SAMPLE))
    phys, _, _ = extract_physical(audio)
    return phys


@pytest.mark.parametrize("baseline", KNOWN_BASELINES)
def test_each_baseline_produces_valid_score(synth_03_phys, baseline: str) -> None:
    """All four canonical baselines load and produce a score in [0, 1]."""
    score = score_rpe(synth_03_phys, baseline=baseline)
    assert 0.0 <= score.overall <= 1.0
    for component in (
        score.rms_score,
        score.active_rate_score,
        score.crest_factor_score,
        score.valley_score,
        score.thickness_score,
    ):
        assert 0.0 <= component <= 1.0


def test_default_baseline_is_pro(synth_03_phys) -> None:
    """Default behavior must match explicit baseline='pro' (snapshot stability)."""
    assert DEFAULT_BASELINE == "pro"
    explicit = score_rpe(synth_03_phys, baseline="pro")
    implicit = score_rpe(synth_03_phys)
    assert explicit.overall == implicit.overall
    assert explicit.rms_score == implicit.rms_score


def test_baselines_are_distinguishable(synth_03_phys) -> None:
    """Brief AC: switching baseline must produce >0.05 difference for at least
    one pair, otherwise the genre selection is a no-op for synth fixtures."""
    scores = {
        b: score_rpe(synth_03_phys, baseline=b).overall for b in KNOWN_BASELINES
    }
    pairs = [
        (a, b) for i, a in enumerate(KNOWN_BASELINES) for b in KNOWN_BASELINES[i + 1:]
    ]
    spreads = [(a, b, abs(scores[a] - scores[b])) for a, b in pairs]
    biggest = max(spreads, key=lambda t: t[2])
    assert biggest[2] > 0.05, (
        f"all baselines produce nearly identical scores: {scores}; "
        "Q1-4 genre selection has no measurable effect"
    )


def test_unknown_baseline_raises_file_not_found(synth_03_phys) -> None:
    """Unknown baseline names must surface as FileNotFoundError so typos are
    caught early instead of silently falling back to defaults."""
    with pytest.raises(FileNotFoundError):
        score_rpe(synth_03_phys, baseline="nonexistent_genre")


def test_known_baselines_includes_q1_4_canonical_set() -> None:
    """Regression: KNOWN_BASELINES must contain the four Q1-4 canonical names."""
    assert set(KNOWN_BASELINES) == {"pro", "loud_pop", "acoustic", "edm"}
