"""test_run10_contract.py — RUN10 Phase 0 スキャフォールドの最低テスト。

DESIGN_RUN10 §28「最低テスト」121 項目のうち、本 PR の範囲（契約機械化・
公開境界・AF01 凍結検証・Pre-Run Inventory）で静的に検証できるサブセットを
実装する。各テストの docstring に §28 の項目番号を対応づける。

音声処理・実測を伴わないため全て高速（slow マーカー不要）。
"""
from __future__ import annotations

import copy
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
    with pytest.raises(m.Run10ContractError, match="phase_b_eligible にできない"):
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
    doc["external_calibration"] = {"E0": "PASS"}
    doc["decision_rules"] = {"F1_F2_F3": {"noise_floor": 1.0}}
    doc["compatibility_matrix"] = {"F1_F2_F3": {"status": "DIRECT_COMPATIBLE"}}
    doc["difference_map"] = {"F1_F2_F3": {"effect": 0.1}}
    doc["path_effects"] = {"delta_a_path": {}, "delta_v_path": {}}
    doc["replay"] = {"same_process": "PASS", "cross_process": "PASS"}
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
    doc["generative_compatibility_matrix"] = {"F1_F2_F3": {"synthesis_status": "x"}}
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
