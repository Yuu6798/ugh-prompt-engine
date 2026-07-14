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
from svp_rpe.arrange.identity import (
    IdentityAnchor,
    IdentityManifest,
    IdentityManifestError,
    load_identity_manifest,
)
from svp_rpe.arrange.loader import load_arrangement_spec
from svp_rpe.arrange.models import (
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
from svp_rpe.arrange.resolver import (
    ArrangementConflictError,
    ArrangementError,
    ArrangementPolicyError,
    CANONICAL_PATHS,
    resolve_arrangement,
)

__all__ = [
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
    "CompiledArrangement",
    "DERIVED_SCORE_FILENAME",
    "DIFF_FILENAME",
    "DeltaEOverride",
    "EventOverride",
    "GrvOverride",
    "IdentityAnchor",
    "IdentityManifest",
    "IdentityManifestError",
    "PhysicalOverride",
    "PreservationMode",
    "PreservationSpec",
    "RenderingOverride",
    "SemanticOverride",
    "compile_arrangement",
    "load_arrangement_spec",
    "load_identity_manifest",
    "resolve_arrangement",
]
