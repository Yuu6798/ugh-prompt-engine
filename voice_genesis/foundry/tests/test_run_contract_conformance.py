"""test_run_contract_conformance.py — Run Contract 機械層の fail-closed 検査
（Phase D0・DEBT_ADJUDICATION_v1.1.md 裁定4。2026-08-22 セルフレビュー修正5/6/7/8
+ Codex bot レビュー #7 で改訂: gate の pre_run/post_run 分離・PINNED フィールド
列の schema 派生化・sha256 改竄検出テストの実体化・schema 制約の明示検査）。

境界宣言: 本ファイルが照合するのは projection に記録された sha256 の**形式**
（hex64 pattern）および design_doc の実バイトとの一致のみ。dataset/checkpoint
実体（repo に非同梱）の sha256 と projection 値との一致は実行環境側の pin
照合ツール（例: `scripts/check_checkpoint_finite.py --pins`）の責務であり、
本テストの対象外。

`debt/RUN_CONTRACT_SCHEMA_v1.json` が定義する projection 形状に対し、
`debt/DESIGN_S7_run8.contract_projection.json` が実際に fail-closed で
振る舞うことを検査する。検査ロジックは schema の意味論を Python で
再実装したもの（外部 jsonschema ライブラリは前提にしない = schema 自身の
コメント方針）。

検査内容:
(a) projection の design_doc_sha256 が実ファイルの sha256 と一致
(b) schema 必須フィールドが projection に全て存在
(c) single_intervention.count == 1
(d) runbook_may_override_design == false
(e) pre_run フィールドが1つでも未 PINNED（または post_run フィールドが
    BLOCKED）であれば main_training_gate == "BLOCKED"
(f) main_training_gate == "OPEN" は 全 pre_run フィールドが実質 PINNED
    （status PINNED かつ value/source が非 null）かつ 全 post_run フィールドが
    BLOCKED でない（PENDING までは許容）ときのみ許される
    （現状データでは pre_run に BLOCKED/PENDING があるため引き続き BLOCKED
    であることを実証する）
(g) pinned_field 形 object / single_intervention の許容キー外のフィールドが
    無いこと、design_doc_sha256 の hex64 pattern、claim_strength_target.value
    の禁止記号（"C0"〜"C3" 等）非混入、main_training_gate の許容値
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

DEBT_DIR = Path(__file__).resolve().parent.parent / "debt"
SCHEMA_PATH = DEBT_DIR / "RUN_CONTRACT_SCHEMA_v1.json"
PROJECTION_PATH = DEBT_DIR / "DESIGN_S7_run8.contract_projection.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def schema() -> Dict[str, Any]:
    assert SCHEMA_PATH.exists(), f"schema not found: {SCHEMA_PATH}"
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def projection() -> Dict[str, Any]:
    assert PROJECTION_PATH.exists(), f"projection not found: {PROJECTION_PATH}"
    return json.loads(PROJECTION_PATH.read_text(encoding="utf-8"))


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --- schema 派生ヘルパー（修正6: PINNED_FIELD_NAMES 手書きリストの廃止） -----
#
# 「pinned_field 形（value/source/status を持つ object）のフィールド一覧」と
# 「x-gate-class（pre_run/post_run）ごとのフィールド一覧」を、どちらも schema
# の properties から動的に導出する。schema にフィールドが増減すれば、この
# ファイルのコードを一切変更せずに以後のテストが自動追随する。


def _resolve_property_schema(schema: Dict[str, Any], prop_schema: Dict[str, Any]) -> Dict[str, Any]:
    """properties[name] の schema を返す。$ref があれば definitions から解決する
    （x-gate-class 等のフィールド固有キーは $ref の兄弟キーとして properties[name]
    自体に付与されているため、呼び出し側は解決前の prop_schema も参照できる）。"""
    ref = prop_schema.get("$ref")
    if ref is None:
        return prop_schema
    assert ref.startswith("#/definitions/"), f"unsupported $ref target: {ref}"
    return schema["definitions"][ref.split("/")[-1]]


def _pinned_field_names(schema: Dict[str, Any]) -> List[str]:
    """{value, source, status} を required に持つ object 型フィールド名一覧
    （$ref 経由の pinned_field 群 + インライン定義の claim_strength_target）。"""
    names: List[str] = []
    for name, prop_schema in schema["properties"].items():
        resolved = _resolve_property_schema(schema, prop_schema)
        if set(resolved.get("required", [])) == {"value", "source", "status"}:
            names.append(name)
    return names


def _gate_class_field_names(schema: Dict[str, Any], gate_class: str) -> List[str]:
    """x-gate-class == gate_class （"pre_run" / "post_run"）が付与された
    フィールド名一覧を properties から動的に導出する。"""
    return [
        name
        for name, prop_schema in schema["properties"].items()
        if prop_schema.get("x-gate-class") == gate_class
    ]


def _sha256_named_field_names(schema: Dict[str, Any]) -> List[str]:
    """フィールド名が '_sha256' で終わる schema properties 名一覧
    （design_doc_sha256 のような単純 string 欄と、dataset_manifest_sha256 等の
    pinned_field 型欄の両方を含む。Codex bot レビュー #7）。"""
    return [name for name in schema["properties"] if name.endswith("_sha256")]


def _is_effectively_pinned(field: Any) -> bool:
    """status が "PINNED" を名乗っていても value/source が null なら実質未 PIN
    として扱う（Codex bot レビュー #7「PINNED の実質検証」）。gate 判定は
    status 文字列だけでなくこの関数の結果を使う。"""
    if not isinstance(field, dict):
        return False
    return (
        field.get("status") == "PINNED"
        and field.get("value") is not None
        and field.get("source") is not None
    )


def _is_pre_run_field_satisfied(name: str, field: Any) -> bool:
    """pre_run フィールドが gate OPEN の要件を満たすか判定する。
    single_intervention のみ {value,source,status} 形でなく
    {declared,intervention,count} 形なので個別分岐する。"""
    if not isinstance(field, dict):
        return False
    if name == "single_intervention":
        return field.get("declared") is True and field.get("count") == 1
    return _is_effectively_pinned(field)


def _unsatisfied_pre_run_fields(schema: Dict[str, Any], projection: Dict[str, Any]) -> List[str]:
    unsatisfied: List[str] = []
    for name in _gate_class_field_names(schema, "pre_run"):
        field = projection.get(name)
        if not _is_pre_run_field_satisfied(name, field):
            unsatisfied.append(name)
    return unsatisfied


def _blocked_post_run_fields(schema: Dict[str, Any], projection: Dict[str, Any]) -> List[str]:
    """post_run フィールドは PENDING までは許容するが、欠落 or status ==
    BLOCKED は gate OPEN を妨げる（run record closure 側の要件）。"""
    blocked: List[str] = []
    for name in _gate_class_field_names(schema, "post_run"):
        field = projection.get(name)
        if not isinstance(field, dict) or field.get("status") not in ("PINNED", "PENDING"):
            blocked.append(name)
    return blocked


def _expected_gate(schema: Dict[str, Any], projection: Dict[str, Any]) -> str:
    """schema の x-gate-class 意味論を素直に実装した参照ロジック:
    全 pre_run フィールドが実質 PINNED、かつ全 post_run フィールドが
    BLOCKED でなければ OPEN。それ以外は BLOCKED。"""
    if _unsatisfied_pre_run_fields(schema, projection) or _blocked_post_run_fields(schema, projection):
        return "BLOCKED"
    return "OPEN"


# --- (a) design_doc_sha256 が実ファイルと一致 ---------------------------


def _check_design_doc_sha256(projection: Dict[str, Any], repo_root: Path) -> None:
    """design_doc_sha256 と実ファイル sha256 の照合ロジック本体。
    `test_design_doc_sha256_matches_actual_file` /
    `test_design_doc_sha256_mismatch_is_detected` の両方から呼ばれる共有関数
    （修正7: テスト自体を空虚にしない — 実際にこの関数へ食わせて検査する）。"""
    design_doc_rel = projection["design_doc"]
    design_doc_path = repo_root / design_doc_rel
    assert design_doc_path.exists(), f"design_doc not found: {design_doc_path}"
    actual_sha256 = _sha256_of_file(design_doc_path)
    assert actual_sha256 == projection["design_doc_sha256"], (
        "DESIGN 文書が改変されたのに projection の design_doc_sha256 が"
        f"追随していません: actual={actual_sha256} projection={projection['design_doc_sha256']}"
    )


def test_design_doc_sha256_matches_actual_file(projection: Dict[str, Any]) -> None:
    _check_design_doc_sha256(projection, REPO_ROOT)


def test_design_doc_sha256_mismatch_is_detected(projection: Dict[str, Any]) -> None:
    """sha256 照合ロジック自体が改変検出できることを、(a) 実ファイル + 正しい
    sha → pass、(b) 改竄 sha ('0'*64) を入れた projection dict → 照合関数が
    不一致を検出して fail、の両方向を実際に `_check_design_doc_sha256` へ
    食わせて検査する（修正7: 従来は sha を比較せずアサートするだけの空虚な
    テストだった）。"""
    # (a) 正しい projection はそのまま通る。
    _check_design_doc_sha256(projection, REPO_ROOT)

    # (b) design_doc_sha256 を改竄した projection dict は検出されて fail する。
    tampered = dict(projection)
    tampered["design_doc_sha256"] = "0" * 64
    with pytest.raises(AssertionError):
        _check_design_doc_sha256(tampered, REPO_ROOT)


# --- (b) schema 必須フィールドが projection に全て存在 -------------------


def test_all_schema_required_fields_present_in_projection(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    required = schema["required"]
    missing = [k for k in required if k not in projection]
    assert not missing, f"projection missing required fields: {missing}"


def test_projection_has_no_fields_outside_schema(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    allowed = set(schema["properties"].keys())
    extra = [k for k in projection if k not in allowed]
    assert not extra, f"projection has fields not declared in schema: {extra}"


def test_pinned_field_objects_have_required_subkeys(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    for name in _pinned_field_names(schema):
        field = projection[name]
        assert isinstance(field, dict), f"{name} must be an object"
        for subkey in ("value", "source", "status"):
            assert subkey in field, f"{name} missing subkey {subkey!r}"
        assert field["status"] in ("PINNED", "PENDING", "BLOCKED"), (
            f"{name}.status has invalid value {field['status']!r}"
        )


# --- (c) single_intervention.count == 1 ----------------------------------


def test_single_intervention_count_is_one(projection: Dict[str, Any]) -> None:
    single_intervention = projection["single_intervention"]
    assert single_intervention["declared"] is True
    assert single_intervention["count"] == 1


# --- (d) runbook_may_override_design == false -----------------------------


def test_runbook_may_not_override_design(projection: Dict[str, Any]) -> None:
    assert projection["runbook_may_override_design"] is False


# --- (e)/(f) main_training_gate の fail-closed ロジック (pre_run/post_run) -


def test_main_training_gate_is_blocked_when_pre_run_or_post_run_unsatisfied(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    unsatisfied_pre = _unsatisfied_pre_run_fields(schema, projection)
    blocked_post = _blocked_post_run_fields(schema, projection)
    assert unsatisfied_pre or blocked_post, (
        "この検査は『現状データでは BLOCKED 側を通る』ことを実証する目的のため、"
        "少なくとも1つの未充足 pre_run または BLOCKED な post_run フィールドが"
        "存在するはずです"
    )
    assert projection["main_training_gate"] == "BLOCKED", (
        f"未充足の pre_run ({unsatisfied_pre}) / BLOCKED な post_run ({blocked_post}) が"
        f"存在するのに main_training_gate が {projection['main_training_gate']!r} です。"
        "BLOCKED であるべきです。"
    )


def test_main_training_gate_matches_reference_logic(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    assert projection["main_training_gate"] == _expected_gate(schema, projection)


def test_gate_logic_flags_open_only_when_all_pre_run_pinned_and_post_run_not_blocked() -> None:
    """参照ロジック (_expected_gate) が
    (全 pre_run PINNED かつ 全 post_run 非 BLOCKED) のときのみ OPEN を返す
    ことを、schema の x-gate-class から動的導出した最小の合成 schema/projection
    で検証する（実データに依存しない純粋な論理検査。修正5）。"""
    schema = {
        "properties": {
            "single_intervention": {"x-gate-class": "pre_run"},
            "baseline_run": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
            "measurement_spec_version": {
                "$ref": "#/definitions/pinned_field",
                "x-gate-class": "pre_run",
            },
            "checkpoint_sha256": {"$ref": "#/definitions/pinned_field", "x-gate-class": "post_run"},
            "cost_record": {"$ref": "#/definitions/pinned_field", "x-gate-class": "post_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    base_projection = {
        "single_intervention": {"declared": True, "intervention": "x", "count": 1},
        "baseline_run": {"value": "x", "source": "y", "status": "PINNED"},
        "measurement_spec_version": {"value": "x", "source": "y", "status": "PINNED"},
        # post_run は PENDING までなら gate OPEN を妨げない。
        "checkpoint_sha256": {"value": None, "source": None, "status": "PENDING"},
        "cost_record": {"value": None, "source": None, "status": "PENDING"},
    }
    assert _expected_gate(schema, base_projection) == "OPEN"

    # pre_run が1つでも PENDING/BLOCKED なら BLOCKED。
    one_pre_run_pending = json.loads(json.dumps(base_projection))
    one_pre_run_pending["measurement_spec_version"] = {
        "value": None,
        "source": None,
        "status": "PENDING",
    }
    assert _expected_gate(schema, one_pre_run_pending) == "BLOCKED"

    # post_run が BLOCKED なら（pre_run が全 PINNED でも）BLOCKED。
    one_post_run_blocked = json.loads(json.dumps(base_projection))
    one_post_run_blocked["cost_record"] = {"value": None, "source": None, "status": "BLOCKED"}
    assert _expected_gate(schema, one_post_run_blocked) == "BLOCKED"

    # 必須欄の欠落は BLOCKED（pre_run/post_run とも）。
    missing_pre_run = json.loads(json.dumps(base_projection))
    del missing_pre_run["baseline_run"]
    assert _expected_gate(schema, missing_pre_run) == "BLOCKED"

    missing_post_run = json.loads(json.dumps(base_projection))
    del missing_post_run["checkpoint_sha256"]
    assert _expected_gate(schema, missing_post_run) == "BLOCKED"


def test_open_gate_requires_all_pre_run_pinned_and_no_post_run_blocked() -> None:
    """main_training_gate == OPEN を宣言している projection は、
    _unsatisfied_pre_run_fields / _blocked_post_run_fields が両方とも
    空であることを要求する（(f) の直接検査）。"""
    schema = {
        "properties": {
            "baseline_run": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
            "checkpoint_sha256": {"$ref": "#/definitions/pinned_field", "x-gate-class": "post_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    fake_open_projection = {
        "baseline_run": {"value": "x", "source": "y", "status": "PINNED"},
        "checkpoint_sha256": {"value": None, "source": None, "status": "PENDING"},
        "main_training_gate": "OPEN",
    }
    assert _unsatisfied_pre_run_fields(schema, fake_open_projection) == []
    assert _blocked_post_run_fields(schema, fake_open_projection) == []


# --- 修正6: 動的導出そのものの単体検査（schema へのフィールド追加が自動追随）--


def test_adding_new_pre_run_schema_field_changes_gate_result_without_code_change() -> None:
    """PINNED_FIELD_NAMES 手書きリストを廃止した効果の実証: schema の
    properties にフィールドを1つ足すだけで、このファイルのコードを一切
    変更せずに _expected_gate の判定が追随することを検査する。"""
    base_schema = {
        "properties": {
            "a": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    projection_missing_new_field = {"a": {"value": "x", "source": "y", "status": "PINNED"}}
    assert _expected_gate(base_schema, projection_missing_new_field) == "OPEN"

    # 架空の新規フィールド "b" を pre_run として schema に追加する。
    schema_with_new_field = json.loads(json.dumps(base_schema))
    schema_with_new_field["properties"]["b"] = {
        "$ref": "#/definitions/pinned_field",
        "x-gate-class": "pre_run",
    }
    # projection 側は "b" を追加していない（= 未 PINNED 扱い）ので BLOCKED に転じる。
    assert _expected_gate(schema_with_new_field, projection_missing_new_field) == "BLOCKED"

    # projection 側も "b" を実質 PINNED で足せば OPEN に戻る。
    projection_with_new_field = dict(projection_missing_new_field)
    projection_with_new_field["b"] = {"value": "x", "source": "y", "status": "PINNED"}
    assert _expected_gate(schema_with_new_field, projection_with_new_field) == "OPEN"


# --- Codex bot レビュー #7: PINNED の実質検証 -------------------------------


def test_effectively_pinned_rejects_pinned_status_with_null_value_or_source() -> None:
    assert not _is_effectively_pinned({"status": "PINNED", "value": None, "source": "x"})
    assert not _is_effectively_pinned({"status": "PINNED", "value": "x", "source": None})
    assert not _is_effectively_pinned({"status": "PINNED", "value": None, "source": None})
    assert _is_effectively_pinned({"status": "PINNED", "value": "x", "source": "y"})
    # PENDING/BLOCKED は value/source の有無に関わらず実質 PIN とは認めない。
    assert not _is_effectively_pinned({"status": "PENDING", "value": "x", "source": "y"})


def test_gate_treats_pinned_status_with_null_value_as_unpinned() -> None:
    """status: "PINNED" を名乗るだけで value/source が null なフィールドは
    gate 判定上 PINNED として扱わない（実質検証。Codex bot レビュー #7 —
    status 文字列だけを見ていた旧ロジックだと素通りしてしまう欠陥の再現）。"""
    schema = {
        "properties": {
            "a": {"$ref": "#/definitions/pinned_field", "x-gate-class": "pre_run"},
        },
        "definitions": {"pinned_field": {"required": ["value", "source", "status"]}},
    }
    fake_pinned_projection = {"a": {"status": "PINNED", "value": None, "source": None}}
    assert _expected_gate(schema, fake_pinned_projection) == "BLOCKED"
    assert _unsatisfied_pre_run_fields(schema, fake_pinned_projection) == ["a"]


def test_pinned_sha256_named_fields_match_hex64_pattern(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """PINNED 状態の *_sha256 pinned_field は value が ^[0-9a-f]{64}$ に一致する
    こと。design_doc_sha256（常に必須の平文字列欄）も同じ pattern を満たす
    こと（Codex bot レビュー #7）。境界宣言はモジュール docstring 冒頭を参照
    （実バイト照合ではなく形式検査のみ）。"""
    checked_any = False
    for name in _sha256_named_field_names(schema):
        checked_any = True
        if name == "design_doc_sha256":
            assert SHA256_HEX_PATTERN.fullmatch(projection[name]), (
                f"{name} が hex64 pattern に一致しません: {projection[name]!r}"
            )
            continue
        field = projection.get(name)
        if isinstance(field, dict) and field.get("status") == "PINNED":
            assert isinstance(field.get("value"), str) and SHA256_HEX_PATTERN.fullmatch(
                field["value"]
            ), f"{name} は PINNED なのに value が sha256 hex64 形式ではありません: {field.get('value')!r}"
    assert checked_any, "schema に *_sha256 フィールドが1つも見つかりませんでした"


def test_sha256_pattern_violation_is_detected() -> None:
    """sha256 hex64 pattern 照合ロジックが不正値を実際に検出できることの
    負例テスト。"""
    assert not SHA256_HEX_PATTERN.fullmatch("not-a-valid-sha")
    assert not SHA256_HEX_PATTERN.fullmatch("A" * 64)  # 大文字は不可
    assert not SHA256_HEX_PATTERN.fullmatch("0" * 63)  # 桁数不足
    assert SHA256_HEX_PATTERN.fullmatch("0" * 64)


# --- 修正8: 未強制の schema 制約をテストへ実装 ------------------------------


def test_schema_pinned_field_definition_declares_expected_allowed_keys(
    schema: Dict[str, Any],
) -> None:
    """(a) pinned_field 定義の許容キーが {value, source, status, reason} の
    みであること（schema 側の additionalProperties: false）。"""
    pinned_field_def = schema["definitions"]["pinned_field"]
    assert pinned_field_def.get("additionalProperties") is False
    assert set(pinned_field_def["properties"].keys()) == {"value", "source", "status", "reason"}


def test_pinned_field_shaped_projection_entries_reject_extra_keys(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(a) 実データ + 負例: pinned_field 形の projection エントリが許容キー外
    を持てば検出できること。"""
    allowed = set(schema["definitions"]["pinned_field"]["properties"].keys())
    for name in _pinned_field_names(schema):
        extra = set(projection[name].keys()) - allowed
        assert not extra, f"{name} has keys outside {allowed}: {extra}"

    tampered = dict(projection["baseline_run"])
    tampered["unexpected_extra_key"] = "should not be allowed"
    assert set(tampered.keys()) - allowed == {"unexpected_extra_key"}


def test_schema_single_intervention_declares_expected_allowed_keys(schema: Dict[str, Any]) -> None:
    """(b) single_intervention の許容キーが {declared, intervention, count}
    のみであること。"""
    si_schema = schema["properties"]["single_intervention"]
    assert si_schema.get("additionalProperties") is False
    assert set(si_schema["properties"].keys()) == {"declared", "intervention", "count"}


def test_single_intervention_projection_rejects_extra_keys(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(b) 実データ + 負例。"""
    allowed = set(schema["properties"]["single_intervention"]["properties"].keys())
    extra = set(projection["single_intervention"].keys()) - allowed
    assert not extra, f"single_intervention has keys outside {allowed}: {extra}"

    tampered = dict(projection["single_intervention"])
    tampered["unexpected_extra_key"] = "x"
    assert set(tampered.keys()) - allowed == {"unexpected_extra_key"}


def test_schema_design_doc_sha256_has_hex64_pattern(schema: Dict[str, Any]) -> None:
    """(c) design_doc_sha256 の schema pattern が ^[0-9a-f]{64}$ であること。"""
    assert schema["properties"]["design_doc_sha256"]["pattern"] == r"^[0-9a-f]{64}$"


def test_schema_claim_strength_target_value_enum_excludes_forbidden_symbols(
    schema: Dict[str, Any],
) -> None:
    """(d) claim_strength_target.value の許容値が
    {null, descriptive, suggestive, moderate, strong} のみで、"C0"〜"C3" 等の
    禁止記号（DEBT_ADJUDICATION_v1.1.md 裁定1: causal_evidence_strength 語彙は
    C0-C3 記号を禁止）が含まれないこと。"""
    allowed_values = schema["properties"]["claim_strength_target"]["properties"]["value"]["enum"]
    assert set(allowed_values) == {"descriptive", "suggestive", "moderate", "strong", None}
    forbidden_symbols = ["C0", "C1", "C2", "C3"]
    for symbol in forbidden_symbols:
        assert symbol not in allowed_values, f"forbidden symbol {symbol!r} must not be a valid enum value"


def test_claim_strength_target_projection_value_is_within_allowed_enum(
    schema: Dict[str, Any], projection: Dict[str, Any]
) -> None:
    """(d) 実データが許容 enum 内であること。"""
    allowed_values = set(schema["properties"]["claim_strength_target"]["properties"]["value"]["enum"])
    assert projection["claim_strength_target"]["value"] in allowed_values


def test_schema_main_training_gate_enum_is_open_blocked_only(schema: Dict[str, Any]) -> None:
    """(e) main_training_gate の schema enum が {"OPEN", "BLOCKED"} のみで
    あること。"""
    assert set(schema["properties"]["main_training_gate"]["enum"]) == {"OPEN", "BLOCKED"}


def test_main_training_gate_projection_value_is_open_or_blocked(projection: Dict[str, Any]) -> None:
    """(e) 実データが許容値内であること。"""
    assert projection["main_training_gate"] in {"OPEN", "BLOCKED"}
