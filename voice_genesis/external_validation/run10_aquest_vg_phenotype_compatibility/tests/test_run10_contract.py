"""test_run10_contract.py — RUN10 Phase 0 スキャフォールドの最低テスト。

DESIGN_RUN10 §28「最低テスト」121 項目のうち、本 PR の範囲（契約機械化・
公開境界・AF01 凍結検証・Pre-Run Inventory）で静的に検証できるサブセットを
実装する。各テストの docstring に §28 の項目番号を対応づける。

音声処理・実測を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import copy
import inspect
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

_THIS_DIR = Path(__file__).resolve().parent
_RUN_DIR = _THIS_DIR.parent
if str(_RUN_DIR) not in sys.path:
    sys.path.insert(0, str(_RUN_DIR))

import af01_freeze_verifier as verifier  # noqa: E402
import run10_schema as m  # noqa: E402

CONTRACT_PATH = _RUN_DIR / "RUN10_CONTRACT.yaml"
RIGHTS_PATH = _RUN_DIR / "inputs" / "rights_manifest.json"
PRIVATE_POLICY_PATH = _RUN_DIR / "inputs" / "private_storage_policy.json"


@pytest.fixture(scope="module")
def contract_doc() -> Dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract(contract_doc: Dict[str, Any]) -> m.Run10Contract:
    return m.parse_run10_contract(contract_doc)


def _pin_value(name: str) -> Any:
    """欄の形式契約を満たすダミー値（形式が緩んだら本 helper が壊れる）。"""
    if name == "attempt_id":
        return "RUN10-ATTEMPT-99"
    if name == "repository_commit_sha":
        return "b" * 40
    if name == "minimum_generatable_traits":
        return 3
    return "a" * 64


def _fully_pinned(doc: Dict[str, Any]) -> Dict[str, Any]:
    """全 pin 欄を PINNED にした contract を作る（gate PASS 側の検証用）。"""
    filled = copy.deepcopy(doc)
    for name in m.ALL_PIN_FIELDS:
        filled[name] = {"value": _pin_value(name), "status": "PINNED"}
    for name in m.COST_CAP_FIELDS:
        filled["cost_cap"][name] = {"value": 1, "status": "PINNED"}
    return filled


# --- §28-1 / §28-2: contract の完全性と fail-closed -------------------------


def test_contract_parses_and_reports_true_gate_state(contract: m.Run10Contract) -> None:
    """§28-1: R10-G0 は Core 欄が全て pin 済みのときだけ PASS になる。

    現時点の contract は A0 未取得・measurement 層未実装のため BLOCKED である
    ことをそのまま検証する（捏造して PASS にしない）。
    """
    assert contract.gate_r10_g0() == "BLOCKED"
    missing = contract.missing("CORE")
    assert missing, "Core 欄が全て pin 済みなら BLOCKED にならないはず"
    # PINNED 済みの欄は AF01 由来の 4 件 + attempt_id。
    pinned = [n for n in m.CORE_PIN_FIELDS if contract.pin(n).pinned]
    assert set(pinned) == {
        "attempt_id",
        "vg_reference_manifest_sha",
        "vg_body_artifact_sha",
        "e0_external_calibration_source_sha",
        "e0_af01_sf1_truth_sha",
    }


def test_fully_pinned_contract_passes_core_gate(contract_doc: Dict[str, Any]) -> None:
    """§28-1: 全 pin が埋まれば R10-G0 は PASS へ遷移する（gate が常時 BLOCKED でない）。"""
    filled = m.parse_run10_contract(_fully_pinned(contract_doc))
    assert filled.gate_r10_g0() == "PASS"
    for stage in m.CONTRACT_STAGES:
        assert filled.stage_state(stage) == "PASS"


def test_unknown_contract_field_fails_closed(contract_doc: Dict[str, Any]) -> None:
    """§28-2: 未知欄は拒否する。"""
    bad = copy.deepcopy(contract_doc)
    bad["undocumented_extra_field"] = 1
    with pytest.raises(m.Run10ContractError, match="未知の欄"):
        m.parse_run10_contract(bad)


def test_missing_pin_field_fails_closed(contract_doc: Dict[str, Any]) -> None:
    """§28-2: 欠落キーをデフォルト補完しない。"""
    bad = copy.deepcopy(contract_doc)
    del bad["resampler_sha"]
    with pytest.raises(m.Run10ContractError, match="pin 欄が無い"):
        m.parse_run10_contract(bad)


@pytest.mark.parametrize(
    "mutation, pattern",
    [
        ({"status": "PINNED", "value": None}, "value が null"),
        ({"status": "PINNED", "value": "pinned::resampler_sha"}, "sha256 は小文字 16 進"),
        ({"status": "PINNED", "value": "A" * 64}, "sha256 は小文字 16 進"),
        ({"status": "PINNED", "value": 1}, "sha256 は小文字 16 進"),
        ({"status": "PENDING", "value": "leaked"}, "value が非 null"),
        ({"status": "MAYBE", "value": None}, "未知の status"),
        ({"status": "PENDING", "value": None}, "reason が必須"),
    ],
)
def test_pin_field_shape_is_fail_closed(
    contract_doc: Dict[str, Any], mutation: Dict[str, Any], pattern: str
) -> None:
    """§28-2: pin 欄の {value, status, reason} 整合を強制する。"""
    bad = copy.deepcopy(contract_doc)
    bad["resampler_sha"] = dict(mutation)
    with pytest.raises(m.Run10ContractError, match=pattern):
        m.parse_run10_contract(bad)


def test_blocked_status_only_allowed_for_optional_references(
    contract_doc: Dict[str, Any],
) -> None:
    """§7.7 / §21 R10-G2: BLOCKED を許すのは AF-P0 / AF0 の 2 欄だけ。"""
    ok = copy.deepcopy(contract_doc)
    ok["af0_canonical_artifact_sha"] = {
        "value": None,
        "status": "BLOCKED",
        "reason": "optional historical reference",
    }
    parsed = m.parse_run10_contract(ok)
    assert "af0_canonical_artifact_sha" not in parsed.missing("CORE")

    bad = copy.deepcopy(contract_doc)
    bad["resampler_sha"] = {"value": None, "status": "BLOCKED", "reason": "x"}
    with pytest.raises(m.Run10ContractError, match="BLOCKED は許されない"):
        m.parse_run10_contract(bad)


# --- §28-3: 旧 RUN10 案の supersession --------------------------------------


def test_old_run10_design_is_superseded_before_execution(contract_doc: Dict[str, Any]) -> None:
    """§28-3 / §1.2: 旧 RUN10 案は実行前 supersede として記録されている。"""
    entries = {e["document"]: e["status"] for e in contract_doc["supersedes"]}
    assert entries[m.SUPERSEDED_DESIGN_DOCUMENT] == "SUPERSEDED_BEFORE_EXECUTION"


def test_missing_supersession_fails_closed(contract_doc: Dict[str, Any]) -> None:
    """§28-3: supersede 記録が無い contract は拒否する。"""
    bad = copy.deepcopy(contract_doc)
    bad["supersedes"] = [{"document": "other", "status": "SUPERSEDED_BEFORE_EXECUTION"}]
    with pytest.raises(m.Run10ContractError, match="supersede 記録が無い"):
        m.parse_run10_contract(bad)


# --- §28-5 / §28-6: rights manifest / private storage policy ----------------


def test_rights_manifest_declares_full_boundary() -> None:
    """§28-5 / §2.2: 権利境界 5 項目が prohibited として宣言されている。"""
    import json

    doc = json.loads(RIGHTS_PATH.read_text(encoding="utf-8"))
    assert doc["research_scope"] == "personal_private"
    boundaries = doc["boundaries"]
    assert boundaries["third_party_distribution"] == "prohibited"
    assert boundaries["public_audio_release"] == "prohibited"
    assert boundaries["public_model_release"] == "prohibited"
    assert boundaries["public_synthesis_system_release"] == "prohibited"
    assert boundaries["external_listener_panel"] == "prohibited_without_new_permission"
    undetermined = doc["undetermined_publication_categories"]
    assert undetermined["run10_disposition"] == "DO_NOT_PUBLISH"
    assert set(undetermined["categories"]) == {
        "analysis_tables",
        "aggregate_values",
        "design_documents",
    }


def test_private_storage_policy_records_public_repo_adjudication() -> None:
    """§28-6 / §32-2: R10-PUB-1 の裁定が日付つきで記録されている。

    User 裁定 2026-08-27 = 「実装コードのみ public で継続」。裁定は §33 により
    User に属し、実装側が勝手に「解決済み」と書ける項目ではない。
    """
    import json

    doc = json.loads(PRIVATE_POLICY_PATH.read_text(encoding="utf-8"))
    question = doc["blocking_question"]
    assert question["adjudicator"] == "User"
    assert question["adjudication"] == "APPROVED_CODE_ONLY_PUBLIC"
    assert question["adjudicated_at"] == "2026-08-27"
    assert question["interim_disposition"] == "ADOPTED_AS_STANDING_POLICY"
    assert "設計文書本文" in question["adjudication_record"]


def test_private_storage_policy_pin_is_not_frozen(contract: m.Run10Contract) -> None:
    """§32-2: staging root が未確定の間は private_storage_policy_sha を PINNED にしない。

    R10-PUB-1 の裁定は下りたが、§26 private staging root の実体が未確定である
    以上、方針文書は凍結できない（残件は `residual_unresolved`）。
    """
    import json

    doc = json.loads(PRIVATE_POLICY_PATH.read_text(encoding="utf-8"))
    assert doc["private_staging"]["verified"] is False
    assert doc["residual_unresolved"]["items"] == ["private_staging.root"]
    assert contract.pin("private_storage_policy_sha").status == "PENDING"


# --- §28-13: Performance 除外 ----------------------------------------------


def test_performance_analysis_field_is_out_of_scope(contract_doc: Dict[str, Any]) -> None:
    """§28-13 / §6 / H7: performance_analysis は OUT_OF_SCOPE 固定。"""
    assert contract_doc["performance_analysis"] == "OUT_OF_SCOPE"
    bad = copy.deepcopy(contract_doc)
    bad["performance_analysis"] = "PRIMARY"
    with pytest.raises(m.Run10ContractError, match="performance_analysis"):
        m.parse_run10_contract(bad)


def test_phase_a_is_non_interventional(contract_doc: Dict[str, Any]) -> None:
    """§23: Phase A の changed_edge は NONE_OBSERVATIONAL_AUDIT 固定。"""
    assert (
        contract_doc["staged_intervention"]["phase_a"]["changed_edge"]
        == "NONE_OBSERVATIONAL_AUDIT"
    )
    bad = copy.deepcopy(contract_doc)
    bad["staged_intervention"]["phase_a"]["changed_edge"] = "LEARN_PERFORMANCE"
    with pytest.raises(m.Run10ContractError, match="NONE_OBSERVATIONAL_AUDIT"):
        m.parse_run10_contract(bad)


def test_phase_b_is_conditional(contract_doc: Dict[str, Any]) -> None:
    """§0 / §23: Phase B は自動開始しない（activation = CONDITIONAL）。"""
    assert contract_doc["staged_intervention"]["phase_b"]["activation"] == "CONDITIONAL"
    bad = copy.deepcopy(contract_doc)
    bad["staged_intervention"]["phase_b"]["activation"] = "ALWAYS"
    with pytest.raises(m.Run10ContractError, match="CONDITIONAL"):
        m.parse_run10_contract(bad)


# --- §28-19/20: AF01 凍結識別子の contract 側整合 ---------------------------


def test_af01_frozen_hashes_match_pinned_ledger(contract_doc: Dict[str, Any]) -> None:
    """§28-19 / §28-20: contract の AF01 凍結値が同梱台帳の実体と一致する。"""
    entries = verifier.load_pinned_ledger()
    assert contract_doc["af01_spec_sha256"] == entries["AF01.json"]
    assert contract_doc["af01_generator_sha256"] == entries["generator_AF01_SF1.py"]
    assert contract_doc["af01_manifest_sha256"] == entries["founder_manifest.json"]
    assert contract_doc["af01_canonical_c4_sha256"] == entries["AF01_all25_units_C4.wav"]
    assert (
        contract_doc["af01_payload_ledger_sha256"]
        == m.compute_file_sha256(verifier.PINNED_LEDGER_PATH)
    )


def test_af01_derived_pins_match_ledger(contract: m.Run10Contract) -> None:
    """AF01 台帳から導いた PINNED 欄が台帳の実値と一致する。"""
    entries = verifier.load_pinned_ledger()
    assert contract.pin("vg_reference_manifest_sha").value == entries["founder_manifest.json"]
    assert contract.pin("vg_body_artifact_sha").value == entries["AF01_all25_units_C4.wav"]
    assert (
        contract.pin("e0_external_calibration_source_sha").value
        == entries["generator_AF01_SF1.py"]
    )
    assert (
        contract.pin("e0_af01_sf1_truth_sha").value
        == entries["E0_calibration/E0_calibration_truth.json"]
    )


def test_af01_substitution_fails_closed(contract_doc: Dict[str, Any]) -> None:
    """§7.3: RUN10 内で AF01 を差し替えられない。"""
    bad = copy.deepcopy(contract_doc)
    bad["af01_canonical_c4_sha256"] = "0" * 64
    with pytest.raises(m.Run10ContractError, match="AF01 v1.0 凍結値"):
        m.parse_run10_contract(bad)


# --- §28-75..83: 分類 enum の規律 -------------------------------------------


def test_canonical_compatibility_enum_is_single_source_of_truth() -> None:
    """§28-75 / §15: 正規 enum は §15 の 9 値のみ。"""
    assert m.COMPATIBILITY_STATUS == (
        "DIRECT_COMPATIBLE",
        "ANALOGOUS_COMPATIBLE",
        "PARTIAL_COMPATIBLE",
        "VG_ONLY_OBSERVED",
        "AQUEST_ONLY_CANDIDATE",
        "NO_STABLE_MAPPING",
        "MEASUREMENT_CONFOUNDED",
        "NOT_EVALUABLE",
        "UNCALIBRATED",
    )
    with pytest.raises(m.Run10ContractError, match="未知の値"):
        m.assert_compatibility_entry("F1_F2_F3", {"status": "MOSTLY_COMPATIBLE"})


def test_family_alias_cannot_override_canonical_status() -> None:
    """§28-76 / §28-77 / §28-78 / §15.10: alias は status 語彙を名乗れない。"""
    m.assert_compatibility_entry(
        "HL_alpha",
        {"status": "NO_STABLE_MAPPING", "family_alias": "HL_LIKE_ODD_EVEN_STRUCTURE"},
    )
    with pytest.raises(m.Run10ContractError, match="alias に使えない"):
        m.assert_compatibility_entry(
            "AR_alpha",
            {"status": "ANALOGOUS_COMPATIBLE", "family_alias": "DIRECT_COMPATIBLE"},
        )


def test_aquest_only_candidate_cannot_enter_phase_b() -> None:
    """§28-81 / §15.5 / §17: AQUEST_ONLY_CANDIDATE は Phase B へ送らない。"""
    m.assert_compatibility_entry(
        "AQUEST_X01", {"status": "AQUEST_ONLY_CANDIDATE", "phase_b_eligible": False}
    )
    with pytest.raises(m.Run10ContractError, match="phase_b_eligible=false"):
        m.assert_compatibility_entry(
            "AQUEST_X01", {"status": "AQUEST_ONLY_CANDIDATE", "phase_b_eligible": True}
        )


def test_trait_value_equivalence_is_separate_from_status() -> None:
    """§14.7: DIRECT_COMPATIBLE は trait 値の等価を意味しない（別 flag）。"""
    assert "NOT_EVALUATED" in m.TRAIT_VALUE_EQUIVALENCE
    with pytest.raises(m.Run10ContractError, match="trait_value_equivalence"):
        m.assert_compatibility_entry(
            "F1_F2_F3",
            {"status": "DIRECT_COMPATIBLE", "trait_value_equivalence": "PROBABLY_SAME"},
        )


def test_no_total_score_field_anywhere() -> None:
    """§28-83 / §14.5: TotalScore 系の圧縮スカラを再帰的に拒否する。"""
    m.assert_no_forbidden_score_field({"compatibility_matrix": {"F1": {"status": "x"}}})
    with pytest.raises(m.Run10ContractError, match="単一スコア欄は禁止"):
        m.assert_no_forbidden_score_field({"total_score": 87})
    with pytest.raises(m.Run10ContractError, match="単一スコア欄は禁止"):
        m.assert_no_forbidden_score_field({"a": [{"b": {"similarity_score": 92}}]})


def test_claim_strength_target_is_a_vector_not_a_scalar(contract_doc: Dict[str, Any]) -> None:
    """§28-83 系 / §5.3: claim ceiling を scalar へ圧縮しない。"""
    assert set(contract_doc["claim_strength_target"]) == set(m.CLAIM_STRENGTH_KEYS)
    assert contract_doc["claim_strength_target"]["performance_claim"] == "C0"
    assert contract_doc["claim_strength_target"]["trait_identity_equivalence_claim"] == "C0"
    bad = copy.deepcopy(contract_doc)
    bad["claim_strength_target"] = "C2"
    with pytest.raises(m.Run10ContractError, match="claim_strength_target"):
        m.parse_run10_contract(bad)


# --- §28-120 / §28-121: cost cap と自動進行禁止 -----------------------------


def test_cost_cap_fields_present(contract_doc: Dict[str, Any]) -> None:
    """§28-120 / §31: cost cap 4 欄が存在する。"""
    assert set(contract_doc["cost_cap"]) == set(m.COST_CAP_FIELDS)
    bad = copy.deepcopy(contract_doc)
    del bad["cost_cap"]["gpu_hours_max"]
    with pytest.raises(m.Run10ContractError, match="cost_cap"):
        m.parse_run10_contract(bad)


def test_no_automatic_next_run_field(contract_doc: Dict[str, Any]) -> None:
    """§28-121 / §29「次Runへ自動進行しない」: 次 Run を宣言する欄を持たない。"""
    forbidden = {"next_run", "next_run_id", "auto_advance", "successor_run"}
    assert not forbidden & set(contract_doc)
    assert not forbidden & set(m.ALLOWED_TOP_LEVEL_FIELDS)


# --- results 文書（§27） ----------------------------------------------------


def _minimal_results() -> Dict[str, Any]:
    return {
        "schema": m.SCHEMA_RESULTS,
        "run_id": m.RUN_ID,
        "experiment_id": m.EXPERIMENT_ID,
        "design_revision": m.DESIGN_REVISION,
        "scope": "PRIVATE_ONLY",
        "cohorts": dict(m.COHORTS),
        "performance_analysis": "OUT_OF_SCOPE",
        "identity_copy": "PROHIBITED",
        "protocol_verdict": "BLOCKED",
        "phase_b_entry": "NOT_REACHED",
        "scientific_outcome": ["MEASUREMENT_INSUFFICIENT"],
        "rights": {"private_only": True, "third_party_distribution": False},
    }


def test_results_document_minimal_schema_validates() -> None:
    """§27: 最小 schema が通る。"""
    m.validate_results_document(_minimal_results())


@pytest.mark.parametrize(
    "key, value, pattern",
    [
        ("scope", "PUBLIC", "PRIVATE_ONLY"),
        ("performance_analysis", "PRIMARY", "OUT_OF_SCOPE"),
        ("identity_copy", "ALLOWED", "PROHIBITED"),
        ("protocol_verdict", "MOSTLY_PASS", "protocol_verdict"),
        ("phase_b_entry", "MAYBE", "phase_b_entry"),
    ],
)
def test_results_document_rejects_out_of_vocabulary(key: str, value: Any, pattern: str) -> None:
    """§27: 語彙外の値を fail-closed で拒否する。"""
    doc = _minimal_results()
    doc[key] = value
    with pytest.raises(m.Run10ContractError, match=pattern):
        m.validate_results_document(doc)


def test_results_document_rejects_public_rights() -> None:
    """§2.2 / §27: rights が public を主張する結果文書を拒否する。"""
    doc = _minimal_results()
    doc["rights"] = {"private_only": False, "third_party_distribution": True}
    with pytest.raises(m.Run10ContractError, match="private_only"):
        m.validate_results_document(doc)


def test_gate_registry_matches_design() -> None:
    """§21: Hard Gate 集合が設計どおり（Core 15 + Entry 1 + Phase B 7）。"""
    assert len(m.PHASE_A_CORE_GATES) == 15
    assert m.PHASE_A_CORE_GATES[0] == ("R10-G0", "RUN_CONTRACT_COMPLETE")
    assert m.PHASE_A_CORE_GATES[-1] == ("R10-G14", "PHASE_A_REPLAY_AND_PRIVATE_PUBLICATION")
    assert m.PHASE_B_ENTRY_GATE == ("R10-G15", "PHASE_B_ENTRY")
    assert len(m.PHASE_B_GATES) == 7
    ids = [gid for gid, _ in m.PHASE_A_CORE_GATES + (m.PHASE_B_ENTRY_GATE,) + m.PHASE_B_GATES]
    assert ids == [f"R10-G{i}" for i in range(23)]


# --- PINNED 値の形式契約（PR #330 Codex 第 1 巡 P1） ------------------------


def test_pinned_values_require_field_specific_formats(contract_doc: Dict[str, Any]) -> None:
    """プレースホルダ文字列で R10-G0 を開けない（実在の成果物へ束縛する）。"""
    bad = copy.deepcopy(contract_doc)
    for name in m.ALL_PIN_FIELDS:
        bad[name] = {"value": f"pinned::{name}", "status": "PINNED"}
    with pytest.raises(m.Run10ContractError):
        m.parse_run10_contract(bad)


@pytest.mark.parametrize(
    "name, value, ok",
    [
        ("repository_commit_sha", "b" * 40, True),
        ("repository_commit_sha", "b" * 64, True),
        ("repository_commit_sha", "b" * 39, False),
        ("repository_commit_sha", "main", False),
        ("attempt_id", "RUN10-ATTEMPT-01", True),
        ("attempt_id", "attempt-1", False),
        ("minimum_generatable_traits", 2, True),
        ("minimum_generatable_traits", 0, False),
        ("minimum_generatable_traits", True, False),
        ("minimum_generatable_traits", "2", False),
        ("resampler_sha", "a" * 64, True),
        ("resampler_sha", "a" * 63, False),
        ("cost_cap.cpu_hours_max", 4, True),
        ("cost_cap.cpu_hours_max", 0, False),
        ("cost_cap.cpu_hours_max", -1, False),
        ("cost_cap.cpu_hours_max", True, False),
    ],
)
def test_pin_value_format_contract(name: str, value: Any, ok: bool) -> None:
    """欄ごとの正の形式を閉世界で検査する。"""
    if ok:
        m._validate_pin_value(name, value)
    else:
        with pytest.raises(m.Run10ContractError):
            m._validate_pin_value(name, value)


def test_every_pin_field_has_a_declared_value_format() -> None:
    """形式未定義の欄を残さない（閉世界契約の被覆）。"""
    for name in m.ALL_PIN_FIELDS + m.COST_CAP_PIN_FIELDS:
        assert m._pin_value_format(name)


# --- cost cap を R10-G0 の対象に含める（PR #330 Codex 第 1 巡 P2） ----------


def test_cost_cap_pins_block_gate_g0(contract_doc: Dict[str, Any]) -> None:
    """cost cap が PENDING のまま R10-G0 を PASS させない（§31 / §32-20）。"""
    filled = _fully_pinned(contract_doc)
    for name in m.COST_CAP_FIELDS:
        filled["cost_cap"][name] = {
            "value": None,
            "status": "PENDING",
            "reason": "User 未裁定",
        }
    parsed = m.parse_run10_contract(filled)
    assert parsed.gate_r10_g0() == "BLOCKED"
    assert set(parsed.missing("CORE")) == set(m.COST_CAP_PIN_FIELDS)


def test_cost_cap_pins_are_registered_as_pin_fields(contract: m.Run10Contract) -> None:
    """cost cap 4 欄が pin 欄として登録され、現状は PENDING である。"""
    for name in m.COST_CAP_PIN_FIELDS:
        assert contract.pin(name).status == "PENDING"


# --- results の evidence 要求（PR #330 Codex 第 1 巡 P1） -------------------


def _passing_results() -> Dict[str, Any]:
    doc = _minimal_results()
    doc["protocol_verdict"] = "PASS"
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED"]
    doc["hard_gates"] = {gate_id: "PASS" for gate_id, _ in m.PHASE_A_CORE_GATES}
    # §29 手順 35: Entry の裁定は ENTER / SKIP のいずれでも一度行われるため、
    # PASS の結果文書には R10-G15 の裁定結果が実在する。
    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "SKIP"
    doc["external_calibration"] = {
        "e0_parameter_recovery": {"01_f1_low": {"abs_error": 12.0}},
        "meter_calibration_status": {"F1_F2_F3": "CALIBRATED_EXTERNAL"},
        "measurement_overfit_signal": False,
    }
    doc["decision_rules"] = {
        "minimum_support": {"phoneme_contexts": 5},
        "calibration": {"noise_floor": 1.0, "minimum_detectable_effect": 2.0},
        "equivalence": {"method": "TOST", "margin_lower": -1.0, "margin_upper": 1.0},
        "context_persistence": {"direction_agreement_ratio": 0.8},
    }
    doc["compatibility_matrix"] = {
        "F1_F2_F3": {
            "status": "DIRECT_COMPATIBLE",
            "support": {"phoneme_contexts": 5},
            "calibration": "CALIBRATED_EXTERNAL",
            "holdout": "PASS",
        }
    }
    doc["difference_map"] = {"F1_F2_F3": {"effect": 0.1}}
    doc["path_effects"] = {
        "delta_a_path": {"F1_F2_F3": 0.2},
        "delta_v_path": {"F1_F2_F3": 0.1},
        "d_output": {"F1_F2_F3": 0.3},
    }
    doc["replay"] = {
        "same_process": "PASS",
        "cross_process": "PASS",
        "feature_json_sha": "c" * 64,
    }
    return doc


def test_established_outcome_requires_evidence() -> None:
    """構造だけの空文書で成功側 outcome を記録できない（偽成功経路の閉塞）。"""
    doc = _minimal_results()
    doc["protocol_verdict"] = "PASS"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED"]
    with pytest.raises(m.Run10ContractError, match="hard_gates"):
        m.validate_results_document(doc)


def test_established_outcome_with_full_evidence_validates() -> None:
    """evidence が揃えば通る（常時拒否する張りぼてでない）。"""
    m.validate_results_document(_passing_results())


@pytest.mark.parametrize(
    "section",
    [
        "external_calibration",
        "decision_rules",
        "compatibility_matrix",
        "difference_map",
        "path_effects",
        "replay",
    ],
)
def test_each_required_evidence_section_is_enforced(section: str) -> None:
    """要求 evidence 節を 1 つでも空にすれば拒否される。"""
    doc = _passing_results()
    doc[section] = {}
    with pytest.raises(m.Run10ContractError, match=section):
        m.validate_results_document(doc)


def test_pass_requires_all_phase_a_gates_passing() -> None:
    """§21 / §22.1: R10-G0..G14 の 1 つでも PASS でなければ PASS を名乗れない。"""
    doc = _passing_results()
    doc["hard_gates"]["R10-G7"] = "FAIL"
    with pytest.raises(m.Run10ContractError, match="PASS でない Gate"):
        m.validate_results_document(doc)
    doc = _passing_results()
    del doc["hard_gates"]["R10-G12"]
    with pytest.raises(m.Run10ContractError, match="必要な Gate が無い"):
        m.validate_results_document(doc)


def test_blocked_verdict_cannot_claim_established_outcome() -> None:
    """§22: BLOCKED / FAILED で成功側 outcome を名乗らせない。"""
    doc = _passing_results()
    doc["protocol_verdict"] = "BLOCKED"
    with pytest.raises(m.Run10ContractError, match="成功側 outcome"):
        m.validate_results_document(doc)


def test_generative_outcome_requires_phase_b_entry() -> None:
    """§16: GENERATIVE_COMPATIBILITY_ESTABLISHED は phase_b_entry=ENTER が前提。"""
    doc = _passing_results()
    doc["scientific_outcome"] = ["GENERATIVE_COMPATIBILITY_ESTABLISHED"]
    doc["generative_compatibility_matrix"] = {"F1_F2_F3": {"synthesis_status": "GENERATIVELY_COMPATIBLE"}}
    doc["synthesis_validation"] = {
        "controls": {"G_null": {}, "G_target": {}, "G_inverse": {}},
        "construction_meter": {"m": "PASS"},
        "confirmation_meter": {"m2": "PASS"},
    }
    with pytest.raises(m.Run10ContractError, match="phase_b_entry=ENTER"):
        m.validate_results_document(doc)


def test_synthesis_validation_requires_three_controls_and_confirmation_meter() -> None:
    """§7.5 / §21 R10-G20・G21 / §32-26: 3 対照と独立 confirmation meter を要求する。"""
    base = _passing_results()
    base["synthesis_validation"] = {
        "controls": {"G_null": {"n": 1}, "G_target": {"n": 1}},
        "construction_meter": {"m": "PASS"},
        "confirmation_meter": {"m2": "PASS"},
    }
    with pytest.raises(m.Run10ContractError, match="G_inverse"):
        m.validate_results_document(base)

    base = _passing_results()
    base["synthesis_validation"] = {
        "controls": {"G_null": {"n": 1}, "G_target": {"n": 1}, "G_inverse": {"n": 1}},
        "construction_meter": {"m": "PASS"},
    }
    with pytest.raises(m.Run10ContractError, match="confirmation_meter"):
        m.validate_results_document(base)


def test_compatibility_matrix_entries_are_validated_inside_results() -> None:
    """results 経由でも §15 の enum 規律が効く。"""
    doc = _passing_results()
    doc["compatibility_matrix"] = {"X": {"status": "MOSTLY_COMPATIBLE"}}
    with pytest.raises(m.Run10ContractError, match="未知の値"):
        m.validate_results_document(doc)


# --- 第 2 巡: evidence の型契約 / R10-G15 / 非有限 cost cap ----------------


@pytest.mark.parametrize("placeholder", ["placeholder", 1, 1.5, True, ["x"], ("x",)])
def test_evidence_sections_reject_non_mapping_placeholders(placeholder: Any) -> None:
    """スカラや list を evidence として受理しない（PR #330 Codex 第 2 巡 P1）。

    「空でなければよい」だと `compatibility_matrix: placeholder` が通り、
    mapping でないためエントリ検証も飛んでいた。
    """
    doc = _passing_results()
    doc["compatibility_matrix"] = placeholder
    with pytest.raises(m.Run10ContractError, match="compatibility_matrix"):
        m.validate_results_document(doc)


def test_phase_b_entry_requires_g15_in_the_gate_ledger() -> None:
    """§21 R10-G15: Phase B を authorize する Gate を要求集合から落とさない。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "ENTER"
    doc["scientific_outcome"] = ["GENERATIVE_COMPATIBILITY_ESTABLISHED"]
    doc["generative_compatibility_matrix"] = {"F1_F2_F3": {"synthesis_status": "GENERATIVELY_COMPATIBLE"}}
    doc["synthesis_validation"] = {
        "controls": {"G_null": {"n": 1}, "G_target": {"n": 1}, "G_inverse": {"n": 1}},
        "construction_meter": {"m": "PASS"},
        "confirmation_meter": {"m2": "PASS"},
    }
    doc["hard_gates"] = {gate_id: "PASS" for gate_id, _ in m.PHASE_A_CORE_GATES}
    doc["hard_gates"].update({gate_id: "PASS" for gate_id, _ in m.PHASE_B_GATES})
    # R10-G15 だけ欠落させる。
    with pytest.raises(m.Run10ContractError, match="R10-G15"):
        m.validate_results_document(doc)

    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "PASS"
    m.validate_results_document(doc)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_cost_cap_rejects_non_finite_values(value: float) -> None:
    """§31 / §32-20: `.inf` / `.nan` で「有限の上限なし」の G0 開放を作らない。

    inf は `<= 0` を通り抜け、NaN はあらゆる順序比較が False になるため、
    素朴な正値判定を素通りしていた（PR #330 Codex 第 2 巡 P2）。
    """
    with pytest.raises(m.Run10ContractError, match="有限かつ正の数"):
        m._validate_pin_value("cost_cap.cpu_hours_max", value)


def test_cost_cap_non_finite_blocks_gate_g0(contract_doc: Dict[str, Any]) -> None:
    """contract 経由でも非有限 cap を拒否する（YAML の `.inf` 表記を含む）。"""
    filled = _fully_pinned(contract_doc)
    filled["cost_cap"]["cpu_hours_max"] = {"value": float("inf"), "status": "PINNED"}
    with pytest.raises(m.Run10ContractError, match="有限かつ正の数"):
        m.parse_run10_contract(filled)


# --- 第 3 巡: evidence 形状契約 / Phase B outcome 不変条件 -----------------


@pytest.mark.parametrize(
    "section, key",
    [
        ("external_calibration", "e0_parameter_recovery"),
        ("external_calibration", "meter_calibration_status"),
        ("decision_rules", "minimum_support"),
        ("decision_rules", "equivalence"),
        ("path_effects", "delta_a_path"),
        ("path_effects", "d_output"),
        ("replay", "same_process"),
        ("replay", "feature_json_sha"),
    ],
)
def test_evidence_sections_require_design_specified_fields(section: str, key: str) -> None:
    """非空 mapping であるだけでは成立しない（PR #330 Codex 第 3 巡 P1）。

    `{"placeholder": true}` のような中身のない mapping で成功側 outcome を
    名乗れないよう、設計が節ごとに明示する固定欄を要求する。
    """
    doc = _passing_results()
    del doc[section][key]
    with pytest.raises(m.Run10ContractError, match=key):
        m.validate_results_document(doc)


def test_placeholder_mapping_is_rejected_for_every_shaped_section() -> None:
    """全 shaped 節が placeholder mapping を拒否する（ファミリー全数掃討）。"""
    for section, required in m._EVIDENCE_SECTION_SHAPE.items():
        if not required:
            continue
        doc = _passing_results()
        doc[section] = {"placeholder": True}
        with pytest.raises(m.Run10ContractError, match=section):
            m.validate_results_document(doc)


def test_compatibility_matrix_entry_needs_support_and_holdout() -> None:
    """§15.1 / §20.1: status だけの行で比較地図成立を主張させない。"""
    doc = _passing_results()
    doc["compatibility_matrix"] = {"F1_F2_F3": {"status": "DIRECT_COMPATIBLE"}}
    with pytest.raises(m.Run10ContractError, match="support"):
        m.validate_results_document(doc)


@pytest.mark.parametrize("entry_state", ["SKIP", "BLOCKED", "NOT_REACHED"])
def test_all_phase_b_derived_outcomes_require_enter(entry_state: str) -> None:
    """§16: synthesis 由来の結論は Phase B が走った場合にしか出ない。

    GENERATIVE_COMPATIBILITY_ESTABLISHED だけを縛ると
    MEASUREMENT_ONLY_COMPATIBILITY が SKIP のまま通っていた
    （PR #330 Codex 第 3 巡 P1）。
    """
    for outcome in m.PHASE_B_DERIVED_OUTCOMES:
        doc = _passing_results()
        doc["phase_b_entry"] = entry_state
        doc["scientific_outcome"] = [outcome]
        doc["generative_compatibility_matrix"] = {"F1_F2_F3": {"synthesis_status": "GENERATIVELY_COMPATIBLE"}}
        doc["synthesis_validation"] = {
            "controls": {"G_null": {"n": 1}, "G_target": {"n": 1}, "G_inverse": {"n": 1}},
            "construction_meter": {"m": "PASS"},
            "confirmation_meter": {"m2": "PASS"},
        }
        with pytest.raises(m.Run10ContractError, match="phase_b_entry=ENTER"):
            m.validate_results_document(doc)


# --- 第 4 巡: §16 enum / overfit evidence / claim 語彙 ---------------------


def test_generative_matrix_entries_are_enum_validated() -> None:
    """§16 / §20.3: GENERATIVE_STATUS が宣言だけで未適用だった穴を塞ぐ。"""
    m.assert_generative_entry("F1", {"synthesis_status": "GENERATIVELY_PARTIAL"})
    with pytest.raises(m.Run10ContractError, match="未知の値"):
        m.assert_generative_entry("F1", {"synthesis_status": "NOT_A_REAL_STATUS"})


def test_generative_matrix_is_validated_inside_results() -> None:
    """results 経由でも §16 の enum 規律が効く（PR #330 Codex 第 4 巡 P1）。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "ENTER"
    doc["scientific_outcome"] = ["GENERATIVE_COMPATIBILITY_ESTABLISHED"]
    doc["hard_gates"].update({gate_id: "PASS" for gate_id, _ in m.PHASE_B_GATES})
    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "PASS"
    doc["synthesis_validation"] = {
        "controls": {"G_null": {"n": 1}, "G_target": {"n": 1}, "G_inverse": {"n": 1}},
        "construction_meter": {"m": "PASS"},
        "confirmation_meter": {"m2": "PASS"},
    }
    doc["generative_compatibility_matrix"] = {"F1": {"synthesis_status": "NOT_A_REAL_STATUS"}}
    with pytest.raises(m.Run10ContractError, match="未知の値"):
        m.validate_results_document(doc)


def test_aquest_only_candidate_cannot_be_synthesis_eligible_in_generative_matrix() -> None:
    """§15.5 / §21 R10-G18: schema 拡張候補を生成対象へ格上げさせない。"""
    with pytest.raises(m.Run10ContractError, match="NOT_SYNTHESIS_ELIGIBLE"):
        m.assert_generative_entry(
            "AQUEST_X01",
            {
                "measurement_status": "AQUEST_ONLY_CANDIDATE",
                "synthesis_status": "GENERATIVELY_COMPATIBLE",
            },
        )


def test_measurement_overfit_detected_requires_evidence() -> None:
    """§12.6 / §22.2 Outcome C: overfit 検出はデータ無しで名乗れない。"""
    doc = _minimal_results()
    doc["scientific_outcome"] = ["MEASUREMENT_OVERFIT_DETECTED"]
    with pytest.raises(m.Run10ContractError, match="external_calibration"):
        m.validate_results_document(doc)


def test_measurement_overfit_detected_is_allowed_under_blocked_verdict() -> None:
    """overfit は成立主張ではなく有効な否定的診断であり BLOCKED でも成り立つ。

    成功側 outcome の集合（`_ESTABLISHED_OUTCOMES`）と evidence 要求表
    （`_EVIDENCE_FOR_OUTCOME`）を分離した理由がこれである。
    """
    doc = _minimal_results()
    doc["scientific_outcome"] = ["MEASUREMENT_OVERFIT_DETECTED"]
    doc["external_calibration"] = {
        "e0_parameter_recovery": {"01_f1_low": {"abs_error": 900.0}},
        "meter_calibration_status": {"F1_F2_F3": "UNCALIBRATED"},
        "measurement_overfit_signal": True,
    }
    doc["replay"] = {
        "same_process": "PASS",
        "cross_process": "PASS",
        "feature_json_sha": "d" * 64,
    }
    m.validate_results_document(doc)


def test_established_outcomes_are_separate_from_the_evidence_table() -> None:
    """overfit は evidence を要求されるが成立側 outcome ではない。"""
    assert "MEASUREMENT_OVERFIT_DETECTED" in m._EVIDENCE_FOR_OUTCOME
    assert "MEASUREMENT_OVERFIT_DETECTED" not in m._ESTABLISHED_OUTCOMES


@pytest.mark.parametrize(
    "key, bad",
    [
        ("performance_claim", "C2"),
        ("trait_identity_equivalence_claim", "C999"),
        ("measurement_compatibility_claim", "C3"),
        ("transfer_or_reconstruction_claim", "C1"),
    ],
)
def test_claim_strength_vocabulary_is_frozen(
    contract_doc: Dict[str, Any], key: str, bad: str
) -> None:
    """§5.3: 任意の非空文字列を許すと主張の天井が意味を失う。"""
    doc = copy.deepcopy(contract_doc)
    doc["claim_strength_target"][key] = bad
    with pytest.raises(m.Run10ContractError, match=key):
        m.parse_run10_contract(doc)


def test_claim_strength_ceilings_match_design() -> None:
    """§5.3 の C0 軸（Performance / Identity 等価 / 移植）が凍結されている。"""
    assert m.CLAIM_STRENGTH_TARGET["performance_claim"] == "C0"
    assert m.CLAIM_STRENGTH_TARGET["trait_identity_equivalence_claim"] == "C0"
    assert m.CLAIM_STRENGTH_TARGET["transfer_or_reconstruction_claim"] == "C0"


# --- 第 5 巡: outcome と Run 状態量の整合（ファミリー終端） ----------------


def test_phase_b_not_entered_conflicts_with_enter() -> None:
    """§22.2: Phase B へ入ったのに「入らなかった」と記録させない（逆方向）。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "ENTER"
    doc["scientific_outcome"] = ["PHASE_B_NOT_ENTERED"]
    doc["hard_gates"].update({gate_id: "PASS" for gate_id, _ in m.PHASE_B_GATES})
    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "PASS"
    with pytest.raises(m.Run10ContractError, match="PHASE_B_NOT_ENTERED"):
        m.validate_results_document(doc)


def test_phase_b_not_entered_is_fine_when_skipped() -> None:
    """SKIP なら PHASE_B_NOT_ENTERED は正当（§0「SKIPは失敗を意味しない」）。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED", "PHASE_B_NOT_ENTERED"]
    m.validate_results_document(doc)


def test_overfit_outcome_requires_the_signal_to_be_true() -> None:
    """§12.6: evidence が結論を否定したまま記録させない。

    存在判定に直した副作用で `measurement_overfit_signal: false` が通っていた
    （PR #330 Codex 第 5 巡 P1）。
    """
    doc = _minimal_results()
    doc["scientific_outcome"] = ["MEASUREMENT_OVERFIT_DETECTED"]
    doc["external_calibration"] = {
        "e0_parameter_recovery": {"01_f1_low": {"abs_error": 900.0}},
        "meter_calibration_status": {"F1_F2_F3": "UNCALIBRATED"},
        "measurement_overfit_signal": False,
    }
    doc["replay"] = {
        "same_process": "PASS",
        "cross_process": "PASS",
        "feature_json_sha": "d" * 64,
    }
    with pytest.raises(m.Run10ContractError, match="measurement_overfit_signal = true"):
        m.validate_results_document(doc)


def test_overfit_signal_blocks_established_outcomes() -> None:
    """§12.6 / §21 R10-G7: 外的妥当性が立たないまま成立側の結論を名乗らせない。

    第 5 巡の指摘は overfit 側 1 方向だけだったが、同型の逆方向をここで併せて
    掃討する（片方だけ塞ぐと矛盾記録の穴が残る）。
    """
    for outcome in m._ESTABLISHED_OUTCOMES:
        doc = _passing_results()
        doc["scientific_outcome"] = [outcome]
        doc["external_calibration"]["measurement_overfit_signal"] = True
        if outcome in m.PHASE_B_DERIVED_OUTCOMES:
            doc["phase_b_entry"] = "ENTER"
            doc["hard_gates"].update({g: "PASS" for g, _ in m.PHASE_B_GATES})
            doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "PASS"
            doc["generative_compatibility_matrix"] = {
                "F1_F2_F3": {"synthesis_status": "GENERATIVELY_COMPATIBLE"}
            }
            doc["synthesis_validation"] = {
                "controls": {"G_null": {"n": 1}, "G_target": {"n": 1}, "G_inverse": {"n": 1}},
                "construction_meter": {"m": "PASS"},
                "confirmation_meter": {"m2": "PASS"},
            }
        for section in ("novel_trait_candidates",):
            doc.setdefault(section, {"AQUEST_X01": {"state": "confirmed"}})
        with pytest.raises(m.Run10ContractError, match="measurement_overfit_signal=true"):
            m.validate_results_document(doc)


def test_outcome_consistency_rules_are_enumerated() -> None:
    """整合規則が outcome × Run 状態量の 4 方向をすべて持つ（掃討の被覆）。"""
    source = inspect.getsource(m._validate_outcome_consistency)
    for rule in ("規則 1", "規則 2", "規則 3", "規則 4"):
        assert rule in source


# --- 第 6 巡: outcome 同士の矛盾（PR #330 Codex 第 6 巡 P1） --------------


def test_contradictory_outcomes_are_rejected() -> None:
    """§22.2: 地図成立と「安定した対応が無い」を並べた正典結果を拒否する。"""
    doc = _passing_results()
    doc["scientific_outcome"] = [
        "COMPATIBILITY_MAP_ESTABLISHED",
        "NO_STABLE_CROSS_SYSTEM_MAPPING",
    ]
    with pytest.raises(m.Run10ContractError, match="同時に成立しない"):
        m.validate_results_document(doc)


@pytest.mark.parametrize("left, right", m._MUTUALLY_EXCLUSIVE_OUTCOMES)
def test_every_declared_exclusive_pair_is_enforced(left: str, right: str) -> None:
    """宣言した相互排他対がすべて実際に拒否される（掃討の被覆）。"""
    doc = _passing_results()
    doc["scientific_outcome"] = [left, right]
    with pytest.raises(m.Run10ContractError, match="同時に成立しない"):
        m.validate_results_document(doc)


def test_duplicate_outcomes_are_rejected() -> None:
    """同じ結論の重複記載を正典結果に残さない。"""
    doc = _passing_results()
    doc["scientific_outcome"] = [
        "COMPATIBILITY_MAP_ESTABLISHED",
        "COMPATIBILITY_MAP_ESTABLISHED",
    ]
    with pytest.raises(m.Run10ContractError, match="重複"):
        m.validate_results_document(doc)


def test_compatible_outcome_combinations_still_pass() -> None:
    """矛盾しない組み合わせまで塞いでいないこと（偽陽性の確認）。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED", "PHASE_B_NOT_ENTERED"]
    m.validate_results_document(doc)

    doc = _passing_results()
    doc["scientific_outcome"] = ["PARTIAL_COMPATIBILITY_MAP", "SCHEMA_GAP_IDENTIFIED"]
    doc["novel_trait_candidates"] = {"AQUEST_X01": {"state": "confirmed"}}
    m.validate_results_document(doc)


# --- 第 7 巡: overfit 信号と R10-G7 / verdict の整合 -----------------------


def test_overfit_signal_forbids_passing_g7() -> None:
    """§21 R10-G7 / §12.6: E0 校正が失敗した meter は外的妥当性を確立しない。

    提出された Gate 台帳が G0..G14 を PASS と主張するだけで
    `protocol_verdict: PASS` の overfit 結果が成立していた
    （PR #330 Codex 第 7 巡 P1）。
    """
    doc = _minimal_results()
    doc["protocol_verdict"] = "PASS"
    doc["scientific_outcome"] = ["MEASUREMENT_OVERFIT_DETECTED"]
    doc["hard_gates"] = {gate_id: "PASS" for gate_id, _ in m.PHASE_A_CORE_GATES}
    doc["external_calibration"] = {
        "e0_parameter_recovery": {"01_f1_low": {"abs_error": 900.0}},
        "meter_calibration_status": {"F1_F2_F3": "UNCALIBRATED"},
        "measurement_overfit_signal": True,
    }
    doc["replay"] = {
        "same_process": "PASS",
        "cross_process": "PASS",
        "feature_json_sha": "d" * 64,
    }
    with pytest.raises(m.Run10ContractError, match="R10-G7"):
        m.validate_results_document(doc)


def test_overfit_with_failing_g7_is_recorded_as_blocked() -> None:
    """G7 が PASS でなければ overfit の記録自体は成立する（偽陽性の確認）。"""
    doc = _minimal_results()
    doc["protocol_verdict"] = "BLOCKED"
    doc["scientific_outcome"] = ["MEASUREMENT_OVERFIT_DETECTED"]
    doc["hard_gates"] = {gate_id: "PASS" for gate_id, _ in m.PHASE_A_CORE_GATES}
    doc["hard_gates"]["R10-G7"] = "FAIL"
    doc["external_calibration"] = {
        "e0_parameter_recovery": {"01_f1_low": {"abs_error": 900.0}},
        "meter_calibration_status": {"F1_F2_F3": "UNCALIBRATED"},
        "measurement_overfit_signal": True,
    }
    doc["replay"] = {
        "same_process": "PASS",
        "cross_process": "PASS",
        "feature_json_sha": "d" * 64,
    }
    m.validate_results_document(doc)


# --- 第 9 巡: 設計文書 hash の契約束縛 / G15 の実在 -----------------------


def test_contract_binds_the_design_document_hash(contract_doc: Dict[str, Any]) -> None:
    """§2.2: 設計文書は repo に置かないため、sha256 が唯一の来歴束縛である。"""
    assert contract_doc["design_doc_sha256"] == m.DESIGN_DOC_SHA256
    bad = copy.deepcopy(contract_doc)
    bad["design_doc_sha256"] = "0" * 64
    with pytest.raises(m.Run10ContractError, match="design_doc_sha256"):
        m.parse_run10_contract(bad)


def test_missing_design_doc_hash_fails_closed(contract_doc: Dict[str, Any]) -> None:
    """題名だけで同題名の差し替えと区別できる、という誤りを塞ぐ。"""
    bad = copy.deepcopy(contract_doc)
    del bad["design_doc_sha256"]
    with pytest.raises(m.Run10ContractError, match="構造欄が無い"):
        m.parse_run10_contract(bad)


def test_verify_design_document_checks_real_bytes(tmp_path: Path) -> None:
    """手元の設計文書が凍結 sha と一致するかを実バイトで確かめられる。"""
    target = tmp_path / m.DESIGN_DOC_TITLE
    target.write_bytes(b"not the design document")
    assert m.verify_design_document(target) is False
    assert m.verify_design_document(tmp_path / "absent.md") is False


def test_pass_requires_the_phase_b_entry_adjudication_to_exist() -> None:
    """§29 手順 35: Entry の裁定は ENTER でも SKIP でも一度行われる。

    G15 を ENTER のときだけ要求していたため、裁定を経ていない SKIP を
    正典化できた（PR #330 Codex 第 9 巡 P1）。
    """
    doc = _passing_results()
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED", "PHASE_B_NOT_ENTERED"]
    doc["hard_gates"] = {gate_id: "PASS" for gate_id, _ in m.PHASE_A_CORE_GATES}
    with pytest.raises(m.Run10ContractError, match="R10-G15"):
        m.validate_results_document(doc)


def test_skip_records_g15_without_requiring_it_to_pass() -> None:
    """§21 R10-G15 / §22.1: 条件不成立の SKIP でも Protocol PASS は成立する。

    G15 の**値**まで PASS を要求すると SKIP が原理的に記録できなくなる。
    要求するのは裁定結果の実在であって PASS ではない。
    """
    doc = _passing_results()
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED", "PHASE_B_NOT_ENTERED"]
    doc["hard_gates"] = {gate_id: "PASS" for gate_id, _ in m.PHASE_A_CORE_GATES}
    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "SKIP"
    m.validate_results_document(doc)


# --- 第 10 巡: evidence 値の整合 / G15 と entry の一対一束縛 -------------


@pytest.mark.parametrize("field", ["same_process", "cross_process"])
def test_failed_replay_cannot_support_an_established_outcome(field: str) -> None:
    """§21 R10-G14: 再現できていない replay を添えて成立を主張させない。

    欄の実在だけを見ていたため `same_process: FAIL` が通っていた
    （PR #330 Codex 第 10 巡 P1）。
    """
    doc = _passing_results()
    doc["replay"][field] = "FAIL"
    with pytest.raises(m.Run10ContractError, match=field):
        m.validate_results_document(doc)


def test_replay_required_values_are_enumerated() -> None:
    """値まで固定する evidence 欄が閉世界表に列挙されている。"""
    assert m._EVIDENCE_FIELD_REQUIRED_VALUES[("replay", "same_process")] == ("PASS",)
    assert m._EVIDENCE_FIELD_REQUIRED_VALUES[("replay", "cross_process")] == ("PASS",)


@pytest.mark.parametrize("bogus", ["FABRICATED", None, "ENTER", "FAIL"])
def test_g15_verdict_must_match_the_entry_state(bogus: Any) -> None:
    """§20.4 / §21 R10-G15: 裁定を表していない値で SKIP を正典化させない。

    第 9 巡では実在だけを要求したため、`R10-G15: FABRICATED` や null が通った
    （PR #330 Codex 第 10 巡 P1）。
    """
    doc = _passing_results()
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED", "PHASE_B_NOT_ENTERED"]
    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = bogus
    with pytest.raises(m.Run10ContractError, match="R10-G15"):
        m.validate_results_document(doc)


@pytest.mark.parametrize(
    "entry, verdict", [("ENTER", "PASS"), ("SKIP", "SKIP"), ("BLOCKED", "BLOCKED")]
)
def test_every_entry_state_has_a_matching_g15_verdict(entry: str, verdict: str) -> None:
    """Entry 状態と G15 の値が一対一で対応している（掃討の被覆）。"""
    assert m._G15_VERDICT_FOR_ENTRY[entry] == verdict


def test_pass_cannot_leave_the_entry_adjudication_unreached() -> None:
    """§29 手順 35: Phase A PASS なら Entry 裁定は必ず一度行われている。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "NOT_REACHED"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED"]
    with pytest.raises(m.Run10ContractError, match="phase_b_entry"):
        m.validate_results_document(doc)


def test_enter_requires_g15_pass() -> None:
    """ENTER のときは G15 の値まで PASS を要求する。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "ENTER"
    doc["scientific_outcome"] = ["GENERATIVE_COMPATIBILITY_ESTABLISHED"]
    doc["hard_gates"].update({gate_id: "PASS" for gate_id, _ in m.PHASE_B_GATES})
    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "SKIP"
    doc["generative_compatibility_matrix"] = {
        "F1_F2_F3": {"synthesis_status": "GENERATIVELY_COMPATIBLE"}
    }
    doc["synthesis_validation"] = {
        "controls": {"G_null": {"n": 1}, "G_target": {"n": 1}, "G_inverse": {"n": 1}},
        "construction_meter": {"m": "PASS"},
        "confirmation_meter": {"m2": "PASS"},
    }
    with pytest.raises(m.Run10ContractError, match="R10-G15"):
        m.validate_results_document(doc)

    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "PASS"
    m.validate_results_document(doc)


# --- 第 11 巡: PASS と BLOCKED な Entry 裁定は両立しない ------------------


def test_pass_cannot_stand_on_a_blocked_entry_adjudication() -> None:
    """§22.1: PASS と両立を認められているのは SKIP まで。

    第 10 巡で導入した ENTER/SKIP/BLOCKED 写像が、裁定未解決（BLOCKED）のまま
    Protocol PASS を記録する経路を新たに到達可能にしていた
    （PR #330 Codex 第 11 巡 P1）。
    """
    doc = _passing_results()
    doc["phase_b_entry"] = "BLOCKED"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED"]
    doc["hard_gates"][m.PHASE_B_ENTRY_GATE[0]] = "BLOCKED"
    with pytest.raises(m.Run10ContractError, match="phase_b_entry"):
        m.validate_results_document(doc)


def test_entry_states_compatible_with_pass_are_enumerated() -> None:
    """PASS と両立する Entry 状態が閉世界で列挙されている。"""
    assert m._ENTRY_STATES_COMPATIBLE_WITH_PASS == ("ENTER", "SKIP")
    assert "BLOCKED" not in m._ENTRY_STATES_COMPATIBLE_WITH_PASS
    assert "NOT_REACHED" not in m._ENTRY_STATES_COMPATIBLE_WITH_PASS


def test_blocked_entry_is_recorded_under_a_blocked_verdict() -> None:
    """裁定が BLOCKED なら protocol_verdict も PASS 以外になる（記録自体は可能）。"""
    doc = _minimal_results()
    doc["protocol_verdict"] = "BLOCKED"
    doc["phase_b_entry"] = "BLOCKED"
    doc["scientific_outcome"] = ["MEASUREMENT_INSUFFICIENT"]
    m.validate_results_document(doc)


# --- 第 12 巡: rights の閉世界形状 / overfit 信号の型 --------------------


def test_rights_rejects_contradictory_publication_modes() -> None:
    """§2.2: private_only を宣言しながら禁止された公開モードを許可させない。"""
    doc = _passing_results()
    doc["rights"] = {
        "private_only": True,
        "third_party_distribution": False,
        "public_audio_release": True,
    }
    with pytest.raises(m.Run10ContractError, match="public_audio_release"):
        m.validate_results_document(doc)


def test_rights_rejects_unknown_keys() -> None:
    """rights 節は閉世界形状（未知欄で境界を骨抜きにさせない）。"""
    doc = _passing_results()
    doc["rights"] = {
        "private_only": True,
        "third_party_distribution": False,
        "public_dataset_release": True,
    }
    with pytest.raises(m.Run10ContractError, match="未知の欄"):
        m.validate_results_document(doc)


def test_rights_accepts_explicit_prohibited_declarations() -> None:
    """禁止側を明示的に false と宣言するのは正当（偽陽性の確認）。"""
    doc = _passing_results()
    doc["rights"] = {
        "private_only": True,
        "third_party_distribution": False,
        "public_audio_release": False,
        "public_model_release": False,
        "public_synthesis_system_release": False,
        "external_listener_panel": False,
    }
    m.validate_results_document(doc)


@pytest.mark.parametrize("bogus", [1, 0, "true", "TRUE", []])
def test_overfit_signal_must_be_boolean(bogus: Any) -> None:
    """非 bool の truthy 値が全整合規則を素通りする経路を塞ぐ。

    規則 3/4/5 はすべて `signal is True` で判定するため、`1` はどの規則にも
    掛からず overfit 信号を立てたまま成立側 outcome と R10-G7 PASS を記録できた
    （PR #330 Codex 第 12 巡 P1）。
    """
    doc = _passing_results()
    doc["external_calibration"]["measurement_overfit_signal"] = bogus
    with pytest.raises(m.Run10ContractError, match="真偽値"):
        m.validate_results_document(doc)


# --- 第 13 巡: 開いたキー集合の全数掃討 -----------------------------------


@pytest.mark.parametrize(
    "phase, extra, value",
    [
        ("phase_a", "activation", "INTERVENTIONAL"),
        ("phase_a", "identity_copy", "ALLOWED"),
        ("phase_b", "identity_copy", "ALLOWED"),
        ("phase_b", "performance_analysis", "PRIMARY"),
    ],
)
def test_staged_intervention_nested_shapes_are_closed(
    contract_doc: Dict[str, Any], phase: str, extra: str, value: str
) -> None:
    """§23: 入れ子へ機械可読欄を足して不変条件と矛盾させられない。

    外側のキー集合だけ閉じて内側を `get()` で拾っていたため、
    `phase_a.activation: INTERVENTIONAL` のような欄を足した契約が通り、
    R10-G0 が PASS になり得た（PR #330 Codex 第 13 巡 P1）。
    """
    bad = copy.deepcopy(contract_doc)
    bad["staged_intervention"][phase][extra] = value
    with pytest.raises(m.Run10ContractError, match=phase):
        m.parse_run10_contract(bad)


@pytest.mark.parametrize("phase, missing", [("phase_a", "description"), ("phase_b", "activation")])
def test_staged_intervention_nested_shapes_require_all_fields(
    contract_doc: Dict[str, Any], phase: str, missing: str
) -> None:
    """入れ子の欄欠落も拒否する（閉世界は両方向）。"""
    bad = copy.deepcopy(contract_doc)
    del bad["staged_intervention"][phase][missing]
    with pytest.raises(m.Run10ContractError, match=phase):
        m.parse_run10_contract(bad)


@pytest.mark.parametrize(
    "field, value",
    [
        ("publication_scope", "PUBLIC"),
        ("public_dataset_release", True),
        ("total_score_note", "x"),
        ("extra", 1),
    ],
)
def test_results_top_level_keys_are_closed(field: str, value: Any) -> None:
    """§27: 対立する機械可読宣言を必須の private 宣言と同居させられない。"""
    doc = _passing_results()
    doc[field] = value
    with pytest.raises(m.Run10ContractError):
        m.validate_results_document(doc)


def test_results_allowed_fields_cover_the_minimal_schema() -> None:
    """§27 最小 schema の欄がすべて allowlist に入っている（偽陽性の防止）。"""
    for field in _minimal_results():
        assert field in m.RESULTS_ALLOWED_FIELDS
    for field in _passing_results():
        assert field in m.RESULTS_ALLOWED_FIELDS


def test_no_validated_mapping_is_left_open() -> None:
    """契約・結果の検証で扱う mapping に開いたキー集合が残っていない。

    第 12 巡（rights）・第 13 巡（staged_intervention 入れ子 / results top-level）と
    同型が 2 巡続いたため、閉世界表の存在をテストで固定してファミリーを終端する。
    """
    assert set(m.STAGED_INTERVENTION_SHAPE) == {"phase_a", "phase_b"}
    assert m.RESULTS_ALLOWED_FIELDS
    assert m.RESULTS_RIGHTS_REQUIRED and m.RESULTS_RIGHTS_OPTIONAL
    assert m.ALLOWED_TOP_LEVEL_FIELDS
    assert m.COST_CAP_FIELDS
    assert m.CLAIM_STRENGTH_KEYS


# --- 第 14 巡: SKIP 時の Phase B gate / 真偽値欄の型 ----------------------


def test_skip_cannot_claim_passing_phase_b_gates() -> None:
    """§21: 入らなかった Run で走っていない Gate を合格にしない。

    Gate 台帳が「Phase B へ入らなかった」と「その実行 Gate は全部 PASS」を
    同時に主張できた（PR #330 Codex 第 14 巡 P1）。
    """
    doc = _passing_results()
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED", "PHASE_B_NOT_ENTERED"]
    doc["hard_gates"].update({gate_id: "PASS" for gate_id, _ in m.PHASE_B_GATES})
    with pytest.raises(m.Run10ContractError, match="R10-G16"):
        m.validate_results_document(doc)


def test_skip_may_record_phase_b_gates_as_not_reached() -> None:
    """未実行状態での記載は正当（偽陽性の確認）。"""
    doc = _passing_results()
    doc["phase_b_entry"] = "SKIP"
    doc["scientific_outcome"] = ["COMPATIBILITY_MAP_ESTABLISHED", "PHASE_B_NOT_ENTERED"]
    doc["hard_gates"].update({gate_id: "NOT_REACHED" for gate_id, _ in m.PHASE_B_GATES})
    m.validate_results_document(doc)


@pytest.mark.parametrize("bogus", [1, 0, "yes", "false", []])
def test_phase_b_eligible_must_be_boolean(bogus: Any) -> None:
    """§15.5: 非 bool の truthy/falsy 値がガードを素通りする経路を塞ぐ。

    第 12 巡の overfit 信号と同型。判定を `_require_boolean_field()` へ一本化した。
    """
    with pytest.raises(m.Run10ContractError, match="真偽値"):
        m.assert_compatibility_entry(
            "AQUEST_X01",
            {"status": "AQUEST_ONLY_CANDIDATE", "phase_b_eligible": bogus},
        )


def test_boolean_semantics_fields_share_one_validator() -> None:
    """真偽値欄の型検査が 1 実装であること（同型の再発防止）。"""
    m._require_boolean_field("x", None)
    m._require_boolean_field("x", True)
    m._require_boolean_field("x", False)
    with pytest.raises(m.Run10ContractError, match="真偽値"):
        m._require_boolean_field("x", 1)
