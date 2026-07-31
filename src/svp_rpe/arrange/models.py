"""ArrangementSpec: 既存 CompositionScore を破壊せず部分上書きするための入力スキーマ。

`ArrangementModel` を基底として `extra="forbid"` を継承する（未知 key は fail-fast
で拒否する。CompositionScore 側の `CompositionModel` と同じ規約）。ここで定義する
モデルは canonical `CompositionScore` schema を一切変更しない — override 用の
別スキーマとして独立に存在する。

`None` は「override なし（source の値をそのまま使う）」を意味し、既存値を
明示的に null へ上書きする用途は扱わない。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from svp_rpe.compose.models import ChordSpec, CompositionScore, StructureSection

PreservationMode = Literal["hard", "elastic", "free"]

# AR2-2/AR2-3: identity anchor に許容する変形の語彙。
# AR2-2 が定義した最初の 7 語（octave_displacement..instrumental_break）に、
# AR2-3（design_memo_ar2_3.md）が structure domain 向けに 3 語を追加した:
# section_insertion / section_omission / section_repetition。この 3 語は
# 推測補完ではなく、#193 の実 form 実測（MusicGen 30s ×
# examples/arrangement/midnight_signal/observed/musicgen_form/）が観測した
# 変形カテゴリそのもの — 挿入（正典に無い outro の観測）・欠落（verse が
# 2 take とも一度も観測されない）・反復（chorus×2）に由来する。
# 語彙追加は Design Memo 経由に限る（ここで勝手に増やさない）。
AllowedTransformation = Literal[
    "octave_displacement",
    "ornamentation",
    "timing_warp",
    "chord_extensions",
    "functional_substitution",
    "intro_extension",
    "instrumental_break",
    "section_insertion",
    "section_omission",
    "section_repetition",
]

# pydantic>=2.0 下限では pydantic.JsonValue (2.5+) が使えないためローカル定義。
# before/after は resolver が model_dump(mode="json") で正規化済みの JSON 互換値のみを格納する。
JsonValue = Any


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


class AnchorPreservation(ArrangementModel):
    """IdentityManifest の anchor 単位に宣言する保持方針（AR2-2）。

    `mode` と `allow` の整合はここで強制する: ``hard`` は変形を一切許さない
    ため `allow` は空必須、``free`` は変形を個別列挙で制限しないため
    `allow` は空必須、``elastic`` は変形の語彙を最低 1 件宣言することを
    必須とする（空の elastic は「何でも許す」への推測補完になるため拒否する）。

    domain 別にどの変形語彙が許容されるか（`AllowedTransformation` のうち
    どの部分集合か）は本モデルでは検証しない — anchor の domain と
    `arrange/contract.py` の `DOMAIN_ALLOWED_TRANSFORMS` を突き合わせる
    cross-validation は `build_preservation_contract` の責務である
    （本モデル単体では anchor の domain を知らないため）。

    `tolerance_profile` は AR4 が意味論を定義するまで opaque な文字列として
    保持するのみで、本モデルは値を検証・解釈しない。

    `axis_policy`（M4 DD-7、additive）は軸単位の保持方針を宣言する
    opt-in 欄: 契約 anchor にこのフィールドが存在すること自体が
    `recast/experimental.py` の M4 experimental 経路への opt-in になる
    （`None` の既定は「axis_policy 宣言なし」＝現行の `_observe_melody`
    LCS・本会計をそのまま維持する）。domain 別にどの軸名が許容されるか
    （`arrange/contract.py` の `DOMAIN_AXIS_VOCAB`）は本モデルでは検証しない
    — anchor の domain を知らないため、`mode`/`allow` の domain 語彙検証と
    同じ理由で `build_preservation_contract` の cross-validation の責務と
    する。本モデルは domain 非依存の 2 条件（非空・hard/elastic 最低 1 軸）
    のみを検証する。
    """

    mode: PreservationMode
    allow: List[AllowedTransformation] = []
    tolerance_profile: Optional[str] = None
    axis_policy: Optional[Dict[str, PreservationMode]] = None

    @model_validator(mode="after")
    def _validate_mode_allow_consistency(self) -> "AnchorPreservation":
        if self.mode in ("hard", "free") and self.allow:
            reason = (
                "hard forbids all transformations"
                if self.mode == "hard"
                else "free does not constrain transformations by enumeration"
            )
            raise ValueError(
                f"AnchorPreservation: mode={self.mode!r} must have an empty 'allow' list "
                f"({reason}), got {self.allow!r}"
            )
        if self.mode == "elastic" and not self.allow:
            raise ValueError(
                "AnchorPreservation: mode='elastic' requires at least one entry in "
                "'allow' (an empty allow list would make the contract vacuous)"
            )
        return self

    @model_validator(mode="after")
    def _validate_axis_policy_shape(self) -> "AnchorPreservation":
        """domain 非依存の 2 条件のみ（domain 語彙検証は
        `build_preservation_contract` / `ContractAnchor` の責務。docstring
        参照）。"""
        if self.axis_policy is not None:
            if not self.axis_policy:
                raise ValueError(
                    "AnchorPreservation: axis_policy must not be empty (omit the field "
                    "entirely to declare no axis_policy)"
                )
            if not any(mode in ("hard", "elastic") for mode in self.axis_policy.values()):
                raise ValueError(
                    "AnchorPreservation: axis_policy must declare at least one 'hard' or "
                    "'elastic' axis (an all-'free' policy would promise nothing)"
                )
        return self


class PreservationSpec(ArrangementModel):
    """canonical field path -> PreservationMode の対応表 + identity anchor 単位の保持方針。

    `score_fields` の path allowlist 検証は resolver 側の明示定数で行う
    （本モデルは容れ物のみ）。`identity_anchors` は AR2-2 で additive に
    追加された欄で、IdentityManifest の anchor id -> `AnchorPreservation` の
    対応表を宣言する（省略時 `None` は「anchor policy 宣言なし」を意味し、
    既存 spec の後方互換をそのまま保つ）。
    """

    score_fields: dict[str, PreservationMode]
    identity_anchors: Optional[dict[str, AnchorPreservation]] = None


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
