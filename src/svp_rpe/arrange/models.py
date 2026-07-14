"""ArrangementSpec: 既存 CompositionScore を破壊せず部分上書きするための入力スキーマ。

`ArrangementModel` を基底として `extra="forbid"` を継承する（未知 key は fail-fast
で拒否する。CompositionScore 側の `CompositionModel` と同じ規約）。ここで定義する
モデルは canonical `CompositionScore` schema を一切変更しない — override 用の
別スキーマとして独立に存在する。

`None` は「override なし（source の値をそのまま使う）」を意味し、既存値を
明示的に null へ上書きする用途は扱わない。
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, JsonValue

from svp_rpe.compose.models import ChordSpec, CompositionScore, StructureSection

PreservationMode = Literal["hard", "elastic", "free"]


class ArrangementModel(BaseModel):
    """arrangement 側スキーマの共通基底。未知 key を拒否する。"""

    model_config = ConfigDict(extra="forbid")


class GrvOverride(ArrangementModel):
    primary: Optional[str] = None
    secondary: Optional[str] = None


class DeltaEOverride(ArrangementModel):
    overall: Optional[str] = None


class SemanticOverride(ArrangementModel):
    core: Optional[str] = None
    grv: Optional[GrvOverride] = None
    delta_e: Optional[DeltaEOverride] = None
    avoid: Optional[List[str]] = None
    lyrics_presence: Optional[Literal["present", "absent"]] = None


class PhysicalOverride(ArrangementModel):
    bpm: int | str | None = None
    key: Optional[str] = None
    time_signature: Optional[str] = None
    active_rate_target: Optional[str] = None
    valley_depth_target: Optional[str] = None
    brightness: Optional[str] = None
    stereo_width: Optional[str] = None


class RenderingOverride(ArrangementModel):
    target_backend: Optional[str] = None
    prompt_max_chars: Optional[int] = None
    priority: Optional[List[str]] = None


class EventOverride(ArrangementModel):
    chord_progression: Optional[List[ChordSpec]] = None


class ArrangementTarget(ArrangementModel):
    """CompositionScore の semantic / physical / rendering / events / structure を
    部分的に上書きする値の集合。`structure` と各 list 型欄 (avoid / priority /
    chord_progression) は atomic field として扱う — 部分 patch は実装しない
    （list の要素単位マージは意味的に曖昧なため、常に全体置換）。
    """

    semantic: Optional[SemanticOverride] = None
    physical: Optional[PhysicalOverride] = None
    rendering: Optional[RenderingOverride] = None
    events: Optional[EventOverride] = None
    structure: Optional[List[StructureSection]] = None


class PreservationSpec(ArrangementModel):
    """canonical field path -> PreservationMode の対応表。

    path の allowlist 検証は resolver 側の明示定数で行う（本モデルは容れ物のみ）。
    """

    score_fields: dict[str, PreservationMode]


class ArrangementMeta(ArrangementModel):
    id: str
    version: str | float
    description: Optional[str] = None


class ArrangementSpec(ArrangementModel):
    meta: ArrangementMeta
    target: ArrangementTarget
    preservation: PreservationSpec


class ArrangementChange(ArrangementModel):
    """1 leaf path の変更記録（JSON 互換形へ正規化済みの before/after）。"""

    path: str
    before: JsonValue
    after: JsonValue
    preservation_mode: Literal["elastic", "free"]


class ArrangementResolution(ArrangementModel):
    """`resolve_arrangement` の戻り値: source / derived Score と field-level diff。"""

    source_score: CompositionScore
    derived_score: CompositionScore
    changes: List[ArrangementChange]
