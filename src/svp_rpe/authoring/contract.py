"""authoring/contract.py — L0a 著述契約 spec (D-L0a-1) の pydantic モデル + loader.

正本 = `config/authoring_contract_l0.yaml`（`docs/l0a_authoring_contract.md` が
文書として参照する）。spec 自体の妥当性（未知キー拒否・型）はここで検証する
（extra="forbid" — spec ファイル自体がタイプミスで壊れていても黙って無視しない）。
実際にスコアへ適用する側のロジックは `authoring/validate.py`。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional, Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from svp_rpe.utils.config_loader import load_config

SCHEMA_VERSION = "authoring-contract/1.0"

FieldType = Literal["str", "int", "list_str"]


class FieldSpec(BaseModel):
    """1 フィールドの型狭窄 + 任意の列挙/リテラル/形式正規表現/下限。

    `enum`/`literal`/`format`/`min` は `type` が先に確認された（値が実際に
    その型である）場合のみ適用される—— 型違反とこれらの二次制約違反を
    二重報告しない、という `validate_score.py` の非重複規約をそのまま踏襲
    する。

    型 × 制約の対応は spec ロード時（`model_validator`）に強制する
    （PR #246 Codex P2 review 3 巡目: 未検証だと `authoring/validate.py`
    側の `_check_field` が誤った型へ `re.fullmatch`/列挙比較を適用し、
    非構造化クラッシュや常に false になる無意味な制約を生みうる）:

    - `min`（2 巡目）は `type: int` のみに適用可（他 `type` との併用は拒否）。
    - `enum`/`literal`/`format`（3 巡目、`min` と同型のガードへ拡張）は
      `type: str` のみに適用可——`enum`/`literal` は `int`/`list_str` へ
      付与しても実行時クラッシュはしない（型不一致の比較が常に false に
      なるだけ）が、`format` は `type: int` の実値（int）へ
      `re.fullmatch(pattern, value)` を呼ぶと `TypeError` で
      非構造化クラッシュする——3 者とも「str 専用」という同じ意味論の
      制約なので一貫性のため揃って `type: str` 限定にする。
    - `format` 自体の正規表現も spec ロード時に `re.compile` して検証する
      （不正な正規表現、例 `format: '['`、は `re.error` ではなく
      `ValidationError` として exit 2 の運用エラーに落ちる——コンパイル
      結果はキャッシュしない: この spec は起動時に 1 度だけ読まれ、
      `validate.py` 側の `re.fullmatch` 呼び出し頻度もフィールド単位で
      小さいため、決定論と実装の単純さを優先しキャッシュは省略する）。
    """

    model_config = ConfigDict(extra="forbid")

    type: FieldType
    enum: Optional[list[str]] = None
    literal: Optional[str] = None
    format: Optional[str] = None
    min: Optional[int] = None

    @model_validator(mode="after")
    def _min_requires_int_type(self) -> Self:
        if self.min is not None and self.type != "int":
            raise ValueError(f"min is only valid for type='int' fields (got type={self.type!r})")
        return self

    @model_validator(mode="after")
    def _enum_literal_format_require_str_type(self) -> Self:
        if self.type != "str":
            for name, value in (("enum", self.enum), ("literal", self.literal), ("format", self.format)):
                if value is not None:
                    raise ValueError(
                        f"{name} is only valid for type='str' fields (got type={self.type!r})"
                    )
        return self

    @model_validator(mode="after")
    def _format_must_be_a_valid_regex(self) -> Self:
        if self.format is not None:
            try:
                re.compile(self.format)
            except re.error as exc:
                raise ValueError(f"format is not a valid regular expression: {exc}") from exc
        return self


class ObjectSpec(BaseModel):
    """1 階層のオブジェクトが公開するキー集合 + 各フィールドの型狭窄。

    `fields` に列挙されないキー（例: `structure`（トップレベル）や
    `events.chord_progression`）は「キーの存在だけを許可し、値の型は下の
    階層 spec が別途検査する」コンテナ扱い。

    `min_items`（PR #246 Codex P2 review 4 巡目 B）: この `ObjectSpec` が
    リストの各要素を記述する場合（現状唯一の実例 = `structure_section`、
    トップレベル `structure` リストの各セクションを記述する）、リスト自体の
    最小要素数を宣言する。空リストは各要素チェック（`fields`）を素通り
    してしまう（0 回ループ）ため、`fields` だけでは「空リストを許さない」
    制約を表現できない——リストという**コンテナのサイズ**は個々の要素の
    型・値とは別次元の制約であり、`min_items` として `ObjectSpec` 側に
    表現する（`fields`/`allowed_keys` は「1 要素の中身」の制約、
    `min_items` は「要素の個数」の制約、という役割分担）。
    """

    model_config = ConfigDict(extra="forbid")

    allowed_keys: list[str]
    fields: dict[str, FieldSpec] = {}
    min_items: Optional[int] = None


class TopLevelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_keys: list[str]


class AuthoringContractSpec(BaseModel):
    """L0a 著述契約の公開スキーマ spec 全体（`config/authoring_contract_l0.yaml`）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["authoring-contract/1.0"]
    top_level: TopLevelSpec
    meta: ObjectSpec
    semantic: ObjectSpec
    grv: ObjectSpec
    delta_e: ObjectSpec
    physical: ObjectSpec
    structure_section: ObjectSpec
    rendering: ObjectSpec
    events: ObjectSpec
    chord: ObjectSpec


def load_authoring_contract(path: Optional[Path | str] = None) -> AuthoringContractSpec:
    """著述契約 spec を読み込み検証する。

    `path` 省略時は `svp_rpe.utils.config_loader.load_config` の解決順序
    （ローカル `config/` 優先 → パッケージ同梱 `svp_rpe.config` フォールバック）
    で `authoring_contract_l0.yaml` を読む。`path` 指定時はその YAML ファイルを
    直接読む（`svprpe validate --contract <spec.yaml>` が実験用の代替 spec を
    指せるようにするため）。
    """

    if path is None:
        data = load_config("authoring_contract_l0")
    else:
        data = _load_yaml_mapping(Path(path))
    return AuthoringContractSpec.model_validate(data)


def _load_yaml_mapping(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"authoring contract spec must be a mapping: {path}")
    return data
