"""authoring/validate.py — L0a 記号検証ゲート (D-L0a-2).

score dict に対し、(1) `AuthoringContractSpec` が宣言する公開スキーマ範囲
チェック（spec 省略時はスキップ）と (2) canonical `CompositionScore` 検証を
両方かける。`examples/l0s_spike/scripts/validate_score.py`
（`_public_scope_errors`/`_canonical_validation_errors`）のロジックを一般化
した後継——同スクリプト自体は凍結済み歴史的成果物のため変更しない。

エラーは `{where, message, kind}` の決定論ソート済みリストで返す
（`kind` ∈ `public_scope`/`type`/`enum`/`literal`/`format`/`canonical`）。
公開スキーマ範囲チェックの失敗は canonical 検証を短絡しない（両方常に走る、
`validate_score.py` と同じ設計）—— `errors` は公開範囲エラー（`(where,
message)` でソート済み）に続けて canonical エラー（pydantic の `loc` 順、
そのまま）を連結する。
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

from svp_rpe.authoring.contract import AuthoringContractSpec, FieldSpec, ObjectSpec
from svp_rpe.compose.models import CompositionScore

ErrorKind = Literal["public_scope", "type", "enum", "literal", "format", "canonical"]


class AuthoringErrorItem(BaseModel):
    """1 件の記号検証エラー。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    where: str
    message: str
    kind: ErrorKind


class SymbolicValidationResult(BaseModel):
    """`svprpe validate` の結果、および `AuthoringDiffReport.symbolic_validation`
    に埋め込む記号検証の結果。`errors` は fail のときのみ意味を持つ
    （pass 時は `None` のまま——歴史的 `validation.json`/`report.json` の
    `symbolic_validation` が pass 時に `errors` キー自体を持たない後方互換の
    ため、JSON 直列化は `exclude_none` する）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["pass", "fail"]
    errors: Optional[list[AuthoringErrorItem]] = None


OFF_CONTRACT_KEY_MESSAGE = (
    "contract-forbidden key (L0a authoring contract public schema; not published "
    "at this level or any depth)"
)
STR_TYPE_MESSAGE = "must be str per the L0a authoring contract spec"
INT_TYPE_MESSAGE = (
    "must be int per the L0a authoring contract spec; numeric strings and TODO "
    "sentinels are rejected even though canonical CompositionScore coerces them"
)
LIST_STR_TYPE_MESSAGE = "must be a list of str per the L0a authoring contract spec"


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _extra_key_errors(prefix: str, container: Any, allowed: list[str]) -> list[AuthoringErrorItem]:
    if not isinstance(container, dict):
        return []
    allowed_set = frozenset(allowed)
    extra = sorted(str(key) for key in container if key not in allowed_set)
    return [
        AuthoringErrorItem(where=_join(prefix, key), message=OFF_CONTRACT_KEY_MESSAGE, kind="public_scope")
        for key in extra
    ]


def _type_message(field_type: str) -> str:
    if field_type == "str":
        return STR_TYPE_MESSAGE
    if field_type == "int":
        return INT_TYPE_MESSAGE
    return LIST_STR_TYPE_MESSAGE


def _value_matches_type(field_type: str, value: Any) -> bool:
    if field_type == "str":
        return isinstance(value, str)
    if field_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "list_str":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    raise ValueError(f"unknown field type: {field_type}")  # pragma: no cover - guarded by FieldSpec


def _check_field(where: str, container: Any, key: str, spec: FieldSpec) -> list[AuthoringErrorItem]:
    if not isinstance(container, dict) or key not in container:
        return []  # missing entirely is canonical validation's "field required" to report
    value = container[key]
    if not _value_matches_type(spec.type, value):
        return [AuthoringErrorItem(where=where, message=_type_message(spec.type), kind="type")]

    errors: list[AuthoringErrorItem] = []
    if spec.enum is not None and value not in spec.enum:
        errors.append(
            AuthoringErrorItem(
                where=where,
                message=f"must be one of {sorted(spec.enum)!r} per the L0a authoring contract spec",
                kind="enum",
            )
        )
    if spec.literal is not None and value != spec.literal:
        errors.append(
            AuthoringErrorItem(
                where=where,
                message=f"must be {spec.literal!r} per the L0a authoring contract spec (literal)",
                kind="literal",
            )
        )
    if spec.format is not None:
        if re.fullmatch(spec.format, value) is None:
            errors.append(
                AuthoringErrorItem(
                    where=where,
                    message=(
                        f"must match format {spec.format!r} per the L0a authoring contract spec"
                    ),
                    kind="format",
                )
            )
    return errors


def _object_errors(prefix: str, container: Any, spec: ObjectSpec) -> list[AuthoringErrorItem]:
    errors = _extra_key_errors(prefix, container, spec.allowed_keys)
    for key, field_spec in spec.fields.items():
        errors += _check_field(_join(prefix, key), container, key, field_spec)
    return errors


def _public_scope_errors(raw: dict[str, Any], spec: AuthoringContractSpec) -> list[AuthoringErrorItem]:
    errors: list[AuthoringErrorItem] = []
    errors += _extra_key_errors("", raw, spec.top_level.allowed_keys)

    meta = raw.get("meta")
    errors += _object_errors("meta", meta, spec.meta)

    semantic = raw.get("semantic")
    errors += _object_errors("semantic", semantic, spec.semantic)
    grv = semantic.get("grv") if isinstance(semantic, dict) else None
    errors += _object_errors("semantic.grv", grv, spec.grv)
    delta_e = semantic.get("delta_e") if isinstance(semantic, dict) else None
    errors += _object_errors("semantic.delta_e", delta_e, spec.delta_e)

    physical = raw.get("physical")
    errors += _object_errors("physical", physical, spec.physical)

    structure = raw.get("structure")
    if isinstance(structure, list):
        for index, section in enumerate(structure):
            errors += _object_errors(f"structure[{index}]", section, spec.structure_section)

    rendering = raw.get("rendering")
    errors += _object_errors("rendering", rendering, spec.rendering)

    events = raw.get("events")
    if events is not None:
        errors += _object_errors("events", events, spec.events)
        chord_progression = events.get("chord_progression") if isinstance(events, dict) else None
        if isinstance(chord_progression, list):
            for index, chord in enumerate(chord_progression):
                errors += _object_errors(f"events.chord_progression[{index}]", chord, spec.chord)

    return sorted(errors, key=lambda error: (error.where, error.message))


def _canonical_errors(raw: Any) -> list[AuthoringErrorItem]:
    try:
        CompositionScore.model_validate(raw)
    except ValidationError as exc:
        return [
            AuthoringErrorItem(
                where=".".join(str(part) for part in error["loc"]),
                message=error["msg"],
                kind="canonical",
            )
            for error in exc.errors()
        ]
    return []


def validate_score(raw: Any, spec: Optional[AuthoringContractSpec] = None) -> SymbolicValidationResult:
    """`raw`（YAML-loaded の score dict）を検証する。

    `spec` を省略すると canonical 検証のみを行う（公開スキーマ範囲チェックを
    スキップ）——`svprpe validate --contract` を省略した場合の挙動。
    """

    if not isinstance(raw, dict):
        return SymbolicValidationResult(
            status="fail",
            errors=[
                AuthoringErrorItem(
                    where="<file>",
                    message="composition score must be a mapping",
                    kind="canonical",
                )
            ],
        )

    errors: list[AuthoringErrorItem] = []
    if spec is not None:
        errors += _public_scope_errors(raw, spec)
    errors += _canonical_errors(raw)

    if not errors:
        return SymbolicValidationResult(status="pass")
    return SymbolicValidationResult(status="fail", errors=errors)
