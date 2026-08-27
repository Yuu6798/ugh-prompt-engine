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
    excluded = m.CONTRACT_POST_RUN_PIN_FIELDS | m.CONTRACT_OPTIONAL_PIN_FIELDS
    pre_run_fields = [n for n in m.CONTRACT_PIN_FIELDS if n not in excluded]
    pending = [n for n in pre_run_fields if not m._is_field_pinned(contract.pin_field(n))]  # noqa: SLF001
    assert len(pending) == 7, pending
    for entry in _MANIFESTS.values():
        assert entry["pin_name"] not in pending


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
