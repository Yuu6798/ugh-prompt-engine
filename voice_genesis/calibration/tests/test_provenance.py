from __future__ import annotations

import json

from voice_genesis.calibration.provenance import (
    CampaignIdentity,
    CodeIdentity,
    Ledger,
    LedgerEntry,
    ProvenanceRecord,
    provenance_record_to_dict,
)
from voice_genesis.calibration.vocab import BlockedCode


def test_provenance_record_serializes_nested_dataclasses() -> None:
    record = ProvenanceRecord(
        campaign=CampaignIdentity(
            campaign_id="RUN10-CAL",
            campaign_parent_id=None,
            event_id="ev-1",
            event_time_utc="2026-09-01T00:00:00Z",
            actor="test",
            authorization_flags={"execution_authorized": False},
        ),
        code=CodeIdentity(
            source_document_ids=("doc-1",),
            source_document_hashes=("abc123",),
            repo_url="https://example.invalid/repo",
            code_sha="deadbeef",
            dirty_state=False,
            dependency_lock_hash="lockhash",
        ),
    )
    d = provenance_record_to_dict(record)
    assert d["campaign"]["campaign_id"] == "RUN10-CAL"
    assert d["code"]["source_document_ids"] == ["doc-1"]
    assert d["control_gate"] == "APPLICABLE"
    assert d["unstable_cell"] is False


def test_ledger_append_and_read_round_trip(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    e1 = ledger.append({"kind": "render", "row_id": "r1"})
    e2 = ledger.append({"kind": "meter_call", "row_id": "r1"})
    assert e1.seq == 0
    assert e2.seq == 1
    assert e2.prev_sha == e1.entry_sha

    reloaded = Ledger(tmp_path / "ledger.jsonl")
    assert len(reloaded.entries) == 2
    assert reloaded.entries[0].payload["row_id"] == "r1"


def test_ledger_verify_chain_ok_on_untampered(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i in range(5):
        ledger.append({"kind": "meter_call", "row_id": f"r{i}"})
    result = ledger.verify_chain()
    assert result.ok is True
    assert result.entries_verified == 5
    assert result.tamper_at_seq is None
    assert result.truncated_tail is False


def test_ledger_verify_chain_detects_content_tamper(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})
    ledger.append({"kind": "meter_call", "row_id": "r2"})

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["row_id"] = "TAMPERED"
    lines[1] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fresh = Ledger(path)
    result = fresh.verify_chain()
    assert result.ok is False
    assert result.tamper_at_seq == 1
    assert "tamper" in result.detail or "mismatch" in result.detail


def test_ledger_verify_chain_detects_prev_sha_mismatch_sibling_branch(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["prev_sha"] = "f" * 64  # 別 chain から分岐したかのような偽 prev_sha
    lines[1] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    fresh = Ledger(path)
    result = fresh.verify_chain()
    assert result.ok is False
    assert result.tamper_at_seq == 1


def test_ledger_verify_chain_detects_truncated_tail(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 採用] write が中断された痕跡（末尾行が不完全な
    JSON）を、内容改竄とは区別して `truncated_tail=True` として報告する。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    # 2 件目の途中でファイルを切断する (改行なし・JSON 不完全)。
    full_content = path.read_text(encoding="utf-8")
    lines = full_content.splitlines()
    cut_point = len(lines[0]) + 1 + len(lines[1]) // 2
    truncated_content = full_content[:cut_point]
    path.write_text(truncated_content, encoding="utf-8")

    fresh = Ledger(path)
    result = fresh.verify_chain()
    assert result.truncated_tail is True
    # 末尾の壊れた行を除いた prefix (先頭 1 行) は正当な chain として検証される。
    assert result.entries_verified == 1


def test_ledger_verify_chain_truncated_tail_with_valid_prefix_still_ok(tmp_path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    full_content = path.read_text(encoding="utf-8")
    # 先頭 1 行のみを、改行なしで（末尾が切れた体で）書き直す。
    first_line = full_content.splitlines()[0]
    path.write_text(first_line[: len(first_line) // 2], encoding="utf-8")

    result = Ledger(path).verify_chain()
    assert result.truncated_tail is True
    assert result.entries_verified == 0
    assert result.ok is True  # 空の chain は空のまま正当


def test_check_leakage_pre_unseal_access_is_blocked() -> None:
    entries = [
        LedgerEntry(seq=0, prev_sha="0" * 64, entry_sha="a" * 64,
                    payload={"kind": "render", "row_id": "holdout-1"}),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=5)
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.control_excluded_count == 0


def test_check_leakage_unseal_none_blocks_any_holdout_access() -> None:
    entries = [
        LedgerEntry(seq=0, prev_sha="0" * 64, entry_sha="a" * 64,
                    payload={"kind": "meter_call", "row_id": "holdout-1"}),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_post_unseal_access_is_allowed() -> None:
    entries = [
        LedgerEntry(seq=0, prev_sha="0" * 64, entry_sha="a" * 64,
                    payload={"kind": "selection_frozen"}),
        LedgerEntry(seq=1, prev_sha="a" * 64, entry_sha="b" * 64,
                    payload={"kind": "meter_call", "row_id": "holdout-1"}),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=1)
    assert result.blocked is None


def test_check_leakage_non_holdout_row_never_blocks() -> None:
    entries = [
        LedgerEntry(seq=0, prev_sha="0" * 64, entry_sha="a" * 64,
                    payload={"kind": "render", "row_id": "calibration-1"}),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked is None


def test_check_leakage_ignores_non_render_meter_call_entries() -> None:
    entries = [
        LedgerEntry(seq=0, prev_sha="0" * 64, entry_sha="a" * 64,
                    payload={"kind": "split_frozen", "row_id": "holdout-1"}),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked is None


def test_check_leakage_control_row_ids_excluded_non_control_holdout_still_detected() -> None:
    """[IMPLEMENTATION_MAP_v1.md §2.7 control 共有契約] control 行
    (`control_row_ids`) は sweep truth を運ばないため、unseal 前に holdout
    split 上で参照されても leakage としない。一方、同じ entry 集合内の
    非 control な holdout 行は従来どおり検出される。"""
    entries = [
        LedgerEntry(seq=0, prev_sha="0" * 64, entry_sha="a" * 64,
                    payload={"kind": "render", "row_id": "holdout-control-1"}),
        LedgerEntry(seq=1, prev_sha="a" * 64, entry_sha="b" * 64,
                    payload={"kind": "meter_call", "row_id": "holdout-control-1"}),
        LedgerEntry(seq=2, prev_sha="b" * 64, entry_sha="c" * 64,
                    payload={"kind": "meter_call", "row_id": "holdout-sweep-1"}),
    ]
    result = Ledger.check_leakage(
        entries,
        holdout_row_ids=["holdout-control-1", "holdout-sweep-1"],
        unseal_seq=None,
        control_row_ids=["holdout-control-1"],
    )
    # holdout-control-1 への 2 件 (render, meter_call) は除外され、
    # holdout-sweep-1 (非 control) で初めて BLOCKED_LEAKAGE が確定する。
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.control_excluded_count == 2


def test_check_leakage_control_row_pure_control_holdout_never_blocks() -> None:
    entries = [
        LedgerEntry(seq=0, prev_sha="0" * 64, entry_sha="a" * 64,
                    payload={"kind": "render", "row_id": "holdout-control-1"}),
        LedgerEntry(seq=1, prev_sha="a" * 64, entry_sha="b" * 64,
                    payload={"kind": "meter_call", "row_id": "holdout-control-1"}),
    ]
    result = Ledger.check_leakage(
        entries,
        holdout_row_ids=["holdout-control-1"],
        unseal_seq=None,
        control_row_ids=["holdout-control-1"],
    )
    assert result.blocked is None
    assert result.control_excluded_count == 2
