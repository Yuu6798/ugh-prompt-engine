"""Smoke tests for scripts/validate_against_truth.py."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("mir_eval")

from scripts import validate_against_truth as vat  # noqa: E402

_RESULTS: list[vat.SongValidation] | None = None


def _results() -> list[vat.SongValidation]:
    global _RESULTS
    if _RESULTS is None:
        _RESULTS = [vat.evaluate_song(song) for song in vat.load_truth()]
    return _RESULTS


def test_validation_json_schema_has_required_keys() -> None:
    results = _results()
    payload = json.loads(vat.render_json(results))

    assert set(payload) == {"thresholds", "songs", "summary"}
    assert set(payload["thresholds"]) == {
        "bpm_max_abs_diff",
        "bpm_check_uses_octave_adjusted_diff",
        "key_min_score",
        "segment_f_min_at_3s",
        "time_signature_require_match",
        "downbeat_window_sec",
        "downbeat_hit_rate_min",
        "chord_event_hit_rate_min",
        "melody_pitch_accuracy_min",
        "melody_voicing_recall_min",
        "melody_cents_tolerance",
    }
    assert payload["summary"]["total"] == len(results)
    assert 0 <= payload["summary"]["passing"] <= payload["summary"]["total"]

    assert payload["songs"], "validation must produce at least one song result"
    sample = payload["songs"][0]
    assert set(sample) >= {
        "song_id",
        "bpm",
        "key",
        "time_signature",
        "downbeats",
        "chords",
        "melody",
        "segments",
        "baseline_score",
        "passes_thresholds",
        "threshold_failures",
    }
    assert set(sample["bpm"]) == {
        "estimated",
        "reference",
        "abs_diff",
        "octave_adjustment",
        "octave_adjusted_estimated",
        "octave_adjusted_abs_diff",
        "p_score",
    }
    assert set(sample["key"]) == {"estimated", "reference", "weighted_score"}
    assert set(sample["time_signature"]) == {"estimated", "reference", "confidence", "match"}
    assert set(sample["downbeats"]) == {
        "n_reference",
        "n_estimated",
        "hit_rate",
        "mean_abs_error_sec",
        "window_sec",
    }
    assert set(sample["chords"]) == {
        "n_reference",
        "n_estimated",
        "event_hit_rate",
        "unique_reference",
        "unique_matched",
    }
    assert set(sample["melody"]) == {
        "n_reference_frames",
        "n_voiced_frames",
        "voicing_recall",
        "pitch_accuracy_50c",
        "mean_abs_cents",
    }
    assert set(sample["segments"]) >= {
        "n_reference",
        "n_estimated",
        "f_at_0_5s",
        "f_at_3_0s",
    }
    assert set(sample["baseline_score"]) == {
        "profile",
        "overall",
        "rms_score",
        "active_rate_score",
        "crest_factor_score",
        "valley_score",
        "thickness_score",
    }
    assert sample["baseline_score"]["profile"] in {
        "pro",
        "loud_pop",
        "acoustic",
        "edm",
    }


def test_validation_at_least_three_songs_pass_thresholds() -> None:
    """Q0-4 check: at least 3 of 5 synthetic songs should pass thresholds."""
    results = _results()
    passing = sum(1 for r in results if r.passes_thresholds)
    assert passing >= 3, (
        f"only {passing}/{len(results)} songs pass thresholds; "
        "Q0-4 expects at least 3"
    )


def test_synth_01_bpm_octave_error_is_explicitly_modeled() -> None:
    """Q1-3 follow-up: synth_01 remains raw-double, but octave diff passes."""
    result = next(r for r in _results() if r.song_id == "synth_01_slow_pad_c_major")

    assert result.bpm.abs_diff is not None
    assert result.bpm.abs_diff >= vat.BPM_MAX_ABS_DIFF
    assert result.bpm.octave_adjustment == "half"
    assert result.bpm.octave_adjusted_abs_diff is not None
    assert result.bpm.octave_adjusted_abs_diff < vat.BPM_MAX_ABS_DIFF
    assert "BPM" not in " ".join(result.threshold_failures)


def test_downbeat_validation_hits_four_of_five_synth_samples() -> None:
    """Q2-1 regression: downbeat hit-rate should pass on at least 4/5 samples."""
    results = _results()
    passing = sum(
        1
        for r in results
        if r.downbeats.hit_rate >= vat.DOWNBEAT_HIT_RATE_MIN
    )

    assert passing >= 4, (
        f"only {passing}/{len(results)} songs meet downbeat hit-rate threshold"
    )


def test_chord_validation_hits_all_synth_samples() -> None:
    """Q2-2 regression: chord event hit-rate should pass on all synth samples."""
    results = _results()

    assert all(r.chords.event_hit_rate >= vat.CHORD_EVENT_HIT_RATE_MIN for r in results)
    assert all(len(r.chords.unique_matched) >= 3 for r in results)


def test_melody_validation_hits_all_synth_samples() -> None:
    """Q2-3 regression: pyin melody accuracy should pass on all synth samples."""
    results = _results()

    assert all(r.melody.voicing_recall >= vat.MELODY_VOICING_RECALL_MIN for r in results)
    assert all(r.melody.pitch_accuracy_50c >= vat.MELODY_PITCH_ACCURACY_MIN for r in results)
