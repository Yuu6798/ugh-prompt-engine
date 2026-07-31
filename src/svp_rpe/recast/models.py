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

from svp_rpe.arrange.resolver import CANONICAL_PATHS
from svp_rpe.melody.routing import select_routes

InvocationKind = Literal["manual", "local"]
InvocationMode = Literal["prompt_only", "cover"]
CapabilityMode = Literal["strict", "advisory"]
OverrideSupport = Literal["supported", "experimental", "unsupported", "unknown"]
# M4 (Design Memo M4, DD-4): backend が宣言する旋律テイクの帯域自己記述。
# `melody.routing.INPUT_KINDS` と同じ語彙（校正済み集合はこのうち
# `{"clear_lead"}` のみ — M4 実装契約 §1 DD-4 / G2）。
MelodyTakeBand = Literal["vocal_track", "clear_lead", "full_mix", "chord_pad_no_melody"]

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
    # M4 (DD-4, additive): この backend が生成するテイクの旋律帯域の自己記述。
    # 機械検出不能なので宣言 + fail-closed 既定でゲート化する（G2）:
    # `None` または校正済み集合 `{"clear_lead"}` 外は
    # `recast/experimental.py` の G2 が `not_observed(reason:
    # band_out_of_validation(...))` に落とす。既存 backend 定義への影響は
    # ゼロ（省略時 `None` は「宣言なし」であり M4 experimental 経路の
    # opt-in を一切変えない）。
    melody_take_band: Optional[MelodyTakeBand] = None


class RecastPolicy(RecastModel):
    """capability 突合の厳格度と、実行前提とする既存契約の要求。"""

    capability_mode: CapabilityMode
    require_author_fields_resolved: bool = True
    require_verified_package: bool = True


class MelodyObservationConfig(RecastModel):
    """M4 (DD-10): melody anchor の experimental 観測に要る project 側設定。

    axis_policy を宣言した melody anchor があっても本 config が無ければ
    `recast/experimental.py` は `not_observed(reason: melody_config_missing)`
    へ落とす（エラーにしない・正直会計 — `ObservationConfig.melody` docstring
    参照）。
    """

    reference: Literal["score", "audio"] = "score"
    # `reference == "audio"` のときのみ必須（project 相対パス）。
    reference_audio: Optional[str] = None
    comparison_registry: str
    m1_registry: str
    route: str = "pyin_direct"

    @model_validator(mode="after")
    def _validate_reference_audio_pairing(self) -> "MelodyObservationConfig":
        if self.reference == "audio" and self.reference_audio is None:
            raise ValueError(
                "MelodyObservationConfig: reference='audio' requires reference_audio "
                "to be set"
            )
        if self.reference == "score" and self.reference_audio is not None:
            raise ValueError(
                "MelodyObservationConfig: reference='score' forbids reference_audio "
                f"(got {self.reference_audio!r}) — score_reference derives the reference "
                "melody from the identity sidecar's note-events artifact, not audio"
            )
        return self

    @model_validator(mode="after")
    def _validate_route(self) -> "MelodyObservationConfig":
        # M4 DD-5: 既定 route は pyin_direct（重み不要・M1c 確立経路）。route
        # は「clear_lead」帯の候補経路名集合内のみ許可する（load 時
        # fail-closed）——校正済み帯域が clear_lead のみ（G2）である以上、
        # テイク側の抽出経路もその帯域で試すべき経路に限る。
        allowed_routes = {route.name for route in select_routes("clear_lead")}
        if self.route not in allowed_routes:
            raise ValueError(
                f"MelodyObservationConfig: route={self.route!r} is not a 'clear_lead' "
                f"route (expected one of {sorted(allowed_routes)})"
            )
        return self


class ObservationConfig(RecastModel):
    """生成後観測（`arrange/observe.py` 系列）への anchor 参照集合。"""

    enabled: bool
    anchors: List[str]
    # M4 (DD-10, additive): melody anchor の experimental 観測設定。省略時
    # `None` は「melody 観測設定なし」——axis_policy 付き melody anchor が
    # 契約に存在しても、これが無ければ M4 は起動せず
    # `not_observed(reason: melody_config_missing)` になる（`recast/
    # experimental.py` 参照）。既存プロジェクト定義（この欄を持たない）は
    # 完全後方互換。
    melody: Optional[MelodyObservationConfig] = None

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


class ModeOverrideEntry(RecastModel):
    """1 (invocation_mode, score field path) 組の実測サポート状況（★invocation_mode 軸）。

    `InputCapabilityProfile`（generator が受け取れるチャネルの自己記述）とは別軸:
    こちらは「同じ generator でも invocation_mode（cover / prompt_only）が変わると
    同じ canonical path の変更がどれだけ届くか」の実測記録であり、既存
    `CapabilityProfile` / `control_profile` スキーマを一切変更しない別 config。
    """

    support: OverrideSupport
    evidence: Optional[str] = None
    note: Optional[str] = None


class ModeOverridesConfig(RecastModel):
    """generator 単位の mode-overrides/0.1 設定。

    ``modes`` の内側 dict のキーは canonical score field path（`resolver.CANONICAL_PATHS`
    の allowlist に含まれるもののみ）。未知 path は fail-closed で拒否する。
    """

    schema_version: Literal["mode-overrides/0.1"]
    generator: str
    modes: Dict[InvocationMode, Dict[str, ModeOverrideEntry]]

    @model_validator(mode="after")
    def _validate_score_field_paths(self) -> "ModeOverridesConfig":
        allowed = frozenset(CANONICAL_PATHS)
        for mode_name, entries in self.modes.items():
            unknown = sorted(set(entries) - allowed)
            if unknown:
                raise ValueError(
                    f"mode overrides ({self.generator!r}): mode {mode_name!r} references "
                    f"unknown score field path(s) not in CANONICAL_PATHS: {', '.join(unknown)}"
                )
        return self
