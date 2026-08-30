"""Regression coverage for PR #338 diagnostic/refreeze separation."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import birth_probe_executor as bp  # noqa: E402


def _sealed_one_take_result() -> dict:
    references = {
        "R9F-01": bp.RenderObservation.build(
            b"founder-1", bp.FeatureArtifact.from_vector(np.asarray([0.0, 1.0]))
        ),
        "R9F-02": bp.RenderObservation.build(
            b"founder-2", bp.FeatureArtifact.from_vector(np.asarray([1.0, 0.0]))
        ),
    }
    profiles = {
        founder: tuple(profile.profile_id for profile in bp._control_profiles(founder))  # noqa: SLF001
        for founder in references
    }
    c0 = {
        founder: [
            bp.RenderObservation.build(
                reference.wav,
                bp.FeatureArtifact.from_vector(reference.feature.vector),
                control_profile_id=profiles[founder][0],
            )
        ]
        for founder, reference in references.items()
    }
    c1 = {
        founder: [
            bp.RenderObservation.build(
                reference.wav,
                bp.FeatureArtifact.from_vector(reference.feature.vector),
                control_profile_id=profiles[founder][1],
            )
        ]
        for founder, reference in references.items()
    }
    return bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=dict(references),
        pjs_reference=bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
        expected_takes=1,
    )


def test_candidate_bound_diagnostic_never_grants_formal_refreeze(monkeypatch) -> None:
    result = _sealed_one_take_result()
    renderer = object.__new__(bp.GateSynthRenderer)
    monkeypatch.setattr(
        bp,
        "_renderer_consumed_acoustic_sha256",
        lambda _: bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256,  # noqa: SLF001
    )

    diagnostic = bp._issue_candidate_bound_diagnostic_record(  # noqa: SLF001
        result,
        renderer=renderer,
    )

    assert diagnostic["candidate_bytes_bound_to_consumed_buffer"] is True
    assert diagnostic["formal_birth_gate_evidence"] is False
    assert diagnostic["formal_refreeze_eligible"] is False
    assert diagnostic["learning_progression_allowed"] is False
