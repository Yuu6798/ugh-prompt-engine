"""test_debt_ledger_shape.py — `debt/debt_ledger.yaml` と
`debt/debt_adjudication_v1.1.yaml` の形状テスト（Phase D0）。

台帳・裁定記録はともにコード生成物ではなく人手で編集される記録ファイルで
あり、`voice_genesis/foundry/tests/test_genome_ledger_shape.py` の流儀
（人手編集ファイルは構造のみ機械強制し、値の内容は検証しない）を踏襲する。

- debt_ledger.yaml: トップレベルスキーマ・各エントリの必須キー・status/
  priority/class の語彙・debt_id の一意性・blocked_by の参照先実在
- debt_adjudication_v1.1.yaml: vocabulary.values が 4 値ちょうど・
  c0_c3_symbols == "prohibited" 等の裁定1の機械制約

値そのもの（sha256・日付等の記述内容）はここでは検証しない
（`DEBT_ADJUDICATION_v1.1.md` / `DEBT_REPAYMENT_PLAN_v1.0.md` が記述内容の正本）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

DEBT_DIR = Path(__file__).resolve().parent.parent / "debt"
LEDGER_PATH = DEBT_DIR / "debt_ledger.yaml"
ADJUDICATION_PATH = DEBT_DIR / "debt_adjudication_v1.1.yaml"

REQUIRED_TOP_LEVEL_KEYS = {"schema", "debts"}

# DEBT_REPAYMENT_PLAN_v1.0.md §7 のスキーマ（debt_id/scope/class/priority/
# problem.summary/impact.affected_claims/repayment.method/
# repayment.evidence_required/status/close_condition）。
REQUIRED_DEBT_KEYS = {
    "debt_id",
    "scope",
    "class",
    "priority",
    "problem",
    "impact",
    "repayment",
    "status",
    "close_condition",
}

# DEBT_REPAYMENT_PLAN_v1.0.md §7 逐語: open/in_progress/repaid/
# accepted_residual/unrecoverable のみ。
VALID_STATUS = {"open", "in_progress", "repaid", "accepted_residual", "unrecoverable"}
VALID_PRIORITY = {"P0", "P1", "P2"}
VALID_CLASS = {"technical", "research"}
VALID_CAUSAL_EVIDENCE_STRENGTH = {"descriptive", "suggestive", "moderate", "strong"}


@pytest.fixture(scope="module")
def ledger() -> Dict[str, Any]:
    assert LEDGER_PATH.exists(), f"debt_ledger.yaml not found: {LEDGER_PATH}"
    with LEDGER_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def adjudication() -> Dict[str, Any]:
    assert ADJUDICATION_PATH.exists(), f"debt_adjudication_v1.1.yaml not found: {ADJUDICATION_PATH}"
    with ADJUDICATION_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _debts(ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    debts = ledger["debts"]
    assert isinstance(debts, list)
    return debts


# --- debt_ledger.yaml ---------------------------------------------------


def test_ledger_is_valid_mapping(ledger: Dict[str, Any]) -> None:
    assert isinstance(ledger, dict)


def test_top_level_required_keys_present(ledger: Dict[str, Any]) -> None:
    missing = REQUIRED_TOP_LEVEL_KEYS - ledger.keys()
    assert not missing, f"missing top-level keys: {sorted(missing)}"


def test_schema_is_pinned_version(ledger: Dict[str, Any]) -> None:
    assert ledger["schema"] == "voicegenesis-debt-ledger/0.1"


def test_debts_is_nonempty_list(ledger: Dict[str, Any]) -> None:
    debts = _debts(ledger)
    assert len(debts) > 0


def test_every_debt_has_required_keys(ledger: Dict[str, Any]) -> None:
    for debt in _debts(ledger):
        assert isinstance(debt, dict)
        missing = REQUIRED_DEBT_KEYS - debt.keys()
        assert not missing, f"debt {debt.get('debt_id', '<unknown>')} missing keys: {sorted(missing)}"


def test_every_debt_has_minimal_field_types(ledger: Dict[str, Any]) -> None:
    for debt in _debts(ledger):
        debt_id = debt["debt_id"]
        assert isinstance(debt_id, str) and debt_id.startswith("VG-DEBT-"), debt_id
        assert isinstance(debt["scope"], str) and debt["scope"]
        assert isinstance(debt["class"], str)
        assert isinstance(debt["priority"], str)
        assert isinstance(debt["problem"], dict) and "summary" in debt["problem"]
        assert isinstance(debt["problem"]["summary"], str) and debt["problem"]["summary"]
        assert isinstance(debt["impact"], dict) and "affected_claims" in debt["impact"]
        assert isinstance(debt["impact"]["affected_claims"], list)
        assert isinstance(debt["repayment"], dict)
        assert "method" in debt["repayment"] and isinstance(debt["repayment"]["method"], str)
        assert "evidence_required" in debt["repayment"] and isinstance(
            debt["repayment"]["evidence_required"], list
        )
        assert isinstance(debt["status"], str)
        assert isinstance(debt["close_condition"], list)


def test_debt_id_is_unique(ledger: Dict[str, Any]) -> None:
    debt_ids = [d["debt_id"] for d in _debts(ledger)]
    assert len(debt_ids) == len(set(debt_ids)), f"duplicate debt_id(s): {debt_ids}"


def test_status_uses_only_valid_vocabulary(ledger: Dict[str, Any]) -> None:
    for debt in _debts(ledger):
        assert debt["status"] in VALID_STATUS, (
            f"debt {debt['debt_id']} has invalid status {debt['status']!r}; "
            f"must be one of {sorted(VALID_STATUS)}"
        )


def test_priority_uses_only_valid_vocabulary(ledger: Dict[str, Any]) -> None:
    for debt in _debts(ledger):
        assert debt["priority"] in VALID_PRIORITY, (
            f"debt {debt['debt_id']} has invalid priority {debt['priority']!r}; "
            f"must be one of {sorted(VALID_PRIORITY)}"
        )


def test_class_uses_only_valid_vocabulary(ledger: Dict[str, Any]) -> None:
    for debt in _debts(ledger):
        assert debt["class"] in VALID_CLASS, (
            f"debt {debt['debt_id']} has invalid class {debt['class']!r}; "
            f"must be one of {sorted(VALID_CLASS)}"
        )


def test_blocked_by_references_exist(ledger: Dict[str, Any]) -> None:
    debt_ids = {d["debt_id"] for d in _debts(ledger)}
    for debt in _debts(ledger):
        blocked_by = debt.get("blocked_by")
        if blocked_by is None:
            continue
        assert isinstance(blocked_by, list)
        for ref in blocked_by:
            assert ref in debt_ids, (
                f"debt {debt['debt_id']} blocked_by references unknown debt_id {ref!r}"
            )


def test_causal_evidence_strength_if_present_uses_only_valid_vocabulary(
    ledger: Dict[str, Any],
) -> None:
    for debt in _debts(ledger):
        value = debt.get("causal_evidence_strength")
        if value is None:
            continue
        assert value in VALID_CAUSAL_EVIDENCE_STRENGTH, (
            f"debt {debt['debt_id']} has invalid causal_evidence_strength {value!r}; "
            f"must be one of {sorted(VALID_CAUSAL_EVIDENCE_STRENGTH)} (裁定1)"
        )


# --- debt_adjudication_v1.1.yaml ----------------------------------------


def test_adjudication_schema_is_pinned_version(adjudication: Dict[str, Any]) -> None:
    assert adjudication["schema"] == "voicegenesis-debt-adjudication/1.1"


def test_adjudication_vocabulary_values_is_exactly_four(adjudication: Dict[str, Any]) -> None:
    values = adjudication["vocabulary"]["values"]
    assert isinstance(values, list)
    assert set(values) == VALID_CAUSAL_EVIDENCE_STRENGTH
    assert len(values) == 4


def test_adjudication_c0_c3_symbols_prohibited(adjudication: Dict[str, Any]) -> None:
    assert adjudication["vocabulary"]["c0_c3_symbols"] == "prohibited"


def test_adjudication_canonical_field_name(adjudication: Dict[str, Any]) -> None:
    assert adjudication["vocabulary"]["canonical_field"] == "causal_evidence_strength"


def test_adjudication_fixed_probe_does_not_require_git_wav(adjudication: Dict[str, Any]) -> None:
    assert adjudication["fixed_probe"]["git_wav_required"] is False


def test_adjudication_track_c_prohibits_duplicate_r0_r4_execution(
    adjudication: Dict[str, Any],
) -> None:
    assert adjudication["track_c"]["duplicate_r0_r4_execution"] == "prohibited"


def test_adjudication_run_contract_prohibits_parallel_canonical(
    adjudication: Dict[str, Any],
) -> None:
    assert adjudication["run_contract"]["parallel_contract_canonical"] == "prohibited"
    assert adjudication["run_contract"]["runbook_may_override_design"] is False


def test_adjudication_run4_does_not_require_rerun(adjudication: Dict[str, Any]) -> None:
    assert adjudication["run4"]["rerun_required"] is False
    assert adjudication["run4"]["status"] == "PASS_WITH_RESIDUAL"
