"""approvals.py の三要素武装判定 + 承認ファイル loader テスト（設計正本 §18,
IMPLEMENTATION_MAP §6.1）。loader は常に test-local な `approval_dir` を渡し、
本リポジトリの `~/.vg_cal/approvals/` を一切参照・書込しない
（IMPLEMENTATION_MAP §0 授権境界）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from voice_genesis.calibration import approvals
from voice_genesis.calibration.cost_caps import CostCaps

_REPO_ROOT = approvals._REPO_ROOT


def _design_sha() -> str:
    return hashlib.sha256((_REPO_ROOT / approvals.DESIGN_DOC_RELATIVE_PATH).read_bytes()).hexdigest()


def _memo_sha() -> str:
    return hashlib.sha256((_REPO_ROOT / approvals.MEMO_RELATIVE_PATH).read_bytes()).hexdigest()


def _base_design_sha() -> str:
    """v1.0（read-only 基底文書）の実測 sha256。v1.1 統治文書切替後、
    `_design_sha()` は `approvals.DESIGN_DOC_RELATIVE_PATH`（= v1.1）を指す
    ため、v1.0 に直接 pin された過去の承認レコード（v1.1 切替前に発行）を
    照合するにはこちらを使う。"""
    return hashlib.sha256(
        (_REPO_ROOT / approvals.BASE_DESIGN_DOC_RELATIVE_PATH).read_bytes()
    ).hexdigest()


#: PR レビュー第 5 巡: gate1/gate2 は同一 authorization_nonce を要求する。
#: テストの既定値は両者一致させておく（不一致ケースは個別テストで上書きする）。
_TEST_NONCE = "test-nonce-0000000000000000"


def _write_gate1(approval_dir: Path, **overrides: object) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _design_sha(),
        "memo_sha256": _memo_sha(),
        "authorization_nonce": _TEST_NONCE,
        "cost_caps": {
            "compute": 3600.0,
            "storage": 1_000_000,
            "budget": 10.0,
            "budget_accounting_mode": "local_zero_cost",
        },
        "e_use_bound_accepted": True,
        "max_claim_scope": ["formant_frequency"],
    }
    payload.update(overrides)
    (approval_dir / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_gate2(approval_dir: Path, manifest_core_sha: str, **overrides: object) -> None:
    payload = {
        "gate": "GATE2_C0_FREEZE",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _design_sha(),
        "memo_sha256": _memo_sha(),
        "authorization_nonce": _TEST_NONCE,
        "manifest_core_sha": manifest_core_sha,
    }
    payload.update(overrides)
    (approval_dir / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE2_C0_FREEZE]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_gate3(approval_dir: Path, **overrides: object) -> None:
    payload = {
        "gate": "GATE3_SEAL_ACCEPTANCE",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _design_sha(),
        "memo_sha256": _memo_sha(),
        "seal_protection_level_accepted": True,
    }
    payload.update(overrides)
    (approval_dir / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE3_SEAL_ACCEPTANCE]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_load_approval_missing_file_is_not_approved(tmp_path: Path) -> None:
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert result.record is None
    assert any("not found" in r for r in result.reasons)


def test_load_approval_valid_gate1(tmp_path: Path) -> None:
    _write_gate1(tmp_path)
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is True
    assert result.record is not None
    assert result.record.cost_caps == CostCaps(
        compute=3600.0,
        storage=1_000_000,
        budget=10.0,
        budget_accounting_mode="local_zero_cost",
    )
    assert result.record.e_use_bound_accepted is True
    assert result.record.max_claim_scope == ("formant_frequency",)
    assert result.record.authorization_nonce == _TEST_NONCE
    assert result.content_sha256 is not None


def test_load_approval_gate1_duplicate_max_claim_scope_is_not_approved(tmp_path: Path) -> None:
    """第 11 巡採用: `max_claim_scope` に同じ construct-id が重複していると
    shape 検証で fail-closed に拒否される（registry との突合は
    `c0_freeze._check_max_claim_scope()` の責務なので、ここでは重複という
    shape の問題のみを見る）。"""
    _write_gate1(tmp_path, max_claim_scope=["formant_frequency", "formant_frequency"])
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any("duplicate" in r and "max_claim_scope" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# round 24 ADOPT (2) P2 (`[UNDERSPEC-CAL-D56]`): `approved_at_utc` must parse
# as an ISO 8601 UTC timestamp before an approval is accepted.
# ---------------------------------------------------------------------------


def test_load_approval_gate1_unparsable_timestamp_is_not_approved(tmp_path: Path) -> None:
    _write_gate1(tmp_path, approved_at_utc="tomorrow")
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert result.record is None
    assert any(
        "approved_at_utc" in r and "ISO 8601" in r for r in result.reasons
    ), result.reasons


def test_load_approval_gate1_naive_timestamp_is_not_approved(tmp_path: Path) -> None:
    """No UTC offset at all (naive) — distinct from a non-UTC offset, both
    rejected by the same ISO 8601 UTC check."""
    _write_gate1(tmp_path, approved_at_utc="2026-09-02T00:00:00")
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any(
        "approved_at_utc" in r and "ISO 8601" in r for r in result.reasons
    ), result.reasons


def test_load_approval_gate1_non_utc_offset_timestamp_is_not_approved(tmp_path: Path) -> None:
    _write_gate1(tmp_path, approved_at_utc="2026-09-02T09:00:00+09:00")
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any(
        "approved_at_utc" in r and "ISO 8601" in r for r in result.reasons
    ), result.reasons


@pytest.mark.parametrize(
    "approved_at_utc",
    [
        "2026-09-02T00:00:00Z",
        "2026-09-02T00:00:00+00:00",
        "2026-09-02T00:00:00.123456Z",
    ],
)
def test_load_approval_gate1_valid_utc_timestamp_forms_are_accepted(
    tmp_path: Path, approved_at_utc: str
) -> None:
    _write_gate1(tmp_path, approved_at_utc=approved_at_utc)
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is True
    assert result.record is not None
    assert result.record.approved_at_utc == approved_at_utc


def test_is_iso8601_utc_timestamp_rejects_non_string() -> None:
    assert approvals._is_iso8601_utc_timestamp(None) is False
    assert approvals._is_iso8601_utc_timestamp(12345) is False
    assert approvals._is_iso8601_utc_timestamp("") is False


def test_load_approval_gate1_missing_nonce_is_not_approved(tmp_path: Path) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _design_sha(),
        "memo_sha256": _memo_sha(),
        "cost_caps": {
            "compute": 1.0,
            "storage": 1,
            "budget": 1.0,
            "budget_accounting_mode": "local_zero_cost",
        },
        "e_use_bound_accepted": True,
        "max_claim_scope": [],
    }
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any("authorization_nonce" in r for r in result.reasons)


def test_check_armed_gate2_nonce_mismatch_with_gate1_is_authorization_required(
    tmp_path: Path,
) -> None:
    _write_gate1(tmp_path, authorization_nonce="nonce-A")
    _write_gate2(tmp_path, manifest_core_sha="a" * 64, authorization_nonce="nonce-B")
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is False
    assert decision.code == approvals.AUTHORIZATION_REQUIRED
    assert any("nonce_mismatch" in m for m in decision.missing_factors)


def test_check_armed_gate2_matching_nonce_with_gate1_is_armed(tmp_path: Path) -> None:
    _write_gate1(tmp_path, authorization_nonce="same-nonce")
    _write_gate2(tmp_path, manifest_core_sha="a" * 64, authorization_nonce="same-nonce")
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is True


def test_check_armed_gate2_without_gate1_present_skips_nonce_check(tmp_path: Path) -> None:
    """Gate 1 が全く承認されていない場合、Gate 2 単体は nonce cross-check の
    対象外（別問題として manifest validation 側で表面化する）。"""
    _write_gate2(tmp_path, manifest_core_sha="a" * 64, authorization_nonce="only-gate2")
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is True


def test_design_doc_sha_mismatch_is_not_approved(tmp_path: Path) -> None:
    _write_gate1(tmp_path, design_doc_sha256="f" * 64)
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any("design_doc_sha256 mismatch" in r for r in result.reasons)


def test_memo_sha_mismatch_is_not_approved(tmp_path: Path) -> None:
    _write_gate1(tmp_path, memo_sha256="a" * 64)
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any("memo_sha256 mismatch" in r for r in result.reasons)


def test_malformed_json_is_not_approved(tmp_path: Path) -> None:
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    path.write_text("{not json", encoding="utf-8")
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert result.content_sha256 is not None  # file was read even though unparseable


def test_gate1_missing_cost_caps_key_is_not_approved(tmp_path: Path) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _design_sha(),
        "memo_sha256": _memo_sha(),
        "e_use_bound_accepted": True,
        "max_claim_scope": [],
    }
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any("cost_caps" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# round 13 finding #3 (`[UNDERSPEC-CAL-D27]`): the Gate 1 schema must
# accept/validate `budget_accounting_mode` alongside compute/storage/budget.
# ---------------------------------------------------------------------------


def test_gate1_missing_budget_accounting_mode_is_not_approved(tmp_path: Path) -> None:
    _write_gate1(tmp_path, cost_caps={"compute": 3600.0, "storage": 1_000_000, "budget": 10.0})
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any(
        "cost_caps" in r and "budget_accounting_mode" in r for r in result.reasons
    )


def test_gate1_unknown_budget_accounting_mode_is_not_approved(tmp_path: Path) -> None:
    _write_gate1(
        tmp_path,
        cost_caps={
            "compute": 3600.0,
            "storage": 1_000_000,
            "budget": 10.0,
            "budget_accounting_mode": "pay_as_you_go",
        },
    )
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is False
    assert any("cost_caps" in r for r in result.reasons)


def test_gate1_per_unit_fixed_budget_accounting_mode_is_accepted(tmp_path: Path) -> None:
    _write_gate1(
        tmp_path,
        cost_caps={
            "compute": 3600.0,
            "storage": 1_000_000,
            "budget": 10.0,
            "budget_accounting_mode": "per_unit_fixed",
            "budget_unit_cost": 0.01,
        },
    )
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is True
    assert result.record is not None
    assert result.record.cost_caps is not None
    assert result.record.cost_caps.budget_accounting_mode == "per_unit_fixed"
    assert result.record.cost_caps.budget_unit_cost == pytest.approx(0.01)


def test_approval_record_never_carries_campaign_id(tmp_path: Path) -> None:
    """PR レビュー第 2 巡: ハッシュ循環回避のため campaign_id は承認ファイルに
    含めない。dataclass に該当フィールドが存在しないことを型レベルで確認する。"""
    assert not hasattr(approvals.ApprovalRecord, "campaign_id")
    field_names = {f for f in approvals.ApprovalRecord.__dataclass_fields__}
    assert "campaign_id" not in field_names


# ---------------------------------------------------------------------------
# 三要素武装判定: 各要素が単独で欠けても AUTHORIZATION_REQUIRED
# ---------------------------------------------------------------------------


def test_check_armed_all_three_factors_present_is_armed(tmp_path: Path) -> None:
    _write_gate2(tmp_path, manifest_core_sha="a" * 64)
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is True
    assert decision.missing_factors == ()
    assert decision.approval is not None
    assert decision.code is None


def test_check_armed_missing_cli_flag(tmp_path: Path) -> None:
    _write_gate2(tmp_path, manifest_core_sha="a" * 64)
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        False,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is False
    assert decision.code == approvals.AUTHORIZATION_REQUIRED
    assert any(m.startswith("cli_flag:") for m in decision.missing_factors)
    assert decision.approval is None


def test_check_armed_missing_env_var(tmp_path: Path) -> None:
    _write_gate2(tmp_path, manifest_core_sha="a" * 64)
    decision = approvals.check_armed(approvals.Gate.GATE2_C0_FREEZE, True, {}, tmp_path)
    assert decision.armed is False
    assert any(m.startswith("env:") for m in decision.missing_factors)


def test_check_armed_wrong_env_value(tmp_path: Path) -> None:
    _write_gate2(tmp_path, manifest_core_sha="a" * 64)
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "true"},
        tmp_path,
    )
    assert decision.armed is False
    assert any(m.startswith("env:") for m in decision.missing_factors)


def test_check_armed_missing_approval_file(tmp_path: Path) -> None:
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is False
    assert any(m.startswith("approval_file:") for m in decision.missing_factors)


def test_check_armed_all_three_missing_lists_all(tmp_path: Path) -> None:
    decision = approvals.check_armed(approvals.Gate.GATE2_C0_FREEZE, False, {}, tmp_path)
    assert decision.armed is False
    kinds = {m.split(":", 1)[0] for m in decision.missing_factors}
    assert kinds == {"cli_flag", "env", "approval_file"}


@pytest.mark.parametrize(
    "gate,env_var",
    [
        (approvals.Gate.GATE1_CAMPAIGN_EXECUTION, "VG_CAL_CAMPAIGN_AUTHORIZED"),
        (approvals.Gate.GATE2_C0_FREEZE, "VG_CAL_C0_FREEZE_AUTHORIZED"),
        (approvals.Gate.GATE3_SEAL_ACCEPTANCE, "VG_CAL_CAMPAIGN_AUTHORIZED"),
    ],
)
def test_gate_env_var_mapping(gate: approvals.Gate, env_var: str) -> None:
    assert approvals.GATE_ENV_VAR[gate] == env_var


def test_gate1_armed_with_valid_file(tmp_path: Path) -> None:
    _write_gate1(tmp_path)
    decision = approvals.check_armed(
        approvals.Gate.GATE1_CAMPAIGN_EXECUTION,
        True,
        {"VG_CAL_CAMPAIGN_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is True


def test_gate3_armed_with_valid_file(tmp_path: Path) -> None:
    _write_gate3(tmp_path)
    decision = approvals.check_armed(
        approvals.Gate.GATE3_SEAL_ACCEPTANCE,
        True,
        {"VG_CAL_CAMPAIGN_AUTHORIZED": "1"},
        tmp_path,
    )
    assert decision.armed is True
    assert decision.approval is not None
    assert decision.approval.seal_protection_level_accepted is True


def test_default_approval_dir_uses_env_override() -> None:
    resolved = approvals.default_approval_dir({"VG_CAL_APPROVAL_DIR": "/tmp/custom-approvals"})
    assert resolved == Path("/tmp/custom-approvals")


def test_default_approval_dir_falls_back_to_home() -> None:
    resolved = approvals.default_approval_dir({})
    assert resolved == approvals.DEFAULT_APPROVAL_DIR
    assert str(resolved).endswith(".vg_cal/approvals")


def test_load_approval_reads_file_bytes_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR レビュー第 4 巡: parse と sha256 の対象バッファがずれないよう、
    承認ファイルは 1 回の `read_bytes()` のみで読む（別読みによる版ずれ防止）。"""
    _write_gate1(tmp_path)
    call_count = {"n": 0}
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        if self == tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]:
            call_count["n"] += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    result = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert result.approved is True
    assert call_count["n"] == 1


def test_load_all_approvals_only_reads_approval_dir_param(tmp_path: Path) -> None:
    """loader は `approval_dir` 引数のみを見る（`os.environ`/repo 内 fallback は
    呼び出し側 CLI の責務であり、本体 loader は探索しない）。"""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    all_results = approvals.load_all_approvals(empty_dir)
    assert set(all_results) == set(approvals.Gate)
    assert all(not r.approved for r in all_results.values())


# ---------------------------------------------------------------------------
# check_armed(preloaded=...) — avoids re-reading approval files from disk
# (PR review round 6 #5)
# ---------------------------------------------------------------------------


def test_check_armed_preloaded_does_not_touch_disk(tmp_path: Path) -> None:
    _write_gate1(tmp_path, authorization_nonce="same-nonce")
    _write_gate2(tmp_path, manifest_core_sha="a" * 64, authorization_nonce="same-nonce")
    preloaded = approvals.load_all_approvals(tmp_path)

    # A bogus approval_dir: if `preloaded` is actually honored, check_armed
    # never touches this directory at all.
    bogus_dir = tmp_path / "does-not-exist"
    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        bogus_dir,
        preloaded=preloaded,
    )
    assert decision.armed is True


def test_check_armed_preloaded_still_detects_nonce_mismatch(tmp_path: Path) -> None:
    _write_gate1(tmp_path, authorization_nonce="nonce-A")
    _write_gate2(tmp_path, manifest_core_sha="a" * 64, authorization_nonce="nonce-B")
    preloaded = approvals.load_all_approvals(tmp_path)

    decision = approvals.check_armed(
        approvals.Gate.GATE2_C0_FREEZE,
        True,
        {"VG_CAL_C0_FREEZE_AUTHORIZED": "1"},
        tmp_path / "unused",
        preloaded=preloaded,
    )
    assert decision.armed is False
    assert any("nonce_mismatch" in m for m in decision.missing_factors)


# ---------------------------------------------------------------------------
# refresh_document_hashes() — re-stamp design_doc_sha256/memo_sha256
# ---------------------------------------------------------------------------


def test_refresh_document_hashes_updates_only_hash_fields(tmp_path: Path) -> None:
    _write_gate1(tmp_path, design_doc_sha256="f" * 64, memo_sha256="a" * 64)
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    before = json.loads(path.read_text(encoding="utf-8"))

    result = approvals.refresh_document_hashes(path, _REPO_ROOT)

    assert result.old_design_doc_sha256 == "f" * 64
    assert result.new_design_doc_sha256 == _design_sha()
    assert result.old_memo_sha256 == "a" * 64
    assert result.new_memo_sha256 == _memo_sha()
    assert result.changed is True

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["design_doc_sha256"] == _design_sha()
    assert after["memo_sha256"] == _memo_sha()
    # Every other field is untouched.
    for key in before:
        if key in ("design_doc_sha256", "memo_sha256"):
            continue
        assert after[key] == before[key], key

    # And the file is now actually loadable/approved (hash mismatch resolved).
    loaded = approvals.load_approval(approvals.Gate.GATE1_CAMPAIGN_EXECUTION, tmp_path)
    assert loaded.approved is True


def test_refresh_document_hashes_is_idempotent_when_already_current(tmp_path: Path) -> None:
    _write_gate1(tmp_path)  # already uses the current real hashes
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    result = approvals.refresh_document_hashes(path, _REPO_ROOT)
    assert result.changed is False
    assert result.old_design_doc_sha256 == result.new_design_doc_sha256 == _design_sha()
    assert result.old_memo_sha256 == result.new_memo_sha256 == _memo_sha()


def test_refresh_document_hashes_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        approvals.refresh_document_hashes(tmp_path / "nope.json", _REPO_ROOT)


def test_refresh_document_hashes_malformed_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        approvals.refresh_document_hashes(path, _REPO_ROOT)


def test_refresh_document_hashes_non_object_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "array.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        approvals.refresh_document_hashes(path, _REPO_ROOT)


def test_refresh_document_hashes_atomic_no_tmp_file_left_behind(tmp_path: Path) -> None:
    _write_gate1(tmp_path, design_doc_sha256="f" * 64)
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    approvals.refresh_document_hashes(path, _REPO_ROOT)
    leftover = [p for p in tmp_path.iterdir() if p.name != path.name]
    assert leftover == []


# ---------------------------------------------------------------------------
# CLI — `python -m voice_genesis.calibration.approvals refresh --gate gate1 ...`
# ---------------------------------------------------------------------------


def test_cli_refresh_updates_gate1_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_gate1(tmp_path, design_doc_sha256="f" * 64, memo_sha256="a" * 64)
    rc = approvals.main(
        ["refresh", "--gate", "gate1", "--approval-dir", str(tmp_path), "--repo-root", str(_REPO_ROOT)]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "design_doc_sha256:" in out
    assert "memo_sha256:" in out
    assert "changed: True" in out

    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["design_doc_sha256"] == _design_sha()
    assert after["memo_sha256"] == _memo_sha()


def test_cli_refresh_gate3_maps_short_name_correctly(tmp_path: Path) -> None:
    _write_gate3(tmp_path, design_doc_sha256="f" * 64)
    rc = approvals.main(
        ["refresh", "--gate", "gate3", "--approval-dir", str(tmp_path), "--repo-root", str(_REPO_ROOT)]
    )
    assert rc == 0
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE3_SEAL_ACCEPTANCE]
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["design_doc_sha256"] == _design_sha()


def test_cli_refresh_uses_default_approval_dir_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_gate1(tmp_path, design_doc_sha256="f" * 64)
    monkeypatch.setenv(approvals.APPROVAL_DIR_ENV_VAR, str(tmp_path))
    rc = approvals.main(["refresh", "--gate", "gate1", "--repo-root", str(_REPO_ROOT)])
    assert rc == 0
    path = tmp_path / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["design_doc_sha256"] == _design_sha()


def test_cli_refresh_missing_gate_arg_errors() -> None:
    with pytest.raises(SystemExit):
        approvals.main(["refresh"])


# ---------------------------------------------------------------------------
# round 23 ADOPT (1): the checkout-internal reference copy of the Gate 1
# approval file (`approvals/README.md`'s "reference copy" rule — the loader
# never reads it, only `~/.vg_cal/approvals/` does) drifts silently whenever
# `DESIGN_VG_METER_CAL_DEBT_v1.0.md`/`IMPLEMENTATION_MAP_v1.md` are edited
# without a matching `refresh_document_hashes()` re-stamp of that copy (the
# live approval file used by `load_approval()` is outside the checkout, so
# CI cannot see a drift there — only this reference copy is inspectable).
# `[UNDERSPEC-CAL-D51]`.
# ---------------------------------------------------------------------------

_GATE1_RECORD_COPY_RELATIVE_PATH = (
    "voice_genesis/calibration/approvals/records/gate1_campaign_execution.2026-09-02.json"
)


def test_repo_gate1_record_copy_document_hashes_match_tree_at_head() -> None:
    """`approvals/records/gate1_campaign_execution.2026-09-02.json`（checkout
    内の参照用コピー。正本は checkout 外の `~/.vg_cal/approvals/` で本テストの
    対象外）の `design_doc_sha256`/`memo_sha256` が、現在の
    `DESIGN_VG_METER_CAL_DEBT_v1.0.md`/`IMPLEMENTATION_MAP_v1.md` の実測 sha256
    と一致することを確認する regression guard（round 23 ADOPT (1):
    `memo_sha256` が stale だった finding の再発防止）。

    期待値は `_base_design_sha()`/`_memo_sha()` が現在の working tree から都度
    実測する値であり、本テスト内にハッシュを一切ハードコードしない —
    DESIGN/メモを将来編集し、その都度 `refresh_document_hashes()` で参照用
    コピーを追随させる正当な変更では失敗しない。比較対象は「committed record
    の値」と「working tree のドキュメント実体」であり、`c0_validate.py` の
    `repo.dirty_tree` チェック（未コミット差分の有無）とは無関係 — 本テストが
    dirty tree で意味を変えることはない（GATE1_DECISION_RECORD.md 冒頭の
    注記どおり、承認ファイルの正本自体は git 管理外）。

    v1.1 統治文書切替（§V6, 2026-09-04）後の注記: このレコードは切替前
    （2026-09-02/03）に発行され、承認時点で pin していたのは v1.0 のため、
    比較対象は `approvals.DESIGN_DOC_RELATIVE_PATH`（切替後は v1.1）ではなく
    `approvals.BASE_DESIGN_DOC_RELATIVE_PATH`（v1.0、常に不変の read-only
    基底文書）である — `_design_sha()` ではなく `_base_design_sha()` を使う。
    """
    record_path = _REPO_ROOT / _GATE1_RECORD_COPY_RELATIVE_PATH
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert payload["design_doc_sha256"] == _base_design_sha()
    assert payload["memo_sha256"] == _memo_sha()


# ---------------------------------------------------------------------------
# §V6「統治文書の切替 + 基底文書の実行時 pin」(DESIGN_VG_METER_CAL_DEBT_v1.1.md):
# `load_approval()` は承認ファイル自体の design_doc_sha256（= v1.1 の実測
# sha256）照合に加え、v1.1 front matter の `base_document_sha256` と checkout
# 上の v1.0 の実測 sha256 が一致することを検証する（信頼の連鎖: 承認 →
# v1.1 バイト列 → v1.0 バイト列）。実リポジトリの v1.0/v1.1 を書き換えずに
# 検証するため、各テストは独立した tmp repo_root へ両ドキュメント（+ memo）
# を複製し、そこだけを改変する。
# ---------------------------------------------------------------------------


def _write_base_pin_fixture_repo(
    tmp_path: Path, *, corrupt_base_doc: bool = False, corrupt_front_matter: bool = False
) -> Path:
    """`tmp_path` 配下に `DESIGN_DOC_RELATIVE_PATH`/`BASE_DESIGN_DOC_RELATIVE_PATH`/
    `MEMO_RELATIVE_PATH` と同じ相対 path で実ドキュメントのコピーを作り、
    そのルート（= 使うべき `repo_root`）を返す。`corrupt_base_doc=True` は
    v1.0 コピーを 1 バイト改変（front matter の base_document_sha256 と実測が
    食い違う状態を作る）。`corrupt_front_matter=True` は v1.1 コピーの front
    matter を壊れた YAML に置換する（パース不能ケース）。"""
    real_v11 = _REPO_ROOT / approvals.DESIGN_DOC_RELATIVE_PATH
    real_v10 = _REPO_ROOT / approvals.BASE_DESIGN_DOC_RELATIVE_PATH
    real_memo = _REPO_ROOT / approvals.MEMO_RELATIVE_PATH

    v11_dst = tmp_path / approvals.DESIGN_DOC_RELATIVE_PATH
    v10_dst = tmp_path / approvals.BASE_DESIGN_DOC_RELATIVE_PATH
    memo_dst = tmp_path / approvals.MEMO_RELATIVE_PATH
    v11_dst.parent.mkdir(parents=True, exist_ok=True)
    memo_dst.parent.mkdir(parents=True, exist_ok=True)

    v11_text = real_v11.read_text(encoding="utf-8")
    if corrupt_front_matter:
        # 先頭の `---` を落として front matter 自体を消す — `yaml.safe_load`
        # 云々ではなく、そもそも `_FRONT_MATTER_RE` にマッチしなくなるケース。
        v11_text = v11_text.replace("---\n", "***\n", 1)
    v11_dst.write_text(v11_text, encoding="utf-8")

    v10_bytes = real_v10.read_bytes()
    if corrupt_base_doc:
        v10_bytes = v10_bytes + b"\n<!-- tampered for test -->\n"
    v10_dst.write_bytes(v10_bytes)

    memo_dst.write_bytes(real_memo.read_bytes())
    return tmp_path


def _write_gate1_for_repo_root(approval_dir: Path, repo_root: Path, **overrides: object) -> None:
    """`_write_gate1()` 相当だが、design_doc_sha256/memo_sha256 を `_REPO_ROOT`
    ではなく `repo_root`（V6 fixture の tmp コピー）の実測値でスタンプする。"""
    design_sha = hashlib.sha256(
        (repo_root / approvals.DESIGN_DOC_RELATIVE_PATH).read_bytes()
    ).hexdigest()
    memo_sha = hashlib.sha256((repo_root / approvals.MEMO_RELATIVE_PATH).read_bytes()).hexdigest()
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-04T00:00:00Z",
        "design_doc_sha256": design_sha,
        "memo_sha256": memo_sha,
        "authorization_nonce": _TEST_NONCE,
        "cost_caps": {
            "compute": 3600.0,
            "storage": 1_000_000,
            "budget": 10.0,
            "budget_accounting_mode": "local_zero_cost",
        },
        "e_use_bound_accepted": True,
        "max_claim_scope": ["formant_frequency"],
    }
    payload.update(overrides)
    (approval_dir / approvals.APPROVAL_FILENAMES[approvals.Gate.GATE1_CAMPAIGN_EXECUTION]).write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_base_document_pin_holds_with_unmodified_v10_and_v11(tmp_path: Path) -> None:
    """(a) 正しい v1.1 front matter（実物そのまま）+ 無改変 v1.0 コピー ->
    base_document_sha256 検証は pin 成立し、通常どおり approved になる。"""
    repo_root = _write_base_pin_fixture_repo(tmp_path / "repo")
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1_for_repo_root(approval_dir, repo_root)

    result = approvals.load_approval(
        approvals.Gate.GATE1_CAMPAIGN_EXECUTION, approval_dir, repo_root=repo_root
    )
    assert result.approved is True, result.reasons
    assert result.record is not None


def test_base_document_pin_rejects_modified_v10(tmp_path: Path) -> None:
    """(b) v1.0 を 1 バイト改変すると、front matter が pin する
    base_document_sha256 と実測 sha256 が食い違い、未承認 + 理由列挙になる
    （承認ファイル自体は正しくても、というのが要点: base pin は承認ファイル
    の内容と独立に検証される）。"""
    repo_root = _write_base_pin_fixture_repo(tmp_path / "repo", corrupt_base_doc=True)
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1_for_repo_root(approval_dir, repo_root)

    result = approvals.load_approval(
        approvals.Gate.GATE1_CAMPAIGN_EXECUTION, approval_dir, repo_root=repo_root
    )
    assert result.approved is False
    assert any(
        "base_document_sha256 mismatch" in r for r in result.reasons
    ), result.reasons


def test_base_document_pin_rejects_missing_front_matter(tmp_path: Path) -> None:
    """(c) v1.1 の front matter が読めない（先頭の `---` 区切りが壊れている）
    場合は base_document_sha256 自体を検証できず、fail-closed で未承認になる。"""
    repo_root = _write_base_pin_fixture_repo(tmp_path / "repo", corrupt_front_matter=True)
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1_for_repo_root(approval_dir, repo_root)

    result = approvals.load_approval(
        approvals.Gate.GATE1_CAMPAIGN_EXECUTION, approval_dir, repo_root=repo_root
    )
    assert result.approved is False
    assert any(
        "base_document_sha256" in r and ("missing" in r or "unparsable" in r)
        for r in result.reasons
    ), result.reasons


def test_base_document_pin_rejects_missing_base_document_sha_field(tmp_path: Path) -> None:
    """front matter は存在するが `base_document_sha256` フィールド自体が
    欠落/不正な形式（sha256 hex でない）ケース。"""
    repo_root = tmp_path / "repo"
    v11_dst = repo_root / approvals.DESIGN_DOC_RELATIVE_PATH
    v10_dst = repo_root / approvals.BASE_DESIGN_DOC_RELATIVE_PATH
    memo_dst = repo_root / approvals.MEMO_RELATIVE_PATH
    v11_dst.parent.mkdir(parents=True, exist_ok=True)
    memo_dst.parent.mkdir(parents=True, exist_ok=True)

    v11_dst.write_text(
        "---\ndocument_id: TEST\nbase_document_path: irrelevant.md\n---\n# body\n",
        encoding="utf-8",
    )
    v10_dst.write_bytes((_REPO_ROOT / approvals.BASE_DESIGN_DOC_RELATIVE_PATH).read_bytes())
    memo_dst.write_bytes((_REPO_ROOT / approvals.MEMO_RELATIVE_PATH).read_bytes())

    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1_for_repo_root(approval_dir, repo_root)

    result = approvals.load_approval(
        approvals.Gate.GATE1_CAMPAIGN_EXECUTION, approval_dir, repo_root=repo_root
    )
    assert result.approved is False
    assert any(
        "base_document_sha256" in r and "missing/invalid" in r for r in result.reasons
    ), result.reasons


def test_verify_base_document_pin_directly_ok(tmp_path: Path) -> None:
    """`_verify_base_document_pin()` 単体呼び出しでも、無改変コピーなら空
    reasons（pin 成立）を返す。R16 対応で v1.1 バイト列は呼び出し側が渡す。"""
    repo_root = _write_base_pin_fixture_repo(tmp_path / "repo")
    design_doc_bytes = (repo_root / approvals.DESIGN_DOC_RELATIVE_PATH).read_bytes()
    assert approvals._verify_base_document_pin(repo_root, design_doc_bytes) == []


def test_load_approval_reads_design_doc_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R16 対応（Codex PR #346 第 16 巡指摘）: `load_approval()` は v1.1
    統治文書を **1 回だけ** 読み、その同一バイト列から design_doc_sha256 と
    `_verify_base_document_pin()` の front matter 解析の両方を導出する。
    hash 導出と pin 検証が別々の読取に基づくと、その間隔で文書が差し替わり
    「hash は版 A・base pin は版 B」の組み合わせで承認が通り得た
    （承認 provenance の汚染）。`Path.read_bytes` の呼び出し回数を数え、
    v1.1 パスに対する呼び出しがちょうど 1 回であることを固定する。
    """
    repo_root = _write_base_pin_fixture_repo(tmp_path / "repo")
    approval_dir = tmp_path / "approvals"
    approval_dir.mkdir()
    _write_gate1_for_repo_root(approval_dir, repo_root)

    design_doc_path = repo_root / approvals.DESIGN_DOC_RELATIVE_PATH
    read_bytes_calls: list[Path] = []
    original_read_bytes = Path.read_bytes

    def counting_read_bytes(self: Path) -> bytes:
        read_bytes_calls.append(self)
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    result = approvals.load_approval(
        approvals.Gate.GATE1_CAMPAIGN_EXECUTION, approval_dir, repo_root=repo_root
    )
    assert result.approved is True, result.reasons

    design_doc_reads = [p for p in read_bytes_calls if p == design_doc_path]
    assert len(design_doc_reads) == 1, read_bytes_calls
