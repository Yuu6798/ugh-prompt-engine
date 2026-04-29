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
    }
    assert payload["summary"]["total"] == len(results)
    assert 0 <= payload["summary"]["passing"] <= payload["summary"]["total"]

    assert payload["songs"], "validation must produce at least one song result"
    sample = payload["songs"][0]
    assert set(sample) >= {
        "song_id",
        "bpm",
        "key",
        "segments",
        "baseline_score",
        "passes_thresholds",
        "threshold_failures",
    }
    assert set(sample["bpm"]) == {"estimated", "reference", "abs_diff", "p_score"}
    assert set(sample["key"]) == {"estimated", "reference", "weighted_score"}
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
