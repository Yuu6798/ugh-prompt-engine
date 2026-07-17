from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from svp_rpe.arrange.capabilities import InputCapabilityProfile
from svp_rpe.arrange.contract import (
    ContractAnchor,
    ContractInputs,
    InputHash,
    PreservationContract,
)
from svp_rpe.arrange.identity import IdentityAnchor, IdentityManifest
from svp_rpe.arrange.bundle import compute_content_digest
from svp_rpe.arrange.package import (
    PERFORMANCE_PACKAGE_FILENAME,
    CompilationReport,
    PackageCompilationError,
    PerformancePackage,
    build_performance_package,
    compile_performance_package,
    detect_compiler_git_commit,
    detect_compiler_package_version,
)
from svp_rpe.compose.device_profile import DeviceProfile, load_device_profile
from svp_rpe.compose.models import CompositionScore
from svp_rpe.compose.prompt_renderer import ExternalPromptAdapter
from svp_rpe.cli import app

SCORE_PATH = Path("examples/composition/midnight_signal/composition_score.yaml")
SUNO_PROFILE_PATH = Path("config/capability_profiles/suno.yaml")
MUSICGEN_PROFILE_PATH = Path("config/capability_profiles/musicgen.yaml")

MANIFEST_SHA256 = "a" * 64
SPEC_SHA256 = "b" * 64
CONTRACT_SHA256 = "c" * 64
PROFILE_SHA256 = "d" * 64
DERIVED_SHA256 = "e" * 64


def _score(*, backend: str = "suno") -> CompositionScore:
    data = yaml.safe_load(SCORE_PATH.read_text(encoding="utf-8"))
    data["rendering"]["target_backend"] = backend
    return CompositionScore.model_validate(data)


def _anchor(
    anchor_id: str,
    domain: str,
    artifact_type: str,
    *,
    required: bool = True,
    format_version: str | None = None,
) -> IdentityAnchor:
    extension = {
        "lyrics_text": "txt",
        "midi_clip": "mid",
        "note_events_json": "json",
        "chord_sequence_json": "json",
        "audio_excerpt": "wav",
        "section_map": "json",
    }[artifact_type]
    media_type = {
        "lyrics_text": "text/plain",
        "midi_clip": "audio/midi",
        "note_events_json": "application/json",
        "chord_sequence_json": "application/json",
        "audio_excerpt": "audio/wav",
        "section_map": "application/json",
    }[artifact_type]
    return IdentityAnchor(
        id=anchor_id,
        domain=domain,
        artifact=f"{anchor_id}.{extension}",
        artifact_type=artifact_type,
        media_type=media_type,
        format_version=format_version,
        sha256=hashlib.sha256(anchor_id.encode()).hexdigest(),
        required=required,
    )


def _manifest(anchors: list[IdentityAnchor]) -> IdentityManifest:
    return IdentityManifest.model_validate(
        {
            "schema_version": "identity-manifest/0.1",
            "meta": {"work_id": "package-test", "version": "1"},
            "source": {
                "locator": "source.wav",
                "sha256": "f" * 64,
                "rights_basis": "original",
            },
            "anchors": [anchor.model_dump(mode="json") for anchor in anchors],
        }
    )


def _contract(
    manifest: IdentityManifest,
    policies: dict[str, tuple[str, list[str]]],
    *,
    tolerance_profiles: dict[str, str] | None = None,
) -> PreservationContract:
    anchors: list[ContractAnchor] = []
    for anchor in manifest.anchors:
        policy = policies.get(anchor.id)
        if policy is None:
            continue
        mode, allow = policy
        anchors.append(
            ContractAnchor(
                anchor_id=anchor.id,
                domain=anchor.domain,
                mode=mode,
                allow=allow,
                tolerance_profile=(tolerance_profiles or {}).get(anchor.id),
                artifact=anchor.artifact,
                artifact_sha256=anchor.sha256,
            )
        )
    return PreservationContract(
        work_id=manifest.meta.work_id,
        inputs=ContractInputs(
            identity_manifest=InputHash(sha256=MANIFEST_SHA256),
            arrangement_spec=InputHash(sha256=SPEC_SHA256),
        ),
        anchors=anchors,
    )


def _profile(
    *,
    generator: str = "suno",
    style_prompt: str = "supported",
    lyrics_text: str = "supported",
    section_tags: str = "experimental",
    reference_audio: str = "unknown",
    symbolic_melody: str = "unsupported",
    midi: str = "unsupported",
) -> InputCapabilityProfile:
    supports = {
        "style_prompt": style_prompt,
        "lyrics_text": lyrics_text,
        "section_tags": section_tags,
        "reference_audio": reference_audio,
        "symbolic_melody": symbolic_melody,
        "midi": midi,
    }
    return InputCapabilityProfile.model_validate(
        {
            "schema_version": "input-capability/0.1",
            "generator": generator,
            "profile_version": "1",
            "input_channels": {
                channel: {"support": support} for channel, support in supports.items()
            },
        }
    )


def _build(
    manifest: IdentityManifest,
    contract: PreservationContract,
    profile: InputCapabilityProfile,
    *,
    strict: bool = False,
    score: CompositionScore | None = None,
    device_profile: DeviceProfile | None = None,
    artifact_base_locator: str = ".",
    compiler_package_version: str | None = None,
    compiler_git_commit: str | None = None,
):
    effective_score = score or _score()
    resolved_device_profile = (
        device_profile
        if device_profile is not None
        else load_device_profile(profile.generator)
    )
    return build_performance_package(
        manifest,
        contract,
        profile,
        effective_score,
        device_profile=resolved_device_profile,
        artifact_base_locator=artifact_base_locator,
        manifest_sha256=MANIFEST_SHA256,
        contract_sha256=CONTRACT_SHA256,
        profile_sha256=PROFILE_SHA256,
        derived_score_sha256=DERIVED_SHA256,
        strict=strict,
        compiler_package_version=compiler_package_version,
        compiler_git_commit=compiler_git_commit,
    )


def test_builder_separates_all_delivery_states_and_future_states() -> None:
    anchors = [
        _anchor("lyrics", "lyrics", "lyrics_text"),
        _anchor(
            "sections",
            "structure",
            "section_map",
            format_version="section-map/1",
        ),
        _anchor("midi", "melody", "midi_clip"),
        _anchor("audio", "motif", "audio_excerpt"),
        _anchor("chords", "harmony", "chord_sequence_json"),
        _anchor("optional", "lyrics", "lyrics_text", required=False),
    ]
    manifest = _manifest(anchors)
    contract = _contract(
        manifest,
        {
            "lyrics": ("hard", []),
            "sections": ("elastic", ["intro_extension"]),
            "midi": ("hard", []),
            "audio": ("free", []),
            "chords": ("hard", []),
        },
        tolerance_profiles={"sections": "loose-v1"},
    )

    compiled = _build(manifest, contract, _profile())
    statuses = {status.anchor_id: status for status in compiled.package.anchor_statuses}

    assert statuses["lyrics"].delivery.model_dump() == {
        "channel": "lyrics_text",
        "status": "delivered",
    }
    assert statuses["sections"].delivery.model_dump() == {
        "channel": "section_tags",
        "status": "experimental",
    }
    assert statuses["midi"].delivery.model_dump() == {
        "channel": None,
        "status": "unsupported",
    }
    assert statuses["audio"].delivery.model_dump() == {
        "channel": None,
        "status": "unknown",
    }
    assert statuses["chords"].delivery.model_dump() == {
        "channel": None,
        "status": "unknown",
    }
    assert statuses["optional"].requested_mode is None
    assert statuses["optional"].allow == []
    assert statuses["optional"].tolerance_profile is None
    assert statuses["optional"].delivery.status == "not_requested"
    assert statuses["sections"].requested_mode == "elastic"
    assert statuses["sections"].allow == ["intro_extension"]
    assert statuses["sections"].tolerance_profile == "loose-v1"
    assert all(status.control.status == "unknown" for status in statuses.values())
    assert all(status.control.evidence is None for status in statuses.values())
    assert all(
        status.observation.status == "not_observed" for status in statuses.values()
    )
    assert set(compiled.package.channel_artifacts) == {"lyrics_text", "section_tags"}
    assert [
        reference.anchor_id
        for references in compiled.package.channel_artifacts.values()
        for reference in references
    ] == ["lyrics", "sections"]
    assert (
        compiled.package.channel_artifacts["section_tags"][0].format_version
        == "section-map/1"
    )
    assert compiled.package.channel_artifacts[
        "section_tags"
    ][0].artifact_base.model_dump() == {
        "kind": "identity_manifest_directory",
        "relative_to": "performance_package_directory",
        "locator": ".",
    }
    assert (
        json.loads(compiled.package_json)["channel_artifacts"]["section_tags"][0][
            "format_version"
        ]
        == "section-map/1"
    )
    assert (
        json.loads(compiled.package_json)["channel_artifacts"]["section_tags"][0][
            "artifact_base"
        ]
        == {
            "kind": "identity_manifest_directory",
            "relative_to": "performance_package_directory",
            "locator": ".",
        }
    )
    serialized_statuses = {
        status["anchor_id"]: status
        for status in json.loads(compiled.package_json)["anchor_statuses"]
    }
    assert serialized_statuses["sections"]["allow"] == ["intro_extension"]
    assert serialized_statuses["sections"]["tolerance_profile"] == "loose-v1"
    assert compiled.report.warnings[:4] == [
        "anchor 'sections': channel 'section_tags' is experimental",
        "anchor 'midi': channel 'midi' is unsupported",
        "anchor 'audio': channel 'reference_audio' is unknown",
        "anchor 'chords': no channel mapping defined",
    ]


def test_strict_lists_every_hard_unsupported_or_unknown_anchor() -> None:
    manifest = _manifest(
        [
            _anchor("sections", "structure", "section_map"),
            _anchor("midi", "melody", "midi_clip"),
            _anchor("audio", "motif", "audio_excerpt"),
            _anchor("chords", "harmony", "chord_sequence_json"),
        ]
    )
    contract = _contract(
        manifest,
        {
            anchor.id: ("hard", [])
            for anchor in manifest.anchors
        },
    )

    with pytest.raises(PackageCompilationError) as exc_info:
        _build(manifest, contract, _profile(), strict=True)

    message = str(exc_info.value)
    assert "midi (unsupported)" in message
    assert "audio (unknown)" in message
    assert "chords (unknown)" in message
    assert "sections" not in message
    assert message.index("midi") < message.index("audio") < message.index("chords")


def test_strict_accepts_hard_experimental_delivery() -> None:
    manifest = _manifest([_anchor("sections", "structure", "section_map")])
    contract = _contract(manifest, {"sections": ("hard", [])})

    compiled = _build(manifest, contract, _profile(), strict=True)

    assert compiled.package.anchor_statuses[0].delivery.status == "experimental"
    assert compiled.report.mode == "strict"


@pytest.mark.parametrize("artifact_type", ["midi_clip", "note_events_json"])
def test_d7_unsupported_symbolic_anchor_is_not_rewritten_as_prompt_text(
    artifact_type: str,
) -> None:
    anchor = _anchor("melody", "melody", artifact_type)
    manifest = _manifest([anchor])
    contract = _contract(manifest, {"melody": ("hard", [])})
    score = _score()

    compiled = _build(manifest, contract, _profile(), score=score)

    assert compiled.package.prompt is not None
    assert compiled.package.prompt.text == ExternalPromptAdapter().render(score).text
    assert compiled.package.channel_artifacts == {}
    assert compiled.package.anchor_statuses[0].delivery.status == "unsupported"


def test_prompt_and_section_tags_follow_capability_channels() -> None:
    manifest = _manifest([])
    contract = _contract(manifest, {})

    no_section = _build(
        manifest,
        contract,
        _profile(section_tags="unsupported"),
    )
    assert no_section.package.prompt is not None
    assert no_section.package.prompt.section_tags is None
    assert "section_tags" not in json.loads(no_section.package_json)["prompt"]
    assert no_section.report.warnings == [
        "section_tags channel is unsupported; payload omitted"
    ]

    no_prompt = _build(
        manifest,
        contract,
        _profile(style_prompt="unknown", section_tags="supported"),
    )
    assert no_prompt.package.prompt is None
    assert "prompt" not in json.loads(no_prompt.package_json)
    assert no_prompt.report.warnings == [
        "style_prompt channel is unknown; prompt payload omitted"
    ]


def test_manifest_hash_pairing_mismatch_is_rejected() -> None:
    manifest = _manifest([])
    contract = _contract(manifest, {})
    bad_contract = contract.model_copy(
        update={
            "inputs": ContractInputs(
                identity_manifest=InputHash(sha256="0" * 64),
                arrangement_spec=InputHash(sha256=SPEC_SHA256),
            )
        }
    )

    with pytest.raises(PackageCompilationError, match="does not match"):
        _build(manifest, bad_contract, _profile())


def test_profile_generator_must_match_resolved_score_backend() -> None:
    manifest = _manifest([])
    contract = _contract(manifest, {})

    with pytest.raises(PackageCompilationError) as exc_info:
        _build(manifest, contract, _profile(generator="musicgen"))

    message = str(exc_info.value)
    assert "capability profile generator mismatch" in message
    assert "target_backend 'suno' resolves to generator 'suno'" in message
    assert "profile declares 'musicgen'" in message


def test_external_backend_alias_accepts_suno_capability_profile() -> None:
    manifest = _manifest([])
    contract = _contract(manifest, {})
    external_score = _score(backend="external")

    compiled = _build(
        manifest,
        contract,
        _profile(generator="suno"),
        score=external_score,
    )

    assert compiled.package.generator == "suno"
    assert compiled.package.prompt is not None
    expected = ExternalPromptAdapter().render(_score(backend="suno"))
    assert compiled.package.prompt.text == expected.text
    assert compiled.package.prompt.section_tags == expected.section_tags
    assert compiled.package.prompt.section_tags is not None
    assert ExternalPromptAdapter().render(external_score).section_tags is None
    assert external_score.rendering.target_backend == "external"


def test_builder_pins_the_exact_device_profile_used_for_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest([])
    contract = _contract(manifest, {})
    device_profile = load_device_profile("suno")
    assert device_profile is not None

    def fail_if_reloaded(_generator: str) -> None:
        raise AssertionError("renderer reloaded the device profile")

    monkeypatch.setattr(
        "svp_rpe.compose.prompt_renderer.load_device_profile",
        fail_if_reloaded,
    )
    compiled = _build(
        manifest,
        contract,
        _profile(),
        device_profile=device_profile,
    )
    altered = device_profile.model_copy(update={"notes": "different provenance"})
    altered_compiled = _build(
        manifest,
        contract,
        _profile(),
        device_profile=altered,
    )

    pinned = compiled.package.inputs.device_profile
    assert pinned.generator == "suno"
    assert pinned.status == "loaded"
    assert pinned.sha256 is not None
    assert pinned.hash_basis == "canonical_model_json"
    assert altered_compiled.package.inputs.device_profile.sha256 != pinned.sha256
    assert compiled.report.inputs.device_profile == pinned


def test_package_readback_rejects_duplicate_and_inconsistent_delivery() -> None:
    manifest = _manifest([_anchor("lyrics", "lyrics", "lyrics_text")])
    contract = _contract(manifest, {"lyrics": ("hard", [])})
    compiled = _build(manifest, contract, _profile())
    data = compiled.package.model_dump(mode="json")

    duplicate = json.loads(json.dumps(data))
    duplicate["anchor_statuses"].append(duplicate["anchor_statuses"][0])
    with pytest.raises(ValidationError, match="duplicate anchor id"):
        PerformancePackage.model_validate(duplicate)

    missing_reference = json.loads(json.dumps(data))
    missing_reference["channel_artifacts"] = {}
    with pytest.raises(ValidationError, match="channel_artifacts reference"):
        PerformancePackage.model_validate(missing_reference)

    non_delivery_reference = json.loads(json.dumps(data))
    non_delivery_reference["anchor_statuses"][0]["delivery"] = {
        "channel": None,
        "status": "unsupported",
    }
    with pytest.raises(ValidationError, match="non-delivery anchor"):
        PerformancePackage.model_validate(non_delivery_reference)

    wrong_artifact_type = json.loads(json.dumps(data))
    wrong_artifact_type["channel_artifacts"]["lyrics_text"][0][
        "artifact_type"
    ] = "midi_clip"
    with pytest.raises(ValidationError, match="midi_clip.*maps to 'midi'"):
        PerformancePackage.model_validate(wrong_artifact_type)

    contradictory_request = json.loads(json.dumps(data))
    contradictory_request["anchor_statuses"][0]["requested_mode"] = None
    with pytest.raises(ValidationError, match="requested_mode must be null"):
        PerformancePackage.model_validate(contradictory_request)

    missing_artifact_base = json.loads(json.dumps(data))
    del missing_artifact_base["channel_artifacts"]["lyrics_text"][0]["artifact_base"]
    with pytest.raises(ValidationError, match="artifact_base"):
        PerformancePackage.model_validate(missing_artifact_base)

    for absolute_locator in (
        "/tmp/manifest",
        "C:\\manifest",
        "C:manifest",
        "\\manifest",
    ):
        absolute_artifact_base = json.loads(json.dumps(data))
        absolute_artifact_base["channel_artifacts"]["lyrics_text"][0][
            "artifact_base"
        ]["locator"] = absolute_locator
        with pytest.raises(ValidationError, match="locator must be relative"):
            PerformancePackage.model_validate(absolute_artifact_base)

    for escaping_artifact in (
        "../outside.wav",
        "nested/../../outside.wav",
        "/tmp/outside.wav",
        "C:\\outside.wav",
        "C:outside.wav",
        "\\outside.wav",
    ):
        invalid_artifact = json.loads(json.dumps(data))
        invalid_artifact["channel_artifacts"]["lyrics_text"][0][
            "artifact"
        ] = escaping_artifact
        with pytest.raises(
            ValidationError,
            match="artifact path must (be relative|not contain parent traversal)",
        ):
            PerformancePackage.model_validate(invalid_artifact)


def test_package_readback_rejects_incomplete_or_unrequested_policy() -> None:
    manifest = _manifest([_anchor("sections", "structure", "section_map")])
    contract = _contract(
        manifest,
        {"sections": ("elastic", ["intro_extension"])},
        tolerance_profiles={"sections": "loose-v1"},
    )
    data = _build(manifest, contract, _profile()).package.model_dump(mode="json")

    missing_allow = json.loads(json.dumps(data))
    del missing_allow["anchor_statuses"][0]["allow"]
    with pytest.raises(ValidationError, match="elastic.*requires"):
        PerformancePackage.model_validate(missing_allow)

    unrequested_policy = json.loads(json.dumps(data))
    unrequested_policy["anchor_statuses"][0]["requested_mode"] = None
    with pytest.raises(ValidationError, match="unrequested anchor"):
        PerformancePackage.model_validate(unrequested_policy)


def test_package_and_report_reject_unknown_schema_versions() -> None:
    compiled = _build(_manifest([]), _contract(_manifest([]), {}), _profile())
    package_data = compiled.package.model_dump(mode="json")
    package_data["schema_version"] = "performance-package/0.2"
    with pytest.raises(ValidationError):
        PerformancePackage.model_validate(package_data)

    report_data = compiled.report.model_dump(mode="json")
    # compilation-report/0.2 is now the current (valid) version — item 9/10
    # bumped it to carry content_digest + invocation_provenance.compiler, so
    # the unknown-version probe moves to 0.3.
    report_data["schema_version"] = "compilation-report/0.3"
    with pytest.raises(ValidationError):
        CompilationReport.model_validate(report_data)


def test_package_readback_rejects_inconsistent_device_profile_input() -> None:
    manifest = _manifest([])
    compiled = _build(manifest, _contract(manifest, {}), _profile())
    package_data = compiled.package.model_dump(mode="json")
    package_data["inputs"]["device_profile"]["status"] = "not_found"
    with pytest.raises(ValidationError, match="device profile.*equivalent"):
        PerformancePackage.model_validate(package_data)

    wrong_package_generator = compiled.package.model_dump(mode="json")
    wrong_package_generator["inputs"]["device_profile"]["generator"] = "musicgen"
    with pytest.raises(ValidationError, match="package generator must match"):
        PerformancePackage.model_validate(wrong_package_generator)

    wrong_report_generator = compiled.report.model_dump(mode="json")
    wrong_report_generator["inputs"]["device_profile"]["generator"] = "musicgen"
    with pytest.raises(ValidationError, match="report generator must match"):
        CompilationReport.model_validate(wrong_report_generator)


def test_builder_is_byte_deterministic_and_report_pins_package_bytes() -> None:
    manifest = _manifest([_anchor("lyrics", "lyrics", "lyrics_text")])
    contract = _contract(manifest, {"lyrics": ("hard", [])})

    first = _build(manifest, contract, _profile())
    second = _build(manifest, contract, _profile())

    assert first.package_json.encode() == second.package_json.encode()
    assert first.report_json.encode() == second.report_json.encode()
    assert first.report.package_sha256 == hashlib.sha256(
        first.package_json.encode("utf-8")
    ).hexdigest()


def _write_cli_fixture(
    tmp_path: Path,
    *,
    target_backend: str | None = None,
    source_name: str = "source.wav",
    artifact_name: str = "lyrics.txt",
) -> tuple[Path, Path, Path]:
    source_path = tmp_path / source_name
    source_path.write_bytes(b"source audio")
    artifact_path = tmp_path / artifact_name
    artifact_path.write_bytes(b"hello midnight")

    manifest_path = tmp_path / "identity.yaml"
    manifest_data = {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": "cli-package", "version": "1"},
        "source": {
            "locator": source_path.name,
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "rights_basis": "original",
        },
        "anchors": [
            {
                "id": "lyrics",
                "domain": "lyrics",
                "artifact": artifact_path.name,
                "artifact_type": "lyrics_text",
                "media_type": "text/plain",
                "sha256": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                "required": True,
            }
        ],
    }
    manifest_path.write_text(
        yaml.safe_dump(manifest_data, sort_keys=False),
        encoding="utf-8",
    )

    target = {}
    score_fields = {}
    if target_backend is not None:
        target = {"rendering": {"target_backend": target_backend}}
        score_fields = {"rendering.target_backend": "free"}

    spec_path = tmp_path / "arrangement.yaml"
    spec_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"id": "package-arrangement", "version": "1"},
                "target": target,
                "preservation": {
                    "score_fields": score_fields,
                    "identity_anchors": {"lyrics": {"mode": "hard", "allow": []}},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return manifest_path, spec_path, artifact_path


def _package_cli_args(
    manifest_path: Path,
    spec_path: Path,
    output_dir: Path,
    *,
    profile_path: Path = SUNO_PROFILE_PATH,
    strict: bool = False,
) -> list[str]:
    args = [
        "package",
        str(SCORE_PATH),
        str(manifest_path),
        str(spec_path),
        "--capability-profile",
        str(profile_path),
        "--output-dir",
        str(output_dir),
    ]
    if strict:
        args.append("--strict-capabilities")
    return args


def test_package_cli_outputs_reload_and_are_byte_deterministic(tmp_path: Path) -> None:
    manifest_path, spec_path, artifact_path = _write_cli_fixture(tmp_path)
    outputs = [tmp_path / "out-1", tmp_path / "out-2"]

    for output_dir in outputs:
        result = CliRunner().invoke(
            app,
            _package_cli_args(manifest_path, spec_path, output_dir),
        )
        assert result.exit_code == 0, result.output

    for filename in ("performance_package.json", "compilation_report.json"):
        assert (outputs[0] / filename).read_bytes() == (outputs[1] / filename).read_bytes()

    package_bytes = (outputs[0] / "performance_package.json").read_bytes()
    package = PerformancePackage.model_validate_json(package_bytes)
    report = CompilationReport.model_validate_json(
        (outputs[0] / "compilation_report.json").read_bytes()
    )
    assert package.work_id == "cli-package"
    assert report.package_sha256 == hashlib.sha256(package_bytes).hexdigest()
    reference = package.channel_artifacts["lyrics_text"][0]
    assert (
        outputs[0]
        / reference.artifact_base.locator
        / reference.artifact
    ).resolve() == artifact_path.resolve()
    serialized = json.dumps(package.model_dump(mode="json"))
    assert str(tmp_path.resolve()) not in serialized


def test_path_compiler_reads_each_declared_input_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, spec_path, _ = _write_cli_fixture(tmp_path)
    declared = [SCORE_PATH, manifest_path, spec_path, SUNO_PROFILE_PATH]
    counts: dict[Path, int] = {}
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(path: Path) -> bytes:
        resolved = path.resolve()
        counts[resolved] = counts.get(resolved, 0) + 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    compile_performance_package(
        SCORE_PATH,
        manifest_path,
        spec_path,
        SUNO_PROFILE_PATH,
        tmp_path / "out",
    )

    for path in declared:
        assert counts[path.resolve()] == 1


def test_package_cli_publish_failure_leaves_no_partial_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, spec_path, _ = _write_cli_fixture(tmp_path)
    output_dir = tmp_path / "out"
    original_replace = os.replace

    def failing_replace(src: Any, dst: Any, **kwargs: Any) -> Any:
        if str(dst).endswith("compilation_report.json") and not str(src).endswith(".prev"):
            raise OSError("injected package publish failure")
        return original_replace(src, dst, **kwargs)

    monkeypatch.setattr("os.replace", failing_replace)

    result = CliRunner().invoke(
        app,
        _package_cli_args(manifest_path, spec_path, output_dir),
    )

    assert result.exit_code == 1
    assert "injected package publish failure" in result.stderr
    assert output_dir.exists()
    assert list(output_dir.iterdir()) == []


def test_package_cli_strict_failure_publishes_nothing(tmp_path: Path) -> None:
    manifest_path, spec_path, _ = _write_cli_fixture(
        tmp_path,
        target_backend="musicgen",
    )
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        _package_cli_args(
            manifest_path,
            spec_path,
            output_dir,
            profile_path=MUSICGEN_PROFILE_PATH,
            strict=True,
        ),
    )

    assert result.exit_code == 1
    assert "strict capability check failed" in result.stderr
    assert not output_dir.exists()


def test_package_cli_rejects_profile_for_different_generator(tmp_path: Path) -> None:
    manifest_path, spec_path, _ = _write_cli_fixture(tmp_path)
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        _package_cli_args(
            manifest_path,
            spec_path,
            output_dir,
            profile_path=MUSICGEN_PROFILE_PATH,
        ),
    )

    assert result.exit_code == 1
    assert "capability profile generator mismatch" in result.stderr
    assert "target_backend 'external' resolves to generator 'suno'" in result.stderr
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("source_name", "artifact_name", "colliding_name"),
    [
        ("performance_package.json", "lyrics.txt", "performance_package.json"),
        ("source.wav", "compilation_report.json", "compilation_report.json"),
    ],
)
def test_package_cli_does_not_overwrite_verified_manifest_artifacts(
    tmp_path: Path,
    source_name: str,
    artifact_name: str,
    colliding_name: str,
) -> None:
    manifest_path, spec_path, _ = _write_cli_fixture(
        tmp_path,
        source_name=source_name,
        artifact_name=artifact_name,
    )
    colliding_path = tmp_path / colliding_name
    original_bytes = colliding_path.read_bytes()

    result = CliRunner().invoke(
        app,
        _package_cli_args(manifest_path, spec_path, tmp_path),
    )

    assert result.exit_code == 1
    assert "input path collides with output artifact path" in result.stderr
    assert colliding_path.read_bytes() == original_bytes
    other_name = (
        "compilation_report.json"
        if colliding_name == "performance_package.json"
        else "performance_package.json"
    )
    assert not (tmp_path / other_name).exists()


# --- item 9/10: content_digest + invocation_provenance.compiler -----------------


def test_report_content_digest_matches_independent_recomputation() -> None:
    """content_digest = sha256(canonical_json({performance_package.json: package_sha256}))."""
    manifest = _manifest([_anchor("lyrics", "lyrics", "lyrics_text")])
    contract = _contract(manifest, {"lyrics": ("hard", [])})

    compiled = _build(manifest, contract, _profile())

    expected = compute_content_digest(
        {PERFORMANCE_PACKAGE_FILENAME: compiled.report.package_sha256}
    )
    assert compiled.report.content_digest == expected
    assert (
        compiled.report.content_digest_basis
        == "sha256-of-canonical-json-artifact-name-to-sha256/v1"
    )


def test_report_content_digest_excludes_invocation_provenance() -> None:
    """content_digest is stable across differing compiler provenance (D-1)."""
    manifest = _manifest([])
    contract = _contract(manifest, {})
    profile = _profile()

    first = _build(
        manifest,
        contract,
        profile,
        compiler_package_version="1.2.3",
        compiler_git_commit="a" * 40,
    )
    second = _build(
        manifest,
        contract,
        profile,
        compiler_package_version=None,
        compiler_git_commit=None,
    )

    assert first.report.content_digest == second.report.content_digest
    assert first.report.package_sha256 == second.report.package_sha256
    assert first.report.invocation_provenance.compiler.package_version == "1.2.3"
    assert first.report.invocation_provenance.compiler.git_commit == "a" * 40
    assert second.report.invocation_provenance.compiler.package_version is None
    assert second.report.invocation_provenance.compiler.git_commit is None


def test_build_performance_package_defaults_compiler_fields_to_none() -> None:
    """Callers that don't supply compiler provenance get `None`, not fabricated values."""
    manifest = _manifest([])
    contract = _contract(manifest, {})

    compiled = _build(manifest, contract, _profile())

    compiler = compiled.report.invocation_provenance.compiler
    assert compiler.package_version is None
    assert compiler.git_commit is None


def test_detect_compiler_package_version_and_git_commit_never_raise() -> None:
    """Best-effort detection: any failure degrades to `None`, never an exception."""
    version = detect_compiler_package_version()
    commit = detect_compiler_git_commit()

    assert version is None or isinstance(version, str)
    assert commit is None or (isinstance(commit, str) and len(commit) == 40)


def test_detect_compiler_git_commit_returns_none_when_no_checkout_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Outside any git checkout, detection returns `None` without invoking git."""
    import subprocess

    from svp_rpe.arrange import package as package_module

    def fail_if_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("git should not be invoked when no .git directory is found")

    # `tmp_path` has no `.git` anywhere above it, simulating an installed
    # (non-checkout) environment; `__file__` is module-level so this affects
    # the `Path(__file__).resolve().parent` walk inside the function.
    monkeypatch.setattr(package_module, "__file__", str(tmp_path / "arrange" / "package.py"))
    monkeypatch.setattr(subprocess, "run", fail_if_called)

    assert package_module.detect_compiler_git_commit() is None


def test_package_cli_compiler_provenance_is_present_or_none(tmp_path: Path) -> None:
    """The CLI-facing entry point (`compile_performance_package`) fills in real
    (or `None`) compiler provenance, never raising for either outcome."""
    manifest_path, spec_path, _ = _write_cli_fixture(tmp_path)
    output_dir = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        _package_cli_args(manifest_path, spec_path, output_dir),
    )
    assert result.exit_code == 0, result.output

    report = CompilationReport.model_validate_json(
        (output_dir / "compilation_report.json").read_bytes()
    )
    compiler = report.invocation_provenance.compiler
    assert compiler.package_version is None or isinstance(compiler.package_version, str)
    assert compiler.git_commit is None or len(compiler.git_commit) == 40
    # this repo checkout has a .git directory, so git_commit should resolve.
    assert compiler.git_commit is not None
    assert compiler.git_commit == detect_compiler_git_commit()
