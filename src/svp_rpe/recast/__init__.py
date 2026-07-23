"""RecastProject: 既存 sidecar への参照+実行方針だけを持つワークスペース定義。

`svprpe recast`（CLI 配線は PR2 以降）の schema + loader 層（PR1）。既存
`CompositionScore` / `IdentityManifest` / `ArrangementSpec` /
`InputCapabilityProfile` のスキーマを一切変更せず、それらへの参照のみを
recast-project/0.1 として宣言する。
"""
from __future__ import annotations

from svp_rpe.recast.loader import LoadedRecastProject, load_mode_overrides, load_recast_project
from svp_rpe.recast.models import (
    BackendRef,
    CapabilityMode,
    InvocationKind,
    InvocationMode,
    ModeOverrideEntry,
    ModeOverridesConfig,
    ObservationConfig,
    OverrideSupport,
    ProjectMeta,
    RecastError,
    RecastPolicy,
    RecastProject,
    RecastReferenceError,
    VariantRef,
    WorkRefs,
)
from svp_rpe.recast.plan import RecastPlan, RecastPlanResult, build_recast_plan
from svp_rpe.recast.state import (
    RECAST_STATE_FILENAME,
    RecastRunState,
    RecastState,
    RecastStateFile,
    load_recast_state,
    record_state,
)

__all__ = [
    "BackendRef",
    "CapabilityMode",
    "InvocationKind",
    "InvocationMode",
    "LoadedRecastProject",
    "ModeOverrideEntry",
    "ModeOverridesConfig",
    "ObservationConfig",
    "OverrideSupport",
    "ProjectMeta",
    "RECAST_STATE_FILENAME",
    "RecastError",
    "RecastPlan",
    "RecastPlanResult",
    "RecastPolicy",
    "RecastProject",
    "RecastReferenceError",
    "RecastRunState",
    "RecastState",
    "RecastStateFile",
    "VariantRef",
    "WorkRefs",
    "build_recast_plan",
    "load_mode_overrides",
    "load_recast_project",
    "load_recast_state",
    "record_state",
]
