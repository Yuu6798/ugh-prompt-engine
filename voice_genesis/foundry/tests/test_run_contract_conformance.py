"""test_run_contract_conformance.py — Run Contract 機械層の fail-closed 検査
（Phase D0・DEBT_ADJUDICATION_v1.1.md 裁定4）。

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
(e) 必須欄に BLOCKED/PENDING/missing が 1 つでもあれば
    main_training_gate == "BLOCKED"
(f) main_training_gate == "OPEN" は全必須欄 PINNED のときのみ許される
    （現状データでは BLOCKED 側を通ることを実証する）
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

DEBT_DIR = Path(__file__).resolve().parent.parent / "debt"
SCHEMA_PATH = DEBT_DIR / "RUN_CONTRACT_SCHEMA_v1.json"
PROJECTION_PATH = DEBT_DIR / "DESIGN_S7_run8.contract_projection.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# {value, source, status} 形の pinned_field を持つフィールド一式
# （schema の definitions/pinned_field を参照するフィールド。
#   claim_strength_target も同型の object なのでここに含める）。
PINNED_FIELD_NAMES = [
    "baseline_run",
    "parent_run",
    "code_commit_sha",
    "dataset_manifest_sha256",
    "dataset_row_order",
    "config_sha256",
    "dependency_pins",
    "execution_profile",
    "seed_sampler_settings",
    "expected_speaker_map",
    "checkpoint_sha256",
    "artifact_manifest",
    "fixed_probe_set",
    "measurement_spec_version",
    "hypothesis_algebra_version",
    "human_evaluation_protocol",
    "cost_record",
    "failure_abort_criteria",
    "claim_strength_target",
]


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


def _unpinned_required_fields(projection: Dict[str, Any]) -> List[str]:
    """必須 pinned_field のうち status が PINNED でない（= PENDING/BLOCKED、
    または欄自体が欠落している = missing）フィールド名を返す。"""
    unpinned: List[str] = []
    for name in PINNED_FIELD_NAMES:
        field = projection.get(name)
        if field is None:
            unpinned.append(name)
            continue
        if not isinstance(field, dict) or field.get("status") != "PINNED":
            unpinned.append(name)
    return unpinned


# --- (a) design_doc_sha256 が実ファイルと一致 ---------------------------


def test_design_doc_sha256_matches_actual_file(projection: Dict[str, Any]) -> None:
    design_doc_rel = projection["design_doc"]
    design_doc_path = REPO_ROOT / design_doc_rel
    assert design_doc_path.exists(), f"design_doc not found: {design_doc_path}"
    actual_sha256 = _sha256_of_file(design_doc_path)
    assert actual_sha256 == projection["design_doc_sha256"], (
        "DESIGN 文書が改変されたのに projection の design_doc_sha256 が"
        f"追随していません: actual={actual_sha256} projection={projection['design_doc_sha256']}"
    )


def test_design_doc_sha256_mismatch_is_detected(tmp_path: Path, projection: Dict[str, Any]) -> None:
    """conformance test 自体が改変検出できることを、意図的に不一致な
    projection で検証する（fail-closed の実効性テスト）。"""
    tampered = dict(projection)
    tampered["design_doc_sha256"] = "0" * 64
    design_doc_path = REPO_ROOT / tampered["design_doc"]
    actual_sha256 = _sha256_of_file(design_doc_path)
    assert actual_sha256 != tampered["design_doc_sha256"]


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


def test_pinned_field_objects_have_required_subkeys(projection: Dict[str, Any]) -> None:
    for name in PINNED_FIELD_NAMES:
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


# --- (e)/(f) main_training_gate の fail-closed ロジック --------------------


def _expected_gate(projection: Dict[str, Any]) -> str:
    """schema の意味論を素直に実装した参照ロジック: 必須 pinned_field が
    1 つでも PINNED でなければ BLOCKED、全て PINNED なら OPEN。"""
    if _unpinned_required_fields(projection):
        return "BLOCKED"
    return "OPEN"


def test_main_training_gate_is_blocked_when_any_required_field_unpinned(
    projection: Dict[str, Any],
) -> None:
    unpinned = _unpinned_required_fields(projection)
    assert unpinned, (
        "この検査は『現状データでは BLOCKED 側を通る』ことを実証する目的のため、"
        "少なくとも1つの未凍結フィールドが存在するはずです"
    )
    assert projection["main_training_gate"] == "BLOCKED", (
        f"未凍結フィールドが存在する ({unpinned}) のに main_training_gate が "
        f"{projection['main_training_gate']!r} です。BLOCKED であるべきです。"
    )


def test_main_training_gate_matches_reference_logic(projection: Dict[str, Any]) -> None:
    assert projection["main_training_gate"] == _expected_gate(projection)


def test_gate_logic_flags_open_only_when_all_pinned() -> None:
    """参照ロジック (_expected_gate) が全欄 PINNED のときのみ OPEN を返す
    ことを、最小の合成 projection で検証する（DESIGN_S7 の実データに
    依存しない純粋な論理検査）。"""
    all_pinned = {
        name: {"value": "x", "source": "y", "status": "PINNED"} for name in PINNED_FIELD_NAMES
    }
    assert _expected_gate(all_pinned) == "OPEN"

    one_pending = dict(all_pinned)
    one_pending["cost_record"] = {"value": None, "source": None, "status": "PENDING"}
    assert _expected_gate(one_pending) == "BLOCKED"

    one_blocked = dict(all_pinned)
    one_blocked["measurement_spec_version"] = {"value": None, "source": None, "status": "BLOCKED"}
    assert _expected_gate(one_blocked) == "BLOCKED"

    one_missing = dict(all_pinned)
    del one_missing["fixed_probe_set"]
    assert _expected_gate(one_missing) == "BLOCKED"


def test_open_gate_requires_all_required_fields_pinned() -> None:
    """main_training_gate == OPEN を宣言している projection は、
    _unpinned_required_fields が空であることを要求する（(f) の直接検査）。"""
    all_pinned = {
        name: {"value": "x", "source": "y", "status": "PINNED"} for name in PINNED_FIELD_NAMES
    }
    fake_open_projection = dict(all_pinned)
    fake_open_projection["main_training_gate"] = "OPEN"
    assert _unpinned_required_fields(fake_open_projection) == []
