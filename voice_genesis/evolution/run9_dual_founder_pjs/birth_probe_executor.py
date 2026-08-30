#!/usr/bin/env python3
"""RUN9 rev 0.6 Birth Probe executor and closed-world result consumer.

The scientific decision rules live in the pinned rev 0.6 protocol.  This
module supplies the execution boundary for exact asset preflight, fixed
renders, WORLD feature extraction, C0/C1/positive replay audits,
founder/PJS distances, and an atomic evidence bundle.  Any prerequisite
that is not actually bound to the execution semantics is rejected before
measurement rather than being silently treated as implemented.

No learning operation is exposed here.  In particular, this module cannot
build or pin ``learning_recipe_sha``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import math
import os
import platform
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Sequence

_EXECUTOR_PATH = Path(__file__).resolve()
_EXECUTOR_LOAD_SHA256 = hashlib.sha256(_EXECUTOR_PATH.read_bytes()).hexdigest()
np = importlib.import_module("numpy")

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[2]
_FOUNDER_IDS = ("R9F-01", "R9F-02")
_CONDITIONS = ("reference", "c0", "c1", "positive_reference")
_RESULT_SCHEMA = "run9-birth-gate-evidence/0.6"
_FEATURE_SCHEMA = b"RUN9-IDENTITY-FEATURE-F64LE/1\x00"
_RUNTIME_SEED = 42
_DIRECT_DEPENDENCY_MANIFEST_PATH = _THIS_DIR / "inputs" / "dependency_pins_manifest.json"
_DIRECT_DEPENDENCY_PACKAGES = ("numpy", "scipy", "soundfile", "PyYAML", "onnxruntime")
_PYWORLD_PIN_VERSION = "0.3.5"
_LEGACY_REV06_PUBLICATION_DISABLED = True
_RUN9_CONTROL_PROFILE_KEYS = frozenset({
    "schema",
    "voice_id",
    "branch",
    "revision",
    "parent_revision",
    "partitions",
    "profile_id",
})
_RUN9_CONTROL_PARTITION_KEYS = frozenset({"trait_control", "technique_control"})


class BirthProbeError(RuntimeError):
    """A fail-closed preflight, render, feature, or publication failure."""


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical UTF-8 JSON for evidence identity and deterministic tests."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _consume_run9_zero_controlprofile_sham(profile: Mapping[str, Any]) -> Dict[str, str]:
    """Validate the exact inert C1 profile and build its synthesis attestation."""
    if not isinstance(profile, Mapping) or set(profile) != _RUN9_CONTROL_PROFILE_KEYS:
        raise BirthProbeError("RUN9 C1 ControlProfile top-level keys are not closed-world")
    expected_literals = {
        "schema": "run9-control-profile/1.0",
        "branch": "CONTROL",
        "revision": "r_sham",
        "parent_revision": "r0",
    }
    for key, expected in expected_literals.items():
        if profile.get(key) != expected:
            raise BirthProbeError(f"RUN9 C1 ControlProfile {key} must be {expected!r}")
    voice_id = profile.get("voice_id")
    profile_id = profile.get("profile_id")
    if not isinstance(voice_id, str) or not voice_id:
        raise BirthProbeError("RUN9 C1 ControlProfile voice_id must be a non-empty string")
    if not isinstance(profile_id, str) or re.fullmatch(r"[0-9a-f]{16}", profile_id) is None:
        raise BirthProbeError(
            "RUN9 C1 ControlProfile profile_id must be 16 lowercase hex characters"
        )
    partitions = profile.get("partitions")
    if not isinstance(partitions, Mapping) or set(partitions) != _RUN9_CONTROL_PARTITION_KEYS:
        raise BirthProbeError("RUN9 C1 ControlProfile partitions are not closed-world")
    if any(not isinstance(partitions[key], Mapping) or partitions[key] for key in partitions):
        raise BirthProbeError("RUN9 C1 ControlProfile partitions must both be empty objects")
    return {
        "status": "CONSUMED_INERT_ZERO_PROFILE",
        "voice_id": voice_id,
        "revision": "r_sham",
        "profile_id": profile_id,
    }


def _run9_zero_controlprofile_sham_duration_hook(
    profile: Mapping[str, Any],
    record: Dict[str, Any],
) -> Callable[[list[int], dict], list[int]]:
    """Bind the zero profile to a synthesis hook while preserving output semantics."""
    attestation = _consume_run9_zero_controlprofile_sham(profile)
    called = False

    def apply(predicted: list[int], context: dict) -> list[int]:
        nonlocal called
        if called:
            raise BirthProbeError("RUN9 C1 zero-profile synthesis hook was invoked more than once")
        if not isinstance(predicted, list) or any(
            isinstance(value, bool) or not isinstance(value, int) for value in predicted
        ):
            raise BirthProbeError("RUN9 C1 synthesis hook received a non-integer duration vector")
        if not isinstance(context, dict):
            raise BirthProbeError("RUN9 C1 synthesis hook received an invalid context")
        if "run9_control_profile_attachment" in record:
            raise BirthProbeError("RUN9 C1 synthesis attestation existed before hook consumption")
        called = True
        record["run9_control_profile_attachment"] = attestation
        setattr(apply, "run9_consumed", True)
        return list(predicted)

    setattr(apply, "run9_consumed", False)
    return apply


def _read_once(path: Path, *, label: str) -> tuple[bytes, str]:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise BirthProbeError(f"{label}: unable to read {path}: {exc}") from exc
    return value, sha256_bytes(value)


_HELPER_PROVENANCE_PATHS: Dict[str, Path] = {
    "run9_schema_sha256": _THIS_DIR / "run9_schema.py",
    "run9_controlprofile_sha256": _THIS_DIR / "run9_controlprofile.py",
    "gate_synth_sha256": _REPO_ROOT / "voice_genesis" / "foundry" / "s1_gate" / "gate_synth.py",
}
_HELPER_MODULE_NAMES = {
    "run9_schema_sha256": "run9_schema",
    "run9_controlprofile_sha256": "run9_controlprofile",
    "gate_synth_sha256": "gate_synth",
}
_LOAD_TIME_PROVENANCE_PATHS: Dict[str, Path] = {
    "executor_sha256": _EXECUTOR_PATH,
    **_HELPER_PROVENANCE_PATHS,
}
_LOAD_TIME_PROVENANCE_SHA256: Dict[str, str] = {
    "executor_sha256": _EXECUTOR_LOAD_SHA256,
    **{
        key: _read_once(path, label=f"{key} module-load provenance")[1]
        for key, path in _HELPER_PROVENANCE_PATHS.items()
    },
}


def _assert_helper_modules_not_preloaded() -> None:
    """Reject stale repo helper modules before provenance-controlled execution."""
    loaded = sorted(
        module_name for module_name in _HELPER_MODULE_NAMES.values() if module_name in sys.modules
    )
    if loaded:
        raise BirthProbeError(
            "repo helper modules were already loaded before Birth Probe main provenance control: "
            + ", ".join(loaded)
        )


def _assert_local_helper_module(module: Any, provenance_key: str) -> None:
    """Bind an imported repo helper module to the load-time path and bytes."""
    expected_path = _LOAD_TIME_PROVENANCE_PATHS[provenance_key].resolve()
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str) or Path(module_file).resolve() != expected_path:
        raise BirthProbeError(
            f"{provenance_key} loaded from an unexpected module path: {module_file!r}"
        )
    _, actual = _read_once(expected_path, label=f"loaded helper {provenance_key}")
    expected = _LOAD_TIME_PROVENANCE_SHA256[provenance_key]
    if actual != expected:
        raise BirthProbeError(f"{provenance_key} changed after executor module load")


def _provenance_input_paths() -> Dict[str, Path]:
    """Return the repo-resident execution/provenance inputs frozen for one run."""
    return {
        "run9_contract_sha256": _THIS_DIR / "RUN9_CONTRACT.yaml",
        "identity_decision_protocol_sha256": (
            _THIS_DIR / "inputs" / "identity_decision_protocol_v0.6.json"
        ),
        "probe_manifest_sha256": _THIS_DIR / "evaluation" / "probe_manifest.json",
        "speaker_map_manifest_sha256": _THIS_DIR / "inputs" / "speaker_map_manifest.json",
        "reexport_manifest_sha256": _THIS_DIR / "inputs" / "reexport_manifest.json",
        "backbone_runtime_bundle_sha256": (
            _THIS_DIR / "inputs" / "backbone_runtime_bundle.json"
        ),
        "dependency_pins_manifest_sha256": _DIRECT_DEPENDENCY_MANIFEST_PATH,
        **_LOAD_TIME_PROVENANCE_PATHS,
    }


def _snapshot_provenance_inputs() -> Dict[str, str]:
    """Hash repo provenance inputs and bind executed code to module-load bytes."""
    snapshot: Dict[str, str] = {}
    for key, path in _provenance_input_paths().items():
        _, actual = _read_once(path, label=f"provenance snapshot {key}")
        expected_path = _LOAD_TIME_PROVENANCE_PATHS.get(key)
        if expected_path is not None and path.resolve() == expected_path.resolve():
            expected = _LOAD_TIME_PROVENANCE_SHA256[key]
            if actual != expected:
                raise BirthProbeError(f"{key} changed after executor module load")
            snapshot[key] = expected
        else:
            snapshot[key] = actual
    return snapshot


def _verify_provenance_inputs_unchanged(snapshot: Mapping[str, str]) -> None:
    """Fail closed if any repo provenance input changed after the snapshot."""
    paths = _provenance_input_paths()
    if set(snapshot) != set(paths):
        raise BirthProbeError("provenance snapshot is incomplete or contains unknown inputs")
    for key, path in paths.items():
        _, actual = _read_once(path, label=f"provenance post-run {key}")
        if actual != snapshot[key]:
            raise BirthProbeError(f"provenance input changed during execution: {path}")


def serialize_feature(vector: np.ndarray) -> bytes:
    """Serialize a feature in one explicit, signed-zero-preserving format.

    The metric fixes the vector computation but not a container format.  The
    executor therefore uses a versioned header, a little-endian uint64 length,
    and contiguous little-endian float64 payload.  This makes dtype, shape,
    endianness, NaN payloads, and signed zero observable to exact replay.
    """
    array = np.asarray(vector)
    if array.ndim != 1:
        raise BirthProbeError(f"identity feature must be one-dimensional, got shape={array.shape!r}")
    payload = np.ascontiguousarray(array, dtype="<f8").tobytes(order="C")
    return _FEATURE_SCHEMA + len(array).to_bytes(8, "little", signed=False) + payload


@dataclass(frozen=True)
class FeatureArtifact:
    vector: np.ndarray
    data: bytes
    sha256: str

    @classmethod
    def from_vector(cls, vector: np.ndarray) -> "FeatureArtifact":
        copied = np.asarray(vector, dtype=np.float64).reshape(-1).copy()
        data = serialize_feature(copied)
        return cls(vector=copied, data=data, sha256=sha256_bytes(data))


@dataclass(frozen=True)
class PJSReference:
    feature: FeatureArtifact
    corpus_sha256: str
    excluded_relative_paths: tuple[str, ...]


@dataclass(frozen=True)
class RenderObservation:
    wav: bytes
    wav_sha256: str
    feature: FeatureArtifact
    runtime_seed: int
    control_profile_id: Optional[str] = None

    @classmethod
    def build(
        cls,
        wav: bytes,
        feature: FeatureArtifact,
        *,
        runtime_seed: int = _RUNTIME_SEED,
        control_profile_id: Optional[str] = None,
    ) -> "RenderObservation":
        return cls(
            wav=bytes(wav),
            wav_sha256=sha256_bytes(wav),
            feature=feature,
            runtime_seed=runtime_seed,
            control_profile_id=control_profile_id,
        )


class Renderer(Protocol):
    def __call__(
        self,
        founder_id: str,
        condition: str,
        take_index: int,
        control_profile: Optional[Mapping[str, Any]],
    ) -> bytes: ...


FeatureExtractor = Callable[[bytes], FeatureArtifact]


def _control_profiles(founder_id: str) -> tuple[Any, Any]:
    import run9_controlprofile as controlprofile
    import run9_schema

    _assert_local_helper_module(run9_schema, "run9_schema_sha256")
    _assert_local_helper_module(controlprofile, "run9_controlprofile_sha256")
    neutral = controlprofile.build_neutral_profile(founder_id)
    replay = controlprofile.derive_profile(
        neutral,
        run9_schema.CONTROL_BRANCH,
        {},
        control_condition="NO_LEARNING_REPLAY",
    )
    sham = controlprofile.derive_profile(
        neutral,
        run9_schema.CONTROL_BRANCH,
        {},
        control_condition="ZERO_CONTROLPROFILE_SHAM",
    )
    for label, profile in (("NO_LEARNING_REPLAY", replay), ("ZERO_CONTROLPROFILE_SHAM", sham)):
        if any(profile.partitions[key] for key in profile.partitions):
            raise BirthProbeError(f"{label} produced non-empty partitions")
    return replay, sham


def extract_identity_feature(wav_bytes: bytes) -> FeatureArtifact:
    """Apply the pinned WORLD identity feature procedure to WAV bytes.

    Imports are intentionally local so unit tests can exercise decision logic
    without the measurement stack.  Production ``main()`` preflights and
    version-binds this exact stack before the first render is admitted.
    """
    try:
        import pyworld  # type: ignore[import-not-found]
        import soundfile as sf
        from scipy.signal import resample_poly
    except ImportError as exc:  # pragma: no cover - measurement environment only
        raise BirthProbeError(
            "WORLD extraction requires pyworld==0.3.5 plus soundfile/scipy in the execution environment"
        ) from exc
    if getattr(pyworld, "__version__", None) != _PYWORLD_PIN_VERSION:
        raise BirthProbeError(
            f"pyworld version must be exactly {_PYWORLD_PIN_VERSION!r}, "
            f"got {getattr(pyworld, '__version__', None)!r}"
        )
    try:
        samples, native_sr = sf.read(io.BytesIO(wav_bytes), dtype="float64", always_2d=False)
    except Exception as exc:
        raise BirthProbeError(f"invalid WAV for identity feature extraction: {exc}") from exc
    if samples.ndim != 1:
        raise BirthProbeError(f"identity WAV must be mono, got shape={samples.shape!r}")
    if not np.isfinite(samples).all() or samples.size == 0:
        raise BirthProbeError("identity WAV samples must be non-empty and finite")
    target_sr = 44_100
    if native_sr != target_sr:
        if isinstance(native_sr, bool) or not isinstance(native_sr, int) or native_sr <= 0:
            raise BirthProbeError(f"invalid native sample rate: {native_sr!r}")
        divisor = math.gcd(target_sr, native_sr)
        samples = resample_poly(samples, up=target_sr // divisor, down=native_sr // divisor)
    f0, temporal_positions = pyworld.harvest(samples, target_sr, frame_period=5.0)
    spectral_envelope = pyworld.cheaptrick(samples, f0, temporal_positions, target_sr)
    voiced = f0 > 0
    if not bool(np.any(voiced)):
        raise BirthProbeError("identity feature is undefined: WAV has zero voiced WORLD frames")
    log_sp = np.log(np.maximum(spectral_envelope[voiced], 1e-12))
    aggregate = np.mean(log_sp, axis=0, dtype=np.float64)
    feature = aggregate - np.mean(aggregate, dtype=np.float64)
    if feature.ndim != 1 or feature.size == 0 or not np.isfinite(feature).all():
        raise BirthProbeError("identity feature must be a non-empty finite vector")
    return FeatureArtifact.from_vector(feature)


def _feature_valid(feature: FeatureArtifact) -> bool:
    return (
        isinstance(feature, FeatureArtifact)
        and feature.vector.ndim == 1
        and feature.vector.size > 0
        and bool(np.isfinite(feature.vector).all())
        and feature.data == serialize_feature(feature.vector)
        and feature.sha256 == sha256_bytes(feature.data)
    )


def _distance(left: FeatureArtifact, right: FeatureArtifact) -> float:
    if left.vector.shape != right.vector.shape:
        return math.nan
    with np.errstate(over="ignore", invalid="ignore"):
        return float(np.linalg.norm(left.vector - right.vector))


def _observation_record(observation: RenderObservation, *, distance: float) -> Dict[str, Any]:
    return {
        "wav_sha256": observation.wav_sha256,
        "feature_sha256": observation.feature.sha256,
        "feature_bytes": len(observation.feature.data),
        "distance_from_reference": distance if math.isfinite(distance) else None,
        "distance_finite": math.isfinite(distance),
        "runtime_seed": observation.runtime_seed,
        "control_profile_id": observation.control_profile_id,
    }


def _seal_result(result: Dict[str, Any]) -> Dict[str, Any]:
    result.pop("evidence_sha256", None)
    result["evidence_sha256"] = sha256_bytes(canonical_json_bytes(result))
    return result


def _closed_founder_mapping(value: Mapping[str, Any], *, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != set(_FOUNDER_IDS):
        got = sorted(value) if isinstance(value, Mapping) else type(value).__name__
        raise BirthProbeError(f"{label} must contain exactly {list(_FOUNDER_IDS)!r}, got {got!r}")


def evaluate_birth_gate(
    *,
    references: Mapping[str, RenderObservation],
    c0_takes: Mapping[str, Sequence[RenderObservation]],
    c1_takes: Mapping[str, Sequence[RenderObservation]],
    positive_references: Mapping[str, RenderObservation],
    pjs_reference: FeatureArtifact,
    expected_takes: int = 20,
) -> Dict[str, Any]:
    """Consume the complete rev 0.6 evidence set without threshold rescue."""
    for label, value in (
        ("references", references),
        ("c0_takes", c0_takes),
        ("c1_takes", c1_takes),
        ("positive_references", positive_references),
    ):
        _closed_founder_mapping(value, label=label)
    if isinstance(expected_takes, bool) or not isinstance(expected_takes, int) or expected_takes <= 0:
        raise BirthProbeError("expected_takes must be a positive integer")

    complete = all(
        len(c0_takes[founder]) == expected_takes and len(c1_takes[founder]) == expected_takes
        for founder in _FOUNDER_IDS
    )
    audit_stops: list[Dict[str, Any]] = []
    founder_records: Dict[str, Any] = {}
    founder_features_valid = True
    pjs_feature_valid = _feature_valid(pjs_reference)

    for founder in _FOUNDER_IDS:
        expected_replay, expected_sham = _control_profiles(founder)
        reference = references[founder]
        feature_ok = _feature_valid(reference.feature)
        founder_features_valid = founder_features_valid and feature_ok
        c0_records: list[Dict[str, Any]] = []
        c1_records: list[Dict[str, Any]] = []
        if reference.runtime_seed != _RUNTIME_SEED or not feature_ok:
            audit_stops.append({
                "founder_id": founder,
                "cell": "reference",
                "take_index": 0,
                "outcome": "IMPLEMENTATION_FAILURE",
                "applicable_keys": ["runtime_or_feature_contract_mismatch"],
            })

        for index, take in enumerate(c0_takes[founder]):
            distance = _distance(reference.feature, take.feature)
            record = _observation_record(take, distance=distance)
            if (
                take.runtime_seed != _RUNTIME_SEED
                or not _feature_valid(take.feature)
                or take.control_profile_id != expected_replay.profile_id
            ):
                audit_stops.append({
                    "founder_id": founder,
                    "cell": "c0",
                    "take_index": index,
                    "outcome": "IMPLEMENTATION_FAILURE",
                    "applicable_keys": ["runtime_feature_or_replay_profile_contract_mismatch"],
                })
            elif take.wav_sha256 != reference.wav_sha256:
                audit_stops.append({
                    "founder_id": founder,
                    "cell": "c0",
                    "take_index": index,
                    "outcome": "DETERMINISM_CONTRACT_BROKEN",
                    "applicable_keys": ["render_byte_mismatch"],
                })
            elif take.feature.data != reference.feature.data or distance != 0.0:
                audit_stops.append({
                    "founder_id": founder,
                    "cell": "c0",
                    "take_index": index,
                    "outcome": "IMPLEMENTATION_FAILURE",
                    "applicable_keys": ["feature_computation_mismatch_with_matching_render"],
                })
            c0_records.append(record)

        for index, take in enumerate(c1_takes[founder]):
            distance = _distance(reference.feature, take.feature)
            record = _observation_record(take, distance=distance)
            applicable: list[str] = []
            if (
                take.runtime_seed != _RUNTIME_SEED
                or not _feature_valid(take.feature)
                or take.control_profile_id != expected_sham.profile_id
            ):
                audit_stops.append({
                    "founder_id": founder,
                    "cell": "c1",
                    "take_index": index,
                    "outcome": "IMPLEMENTATION_FAILURE",
                    "applicable_keys": ["runtime_feature_or_sham_profile_contract_mismatch"],
                })
            if take.wav_sha256 != reference.wav_sha256:
                applicable.append("on_wav_byte_mismatch")
            if (
                take.wav_sha256 == reference.wav_sha256
                and take.feature.data != reference.feature.data
                and distance == 0.0
            ):
                applicable.append("on_feature_mismatch")
            if distance != 0.0:
                applicable.append("on_nonzero")
            priority = (
                ("on_wav_byte_mismatch", "DETERMINISM_CONTRACT_BROKEN"),
                ("on_feature_mismatch", "IMPLEMENTATION_FAILURE"),
                ("on_nonzero", "C1_SHAM_EFFECT_DETECTED"),
            )
            for key, outcome in priority:
                if key in applicable:
                    audit_stops.append({
                        "founder_id": founder,
                        "cell": "c1",
                        "take_index": index,
                        "outcome": outcome,
                        "applicable_keys": applicable,
                    })
                    break
            c1_records.append(record)

        positive = positive_references[founder]
        positive_distance = _distance(reference.feature, positive.feature)
        positive_record = _observation_record(positive, distance=positive_distance)
        if positive.runtime_seed != _RUNTIME_SEED or not _feature_valid(positive.feature):
            audit_stops.append({
                "founder_id": founder,
                "cell": "positive_reference",
                "take_index": 0,
                "outcome": "IMPLEMENTATION_FAILURE",
                "applicable_keys": ["runtime_or_feature_contract_mismatch"],
            })
        elif positive.wav_sha256 != reference.wav_sha256:
            audit_stops.append({
                "founder_id": founder,
                "cell": "positive_reference",
                "take_index": 0,
                "outcome": "DETERMINISM_CONTRACT_BROKEN",
                "applicable_keys": ["wav_byte_mismatch"],
            })
        elif positive.feature.data != reference.feature.data or positive_distance != 0.0:
            audit_stops.append({
                "founder_id": founder,
                "cell": "positive_reference",
                "take_index": 0,
                "outcome": "IMPLEMENTATION_FAILURE",
                "applicable_keys": ["distance_nonzero_or_feature_mismatch_with_matching_wav"],
            })

        pjs_distance = (
            _distance(reference.feature, pjs_reference)
            if feature_ok and pjs_feature_valid
            else math.nan
        )
        founder_records[founder] = {
            "reference": _observation_record(reference, distance=0.0),
            "feature_valid_finite": feature_ok,
            "c0": c0_records,
            "c1": c1_records,
            "positive_reference": positive_record,
            "pjs_confuser_distance": pjs_distance if math.isfinite(pjs_distance) else None,
            "pjs_confuser_distance_finite": math.isfinite(pjs_distance),
        }

    d12 = _distance(references[_FOUNDER_IDS[0]].feature, references[_FOUNDER_IDS[1]].feature)
    pjs_distances = [founder_records[founder]["pjs_confuser_distance"] for founder in _FOUNDER_IDS]
    failures: list[str] = []
    if not founder_features_valid:
        failures.append("invalid_or_nonfinite_feature")
    if not math.isfinite(d12):
        failures.append("invalid_or_nonfinite_d12")
    if any(value is None for value in pjs_distances):
        failures.append("invalid_or_nonfinite_pjs_distance")
    if math.isfinite(d12) and d12 == 0.0:
        failures.append("d12_zero_collapse")
    if any(value == 0.0 for value in pjs_distances if value is not None):
        failures.append("pjs_confuser_zero_distance")
    detail_by_key = {
        "invalid_or_nonfinite_feature": "IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_INVALID_OR_NONFINITE_FEATURE",
        "invalid_or_nonfinite_d12": "IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_INVALID_OR_NONFINITE_D12",
        "invalid_or_nonfinite_pjs_distance": "IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_PJS_CONFUSER_INVALID_OR_NONFINITE_DISTANCE",
        "d12_zero_collapse": "PROJECTED_RUNTIME_IDENTITIES_COLLAPSED_IN_MACHINE_FEATURE_SPACE",
        "pjs_confuser_zero_distance": "IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_PJS_CONFUSER_FEATURE_COLLAPSE",
    }
    identity_established = not failures
    overall_pass = identity_established and complete and not audit_stops
    result: Dict[str, Any] = {
        "schema": _RESULT_SCHEMA,
        "protocol_revision": "0.6",
        "founder_ids": list(_FOUNDER_IDS),
        "expected_takes_per_founder": expected_takes,
        "feature_serialization": _FEATURE_SCHEMA[:-1].decode("ascii"),
        "pjs_reference": {
            "feature_sha256": pjs_reference.sha256,
            "feature_bytes": len(pjs_reference.data),
            "feature_valid_finite": _feature_valid(pjs_reference),
        },
        "founders": founder_records,
        "d12": d12 if math.isfinite(d12) else None,
        "d12_finite": math.isfinite(d12),
        "identity_establishment": {
            "birth_outcome": "ESTABLISHED" if identity_established else "NOT_ESTABLISHED",
            "outcome_detail": (
                "ESTABLISHED_BY_MACHINE_FEATURE" if identity_established else detail_by_key[failures[0]]
            ),
            "outcome_detail_all_applicable_keys": failures,
        },
        "audit": {
            "complete": complete,
            "stops": audit_stops,
            "completion_detail": None if complete else "IDENTITY_PROTOCOL_AUDIT_INCOMPLETE",
            "completion_outcome": None if complete else "IMPLEMENTATION_FAILURE",
        },
        "overall_pass": overall_pass,
        "learning_progression_allowed": overall_pass,
    }
    return _seal_result(result)


def build_pjs_reference(
    corpus_root: Path,
    extractor: FeatureExtractor = extract_identity_feature,
    *,
    expected_corpus_sha256: Optional[str] = None,
) -> PJSReference:
    """Build the frozen dictionary-order PJS ``_song.wav`` aggregate."""
    root = corpus_root.resolve(strict=True)
    labs = sorted(root.glob("pjs*/pjs*.lab"), key=lambda path: path.relative_to(root).as_posix().encode())
    if not labs:
        raise BirthProbeError(f"no pjs*/pjs*.lab found under {root}")
    vectors: list[np.ndarray] = []
    corpus_pairs: list[tuple[str, str]] = []
    pinned_wavs: list[tuple[Path, str]] = []
    for lab in labs:
        wav = lab.parent / f"{lab.stem}_song.wav"
        for candidate in (lab, wav):
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise BirthProbeError(f"PJS corpus path escapes root: {candidate}")
        lab_bytes, _ = _read_once(lab, label="PJS lab pin input")
        wav_bytes, _ = _read_once(wav, label="PJS song WAV")
        corpus_pairs.extend(
            (
                (lab.relative_to(root).as_posix(), sha256_bytes(lab_bytes)),
                (wav.relative_to(root).as_posix(), sha256_bytes(wav_bytes)),
            )
        )
        pinned_wavs.append((wav, sha256_bytes(wav_bytes)))
    material = "|".join(f"{path}:{digest}" for path, digest in sorted(corpus_pairs))
    corpus_sha = sha256_bytes(material.encode("utf-8"))
    if expected_corpus_sha256 is not None and corpus_sha != expected_corpus_sha256:
        raise BirthProbeError(
            f"PJS expanded corpus identity mismatch: expected {expected_corpus_sha256}, got {corpus_sha}"
        )

    expected_shape: Optional[tuple[int, ...]] = None
    excluded: list[str] = []
    for wav, pinned_wav_sha in pinned_wavs:
        wav_bytes, current_wav_sha = _read_once(wav, label="PJS song WAV feature input")
        if current_wav_sha != pinned_wav_sha:
            raise BirthProbeError(f"PJS song WAV changed after corpus pin verification: {wav}")
        try:
            feature = extractor(wav_bytes)
        except BirthProbeError as exc:
            if "zero voiced" in str(exc):
                excluded.append(wav.relative_to(root).as_posix())
                continue
            raise
        if not _feature_valid(feature):
            raise BirthProbeError(f"PJS feature is invalid for {wav.relative_to(root)}")
        if expected_shape is None:
            expected_shape = feature.vector.shape
        if feature.vector.shape != expected_shape:
            raise BirthProbeError("PJS features have inconsistent vector shapes")
        vectors.append(feature.vector)
    if not vectors:
        raise BirthProbeError("all pin-covered PJS song WAVs had zero voiced frames")
    aggregate = np.mean(np.stack(vectors), axis=0, dtype=np.float64)
    return PJSReference(
        feature=FeatureArtifact.from_vector(aggregate),
        corpus_sha256=corpus_sha,
        excluded_relative_paths=tuple(excluded),
    )


def verify_exact_acoustic(acoustic_path: Path, expected_sha256: str) -> bytes:
    """Read and verify the acoustic ONNX before a renderer can be constructed."""
    value, actual = _read_once(acoustic_path, label="acoustic ONNX")
    if actual != expected_sha256:
        raise BirthProbeError(
            f"acoustic ONNX sha256 mismatch: expected {expected_sha256}, got {actual}; zero renders admitted"
        )
    return value


def execute_birth_gate(
    *,
    renderer: Renderer,
    pjs_reference: FeatureArtifact,
    extractor: FeatureExtractor = extract_identity_feature,
    expected_takes: int = 20,
) -> tuple[Dict[str, Any], Dict[str, Dict[str, list[RenderObservation]]]]:
    """Execute the fixed 84-render order and return result plus observations."""
    observations: Dict[str, Dict[str, list[RenderObservation]]] = {
        founder: {condition: [] for condition in _CONDITIONS} for founder in _FOUNDER_IDS
    }
    for founder in _FOUNDER_IDS:
        replay, sham = _control_profiles(founder)
        render_plan = (
            ("reference", 1, None),
            ("c0", expected_takes, replay.to_dict()),
            ("c1", expected_takes, sham.to_dict()),
            ("positive_reference", 1, None),
        )
        for condition, count, profile in render_plan:
            for take_index in range(count):
                wav = renderer(founder, condition, take_index, profile)
                feature = extractor(wav)
                observation = RenderObservation.build(
                    wav,
                    feature,
                    control_profile_id=(
                        replay.profile_id if condition == "c0" else sham.profile_id if condition == "c1" else None
                    ),
                )
                observations[founder][condition].append(observation)
    result = evaluate_birth_gate(
        references={founder: observations[founder]["reference"][0] for founder in _FOUNDER_IDS},
        c0_takes={founder: observations[founder]["c0"] for founder in _FOUNDER_IDS},
        c1_takes={founder: observations[founder]["c1"] for founder in _FOUNDER_IDS},
        positive_references={
            founder: observations[founder]["positive_reference"][0] for founder in _FOUNDER_IDS
        },
        pjs_reference=pjs_reference,
        expected_takes=expected_takes,
    )
    return result, observations


def _staged_result_record(
    parsed_result: Mapping[str, Any], founder: str, condition: str, index: int
) -> Mapping[str, Any]:
    founders = parsed_result.get("founders")
    if not isinstance(founders, Mapping) or set(founders) != set(_FOUNDER_IDS):
        raise BirthProbeError("staged evidence result founders are not closed-world")
    founder_result = founders.get(founder)
    if not isinstance(founder_result, Mapping):
        raise BirthProbeError(f"staged evidence result is missing founder {founder}")
    if condition in {"reference", "positive_reference"}:
        record = founder_result.get(condition)
    else:
        records = founder_result.get(condition)
        if not isinstance(records, list) or index >= len(records):
            raise BirthProbeError(f"staged evidence result is missing {founder}/{condition}/{index}")
        record = records[index]
    if not isinstance(record, Mapping):
        raise BirthProbeError(f"staged evidence result record is invalid for {founder}/{condition}/{index}")
    return record


def _validate_staged_evidence_bundle(
    staging: Path,
    result: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Sequence[RenderObservation]]],
    pjs_reference: FeatureArtifact,
    expected_inventory: Mapping[str, str],
) -> None:
    """Parse and read back a complete staged bundle before the atomic rename."""
    evidence_path = staging / "birth_gate_evidence.json"
    evidence_bytes, _ = _read_once(evidence_path, label="staged birth gate evidence")
    expected_evidence_bytes = canonical_json_bytes(dict(result))
    if evidence_bytes != expected_evidence_bytes:
        raise BirthProbeError("staged birth gate evidence bytes diverge from the in-memory result")
    try:
        parsed_result = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BirthProbeError(f"staged birth gate evidence is not valid UTF-8 JSON: {exc}") from exc
    if parsed_result != dict(result):
        raise BirthProbeError("staged birth gate evidence parse differs from the in-memory result")

    for founder in _FOUNDER_IDS:
        for condition in _CONDITIONS:
            for index, observation in enumerate(observations[founder][condition]):
                stem = f"{founder}_{condition}_{index:02d}"
                wav_bytes, wav_sha = _read_once(
                    staging / "wav" / f"{stem}.wav", label=f"staged WAV {stem}"
                )
                feature_bytes, feature_sha = _read_once(
                    staging / "features" / f"{stem}.bin", label=f"staged feature {stem}"
                )
                if wav_bytes != observation.wav or wav_sha != observation.wav_sha256:
                    raise BirthProbeError(f"staged WAV readback mismatch for {stem}")
                if feature_bytes != observation.feature.data or feature_sha != observation.feature.sha256:
                    raise BirthProbeError(f"staged feature readback mismatch for {stem}")
                record = _staged_result_record(parsed_result, founder, condition, index)
                if (
                    record.get("wav_sha256") != wav_sha
                    or record.get("feature_sha256") != feature_sha
                    or record.get("feature_bytes") != len(feature_bytes)
                ):
                    raise BirthProbeError(f"staged evidence record disagrees with artifact bytes for {stem}")

    pjs_bytes, pjs_sha = _read_once(
        staging / "features" / "pjs_reference.bin", label="staged PJS reference"
    )
    if pjs_bytes != pjs_reference.data or pjs_sha != pjs_reference.sha256:
        raise BirthProbeError("staged PJS reference readback mismatch")
    pjs_record = parsed_result.get("pjs_reference")
    if not isinstance(pjs_record, Mapping) or (
        pjs_record.get("feature_sha256") != pjs_sha
        or pjs_record.get("feature_bytes") != len(pjs_bytes)
    ):
        raise BirthProbeError("staged PJS evidence record disagrees with artifact bytes")

    manifest_path = staging / "artifact_manifest.json"
    manifest_bytes, _ = _read_once(manifest_path, label="staged artifact manifest")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BirthProbeError(f"staged artifact manifest is not valid UTF-8 JSON: {exc}") from exc
    actual_inventory: Dict[str, str] = {}
    actual_files: set[str] = set()
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            relative = path.relative_to(staging).as_posix()
            actual_files.add(relative)
            if relative != "artifact_manifest.json":
                actual_inventory[relative] = sha256_bytes(path.read_bytes())
    if actual_inventory != dict(expected_inventory):
        raise BirthProbeError("staged artifact inventory changed after manifest construction")
    if manifest != {
        "schema": "run9-birth-gate-artifact-manifest/1.0",
        "files": actual_inventory,
    }:
        raise BirthProbeError("staged artifact manifest disagrees with readback inventory")
    if actual_files != set(actual_inventory) | {"artifact_manifest.json"}:
        raise BirthProbeError("staged evidence bundle contains missing or unexpected files")


def publish_evidence_bundle(
    output_dir: Path,
    result: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Sequence[RenderObservation]]],
    pjs_reference: FeatureArtifact,
) -> None:
    """Publish all evidence only after a complete staging build succeeds."""
    if _LEGACY_REV06_PUBLICATION_DISABLED:
        raise BirthProbeError(
            "legacy RUN9 rev 0.6 publication is disabled; use the success-only admission harness"
        )
    if result.get("schema") != _RESULT_SCHEMA:
        raise BirthProbeError("evidence result has an unknown or missing schema")
    expected_takes = result.get("expected_takes_per_founder")
    if isinstance(expected_takes, bool) or not isinstance(expected_takes, int) or expected_takes <= 0:
        raise BirthProbeError("evidence result has an invalid expected take count")
    _closed_founder_mapping(observations, label="publication observations")
    expected_counts = {
        "reference": 1,
        "c0": expected_takes,
        "c1": expected_takes,
        "positive_reference": 1,
    }
    for founder in _FOUNDER_IDS:
        founder_observations = observations[founder]
        if not isinstance(founder_observations, Mapping) or set(founder_observations) != set(_CONDITIONS):
            raise BirthProbeError(f"publication observations for {founder} are not closed-world")
        for condition, count in expected_counts.items():
            if len(founder_observations[condition]) != count:
                raise BirthProbeError(
                    f"publication observations for {founder}/{condition} must contain {count} items"
                )
    if not _feature_valid(pjs_reference):
        raise BirthProbeError("refusing to publish an invalid PJS reference feature")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise BirthProbeError(f"refusing to overwrite existing evidence directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.build-", dir=output_dir.parent))
    try:
        (staging / "wav").mkdir()
        (staging / "features").mkdir()
        for founder in _FOUNDER_IDS:
            for condition in _CONDITIONS:
                for index, observation in enumerate(observations[founder][condition]):
                    stem = f"{founder}_{condition}_{index:02d}"
                    (staging / "wav" / f"{stem}.wav").write_bytes(observation.wav)
                    (staging / "features" / f"{stem}.bin").write_bytes(observation.feature.data)
        (staging / "features" / "pjs_reference.bin").write_bytes(pjs_reference.data)
        result_bytes = canonical_json_bytes(dict(result))
        (staging / "birth_gate_evidence.json").write_bytes(result_bytes)
        inventory: Dict[str, str] = {}
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                inventory[path.relative_to(staging).as_posix()] = sha256_bytes(path.read_bytes())
        (staging / "artifact_manifest.json").write_bytes(
            canonical_json_bytes({"schema": "run9-birth-gate-artifact-manifest/1.0", "files": inventory})
        )
        _validate_staged_evidence_bundle(staging, result, observations, pjs_reference, inventory)
        os.replace(staging, output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@dataclass(frozen=True)
class _ProbeNote:
    mora: str
    midi: int
    duration_beats: float


class GateSynthRenderer:
    """Production adapter for the frozen ``gate_synth.run_pipeline`` path.

    C1 attaches the exact neutral ``r_sham`` ControlProfile to the duration
    override callback that the frozen synthesis path actually invokes.  The
    callback is output-inert but single-use and attested, so merely carrying a
    profile beside a render cannot satisfy the sham mechanism.
    """

    def __init__(
        self,
        *,
        acoustic_dir: Path,
        canon_model_dir: Path,
        vocoder_dir: Path,
        reexport_manifest: Mapping[str, Any],
        backbone_bundle: Mapping[str, Any],
        probe_manifest: Mapping[str, Any],
        speaker_map_manifest: Mapping[str, Any],
    ) -> None:
        acoustic_dir = acoustic_dir.resolve(strict=True)
        expected_files = {
            artifact["file"]: artifact for artifact in reexport_manifest["artifacts"].values()
        }
        actual_files = {path.name for path in acoustic_dir.iterdir() if path.is_file()}
        if actual_files != set(expected_files):
            raise BirthProbeError(
                "acoustic export directory must contain exactly the nine reexport artifacts; "
                f"missing={sorted(set(expected_files) - actual_files)!r}, "
                f"unexpected={sorted(actual_files - set(expected_files))!r}"
            )
        artifact_bytes: Dict[str, bytes] = {}
        for filename, artifact in expected_files.items():
            artifact_path = acoustic_dir / filename
            resolved_artifact = artifact_path.resolve(strict=True)
            if not resolved_artifact.is_relative_to(acoustic_dir):
                raise BirthProbeError(f"reexport artifact escapes acoustic directory: {artifact_path}")
            value, actual = _read_once(artifact_path, label=f"reexport artifact {filename}")
            if len(value) != artifact["bytes"] or actual != artifact["sha256_run1"]:
                raise BirthProbeError(
                    f"reexport artifact mismatch for {filename}: expected bytes={artifact['bytes']}, "
                    f"sha256={artifact['sha256_run1']}; got bytes={len(value)}, sha256={actual}"
                )
            artifact_bytes[filename] = value
        acoustic_path = acoustic_dir / reexport_manifest["artifacts"]["acoustic_onnx"]["file"]
        verify_exact_acoustic(
            acoustic_path, reexport_manifest["artifacts"]["acoustic_onnx"]["sha256_run1"]
        )
        canon_model_dir = canon_model_dir.resolve(strict=True)
        vocoder_dir = vocoder_dir.resolve(strict=True)
        canon_assets = backbone_bundle["run9_runtime_inputs"]["canon_model_assets"]["assets"]
        pinned_external = {
            canon_model_dir / "linguistic.onnx": canon_assets["linguistic_onnx"]["value"],
            canon_model_dir / "dsdur" / "dur.onnx": canon_assets["variance_duration_onnx"]["value"],
            canon_model_dir / "dspitch" / "pitch.onnx": canon_assets["variance_pitch_onnx"]["value"],
            canon_model_dir / "phonemes.txt": canon_assets["phonemes_txt"]["value"],
            vocoder_dir / "nsf_hifigan.onnx": backbone_bundle["run9_runtime_inputs"]["vocoder"][
                "runtime_onnx_sha256"
            ]["value"],
        }
        try:
            import soundfile as sf

            foundry_dir = _REPO_ROOT / "voice_genesis" / "foundry" / "s1_gate"
            if str(foundry_dir) not in sys.path:
                sys.path.insert(0, str(foundry_dir))
            import gate_synth
        except ImportError as exc:  # pragma: no cover - measurement environment only
            raise BirthProbeError(f"gate_synth runtime dependency unavailable: {exc}") from exc
        _assert_local_helper_module(gate_synth, "gate_synth_sha256")
        if getattr(gate_synth, "_GATE_SYNTH_PY_LOAD_TIME_SHA256", None) != (
            _LOAD_TIME_PROVENANCE_SHA256["gate_synth_sha256"]
        ):
            raise BirthProbeError("gate_synth in-memory load-time bytes diverge from Birth Probe provenance")
        self._sf = sf
        self._gate = gate_synth
        self._input_paths = {
            "acoustic": acoustic_path,
            "dsconfig": acoustic_dir / "dsconfig.yaml",
            "phonemes": acoustic_dir / "s5_run6_acoustic_v1.phonemes.json",
            "ritsu": acoustic_dir / "s5_run6_acoustic_v1.ritsu.emb",
            "user": acoustic_dir / "s5_run6_acoustic_v1.user.emb",
        }
        self._post_paths = {
            f"artifact_{filename}": acoustic_dir / filename for filename in expected_files
        }
        self._input_hashes = {
            f"artifact_{filename}": expected_files[filename]["sha256_run1"]
            for filename in expected_files
        }
        for index, path in enumerate(pinned_external):
            key = f"external_{index}"
            self._post_paths[key] = path
            self._input_hashes[key] = pinned_external[path]
        self._model_bytes, model_shas = gate_synth.load_model_bundle_bytes(
            canon_model_dir,
            vocoder_dir,
            acoustic_path,
            self._input_paths["dsconfig"],
        )
        expected_model_shas = {
            "canon_linguistic_onnx": pinned_external[canon_model_dir / "linguistic.onnx"],
            "canon_variance_dur_onnx": pinned_external[canon_model_dir / "dsdur" / "dur.onnx"],
            "canon_variance_pitch_onnx": pinned_external[canon_model_dir / "dspitch" / "pitch.onnx"],
            "acoustic_onnx": reexport_manifest["artifacts"]["acoustic_onnx"]["sha256_run1"],
            "vocoder_onnx": pinned_external[vocoder_dir / "nsf_hifigan.onnx"],
            "acoustic_dsconfig_yaml": reexport_manifest["artifacts"]["dsconfig_yaml"]["sha256_run1"],
        }
        if model_shas != expected_model_shas:
            raise BirthProbeError(
                f"model bytes consumed by gate_synth diverge from pins: expected "
                f"{expected_model_shas!r}, got {model_shas!r}"
            )
        self._variance_phonemes, canon_phonemes_sha = gate_synth.load_canon_phonemes_with_sha(
            canon_model_dir / "phonemes.txt"
        )
        if canon_phonemes_sha != pinned_external[canon_model_dir / "phonemes.txt"]:
            raise BirthProbeError("canon phoneme bytes consumed by gate_synth diverge from the pin")
        self._acoustic_phonemes, acoustic_phonemes_sha = gate_synth.load_own_phonemes_json_with_sha(
            self._input_paths["phonemes"]
        )
        if acoustic_phonemes_sha != reexport_manifest["artifacts"]["phonemes_json"]["sha256_run1"]:
            raise BirthProbeError("acoustic phoneme bytes consumed by gate_synth diverge from the pin")
        self._embeddings = self._build_embeddings(speaker_map_manifest, artifact_bytes)
        p0 = [probe for probe in probe_manifest["probes"] if probe["probe_id"] == "P0"]
        if len(p0) != 1 or len(p0[0]["cells"]) != 1:
            raise BirthProbeError("pinned probe manifest must contain exactly one P0 cell")
        cell = p0[0]["cells"][0]
        self._tempo = float(cell["tempo_bpm"])
        self._notes = [
            _ProbeNote(
                mora=str(note["kana"]),
                midi=int(note["pitch_midi"]),
                duration_beats=float(note["duration_beats"]),
            )
            for note in cell["notes"]
        ]

    @property
    def verified_acoustic_sha256(self) -> str:
        """Digest the exact acoustic buffer loaded for synthesis."""
        acoustic = self._model_bytes.get("acoustic_onnx")
        if not isinstance(acoustic, bytes) or not acoustic:
            raise BirthProbeError("renderer acoustic buffer is unavailable")
        return sha256_bytes(acoustic)

    def _build_embeddings(
        self, speaker_map: Mapping[str, Any], artifact_bytes: Mapping[str, bytes]
    ) -> Dict[str, np.ndarray]:
        ritsu = np.frombuffer(artifact_bytes[self._input_paths["ritsu"].name], dtype=np.float32).copy()
        user = np.frombuffer(artifact_bytes[self._input_paths["user"].name], dtype=np.float32).copy()
        if ritsu.shape != (384,) or user.shape != (384,) or not np.isfinite(ritsu).all() or not np.isfinite(user).all():
            raise BirthProbeError("source speaker embeddings must be finite float32 vectors of length 384")
        result: Dict[str, np.ndarray] = {}
        for founder in _FOUNDER_IDS:
            weights = speaker_map["founders"][founder]["renormalized_runtime_weights"]
            w_ritsu = np.float32(float(weights["w_ritsu_float32_repr"]))
            w_user = np.float32(float(weights["w_user_float32_repr"]))
            vector = w_ritsu * ritsu + w_user * user
            expected = speaker_map["founders"][founder]["synthesized_embedding"]["sha256"]
            actual = sha256_bytes(vector.astype(np.float32).tobytes())
            if actual != expected:
                raise BirthProbeError(f"{founder} synthesized embedding mismatch: expected {expected}, got {actual}")
            result[founder] = vector
        return result

    def __call__(
        self,
        founder_id: str,
        condition: str,
        take_index: int,
        control_profile: Optional[Mapping[str, Any]],
    ) -> bytes:
        if founder_id not in _FOUNDER_IDS or condition not in _CONDITIONS or take_index < 0:
            raise BirthProbeError("renderer received an out-of-contract render coordinate")
        if (condition in {"c0", "c1"}) != (control_profile is not None):
            raise BirthProbeError("C0/C1 must carry their exact empty CONTROL profiles")
        if control_profile is not None and any(control_profile["partitions"].values()):
            raise BirthProbeError("C0/C1 CONTROL profile partitions must remain empty")
        if condition in {"c0", "c1"}:
            replay, sham = _control_profiles(founder_id)
            expected_profile = replay.to_dict() if condition == "c0" else sham.to_dict()
            if dict(control_profile) != expected_profile:
                raise BirthProbeError(
                    f"{condition.upper()} must carry the exact derived CONTROL profile"
                )
        record: Dict[str, Any] = {}
        run_kwargs: Dict[str, Any] = {
            "speaker_name": "ritsu",
            "speaker_embed_vector": self._embeddings[founder_id],
        }
        sham_hook: Optional[Callable[[list[int], dict], list[int]]] = None
        if condition == "c1":
            sham_hook = _run9_zero_controlprofile_sham_duration_hook(
                control_profile,
                record,
            )
            run_kwargs["final_phone_dur_override"] = sham_hook
        waveform = self._gate.run_pipeline(
            self._notes,
            lambda beats, tempo_bpm: beats * 60.0 / tempo_bpm,
            self._tempo,
            self._model_bytes,
            self._variance_phonemes,
            self._acoustic_phonemes,
            record,
            **run_kwargs,
        )
        if condition == "c1":
            if sham_hook is None or getattr(sham_hook, "run9_consumed", False) is not True:
                raise BirthProbeError(
                    "C1 synthesis call did not invoke the ControlProfile duration hook"
                )
            expected_attachment = {
                "status": "CONSUMED_INERT_ZERO_PROFILE",
                "voice_id": founder_id,
                "revision": "r_sham",
                "profile_id": control_profile["profile_id"],
            }
            if record.get("run9_control_profile_attachment") != expected_attachment:
                raise BirthProbeError(
                    "C1 synthesis call did not preserve exact ControlProfile consumption"
                )
        elif "run9_control_profile_attachment" in record:
            raise BirthProbeError("gate_synth emitted a ControlProfile attachment outside C1")
        if record.get("seed") != _RUNTIME_SEED:
            raise BirthProbeError(f"gate_synth recorded unexpected runtime seed: {record.get('seed')!r}")
        waveform = np.asarray(waveform)
        if waveform.ndim != 1 or waveform.size == 0 or not bool(np.isfinite(waveform).all()):
            raise BirthProbeError("gate_synth waveform must be a non-empty finite one-dimensional array")
        peak = float(np.max(np.abs(waveform)))
        if peak > 0.0:
            waveform = waveform / peak * 0.6
        with tempfile.TemporaryDirectory(prefix="run9-birth-render-") as directory:
            published = Path(directory) / "render.wav"
            self._sf.write(published, waveform.astype(np.float32), 44_100, subtype="PCM_16", format="WAV")
            wav_bytes, _ = _read_once(published, label="published render WAV")
        readback, sample_rate = self._sf.read(io.BytesIO(wav_bytes), dtype="float64", always_2d=False)
        if sample_rate != 44_100 or readback.ndim != 1 or not np.isfinite(readback).all():
            raise BirthProbeError("published render WAV failed PCM readback/meter validation")
        # Compute the frozen publication meters from the exact readback bytes.
        _ = float(np.max(np.abs(readback))) if readback.size else 0.0
        _ = float(np.sqrt(np.mean(readback**2))) if readback.size else 0.0
        return wav_bytes

    def verify_inputs_unchanged(self) -> None:
        for key, path in self._post_paths.items():
            _, actual = _read_once(path, label=f"renderer post-run {key} input")
            if actual != self._input_hashes[key]:
                raise BirthProbeError(f"renderer input changed during execution: {path}")


def _load_pinned_json(contract: Any, pin_name: str, path: Path) -> Dict[str, Any]:
    value, actual = _read_once(path, label=pin_name)
    pin = contract.pin_field(pin_name)
    if pin.get("status") != "PINNED" or actual != pin.get("value"):
        raise BirthProbeError(f"{pin_name} does not match its RUN9 contract pin")
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BirthProbeError(f"{pin_name} source is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BirthProbeError(f"{pin_name} source must be a JSON object")
    return parsed


def _load_direct_dependency_pin_versions(contract: Any) -> Dict[str, str]:
    """Load direct runtime versions only from the preregistered contract pin."""
    parsed = _load_pinned_json(
        contract,
        "dependency_pins_sha",
        _DIRECT_DEPENDENCY_MANIFEST_PATH,
    )
    rows = parsed.get("python_dependency_pins")
    if not isinstance(rows, list):
        raise BirthProbeError("dependency pins manifest must contain python_dependency_pins list")
    pins: Dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise BirthProbeError("dependency pin rows must be objects")
        package = row.get("package")
        pin_version = row.get("pin_version")
        observed_version = row.get("observed_version")
        status = row.get("status")
        if not isinstance(package, str) or not isinstance(pin_version, str):
            raise BirthProbeError("dependency pin rows require string package and pin_version")
        if package in pins:
            raise BirthProbeError(f"duplicate dependency pin row for {package}")
        if status != "MATCH" or observed_version != pin_version:
            raise BirthProbeError(f"dependency pin record is not a verified MATCH for {package}")
        pins[package] = pin_version
    missing = sorted(set(_DIRECT_DEPENDENCY_PACKAGES) - set(pins))
    if missing:
        raise BirthProbeError(f"dependency pins manifest is missing direct runtime packages: {missing!r}")
    selected = {package: pins[package] for package in _DIRECT_DEPENDENCY_PACKAGES}
    selected["pyworld"] = _PYWORLD_PIN_VERSION
    return selected


def _validate_execution_environment(
    profile: Mapping[str, Any], expected_dependencies: Mapping[str, str]
) -> Dict[str, str]:
    """Fail closed on runtime identity or directly executed dependency drift."""
    runtime = profile["identity_semantics"]["runtime"]
    required_dependencies = set(_DIRECT_DEPENDENCY_PACKAGES) | {"pyworld"}
    if set(expected_dependencies) != required_dependencies:
        raise BirthProbeError(
            f"direct dependency expectation set is not closed: expected {sorted(required_dependencies)!r}, "
            f"got {sorted(expected_dependencies)!r}"
        )
    try:
        scipy = importlib.import_module("scipy")
        soundfile = importlib.import_module("soundfile")
        yaml_module = importlib.import_module("yaml")
        ort = importlib.import_module("onnxruntime")
        pyworld = importlib.import_module("pyworld")
    except ImportError as exc:  # pragma: no cover - measurement environment only
        raise BirthProbeError(f"direct runtime dependency is unavailable: {exc}") from exc
    module_versions = {
        "numpy": getattr(np, "__version__", None),
        "scipy": getattr(scipy, "__version__", None),
        "soundfile": getattr(soundfile, "__version__", None),
        "PyYAML": getattr(yaml_module, "__version__", None),
        "onnxruntime": getattr(ort, "__version__", None),
        "pyworld": getattr(pyworld, "__version__", None),
    }
    actual_dependencies: Dict[str, str] = {}
    for package in sorted(required_dependencies):
        expected = expected_dependencies[package]
        module_version = module_versions[package]
        try:
            distribution_version = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError as exc:
            raise BirthProbeError(f"runtime dependency distribution is unavailable: {package}") from exc
        if module_version != expected or distribution_version != expected:
            raise BirthProbeError(
                f"runtime dependency version mismatch for {package}: expected {expected!r}, "
                f"module={module_version!r}, distribution={distribution_version!r}"
            )
        actual_dependencies[package] = expected
    os_release = platform.freedesktop_os_release()
    pretty_os = os_release.get("PRETTY_NAME", "")
    os_match = re.match(r"^(Ubuntu\s+\d+\.\d+(?:\.\d+)?)", pretty_os)
    normalized_os = os_match.group(1) if os_match is not None else pretty_os
    actual = {
        "architecture": platform.machine(),
        "onnxruntime": actual_dependencies["onnxruntime"],
        "os": normalized_os,
        "python": platform.python_version(),
        "selected_execution_provider": "CPUExecutionProvider",
    }
    if actual != runtime:
        raise BirthProbeError(f"execution profile mismatch: expected {runtime!r}, got {actual!r}")
    if "CPUExecutionProvider" not in ort.get_available_providers():
        raise BirthProbeError("CPUExecutionProvider is not available")
    return {"python": platform.python_version(), **actual_dependencies}


def _load_verified_inputs() -> tuple[
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    int,
    Dict[str, str],
]:
    import run9_schema

    _assert_local_helper_module(run9_schema, "run9_schema_sha256")
    contract_path = _THIS_DIR / "RUN9_CONTRACT.yaml"
    contract = run9_schema.load_run9_contract_from_yaml_path(contract_path)
    domain = run9_schema.load_run9_identity_domain(_THIS_DIR / "domains" / "identity_domain_run9_v1.json")
    protocol = run9_schema.load_pinned_identity_decision_protocol(contract, domain=domain)
    probe = run9_schema.load_pinned_probe_manifest(contract)
    reexport = run9_schema.load_pinned_reexport_manifest(contract)
    run9_schema.verify_user_donor_manifest_complete()
    rights_path = _THIS_DIR / "inputs" / "rights_manifest.json"
    rights = run9_schema.load_rights_manifest_json(rights_path.read_text(encoding="utf-8"))
    speaker_map = run9_schema.load_pinned_speaker_map_manifest(
        contract, domain=domain, rights_manifest=rights
    )
    execution_profile = run9_schema.load_pinned_execution_profile_manifest(contract)
    expected_dependencies = _load_direct_dependency_pin_versions(contract)
    runtime_dependency_versions = _validate_execution_environment(
        execution_profile, expected_dependencies
    )
    backbone_bundle = _load_pinned_json(
        contract,
        "backbone_runtime_bundle_sha",
        _THIS_DIR / "inputs" / "backbone_runtime_bundle.json",
    )
    practice_split = run9_schema.load_pinned_practice_split_manifest(contract)
    expected_takes = protocol["c0_determinism_attestation"]["takes_per_founder"]
    if expected_takes != protocol["c1_sham_attestation"]["takes_per_founder"]:
        raise BirthProbeError("C0/C1 take counts diverge in the pinned protocol")
    return (
        probe,
        speaker_map,
        reexport,
        backbone_bundle,
        practice_split,
        expected_takes,
        runtime_dependency_versions,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acoustic-dir", required=True, type=Path)
    parser.add_argument("--canon-model-dir", required=True, type=Path)
    parser.add_argument("--vocoder-dir", required=True, type=Path)
    parser.add_argument("--pjs-corpus-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if _LEGACY_REV06_PUBLICATION_DISABLED:
            raise BirthProbeError(
                "legacy RUN9 rev 0.6 CLI is disabled; use the success-only admission harness "
                "run9_success_admission.py"
            )
        _assert_helper_modules_not_preloaded()
        provenance_snapshot = _snapshot_provenance_inputs()
        (
            probe,
            speaker_map,
            reexport,
            backbone_bundle,
            practice_split,
            expected_takes,
            runtime_dependency_versions,
        ) = _load_verified_inputs()
        # Catch a repo mutation that happened between the pre-render snapshot and
        # the verified loader consumption before admitting any expensive render.
        _verify_provenance_inputs_unchanged(provenance_snapshot)
        expected_acoustic = reexport["artifacts"]["acoustic_onnx"]["sha256_run1"]
        # The exact acoustic byte gate is deliberately the first external asset
        # check and precedes renderer construction or PJS feature work.
        verify_exact_acoustic(
            args.acoustic_dir / reexport["artifacts"]["acoustic_onnx"]["file"],
            expected_acoustic,
        )
        renderer = GateSynthRenderer(
            acoustic_dir=args.acoustic_dir,
            canon_model_dir=args.canon_model_dir,
            vocoder_dir=args.vocoder_dir,
            reexport_manifest=reexport,
            backbone_bundle=backbone_bundle,
            probe_manifest=probe,
            speaker_map_manifest=speaker_map,
        )
        pjs_reference_record = build_pjs_reference(
            args.pjs_corpus_root,
            expected_corpus_sha256=practice_split["expanded_corpus_identity_sha256"],
        )
        pjs_reference = pjs_reference_record.feature
        result, observations = execute_birth_gate(
            renderer=renderer,
            pjs_reference=pjs_reference,
            expected_takes=expected_takes,
        )
        renderer.verify_inputs_unchanged()
        # Verify the exact repo-resident code/protocol/manifest snapshot again
        # immediately before evidence provenance is sealed and published.
        _verify_provenance_inputs_unchanged(provenance_snapshot)
        result["input_provenance"] = {
            **provenance_snapshot,
            "runtime_dependency_versions": runtime_dependency_versions,
            "practice_expanded_corpus_identity_sha256": practice_split[
                "expanded_corpus_identity_sha256"
            ],
            "pjs_reference_excluded_relative_paths": list(
                pjs_reference_record.excluded_relative_paths
            ),
            "acoustic_onnx_sha256": expected_acoustic,
        }
        _seal_result(result)
        publish_evidence_bundle(args.out, result, observations, pjs_reference)
    except (BirthProbeError, OSError, ValueError) as exc:
        print(f"BIRTH_PROBE_ABORTED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"overall_pass": result["overall_pass"], "out": str(args.out)}, sort_keys=True))
    return 0 if result["overall_pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
