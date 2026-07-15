"""Compile arrangement inputs into a deterministic generator handoff package.

Capability support, control evidence, and post-generation observation are kept
as separate states.  In particular, delivery never implies preservation.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Optional

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from svp_rpe.arrange.bundle import render_score_yaml
from svp_rpe.arrange.capabilities import (
    INPUT_CHANNELS,
    ChannelSupport,
    InputCapabilityProfile,
    _validate_evidence_form,
)
from svp_rpe.arrange.contract import PreservationContract, build_preservation_contract
from svp_rpe.arrange.identity import (
    ArtifactType,
    IdentityManifest,
    _resolve_confined,
    _verify_artifact_hash,
)
from svp_rpe.arrange.models import (
    AllowedTransformation,
    ArrangementSpec,
    PreservationMode,
)
from svp_rpe.arrange.resolver import ArrangementError, resolve_arrangement
from svp_rpe.compose.device_profile import DeviceProfile, load_device_profile
from svp_rpe.compose.models import CompositionScore
from svp_rpe.compose.prompt_renderer import (
    ExternalPromptAdapter,
    resolve_backend_descriptor,
)

PERFORMANCE_PACKAGE_FILENAME = "performance_package.json"
COMPILATION_REPORT_FILENAME = "compilation_report.json"

_SHA256_PATTERN = r"^[0-9a-f]{64}$"

InputChannel = Literal[
    "style_prompt",
    "lyrics_text",
    "section_tags",
    "reference_audio",
    "symbolic_melody",
    "midi",
]
DeliveryStatus = Literal[
    "delivered",
    "experimental",
    "unsupported",
    "unknown",
    "not_requested",
]
ControlStatus = Literal["controllable", "uncontrollable", "unknown"]
AdherenceStatus = Literal[
    "preserved",
    "changed_within_policy",
    "changed_outside_policy",
    "sensor_blind",
    "not_observed",
]
CompilationMode = Literal["strict", "advisory"]


ARTIFACT_TYPE_CHANNEL: dict[ArtifactType, InputChannel | None] = {
    "lyrics_text": "lyrics_text",
    "midi_clip": "midi",
    "note_events_json": "symbolic_melody",
    "chord_sequence_json": None,
    "audio_excerpt": "reference_audio",
    "section_map": "section_tags",
}


class PackageCompilationError(ArrangementError):
    """PerformancePackage construction or strict capability failure."""


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PackageInputHash(PackageModel):
    sha256: str = Field(pattern=_SHA256_PATTERN)


class DeviceProfileInput(PackageModel):
    generator: str
    status: Literal["loaded", "not_found"]
    sha256: Optional[str] = Field(default=None, pattern=_SHA256_PATTERN)
    hash_basis: Literal["canonical_model_json"] = "canonical_model_json"

    @model_validator(mode="after")
    def _validate_status_hash(self) -> "DeviceProfileInput":
        if (self.status == "loaded") != (self.sha256 is not None):
            raise ValueError(
                "device profile status='loaded' and a non-null sha256 must be "
                "equivalent"
            )
        return self


class PackageInputs(PackageModel):
    identity_manifest: PackageInputHash
    preservation_contract: PackageInputHash
    capability_profile: PackageInputHash
    derived_score: PackageInputHash
    device_profile: DeviceProfileInput


class DeliveryState(PackageModel):
    channel: Optional[InputChannel] = None
    status: DeliveryStatus


class ControlState(PackageModel):
    status: ControlStatus
    evidence: Optional[str] = None


class ObservationState(PackageModel):
    status: AdherenceStatus


class PackageAnchorStatus(PackageModel):
    anchor_id: str
    requested_mode: Optional[PreservationMode] = None
    allow: list[AllowedTransformation] = Field(default_factory=list)
    tolerance_profile: Optional[str] = None
    delivery: DeliveryState
    control: ControlState
    observation: ObservationState

    @model_validator(mode="after")
    def _validate_requested_policy(self) -> "PackageAnchorStatus":
        if self.requested_mode is None:
            if self.allow or self.tolerance_profile is not None:
                raise ValueError(
                    f"anchor '{self.anchor_id}': an unrequested anchor cannot carry "
                    "allow or tolerance_profile"
                )
            return self
        if self.requested_mode in ("hard", "free") and self.allow:
            raise ValueError(
                f"anchor '{self.anchor_id}': mode={self.requested_mode!r} must "
                "have an empty allow list"
            )
        if self.requested_mode == "elastic" and not self.allow:
            raise ValueError(
                f"anchor '{self.anchor_id}': mode='elastic' requires at least "
                "one allowed transformation"
            )
        return self


class ArtifactBase(PackageModel):
    kind: Literal["identity_manifest_directory"]
    relative_to: Literal["performance_package_directory"]
    locator: str = Field(min_length=1)

    @field_validator("locator")
    @classmethod
    def _validate_relative_locator(cls, value: str) -> str:
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError("artifact base locator must be relative")
        return value


class ChannelArtifactReference(PackageModel):
    anchor_id: str
    artifact: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_type: ArtifactType
    media_type: str = Field(min_length=1)
    format_version: Optional[str] = None
    artifact_base: ArtifactBase


class PromptPayload(PackageModel):
    text: str
    tags: list[str]
    negative_tags: list[str]
    dropped_elements: list[str]
    advisories: list[str]
    section_tags: Optional[str] = None

    @model_serializer(mode="wrap")
    def serialize_without_section_tags(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.section_tags is None:
            data.pop("section_tags", None)
        return data


class PerformancePackage(PackageModel):
    schema_version: Literal["performance-package/0.1"] = "performance-package/0.1"
    work_id: str
    generator: str
    inputs: PackageInputs
    prompt: Optional[PromptPayload] = None
    channel_artifacts: dict[InputChannel, list[ChannelArtifactReference]] = Field(
        default_factory=dict
    )
    anchor_statuses: list[PackageAnchorStatus]

    @model_validator(mode="after")
    def _validate_delivery_references(self) -> "PerformancePackage":
        statuses: dict[str, PackageAnchorStatus] = {}
        duplicates: set[str] = set()
        for status in self.anchor_statuses:
            if status.anchor_id in statuses:
                duplicates.add(status.anchor_id)
            statuses[status.anchor_id] = status
        if duplicates:
            raise ValueError(
                "duplicate anchor id(s) in performance package statuses: "
                + ", ".join(sorted(duplicates))
            )

        referenced: dict[str, InputChannel] = {}
        for channel, references in self.channel_artifacts.items():
            for reference in references:
                if reference.anchor_id in referenced:
                    raise ValueError(
                        "duplicate channel artifact reference for anchor "
                        f"'{reference.anchor_id}'"
                    )
                referenced[reference.anchor_id] = channel
                status = statuses.get(reference.anchor_id)
                if status is None:
                    raise ValueError(
                        "channel artifact references unknown anchor "
                        f"'{reference.anchor_id}'"
                    )
                if status.delivery.status not in ("delivered", "experimental"):
                    raise ValueError(
                        f"non-delivery anchor '{reference.anchor_id}' appears in "
                        "channel_artifacts"
                    )
                if status.delivery.channel != channel:
                    raise ValueError(
                        f"anchor '{reference.anchor_id}' is listed under channel "
                        f"'{channel}' but status declares {status.delivery.channel!r}"
                    )
                mapped_channel = ARTIFACT_TYPE_CHANNEL[reference.artifact_type]
                if mapped_channel != channel:
                    raise ValueError(
                        f"anchor '{reference.anchor_id}' artifact_type "
                        f"{reference.artifact_type!r} maps to {mapped_channel!r}, "
                        f"not channel '{channel}'"
                    )

        for status in self.anchor_statuses:
            is_requested = status.requested_mode is not None
            is_not_requested = status.delivery.status == "not_requested"
            if is_requested == is_not_requested:
                raise ValueError(
                    f"anchor '{status.anchor_id}': requested_mode must be null exactly "
                    "when delivery status is not_requested"
                )
            is_delivered = status.delivery.status in ("delivered", "experimental")
            if is_delivered != (status.delivery.channel is not None):
                raise ValueError(
                    f"anchor '{status.anchor_id}': delivered/experimental status and "
                    "non-null delivery channel must be equivalent"
                )
            is_referenced = status.anchor_id in referenced
            if is_delivered != is_referenced:
                raise ValueError(
                    f"anchor '{status.anchor_id}': delivered/experimental status and "
                    "channel_artifacts reference must be equivalent"
                )
        return self

    @model_serializer(mode="wrap")
    def serialize_without_absent_prompt(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.prompt is None:
            data.pop("prompt", None)
        return data


class CompilationReport(PackageModel):
    """Compilation record.

    ``package_sha256`` pins the published package bytes.  The report cannot
    contain a self-hash because adding that hash would change the report bytes.
    """

    schema_version: Literal["compilation-report/0.1"] = "compilation-report/0.1"
    work_id: str
    generator: str
    mode: CompilationMode
    inputs: PackageInputs
    package_sha256: str = Field(pattern=_SHA256_PATTERN)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class CompiledPerformancePackage:
    """Fully rendered artifacts returned before any filesystem publication."""

    package: PerformancePackage
    report: CompilationReport
    package_json: str
    report_json: str
    protected_input_paths: tuple[Path, ...] = ()


def _render_json(model: BaseModel) -> str:
    return json.dumps(
        model.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        indent=2,
    )


def _device_profile_input(
    generator: str,
    device_profile: DeviceProfile | None,
) -> DeviceProfileInput:
    if device_profile is None:
        return DeviceProfileInput(generator=generator, status="not_found")
    if device_profile.generator != generator:
        raise PackageCompilationError(
            "device profile generator mismatch: expected "
            f"{generator!r}, got {device_profile.generator!r}"
        )
    canonical_bytes = json.dumps(
        device_profile.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return DeviceProfileInput(
        generator=generator,
        status="loaded",
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )


def _delivery_status(support: ChannelSupport) -> DeliveryStatus:
    if support == "supported":
        return "delivered"
    return support


def _anchor_warning(
    anchor_id: str,
    *,
    channel: InputChannel | None,
    status: DeliveryStatus,
) -> str | None:
    if channel is None:
        return f"anchor '{anchor_id}': no channel mapping defined"
    if status == "experimental":
        return f"anchor '{anchor_id}': channel '{channel}' is experimental"
    if status in ("unsupported", "unknown"):
        return f"anchor '{anchor_id}': channel '{channel}' is {status}"
    return None


def _prompt_payload(
    score: CompositionScore,
    profile: InputCapabilityProfile,
    device_profile: DeviceProfile | None,
    warnings: list[str],
    *,
    render_backend: str,
) -> PromptPayload | None:
    style_support = profile.input_channels.style_prompt.support
    if style_support in ("unsupported", "unknown"):
        warnings.append(
            f"style_prompt channel is {style_support}; prompt payload omitted"
        )
        return None
    if style_support == "experimental":
        warnings.append("style_prompt channel is experimental; prompt payload included")

    render_score = score
    if score.rendering.target_backend != render_backend:
        render_score = score.model_copy(
            update={
                "rendering": score.rendering.model_copy(
                    update={"target_backend": render_backend}
                )
            }
        )
    generated = ExternalPromptAdapter().render(
        render_score,
        device_profile=device_profile,
    )
    section_tags: str | None = None
    if generated.section_tags is not None:
        section_support = profile.input_channels.section_tags.support
        if section_support in ("supported", "experimental"):
            section_tags = generated.section_tags
            if section_support == "experimental":
                warnings.append(
                    "section_tags channel is experimental; payload included"
                )
        else:
            warnings.append(
                f"section_tags channel is {section_support}; payload omitted"
            )

    return PromptPayload(
        text=generated.text,
        tags=list(generated.tags),
        negative_tags=list(generated.negative_tags),
        dropped_elements=list(generated.dropped_elements),
        advisories=list(generated.advisories),
        section_tags=section_tags,
    )


def build_performance_package(
    manifest: IdentityManifest,
    contract: PreservationContract,
    profile: InputCapabilityProfile,
    derived_score: CompositionScore,
    *,
    device_profile: DeviceProfile | None,
    artifact_base_locator: str,
    manifest_sha256: str,
    contract_sha256: str,
    profile_sha256: str,
    derived_score_sha256: str,
    strict: bool = False,
) -> CompiledPerformancePackage:
    """Build package and report completely in memory.

    Hashes are supplied by the caller so this function never performs disk I/O.
    """
    if contract.inputs.identity_manifest.sha256 != manifest_sha256:
        raise PackageCompilationError(
            "preservation contract identity_manifest sha256 does not match the "
            f"supplied manifest sha256: {contract.inputs.identity_manifest.sha256} "
            f"!= {manifest_sha256}"
        )

    target_backend = derived_score.rendering.target_backend
    expected_generator = resolve_backend_descriptor(target_backend).profile_key
    if profile.generator != expected_generator:
        raise PackageCompilationError(
            "capability profile generator mismatch: derived score target_backend "
            f"{target_backend!r} resolves to generator {expected_generator!r}, but "
            f"the supplied profile declares {profile.generator!r}"
        )

    manifest_by_id = {anchor.id: anchor for anchor in manifest.anchors}
    contract_by_id = {anchor.anchor_id: anchor for anchor in contract.anchors}
    unknown_contract_ids = sorted(set(contract_by_id) - set(manifest_by_id))
    if unknown_contract_ids:
        raise PackageCompilationError(
            "preservation contract references anchor id(s) absent from the identity "
            "manifest: " + ", ".join(unknown_contract_ids)
        )

    statuses: list[PackageAnchorStatus] = []
    channel_artifacts: dict[InputChannel, list[ChannelArtifactReference]] = {}
    artifact_base = ArtifactBase(
        kind="identity_manifest_directory",
        relative_to="performance_package_directory",
        locator=artifact_base_locator,
    )
    warnings: list[str] = []
    strict_failures: list[tuple[str, DeliveryStatus]] = []

    for anchor in manifest.anchors:
        requested = contract_by_id.get(anchor.id)
        if requested is None:
            statuses.append(
                PackageAnchorStatus(
                    anchor_id=anchor.id,
                    requested_mode=None,
                    allow=[],
                    tolerance_profile=None,
                    delivery=DeliveryState(status="not_requested"),
                    control=ControlState(status="unknown"),
                    observation=ObservationState(status="not_observed"),
                )
            )
            continue

        mapped_channel = ARTIFACT_TYPE_CHANNEL[anchor.artifact_type]
        if mapped_channel is None:
            delivery_status: DeliveryStatus = "unknown"
        else:
            support = getattr(profile.input_channels, mapped_channel).support
            delivery_status = _delivery_status(support)

        delivered_channel = (
            mapped_channel
            if delivery_status in ("delivered", "experimental")
            else None
        )
        statuses.append(
            PackageAnchorStatus(
                anchor_id=anchor.id,
                requested_mode=requested.mode,
                allow=list(requested.allow),
                tolerance_profile=requested.tolerance_profile,
                delivery=DeliveryState(
                    channel=delivered_channel,
                    status=delivery_status,
                ),
                control=ControlState(status="unknown"),
                observation=ObservationState(status="not_observed"),
            )
        )

        warning = _anchor_warning(
            anchor.id,
            channel=mapped_channel,
            status=delivery_status,
        )
        if warning is not None:
            warnings.append(warning)

        if delivered_channel is not None:
            channel_artifacts.setdefault(delivered_channel, []).append(
                ChannelArtifactReference(
                    anchor_id=anchor.id,
                    artifact=anchor.artifact,
                    artifact_sha256=anchor.sha256,
                    artifact_type=anchor.artifact_type,
                    media_type=anchor.media_type,
                    format_version=anchor.format_version,
                    artifact_base=artifact_base,
                )
            )
        if requested.mode == "hard" and delivery_status in ("unsupported", "unknown"):
            strict_failures.append((anchor.id, delivery_status))

    if strict and strict_failures:
        details = ", ".join(
            f"{anchor_id} ({status})" for anchor_id, status in strict_failures
        )
        raise PackageCompilationError(
            "strict capability check failed for hard anchor(s): " + details
        )

    inputs = PackageInputs(
        identity_manifest=PackageInputHash(sha256=manifest_sha256),
        preservation_contract=PackageInputHash(sha256=contract_sha256),
        capability_profile=PackageInputHash(sha256=profile_sha256),
        derived_score=PackageInputHash(sha256=derived_score_sha256),
        device_profile=_device_profile_input(expected_generator, device_profile),
    )
    prompt = _prompt_payload(
        derived_score,
        profile,
        device_profile,
        warnings,
        render_backend=expected_generator,
    )
    package = PerformancePackage(
        work_id=manifest.meta.work_id,
        generator=profile.generator,
        inputs=inputs,
        prompt=prompt,
        channel_artifacts=channel_artifacts,
        anchor_statuses=statuses,
    )
    package_json = _render_json(package)
    package_sha256 = hashlib.sha256(package_json.encode("utf-8")).hexdigest()
    report = CompilationReport(
        work_id=manifest.meta.work_id,
        generator=profile.generator,
        mode="strict" if strict else "advisory",
        inputs=inputs,
        package_sha256=package_sha256,
        warnings=warnings,
    )
    return CompiledPerformancePackage(
        package=package,
        report=report,
        package_json=package_json,
        report_json=_render_json(report),
    )


def _parse_yaml_mapping(raw_bytes: bytes, label: str, path_str: str) -> dict[str, Any]:
    data = yaml.safe_load(raw_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping: {path_str}")
    return data


def _verify_manifest_artifacts(
    manifest: IdentityManifest,
    manifest_path: Path,
) -> tuple[Path, ...]:
    base_dir = manifest_path.resolve().parent
    work_id = manifest.meta.work_id
    source_path = _resolve_confined(
        manifest.source.locator,
        base_dir,
        work_id=work_id,
        target="source",
    )
    _verify_artifact_hash(
        source_path,
        manifest.source.sha256,
        work_id=work_id,
        target="source",
    )
    verified_paths = [source_path]
    for anchor in manifest.anchors:
        target = f"anchor '{anchor.id}'"
        artifact_path = _resolve_confined(
            anchor.artifact,
            base_dir,
            work_id=work_id,
            target=target,
        )
        _verify_artifact_hash(
            artifact_path,
            anchor.sha256,
            work_id=work_id,
            target=target,
        )
        verified_paths.append(artifact_path)
    return tuple(verified_paths)


def compile_performance_package(
    score_path: Path | str,
    manifest_path: Path | str,
    spec_path: Path | str,
    profile_path: Path | str,
    output_dir: Path | str,
    *,
    strict: bool = False,
) -> CompiledPerformancePackage:
    """Read each declared input once, then compile both artifacts in memory."""
    score_file = Path(score_path)
    manifest_file = Path(manifest_path)
    spec_file = Path(spec_path)
    profile_file = Path(profile_path)
    package_dir = Path(output_dir)

    score_bytes = score_file.read_bytes()
    manifest_bytes = manifest_file.read_bytes()
    spec_bytes = spec_file.read_bytes()
    profile_bytes = profile_file.read_bytes()

    source = CompositionScore.model_validate(
        _parse_yaml_mapping(score_bytes, "composition score", str(score_path))
    )
    manifest = IdentityManifest.model_validate(
        _parse_yaml_mapping(manifest_bytes, "identity manifest", str(manifest_path))
    )
    spec = ArrangementSpec.model_validate(
        _parse_yaml_mapping(spec_bytes, "arrangement spec", str(spec_path))
    )
    profile = InputCapabilityProfile.model_validate(
        _parse_yaml_mapping(profile_bytes, "input capability profile", str(profile_path))
    )

    manifest_artifact_paths = _verify_manifest_artifacts(manifest, manifest_file)
    for channel_name in INPUT_CHANNELS:
        capability = getattr(profile.input_channels, channel_name)
        if capability.evidence is not None:
            _validate_evidence_form(
                capability.evidence,
                generator=profile.generator,
                channel=channel_name,
            )

    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    spec_sha256 = hashlib.sha256(spec_bytes).hexdigest()
    resolution = resolve_arrangement(source, spec)
    contract = build_preservation_contract(
        manifest,
        spec,
        manifest_sha256=manifest_sha256,
        spec_sha256=spec_sha256,
    )

    contract_json = _render_json(contract)
    contract_sha256 = hashlib.sha256(contract_json.encode("utf-8")).hexdigest()
    derived_score_yaml = render_score_yaml(resolution.derived_score)
    derived_score_sha256 = hashlib.sha256(
        derived_score_yaml.encode("utf-8")
    ).hexdigest()
    render_generator = resolve_backend_descriptor(
        resolution.derived_score.rendering.target_backend
    ).profile_key
    device_profile = load_device_profile(render_generator)
    try:
        artifact_base_locator = Path(
            os.path.relpath(
                manifest_file.resolve().parent,
                package_dir.resolve(),
            )
        ).as_posix()
    except ValueError as exc:
        raise PackageCompilationError(
            "identity manifest directory cannot be expressed relative to the "
            f"performance package directory: {exc}"
        ) from exc

    compiled = build_performance_package(
        manifest,
        contract,
        profile,
        resolution.derived_score,
        device_profile=device_profile,
        artifact_base_locator=artifact_base_locator,
        manifest_sha256=manifest_sha256,
        contract_sha256=contract_sha256,
        profile_sha256=hashlib.sha256(profile_bytes).hexdigest(),
        derived_score_sha256=derived_score_sha256,
        strict=strict,
    )
    return replace(
        compiled,
        protected_input_paths=(
            score_file.resolve(),
            manifest_file.resolve(),
            spec_file.resolve(),
            profile_file.resolve(),
            *manifest_artifact_paths,
        ),
    )
