"""Generator device profile — 機種ごとの癖を構造化する記述子（PR3 後半）。

K2 grip（tight フィールドの既定値）/ K3-2a 非対角クロス効果（未解決記録）/
genre calibration バイアス（over-brightening 等の方向所見）を 1 生成器 = 1 YAML に
まとめ、コンパイル時に 2 経路で `ExternalPromptAdapter` へ接続する
（配線は `compose/prompt_renderer.py`、詳細は `docs/control_profile.md`）:

(a) `control_profile` のフィールド欠落を `control_defaults` で補完する
    （score の宣言が常に勝つ — このブロックは score が黙っているときだけ効く）。
(b) score の指定値が既知の癖（`knob_quirks`）に当たったら
    `GeneratedPrompt.advisories` へ警告を出す（プロンプト本文 / tags は一切変えない
    ＝自動補正はしない。計器であって補正器ではない）。

K3-2a の非対角クロス結合（例: bpm→spectral_centroid）は生成器で符号が反転する
（`controllability_poc.md` §5.4）＝機種ごとの癖は普遍則ではない、という実証的動機が
本モデルの背景にある。`cross_couplings` は記録専用で advisory は出さない
（R=4・dead 行なしのためセル単位の解釈はまだ未解決）。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import field_validator

from svp_rpe.compose.models import (
    SEMANTIC_CONTROL_FIELDS,
    CompositionModel,
    ControlGrip,
    PhysicalLayer,
)
from svp_rpe.utils.config_loader import load_config

# デバイスプロファイルが観測ステータスとして自己記述する語彙。
# observed=直接実証済み（K2/K3 grip）/ directional=方向は一定だが量はジャンル依存（genre
# calib）/ unresolved=記録のみで解釈確定していない（K3-2a 非対角セル）。
QuirkStatus = Literal["observed", "directional", "unresolved"]

# control_defaults の許可キー集合は control_profile と同一（PhysicalLayer フィールド ∪
# 意味層制御フィールド）。fixity と異なり網羅必須は引き継がない（疎を許容）。
_CONTROL_DEFAULT_ALLOWED_FIELDS = frozenset(PhysicalLayer.model_fields) | SEMANTIC_CONTROL_FIELDS


class KnobQuirk(CompositionModel):
    """1 生成器・1 ノブの既知の癖。advisory 発火条件を自己記述する。"""

    field: str
    description: str
    status: QuirkStatus
    evidence: str
    # スコア値の完全一致（str() 比較）で advisory 発火。
    applies_to_values: list[str] = []
    # 数値フィールド（現状 bpm のみ）向けの閾値発火。TODO センチネル文字列はスキップする。
    applies_below: Optional[float] = None
    applies_above: Optional[float] = None
    # 発火時に出す文言。None なら記録のみで advisory は出さない。
    advisory: Optional[str] = None


class CrossCoupling(CompositionModel):
    """K3-2a §5.4 の非対角クロス効果の記録。advisory は出さない（未解決データの保全のみ）。"""

    knob: str
    sensor: str
    effect: float
    status: QuirkStatus
    evidence: str


class SpectralBias(CompositionModel):
    """genre calibration（Phase C）由来の生成器バイアス所見。単一補正係数化はしない。"""

    name: str
    description: str
    direction: str
    status: QuirkStatus
    evidence: str


class DeviceProfile(CompositionModel):
    """1 生成器の癖の自己記述。`control_profile` を補完し、advisory を駆動する。"""

    schema_version: str
    generator: str
    control_defaults: dict[str, ControlGrip] = {}
    knob_quirks: list[KnobQuirk] = []
    cross_couplings: list[CrossCoupling] = []
    spectral_biases: list[SpectralBias] = []
    notes: Optional[str] = None

    @field_validator("control_defaults")
    @classmethod
    def validate_control_defaults_keys(
        cls, value: dict[str, ControlGrip]
    ) -> dict[str, ControlGrip]:
        unknown = sorted(set(value) - _CONTROL_DEFAULT_ALLOWED_FIELDS)
        if unknown:
            raise ValueError(
                "control_defaults field keys must be CompositionScore.physical fields "
                f"or semantic control fields; unknown keys: {', '.join(unknown)}"
            )
        return value


def load_device_profile(generator: str) -> Optional[DeviceProfile]:
    """`device_profiles/<generator>.yaml` を local→packaged パターンで読む。

    ファイルが無ければ ``None``（フォールバックチェーン。未知生成器は正当に
    「プロファイル無し」）。読めた場合のスキーマ違反は fail-fast で例外を通す
    （transitive なパースエラーを握りつぶさない）。
    """

    try:
        data = load_config(f"device_profiles/{generator}")
    except FileNotFoundError:
        return None
    return DeviceProfile.model_validate(data)
