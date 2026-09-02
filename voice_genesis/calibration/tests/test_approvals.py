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
        "cost_caps": {"compute": 3600.0, "storage": 1_000_000, "budget": 10.0},
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
    assert result.record.cost_caps == CostCaps(compute=3600.0, storage=1_000_000, budget=10.0)
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


def test_load_approval_gate1_missing_nonce_is_not_approved(tmp_path: Path) -> None:
    payload = {
        "gate": "GATE1_CAMPAIGN_EXECUTION",
        "approver": "tester",
        "approved_at_utc": "2026-09-02T00:00:00Z",
        "design_doc_sha256": _design_sha(),
        "memo_sha256": _memo_sha(),
        "cost_caps": {"compute": 1.0, "storage": 1, "budget": 1.0},
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
