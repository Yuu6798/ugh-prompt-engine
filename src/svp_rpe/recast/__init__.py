"""RecastProject: 既存 sidecar への参照+実行方針だけを持つワークスペース定義。

`svprpe recast`（CLI 配線は PR2 以降）の schema + loader 層（PR1）。既存
`CompositionScore` / `IdentityManifest` / `ArrangementSpec` /
`InputCapabilityProfile` のスキーマを一切変更せず、それらへの参照のみを
recast-project/0.1 として宣言する。
"""
from __future__ import annotations

from svp_rpe.recast.loader import LoadedRecastProject, load_recast_project
from svp_rpe.recast.models import (
    BackendRef,
    CapabilityMode,
    InvocationKind,
    InvocationMode,
    ObservationConfig,
    ProjectMeta,
    RecastError,
    RecastPolicy,
    RecastProject,
    RecastReferenceError,
    VariantRef,
    WorkRefs,
)

__all__ = [
    "BackendRef",
    "CapabilityMode",
    "InvocationKind",
    "InvocationMode",
    "LoadedRecastProject",
    "ObservationConfig",
    "ProjectMeta",
    "RecastError",
    "RecastPolicy",
    "RecastProject",
    "RecastReferenceError",
    "VariantRef",
    "WorkRefs",
    "load_recast_project",
]
