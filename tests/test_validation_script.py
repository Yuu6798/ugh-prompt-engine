"""tests/test_validation_script.py — smoke test for scripts/validate_against_truth.py.

Runs the validation script in `--json` mode and verifies the output schema.
The actual numerical thresholds are exercised by the script itself; this
test only guards the public contract that downstream consumers (Q0-5
validation.md generation) depend on.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("mir_eval")

from scripts import validate_against_truth as vat  # noqa: E402


def test_validation_json_schema_has_required_keys() -> None:
    results = [vat.evaluate_song(song) for song in vat.load_truth()]
    payload = json.loads(vat.render_json(results))

    assert set(payload) == {"thresholds", "songs", "summary"}
    assert set(payload["thresholds"]) == {
        "bpm_max_abs_diff",
        "key_min_score",
        "segment_f_min_at_3s",
        "time_signature_require_match",
        "downbeat_window_sec",
        "downbeat_hit_rate_min",
        "chord_event_hit_rate_min",
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
        "segments",
        "baseline_score",
        "passes_thresholds",
        "threshold_failures",
    }
    assert set(sample["bpm"]) == {"estimated", "reference", "abs_diff", "p_score"}
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
    """Brief AC: 5 曲のうち少なくとも 3 曲が --check の最低基準を満たす."""
    results = [vat.evaluate_song(song) for song in vat.load_truth()]
    passing = sum(1 for r in results if r.passes_thresholds)
    assert passing >= 3, (
        f"only {passing}/{len(results)} songs pass thresholds; "
        "Q0-4 expects at least 3 (see Brief)"
    )


def test_downbeat_validation_hits_four_of_five_synth_samples() -> None:
    """Q2-1 regression: downbeat hit-rate should pass on at least 4/5 samples."""
    results = [vat.evaluate_song(song) for song in vat.load_truth()]
    passing = sum(
        1
        for r in results
        if r.downbeats.hit_rate >= vat.DOWNBEAT_HIT_RATE_MIN
    )

    assert passing >= 4, (
        f"only {passing}/{len(results)} songs meet downbeat hit-rate threshold"
    )
