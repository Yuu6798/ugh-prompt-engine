#!/usr/bin/env python3
"""RUN9 success-only generated-artifact admission harness.

The generated export is never preregistered.  Its nine files are snapshotted
inside one execution, rendered through the fixed 84-render Birth measurement,
and attached to a registration record only after the output-only evaluator
returns PASS.  Rejection leaves the registration directory absent.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

import birth_probe_executor as bp


_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[2]
_POLICY_PATH = _THIS_DIR / "inputs" / "success_only_admission_policy.json"
_POLICY_SCHEMA = "run9-success-only-admission-policy/1.0"
_ADMISSION_SCHEMA = "run9-successful-artifact-admission/1.0"
_MANIFEST_SCHEMA = "run9-successful-artifact-manifest/1.0"
_SUCCESS_MARKER_SCHEMA = "run9-successful-artifact-marker/1.0"
_FOUNDER_IDS = ("R9F-01", "R9F-02")
_CONDITIONS = ("reference", "c0", "c1", "positive_reference")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ArtifactRejected(bp.BirthProbeError):
    """The generated artifact did not pass; no registration may be created."""


class _IssuedSuccessAdmission(dict):
    """Unforgeable-in-process capability minted only after output-only PASS."""


_ISSUED_ADMISSIONS: Dict[int, str] = {}


@dataclass(frozen=True)
class GeneratedExportSnapshot:
    """One-run byte snapshot of the complete generated export."""

    root: Path
    artifacts: Mapping[str, Mapping[str, Any]]
    artifact_bytes: Mapping[str, bytes]

    @property
    def acoustic_sha256(self) -> str:
        return str(self.artifacts["acoustic_onnx"]["sha256_run1"])

    def renderer_manifest(self) -> Dict[str, Any]:
        return {
            "schema": "run9-ephemeral-generated-export/1.0",
            "artifacts": {key: dict(value) for key, value in self.artifacts.items()},
        }


def _canonical_json_bytes(value: Any) -> bytes:
    return bp.canonical_json_bytes(value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json_object(path: Path, *, label: str) -> Dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise bp.BirthProbeError(f"{label} is unreadable or invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise bp.BirthProbeError(f"{label} must be a JSON object")
    return value


def load_admission_policy(path: Path = _POLICY_PATH) -> Dict[str, Any]:
    """Load and structurally verify the candidate-independent policy."""
    policy = _read_json_object(path, label="success-only admission policy")
    if set(policy) != {
        "admission",
        "evaluator_boundary",
        "expected_takes_per_founder",
        "generated_export_artifacts",
        "measurement_runtime",
        "schema",
        "successful_bundle",
    }:
        raise bp.BirthProbeError("success-only admission policy top-level keys are not closed")
    if policy.get("schema") != _POLICY_SCHEMA:
        raise bp.BirthProbeError("success-only admission policy schema is unsupported")
    admission = policy["admission"]
    if admission != {
        "decision": "PASS_ONLY",
        "failure_registry_effect": "NONE",
        "output_directory_prefix": "successful_",
        "registration_allowed_value": True,
        "required_measurement_overall_pass": True,
    }:
        raise bp.BirthProbeError("success-only admission policy decision boundary changed")
    boundary = policy["evaluator_boundary"]
    allowed = {
        "rendered_wav_bytes",
        "serialized_identity_features",
        "fixed_control_profile_ids",
        "fixed_pjs_reference_feature",
    }
    forbidden = {
        "candidate_artifact_bytes",
        "candidate_artifact_sha256",
        "candidate_attempt_history",
        "candidate_registration_state",
        "candidate_registry_path",
    }
    if set(boundary.get("allowed_inputs", ())) != allowed:
        raise bp.BirthProbeError("output-only evaluator allowed-input set changed")
    if set(boundary.get("forbidden_inputs", ())) != forbidden:
        raise bp.BirthProbeError("output-only evaluator forbidden-input set changed")
    artifacts = policy["generated_export_artifacts"]
    if not isinstance(artifacts, dict) or len(artifacts) != 9:
        raise bp.BirthProbeError("success-only policy must name exactly nine export artifacts")
    if len(set(artifacts.values())) != 9 or any(
        not isinstance(name, str) or not name or Path(name).name != name
        for name in artifacts.values()
    ):
        raise bp.BirthProbeError("generated export filenames are not nine unique basenames")
    if policy.get("expected_takes_per_founder") != 20:
        raise bp.BirthProbeError("success-only policy must require 20 takes per founder")
    bundle = policy["successful_bundle"]
    if bundle != {
        "artifact_manifest_schema": _MANIFEST_SCHEMA,
        "bundle_schema": _ADMISSION_SCHEMA,
        "required_generated_export_count": 9,
        "required_render_count": 84,
    }:
        raise bp.BirthProbeError("successful bundle closed-world contract changed")
    return policy


def snapshot_generated_export(
    acoustic_dir: Path,
    policy: Mapping[str, Any],
) -> GeneratedExportSnapshot:
    """Snapshot a fresh export without assigning it a registered identity."""
    root = acoustic_dir.resolve(strict=True)
    names = policy["generated_export_artifacts"]
    actual_files = {path.name for path in root.iterdir() if path.is_file()}
    expected_files = set(names.values())
    if actual_files != expected_files:
        raise bp.BirthProbeError(
            "generated export is not the closed nine-file set: "
            f"missing={sorted(expected_files - actual_files)!r}, "
            f"unexpected={sorted(actual_files - expected_files)!r}"
        )
    artifact_bytes: Dict[str, bytes] = {}
    artifacts: Dict[str, Dict[str, Any]] = {}
    for logical_name, filename in sorted(names.items()):
        path = root / filename
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise bp.BirthProbeError(f"generated export path escapes its root: {path}")
        value = path.read_bytes()
        if not value:
            raise bp.BirthProbeError(f"generated export artifact is empty: {filename}")
        digest = _sha256_bytes(value)
        artifact_bytes[filename] = value
        artifacts[logical_name] = {
            "bytes": len(value),
            "file": filename,
            "sha256_run1": digest,
        }
    snapshot = GeneratedExportSnapshot(
        root=root,
        artifacts=artifacts,
        artifact_bytes=artifact_bytes,
    )
    _validated_generated_artifact_bytes(snapshot, policy)
    return snapshot


def _validated_generated_artifact_bytes(
    snapshot: GeneratedExportSnapshot,
    policy: Mapping[str, Any],
) -> Dict[str, bytes]:
    """Copy and verify the exact model-byte payload bound by the snapshot records."""
    expected = policy["generated_export_artifacts"]
    if set(snapshot.artifacts) != set(expected):
        raise bp.BirthProbeError("generated export snapshot logical artifact set changed")
    if set(snapshot.artifact_bytes) != set(expected.values()):
        raise bp.BirthProbeError("generated export snapshot byte payload set changed")
    copied: Dict[str, bytes] = {}
    for logical_name, filename in sorted(expected.items()):
        record = snapshot.artifacts.get(logical_name)
        if not isinstance(record, Mapping) or set(record) != {
            "bytes",
            "file",
            "sha256_run1",
        }:
            raise bp.BirthProbeError(
                f"generated export snapshot record is invalid: {logical_name}"
            )
        value = snapshot.artifact_bytes.get(filename)
        if type(value) is not bytes or not value:
            raise bp.BirthProbeError(
                f"generated export snapshot byte payload is invalid: {filename}"
            )
        if (
            record.get("file") != filename
            or record.get("bytes") != len(value)
            or record.get("sha256_run1") != _sha256_bytes(value)
        ):
            raise bp.BirthProbeError(
                f"generated export snapshot byte payload diverges from its record: {filename}"
            )
        copied[filename] = value
    return copied


def _snapshot_repo_inputs() -> Dict[str, str]:
    paths = {
        "success_only_admission_policy_sha256": _POLICY_PATH,
        "success_admission_executor_sha256": Path(__file__).resolve(),
        "birth_probe_executor_sha256": _THIS_DIR / "birth_probe_executor.py",
        "run9_contract_sha256": _THIS_DIR / "RUN9_CONTRACT.yaml",
        "identity_decision_protocol_sha256": (
            _THIS_DIR / "inputs" / "identity_decision_protocol_v0.6.json"
        ),
        "probe_manifest_sha256": _THIS_DIR / "evaluation" / "probe_manifest.json",
        "speaker_map_manifest_sha256": _THIS_DIR / "inputs" / "speaker_map_manifest.json",
        "backbone_runtime_bundle_sha256": (
            _THIS_DIR / "inputs" / "backbone_runtime_bundle.json"
        ),
        "practice_audio_split_manifest_sha256": (
            _THIS_DIR / "inputs" / "practice_audio_split_manifest.json"
        ),
        "run9_schema_sha256": _THIS_DIR / "run9_schema.py",
        "run9_controlprofile_sha256": _THIS_DIR / "run9_controlprofile.py",
        "gate_synth_sha256": (
            _REPO_ROOT / "voice_genesis" / "foundry" / "s1_gate" / "gate_synth.py"
        ),
    }
    return {name: _sha256_bytes(path.read_bytes()) for name, path in paths.items()}


def _verify_repo_inputs_unchanged(snapshot: Mapping[str, str]) -> None:
    current = _snapshot_repo_inputs()
    if current != dict(snapshot):
        changed = sorted(key for key in set(snapshot) | set(current) if snapshot.get(key) != current.get(key))
        raise bp.BirthProbeError(f"success-only evaluator inputs changed during run: {changed!r}")


def _verify_source_commit(source_commit: str) -> None:
    if _COMMIT_RE.fullmatch(source_commit) is None:
        raise bp.BirthProbeError("source commit must be exactly 40 lowercase hex characters")
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip() != source_commit:
        raise bp.BirthProbeError("checked-out repository HEAD does not equal the launch commit")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise bp.BirthProbeError("checked-out repository has tracked modifications")


def _verify_network_isolation() -> None:
    """Require an OUTPUT-drop firewall before ONNX Runtime session creation."""
    try:
        rules = subprocess.run(
            ["iptables", "-S", "OUTPUT"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise bp.BirthProbeError("render network isolation could not be verified") from exc
    required = {
        "-P OUTPUT DROP",
        "-A OUTPUT -o lo -j ACCEPT",
        "-A OUTPUT -j REJECT --reject-with icmp-port-unreachable",
    }
    established_rules = {
        "-A OUTPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
        "-A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
    }
    if not required.issubset(set(rules)) or not established_rules.intersection(rules):
        raise bp.BirthProbeError("render process does not have the required OUTPUT-drop firewall")


def _disable_ort_telemetry() -> None:
    try:
        ort = importlib.import_module("onnxruntime")
        disable = getattr(ort, "disable_telemetry_events")
        disable()
    except (ImportError, AttributeError, RuntimeError) as exc:
        raise bp.BirthProbeError("ONNX Runtime telemetry could not be disabled") from exc


def _validate_runtime(policy: Mapping[str, Any]) -> Dict[str, str]:
    runtime = policy["measurement_runtime"]
    packages = runtime["packages"]
    expected_direct = {
        name: version for name, version in packages.items() if name != "setuptools"
    }
    if set(expected_direct) != {
        "numpy",
        "scipy",
        "soundfile",
        "PyYAML",
        "onnxruntime",
        "pyworld",
    }:
        raise bp.BirthProbeError("measurement runtime dependency set is not closed")
    profile = {
        "identity_semantics": {
            "runtime": {
                "architecture": runtime["architecture"],
                "onnxruntime": packages["onnxruntime"],
                "os": runtime["operating_system"],
                "python": runtime["python"],
                "selected_execution_provider": runtime["selected_execution_provider"],
            }
        }
    }
    observed = bp._validate_execution_environment(profile, expected_direct)  # noqa: SLF001
    setuptools_version = importlib_metadata.version("setuptools")
    if setuptools_version != packages["setuptools"]:
        raise bp.BirthProbeError(
            "setuptools version mismatch: "
            f"expected {packages['setuptools']!r}, got {setuptools_version!r}"
        )
    return {**observed, "setuptools": setuptools_version}


def _load_measurement_inputs(policy: Mapping[str, Any]) -> tuple[
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    int,
]:
    import run9_schema

    contract = run9_schema.load_run9_contract_from_yaml_path(_THIS_DIR / "RUN9_CONTRACT.yaml")
    domain = run9_schema.load_run9_identity_domain(
        _THIS_DIR / "domains" / "identity_domain_run9_v1.json"
    )
    protocol = run9_schema.load_pinned_identity_decision_protocol(contract, domain=domain)
    probe = run9_schema.load_pinned_probe_manifest(contract)
    run9_schema.verify_user_donor_manifest_complete()
    rights = run9_schema.load_rights_manifest_json(
        (_THIS_DIR / "inputs" / "rights_manifest.json").read_text(encoding="utf-8")
    )
    speaker_map = run9_schema.load_pinned_speaker_map_manifest(
        contract,
        domain=domain,
        rights_manifest=rights,
    )
    backbone = bp._load_pinned_json(  # noqa: SLF001
        contract,
        "backbone_runtime_bundle_sha",
        _THIS_DIR / "inputs" / "backbone_runtime_bundle.json",
    )
    practice_split = run9_schema.load_pinned_practice_split_manifest(contract)
    expected_takes = protocol["c0_determinism_attestation"]["takes_per_founder"]
    if expected_takes != policy["expected_takes_per_founder"]:
        raise bp.BirthProbeError("policy take count diverges from the pinned identity protocol")
    if expected_takes != protocol["c1_sham_attestation"]["takes_per_founder"]:
        raise bp.BirthProbeError("C0 and C1 take counts diverge")
    return probe, speaker_map, backbone, practice_split, expected_takes


def _verify_snapshot_still_matches(snapshot: GeneratedExportSnapshot) -> None:
    policy = load_admission_policy()
    expected_bytes = _validated_generated_artifact_bytes(snapshot, policy)
    current = snapshot_generated_export(snapshot.root, policy)
    current_bytes = _validated_generated_artifact_bytes(current, policy)
    if current.artifacts != snapshot.artifacts or current_bytes != expected_bytes:
        raise bp.BirthProbeError("generated export bytes changed during measurement")


def _issue_success_admission(
    *,
    measurement: Mapping[str, Any],
    snapshot: GeneratedExportSnapshot,
    source_commit: str,
    repo_provenance: Mapping[str, str],
) -> _IssuedSuccessAdmission:
    """Mint registration authority only after the output-only decision is PASS."""
    policy = load_admission_policy()
    _validated_generated_artifact_bytes(snapshot, policy)
    if measurement.get("schema") != bp._RESULT_SCHEMA:  # noqa: SLF001
        raise bp.BirthProbeError("success admission requires the fixed Birth evidence schema")
    if measurement.get("overall_pass") is not True:
        raise ArtifactRejected("generated artifact did not pass the fixed output-only evaluator")
    if measurement.get("learning_progression_allowed") is not True:
        raise bp.BirthProbeError("PASS result did not grant the protocol progression flag")
    sealed = dict(measurement)
    claimed = sealed.pop("evidence_sha256", None)
    if not isinstance(claimed, str) or _sha256_bytes(_canonical_json_bytes(sealed)) != claimed:
        raise bp.BirthProbeError("PASS measurement evidence seal is invalid")
    record: Dict[str, Any] = {
        "schema": _ADMISSION_SCHEMA,
        "admission_decision": "PASS",
        "registration_allowed": True,
        "source_commit": source_commit,
        "measurement_evidence_sha256": claimed,
        "repo_provenance": dict(repo_provenance),
        "generated_export_artifacts": {
            key: {
                "bytes": value["bytes"],
                "file": value["file"],
                "sha256": value["sha256_run1"],
            }
            for key, value in sorted(snapshot.artifacts.items())
        },
    }
    record["admission_sha256"] = _sha256_bytes(_canonical_json_bytes(record))
    admission = _IssuedSuccessAdmission(record)
    _ISSUED_ADMISSIONS[id(admission)] = record["admission_sha256"]
    return admission


def execute_success_only_admission(
    *,
    acoustic_dir: Path,
    canon_model_dir: Path,
    vocoder_dir: Path,
    pjs_corpus_root: Path,
    source_commit: str,
) -> tuple[
    _IssuedSuccessAdmission,
    Dict[str, Any],
    Dict[str, Dict[str, list[bp.RenderObservation]]],
    bp.PJSReference,
    GeneratedExportSnapshot,
]:
    """Render, judge outputs, and issue a success capability; never register failure."""
    policy = load_admission_policy()
    _verify_source_commit(source_commit)
    _verify_network_isolation()
    _disable_ort_telemetry()
    runtime_versions = _validate_runtime(policy)
    bp._assert_helper_modules_not_preloaded()  # noqa: SLF001
    repo_provenance = _snapshot_repo_inputs()
    probe, speaker_map, backbone, practice_split, expected_takes = _load_measurement_inputs(
        policy
    )
    generated = snapshot_generated_export(acoustic_dir, policy)
    renderer = bp.GateSynthRenderer(
        acoustic_dir=generated.root,
        canon_model_dir=canon_model_dir,
        vocoder_dir=vocoder_dir,
        reexport_manifest=generated.renderer_manifest(),
        backbone_bundle=backbone,
        probe_manifest=probe,
        speaker_map_manifest=speaker_map,
    )
    if renderer.verified_acoustic_sha256 != generated.acoustic_sha256:
        raise bp.BirthProbeError("renderer did not consume the snapshotted acoustic bytes")
    pjs_record = bp.build_pjs_reference(
        pjs_corpus_root,
        expected_corpus_sha256=practice_split["expanded_corpus_identity_sha256"],
    )
    measurement, observations = bp.execute_birth_gate(
        renderer=renderer,
        pjs_reference=pjs_record.feature,
        extractor=bp.extract_identity_feature,
        expected_takes=expected_takes,
    )
    renderer.verify_inputs_unchanged()
    _verify_snapshot_still_matches(generated)
    _verify_repo_inputs_unchanged(repo_provenance)
    measurement["measurement_provenance"] = {
        **repo_provenance,
        "runtime_dependency_versions": runtime_versions,
        "practice_expanded_corpus_identity_sha256": pjs_record.corpus_sha256,
        "pjs_reference_excluded_relative_paths": list(pjs_record.excluded_relative_paths),
        "candidate_identity_exposed_to_evaluator": False,
    }
    bp._seal_result(measurement)  # noqa: SLF001
    admission = _issue_success_admission(
        measurement=measurement,
        snapshot=generated,
        source_commit=source_commit,
        repo_provenance=repo_provenance,
    )
    return admission, measurement, observations, pjs_record, generated


def _observation_count(
    observations: Mapping[str, Mapping[str, Sequence[bp.RenderObservation]]],
) -> int:
    if set(observations) != set(_FOUNDER_IDS):
        raise bp.BirthProbeError("successful observations are not closed-world by founder")
    total = 0
    for founder in _FOUNDER_IDS:
        if set(observations[founder]) != set(_CONDITIONS):
            raise bp.BirthProbeError("successful observations are not closed-world by condition")
        expected = {"reference": 1, "c0": 20, "c1": 20, "positive_reference": 1}
        for condition, count in expected.items():
            if len(observations[founder][condition]) != count:
                raise bp.BirthProbeError(
                    f"successful observation count mismatch for {founder}/{condition}"
                )
            total += count
    return total


def _verify_measurement_artifact_binding(
    measurement: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Sequence[bp.RenderObservation]]],
    pjs_reference: bp.FeatureArtifact,
) -> None:
    """Bind the sealed PASS record to every byte about to be published."""
    sealed = dict(measurement)
    claimed = sealed.pop("evidence_sha256", None)
    if not isinstance(claimed, str) or _sha256_bytes(_canonical_json_bytes(sealed)) != claimed:
        raise bp.BirthProbeError("successful measurement evidence seal is invalid")
    for founder in _FOUNDER_IDS:
        for condition in _CONDITIONS:
            for index, observation in enumerate(observations[founder][condition]):
                if observation.wav_sha256 != _sha256_bytes(observation.wav):
                    raise bp.BirthProbeError(
                        f"successful WAV observation seal mismatch: {founder}/{condition}/{index}"
                    )
                if not bp._feature_valid(observation.feature):  # noqa: SLF001
                    raise bp.BirthProbeError(
                        f"successful feature observation is invalid: {founder}/{condition}/{index}"
                    )
                record = bp._staged_result_record(  # noqa: SLF001
                    measurement,
                    founder,
                    condition,
                    index,
                )
                if (
                    record.get("wav_sha256") != observation.wav_sha256
                    or record.get("feature_sha256") != observation.feature.sha256
                    or record.get("feature_bytes") != len(observation.feature.data)
                ):
                    raise bp.BirthProbeError(
                        "successful measurement record does not bind the published observation: "
                        f"{founder}/{condition}/{index}"
                    )
    if not bp._feature_valid(pjs_reference):  # noqa: SLF001
        raise bp.BirthProbeError("successful PJS reference feature is invalid")
    pjs_record = measurement.get("pjs_reference")
    if not isinstance(pjs_record, Mapping) or (
        pjs_record.get("feature_sha256") != pjs_reference.sha256
        or pjs_record.get("feature_bytes") != len(pjs_reference.data)
    ):
        raise bp.BirthProbeError(
            "successful measurement record does not bind the published PJS reference"
        )


def publish_successful_artifact_bundle(
    output_dir: Path,
    admission: Mapping[str, Any],
    measurement: Mapping[str, Any],
    observations: Mapping[str, Mapping[str, Sequence[bp.RenderObservation]]],
    pjs_reference: bp.FeatureArtifact,
    generated: GeneratedExportSnapshot,
) -> None:
    """Atomically publish the complete model/evidence bundle after PASS only."""
    policy = load_admission_policy()
    generated_artifact_bytes = _validated_generated_artifact_bytes(generated, policy)
    registered_seal = _ISSUED_ADMISSIONS.get(id(admission))
    if type(admission) is not _IssuedSuccessAdmission or registered_seal is None:
        raise bp.BirthProbeError("success admission was not issued by this executor")
    if admission.get("schema") != _ADMISSION_SCHEMA:
        raise bp.BirthProbeError("success admission schema is invalid")
    if admission.get("registration_allowed") is not True:
        raise bp.BirthProbeError("success admission does not permit registration")
    if admission.get("admission_sha256") != registered_seal:
        raise bp.BirthProbeError("success admission changed after issuance")
    sealed = dict(admission)
    claimed = sealed.pop("admission_sha256", None)
    if _sha256_bytes(_canonical_json_bytes(sealed)) != claimed:
        raise bp.BirthProbeError("success admission seal is invalid")
    if measurement.get("overall_pass") is not True:
        raise ArtifactRejected("refusing to publish a non-PASS measurement")
    if measurement.get("schema") != bp._RESULT_SCHEMA:  # noqa: SLF001
        raise bp.BirthProbeError("successful measurement schema is invalid")
    if measurement.get("evidence_sha256") != admission.get("measurement_evidence_sha256"):
        raise bp.BirthProbeError("measurement no longer matches the issued success admission")
    if len(generated.artifacts) != 9:
        raise bp.BirthProbeError("successful export snapshot does not contain nine artifacts")
    admitted_artifacts = admission.get("generated_export_artifacts")
    expected_admitted_artifacts = {
        key: {
            "bytes": value["bytes"],
            "file": value["file"],
            "sha256": value["sha256_run1"],
        }
        for key, value in sorted(generated.artifacts.items())
    }
    if admitted_artifacts != expected_admitted_artifacts:
        raise bp.BirthProbeError("successful export snapshot diverges from issued admission")
    if _observation_count(observations) != 84:
        raise bp.BirthProbeError("successful bundle must contain exactly 84 render observations")
    _verify_measurement_artifact_binding(measurement, observations, pjs_reference)
    _verify_snapshot_still_matches(generated)
    output_dir = output_dir.resolve()
    if not output_dir.name.startswith("successful_"):
        raise bp.BirthProbeError("successful output directory must start with 'successful_'")
    if output_dir.exists():
        raise bp.BirthProbeError(f"refusing to overwrite successful bundle: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.build-", dir=output_dir.parent))
    try:
        (staging / "model").mkdir()
        (staging / "wav").mkdir()
        (staging / "features").mkdir()
        for filename, value in generated_artifact_bytes.items():
            (staging / "model" / filename).write_bytes(value)
        for founder in _FOUNDER_IDS:
            for condition in _CONDITIONS:
                for index, observation in enumerate(observations[founder][condition]):
                    stem = f"{founder}_{condition}_{index:02d}"
                    (staging / "wav" / f"{stem}.wav").write_bytes(observation.wav)
                    (staging / "features" / f"{stem}.bin").write_bytes(
                        observation.feature.data
                    )
        (staging / "features" / "pjs_reference.bin").write_bytes(pjs_reference.data)
        (staging / "success_admission.json").write_bytes(
            _canonical_json_bytes(dict(admission))
        )
        (staging / "birth_gate_evidence.json").write_bytes(
            _canonical_json_bytes(dict(measurement))
        )
        success_marker = {
            "schema": _SUCCESS_MARKER_SCHEMA,
            "admission_sha256": claimed,
            "registration_allowed": True,
        }
        (staging / "SUCCESS.json").write_bytes(_canonical_json_bytes(success_marker))
        for filename, expected in generated_artifact_bytes.items():
            if (staging / "model" / filename).read_bytes() != expected:
                raise bp.BirthProbeError(
                    f"staged successful model readback mismatch: {filename}"
                )
        for founder in _FOUNDER_IDS:
            for condition in _CONDITIONS:
                for index, observation in enumerate(observations[founder][condition]):
                    stem = f"{founder}_{condition}_{index:02d}"
                    if (staging / "wav" / f"{stem}.wav").read_bytes() != observation.wav:
                        raise bp.BirthProbeError(
                            f"staged successful WAV readback mismatch: {stem}"
                        )
                    if (
                        staging / "features" / f"{stem}.bin"
                    ).read_bytes() != observation.feature.data:
                        raise bp.BirthProbeError(
                            f"staged successful feature readback mismatch: {stem}"
                        )
        if (staging / "features" / "pjs_reference.bin").read_bytes() != pjs_reference.data:
            raise bp.BirthProbeError("staged successful PJS readback mismatch")
        expected_json_files = {
            "success_admission.json": _canonical_json_bytes(dict(admission)),
            "birth_gate_evidence.json": _canonical_json_bytes(dict(measurement)),
            "SUCCESS.json": _canonical_json_bytes(success_marker),
        }
        for filename, expected in expected_json_files.items():
            if (staging / filename).read_bytes() != expected:
                raise bp.BirthProbeError(
                    f"staged successful JSON readback mismatch: {filename}"
                )
        inventory = {
            path.relative_to(staging).as_posix(): _sha256_bytes(path.read_bytes())
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        manifest = {"schema": _MANIFEST_SCHEMA, "files": inventory}
        (staging / "artifact_manifest.json").write_bytes(_canonical_json_bytes(manifest))
        expected_files = set(inventory) | {"artifact_manifest.json"}
        actual_files = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        if actual_files != expected_files:
            raise bp.BirthProbeError("successful bundle inventory changed before publication")
        parsed_manifest = _read_json_object(
            staging / "artifact_manifest.json",
            label="staged successful artifact manifest",
        )
        if parsed_manifest != manifest:
            raise bp.BirthProbeError("successful artifact manifest failed readback")
        os.replace(staging, output_dir)
        _ISSUED_ADMISSIONS.pop(id(admission), None)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acoustic-dir", required=True, type=Path)
    parser.add_argument("--canon-model-dir", required=True, type=Path)
    parser.add_argument("--vocoder-dir", required=True, type=Path)
    parser.add_argument("--pjs-corpus-root", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        print("RUN9_ADMISSION_ABORTED: output path already exists", file=sys.stderr)
        return 2
    try:
        admission, measurement, observations, pjs_record, generated = (
            execute_success_only_admission(
                acoustic_dir=args.acoustic_dir,
                canon_model_dir=args.canon_model_dir,
                vocoder_dir=args.vocoder_dir,
                pjs_corpus_root=args.pjs_corpus_root,
                source_commit=args.source_commit,
            )
        )
        publish_successful_artifact_bundle(
            args.out,
            admission,
            measurement,
            observations,
            pjs_record.feature,
            generated,
        )
    except ArtifactRejected as exc:
        if args.out.exists():
            raise bp.BirthProbeError("rejected artifact unexpectedly created a registration")
        print(f"RUN9_ARTIFACT_REJECTED: {exc}", file=sys.stderr)
        return 3
    except (bp.BirthProbeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"RUN9_ADMISSION_ABORTED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "admission": "PASS",
                "out": str(args.out),
                "registration_allowed": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
