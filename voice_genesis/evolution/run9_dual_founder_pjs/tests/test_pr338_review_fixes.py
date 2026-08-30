"""Regression coverage for PR #338 review findings."""
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


def _one_take_measurement():
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
    positive = dict(references)
    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))
    result = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
        expected_takes=1,
    )
    observations = {
        founder: {
            "reference": [references[founder]],
            "c0": c0[founder],
            "c1": c1[founder],
            "positive_reference": [positive[founder]],
        }
        for founder in references
    }
    return result, observations, pjs


def test_c1_zero_profile_hook_is_identity_and_is_single_use() -> None:
    _, sham = bp._control_profiles("R9F-01")  # noqa: SLF001
    record: dict[str, object] = {}
    hook = bp._run9_zero_controlprofile_sham_duration_hook(  # noqa: SLF001
        sham.to_dict(), record
    )

    assert record["run9_control_profile_attachment"] == {
        "status": "CONSUMED_INERT_ZERO_PROFILE",
        "voice_id": "R9F-01",
        "revision": "r_sham",
        "profile_id": sham.profile_id,
    }
    predicted = [7, 11, 13]
    assert hook(predicted, {"real_phones": ["a"]}) == predicted
    with pytest.raises(bp.BirthProbeError, match="invoked more than once"):
        hook(predicted, {"real_phones": ["a"]})


def test_gate_synth_renderer_routes_c1_through_consumed_duration_hook() -> None:
    renderer = object.__new__(bp.GateSynthRenderer)
    renderer._notes = [bp._ProbeNote(mora="あ", midi=60, duration_beats=1.0)]  # noqa: SLF001
    renderer._tempo = 120.0
    renderer._model_bytes = {}
    renderer._variance_phonemes = {}
    renderer._acoustic_phonemes = {}
    renderer._embeddings = {"R9F-01": np.zeros(384, dtype=np.float32)}

    class FakeGate:
        @staticmethod
        def run_pipeline(*args, **kwargs):
            record = args[6]
            hook = kwargs.get("final_phone_dur_override")
            assert callable(hook), "C1 must enter the synthesis-consumed duration hook"
            predicted = [5, 8]
            assert hook(predicted, {"real_phones": ["a", "i"]}) == predicted
            record["seed"] = bp._RUNTIME_SEED  # noqa: SLF001
            return np.asarray([0.25, -0.25], dtype=np.float32)

    class FakeSoundFile:
        @staticmethod
        def write(path, waveform, sample_rate, *, subtype, format):
            del waveform, sample_rate, subtype, format
            path.write_bytes(b"hook-bound-wav")

        @staticmethod
        def read(file_object, *, dtype, always_2d):
            del file_object, dtype, always_2d
            return np.asarray([0.1, -0.1], dtype=np.float64), 44_100

    renderer._gate = FakeGate()
    renderer._sf = FakeSoundFile()
    _, sham = bp._control_profiles("R9F-01")  # noqa: SLF001
    assert renderer("R9F-01", "c1", 0, sham.to_dict()) == b"hook-bound-wav"


def test_custom_renderer_claiming_candidate_digest_is_downgraded_to_unbound() -> None:
    class LyingCustomRenderer:
        verified_acoustic_sha256 = bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256  # noqa: SLF001

        def __init__(self) -> None:
            self.post_verified = False

        def __call__(self, founder_id, condition, take_index, control_profile):
            del condition, take_index, control_profile
            return founder_id.encode("ascii")

        def verify_inputs_unchanged(self) -> None:
            self.post_verified = True

    def extractor(wav_bytes: bytes) -> bp.FeatureArtifact:
        vector = [0.0, 1.0] if wav_bytes == b"R9F-01" else [1.0, 0.0]
        return bp.FeatureArtifact.from_vector(np.asarray(vector))

    renderer = LyingCustomRenderer()
    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))
    diagnostic, _ = bp.execute_non_adjudicative_diagnostic(
        renderer=renderer,
        pjs_reference=pjs,
        extractor=extractor,
        expected_takes=1,
    )

    assert renderer.post_verified is True
    assert diagnostic["schema"] == bp._UNBOUND_RENDERER_DIAGNOSTIC_SCHEMA  # noqa: SLF001
    assert diagnostic["candidate_acoustic_onnx_sha256"] is None
    assert diagnostic["declared_candidate_acoustic_onnx_sha256"] == (
        bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256  # noqa: SLF001
    )
    assert diagnostic["candidate_bytes_bound_to_consumed_buffer"] is False
    assert diagnostic["formal_refreeze_eligible"] is False
    assert diagnostic["formal_birth_gate_evidence"] is False
    assert diagnostic["learning_progression_allowed"] is False


def test_production_renderer_digest_is_from_consumed_in_memory_model_buffer() -> None:
    renderer = object.__new__(bp.GateSynthRenderer)
    acoustic_bytes = b"exact-in-memory-acoustic-model-buffer"
    renderer._model_bytes = {"acoustic_onnx": acoustic_bytes}
    renderer._input_paths = {"acoustic": Path("acoustic.onnx")}
    renderer._input_hashes = {"artifact_acoustic.onnx": "0" * 64}

    expected = bp.sha256_bytes(acoustic_bytes)
    assert bp._renderer_consumed_acoustic_sha256(renderer) == expected  # noqa: SLF001
    assert renderer.verified_acoustic_sha256 == expected
    assert renderer.verified_acoustic_sha256 != renderer._input_hashes["artifact_acoustic.onnx"]


def test_direct_builder_cannot_mint_candidate_bound_or_refreeze_eligible_record() -> None:
    result, _, _ = _one_take_measurement()
    diagnostic = bp.build_non_adjudicative_diagnostic_record(
        result,
        observed_acoustic_sha256=bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256,  # noqa: SLF001
    )

    assert diagnostic["schema"] == bp._UNBOUND_RENDERER_DIAGNOSTIC_SCHEMA  # noqa: SLF001
    assert diagnostic["candidate_acoustic_onnx_sha256"] is None
    assert diagnostic["declared_candidate_acoustic_onnx_sha256"] == (
        bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256  # noqa: SLF001
    )
    assert diagnostic["candidate_bytes_bound_to_consumed_buffer"] is False
    assert diagnostic["formal_refreeze_eligible"] is False


def test_publisher_rejects_forged_candidate_bound_plain_mapping(tmp_path: Path) -> None:
    result, observations, pjs = _one_take_measurement()
    unbound = bp.build_non_adjudicative_diagnostic_record(
        result,
        observed_acoustic_sha256=bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256,  # noqa: SLF001
    )
    forged = dict(unbound)
    forged["schema"] = bp._NON_ADJUDICATIVE_DIAGNOSTIC_SCHEMA  # noqa: SLF001
    forged["candidate_acoustic_onnx_sha256"] = bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256  # noqa: SLF001
    forged["declared_candidate_acoustic_onnx_sha256"] = None
    forged["candidate_bytes_bound_to_consumed_buffer"] = True
    forged["formal_refreeze_eligible"] = True
    forged.pop("diagnostic_record_sha256", None)
    forged["diagnostic_record_sha256"] = bp.sha256_bytes(bp.canonical_json_bytes(forged))

    with pytest.raises(bp.BirthProbeError, match="not issued by the verified production executor"):
        bp.publish_non_adjudicative_diagnostic_bundle(
            tmp_path / "non_adjudicative_forged",
            forged,
            observations,
            pjs,
        )


def test_publisher_rejects_unregistered_issued_type_instance(tmp_path: Path) -> None:
    result, observations, pjs = _one_take_measurement()
    unbound = bp.build_non_adjudicative_diagnostic_record(
        result,
        observed_acoustic_sha256=bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256,  # noqa: SLF001
    )
    forged_payload = dict(unbound)
    forged_payload["schema"] = bp._NON_ADJUDICATIVE_DIAGNOSTIC_SCHEMA  # noqa: SLF001
    forged_payload["candidate_acoustic_onnx_sha256"] = bp._ALTERNATE_CANDIDATE_ACOUSTIC_SHA256  # noqa: SLF001
    forged_payload["declared_candidate_acoustic_onnx_sha256"] = None
    forged_payload["candidate_bytes_bound_to_consumed_buffer"] = True
    forged_payload["formal_refreeze_eligible"] = True
    forged_payload.pop("diagnostic_record_sha256", None)
    forged_payload["diagnostic_record_sha256"] = bp.sha256_bytes(
        bp.canonical_json_bytes(forged_payload)
    )
    forged_issued = bp._IssuedCandidateDiagnostic(forged_payload)  # noqa: SLF001

    with pytest.raises(bp.BirthProbeError, match="not issued by the verified production executor"):
        bp.publish_non_adjudicative_diagnostic_bundle(
            tmp_path / "non_adjudicative_unregistered",
            forged_issued,
            observations,
            pjs,
        )
