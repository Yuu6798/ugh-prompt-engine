"""Tests for the canonical key-comparison module (src/svp_rpe/keys.py)."""
from __future__ import annotations

import builtins
from typing import Any

import pytest

from svp_rpe.keys import keys_enharmonically_equal, weighted_key_score


def _fake_import_without_mir_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "mir_eval.key":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_weighted_key_score_exact_match() -> None:
    result = weighted_key_score("C major", "C major")
    assert result.score == pytest.approx(1.0)
    assert result.sensor == "mir_eval.key.evaluate"


def test_weighted_key_score_relative_key() -> None:
    result = weighted_key_score("C major", "A minor")
    assert result.score == pytest.approx(0.3)
    assert result.sensor == "mir_eval.key.evaluate"


def test_weighted_key_score_unrelated_key() -> None:
    result = weighted_key_score("C major", "F# minor")
    assert result.score == pytest.approx(0.0)
    assert result.sensor == "mir_eval.key.evaluate"


def test_weighted_key_score_unparseable_observed_scores_zero() -> None:
    result = weighted_key_score("C major", "unknown")
    assert result.score == pytest.approx(0.0)
    assert result.sensor == "mir_eval.key.evaluate"


def test_weighted_key_score_require_mir_eval_happy_path() -> None:
    result = weighted_key_score("C major", "C major", require_mir_eval=True)
    assert result.score == pytest.approx(1.0)
    assert result.sensor == "mir_eval.key.evaluate"


def test_weighted_key_score_fallback_without_mir_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_import_without_mir_eval(monkeypatch)

    match = weighted_key_score("C Major", "C major")
    assert match.score == pytest.approx(1.0)
    assert match.sensor == "exact_key_match"

    mismatch = weighted_key_score("C major", "A minor")
    assert mismatch.score == pytest.approx(0.0)
    assert mismatch.sensor == "exact_key_match"


def test_weighted_key_score_require_mir_eval_raises_without_mir_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_import_without_mir_eval(monkeypatch)

    with pytest.raises(RuntimeError, match=r"\[dev\]"):
        weighted_key_score("C major", "C major", require_mir_eval=True)


@pytest.mark.parametrize(
    ("expected", "observed", "equal"),
    [
        ("Eb major", "D# major", True),
        ("F♯ minor", "F# minor", True),
        ("C major", "A minor", False),
        ("C major", "c  major", True),
        ("weird", "weird", True),
        ("weird", "other", False),
    ],
)
def test_keys_enharmonically_equal(expected: str, observed: str, equal: bool) -> None:
    assert keys_enharmonically_equal(expected, observed) is equal
