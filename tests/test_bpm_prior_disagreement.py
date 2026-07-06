"""tests/test_bpm_prior_disagreement.py — R2-2f BPM high-prior tie-break
disagreement detection.

Pins the decision-table cases from scratchpad/bpm_prior_decision_table.yaml
(mechanically extracted repo records; see docs/roundtrip_corpus_screen.md
finding #6 and docs/metrics.md "BPM prior-disagreement detection (R2-2f)") via
monkeypatch of `compute_bpm`, since the real halved/prior-disagreeing audio
(punk, so_what_run, etc.) is not committed to the repo (only 5 synth fixtures,
all of which the Phase A gate confirmed stay OUTSIDE the (1.35, 1.6) window).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from svp_rpe.io.audio_loader import load_audio
from svp_rpe.rpe.extractor import extract_rpe
from svp_rpe.rpe import extractor as extractor_mod
from svp_rpe.rpe import physical_features as physical_features_mod
from svp_rpe.rpe.physical_features import (
    BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP,
    BPM_PRIOR_DISAGREEMENT_WINDOW,
    BpmOctaveAmbiguity,
    BpmPriorDisagreement,
    compute_bpm,
    detect_bpm_prior_disagreement,
)
from svp_rpe.transcribe.score_draft import _bpm_untrusted

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "examples" / "sample_input"


def _patch_high_prior(monkeypatch: pytest.MonkeyPatch, high_bpm: float | None) -> None:
    """Force compute_bpm(..., start_bpm=180.0) (the probe call) to return
    `high_bpm`, while leaving other start_bpm values untouched — the default
    prior estimate D passed into detect_bpm_prior_disagreement is a plain
    float argument, not recomputed, so only the probe's internal call needs
    patching."""
    real_compute_bpm = physical_features_mod.compute_bpm

    def fake_compute_bpm(y, sr, *, start_bpm=120.0):  # noqa: ANN001
        if start_bpm == pytest.approx(180.0):
            return (high_bpm, 0.8) if high_bpm is not None else (None, 0.0)
        return real_compute_bpm(y, sr, start_bpm=start_bpm)

    monkeypatch.setattr(physical_features_mod, "compute_bpm", fake_compute_bpm)


# --- decision-table cases (scratchpad/bpm_prior_decision_table.yaml) --------

DECISION_TABLE_CASES = [
    # (id, default_bpm D, high_prior_bpm H, expect_fire, expect_corrected_bpm)
    pytest.param("punk", 123.05, 181.45, True, 181.45, id="punk_fires_and_corrects"),
    pytest.param("so_what_run", 117.45, 172.3, True, 172.3, id="so_what_fires"),
    pytest.param("yaoyorozu_shinwa", 95.7, 198.8, False, None, id="yaoyorozu_doubling_artifact_no_fire"),
    pytest.param("shiden_no_inori", 172.27, 172.3, False, None, id="shiden_agreement_no_fire"),
]


@pytest.mark.parametrize(
    ("track_id", "default_bpm", "high_bpm", "expect_fire", "expect_corrected"),
    DECISION_TABLE_CASES,
)
def test_decision_table_cases(
    monkeypatch: pytest.MonkeyPatch,
    track_id: str,
    default_bpm: float,
    high_bpm: float,
    expect_fire: bool,
    expect_corrected: float | None,
) -> None:
    _patch_high_prior(monkeypatch, high_bpm)
    y = np.zeros(22050 * 2, dtype=np.float32)  # unused by the fake compute_bpm

    result = detect_bpm_prior_disagreement(y, 22050, default_bpm)

    assert result.is_disagreement is expect_fire, track_id
    if expect_fire:
        assert result.candidates == tuple(sorted({round(default_bpm, 2), round(high_bpm, 2)}))
        assert max(result.candidates) == expect_corrected
    else:
        assert result.candidates == ()


def test_h_is_none_does_not_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the probe itself fails to detect a tempo (H=None), the detector
    must not fire — there is nothing to disagree with."""
    _patch_high_prior(monkeypatch, None)
    y = np.zeros(22050 * 2, dtype=np.float32)

    result = detect_bpm_prior_disagreement(y, 22050, 117.45)

    assert result.is_disagreement is False
    assert result.candidates == ()
    assert result.prior_ratio is None


@pytest.mark.parametrize("boundary_ratio", [1.35, 1.6])
def test_window_boundary_is_exclusive_no_fire(
    monkeypatch: pytest.MonkeyPatch, boundary_ratio: float
) -> None:
    """The (1.35, 1.6) window is exclusive at both ends per the design memo
    ('窓は排他的境界で良い'): a ratio landing exactly on 1.35 or 1.6 must not
    fire."""
    default_bpm = 100.0
    high_bpm = round(default_bpm * boundary_ratio, 2)
    _patch_high_prior(monkeypatch, high_bpm)
    y = np.zeros(22050 * 2, dtype=np.float32)

    result = detect_bpm_prior_disagreement(y, 22050, default_bpm)

    assert result.is_disagreement is False
    assert result.candidates == ()


def test_window_constant_matches_design_memo() -> None:
    assert BPM_PRIOR_DISAGREEMENT_WINDOW == (1.35, 1.6)


def test_bpm_none_or_nonpositive_does_not_fire() -> None:
    y = np.zeros(22050 * 2, dtype=np.float32)
    assert detect_bpm_prior_disagreement(y, 22050, None).is_disagreement is False
    assert detect_bpm_prior_disagreement(y, 22050, 0.0).is_disagreement is False


# --- extractor integration ---------------------------------------------------


@pytest.mark.slow
def test_extractor_skips_probe_when_r2_2_already_fired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When R2-2 (detect_bpm_octave_ambiguity) already flagged ambiguity, the
    R2-2f probe must NOT run — otherwise a track could be double-corrected."""
    audio = load_audio(str(SAMPLE_DIR / "synth_03_mid_groove_g_major.wav"))
    raw_bpm, _ = compute_bpm(audio.y_mono, audio.sr)

    def fake_octave(y, sr, bpm, **kwargs):  # noqa: ANN001
        candidates = tuple(sorted({round(bpm, 2), round(bpm * 2.0, 2)}))
        return BpmOctaveAmbiguity(True, candidates, 1.3)

    calls = []

    def fake_prior(y, sr, bpm, **kwargs):  # noqa: ANN001
        calls.append(bpm)
        return BpmPriorDisagreement(False, (), None)

    monkeypatch.setattr(extractor_mod, "detect_bpm_octave_ambiguity", fake_octave)
    monkeypatch.setattr(extractor_mod, "detect_bpm_prior_disagreement", fake_prior)

    bundle = extract_rpe(audio)

    assert calls == [], "R2-2f probe must not run when R2-2 already fired"
    # R2-2 alone still corrects bpm/flags as before.
    assert bundle.physical.bpm_octave_ambiguous is True
    assert bundle.physical.bpm_prior_disagreement is False
    assert bundle.physical.bpm == round(raw_bpm * 2.0, 2)


@pytest.mark.slow
def test_extractor_fires_prior_disagreement_sets_flags_and_trust_gate_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When R2-2 does not fire but R2-2f does, the extractor must: correct bpm
    to max(candidates), cap confidence, set both bpm_octave_ambiguous=True and
    bpm_prior_disagreement=True (provenance), and the transcribe trust gate
    must treat the corrected bpm as sensor-blind (configured with zero extra
    wiring, per the design memo)."""
    audio = load_audio(str(SAMPLE_DIR / "synth_03_mid_groove_g_major.wav"))
    raw_bpm, raw_conf = compute_bpm(audio.y_mono, audio.sr)
    assert raw_conf > BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP  # cap must be observable

    recovered = round(raw_bpm * 1.467, 2)

    def fake_octave(y, sr, bpm, **kwargs):  # noqa: ANN001
        return BpmOctaveAmbiguity(False, (), 0.0)

    def fake_prior(y, sr, bpm, **kwargs):  # noqa: ANN001
        candidates = tuple(sorted({round(bpm, 2), recovered}))
        return BpmPriorDisagreement(True, candidates, 1.467)

    monkeypatch.setattr(extractor_mod, "detect_bpm_octave_ambiguity", fake_octave)
    monkeypatch.setattr(extractor_mod, "detect_bpm_prior_disagreement", fake_prior)

    bundle = extract_rpe(audio)
    phys = bundle.physical

    assert phys.bpm == recovered
    assert phys.bpm == max(phys.bpm_candidates)
    assert min(phys.bpm_candidates) == round(raw_bpm, 2)
    assert phys.bpm_octave_ambiguous is True
    assert phys.bpm_prior_disagreement is True
    assert phys.bpm_confidence == BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP

    # Shared trust-gate semantics: score_draft keys off bpm_octave_ambiguous,
    # which R2-2f sets — no additional wiring is required for the gate to close.
    assert _bpm_untrusted(bundle) is True
