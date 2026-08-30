"""Success-only registration and output-only evaluator boundary tests."""
from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest


_RUN_DIR = Path(__file__).resolve().parent.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import birth_probe_executor as bp  # noqa: E402
import run9_success_admission as admission  # noqa: E402


FOUNDERS = ("R9F-01", "R9F-02")
CONDITIONS = ("reference", "c0", "c1", "positive_reference")


def _write_generated_export(root: Path) -> admission.GeneratedExportSnapshot:
    policy = admission.load_admission_policy()
    root.mkdir()
    for index, filename in enumerate(policy["generated_export_artifacts"].values()):
        (root / filename).write_bytes(f"generated-{index}-{filename}".encode())
    return admission.snapshot_generated_export(root, policy)


def _observation(label: str, profile: str | None = None) -> bp.RenderObservation:
    return bp.RenderObservation.build(
        f"wav-{label}".encode(),
        bp.FeatureArtifact.from_vector(np.asarray([0.0, 1.0], dtype=np.float64)),
        control_profile_id=profile,
    )


def _observations() -> dict[str, dict[str, list[bp.RenderObservation]]]:
    result: dict[str, dict[str, list[bp.RenderObservation]]] = {}
    for founder in FOUNDERS:
        replay, sham = bp._control_profiles(founder)  # noqa: SLF001
        result[founder] = {
            "reference": [_observation(f"{founder}-reference")],
            "c0": [_observation(f"{founder}-c0-{i}", replay.profile_id) for i in range(20)],
            "c1": [_observation(f"{founder}-c1-{i}", sham.profile_id) for i in range(20)],
            "positive_reference": [_observation(f"{founder}-positive")],
        }
    return result


def _measurement(*, passed: bool) -> dict[str, object]:
    observations = _observations()
    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))

    def record(item: bp.RenderObservation) -> dict[str, object]:
        return {
            "wav_sha256": item.wav_sha256,
            "feature_sha256": item.feature.sha256,
            "feature_bytes": len(item.feature.data),
        }

    value: dict[str, object] = {
        "schema": bp._RESULT_SCHEMA,  # noqa: SLF001
        "overall_pass": passed,
        "learning_progression_allowed": passed,
        "founders": {
            founder: {
                "reference": record(observations[founder]["reference"][0]),
                "c0": [record(item) for item in observations[founder]["c0"]],
                "c1": [record(item) for item in observations[founder]["c1"]],
                "positive_reference": record(
                    observations[founder]["positive_reference"][0]
                ),
            }
            for founder in FOUNDERS
        },
        "pjs_reference": {
            "feature_sha256": pjs.sha256,
            "feature_bytes": len(pjs.data),
        },
    }
    return bp._seal_result(value)  # noqa: SLF001


def _issue(
    snapshot: admission.GeneratedExportSnapshot,
    *,
    passed: bool = True,
) -> admission._IssuedSuccessAdmission:  # noqa: SLF001
    return admission._issue_success_admission(  # noqa: SLF001
        measurement=_measurement(passed=passed),
        snapshot=snapshot,
        source_commit="1" * 40,
        repo_provenance={"policy": "2" * 64},
    )


def test_policy_is_pass_only_and_candidate_blind() -> None:
    policy = admission.load_admission_policy()
    assert policy["admission"]["decision"] == "PASS_ONLY"
    assert policy["admission"]["failure_registry_effect"] == "NONE"
    assert "candidate_artifact_sha256" in policy["evaluator_boundary"]["forbidden_inputs"]
    assert "candidate_artifact_bytes" in policy["evaluator_boundary"]["forbidden_inputs"]
    assert len(policy["generated_export_artifacts"]) == 9


def test_measurement_lock_is_bound_into_repo_provenance() -> None:
    snapshot = admission._snapshot_repo_inputs()  # noqa: SLF001
    assert "measurement_environment_lock_sha256" in snapshot
    lock_bytes = admission._MEASUREMENT_LOCK_PATH.read_bytes()  # noqa: SLF001
    assert snapshot["measurement_environment_lock_sha256"] == admission._sha256_bytes(lock_bytes)  # noqa: SLF001


def test_native_measurement_lock_is_bound_into_repo_provenance() -> None:
    snapshot = admission._snapshot_repo_inputs()  # noqa: SLF001
    key = "measurement_native_install_lock_sha256"
    path = admission._MEASUREMENT_NATIVE_INSTALL_LOCK_PATH  # noqa: SLF001
    assert key in snapshot
    assert "measurement_native_manifest_sha256" not in snapshot
    assert snapshot[key] == admission._sha256_bytes(path.read_bytes())  # noqa: SLF001


def test_native_measurement_lock_excludes_os_base_packages() -> None:
    rows = [
        line.strip()
        for line in admission._MEASUREMENT_NATIVE_INSTALL_LOCK_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]  # noqa: SLF001
    names = {row.rpartition("=")[0].split(":", 1)[0] for row in rows}
    forbidden = {
        "build-essential",
        "dpkg", "dpkg-dev", "libdpkg-perl",
        "libc-bin", "libc-dev-bin", "libc6", "libc6-dev",
        "linux-libc-dev",
        "perl-base", "perl-modules-5.38", "libperl5.38t64",
    }
    required = {"gcc", "g++", "make", "binutils", "libffi-dev", "libsndfile1"}
    assert names.isdisjoint(forbidden)
    assert required <= names


def test_native_runtime_validator_queries_only_committed_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = [
        line for line in admission._MEASUREMENT_NATIVE_INSTALL_LOCK_PATH.read_text(encoding="utf-8").splitlines() if line
    ]  # noqa: SLF001
    captured = {}
    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        return admission.subprocess.CompletedProcess(args=args, returncode=0, stdout="\n".join(expected) + "\n", stderr="")
    monkeypatch.setattr(admission.subprocess, "run", fake_run)
    observed = admission._validate_native_runtime()  # noqa: SLF001
    assert observed["closure_sha256"] == admission._sha256_bytes(  # noqa: SLF001
        admission._MEASUREMENT_NATIVE_INSTALL_LOCK_PATH.read_bytes()  # noqa: SLF001
    )
    assert observed["package_count"] == len(expected)
    queried = captured["args"][3:]
    assert queried == [row.rpartition("=")[0] for row in sorted(expected)]


def test_native_runtime_validator_rejects_consumed_closure_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = admission.subprocess.CompletedProcess(
        args=["dpkg-query"], returncode=0, stdout="unexpected:amd64=1.0\n", stderr=""
    )
    monkeypatch.setattr(admission.subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(bp.BirthProbeError, match="native measurement dependency closure mismatch"):
        admission._validate_native_runtime()  # noqa: SLF001

def test_production_cli_has_no_network_isolation_bypass() -> None:
    source = Path(admission.__file__).read_text(encoding="utf-8")
    assert "allow-test-network" not in source


def test_verify_network_isolation_rejects_unknown_mode() -> None:
    with pytest.raises(bp.BirthProbeError, match="unknown network isolation mode"):
        admission._verify_network_isolation("not-a-real-mode")  # noqa: SLF001


def test_verify_network_isolation_netns_accepts_loopback_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admission.os, "listdir", lambda path: ["lo"])
    admission._verify_network_isolation("netns")  # noqa: SLF001
    admission._verify_network_isolation("userns-netns")  # noqa: SLF001


def test_verify_network_isolation_netns_rejects_extra_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(admission.os, "listdir", lambda path: ["lo", "eth0"])
    with pytest.raises(bp.BirthProbeError, match="interface-less network namespace"):
        admission._verify_network_isolation("netns")  # noqa: SLF001


def test_verify_network_isolation_netns_wraps_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_oserror(path: str) -> list[str]:
        raise OSError("no such directory")

    monkeypatch.setattr(admission.os, "listdir", raise_oserror)
    with pytest.raises(bp.BirthProbeError, match="could not be verified"):
        admission._verify_network_isolation("netns")  # noqa: SLF001


class _FakeUnixSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


_ALL_INET_PAIRS = frozenset(
    {
        (admission.socket.AF_INET, admission.socket.SOCK_STREAM),
        (admission.socket.AF_INET, admission.socket.SOCK_DGRAM),
        (admission.socket.AF_INET6, admission.socket.SOCK_STREAM),
        (admission.socket.AF_INET6, admission.socket.SOCK_DGRAM),
    }
)


def _seccomp_blocking_socket_factory(*, blocked_pairs: set[tuple[int, int]]) -> Any:
    def factory(family: int, kind: int) -> Any:
        if (family, kind) in blocked_pairs:
            raise OSError(admission.errno.EPERM, "Operation not permitted")
        return _FakeUnixSocket()

    return factory


def test_verify_network_isolation_seccomp_accepts_blocked_inet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        admission.socket,
        "socket",
        _seccomp_blocking_socket_factory(blocked_pairs=set(_ALL_INET_PAIRS)),
    )
    admission._verify_network_isolation("seccomp")  # noqa: SLF001


def test_verify_network_isolation_seccomp_rejects_unblocked_inet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = {
        pair
        for pair in _ALL_INET_PAIRS
        if pair[0] != admission.socket.AF_INET
    }
    monkeypatch.setattr(
        admission.socket,
        "socket",
        _seccomp_blocking_socket_factory(blocked_pairs=blocked),
    )
    with pytest.raises(bp.BirthProbeError, match="can still create AF_INET/SOCK_STREAM sockets"):
        admission._verify_network_isolation("seccomp")  # noqa: SLF001


def test_verify_network_isolation_seccomp_rejects_unblocked_inet6(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = {
        pair
        for pair in _ALL_INET_PAIRS
        if pair[0] != admission.socket.AF_INET6
    }
    monkeypatch.setattr(
        admission.socket,
        "socket",
        _seccomp_blocking_socket_factory(blocked_pairs=blocked),
    )
    with pytest.raises(bp.BirthProbeError, match="can still create AF_INET6/SOCK_STREAM sockets"):
        admission._verify_network_isolation("seccomp")  # noqa: SLF001


def test_verify_network_isolation_seccomp_rejects_dgram_only_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the P2 false-success gap: SOCK_DGRAM blocked, SOCK_STREAM (TCP) open."""
    blocked = {pair for pair in _ALL_INET_PAIRS if pair[1] == admission.socket.SOCK_DGRAM}
    monkeypatch.setattr(
        admission.socket,
        "socket",
        _seccomp_blocking_socket_factory(blocked_pairs=blocked),
    )
    with pytest.raises(bp.BirthProbeError, match="can still create AF_INET/SOCK_STREAM sockets"):
        admission._verify_network_isolation("seccomp")  # noqa: SLF001


def test_verify_network_isolation_seccomp_rejects_wrong_errno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(family: int, kind: int) -> Any:
        if family == admission.socket.AF_INET and kind == admission.socket.SOCK_STREAM:
            raise OSError(admission.errno.EACCES, "Permission denied")
        if (family, kind) in _ALL_INET_PAIRS:
            raise OSError(admission.errno.EPERM, "Operation not permitted")
        return _FakeUnixSocket()

    monkeypatch.setattr(admission.socket, "socket", factory)
    with pytest.raises(bp.BirthProbeError, match="can still create AF_INET/SOCK_STREAM sockets"):
        admission._verify_network_isolation("seccomp")  # noqa: SLF001


def test_verify_network_isolation_seccomp_rejects_broken_unix_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(family: int, kind: int) -> Any:
        if family == admission.socket.AF_UNIX:
            raise OSError("unix sockets unavailable")
        if (family, kind) in _ALL_INET_PAIRS:
            raise OSError(admission.errno.EPERM, "Operation not permitted")
        return _FakeUnixSocket()

    monkeypatch.setattr(admission.socket, "socket", factory)
    with pytest.raises(bp.BirthProbeError, match="seccomp verification socket baseline failed"):
        admission._verify_network_isolation("seccomp")  # noqa: SLF001


def test_seccomp_prelude_module_parses() -> None:
    prelude = _RUN_DIR / "run9_seccomp_prelude.py"
    ast.parse(prelude.read_text(encoding="utf-8"))


def test_seccomp_prelude_rejects_bad_usage() -> None:
    completed = subprocess.run(
        [sys.executable, str(_RUN_DIR / "run9_seccomp_prelude.py")],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "usage: run9_seccomp_prelude.py" in completed.stderr


def test_scientific_evaluator_signature_has_no_candidate_or_registry_input() -> None:
    parameters = set(inspect.signature(bp.evaluate_birth_gate).parameters)
    assert parameters == {
        "references",
        "c0_takes",
        "c1_takes",
        "positive_references",
        "pjs_reference",
        "expected_takes",
    }
    assert not any("candidate" in name or "registry" in name for name in parameters)


def test_generated_export_snapshot_has_no_preregistered_digest(tmp_path: Path) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    assert len(snapshot.artifacts) == 9
    assert all(
        set(record) == {"bytes", "file", "sha256_run1"}
        for record in snapshot.artifacts.values()
    )
    policy_text = admission._POLICY_PATH.read_text(encoding="utf-8")  # noqa: SLF001
    assert snapshot.acoustic_sha256 not in policy_text


def test_generated_export_requires_exactly_nine_files(tmp_path: Path) -> None:
    policy = admission.load_admission_policy()
    root = tmp_path / "generated"
    root.mkdir()
    for filename in policy["generated_export_artifacts"].values():
        (root / filename).write_bytes(b"x")
    (root / "unexpected.bin").write_bytes(b"x")
    with pytest.raises(bp.BirthProbeError, match="closed nine-file set"):
        admission.snapshot_generated_export(root, policy)


def test_nonpass_cannot_issue_registration_authority(tmp_path: Path) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    before = dict(admission._ISSUED_ADMISSIONS)  # noqa: SLF001
    with pytest.raises(admission.ArtifactRejected, match="did not pass"):
        _issue(snapshot, passed=False)
    assert admission._ISSUED_ADMISSIONS == before  # noqa: SLF001


def test_forged_admission_cannot_publish(tmp_path: Path) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    with pytest.raises(bp.BirthProbeError, match="was not issued"):
        admission.publish_successful_artifact_bundle(
            tmp_path / "successful_forged",
            {"schema": admission._ADMISSION_SCHEMA},  # noqa: SLF001
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )


def test_success_bundle_contains_model_and_complete_evidence(tmp_path: Path) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    measurement = _measurement(passed=True)
    issued = _issue(snapshot)
    output = tmp_path / "successful_run9"
    admission.publish_successful_artifact_bundle(
        output,
        issued,
        measurement,
        _observations(),
        bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
        snapshot,
    )
    assert (output / "SUCCESS.json").is_file()
    assert len(list((output / "model").iterdir())) == 9
    assert len(list((output / "wav").iterdir())) == 84
    assert len(list((output / "features").iterdir())) == 85
    manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == admission._MANIFEST_SCHEMA  # noqa: SLF001
    assert "SUCCESS.json" in manifest["files"]


def test_success_publisher_stages_outside_publication_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    public_root = tmp_path / "run9_public"
    public_root.mkdir()
    output = public_root / "successful_run9"
    sources: list[Path] = []
    real_replace = admission.os.replace

    def capture_replace(source: Path, target: Path) -> None:
        sources.append(Path(source).resolve())
        real_replace(source, target)

    monkeypatch.setattr(admission.os, "replace", capture_replace)
    admission.publish_successful_artifact_bundle(
        output,
        issued,
        _measurement(passed=True),
        _observations(),
        bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
        snapshot,
    )
    assert output.is_dir()
    assert len(sources) == 1
    staged = sources[0]
    assert staged.parent != public_root.resolve()
    assert public_root.resolve() not in staged.parents
    assert staged.parent == public_root.resolve().parent


def test_success_publisher_rejects_dangling_publication_leaf_symlink(
    tmp_path: Path,
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    public_root = tmp_path / "run9_public"
    public_root.mkdir()
    output = public_root / "successful_run9"
    redirected = tmp_path / "successful_redirect"
    output.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(bp.BirthProbeError, match="output path must not be a symlink"):
        admission.publish_successful_artifact_bundle(
            output,
            issued,
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )
    assert output.is_symlink()
    assert not redirected.exists()
    assert not (public_root / "SUCCESS.json").exists()


def test_success_publisher_rejects_symlinked_staging_before_bundle_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    public_root = tmp_path / "run9_public"
    public_root.mkdir()
    output = public_root / "successful_run9"
    redirected = tmp_path / "redirected-staging"
    redirected.symlink_to(public_root, target_is_directory=True)

    monkeypatch.setattr(
        admission.tempfile,
        "mkdtemp",
        lambda *args, **kwargs: str(redirected),
    )
    with pytest.raises(bp.BirthProbeError, match="must not be a symlink"):
        admission.publish_successful_artifact_bundle(
            output,
            issued,
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )
    assert not output.exists()
    assert not (public_root / "model").exists()
    assert not (public_root / "wav").exists()
    assert not (public_root / "features").exists()


def test_success_publisher_is_atomic_on_rename_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    output = tmp_path / "successful_atomic"

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(admission.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        admission.publish_successful_artifact_bundle(
            output,
            issued,
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".successful_atomic.build-*"))


@pytest.mark.parametrize("termination_type", [KeyboardInterrupt, SystemExit])
def test_success_publisher_cleans_staging_on_baseexception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    termination_type: type[BaseException],
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    output = tmp_path / "successful_interrupted"

    def interrupt(_source: Path, _target: Path) -> None:
        raise termination_type()

    monkeypatch.setattr(admission.os, "replace", interrupt)
    with pytest.raises(termination_type):
        admission.publish_successful_artifact_bundle(
            output,
            issued,
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".successful_interrupted.build-*"))


def test_success_publisher_rejects_tampered_measurement_after_issue(tmp_path: Path) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    measurement = _measurement(passed=True)
    issued = _issue(snapshot)
    measurement["founders"]["R9F-01"]["reference"]["wav_sha256"] = "0" * 64
    with pytest.raises(bp.BirthProbeError, match="evidence seal is invalid"):
        admission.publish_successful_artifact_bundle(
            tmp_path / "successful_tampered_measurement",
            issued,
            measurement,
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )


def test_success_publisher_rejects_observation_swap_after_pass(tmp_path: Path) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    observations = _observations()
    observations["R9F-01"]["reference"][0] = _observation("replacement")
    with pytest.raises(bp.BirthProbeError, match="does not bind the published observation"):
        admission.publish_successful_artifact_bundle(
            tmp_path / "successful_swapped_observation",
            issued,
            _measurement(passed=True),
            observations,
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )


def test_success_publisher_rejects_snapshot_byte_payload_swap_after_issue(
    tmp_path: Path,
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    filename = next(iter(snapshot.artifact_bytes))
    snapshot.artifact_bytes[filename] = b"post-evaluation-replacement"
    output = tmp_path / "successful_swapped_model_bytes"

    with pytest.raises(bp.BirthProbeError, match="byte payload diverges from its record"):
        admission.publish_successful_artifact_bundle(
            output,
            issued,
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )
    assert not output.exists()


def test_success_publisher_rejects_silently_corrupted_feature_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    original_write = Path.write_bytes

    def corrupt(path: Path, value: bytes) -> int:
        if path.name == "R9F-01_reference_00.bin":
            value = value[:-1]
        return original_write(path, value)

    monkeypatch.setattr(Path, "write_bytes", corrupt)
    output = tmp_path / "successful_corrupt_feature"
    with pytest.raises(bp.BirthProbeError, match="feature readback mismatch"):
        admission.publish_successful_artifact_bundle(
            output,
            issued,
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".successful_corrupt_feature.build-*"))


def test_success_publisher_rejects_corrupted_manifest_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _write_generated_export(tmp_path / "generated")
    issued = _issue(snapshot)
    original_write = Path.write_bytes

    def corrupt(path: Path, value: bytes) -> int:
        if path.name == "artifact_manifest.json":
            value = b"{}\n"
        return original_write(path, value)

    monkeypatch.setattr(Path, "write_bytes", corrupt)
    output = tmp_path / "successful_corrupt_manifest"
    with pytest.raises(bp.BirthProbeError, match="manifest failed readback"):
        admission.publish_successful_artifact_bundle(
            output,
            issued,
            _measurement(passed=True),
            _observations(),
            bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0])),
            snapshot,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".successful_corrupt_manifest.build-*"))


def test_c1_zero_profile_hook_is_identity_and_single_use() -> None:
    _, sham = bp._control_profiles("R9F-01")  # noqa: SLF001
    record: dict[str, object] = {}
    hook = bp._run9_zero_controlprofile_sham_duration_hook(  # noqa: SLF001
        sham.to_dict(),
        record,
    )
    predicted = [1, 2, 3]
    assert record == {}
    assert getattr(hook, "run9_consumed") is False
    assert hook(predicted, {}) == predicted
    assert getattr(hook, "run9_consumed") is True
    assert record["run9_control_profile_attachment"] == {
        "status": "CONSUMED_INERT_ZERO_PROFILE",
        "voice_id": "R9F-01",
        "revision": "r_sham",
        "profile_id": sham.profile_id,
    }
    with pytest.raises(bp.BirthProbeError, match="more than once"):
        hook(predicted, {})


def _c1_renderer(gate: object) -> bp.GateSynthRenderer:
    renderer = object.__new__(bp.GateSynthRenderer)
    renderer._notes = [bp._ProbeNote(mora="あ", midi=60, duration_beats=1.0)]  # noqa: SLF001
    renderer._tempo = 120.0
    renderer._model_bytes = {}
    renderer._variance_phonemes = {}
    renderer._acoustic_phonemes = {}
    renderer._embeddings = {"R9F-01": np.zeros(384, dtype=np.float32)}

    class FakeSoundFile:
        @staticmethod
        def write(path: Path, waveform: np.ndarray, sample_rate: int, **kwargs: object) -> None:
            del waveform, sample_rate, kwargs
            path.write_bytes(b"c1-hook-wav")

        @staticmethod
        def read(file_object: object, **kwargs: object) -> tuple[np.ndarray, int]:
            del file_object, kwargs
            return np.asarray([0.1, -0.1], dtype=np.float64), 44_100

    renderer._gate = gate
    renderer._sf = FakeSoundFile()
    return renderer


def test_c1_renderer_rejects_record_only_attachment_without_hook_invocation() -> None:
    class FakeGate:
        @staticmethod
        def run_pipeline(*args: object, **kwargs: object) -> np.ndarray:
            del kwargs
            record = args[6]
            record["seed"] = bp._RUNTIME_SEED  # noqa: SLF001
            return np.asarray([0.25, -0.25], dtype=np.float32)

    _, sham = bp._control_profiles("R9F-01")  # noqa: SLF001
    with pytest.raises(bp.BirthProbeError, match="did not invoke"):
        _c1_renderer(FakeGate())("R9F-01", "c1", 0, sham.to_dict())


def test_c1_renderer_accepts_actual_single_inert_hook_consumption() -> None:
    class FakeGate:
        @staticmethod
        def run_pipeline(*args: object, **kwargs: object) -> np.ndarray:
            record = args[6]
            hook = kwargs["final_phone_dur_override"]
            assert hook([5, 8], {"real_phones": ["a", "i"]}) == [5, 8]
            record["seed"] = bp._RUNTIME_SEED  # noqa: SLF001
            return np.asarray([0.25, -0.25], dtype=np.float32)

    _, sham = bp._control_profiles("R9F-01")  # noqa: SLF001
    assert (
        _c1_renderer(FakeGate())("R9F-01", "c1", 0, sham.to_dict())
        == b"c1-hook-wav"
    )


def test_legacy_rev06_cli_remains_disabled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = bp.main(
        [
            "--acoustic-dir",
            str(tmp_path),
            "--canon-model-dir",
            str(tmp_path),
            "--vocoder-dir",
            str(tmp_path),
            "--pjs-corpus-root",
            str(tmp_path),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert result == 2
    assert "success-only admission harness" in capsys.readouterr().err
