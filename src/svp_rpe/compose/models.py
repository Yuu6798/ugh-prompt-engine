"""Pydantic models for author-facing Composition Score YAML."""
from __future__ import annotations

from typing import Any, List, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from svp_rpe.rpe.physical_features import CHORD_NAMES
from svp_rpe.sentinels import TODO_SENTINEL_PREFIX, is_todo_sentinel

FixityState = Literal["locked", "unlocked"]
EVENT_FIXITY_FIELDS = frozenset({"chord_progression"})

# SEM-1: control_profile が許可する意味層の制御チャネル。PhysicalLayer フィールドと違い
# fixity 対象外（意味層は fixity の全網羅制約に含めない — docs/control_profile.md 参照）。
SEMANTIC_CONTROL_FIELDS = frozenset({"lyrics_presence"})

# grip_class: 楽譜フィールドが当該生成器でどれだけ「効く」かの実証ラベル。
# tight=保証チャネル / loose=助言（弱い grip）/ dead=無効（センサー盲 or ツマミ死）。
GripClass = Literal["tight", "loose", "dead"]


class CompositionModel(BaseModel):
    """Base model that rejects keys outside the canonical Composition Score schema."""

    model_config = ConfigDict(extra="forbid")


class Meta(CompositionModel):
    title: str
    version: str | float

    @field_validator("version")
    @classmethod
    def normalize_version(cls, value: str | float) -> str:
        return str(value)


class GrvSpec(CompositionModel):
    primary: str
    secondary: str


class DeltaESpec(CompositionModel):
    overall: str


class SemanticLayer(CompositionModel):
    core: str
    grv: GrvSpec
    delta_e: DeltaESpec
    avoid: List[str]
    # SEM-1: 歌詞の有無を意味層の制御チャネルとして自己記述する（control_profile 許可キー）。
    # grip 証拠は現状 loose（docs/lyrics_semantic_anchor.md n=3 追試）— tight は主張しない。
    lyrics_presence: Literal["present", "absent"] | None = None

    @model_serializer(mode="wrap")
    def serialize_without_empty_optionals(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.lyrics_presence is None:
            data.pop("lyrics_presence", None)
        return data


class PhysicalLayer(CompositionModel):
    bpm: int | str
    key: str
    time_signature: str
    active_rate_target: str
    valley_depth_target: str
    brightness: str
    stereo_width: str

    @field_validator("bpm", mode="before")
    @classmethod
    def normalize_bpm(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            signless = stripped[1:] if stripped[:1] in {"+", "-"} else stripped
            if signless.isdigit():
                return int(stripped)
        return value

    @field_validator("bpm")
    @classmethod
    def reject_unknown_bpm_text(cls, value: int | str) -> int | str:
        if isinstance(value, str) and not value.startswith(TODO_SENTINEL_PREFIX):
            raise ValueError(
                "bpm must be an integer, numeric string, or TODO(transcribe): sentinel"
            )
        return value


class ControlGrip(CompositionModel):
    """単一生成器における 1 物理フィールドの制御信頼度（K 系列 grip 由来）。"""

    grip_class: GripClass
    grip: float | None = None  # 効果量 d（K 系列 measure_grip 出力）
    sensor: str | None = None  # 観測センサー名（例: spectral_centroid）
    evidence: str | None = None  # 出所参照（例: examples/control/k2/expected_grip.json）

    @model_serializer(mode="wrap")
    def serialize_without_empty_optionals(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        for field in ("grip", "sensor", "evidence"):
            if getattr(self, field) is None:
                data.pop(field, None)
        return data


# GeneratorProfile = dict[field_name -> ControlGrip]。field_name は PhysicalLayer のキー。
GeneratorProfile = dict[str, ControlGrip]


class StructureSection(CompositionModel):
    section: str
    bars: int
    role: str
    physical: str


class RenderingConfig(CompositionModel):
    target_backend: str
    prompt_max_chars: int
    priority: List[str]


class ChordSpec(CompositionModel):
    root: str
    quality: Literal["major", "minor"]

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        if value not in CHORD_NAMES:
            allowed = ", ".join(CHORD_NAMES)
            raise ValueError(f"root must be one of CHORD_NAMES: {allowed}")
        return value


class EventLayer(CompositionModel):
    chord_progression: List[ChordSpec] = Field(default_factory=list)


class CompositionScore(CompositionModel):
    meta: Meta
    semantic: SemanticLayer
    physical: PhysicalLayer
    structure: List[StructureSection]
    rendering: RenderingConfig
    events: EventLayer | None = None
    fixity: dict[str, FixityState] | None = None
    # key = 生成器名（"suno" / "musicgen" / ...）。値 = PhysicalLayer フィールド粒度の grip。
    control_profile: dict[str, GeneratorProfile] | None = None

    @field_validator("control_profile")
    @classmethod
    def validate_control_profile_keys(
        cls, value: dict[str, GeneratorProfile] | None
    ) -> dict[str, GeneratorProfile] | None:
        if value is None:
            return None
        allowed_fields = set(PhysicalLayer.model_fields) | SEMANTIC_CONTROL_FIELDS
        for generator, profile in value.items():
            unknown = sorted(set(profile) - allowed_fields)
            if unknown:
                raise ValueError(
                    "control_profile field keys must be CompositionScore.physical "
                    f"fields or semantic control fields; unknown keys for generator "
                    f"'{generator}': {', '.join(unknown)}"
                )
        # fixity と異なり「全 physical フィールド網羅必須」は引き継がない＝疎を許容する
        # （K2 由来の Suno profile は bpm/brightness のみ）。
        return value

    @field_validator("fixity")
    @classmethod
    def validate_fixity_keys(
        cls, value: dict[str, FixityState] | None
    ) -> dict[str, FixityState] | None:
        if value is None:
            return None
        physical_fields = set(PhysicalLayer.model_fields)
        allowed = physical_fields | EVENT_FIXITY_FIELDS
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                "fixity keys must be CompositionScore.physical fields "
                "or supported event fields; "
                f"unknown keys: {', '.join(unknown)}"
            )
        missing = sorted(physical_fields - set(value))
        if missing:
            raise ValueError(
                "fixity must cover every CompositionScore.physical field; "
                f"missing keys: {', '.join(missing)}"
            )
        return dict(value)

    @model_validator(mode="after")
    def validate_fixity_matches_physical_values(self) -> Self:
        if self.fixity is None:
            return self
        mismatches: list[str] = []
        for field, state in self.fixity.items():
            if field in PhysicalLayer.model_fields:
                expected = _fixity_state_for_value(getattr(self.physical, field))
            elif field == "chord_progression":
                expected = _fixity_state_for_chord_progression(self.events)
            else:
                continue
            if state != expected:
                mismatches.append(field)
        if mismatches:
            raise ValueError(
                "fixity states must match score field state; "
                f"mismatched keys: {', '.join(sorted(mismatches))}"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_without_empty_optionals(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.events is None:
            data.pop("events", None)
        if self.fixity is None:
            data.pop("fixity", None)
        if self.control_profile is None:
            data.pop("control_profile", None)
        return data


class GeneratedPrompt(CompositionModel):
    backend: Literal["external", "musicgen", "midi"] = "external"
    text: str
    tags: List[str]
    negative_tags: List[str]
    dropped_elements: List[str]
    # PR3 後半: デバイスプロファイル知見が発火した警告（compose/device_profile.py）。
    # プロンプト本文 / tags には一切反映しない（自動補正しない・advisory のみ）。
    advisories: List[str] = Field(default_factory=list)


def _fixity_state_for_value(value: Any) -> FixityState:
    return "unlocked" if is_todo_sentinel(value) else "locked"


def _fixity_state_for_chord_progression(events: EventLayer | None) -> FixityState:
    if events is not None and events.chord_progression:
        return "locked"
    return "unlocked"
