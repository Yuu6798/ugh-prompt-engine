"""test_h3c_learning_recipe_manifests.py — RUN9-L0-HARNESS-3c: 5 manifest
（score_axis_catalog_v1 / loss_evaluator_spec_v1 / candidate_generation_
spec_v1 / compute_budget_manifest_v1 / learning_data_binding_manifest_v1）
+ `score_axis_transform.py` + `run9_schema.load_pinned_*()` 5対の最低
テスト。

fixture は実 manifest（repo 収載、rights 制約のある音声実体を含まない）
そのものと、その in-memory コピーへの局所改変のみを用いる。**render は
一切実行しない**（実測記録は `HARNESS3C_AXIS_FEASIBILITY_RECORD.md` に
収載済み — 本ファイルは fail-closed 検証ロジックのみを高速に検査する）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest
import yaml

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import run9_schema as m  # noqa: E402
import score_axis_transform as sat  # noqa: E402

CONTRACT_PATH = _RUN_DIR / "RUN9_CONTRACT.yaml"
ADJUDICATION_PATH = _RUN_DIR / "USER_ADJUDICATION_20260827_LEARNING_RECIPE_5KEYS.txt"
DETAIL_RECORD_PATH = _RUN_DIR / "HARNESS3C_AXIS_FEASIBILITY_RECORD.md"

_MANIFESTS = {
    "score_axis_catalog": {
        "pin_name": "score_axis_catalog_sha",
        "path_const": "SCORE_AXIS_CATALOG_PATH",
        "validate_fn": "validate_score_axis_catalog_manifest",
        "load_fn": "load_pinned_score_axis_catalog_manifest",
        "schema_const": "SCHEMA_SCORE_AXIS_CATALOG",
    },
    "loss_evaluator_spec": {
        "pin_name": "loss_evaluator_spec_sha",
        "path_const": "LOSS_EVALUATOR_SPEC_PATH",
        "validate_fn": "validate_loss_evaluator_spec_manifest",
        "load_fn": "load_pinned_loss_evaluator_spec_manifest",
        "schema_const": "SCHEMA_LOSS_EVALUATOR_SPEC",
    },
    "candidate_generation_spec": {
        "pin_name": "candidate_generation_spec_sha",
        "path_const": "CANDIDATE_GENERATION_SPEC_PATH",
        "validate_fn": "validate_candidate_generation_spec_manifest",
        "load_fn": "load_pinned_candidate_generation_spec_manifest",
        "schema_const": "SCHEMA_CANDIDATE_GENERATION_SPEC",
    },
    "compute_budget_manifest": {
        "pin_name": "compute_budget_manifest_sha",
        "path_const": "COMPUTE_BUDGET_MANIFEST_PATH",
        "validate_fn": "validate_compute_budget_manifest",
        "load_fn": "load_pinned_compute_budget_manifest",
        "schema_const": "SCHEMA_COMPUTE_BUDGET_MANIFEST",
    },
    "learning_data_binding_manifest": {
        "pin_name": "learning_data_binding_manifest_sha",
        "path_const": "LEARNING_DATA_BINDING_MANIFEST_PATH",
        "validate_fn": "validate_learning_data_binding_manifest",
        "load_fn": "load_pinned_learning_data_binding_manifest",
        "schema_const": "SCHEMA_LEARNING_DATA_BINDING_MANIFEST",
    },
}


@pytest.fixture(scope="module")
def contract_raw() -> Dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract() -> m.Run9RunContract:
    return m.load_run9_contract_from_yaml_path(CONTRACT_PATH)


def _manifest_path(key: str) -> Path:
    return getattr(m, _MANIFESTS[key]["path_const"])


def _manifest_data(key: str) -> Dict[str, Any]:
    return json.loads(_manifest_path(key).read_text(encoding="utf-8"))


def _validate_fn(key: str):
    return getattr(m, _MANIFESTS[key]["validate_fn"])


def _load_fn(key: str):
    return getattr(m, _MANIFESTS[key]["load_fn"])


def _canonical_json_bytes(data: Dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _tampered_contract(
    contract: m.Run9RunContract, tmp_path: Path, *, key: str, mutate,
) -> Tuple[m.Run9RunContract, Path, Path]:
    """`key` の manifest 内容を `mutate` で改変し、その実バイト sha256 で
    対応 pin を差し替えた合成 contract + manifest ファイル + contract
    ファイルを用意するテストヘルパー（`test_education_lesson_builder.py`
    `_tampered_education_manifest_contract()` と同型）。"""
    data = copy.deepcopy(_manifest_data(key))
    mutate(data)
    manifest_bytes = _canonical_json_bytes(data)
    manifest_path = tmp_path / f"{key}.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = __import__("hashlib").sha256(manifest_bytes).hexdigest()
    tampered_raw = copy.deepcopy(contract.raw)
    tampered_raw[_MANIFESTS[key]["pin_name"]] = {"value": manifest_sha, "status": "PINNED"}
    tampered_contract_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_contract_path.write_text(yaml.safe_dump(tampered_raw, allow_unicode=True), encoding="utf-8")
    return m.load_run9_contract(tampered_raw), manifest_path, tampered_contract_path


# ---------------------------------------------------------------------------
# repo 収載ファイルの存在確認
# ---------------------------------------------------------------------------


def test_h3c_adjudication_source_file_exists() -> None:
    assert ADJUDICATION_PATH.is_file()
    assert m.H3C_ADJUDICATION_PATH == ADJUDICATION_PATH


def test_h3c_detail_record_exists() -> None:
    assert DETAIL_RECORD_PATH.is_file()
    assert m.H3C_DETAIL_RECORD_PATH == DETAIL_RECORD_PATH


def test_h3c_score_axis_transform_module_present() -> None:
    assert (_RUN_DIR / "score_axis_transform.py").is_file()


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_manifest_file_exists(key: str) -> None:
    assert _manifest_path(key).is_file()


# ---------------------------------------------------------------------------
# contract 整合: 5 新規 pin 欄が PINNED かつ manifest 実バイトと一致、
# pre-run PENDING 件数が不変
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_contract_sha_pinned_and_matches_manifest_bytes(
    key: str, contract: m.Run9RunContract,
) -> None:
    pin_name = _MANIFESTS[key]["pin_name"]
    field = contract.pin_field(pin_name)
    assert field["status"] == "PINNED"
    assert field["value"] == m.compute_file_sha256(_manifest_path(key))


def test_h3c_all_five_pin_names_in_contract_pin_fields() -> None:
    for entry in _MANIFESTS.values():
        assert entry["pin_name"] in m.CONTRACT_PIN_FIELDS


def test_h3c_pre_run_pending_field_count_is_seven(contract: m.Run9RunContract) -> None:
    """テスト名は歴史的固定（rename しない、他の pending-count テストと
    同型の規約）。値は design_revision 0.6（RUN9-L0-HARNESS-3c rev 0.6）で
    `hypothesis_algebra_sha` が PINNED 化されたことにより 7 → 6 件へ
    減少した。"""
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [n for n in pre_run_fields if not m._is_field_pinned(contract.pin_field(n))]  # noqa: SLF001
    assert len(pending) == 6, pending
    for entry in _MANIFESTS.values():
        assert entry["pin_name"] not in pending
    assert "hypothesis_algebra_sha" not in pending


def test_h3c_learning_recipe_sha_still_pending(contract: m.Run9RunContract) -> None:
    field = contract.pin_field("learning_recipe_sha")
    assert field["status"] == "PENDING"


def test_h3c_gate_state_still_blocked(contract: m.Run9RunContract) -> None:
    assert m.gate_state(contract) == "BLOCKED"


# ---------------------------------------------------------------------------
# validate_*()/load_pinned_*(): 正常系（5 manifest 共通）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_validate_passes_on_real_manifest(key: str) -> None:
    _validate_fn(key)(_manifest_data(key))  # must not raise


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_load_pinned_happy_path(key: str, contract: m.Run9RunContract) -> None:
    data = _load_fn(key)(contract)
    assert data["schema"] == getattr(m, _MANIFESTS[key]["schema_const"])


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_load_pinned_missing_file_rejected(
    key: str, contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    missing_path = tmp_path / "does_not_exist.json"
    with pytest.raises(m.Run9ValidationError, match="does not exist"):
        _load_fn(key)(contract, manifest_path=missing_path)


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_load_pinned_byte_tampering_detected(
    key: str, contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    tampered_path = tmp_path / f"{key}.json"
    tampered_path.write_bytes(_manifest_path(key).read_bytes() + b"\n")
    with pytest.raises(m.Run9ValidationError, match="実バイト sha256"):
        _load_fn(key)(contract, manifest_path=tampered_path)


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_load_pinned_rejects_when_not_pinned(
    key: str, contract_raw: Dict[str, Any], tmp_path: Path,
) -> None:
    pin_name = _MANIFESTS[key]["pin_name"]
    tampered = copy.deepcopy(contract_raw)
    tampered[pin_name] = {"value": None, "status": "PENDING", "reason": "test"}
    tampered_yaml_path = tmp_path / "RUN9_CONTRACT.yaml"
    tampered_yaml_path.write_text(yaml.safe_dump(tampered, allow_unicode=True), encoding="utf-8")
    tampered_contract = m.load_run9_contract(tampered)
    with pytest.raises(m.Run9ValidationError, match="not PINNED"):
        _load_fn(key)(tampered_contract, contract_path=tampered_yaml_path)


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_load_pinned_detects_in_process_contract_tampering(
    key: str, contract: m.Run9RunContract,
) -> None:
    pin_name = _MANIFESTS[key]["pin_name"]
    tampered_contract = copy.deepcopy(contract)
    tampered_contract.raw[pin_name] = {"value": "f" * 64, "status": "PINNED", "source": "forged"}
    with pytest.raises(m.Run9ValidationError, match="tampering evidence"):
        _load_fn(key)(tampered_contract)


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_adjudication_sha_forged_rejected(
    key: str, contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["adjudication_basis"]["sha256"] = "f" * 64

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key=key, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="裁定文書"):
        _load_fn(key)(tampered_contract, manifest_path=manifest_path, contract_path=contract_path)


@pytest.mark.parametrize("key", sorted(_MANIFESTS))
def test_h3c_detail_record_sha_forged_rejected(
    key: str, contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["provenance"]["detail_record"]["sha256"] = "f" * 64

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key=key, mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="W1/W1b 実測"):
        _load_fn(key)(tampered_contract, manifest_path=manifest_path, contract_path=contract_path)


# ---------------------------------------------------------------------------
# 1. score_axis_catalog_v1 固有
# ---------------------------------------------------------------------------


def test_h3c_catalog_family_c_is_not_expressible() -> None:
    data = _manifest_data("score_axis_catalog")
    family_c = data["axes"]["phrase_boundary_control"]
    assert family_c["status"] == "NOT_EXPRESSIBLE_ON_CURRENT_WIRING"
    assert family_c["axes"] == []


def test_h3c_catalog_family_c_status_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("score_axis_catalog"))
    data["axes"]["phrase_boundary_control"]["status"] = "EXPRESSIBLE"
    with pytest.raises(m.Run9ValidationError, match="NOT_EXPRESSIBLE_ON_CURRENT_WIRING"):
        m.validate_score_axis_catalog_manifest(data)


def test_h3c_catalog_ax_p1_range_inverted_rejected() -> None:
    data = copy.deepcopy(_manifest_data("score_axis_catalog"))
    data["axes"]["AX-P1"]["range_semitones"] = [2.0, -2.0]
    with pytest.raises(m.Run9ValidationError, match="range_semitones"):
        m.validate_score_axis_catalog_manifest(data)


def test_h3c_catalog_ax_d1_min_duration_non_positive_rejected() -> None:
    data = copy.deepcopy(_manifest_data("score_axis_catalog"))
    data["axes"]["AX-D1"]["min_duration_beats"] = 0
    with pytest.raises(m.Run9ValidationError, match="min_duration_beats"):
        m.validate_score_axis_catalog_manifest(data)


def test_h3c_catalog_transformer_cross_check_catches_shrunk_range(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    """catalog↔変換器整合チェック: range を書き換えても構造的には妥当な
    ままだが、`score_axis_transform.apply_ax_p1()` の境界受理チェックは
    manifest 自身の（改変後の）range に基づいて動くため矛盾なく通る——
    ここでは「min_duration_beats を AX-D1 の quantization_step_beats より
    小さくする」という構造的整合性の破れ（境界delta計算が矛盾する）を
    与えて、cross-check 経路が例外を機械的に検出することを確認する。"""
    def _mutate(data: Dict[str, Any]) -> None:
        data["axes"]["AX-D1"]["min_duration_beats"] = 100.0  # baseline note より大

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key="score_axis_catalog", mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError):
        m.load_pinned_score_axis_catalog_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


# ---------------------------------------------------------------------------
# 2. loss_evaluator_spec_v1 固有
# ---------------------------------------------------------------------------


def test_h3c_loss_evaluator_calibration_matches_frozen_constant() -> None:
    data = _manifest_data("loss_evaluator_spec")
    for channel in data["channels"]:
        name = channel["name"]
        assert channel["calibration_scale"]["value"] == m.LOSS_EVALUATOR_CALIBRATION_SCALE_V1[name]


def test_h3c_loss_evaluator_calibration_value_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["channels"][0]["calibration_scale"]["value"] += 1.0
    with pytest.raises(m.Run9ValidationError, match="LOSS_EVALUATOR_CALIBRATION_SCALE_V1"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_weight_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["channels"][0]["weight"] = 0.3
    with pytest.raises(m.Run9ValidationError, match="0.2"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_vocab_mismatch_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["channels"][0]["education_allowed_channel"] = "not_a_real_channel"
    with pytest.raises(m.Run9ValidationError, match="education_allowed_channel"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_scope_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["aggregate_scope"] = "final_scientific_judgment"
    with pytest.raises(m.Run9ValidationError, match="aggregate_scope"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_zero_fill_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["missing_policy"]["zero_fill_prohibited"] = False
    with pytest.raises(m.Run9ValidationError, match="zero_fill_prohibited"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_not_measurable_definition_matches_frozen_constant() -> None:
    data = _manifest_data("loss_evaluator_spec")
    assert (
        data["missing_policy"]["not_measurable_definition"]
        == m._LOSS_EVALUATOR_EXPECTED_NOT_MEASURABLE_DEFINITION
    )


def test_h3c_loss_evaluator_not_measurable_definition_reverted_to_old_wording_rejected() -> None:
    # PR #331 第3巡指摘2（P2、採用）: 是正済み candidate 単位 NOT_SCORABLE
    # 文言が、旧「部分 channel 採点」文言へ repin で差し戻されても
    # zero_fill_prohibited/eligible_count_required_per_channel の2
    # boolean だけでは検出できなかった欠陥を閉じる。
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["missing_policy"]["not_measurable_definition"] = (
        "欠測 channel は aggregate から除外し、残る channel の等重み再正規化は行わない"
        "（欠測分を加点も減点もしない）。"
    )
    with pytest.raises(m.Run9ValidationError, match="not_measurable_definition"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_not_measurable_definition_missing_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["missing_policy"]["not_measurable_definition"]
    with pytest.raises(m.Run9ValidationError, match="not_measurable_definition"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_actor_boundary_matches_frozen_constants() -> None:
    data = _manifest_data("loss_evaluator_spec")
    assert data["actor_boundary"]["practice"] == m._LOSS_EVALUATOR_EXPECTED_ACTOR_BOUNDARY_PRACTICE
    assert data["actor_boundary"]["education"] == m._LOSS_EVALUATOR_EXPECTED_ACTOR_BOUNDARY_EDUCATION


def test_h3c_loss_evaluator_actor_boundary_practice_empty_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["actor_boundary"]["practice"] = ""
    with pytest.raises(m.Run9ValidationError, match="actor_boundary.practice"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_actor_boundary_practice_missing_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["actor_boundary"]["practice"]
    with pytest.raises(m.Run9ValidationError, match="actor_boundary.practice"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_actor_boundary_practice_permits_education_lesson_rejected() -> None:
    # レビュー原文が挙げる具体的な汚染経路: PRACTICE に education lesson /
    # precomputed teacher feature の入力を許す緩和文言へ改変。
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["actor_boundary"]["practice"] = (
        "PRACTICE 枝はこの evaluator を PJS raw audio + founder 自己 render に加え、"
        "education lesson / precomputed teacher feature の入力も許可する。"
    )
    with pytest.raises(m.Run9ValidationError, match="actor_boundary.practice"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_actor_boundary_education_missing_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["actor_boundary"]["education"]
    with pytest.raises(m.Run9ValidationError, match="actor_boundary.education"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_actor_boundary_missing_top_level_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["actor_boundary"]
    with pytest.raises(m.Run9ValidationError, match="actor_boundary"):
        m.validate_loss_evaluator_spec_manifest(data)


# ---------------------------------------------------------------------------
# 2b. loss_evaluator_spec_v1: residual_correspondence / reference_source
# （PR #331 第6巡指摘1・2、P1/P1、採用対応）
# ---------------------------------------------------------------------------


def test_h3c_loss_evaluator_residual_correspondence_matches_frozen_constants() -> None:
    data = _manifest_data("loss_evaluator_spec")
    correspondence = data["residual_correspondence"]
    assert (
        correspondence["definition_note"]
        == m._LOSS_EVALUATOR_EXPECTED_RESIDUAL_CORRESPONDENCE_DEFINITION_NOTE
    )
    assert correspondence["unit"] == m._LOSS_EVALUATOR_EXPECTED_RESIDUAL_CORRESPONDENCE_UNIT
    assert (
        correspondence["residual_formula"]
        == m._LOSS_EVALUATOR_EXPECTED_RESIDUAL_CORRESPONDENCE_RESIDUAL_FORMULA
    )
    for channel_name, expected_rule in m._LOSS_EVALUATOR_EXPECTED_RESIDUAL_CORRESPONDENCE_PER_CHANNEL.items():
        assert correspondence["per_channel_aggregation"][channel_name] == expected_rule


def test_h3c_loss_evaluator_residual_correspondence_missing_top_level_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["residual_correspondence"]
    with pytest.raises(m.Run9ValidationError, match="residual_correspondence"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_residual_correspondence_unit_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["residual_correspondence"]["unit"] = "frame"
    with pytest.raises(m.Run9ValidationError, match="residual_correspondence.unit"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_residual_correspondence_per_channel_tamper_rejected() -> None:
    # レビュー原文が挙げる具体的な汚染経路: 未凍結の warping/リサンプリング
    # 手法へ差し替え。
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["residual_correspondence"]["per_channel_aggregation"]["relative_f0"] = (
        "frame 数を線形リサンプリングで揃えてから elementwise 差分を取る"
    )
    with pytest.raises(
        m.Run9ValidationError, match=r"per_channel_aggregation\['relative_f0'\]"
    ):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_residual_correspondence_residual_formula_missing_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["residual_correspondence"]["residual_formula"]
    with pytest.raises(m.Run9ValidationError, match="residual_correspondence.residual_formula"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_residual_correspondence_normalized_energy_pairing_rule_tamper_rejected() -> None:
    # PR #331 第11巡指摘1（P1、採用）の直接テスト: normalized_energy の
    # ペア除外規則（lesson側・render側の双方にenergy blockが存在する
    # moraのみeligible）を、規則を落とした旧文言へ差し替えると拒否される
    # ことを確認する。
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["residual_correspondence"]["per_channel_aggregation"]["normalized_energy"] = (
        "phrase正規化（residual_extraction_spec.energy_normalization、phrase単位で先に適用）後、"
        "mora区間内のblock-RMS値の算術平均。"
    )
    with pytest.raises(
        m.Run9ValidationError, match=r"per_channel_aggregation\['normalized_energy'\]"
    ):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_reference_source_matches_frozen_constants() -> None:
    data = _manifest_data("loss_evaluator_spec")
    reference_source = data["reference_source"]
    assert reference_source["education"] == m._LOSS_EVALUATOR_EXPECTED_REFERENCE_SOURCE_EDUCATION
    assert reference_source["practice"] == m._LOSS_EVALUATOR_EXPECTED_REFERENCE_SOURCE_PRACTICE
    assert reference_source["common"] == m._LOSS_EVALUATOR_EXPECTED_REFERENCE_SOURCE_COMMON


def test_h3c_loss_evaluator_reference_source_missing_top_level_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["reference_source"]
    with pytest.raises(m.Run9ValidationError, match="reference_source"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_reference_source_practice_permits_education_lesson_rejected() -> None:
    # レビュー原文が挙げる具体的な汚染経路: PRACTICE の reference_source に
    # education lesson / precomputed teacher feature の入力を許す緩和文言
    # へ改変。
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["reference_source"]["practice"] = (
        "PRACTICE枝: 凍結済みTechnique lesson bundleの値をprecomputed teacher featureとして"
        "そのまま利用してよい。"
    )
    with pytest.raises(m.Run9ValidationError, match="reference_source.practice"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_reference_source_education_missing_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["reference_source"]["education"]
    with pytest.raises(m.Run9ValidationError, match="reference_source.education"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_reference_source_common_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["reference_source"]["common"] = "calibration_scale は枝ごとに独立に定める。"
    with pytest.raises(m.Run9ValidationError, match="reference_source.common"):
        m.validate_loss_evaluator_spec_manifest(data)


# ---------------------------------------------------------------------------
# 2c. loss_evaluator_spec_v1: aggregate_formula
# （PR #331 第10巡指摘1、P1、採用対応）
# ---------------------------------------------------------------------------


def test_h3c_loss_evaluator_aggregate_formula_matches_frozen_constants() -> None:
    data = _manifest_data("loss_evaluator_spec")
    aggregate_formula = data["aggregate_formula"]
    assert aggregate_formula["formula"] == m._LOSS_EVALUATOR_EXPECTED_AGGREGATE_FORMULA_FORMULA
    assert (
        aggregate_formula["measurable_definition"]
        == m._LOSS_EVALUATOR_EXPECTED_AGGREGATE_FORMULA_MEASURABLE_DEFINITION
    )
    for term_name, expected in m._LOSS_EVALUATOR_EXPECTED_AGGREGATE_FORMULA_TERM_DEFINITIONS.items():
        assert aggregate_formula["term_definitions"][term_name] == expected
    assert aggregate_formula["dtype"] == m._LOSS_EVALUATOR_EXPECTED_AGGREGATE_FORMULA_DTYPE
    assert (
        aggregate_formula["summation_order"]
        == m._LOSS_EVALUATOR_EXPECTED_AGGREGATE_FORMULA_SUMMATION_ORDER
    )
    assert (
        aggregate_formula["objective_direction"]
        == m._LOSS_EVALUATOR_EXPECTED_AGGREGATE_FORMULA_OBJECTIVE_DIRECTION
    )


def test_h3c_loss_evaluator_aggregate_formula_missing_top_level_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["aggregate_formula"]
    with pytest.raises(m.Run9ValidationError, match="aggregate_formula"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_scale_division_dropped_rejected() -> None:
    # レビュー原文が挙げる具体的な汚染経路: calibration_scale による正規化
    # （省略形）を落とした別式への差し替え。
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["aggregate_formula"]["formula"] = (
        "search_objective(candidate) = Σ_{c∈measurable} weight_c × residual_RMS_c"
    )
    with pytest.raises(m.Run9ValidationError, match="aggregate_formula.formula"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_measurable_definition_tamper_rejected() -> None:
    # レビュー原文が挙げる具体的な汚染経路: NOT_SCORABLE 優先を外し、欠測
    # channel を単純にスキップする（部分計測を許す）定義への緩和。
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["aggregate_formula"]["measurable_definition"] = (
        "measurable = eligible > 0 のchannel集合。NOT_SCORABLE規則との優先関係は問わない。"
    )
    with pytest.raises(m.Run9ValidationError, match="measurable_definition"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_weight_term_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["aggregate_formula"]["term_definitions"]["weight_c"] = "channelごとに可変とする。"
    with pytest.raises(m.Run9ValidationError, match=r"term_definitions\['weight_c'\]"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_term_definition_missing_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["aggregate_formula"]["term_definitions"]["residual_RMS_c"]
    with pytest.raises(m.Run9ValidationError, match=r"term_definitions\['residual_RMS_c'\]"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_dtype_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["aggregate_formula"]["dtype"] = "float32"
    with pytest.raises(m.Run9ValidationError, match="aggregate_formula.dtype"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_summation_order_missing_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    del data["aggregate_formula"]["summation_order"]
    with pytest.raises(m.Run9ValidationError, match="aggregate_formula.summation_order"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_objective_direction_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["aggregate_formula"]["objective_direction"] = "search_objectiveは最大化目的。"
    with pytest.raises(m.Run9ValidationError, match="aggregate_formula.objective_direction"):
        m.validate_loss_evaluator_spec_manifest(data)


def test_h3c_loss_evaluator_aggregate_formula_not_object_rejected() -> None:
    data = copy.deepcopy(_manifest_data("loss_evaluator_spec"))
    data["aggregate_formula"] = "search_objective = weighted sum"
    with pytest.raises(m.Run9ValidationError, match="aggregate_formula must be an object"):
        m.validate_loss_evaluator_spec_manifest(data)


# ---------------------------------------------------------------------------
# 3. candidate_generation_spec_v1 固有
# ---------------------------------------------------------------------------


def test_h3c_candidate_generation_seed_equals_learning_seed() -> None:
    data = _manifest_data("candidate_generation_spec")
    assert data["seed"] == m.LEARNING_SEED == 909002


def test_h3c_candidate_generation_seed_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["seed"] = 1
    with pytest.raises(m.Run9ValidationError, match="909002"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_32x4_128_alignment() -> None:
    data = _manifest_data("candidate_generation_spec")
    structure = data["structure"]
    assert structure["trial_count"] == 32
    assert structure["candidates_per_trial"] == 4
    assert structure["trial_count"] * structure["candidates_per_trial"] == 128
    assert structure["units_per_founder_per_arm"] == 128
    assert structure["total_units"] == 512


def test_h3c_candidate_generation_total_units_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["structure"]["total_units"] = 500
    with pytest.raises(m.Run9ValidationError, match="total_units"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_prohibited_missing_item_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["prohibited"] = [p for p in data["prohibited"] if p != "reseed"]
    with pytest.raises(m.Run9ValidationError, match="prohibited"):
        m.validate_candidate_generation_spec_manifest(data)


# --- PR #331 第2巡指摘2（P2、採用）: proposal.proposal_schedule_table 形状
# 強制のテスト。repin で恒等候補脱落・比率復元が起きても fail-closed で
# 拒否されることを確認する。


def test_h3c_candidate_generation_proposal_identity_candidate_dropped_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    # trial1 candidate0 の恒等ルールを hash-derived exploratory へ書き換え
    # る（恒等候補脱落を模す）。
    data["proposal"]["proposal_schedule_table"][0] = {
        "trial_index": 1,
        "candidate_index": 0,
        "rule": "hash-derived exploratory candidate (digest -> grid index)",
    }
    with pytest.raises(m.Run9ValidationError, match="proposal_schedule_table"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_ratio_restored_to_2_2_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    # trial 2-32 の内訳表記を旧・矛盾していた 2:2 へ戻す（第1巡で是正済みの
    # 退行を模す）。
    data["proposal"]["proposal_schedule_table"][2] = {
        "trial_index": "2..32",
        "candidate_index": "0..1",
        "rule": "current-best neighborhood",
    }
    data["proposal"]["proposal_schedule_table"][3] = {
        "trial_index": "2..32",
        "candidate_index": "2..3",
        "rule": "hash-derived exploratory candidates",
    }
    with pytest.raises(m.Run9ValidationError, match="proposal_schedule_table"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_missing_proposal_schedule_table_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["proposal"]["proposal_schedule_table"]
    with pytest.raises(m.Run9ValidationError, match="proposal"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_missing_digest_encoding_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["proposal"]["digest_encoding"]
    with pytest.raises(m.Run9ValidationError, match="proposal"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_digest_formula_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["digest_formula"] = 'digest = sha256(f"{seed}:{arm}:{founder}:{trial}:{candidate}")'
    with pytest.raises(m.Run9ValidationError, match="digest_formula"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_missing_exploratory_rule_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["proposal"]["exploratory_candidate_rule"]["probing_rule"]
    with pytest.raises(m.Run9ValidationError, match="exploratory_candidate_rule"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_byte_to_integer_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["exploratory_candidate_rule"]["byte_to_integer"] = "little-endian uint64"
    with pytest.raises(m.Run9ValidationError, match="byte_to_integer"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_probing_rule_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["exploratory_candidate_rule"]["probing_rule"] = "random retry"
    with pytest.raises(m.Run9ValidationError, match="probing_rule"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_missing_neighborhood_rule_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["proposal"]["neighborhood_candidate_rule"]["identity_neighbor_rule"]
    with pytest.raises(m.Run9ValidationError, match="neighborhood_candidate_rule"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_proposal_valid_manifest_passes() -> None:
    data = _manifest_data("candidate_generation_spec")
    m.validate_candidate_generation_spec_manifest(data)


# --- PR #331 第12巡指摘（P1、採用）: exploratory_candidate_rule.applies_to
# が no_best_handling/shortfall_handling の candidate 0..2 バックフィル
# 要求と矛盾していた旧文言（trial 2..32 の candidate 3 限定）への改ざんを
# fail-closed で拒否する。shortfall_handling 側の applies_to 相互参照
# 脱落も同様に拒否する（双方向参照）。


def test_h3c_candidate_generation_exploratory_applies_to_narrowed_to_candidate3_only_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    # 第11巡以前の旧文言（no_best_handling/shortfall_handling が要求する
    # candidate 0..2 バックフィルの適用範囲宣言が欠落した状態）への改ざんを模す。
    data["proposal"]["exploratory_candidate_rule"]["applies_to"] = (
        "trial 1 の candidate 1..3、および trial 2..32 の candidate 3"
        "（proposal_schedule_table の hash-derived exploratory 行すべて。"
        "digest はスロットごとに {trial}:{candidate} を差し替えて独立に計算する）"
    )
    with pytest.raises(m.Run9ValidationError, match="applies_to"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_shortfall_handling_applies_to_cross_reference_dropped_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["neighborhood_candidate_rule"]["shortfall_handling"] = (
        "未評価かつ有効な近傍候補が3件未満の場合、不足分は exploratory_candidate_rule の手順"
        "（不足している candidate index のスロットごとに digest を計算）で補充する。"
    )
    with pytest.raises(m.Run9ValidationError, match="shortfall_handling"):
        m.validate_candidate_generation_spec_manifest(data)


# --- PR #331 第4巡指摘2（P1、採用）: selection.tie_break の実行可能な全
# 順序凍結の validator 検査。旧版は selection 節の中身を一切検査していな
# かった欠落を埋めたテスト。


def test_h3c_candidate_generation_selection_tie_break_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["selection"]["tie_break"] = "(objective, 軸ベクトルの辞書順) の全順序で決定論的に一意の勝者を選ぶ"
    with pytest.raises(m.Run9ValidationError, match="tie_break"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_selection_missing_tie_break_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["selection"]["tie_break"]
    with pytest.raises(m.Run9ValidationError, match="selection"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_selection_not_object_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["selection"] = "not an object"
    with pytest.raises(m.Run9ValidationError, match="selection"):
        m.validate_candidate_generation_spec_manifest(data)


# --- PR #331 第8巡指摘1（P2「undersized L の run 前拒否ゲート」、採用）:
# `run_precondition` 節の validator 検査。


def test_h3c_candidate_generation_run_precondition_present_and_matches_frozen_constants() -> None:
    data = _manifest_data("candidate_generation_spec")
    run_precondition = data["run_precondition"]
    assert (
        run_precondition["minimum_candidate_space"]
        == m._CANDIDATE_GENERATION_EXPECTED_RUN_PRECONDITION_MINIMUM_CANDIDATE_SPACE
    )
    assert (
        run_precondition["required_minimum_formula"]
        == m._CANDIDATE_GENERATION_EXPECTED_RUN_PRECONDITION_REQUIRED_MINIMUM_FORMULA
    )


def test_h3c_candidate_generation_missing_run_precondition_top_level_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["run_precondition"]
    with pytest.raises(m.Run9ValidationError, match="run_precondition"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_run_precondition_minimum_candidate_space_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["run_precondition"]["minimum_candidate_space"] = "run 開始前の検査は行わない"
    with pytest.raises(m.Run9ValidationError, match="minimum_candidate_space"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_run_precondition_required_minimum_formula_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["run_precondition"]["required_minimum_formula"] = "required_minimum = 0"
    with pytest.raises(m.Run9ValidationError, match="required_minimum_formula"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_run_precondition_missing_required_minimum_formula_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["run_precondition"]["required_minimum_formula"]
    with pytest.raises(m.Run9ValidationError, match="run_precondition"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_run_precondition_not_object_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["run_precondition"] = "not an object"
    with pytest.raises(m.Run9ValidationError, match="run_precondition"):
        m.validate_candidate_generation_spec_manifest(data)


# --- PR #331 第8巡指摘2（P1「同一 trial 内の予約集合の凍結」、採用）:
# `proposal.reservation_semantics` の validator 検査。


def test_h3c_candidate_generation_reservation_semantics_matches_frozen_constant() -> None:
    data = _manifest_data("candidate_generation_spec")
    assert (
        data["proposal"]["reservation_semantics"]
        == m._CANDIDATE_GENERATION_EXPECTED_RESERVATION_SEMANTICS
    )


def test_h3c_candidate_generation_reservation_semantics_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["reservation_semantics"] = "予約集合は評価済みのみを見る"
    with pytest.raises(m.Run9ValidationError, match="reservation_semantics"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_missing_reservation_semantics_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["proposal"]["reservation_semantics"]
    with pytest.raises(m.Run9ValidationError, match="proposal"):
        m.validate_candidate_generation_spec_manifest(data)


# --- PR #331 第8巡指摘3（P2「spec リテラル domain の catalog 連合
# cross-check」、採用）: ax_p1/ax_d1 サブキー validator 検査 +
# load_pinned_candidate_generation_spec_manifest() の catalog cross-check。


def test_h3c_candidate_generation_ax_p1_missing_subkey_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["proposal"]["candidate_ordering"]["ax_p1"]["catalog_cross_check_note"]
    with pytest.raises(m.Run9ValidationError, match="ax_p1"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_ax_p1_offset_domain_wrong_type_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["candidate_ordering"]["ax_p1"]["offset_domain"] = "not a list"
    with pytest.raises(m.Run9ValidationError, match="offset_domain"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_ax_p1_offset_domain_empty_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["candidate_ordering"]["ax_p1"]["offset_domain"] = []
    with pytest.raises(m.Run9ValidationError, match="offset_domain"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_ax_d1_missing_subkey_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["proposal"]["candidate_ordering"]["ax_d1"]["quantization_step_beats"]
    with pytest.raises(m.Run9ValidationError, match="ax_d1"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_ax_d1_quantization_step_beats_non_positive_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["candidate_ordering"]["ax_d1"]["quantization_step_beats"] = 0
    with pytest.raises(m.Run9ValidationError, match="quantization_step_beats"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_ax_d1_min_duration_beats_non_positive_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["proposal"]["candidate_ordering"]["ax_d1"]["min_duration_beats"] = -0.25
    with pytest.raises(m.Run9ValidationError, match="min_duration_beats"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_catalog_cross_check_passes_on_real_pinned_manifests(
    contract: m.Run9RunContract,
) -> None:
    # load_pinned_candidate_generation_spec_manifest() は本 cross-check を
    # 内部で通す（正常系はこれが raise しないことで既に
    # test_h3c_load_pinned_happy_path でも間接検証されているが、本テストは
    # cross-check の意図を明示する直接テスト）。
    data = m.load_pinned_candidate_generation_spec_manifest(contract)
    catalog = m.load_pinned_score_axis_catalog_manifest(contract)
    import candidate_proposal as cp  # noqa: E402

    expected_offset_domain = list(cp.ax_p1_offset_domain_from_catalog(catalog))
    assert (
        data["proposal"]["candidate_ordering"]["ax_p1"]["offset_domain"] == expected_offset_domain
    )
    assert (
        data["proposal"]["candidate_ordering"]["ax_d1"]["quantization_step_beats"]
        == catalog["axes"]["AX-D1"]["quantization_step_beats"]
    )
    assert (
        data["proposal"]["candidate_ordering"]["ax_d1"]["min_duration_beats"]
        == catalog["axes"]["AX-D1"]["min_duration_beats"]
    )


def test_h3c_candidate_generation_catalog_cross_check_catches_stale_offset_domain(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    # spec 側の offset_domain を（構造的には妥当なまま）catalog 由来の値と
    # ずらす — catalog repin 後に spec 側リテラルが追随しなかった状況を
    # 模す。structural validate は通過するが catalog cross-check で拒否
    # されることを確認する。
    def _mutate(data: Dict[str, Any]) -> None:
        data["proposal"]["candidate_ordering"]["ax_p1"]["offset_domain"] = [
            -1.0, -0.5, 0.5, 1.0,
        ]

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key="candidate_generation_spec", mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="offset_domain"):
        m.load_pinned_candidate_generation_spec_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_h3c_candidate_generation_catalog_cross_check_catches_stale_quantization_step_beats(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["proposal"]["candidate_ordering"]["ax_d1"]["quantization_step_beats"] = 0.5

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key="candidate_generation_spec", mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="quantization_step_beats"):
        m.load_pinned_candidate_generation_spec_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_h3c_candidate_generation_catalog_cross_check_catches_stale_min_duration_beats(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["proposal"]["candidate_ordering"]["ax_d1"]["min_duration_beats"] = 0.5

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key="candidate_generation_spec", mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="min_duration_beats"):
        m.load_pinned_candidate_generation_spec_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


# --- PR #331 第9巡指摘1（P2「practice_actor_binding の内容検証」、採用）:
# 旧版は practice_actor_binding 節がトップレベル必須キーとしてのみ検査
# されており中身は無検査だった欠落を埋めたテスト。


def test_h3c_candidate_generation_practice_actor_binding_present_and_matches_frozen_constants() -> None:
    data = _manifest_data("candidate_generation_spec")
    practice_actor_binding = data["practice_actor_binding"]
    assert (
        practice_actor_binding["target_selection"]
        == m._CANDIDATE_GENERATION_EXPECTED_PRACTICE_ACTOR_BINDING_TARGET_SELECTION
    )
    assert (
        practice_actor_binding["difference_estimation"]
        == m._CANDIDATE_GENERATION_EXPECTED_PRACTICE_ACTOR_BINDING_DIFFERENCE_ESTIMATION
    )
    assert (
        practice_actor_binding["trace_storage"]
        == m._CANDIDATE_GENERATION_EXPECTED_PRACTICE_ACTOR_BINDING_TRACE_STORAGE
    )


def test_h3c_candidate_generation_missing_practice_actor_binding_top_level_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["practice_actor_binding"]
    with pytest.raises(m.Run9ValidationError, match="practice_actor_binding"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_not_object_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["practice_actor_binding"] = "not an object"
    with pytest.raises(m.Run9ValidationError, match="practice_actor_binding"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_hollowed_out_rejected() -> None:
    # repin で中身が空洞化した状況を模す（値を空文字列に）。
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["practice_actor_binding"]["target_selection"] = ""
    data["practice_actor_binding"]["difference_estimation"] = ""
    with pytest.raises(m.Run9ValidationError, match="target_selection"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_missing_target_selection_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["practice_actor_binding"]["target_selection"]
    with pytest.raises(m.Run9ValidationError, match="practice_actor_binding"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_missing_difference_estimation_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["practice_actor_binding"]["difference_estimation"]
    with pytest.raises(m.Run9ValidationError, match="practice_actor_binding"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_missing_trace_storage_key_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    del data["practice_actor_binding"]["trace_storage"]
    with pytest.raises(m.Run9ValidationError, match="practice_actor_binding"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_target_selection_tamper_rejected() -> None:
    # target_selection/difference_estimation が Founder-local actor 外へ
    # 移動する緩和文言に置き換わっても拒否されることを確認する。
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["practice_actor_binding"]["target_selection"] = "外部選択を許可する"
    with pytest.raises(m.Run9ValidationError, match="target_selection"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_difference_estimation_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["practice_actor_binding"]["difference_estimation"] = "外部選択を許可する"
    with pytest.raises(m.Run9ValidationError, match="difference_estimation"):
        m.validate_candidate_generation_spec_manifest(data)


def test_h3c_candidate_generation_practice_actor_binding_trace_storage_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("candidate_generation_spec"))
    data["practice_actor_binding"]["trace_storage"] = "trace は保存しない"
    with pytest.raises(m.Run9ValidationError, match="trace_storage"):
        m.validate_candidate_generation_spec_manifest(data)


# ---------------------------------------------------------------------------
# 4. compute_budget_manifest_v1 固有
# ---------------------------------------------------------------------------


def test_h3c_compute_budget_provider_is_cpu_only() -> None:
    data = _manifest_data("compute_budget_manifest")
    assert data["provider"]["selected_execution_provider"] == "CPUExecutionProvider"
    assert data["provider"]["auto_fallback_prohibited"] is True


def test_h3c_compute_budget_provider_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("compute_budget_manifest"))
    data["provider"]["selected_execution_provider"] = "CUDAExecutionProvider"
    with pytest.raises(m.Run9ValidationError, match="CPUExecutionProvider"):
        m.validate_compute_budget_manifest(data)


def test_h3c_compute_budget_total_search_budget_is_512() -> None:
    data = _manifest_data("compute_budget_manifest")
    assert data["total_search_budget"]["value"] == 512


def test_h3c_compute_budget_execution_profile_reference_matches_contract(
    contract: m.Run9RunContract,
) -> None:
    data = _manifest_data("compute_budget_manifest")
    manifest_ref = data["provider"]["execution_profile_sha_reference"]["value"]
    contract_value = contract.pin_field("execution_profile_sha")["value"]
    assert manifest_ref == contract_value


def test_h3c_compute_budget_execution_profile_reference_mismatch_rejected(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["provider"]["execution_profile_sha_reference"]["value"] = "a" * 64

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key="compute_budget_manifest", mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="execution_profile_sha"):
        m.load_pinned_compute_budget_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


# ---------------------------------------------------------------------------
# 5. learning_data_binding_manifest_v1 固有
# ---------------------------------------------------------------------------


def test_h3c_data_binding_bindings_match_contract(contract: m.Run9RunContract) -> None:
    data = _manifest_data("learning_data_binding_manifest")
    for pin_name in (
        "practice_audio_split_manifest_sha",
        "pjs_consumed_inputs_manifest_sha",
        "education_technique_lesson_manifest_sha",
    ):
        assert data["bindings"][pin_name] == contract.pin_field(pin_name)["value"]


def test_h3c_data_binding_mismatch_rejected_fail_closed(
    contract: m.Run9RunContract, tmp_path: Path,
) -> None:
    def _mutate(data: Dict[str, Any]) -> None:
        data["bindings"]["practice_audio_split_manifest_sha"] = "b" * 64

    tampered_contract, manifest_path, contract_path = _tampered_contract(
        contract, tmp_path, key="learning_data_binding_manifest", mutate=_mutate,
    )
    with pytest.raises(m.Run9ValidationError, match="practice_audio_split_manifest_sha"):
        m.load_pinned_learning_data_binding_manifest(
            tampered_contract, manifest_path=manifest_path, contract_path=contract_path,
        )


def test_h3c_data_binding_practice_excludes_education_lesson() -> None:
    data = _manifest_data("learning_data_binding_manifest")
    assert "education_technique_lesson_manifest_sha" not in data["branch_usage"]["practice"]["uses"]


def test_h3c_data_binding_practice_uses_education_lesson_rejected() -> None:
    data = copy.deepcopy(_manifest_data("learning_data_binding_manifest"))
    data["branch_usage"]["practice"]["uses"].append("education_technique_lesson_manifest_sha")
    with pytest.raises(m.Run9ValidationError, match="practice.uses"):
        m.validate_learning_data_binding_manifest(data)


def test_h3c_data_binding_education_raw_audio_exclusion_tamper_rejected() -> None:
    data = copy.deepcopy(_manifest_data("learning_data_binding_manifest"))
    data["branch_usage"]["education"]["excludes_raw_audio_direct_input"] = False
    with pytest.raises(m.Run9ValidationError, match="excludes_raw_audio_direct_input"):
        m.validate_learning_data_binding_manifest(data)


# --- PR #331 第1巡 採用4: branch uses 集合の厳密一致検証 -------------------


def test_h3c_data_binding_practice_uses_missing_element_rejected() -> None:
    """practice.uses から必須要素が欠落したら拒否する（厳密集合一致）。"""
    data = copy.deepcopy(_manifest_data("learning_data_binding_manifest"))
    data["branch_usage"]["practice"]["uses"] = ["practice_audio_split_manifest_sha"]
    with pytest.raises(m.Run9ValidationError, match="practice.uses"):
        m.validate_learning_data_binding_manifest(data)


def test_h3c_data_binding_practice_uses_extra_element_rejected() -> None:
    """practice.uses に許可外の要素が混入したら拒否する（厳密集合一致 —
    education 混入以外の任意の過剰要素も拒否対象であることを確認する）。"""
    data = copy.deepcopy(_manifest_data("learning_data_binding_manifest"))
    data["branch_usage"]["practice"]["uses"].append("unexpected_extra_pin_sha")
    with pytest.raises(m.Run9ValidationError, match="practice.uses"):
        m.validate_learning_data_binding_manifest(data)


def test_h3c_data_binding_practice_uses_empty_rejected() -> None:
    """practice.uses が空リストなら拒否する。"""
    data = copy.deepcopy(_manifest_data("learning_data_binding_manifest"))
    data["branch_usage"]["practice"]["uses"] = []
    with pytest.raises(m.Run9ValidationError, match="practice.uses"):
        m.validate_learning_data_binding_manifest(data)


def test_h3c_data_binding_education_uses_missing_rejected() -> None:
    """education.uses が欠落（None/空）したら拒否する。"""
    data = copy.deepcopy(_manifest_data("learning_data_binding_manifest"))
    data["branch_usage"]["education"]["uses"] = []
    with pytest.raises(m.Run9ValidationError, match="education.uses"):
        m.validate_learning_data_binding_manifest(data)


def test_h3c_data_binding_education_uses_extra_element_rejected() -> None:
    """education.uses に許可外の要素（例: practice 側 pin の混入）が
    混入したら拒否する（既存の「education pin の practice 混入禁止」検査を
    包含する厳密集合一致）。"""
    data = copy.deepcopy(_manifest_data("learning_data_binding_manifest"))
    data["branch_usage"]["education"]["uses"].append("practice_audio_split_manifest_sha")
    with pytest.raises(m.Run9ValidationError, match="education.uses"):
        m.validate_learning_data_binding_manifest(data)


# =============================================================================
# score_axis_transform.py: Composition 不変条件 + catalog 制約拒否
# （W1b の 17 unit ケースの repo テスト化）
# =============================================================================


_CATALOG = _manifest_data("score_axis_catalog")

_BASELINE_NOTES = [
    {"kana": "さ", "midi": 64.0, "duration_beats": 1.0, "phrase_index": 0, "is_phrase_final": False},
    {"kana": "く", "midi": 64.0, "duration_beats": 1.0, "phrase_index": 0, "is_phrase_final": False},
    {"kana": "ら", "midi": 65.0, "duration_beats": 2.0, "phrase_index": 0, "is_phrase_final": True},
]

# PR #331 第1巡 採用5（same-phrase 検査）用: 2 phrase にまたがる baseline。
_TWO_PHRASE_NOTES = [
    {"kana": "さ", "midi": 64.0, "duration_beats": 1.0, "phrase_index": 0, "is_phrase_final": False},
    {"kana": "く", "midi": 64.0, "duration_beats": 1.0, "phrase_index": 0, "is_phrase_final": True},
    {"kana": "ら", "midi": 65.0, "duration_beats": 2.0, "phrase_index": 1, "is_phrase_final": False},
    {"kana": "ん", "midi": 65.0, "duration_beats": 1.0, "phrase_index": 1, "is_phrase_final": True},
]


# --- Composition 不変条件 ----------------------------------------------------


def test_h3c_verify_composition_invariants_passes_on_identical_specs() -> None:
    sat.verify_composition_invariants(_BASELINE_NOTES, copy.deepcopy(_BASELINE_NOTES))


def test_h3c_verify_composition_invariants_rejects_note_count_change() -> None:
    variant = copy.deepcopy(_BASELINE_NOTES)[:2]
    with pytest.raises(sat.CompositionInvariantViolation, match="note数不一致"):
        sat.verify_composition_invariants(_BASELINE_NOTES, variant)


def test_h3c_verify_composition_invariants_rejects_kana_change() -> None:
    variant = copy.deepcopy(_BASELINE_NOTES)
    variant[0]["kana"] = "み"
    with pytest.raises(sat.CompositionInvariantViolation, match="kana"):
        sat.verify_composition_invariants(_BASELINE_NOTES, variant)


def test_h3c_verify_composition_invariants_rejects_phrase_index_change() -> None:
    variant = copy.deepcopy(_BASELINE_NOTES)
    variant[0]["phrase_index"] = 1
    with pytest.raises(sat.CompositionInvariantViolation, match="phrase_index"):
        sat.verify_composition_invariants(_BASELINE_NOTES, variant)


def test_h3c_verify_composition_invariants_rejects_is_phrase_final_change() -> None:
    variant = copy.deepcopy(_BASELINE_NOTES)
    variant[0]["is_phrase_final"] = True
    with pytest.raises(sat.CompositionInvariantViolation, match="is_phrase_final"):
        sat.verify_composition_invariants(_BASELINE_NOTES, variant)


def test_h3c_apply_ax_p1_only_touches_midi() -> None:
    variant = sat.apply_ax_p1(_BASELINE_NOTES, note_index=2, offset_semitones=1.0, catalog=_CATALOG)
    assert variant[2]["midi"] == pytest.approx(66.0)
    assert variant[0] == _BASELINE_NOTES[0]
    assert variant[1] == _BASELINE_NOTES[1]
    assert variant[2]["kana"] == _BASELINE_NOTES[2]["kana"]
    assert variant[2]["phrase_index"] == _BASELINE_NOTES[2]["phrase_index"]
    assert variant[2]["is_phrase_final"] == _BASELINE_NOTES[2]["is_phrase_final"]
    # 入力は変更されない
    assert _BASELINE_NOTES[2]["midi"] == 65.0


def test_h3c_apply_ax_d1_preserves_total_beats() -> None:
    variant = sat.apply_ax_d1(
        _BASELINE_NOTES, note_indices=[1, 2], deltas_beats=[-0.25, 0.25], catalog=_CATALOG,
    )
    total_before = sum(n["duration_beats"] for n in _BASELINE_NOTES)
    total_after = sum(n["duration_beats"] for n in variant)
    assert total_after == pytest.approx(total_before)
    assert variant[1]["duration_beats"] == pytest.approx(0.75)
    assert variant[2]["duration_beats"] == pytest.approx(2.25)


@pytest.mark.parametrize(
    "note_indices,deltas_beats",
    [
        ([0, 1], [-0.25, 0.25]),
        ([1, 0], [0.25, -0.25]),
        ([0, 1, 2], [-0.25, 0.0, 0.25]),
        ([2, 1, 0], [0.5, -0.25, -0.25]),
    ],
)
def test_h3c_apply_ax_d1_preserves_total_beats_property(
    note_indices: list[int], deltas_beats: list[float]
) -> None:
    """PR #331 第1巡 採用1 対応: 合計保存の性質テスト強化。index の重複が
    ない複数の順序・組み合わせで、beat 合計保存が常に成り立つことを確認する
    （重複 index による silent な保存崩れは別テストで拒否を確認する）。"""
    variant = sat.apply_ax_d1(
        _BASELINE_NOTES, note_indices=note_indices, deltas_beats=deltas_beats, catalog=_CATALOG,
    )
    total_before = sum(n["duration_beats"] for n in _BASELINE_NOTES)
    total_after = sum(n["duration_beats"] for n in variant)
    assert total_after == pytest.approx(total_before)
    for idx, delta in zip(note_indices, deltas_beats):
        expected = _BASELINE_NOTES[idx]["duration_beats"] + delta
        assert variant[idx]["duration_beats"] == pytest.approx(expected)


def test_h3c_apply_ax_d1_rejects_duplicate_note_indices() -> None:
    """PR #331 第1巡 採用1: [0,0] + delta [0.25,-0.25] は zero-sum 検査を通過
    するが、同一 note への代入上書きで実質的な beat 合計保存が破れるため
    fail-closed で拒否されなければならない。"""
    with pytest.raises(sat.ScoreAxisTransformError, match=r"重複"):
        sat.apply_ax_d1(
            _BASELINE_NOTES, note_indices=[0, 0], deltas_beats=[0.25, -0.25], catalog=_CATALOG,
        )
    # 拒否後も baseline は不変
    assert _BASELINE_NOTES[0]["duration_beats"] == 1.0


def test_h3c_apply_ax_d1_rejects_note_indices_spanning_multiple_phrases() -> None:
    """PR #331 第1巡 採用5: note_indices が異なる phrase_index にまたがる
    場合、global zero-sum は通過し得るが凍結定義の「phrase 内再配分」に
    違反するため fail-closed で拒否されなければならない。"""
    with pytest.raises(sat.ScoreAxisTransformError, match="phrase"):
        sat.apply_ax_d1(
            _TWO_PHRASE_NOTES, note_indices=[1, 2], deltas_beats=[-0.25, 0.25], catalog=_CATALOG,
        )
    # 拒否後も baseline は不変
    assert _TWO_PHRASE_NOTES[1]["duration_beats"] == 1.0
    assert _TWO_PHRASE_NOTES[2]["duration_beats"] == 2.0


def test_h3c_apply_ax_d1_same_phrase_redistribution_still_accepted() -> None:
    """採否5 の回帰確認: 単一 phrase 内の再配分は引き続き正常に受理される
    （_TWO_PHRASE_NOTES の phrase_index=1 の 2 note 間）。"""
    variant = sat.apply_ax_d1(
        _TWO_PHRASE_NOTES, note_indices=[2, 3], deltas_beats=[-0.25, 0.25], catalog=_CATALOG,
    )
    assert variant[2]["duration_beats"] == pytest.approx(1.75)
    assert variant[3]["duration_beats"] == pytest.approx(1.25)


def test_h3c_apply_ax_p1_int_and_float_offset_converge_to_same_midi() -> None:
    """W1b 実測: float -2.0 オフセットと int -2 半音オフセットは同じ数値
    63.0 へ収束する（既知の型挙動）。"""
    variant_int = sat.apply_ax_p1(_BASELINE_NOTES, note_index=2, offset_semitones=-2, catalog=_CATALOG)
    variant_float = sat.apply_ax_p1(
        _BASELINE_NOTES, note_index=2, offset_semitones=-2.0, catalog=_CATALOG
    )
    assert variant_int[2]["midi"] == variant_float[2]["midi"] == 63.0


# --- AX-P1 catalog 制約拒否（W1b 10ケース） ---------------------------------


@pytest.mark.parametrize(
    "offset,expect_reject",
    [
        (2.5, True),
        (-2.5, True),
        (0.3, True),
        (float("nan"), True),
        (float("inf"), True),
        (float("-inf"), True),
        (2.0, False),
        (-2.0, False),
        (0.5, False),
        (0.0, False),
    ],
)
def test_h3c_validate_ax_p1_offset_catalog_constraints(offset: float, expect_reject: bool) -> None:
    if expect_reject:
        with pytest.raises(sat.CatalogRejected):
            sat.validate_ax_p1_offset(offset, catalog=_CATALOG)
    else:
        assert sat.validate_ax_p1_offset(offset, catalog=_CATALOG) == pytest.approx(offset)


# --- AX-D1 catalog 制約拒否（W1b 7ケース） ----------------------------------


@pytest.mark.parametrize(
    "deltas,expect_reject",
    [
        ([0.5, -0.25], True),  # sum != 0
        ([-1.0, 1.0], True),  # post below min
        ([0.1, -0.1], True),  # non-grid
        ([float("nan"), 0.0], True),
        ([-0.25, 0.25], False),
        ([-0.75, 0.75], False),  # boundary: post == 0.25
        ([0.0, 0.0], False),
    ],
)
def test_h3c_validate_ax_d1_delta_vector_catalog_constraints(deltas, expect_reject: bool) -> None:
    original = [1.0, 2.0]
    if expect_reject:
        with pytest.raises(sat.CatalogRejected):
            sat.validate_ax_d1_delta_vector(original, deltas, catalog=_CATALOG)
    else:
        result = sat.validate_ax_d1_delta_vector(original, deltas, catalog=_CATALOG)
        assert result == pytest.approx(deltas)


def test_h3c_apply_ax_p1_out_of_range_rejected_and_notes_unchanged() -> None:
    with pytest.raises(sat.CatalogRejected):
        sat.apply_ax_p1(_BASELINE_NOTES, note_index=2, offset_semitones=2.5, catalog=_CATALOG)
    # 拒否後も baseline は不変
    assert _BASELINE_NOTES[2]["midi"] == 65.0


def test_h3c_apply_ax_d1_min_duration_violation_rejected() -> None:
    with pytest.raises(sat.CatalogRejected):
        sat.apply_ax_d1(
            _BASELINE_NOTES, note_indices=[0, 1], deltas_beats=[-1.0, 1.0], catalog=_CATALOG,
        )
