"""RecastProject: 既存 sidecar への参照+実行方針のみを持つワークスペース定義。

`svprpe recast` = 既存 sidecar（`CompositionScore` / `IdentityManifest` /
`ArrangementSpec` / `InputCapabilityProfile`）への**参照のみ**を宣言する
ワークスペース定義スキーマ（recast-project/0.1）。歌詞・旋律・楽譜本文の
複製は禁止（参照のみ）。既存 sidecar のスキーマ変更も禁止（本モジュールは
追加のみで既存モデルを一切 import/変更しない）。

共通基底 `RecastModel`（`arrange/models.py` の `ArrangementModel` と同型）は
`extra="forbid"` を継承し、未知 key を fail-fast で拒否する。

参照解決（封じ込め・存在チェック・名前参照の展開）は本モジュールの責務外で
`recast/loader.py` が担う — 本モジュールは pydantic スキーマのみを定義する。
"""
from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

InvocationKind = Literal["manual", "local"]
InvocationMode = Literal["prompt_only", "cover"]
CapabilityMode = Literal["strict", "advisory"]

# project.id / variants キー / backends キーに共通する slug 規約。
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_slug(value: str, *, field: str) -> str:
    if not _SLUG_PATTERN.match(value):
        raise ValueError(
            f"{field} must be a slug matching {_SLUG_PATTERN.pattern!r}, got {value!r}"
        )
    return value


class RecastModel(BaseModel):
    """recast 側スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")


class ProjectMeta(RecastModel):
    """workspace 自体の識別子と build 出力先の宣言。"""

    id: str
    builds_root: str

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _validate_slug(value, field="project.id")


class WorkRefs(RecastModel):
    """canonical CompositionScore + IdentityManifest への参照（相対パス）。"""

    score: str
    identity_manifest: str


class VariantRef(RecastModel):
    """1 variant が使う ArrangementSpec への参照（相対パス）。"""

    arrangement: str


class BackendRef(RecastModel):
    """1 backend の capability profile 参照 + 実行方針。"""

    capability_profile: str
    invocation: InvocationKind
    invocation_mode: InvocationMode
    mode_overrides: Optional[str] = None


class RecastPolicy(RecastModel):
    """capability 突合の厳格度と、実行前提とする既存契約の要求。"""

    capability_mode: CapabilityMode
    require_author_fields_resolved: bool = True
    require_verified_package: bool = True


class ObservationConfig(RecastModel):
    """生成後観測（`arrange/observe.py` 系列）への anchor 参照集合。"""

    enabled: bool
    anchors: List[str]

    @model_validator(mode="after")
    def _validate_unique_anchors(self) -> "ObservationConfig":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for anchor in self.anchors:
            if anchor in seen:
                duplicates.add(anchor)
            seen.add(anchor)
        if duplicates:
            raise ValueError(
                f"observation.anchors must not contain duplicates: {', '.join(sorted(duplicates))}"
            )
        return self


class RecastProject(RecastModel):
    """recast-project/0.1 ワークスペース定義本体。"""

    schema_version: Literal["recast-project/0.1"]
    project: ProjectMeta
    work: WorkRefs
    variants: Dict[str, VariantRef]
    backends: Dict[str, BackendRef]
    policy: RecastPolicy
    observation: ObservationConfig

    @field_validator("variants")
    @classmethod
    def _validate_variants(cls, value: Dict[str, VariantRef]) -> Dict[str, VariantRef]:
        if not value:
            raise ValueError("variants must not be empty")
        for key in value:
            _validate_slug(key, field="variants key")
        return value

    @field_validator("backends")
    @classmethod
    def _validate_backends(cls, value: Dict[str, BackendRef]) -> Dict[str, BackendRef]:
        if not value:
            raise ValueError("backends must not be empty")
        for key in value:
            _validate_slug(key, field="backends key")
        return value


class RecastError(ValueError):
    """RecastProject のロード・参照解決に関するエラーの基底クラス。"""


class RecastReferenceError(RecastError):
    """参照先の不在・封じ込め違反に関するエラー（project path + 参照値を含む）。"""
