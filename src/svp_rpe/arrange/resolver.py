"""ArrangementSpec の決定論的 Resolver。

既存 `CompositionScore` を変更せず、`ArrangementSpec` の部分 override と
preservation policy から `DerivedScore`（`CompositionScore.model_validate` を
通った新規オブジェクト）と field-level diff を導出する core API。

``hard`` は本 resolver における「変更を許可しない」という preservation policy
であり、生成器（Suno / MusicGen 等）が実際にその物理量を厳密に守ることを保証する
ものではない（それは `CompositionScore.control_profile` の grip_class が別途担う
実測ベースの契約である）。本モジュールは `CompositionScore.fixity` を読み書きせず、
source から derived へそのままコピーするだけで、fixity の値から preservation
mode を自動決定することはしない。

resolve flow:

    source.model_dump(mode="json") で deep copy
    -> target override を適用
    -> 明示 override path を導出
    -> preservation policy を検証
       - hard   + 変更あり -> ArrangementConflictError
       - hard   + 変更なし -> 許可、change record なし
       - elastic/free + 変更あり -> change record
       - policy 未指定      -> ArrangementPolicyError
    -> CompositionScore.model_validate(candidate)
    -> path の安定ソート
    -> ArrangementResolution
"""
from __future__ import annotations

import copy
from typing import Any

from svp_rpe.arrange.models import ArrangementChange, ArrangementResolution, ArrangementSpec
from svp_rpe.compose.models import CompositionScore

# 明示定数 allowlist。introspection による自動導出は禁止（Design Memo 契約）。
# scalar / nested scalar は leaf path、list 型欄 (avoid / priority / chord_progression /
# structure) は atomic path として扱う（部分 patch なし・全体置換のみ）。
CANONICAL_PATHS: tuple[str, ...] = (
    "semantic.core",
    "semantic.grv.primary",
    "semantic.grv.secondary",
    "semantic.delta_e.overall",
    "semantic.avoid",
    "semantic.lyrics_presence",
    "physical.bpm",
    "physical.key",
    "physical.time_signature",
    "physical.active_rate_target",
    "physical.valley_depth_target",
    "physical.brightness",
    "physical.stereo_width",
    "rendering.target_backend",
    "rendering.prompt_max_chars",
    "rendering.priority",
    "events.chord_progression",
    "structure",
)
_ALLOWED_PATHS = frozenset(CANONICAL_PATHS)
_PATH_ORDER = {path: index for index, path in enumerate(CANONICAL_PATHS)}


class ArrangementError(Exception):
    """ArrangementSpec 解決に関するエラーの基底クラス。"""


class ArrangementPolicyError(ArrangementError):
    """preservation policy が欠落・不正（未知 path / 親 path のみの指定）な場合。"""


class ArrangementConflictError(ArrangementError):
    """``hard`` path の値が source と異なる値へ override されようとした場合。"""


def resolve_arrangement(source: CompositionScore, spec: ArrangementSpec) -> ArrangementResolution:
    """`source` を変更せず、`spec` に基づき derived `CompositionScore` と diff を導出する。

    `source` および `source` 配下の list / dict は一切 mutation されない
    （`model_dump(mode="json")` の結果をさらに `copy.deepcopy` して独立オブジェクト
    として扱う）。
    """

    arrangement_id = spec.meta.id
    _validate_preservation_paths(spec, arrangement_id)

    source_json = source.model_dump(mode="json")
    candidate = copy.deepcopy(source_json)

    explicit_overrides = _collect_explicit_overrides(spec)

    changes: list[ArrangementChange] = []
    for path, new_value in explicit_overrides.items():
        mode = spec.preservation.score_fields.get(path)
        if mode is None:
            raise ArrangementPolicyError(
                f"arrangement '{arrangement_id}': path '{path}' has no preservation policy "
                "in preservation.score_fields"
            )

        before = copy.deepcopy(_get_path(source_json, path))
        after = copy.deepcopy(new_value)
        _set_path(candidate, path, after)

        if before == after:
            continue

        if mode == "hard":
            raise ArrangementConflictError(
                f"arrangement '{arrangement_id}': hard-preserved path '{path}' would change "
                f"from {before!r} to {after!r}"
            )

        changes.append(
            ArrangementChange(path=path, before=before, after=after, preservation_mode=mode)
        )

    derived = CompositionScore.model_validate(candidate)
    changes.sort(key=lambda change: _PATH_ORDER[change.path])

    return ArrangementResolution(source_score=source, derived_score=derived, changes=changes)


def _validate_preservation_paths(spec: ArrangementSpec, arrangement_id: str) -> None:
    """preservation.score_fields の全 key が canonical leaf path であることを検証する。

    親 path のみの曖昧指定（例 ``semantic.grv``、``physical``）や存在しない field
    path は allowlist に含まれないため、ここで一括して弾かれる。
    """

    unknown = sorted(set(spec.preservation.score_fields) - _ALLOWED_PATHS)
    if unknown:
        raise ArrangementPolicyError(
            f"arrangement '{arrangement_id}': unknown preservation path(s): {', '.join(unknown)}"
        )


def _collect_explicit_overrides(spec: ArrangementSpec) -> dict[str, Any]:
    """`spec.target` で明示された canonical leaf path -> JSON 互換の新値を導出する。"""

    values: dict[str, Any] = {}
    target = spec.target

    if target.semantic is not None:
        semantic = target.semantic
        if semantic.core is not None:
            values["semantic.core"] = semantic.core
        if semantic.grv is not None:
            if semantic.grv.primary is not None:
                values["semantic.grv.primary"] = semantic.grv.primary
            if semantic.grv.secondary is not None:
                values["semantic.grv.secondary"] = semantic.grv.secondary
        if semantic.delta_e is not None and semantic.delta_e.overall is not None:
            values["semantic.delta_e.overall"] = semantic.delta_e.overall
        if semantic.avoid is not None:
            values["semantic.avoid"] = list(semantic.avoid)
        if semantic.lyrics_presence is not None:
            values["semantic.lyrics_presence"] = semantic.lyrics_presence

    if target.physical is not None:
        physical = target.physical
        for field in (
            "bpm",
            "key",
            "time_signature",
            "active_rate_target",
            "valley_depth_target",
            "brightness",
            "stereo_width",
        ):
            value = getattr(physical, field)
            if value is not None:
                values[f"physical.{field}"] = value

    if target.rendering is not None:
        rendering = target.rendering
        if rendering.target_backend is not None:
            values["rendering.target_backend"] = rendering.target_backend
        if rendering.prompt_max_chars is not None:
            values["rendering.prompt_max_chars"] = rendering.prompt_max_chars
        if rendering.priority is not None:
            values["rendering.priority"] = list(rendering.priority)

    if target.events is not None and target.events.chord_progression is not None:
        values["events.chord_progression"] = [
            chord.model_dump(mode="json") for chord in target.events.chord_progression
        ]

    if target.structure is not None:
        values["structure"] = [section.model_dump(mode="json") for section in target.structure]

    return values


def _get_path(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value
