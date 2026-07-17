"""ArrangementSpec と決定論的 Resolver。

既存 `CompositionScore` を変更せず、`ArrangementSpec`（部分 override +
preservation policy）から `DerivedScore` と field-level diff を導出する core API。
CLI / identity artifact / backend capability / 音源解析はスコープ外。
"""
from __future__ import annotations

from svp_rpe.arrange.bundle import (
    BUNDLE_FILENAME,
    CompiledArrangement,
    DERIVED_SCORE_FILENAME,
    DIFF_FILENAME,
    compile_arrangement,
)
from svp_rpe.arrange.capabilities import (
    ChannelSupport,
    INPUT_CHANNELS,
    InputCapabilityError,
    InputCapabilityProfile,
    InputChannelCapability,
    InputChannels,
    load_input_capability_profile,
)
from svp_rpe.arrange.contract import (
    ContractAnchor,
    DOMAIN_ALLOWED_TRANSFORMS,
    PreservationContract,
    PreservationContractError,
    build_preservation_contract,
)
from svp_rpe.arrange.identity import (
    IdentityAnchor,
    IdentityManifest,
    IdentityManifestError,
    load_identity_manifest,
)
from svp_rpe.arrange.loader import load_arrangement_spec
from svp_rpe.arrange.models import (
    AllowedTransformation,
    AnchorPreservation,
    ArrangementChange,
    ArrangementMeta,
    ArrangementResolution,
    ArrangementSpec,
    ArrangementTarget,
    DeltaEOverride,
    EventOverride,
    GrvOverride,
    PhysicalOverride,
    PreservationMode,
    PreservationSpec,
    RenderingOverride,
    SemanticOverride,
)
from svp_rpe.arrange.observe import (
    OBSERVATION_REPORT_SCHEMA_VERSION,
    AnchorObservation,
    GeneratedArtifactRef,
    ObservationReport,
    SensorRecord,
    build_observation_report,
)
from svp_rpe.arrange.package import (
    ARTIFACT_TYPE_CHANNEL,
    COMPILATION_REPORT_FILENAME,
    PERFORMANCE_PACKAGE_FILENAME,
    CompilationReport,
    CompiledPerformancePackage,
    PackageCompilationError,
    PerformancePackage,
    build_performance_package,
    compile_performance_package,
)
from svp_rpe.arrange.resolver import (
    ArrangementConflictError,
    ArrangementError,
    ArrangementPolicyError,
    CANONICAL_PATHS,
    resolve_arrangement,
)

__all__ = [
    "AllowedTransformation",
    "ARTIFACT_TYPE_CHANNEL",
    "AnchorObservation",
    "AnchorPreservation",
    "ArrangementChange",
    "ArrangementConflictError",
    "ArrangementError",
    "ArrangementMeta",
    "ArrangementPolicyError",
    "ArrangementResolution",
    "ArrangementSpec",
    "ArrangementTarget",
    "BUNDLE_FILENAME",
    "CANONICAL_PATHS",
    "ChannelSupport",
    "COMPILATION_REPORT_FILENAME",
    "CompilationReport",
    "CompiledArrangement",
    "CompiledPerformancePackage",
    "ContractAnchor",
    "DERIVED_SCORE_FILENAME",
    "DIFF_FILENAME",
    "DOMAIN_ALLOWED_TRANSFORMS",
    "DeltaEOverride",
    "EventOverride",
    "GeneratedArtifactRef",
    "GrvOverride",
    "INPUT_CHANNELS",
    "IdentityAnchor",
    "IdentityManifest",
    "IdentityManifestError",
    "InputCapabilityError",
    "InputCapabilityProfile",
    "InputChannelCapability",
    "InputChannels",
    "OBSERVATION_REPORT_SCHEMA_VERSION",
    "ObservationReport",
    "PhysicalOverride",
    "PERFORMANCE_PACKAGE_FILENAME",
    "PackageCompilationError",
    "PerformancePackage",
    "PreservationContract",
    "PreservationContractError",
    "PreservationMode",
    "PreservationSpec",
    "RenderingOverride",
    "SemanticOverride",
    "SensorRecord",
    "build_observation_report",
    "build_preservation_contract",
    "build_performance_package",
    "compile_arrangement",
    "compile_performance_package",
    "load_arrangement_spec",
    "load_identity_manifest",
    "load_input_capability_profile",
    "resolve_arrangement",
]
