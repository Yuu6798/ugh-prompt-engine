"""authoring/contract.py — L0a 著述契約 spec (D-L0a-1) の pydantic モデル + loader.

正本 = `config/authoring_contract_l0.yaml`（`docs/l0a_authoring_contract.md` が
文書として参照する）。spec 自体の妥当性（未知キー拒否・型）はここで検証する
（extra="forbid" — spec ファイル自体がタイプミスで壊れていても黙って無視しない）。
実際にスコアへ適用する側のロジックは `authoring/validate.py`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict

from svp_rpe.utils.config_loader import load_config

SCHEMA_VERSION = "authoring-contract/1.0"

FieldType = Literal["str", "int", "list_str"]


class FieldSpec(BaseModel):
    """1 フィールドの型狭窄 + 任意の列挙/リテラル/形式正規表現。

    `enum`/`literal`/`format` は `type` が先に確認された（値が実際にその型
    である）場合のみ適用される—— 型違反と列挙/リテラル/形式違反を二重報告
    しない、という `validate_score.py` の非重複規約をそのまま踏襲する。
    """

    model_config = ConfigDict(extra="forbid")

    type: FieldType
    enum: Optional[list[str]] = None
    literal: Optional[str] = None
    format: Optional[str] = None


class ObjectSpec(BaseModel):
    """1 階層のオブジェクトが公開するキー集合 + 各フィールドの型狭窄。

    `fields` に列挙されないキー（例: `structure`（トップレベル）や
    `events.chord_progression`）は「キーの存在だけを許可し、値の型は下の
    階層 spec が別途検査する」コンテナ扱い。
    """

    model_config = ConfigDict(extra="forbid")

    allowed_keys: list[str]
    fields: dict[str, FieldSpec] = {}


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
