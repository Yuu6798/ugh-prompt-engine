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

from svp_rpe.melody.representation import _NoDupSafeLoader
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

    `fields ⊆ allowed_keys`（PR #246 Codex P2 review 6 巡目）: spec ロード時
    に強制する——`fields` に `allowed_keys` 外のキー（typo、例
    `bpn`）が紛れていると、`authoring/validate.py:_object_errors` は
    `spec.fields.items()` を回して `_check_field` を呼ぶだけで
    `allowed_keys` 側との整合は取らないため、意図した型/列挙/形式/下限
    制約が**一切適用されない**まま spec のロードだけは通ってしまう
    （`FieldSpec` 側の型×制約ガード群（3 巡目）と同じ「壊れた計器設定は
    ロード時に fail-closed」という節に属する欠陥）。
    """

    model_config = ConfigDict(extra="forbid")

    allowed_keys: list[str]
    fields: dict[str, FieldSpec] = {}
    min_items: Optional[int] = None

    @model_validator(mode="after")
    def _fields_must_be_subset_of_allowed_keys(self) -> Self:
        unknown = sorted(set(self.fields) - set(self.allowed_keys))
        if unknown:
            raise ValueError(
                f"fields declares key(s) not in allowed_keys (typo?): {unknown!r} "
                f"— allowed_keys={sorted(self.allowed_keys)!r}"
            )
        return self


class TopLevelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_keys: list[str]


class AuthoringContractSpec(BaseModel):
    """L0a 著述契約の公開スキーマ spec 全体（`config/authoring_contract_l0.yaml`）。

    `min_items` の適用先限定（PR #246 Codex P2 review 15 巡目、6 巡目
    `fields ⊆ allowed_keys` と同族の spec 内部整合性ガード）: `ObjectSpec.
    min_items` は宣言してもそれだけでは効かない——実際に強制するのは
    `authoring/validate.py` の `_min_items_errors` で、現状この関数は
    `structure`（トップレベルのリスト）× `structure_section`
    （その要素 `ObjectSpec`）という**この 1 組にしか配線されていない**。
    それ以外の `ObjectSpec`（例 `chord`）へ `min_items` を宣言しても
    どこにも参照されない inert な値になり、spec ロードだけは通ってしまう
    ——著者が「最小要素数を強制した」と誤認する壊れた計器設定を防ぐため、
    `structure_section` 以外の `ObjectSpec` フィールドに `min_items` が
    非 `None` で宣言されていたら spec ロード時に `ValidationError` で
    拒否する。**設計判断（拒否 vs 汎用適用）**: `min_items` を汎用的に
    全 `ObjectSpec` へ適用できるよう `validate.py` 側を拡張する選択肢も
    あったが、現行契約 (`config/authoring_contract_l0.yaml`) に
    `structure_section` 以外での要求が存在しないため、要求のない汎用化は
    作らない（YAGNI、12 巡目の非測定系 kind 見送りと同方針）。**境界宣言**:
    `min_items` の適用先はこのガードと `validate.py` の適用実装を 1:1
    で対応させる必要がある——将来 `chord_progression` 等のコンテナへ
    `min_items` を拡張する場合は、適用実装（`_min_items_errors` の配線）
    とこのガードの許可リストを**同時に**更新すること（inert 宣言を
    構造的に作れない状態を維持する）。
    """

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

    @model_validator(mode="after")
    def _min_items_only_supported_on_structure_section(self) -> Self:
        errors = []
        for field_name in ("meta", "semantic", "grv", "delta_e", "physical", "rendering", "events", "chord"):
            object_spec: ObjectSpec = getattr(self, field_name)
            if object_spec.min_items is not None:
                errors.append(
                    f"{field_name}.min_items is declared but not applied — the only "
                    "ObjectSpec wired to a min_items enforcement path in "
                    "authoring/validate.py is structure_section (for the top-level "
                    "structure list); declaring min_items elsewhere would be an inert, "
                    "misleading spec value"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return self


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
    """`--contract` の代替 spec YAML を読む。

    重複キー拒否（PR #246 Codex P2 review 18 巡目 B）: 素の `yaml.safe_load`
    は重複 mapping キーを「最後の値が勝つ」形で静かに受理してしまう——
    計器設定（契約 spec）自体がこの穴を持つと、意図しない `allowed_keys`/
    `fields`/`min`/`min_items` が黙って上書きされうる。`melody.representation.
    _NoDupSafeLoader`（import のみ・重複実装禁止——`recast/experimental.py:
    _load_m1_registry` と同じ再利用パターン）へ差し替える。重複キー検出時は
    `ValueError` を送出するため、既に `not isinstance(data, dict)` と同じ
    `ValueError` 経路——呼び出し側 `load_authoring_contract` の
    `except (OSError, yaml.YAMLError, ValueError, ValidationError)`
    （`svp_rpe.cli.validate_cmd.validate_cmd`）にそのまま乗り、exit 2
    （計器設定の不正）として分類される（`validate_cmd.py` 側の変更は
    不要）。
    """

    with path.open(encoding="utf-8") as handle:
        data = yaml.load(handle, Loader=_NoDupSafeLoader)  # noqa: S506 (dup-key 拒否付き SafeLoader)
    if not isinstance(data, dict):
        raise ValueError(f"authoring contract spec must be a mapping: {path}")
    return data
