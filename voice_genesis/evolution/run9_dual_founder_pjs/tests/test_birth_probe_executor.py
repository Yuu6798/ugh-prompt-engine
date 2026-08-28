"""RUN9 rev 0.6 Birth Probe executor/consumer tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import birth_probe_executor as bp  # noqa: E402


FOUNDERS = ("R9F-01", "R9F-02")


def _observation(wav: bytes, vector: list[float], *, profile: str | None = None) -> bp.RenderObservation:
    return bp.RenderObservation.build(
        wav,
        bp.FeatureArtifact.from_vector(np.asarray(vector, dtype=np.float64)),
        control_profile_id=profile,
    )


def _evidence(*, count: int = 20):
    references = {
        "R9F-01": _observation(b"founder-1", [0.0, 1.0]),
        "R9F-02": _observation(b"founder-2", [1.0, 0.0]),
    }
    profile_ids = {
        founder: tuple(profile.profile_id for profile in bp._control_profiles(founder))  # noqa: SLF001
        for founder in FOUNDERS
    }
    c0 = {
        founder: [
            _observation(
                references[founder].wav,
                references[founder].feature.vector.tolist(),
                profile=profile_ids[founder][0],
            )
        ]
        * count
        for founder in FOUNDERS
    }
    c1 = {
        founder: [
            _observation(
                references[founder].wav,
                references[founder].feature.vector.tolist(),
                profile=profile_ids[founder][1],
            )
        ]
        * count
        for founder in FOUNDERS
    }
    positive = dict(references)
    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))
    return references, c0, c1, positive, pjs


def _dependency_rows() -> list[dict[str, object]]:
    versions = {
        "python": "3.11.15",
        "numpy": "2.4.6",
        "librosa": "0.11.0",
        "numba": "0.66.0",
        "scipy": "1.17.1",
        "soundfile": "0.14.0",
        "PyYAML": "6.0.1",
        "pyloudnorm": "0.2.0",
        "onnxruntime": "1.29.0",
    }
    return [
        {
            "package": package,
            "pin_version": version,
            "observed_version": version,
            "status": "MATCH",
        }
        for package, version in versions.items()
    ]


def test_happy_path_is_complete_deterministic_and_passes() -> None:
    references, c0, c1, positive, pjs = _evidence()
    first = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    second = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    assert bp.canonical_json_bytes(first) == bp.canonical_json_bytes(second)
    assert first["identity_establishment"] == {
        "birth_outcome": "ESTABLISHED",
        "outcome_detail": "ESTABLISHED_BY_MACHINE_FEATURE",
        "outcome_detail_all_applicable_keys": [],
    }
    assert first["d12"] == pytest.approx(2**0.5)
    assert first["audit"] == {
        "complete": True,
        "stops": [],
        "completion_detail": None,
        "completion_outcome": None,
    }
    assert first["overall_pass"] is True
    assert first["learning_progression_allowed"] is True


def test_c1_priority_records_all_applicable_keys() -> None:
    references, c0, c1, positive, pjs = _evidence()
    _, sham = bp._control_profiles("R9F-01")  # noqa: SLF001
    c1["R9F-01"][0] = _observation(
        b"different-wav", [9.0, 9.0], profile=sham.profile_id
    )
    result = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    stop = result["audit"]["stops"][0]
    assert stop["outcome"] == "DETERMINISM_CONTRACT_BROKEN"
    assert stop["applicable_keys"] == ["on_wav_byte_mismatch", "on_nonzero"]
    assert result["identity_establishment"]["birth_outcome"] == "ESTABLISHED"
    assert result["overall_pass"] is False


def test_c1_same_wav_nonzero_feature_distance_is_sham_effect() -> None:
    references, c0, c1, positive, pjs = _evidence()
    _, sham = bp._control_profiles("R9F-01")  # noqa: SLF001
    c1["R9F-01"][0] = _observation(
        references["R9F-01"].wav,
        [0.5, 1.0],
        profile=sham.profile_id,
    )
    result = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    matching = [
        stop
        for stop in result["audit"]["stops"]
        if stop["founder_id"] == "R9F-01" and stop["cell"] == "c1" and stop["take_index"] == 0
    ]
    assert matching[0]["outcome"] == "C1_SHAM_EFFECT_DETECTED"
    assert matching[0]["applicable_keys"] == ["on_nonzero"]


def test_matching_wav_but_different_serialized_feature_is_implementation_failure() -> None:
    references, c0, c1, positive, pjs = _evidence()
    # Euclidean distance treats +0/-0 as equal, but the frozen exact-feature
    # audit must still observe the byte-level signed-zero difference.
    references["R9F-01"] = _observation(b"same", [0.0, 1.0])
    replay, _ = bp._control_profiles("R9F-01")  # noqa: SLF001
    matching = _observation(b"same", [0.0, 1.0], profile=replay.profile_id)
    c0["R9F-01"] = [
        _observation(b"same", [-0.0, 1.0], profile=replay.profile_id)
    ] + [matching] * 19
    result = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    assert result["audit"]["stops"][0]["outcome"] == "IMPLEMENTATION_FAILURE"
    assert result["audit"]["stops"][0]["applicable_keys"] == [
        "feature_computation_mismatch_with_matching_render"
    ]


@pytest.mark.parametrize(("cell", "profile"), (("c0", "wrong-replay"), ("c1", "wrong-sham")))
def test_control_cells_require_the_exact_derived_profile(cell: str, profile: str) -> None:
    references, c0, c1, positive, pjs = _evidence()
    takes = c0 if cell == "c0" else c1
    takes["R9F-01"][0] = _observation(
        references["R9F-01"].wav,
        references["R9F-01"].feature.vector.tolist(),
        profile=profile,
    )
    result = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    matching = [
        stop
        for stop in result["audit"]["stops"]
        if stop["founder_id"] == "R9F-01" and stop["cell"] == cell and stop["take_index"] == 0
    ]
    assert matching[0]["outcome"] == "IMPLEMENTATION_FAILURE"
    assert result["overall_pass"] is False


def test_incomplete_evidence_is_closed_world_non_pass() -> None:
    references, c0, c1, positive, pjs = _evidence(count=19)
    result = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    assert result["audit"]["complete"] is False
    assert result["audit"]["completion_detail"] == "IDENTITY_PROTOCOL_AUDIT_INCOMPLETE"
    assert result["overall_pass"] is False
    assert result["learning_progression_allowed"] is False


def test_invalid_d12_has_frozen_priority_over_collapses() -> None:
    references, c0, c1, positive, pjs = _evidence()
    huge = bp.FeatureArtifact.from_vector(np.asarray([1e308, -1e308]))
    references["R9F-02"] = bp.RenderObservation.build(b"huge", huge)
    c0["R9F-02"] = [references["R9F-02"]] * 20
    c1["R9F-02"] = [references["R9F-02"]] * 20
    positive["R9F-02"] = references["R9F-02"]
    result = bp.evaluate_birth_gate(
        references=references,
        c0_takes=c0,
        c1_takes=c1,
        positive_references=positive,
        pjs_reference=pjs,
    )
    assert result["d12"] is None
    assert result["identity_establishment"]["outcome_detail"] == (
        "IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_INVALID_OR_NONFINITE_D12"
    )


def test_execute_uses_fixed_order_and_real_zero_controlprofile_sham() -> None:
    calls: list[tuple[str, str, int, object]] = []

    def renderer(founder: str, condition: str, index: int, profile):
        calls.append((founder, condition, index, profile))
        return founder.encode("ascii")

    def extractor(wav: bytes) -> bp.FeatureArtifact:
        value = 0.0 if wav == b"R9F-01" else 1.0
        return bp.FeatureArtifact.from_vector(np.asarray([value, 1.0 - value]))

    pjs = bp.FeatureArtifact.from_vector(np.asarray([2.0, 2.0]))
    result, _ = bp.execute_birth_gate(
        renderer=renderer,
        extractor=extractor,
        pjs_reference=pjs,
        expected_takes=2,
    )
    assert len(calls) == 12
    assert [(condition, index) for _, condition, index, _ in calls[:6]] == [
        ("reference", 0),
        ("c0", 0),
        ("c0", 1),
        ("c1", 0),
        ("c1", 1),
        ("positive_reference", 0),
    ]
    c1_profiles = [profile for _, condition, _, profile in calls if condition == "c1"]
    c0_profiles = [profile for _, condition, _, profile in calls if condition == "c0"]
    assert all(profile["branch"] == "CONTROL" for profile in c0_profiles)
    assert all(profile["revision"] == "replay" for profile in c0_profiles)
    assert all(not any(profile["partitions"].values()) for profile in c0_profiles)
    assert all(profile["branch"] == "CONTROL" for profile in c1_profiles)
    assert all(profile["revision"] == "r_sham" for profile in c1_profiles)
    assert all(not any(profile["partitions"].values()) for profile in c1_profiles)
    assert result["overall_pass"] is True


def test_acoustic_pin_mismatch_prevents_renderer_use(tmp_path: Path) -> None:
    acoustic = tmp_path / "acoustic.onnx"
    acoustic.write_bytes(b"wrong")
    with pytest.raises(bp.BirthProbeError, match="zero renders admitted"):
        bp.verify_exact_acoustic(acoustic, "0" * 64)


def test_pjs_pin_is_verified_before_feature_extraction(tmp_path: Path) -> None:
    song = tmp_path / "pjs001"
    song.mkdir()
    lab = song / "pjs001.lab"
    wav = song / "pjs001_song.wav"
    lab.write_bytes(b"lab")
    wav.write_bytes(b"wav")
    called = False

    def extractor(_: bytes) -> bp.FeatureArtifact:
        nonlocal called
        called = True
        return bp.FeatureArtifact.from_vector(np.asarray([1.0]))

    with pytest.raises(bp.BirthProbeError, match="expanded corpus identity mismatch"):
        bp.build_pjs_reference(tmp_path, extractor, expected_corpus_sha256="0" * 64)
    assert called is False


def test_publish_is_atomic_and_refuses_overwrite(tmp_path: Path) -> None:
    references, c0, c1, positive, pjs = _evidence(count=1)
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
        for founder in FOUNDERS
    }
    output = tmp_path / "evidence"
    bp.publish_evidence_bundle(output, result, observations, pjs)
    assert (output / "birth_gate_evidence.json").is_file()
    assert (output / "artifact_manifest.json").is_file()
    with pytest.raises(bp.BirthProbeError, match="refusing to overwrite"):
        bp.publish_evidence_bundle(output, result, observations, pjs)


def test_publish_failure_injection_leaves_no_partial_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    references, c0, c1, positive, pjs = _evidence(count=1)
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
        for founder in FOUNDERS
    }
    original_write_bytes = Path.write_bytes

    def injected_write(path: Path, data: bytes) -> int:
        if path.name == "birth_gate_evidence.json":
            raise OSError("injected publication failure")
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", injected_write)
    output = tmp_path / "evidence"
    with pytest.raises(OSError, match="injected publication failure"):
        bp.publish_evidence_bundle(output, result, observations, pjs)
    assert not output.exists()
    assert list(tmp_path.glob(".evidence.build-*")) == []


@pytest.mark.parametrize("termination_type", [KeyboardInterrupt, SystemExit])
def test_publish_cleanup_covers_baseexception_termination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    termination_type: type[BaseException],
) -> None:
    references, c0, c1, positive, pjs = _evidence(count=1)
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
        for founder in FOUNDERS
    }
    original_write_bytes = Path.write_bytes

    def injected_write(path: Path, data: bytes) -> int:
        if path.name == "birth_gate_evidence.json":
            raise termination_type()
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", injected_write)
    output = tmp_path / "evidence"
    with pytest.raises(termination_type):
        bp.publish_evidence_bundle(output, result, observations, pjs)
    assert not output.exists()
    assert list(tmp_path.glob(".evidence.build-*")) == []


@pytest.mark.parametrize(
    "changed_key",
    [
        "run9_contract_sha256",
        "identity_decision_protocol_sha256",
        "probe_manifest_sha256",
        "speaker_map_manifest_sha256",
        "reexport_manifest_sha256",
        "backbone_runtime_bundle_sha256",
        "dependency_pins_manifest_sha256",
        "executor_sha256",
        "run9_schema_sha256",
        "run9_controlprofile_sha256",
        "gate_synth_sha256",
    ],
)
def test_provenance_snapshot_rejects_post_snapshot_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_key: str,
) -> None:
    keys = [
        "run9_contract_sha256",
        "identity_decision_protocol_sha256",
        "probe_manifest_sha256",
        "speaker_map_manifest_sha256",
        "reexport_manifest_sha256",
        "backbone_runtime_bundle_sha256",
        "dependency_pins_manifest_sha256",
        "executor_sha256",
        "run9_schema_sha256",
        "run9_controlprofile_sha256",
        "gate_synth_sha256",
    ]
    paths = {key: tmp_path / f"{index}.bin" for index, key in enumerate(keys)}
    for index, path in enumerate(paths.values()):
        path.write_bytes(f"before-{index}".encode("ascii"))
    monkeypatch.setattr(bp, "_provenance_input_paths", lambda: paths)
    snapshot = bp._snapshot_provenance_inputs()
    paths[changed_key].write_bytes(b"after")
    with pytest.raises(bp.BirthProbeError, match="provenance input changed during execution"):
        bp._verify_provenance_inputs_unchanged(snapshot)


def test_provenance_snapshot_rejects_executor_changed_since_module_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, on_disk_sha = bp._read_once(bp._EXECUTOR_PATH, label="test executor provenance")
    assert on_disk_sha == bp._EXECUTOR_LOAD_SHA256
    monkeypatch.setattr(bp, "_EXECUTOR_LOAD_SHA256", "0" * 64)
    monkeypatch.setitem(bp._LOAD_TIME_PROVENANCE_SHA256, "executor_sha256", "0" * 64)
    with pytest.raises(bp.BirthProbeError, match="executor_sha256 changed after executor module load"):
        bp._snapshot_provenance_inputs()


def test_executor_digest_is_captured_before_numpy_import() -> None:
    source = Path(bp.__file__).read_text(encoding="utf-8")
    digest_capture = "_EXECUTOR_LOAD_SHA256 = hashlib.sha256(_EXECUTOR_PATH.read_bytes()).hexdigest()"
    numpy_import = 'np = importlib.import_module("numpy")'
    assert source.index(digest_capture) < source.index(numpy_import)


@pytest.mark.parametrize(
    "changed_key",
    ["run9_schema_sha256", "run9_controlprofile_sha256", "gate_synth_sha256"],
)
def test_provenance_snapshot_rejects_helper_changed_since_executor_load(
    monkeypatch: pytest.MonkeyPatch,
    changed_key: str,
) -> None:
    path = bp._LOAD_TIME_PROVENANCE_PATHS[changed_key]
    _, on_disk_sha = bp._read_once(path, label=f"test {changed_key}")
    assert on_disk_sha == bp._LOAD_TIME_PROVENANCE_SHA256[changed_key]
    monkeypatch.setitem(bp._LOAD_TIME_PROVENANCE_SHA256, changed_key, "0" * 64)
    with pytest.raises(bp.BirthProbeError, match=f"{changed_key} changed after executor module load"):
        bp._snapshot_provenance_inputs()


@pytest.mark.parametrize("module_name", list(bp._HELPER_MODULE_NAMES.values()))
def test_main_provenance_guard_rejects_preloaded_repo_helper(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    for helper_name in bp._HELPER_MODULE_NAMES.values():
        monkeypatch.delitem(sys.modules, helper_name, raising=False)
    monkeypatch.setitem(sys.modules, module_name, object())
    with pytest.raises(bp.BirthProbeError, match="repo helper modules were already loaded"):
        bp._assert_helper_modules_not_preloaded()


def test_direct_dependency_pins_are_read_from_recorded_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "dependency_pins_manifest.json"
    manifest_path.write_text(
        json.dumps({"python_dependency_pins": _dependency_rows()}), encoding="utf-8"
    )
    monkeypatch.setattr(bp, "_DIRECT_DEPENDENCY_MANIFEST_PATH", manifest_path)
    assert bp._load_direct_dependency_pin_versions() == {
        "numpy": "2.4.6",
        "scipy": "1.17.1",
        "soundfile": "0.14.0",
        "PyYAML": "6.0.1",
        "onnxruntime": "1.29.0",
        "pyworld": "0.3.5",
    }


def test_direct_dependency_pin_record_must_be_verified_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = _dependency_rows()
    numpy_row = next(row for row in rows if row["package"] == "numpy")
    numpy_row["observed_version"] = "0.0.0"
    manifest_path = tmp_path / "dependency_pins_manifest.json"
    manifest_path.write_text(json.dumps({"python_dependency_pins": rows}), encoding="utf-8")
    monkeypatch.setattr(bp, "_DIRECT_DEPENDENCY_MANIFEST_PATH", manifest_path)
    with pytest.raises(bp.BirthProbeError, match="not a verified MATCH for numpy"):
        bp._load_direct_dependency_pin_versions()


def test_execution_environment_validates_direct_dependency_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "numpy": "2.4.6",
        "scipy": "1.17.1",
        "soundfile": "0.14.0",
        "PyYAML": "6.0.1",
        "onnxruntime": "1.29.0",
        "pyworld": "0.3.5",
    }
    modules = {
        "numpy": SimpleNamespace(__version__="2.4.6"),
        "scipy": SimpleNamespace(__version__="1.17.1"),
        "soundfile": SimpleNamespace(__version__="0.14.0"),
        "yaml": SimpleNamespace(__version__="6.0.1"),
        "onnxruntime": SimpleNamespace(
            __version__="1.29.0",
            get_available_providers=lambda: ["CPUExecutionProvider"],
        ),
        "pyworld": SimpleNamespace(__version__="0.3.5"),
    }
    monkeypatch.setattr(bp, "np", modules["numpy"])
    monkeypatch.setattr(bp.importlib, "import_module", lambda name: modules[name])
    monkeypatch.setattr(bp.importlib_metadata, "version", lambda package: expected[package])
    os_release = bp.platform.freedesktop_os_release()
    pretty_os = os_release.get("PRETTY_NAME", "")
    os_match = bp.re.match(r"^(Ubuntu\s+\d+\.\d+(?:\.\d+)?)", pretty_os)
    normalized_os = os_match.group(1) if os_match is not None else pretty_os
    profile = {
        "identity_semantics": {
            "runtime": {
                "architecture": bp.platform.machine(),
                "onnxruntime": "1.29.0",
                "os": normalized_os,
                "python": bp.platform.python_version(),
                "selected_execution_provider": "CPUExecutionProvider",
            }
        }
    }
    assert bp._validate_execution_environment(profile, expected) == {
        "python": bp.platform.python_version(),
        **expected,
    }


def test_execution_environment_rejects_distribution_version_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "numpy": "2.4.6",
        "scipy": "1.17.1",
        "soundfile": "0.14.0",
        "PyYAML": "6.0.1",
        "onnxruntime": "1.29.0",
        "pyworld": "0.3.5",
    }
    modules = {
        "numpy": SimpleNamespace(__version__="2.4.6"),
        "scipy": SimpleNamespace(__version__="1.17.1"),
        "soundfile": SimpleNamespace(__version__="0.14.0"),
        "yaml": SimpleNamespace(__version__="6.0.1"),
        "onnxruntime": SimpleNamespace(
            __version__="1.29.0",
            get_available_providers=lambda: ["CPUExecutionProvider"],
        ),
        "pyworld": SimpleNamespace(__version__="0.3.5"),
    }
    monkeypatch.setattr(bp, "np", modules["numpy"])
    monkeypatch.setattr(bp.importlib, "import_module", lambda name: modules[name])
    monkeypatch.setattr(
        bp.importlib_metadata,
        "version",
        lambda package: "9.9.9" if package == "scipy" else expected[package],
    )
    profile = {"identity_semantics": {"runtime": {}}}
    with pytest.raises(bp.BirthProbeError, match="runtime dependency version mismatch for scipy"):
        bp._validate_execution_environment(profile, expected)


def test_gate_synth_renderer_adapter_matches_runtime_note_and_tempo_contract() -> None:
    renderer = object.__new__(bp.GateSynthRenderer)
    renderer._notes = [bp._ProbeNote(mora="あ", midi=60, duration_beats=1.5)]
    renderer._tempo = 120.0
    renderer._model_bytes = {}
    renderer._variance_phonemes = {}
    renderer._acoustic_phonemes = {}
    renderer._embeddings = {"R9F-01": np.zeros(384, dtype=np.float32)}

    class FakeGate:
        @staticmethod
        def run_pipeline(
            notes,
            beats_to_seconds,
            tempo_bpm,
            model_bytes,
            variance_phonemes,
            acoustic_phonemes,
            record,
            **kwargs,
        ):
            del model_bytes, variance_phonemes, acoustic_phonemes, kwargs
            note = notes[0]
            assert note.mora == "あ"
            assert note.midi == 60
            assert beats_to_seconds(note.duration_beats, tempo_bpm) == pytest.approx(0.75)
            record["seed"] = bp._RUNTIME_SEED
            return np.asarray([0.25, -0.25], dtype=np.float32)

    class FakeSoundFile:
        @staticmethod
        def write(path, waveform, sample_rate, *, subtype, format):
            del waveform, sample_rate, subtype, format
            path.write_bytes(b"adapter-wav")

        @staticmethod
        def read(file_object, *, dtype, always_2d):
            del file_object, dtype, always_2d
            return np.asarray([0.1, -0.1], dtype=np.float64), 44_100

    renderer._gate = FakeGate()
    renderer._sf = FakeSoundFile()
    assert renderer("R9F-01", "reference", 0, None) == b"adapter-wav"


def test_unknown_or_missing_founder_is_rejected() -> None:
    references, c0, c1, positive, pjs = _evidence()
    del c0["R9F-02"]
    with pytest.raises(bp.BirthProbeError, match="exactly"):
        bp.evaluate_birth_gate(
            references=references,
            c0_takes=c0,
            c1_takes=c1,
            positive_references=positive,
            pjs_reference=pjs,
        )
