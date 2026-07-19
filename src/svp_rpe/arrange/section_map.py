"""section-map artifact schema, shared by `identity.py` and `observe.py` (AR2-3).

`identity.py` and `observe.py` have a fixed one-way import direction
(`observe.py` imports `identity.py`; `identity.py` never imports `observe.py` —
sidecar-first import boundary, `docs/arrangement_identity_planning.md` D1).
AR2-3's `section_ref` resolution needs `identity.py` to read the exact same
section-map artifact schema `observe.py`'s structure sensor already parses.
Putting the schema in a third module that neither consumer depends on avoids
both a circular import and a second, potentially drifting, copy of the schema.

Two format versions coexist deliberately — not an implicit migration
(structure Design Memo / AR2-3 Design Memo `design_memo_ar2_3.md`):

- ``section-map/0.1`` (`SectionMapArtifact`): an ordered label list, no id
  concept. This is the format the committed, hash-pinned fixture
  (`examples/arrangement/midnight_signal/identity/section_map.json`) already
  uses; that fixture is never touched or migrated by this module — a 0.1
  artifact never gains ids, and is never read as if it were 0.2.
- ``section-map/0.2`` (`SectionMapArtifactV2`): additive. Each section carries
  a stable ``id`` (unique within the artifact) alongside its ``label``,
  letting another anchor's `IdentityAnchor.section_ref` resolve to a specific
  section instead of naming it only by (unstable, order-dependent) label text.

Both parsers share the same fail-closed posture `_load_chord_progression`
(``observe.py``) established for anchor artifact content: unknown keys, an
empty ``sections`` list, and a ``schema_version`` mismatch are all rejected at
parse time (``ValueError``, with the offending value visible in the message)
rather than silently accepted or coerced.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SECTION_MAP_ARTIFACT_SCHEMA_0_1 = "section-map/0.1"
SECTION_MAP_ARTIFACT_SCHEMA_0_2 = "section-map/0.2"


class SectionMapModel(BaseModel):
    """section-map artifact スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")


class SectionMapArtifact(SectionMapModel):
    """``section-map/0.1`` anchor artifact — 正典セクションラベルの順序付き列。

    id の概念を持たない（AR2-3 より前からの既存形式。委譲された stable ID は
    `SectionMapArtifactV2` の専管であり、この 0.1 モデルへ後付けしない）。
    """

    schema_version: Literal["section-map/0.1"]
    sections: list[str] = Field(min_length=1)


class SectionEntry(SectionMapModel):
    """``section-map/0.2`` の 1 セクション: artifact 内で一意な安定 id + ラベル。"""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class SectionMapArtifactV2(SectionMapModel):
    """``section-map/0.2`` anchor artifact — 安定 id 付きセクションの順序付き列。

    ``id`` は artifact 内で一意でなければならない（fail-closed）。順序は
    ``sections`` の記載順そのものが正典順を表す（0.1 と同じ契約）。
    """

    schema_version: Literal["section-map/0.2"]
    sections: list[SectionEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> "SectionMapArtifactV2":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in self.sections:
            if entry.id in seen:
                duplicates.add(entry.id)
            seen.add(entry.id)
        if duplicates:
            raise ValueError(
                "duplicate section id(s) in section-map/0.2 artifact: "
                f"{', '.join(sorted(duplicates))}"
            )
        return self


def _parse_json_mapping(raw_bytes: bytes, *, artifact_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"section map artifact is not valid JSON: {artifact_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            "section map artifact must be a mapping with 'schema_version' and "
            f"'sections' keys: {artifact_path}"
        )
    return payload


def parse_section_map_artifact_0_1(
    raw_bytes: bytes, *, artifact_path: Path
) -> SectionMapArtifact:
    """``section-map/0.1`` artifact (JSON) を検証済みモデルへ変換する（fail-closed）。

    ``raw_bytes`` は呼び出し側が既に hash 照合済みの bytes を渡す想定（本関数は
    一切ファイルを読まない — ``observe.py`` の既存 artifact loader 群と同じ契約）。
    未知キー・空 ``sections``・``schema_version`` 不一致はいずれも ``ValueError``
    で拒否する。
    """
    payload = _parse_json_mapping(raw_bytes, artifact_path=artifact_path)
    try:
        return SectionMapArtifact.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"section map artifact failed validation: {artifact_path}: {exc}"
        ) from exc


def parse_section_map_artifact_0_2(
    raw_bytes: bytes, *, artifact_path: Path
) -> SectionMapArtifactV2:
    """``section-map/0.2`` artifact (JSON) を検証済みモデルへ変換する（fail-closed）。

    契約は `parse_section_map_artifact_0_1` と同型。加えて id の artifact 内
    一意性を `SectionMapArtifactV2` 自身の validator が強制する。
    """
    payload = _parse_json_mapping(raw_bytes, artifact_path=artifact_path)
    try:
        return SectionMapArtifactV2.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"section map artifact failed validation: {artifact_path}: {exc}"
        ) from exc
