"""Regression coverage for issued-diagnostic immutability and production protocol pins."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import birth_probe_executor as bp  # noqa: E402


def _sealed_result(*, expected_takes: int = 20) -> dict:
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
            for _ in range(expected_takes)
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
            for _ in range(expected_takes)
        ]
        for founder, reference in references.items()
    }
    return bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=dict(references),
        pjs_reference=bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
        expected_takes=expected_takes,
    )


def test_production_diagnostic_rejects_shortened_take_count_before_render() -> None:
    renderer = object.__new__(bp.GateSynthRenderer)
    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))

    with pytest.raises(bp.BirthProbeError, match="fixed 20 takes per founder"):
        bp.execute_non_adjudicative_diagnostic(
            renderer=renderer,
            pjs_reference=pjs,
            expected_takes=1,
        )


def test_production_diagnostic_rejects_custom_extractor_before_render() -> None:
    renderer = object.__new__(bp.GateSynthRenderer)
    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))

    def fake_extractor(_: bytes) -> bp.FeatureArtifact:
        return bp.FeatureArtifact.from_vector(np.asarray([0.0, 1.0]))

    with pytest.raises(bp.BirthProbeError, match="pinned identity feature extractor"):
        bp.execute_non_adjudicative_diagnostic(
            renderer=renderer,
            pjs_reference=pjs,
            extractor=fake_extractor,
        )


def test_direct_issue_helper_does_not_receive_publication_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = object.__new__(bp.GateSynthRenderer)
    monkeypatch.setattr(
        bp,
        "_renderer_consumed_acoustic_sha256",
        lambda _: bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256,  # noqa: SLF001
    )
    diagnostic = bp._issue_candidate_bound_diagnostic_record(  # noqa: SLF001
        _sealed_result(),
        renderer=renderer,
    )

    assert id(diagnostic) not in bp._ISSUED_CANDIDATE_DIAGNOSTIC_IDS  # noqa: SLF001
    assert diagnostic["formal_refreeze_eligible"] is False


def test_registered_issued_diagnostic_rejects_post_issue_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    renderer = object.__new__(bp.GateSynthRenderer)
    renderer.verify_inputs_unchanged = lambda: None
    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))
    result = _sealed_result()
    observations = {
        founder: {condition: [] for condition in bp._CONDITIONS}  # noqa: SLF001
        for founder in bp._FOUNDER_IDS  # noqa: SLF001
    }

    monkeypatch.setattr(
        bp,
        "_renderer_consumed_acoustic_sha256",
        lambda _: bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256,  # noqa: SLF001
    )
    monkeypatch.setattr(
        bp,
        "execute_birth_gate",
        lambda **_: (result, observations),
    )

    diagnostic, returned_observations = bp.execute_non_adjudicative_diagnostic(
        renderer=renderer,
        pjs_reference=pjs,
    )
    assert id(diagnostic) in bp._ISSUED_CANDIDATE_DIAGNOSTIC_IDS  # noqa: SLF001

    diagnostic["diagnostic_record_sha256"] = "0" * 64
    with pytest.raises(bp.BirthProbeError, match="changed after verified production issuance"):
        bp.publish_non_adjudicative_diagnostic_bundle(
            tmp_path / "non_adjudicative_mutated",
            diagnostic,
            returned_observations,
            pjs,
        )
