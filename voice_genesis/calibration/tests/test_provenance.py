from __future__ import annotations

import json

import pytest

from voice_genesis.calibration.provenance import (
    CampaignIdentity,
    CodeIdentity,
    Ledger,
    LedgerChainInvalidError,
    LedgerEntry,
    LedgerTruncatedTailError,
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


def test_ledger_append_refreshes_entries_without_reconstruction(tmp_path) -> None:
    """[P1] regression: `append()` の in-memory キャッシュ再構築は
    `self._entries, self._malformed = self._read_all()` のようにアンパック
    しなければならない。旧実装は `self._entries = list(self._read_all())`
    のように `(entries, malformed)` の 2-tuple をそのまま代入していたため、
    `ledger.entries` が `LedgerEntry` インスタンスの代わりに
    `[entries_list, malformed_list]` という 2 要素の list を返すように
    壊れており、`check_leakage(ledger.entries, ...)` が
    `AttributeError: 'list' object has no attribute 'payload'` で
    クラッシュしていた（別インスタンスとして再構築すれば
    `_read_all()` が `__init__` 経路で正しくアンパックされるため露呈せず、
    append 直後に同一インスタンスの `.entries` を使う経路でのみ再現する）。"""
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"kind": "render", "row_id": "r1"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    # append 直後、再構築なしで ledger.entries を直接使う (regression 経路)。
    assert len(ledger.entries) == 2
    assert all(isinstance(e, LedgerEntry) for e in ledger.entries)
    assert ledger.malformed_lines == ()

    # check_leakage は再構築なしでもクラッシュせず正常に動く。
    result = Ledger.check_leakage(ledger.entries, holdout_row_ids=["holdout-x"], unseal_seq=None)
    assert result.blocked is None


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
    # fail-closed（Codex レビュー 2026-09-01 採用）: truncated tail は常に
    # ok=False。「検証未完了」であって「検証に成功した」のではない。
    assert result.ok is False


def test_ledger_verify_chain_truncated_tail_with_valid_prefix_fails_closed(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 採用] 旧実装は truncated tail でも有効な
    prefix さえあれば `ok=True` を返していた（fail-open のバグ）。prefix が
    空であっても truncated tail である以上、`ok=False` を返す（fail-closed）。
    """
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
    assert result.ok is False  # fail-closed: truncated tail は常に ok=False


def test_ledger_append_refuses_when_existing_tail_truncated(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 採用] `append()` は、既存ファイルの末尾が
    truncated（write 中断で不完全な最終行）だった場合、その破損 bytes へ盲目的
    に追記して破損を積み重ねてはならず、`LedgerTruncatedTailError` で
    fail-closed する（ファイル内容も一切変更しない）。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    full_content = path.read_text(encoding="utf-8")
    lines = full_content.splitlines()
    cut_point = len(lines[0]) + 1 + len(lines[1]) // 2
    truncated_content = full_content[:cut_point]
    path.write_text(truncated_content, encoding="utf-8")

    fresh = Ledger(path)
    with pytest.raises(LedgerTruncatedTailError):
        fresh.append({"kind": "meter_call", "row_id": "r2"})

    # append 失敗後もファイル内容は一切変更されていない（破損 bytes への
    # 追記が起きていないことを直接検証する）。
    assert path.read_text(encoding="utf-8") == truncated_content


def test_ledger_append_refuses_when_middle_entry_tampered(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 P1 finding #4] `append()` は末尾行の
    seq/entry_sha だけでなく既存台帳の全 chain を検証する。途中（末尾ではない）
    のエントリが改竄されていれば `LedgerChainInvalidError` を送出し、ファイルへ
    は一切書き込まない（改竄行より後ろの行だけを見る旧実装は、末尾が改竄後に
    再計算された "整合する" prev_sha 連鎖で偽装されていれば検出できなかった）。
    """
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "render", "row_id": "r0"})
    ledger.append({"kind": "render", "row_id": "r1"})
    ledger.append({"kind": "render", "row_id": "r2"})

    # 先頭（末尾ではない）エントリの payload を改竄する。entry_sha/prev_sha は
    # 再計算しないため、末尾行だけを見れば依然 "整合する" ように見える。
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["row_id"] = "TAMPERED"
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    fresh = Ledger(path)
    with pytest.raises(LedgerChainInvalidError) as excinfo:
        fresh.append({"kind": "render", "row_id": "r3"})
    assert excinfo.value.tamper_at_seq == 0

    # fail-closed: append 失敗後もファイル内容は一切変更されていない。
    assert path.read_text(encoding="utf-8") == before


def test_ledger_verify_chain_ok_with_missing_final_newline_flag(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 P1] 最終行が改行未終端でも JSON としては
    完全にパース可能（＝write 中断ではない）なら、chain は正当として
    `ok=True` のまま `missing_final_newline=True` を追加情報として返す。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    # 末尾の改行だけを取り除く（JSON 本体は完全に残す）。
    full_content = path.read_text(encoding="utf-8")
    assert full_content.endswith("\n")
    path.write_text(full_content[:-1], encoding="utf-8")

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 2
    assert result.tamper_at_seq is None
    assert result.truncated_tail is False
    assert result.missing_final_newline is True


def test_ledger_append_self_heals_missing_final_newline_no_corruption(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 P1] append() は、既存台帳の末尾改行のみが
    欠けている場合（内容は完全な JSON）、`LedgerTruncatedTailError` を投げず、
    まず欠けた "\n" を書いてから通常どおり追記する。結果ファイルは `}{` の
    ような連結破損を起こさず、seq/prev_sha chain も連続する。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    full_content = path.read_text(encoding="utf-8")
    path.write_text(full_content[:-1], encoding="utf-8")  # 末尾改行のみ除去

    fresh = Ledger(path)
    e2 = fresh.append({"kind": "meter_call", "row_id": "r2"})
    assert e2.seq == 2

    final_content = path.read_text(encoding="utf-8")
    assert "}{" not in final_content  # 連結破損が起きていない
    lines = [ln for ln in final_content.splitlines() if ln.strip()]
    assert len(lines) == 3
    for i, ln in enumerate(lines):
        assert json.loads(ln)["seq"] == i

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 3
    assert result.tamper_at_seq is None


def test_ledger_two_instances_interleaved_append_no_sibling_seq(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 採用] 同一パスに対する 2 つの `Ledger`
    インスタンスが交互に append しても、seq/prev_sha は on-disk の真の tail
    から導出されるため、兄弟 `seq=0` の重複は起きず、chain は連続する。"""
    path = tmp_path / "ledger.jsonl"
    l1 = Ledger(path)
    l2 = Ledger(path)  # l1 の append をまだ知らない、独立にキャッシュ空の状態

    e1 = l1.append({"kind": "meter_call", "row_id": "r0"})
    e2 = l2.append({"kind": "meter_call", "row_id": "r1"})  # l2 のキャッシュは stale
    e3 = l1.append({"kind": "meter_call", "row_id": "r2"})
    e4 = l2.append({"kind": "meter_call", "row_id": "r3"})

    assert [e1.seq, e2.seq, e3.seq, e4.seq] == [0, 1, 2, 3]
    assert e2.prev_sha == e1.entry_sha
    assert e3.prev_sha == e2.entry_sha
    assert e4.prev_sha == e3.entry_sha

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 4
    assert result.tamper_at_seq is None


def test_check_leakage_pre_unseal_access_is_blocked() -> None:
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "render", "row_id": "holdout-1"},
        ),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=5)
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.control_excluded_count == 0


def test_check_leakage_unseal_none_blocks_any_holdout_access() -> None:
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "meter_call", "row_id": "holdout-1"},
        ),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_post_unseal_access_is_allowed(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = {
        "baseline_audit_sha": "1" * 64,
        "candidate_space_sha": "2" * 64,
        "selection_rule_sha": "3" * 64,
        "selected_candidate_sha": "4" * 64,
    }
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
        }
    )
    ledger.append({"kind": "meter_call", "row_id": "holdout-1"})
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=unseal.seq,
    )
    assert result.blocked is None


def test_check_leakage_non_holdout_row_never_blocks() -> None:
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "render", "row_id": "calibration-1"},
        ),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked is None


def test_check_leakage_ignores_non_render_meter_call_entries() -> None:
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "split_frozen", "row_id": "holdout-1"},
        ),
    ]
    result = Ledger.check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked is None


def test_check_leakage_control_row_ids_excluded_non_control_holdout_still_detected() -> None:
    """[IMPLEMENTATION_MAP_v1.md §2.7 control 共有契約] control 行
    (`control_row_ids`) は sweep truth を運ばないため、unseal 前に holdout
    split 上で参照されても leakage としない。一方、同じ entry 集合内の
    非 control な holdout 行は従来どおり検出される。"""
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "render", "row_id": "holdout-control-1"},
        ),
        LedgerEntry(
            seq=1,
            prev_sha="a" * 64,
            entry_sha="b" * 64,
            payload={"kind": "meter_call", "row_id": "holdout-control-1"},
        ),
        LedgerEntry(
            seq=2,
            prev_sha="b" * 64,
            entry_sha="c" * 64,
            payload={"kind": "meter_call", "row_id": "holdout-sweep-1"},
        ),
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


def test_ledger_tolerates_structurally_malformed_line_seq_only(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 P1 finding #3] `{"seq": 0}` のような
    parseable-but-malformed な行（JSON としてはパース可能だが `prev_sha`/
    `entry_sha`/`payload` を欠く）に対して、`Ledger.__init__`（内部で
    `_read_all()` を呼ぶ）は `KeyError` を送出してクラッシュしてはならない。
    構築は成功し、`verify_chain()` が chain-invalid（`ok=False`、malformed
    行の位置を指す `tamper_at_seq`）として報告し、`append()` は改竄パスと
    同様に `LedgerChainInvalidError` で拒否する。"""
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"seq":0}\n', encoding="utf-8")

    # 構築自体がクラッシュしない（旧実装は KeyError を送出していた）。
    ledger = Ledger(path)
    assert ledger.entries == ()
    assert len(ledger.malformed_lines) == 1
    assert ledger.malformed_lines[0].line_index == 0

    result = ledger.verify_chain()
    assert result.ok is False
    assert result.tamper_at_seq == 0
    assert "malformed" in result.detail

    with pytest.raises(LedgerChainInvalidError) as excinfo:
        ledger.append({"kind": "render", "row_id": "r0"})
    assert excinfo.value.tamper_at_seq == 0
    # fail-closed: append 失敗後もファイル内容は一切変更されていない。
    assert path.read_text(encoding="utf-8") == '{"seq":0}\n'


def test_check_leakage_control_row_pure_control_holdout_never_blocks() -> None:
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "render", "row_id": "holdout-control-1"},
        ),
        LedgerEntry(
            seq=1,
            prev_sha="a" * 64,
            entry_sha="b" * 64,
            payload={"kind": "meter_call", "row_id": "holdout-control-1"},
        ),
    ]
    result = Ledger.check_leakage(
        entries,
        holdout_row_ids=["holdout-control-1"],
        unseal_seq=None,
        control_row_ids=["holdout-control-1"],
    )
    assert result.blocked is None
    assert result.control_excluded_count == 2


def test_check_leakage_forged_unseal_integer_cannot_grant_access(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        {
            "kind": "selection_frozen",
            "baseline_audit_sha": "1" * 64,
            "candidate_space_sha": "2" * 64,
            "selection_rule_sha": "3" * 64,
            "selected_candidate_sha": "4" * 64,
        }
    )
    ledger.append({"kind": "meter_call", "row_id": "holdout-1"})
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=0,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_mismatched_unseal_commitments_fail_closed(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = {
        "baseline_audit_sha": "1" * 64,
        "candidate_space_sha": "2" * 64,
        "selection_rule_sha": "3" * 64,
        "selected_candidate_sha": "4" * 64,
    }
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selected_candidate_sha": "5" * 64,
            "selection_freeze_event_sha": frozen.entry_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
