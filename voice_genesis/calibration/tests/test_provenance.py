from __future__ import annotations

import hashlib
import json
from functools import lru_cache

import pytest

from voice_genesis.calibration.provenance import (
    CampaignIdentity,
    CodeIdentity,
    Ledger,
    LedgerChainInvalidError,
    LedgerEntry,
    LedgerTruncatedTailError,
    ProvenanceRecord,
    GENESIS_PREV_SHA,
    _entry_sha,
    provenance_record_to_dict,
)
from voice_genesis.calibration.fixtures.controls import negative_control_row_ids
from voice_genesis.calibration.fixtures.matrix import build_matrix
from voice_genesis.calibration.splitter import RealizedSplitMap, RowInput, realize_split
from voice_genesis.calibration.vocab import BlockedCode


@lru_cache(maxsize=1)
def _canonical_split_material():
    from voice_genesis.calibration.vocab import Split

    matrix = build_matrix()
    rows = tuple(
        RowInput(
            row_id=matrix_row.row_id,
            family=matrix_row.row.family,
            stratum={},
            truth_level=matrix_row.row.block,
            generator_impl=matrix_row.row.generator_impl,
            boundary_class=matrix_row.domain.value,
        )
        for matrix_row in matrix
    )
    negative_ids = set(negative_control_row_ids(matrix))
    truth_ids = {
        matrix_row.row_id for matrix_row in matrix if matrix_row.row.block == "TRUTH_CORE"
    }
    for nonce in range(64):
        secret = hashlib.sha256(f"canonical-split-test-{nonce}".encode("utf-8")).digest()
        realized = realize_split(rows, secret, ())
        holdout = {
            row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
        }
        if len(holdout & negative_ids) >= 2 and len(holdout & truth_ids) >= 3:
            return rows, secret, realized
    raise AssertionError("could not construct canonical test split with required holdout classes")


def _requested_row_map(requested_holdout_ids):
    rows, secret, realized = _canonical_split_material()
    matrix = build_matrix()
    matrix_by_id = {row.row_id: row for row in matrix}
    negative_ids = set(negative_control_row_ids(matrix))
    holdout = sorted(
        row_id
        for row_id, split in realized.assignment.items()
        if split.value == "HOLDOUT"
    )
    holdout_negative = [row_id for row_id in holdout if row_id in negative_ids]
    holdout_truth = [
        row_id
        for row_id in holdout
        if matrix_by_id[row_id].row.block == "TRUTH_CORE" and row_id not in negative_ids
    ]
    holdout_general = [row_id for row_id in holdout if row_id not in negative_ids]

    mapping = {}
    used = set()
    for requested in dict.fromkeys(requested_holdout_ids):
        if requested in holdout:
            chosen = requested
        elif requested in negative_ids:
            pool = holdout_negative
            chosen = next(row_id for row_id in pool if row_id not in used)
        elif requested in matrix_by_id and matrix_by_id[requested].row.block == "TRUTH_CORE":
            pool = holdout_truth
            chosen = next(row_id for row_id in pool if row_id not in used)
        else:
            pool = holdout_general
            chosen = next(row_id for row_id in pool if row_id not in used)
        mapping[requested] = chosen
        used.add(chosen)
    return mapping, tuple(holdout), rows, secret, realized


def _remap_payload(value, row_map, prior_sha_map):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "row_id" and isinstance(item, str) and item in row_map:
                out[key] = row_map[item]
            else:
                out[key] = _remap_payload(item, row_map, prior_sha_map)
        return out
    if isinstance(value, list):
        return [_remap_payload(item, row_map, prior_sha_map) for item in value]
    if isinstance(value, tuple):
        return tuple(_remap_payload(item, row_map, prior_sha_map) for item in value)
    if isinstance(value, str) and value in prior_sha_map:
        return prior_sha_map[value]
    return value


def _authenticated_entries(entries, row_map, realized, secret):
    rebuilt = []
    split_payload = {
        "kind": "split_frozen",
        "realized_split_map_hash": realized.realized_sha,
        "seal_commitment": hashlib.sha256(secret).hexdigest(),
    }
    split_sha = _entry_sha(0, GENESIS_PREV_SHA, split_payload)
    rebuilt.append(
        LedgerEntry(
            seq=0,
            prev_sha=GENESIS_PREV_SHA,
            entry_sha=split_sha,
            payload=split_payload,
        )
    )
    prior_sha_map = {}
    prev_sha = split_sha
    for old in entries:
        payload = _remap_payload(dict(old.payload), row_map, prior_sha_map)
        seq = len(rebuilt)
        entry_sha = _entry_sha(seq, prev_sha, payload)
        rebuilt.append(
            LedgerEntry(seq=seq, prev_sha=prev_sha, entry_sha=entry_sha, payload=payload)
        )
        prior_sha_map[old.entry_sha] = entry_sha
        prev_sha = entry_sha
    return tuple(rebuilt)


def _check_leakage(*args, **kwargs):
    if "holdout_row_ids" in kwargs:
        requested_holdout = tuple(kwargs["holdout_row_ids"])
    elif len(args) >= 2:
        requested_holdout = tuple(args[1])
    else:
        raise AssertionError("holdout_row_ids required by test wrapper")

    row_map, full_holdout, rows, secret, realized = _requested_row_map(requested_holdout)
    mutable_args = list(args)
    entries = kwargs.get("ledger_entries", mutable_args[0] if mutable_args else ())
    rebuilt = _authenticated_entries(entries, row_map, realized, secret)
    if mutable_args:
        mutable_args[0] = rebuilt
    else:
        kwargs["ledger_entries"] = rebuilt

    if len(mutable_args) >= 2:
        mutable_args[1] = full_holdout
    else:
        kwargs["holdout_row_ids"] = full_holdout

    if len(mutable_args) >= 3 and isinstance(mutable_args[2], int):
        mutable_args[2] += 1
    elif isinstance(kwargs.get("unseal_seq"), int):
        kwargs["unseal_seq"] += 1

    if "control_row_ids" in kwargs:
        kwargs["control_row_ids"] = tuple(
            row_map.get(row_id, row_id) for row_id in kwargs["control_row_ids"]
        )
    kwargs.setdefault("realized_split_map", realized)
    kwargs.setdefault("split_verification_rows", rows)
    kwargs.setdefault("split_secret", secret)
    return Ledger.check_leakage(*mutable_args, **kwargs)


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

    assert len(ledger.entries) == 2
    assert all(isinstance(e, LedgerEntry) for e in ledger.entries)
    assert ledger.malformed_lines == ()

    result = _check_leakage(ledger.entries, holdout_row_ids=["holdout-x"], unseal_seq=None)
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
    tampered["prev_sha"] = "f" * 64
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

    full_content = path.read_text(encoding="utf-8")
    lines = full_content.splitlines()
    cut_point = len(lines[0]) + 1 + len(lines[1]) // 2
    truncated_content = full_content[:cut_point]
    path.write_text(truncated_content, encoding="utf-8")

    fresh = Ledger(path)
    result = fresh.verify_chain()
    assert result.truncated_tail is True
    assert result.entries_verified == 1
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
    first_line = full_content.splitlines()[0]
    path.write_text(first_line[: len(first_line) // 2], encoding="utf-8")

    result = Ledger(path).verify_chain()
    assert result.truncated_tail is True
    assert result.entries_verified == 0
    assert result.ok is False


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

    assert path.read_text(encoding="utf-8") == before


def test_ledger_verify_chain_ok_with_missing_final_newline_flag(tmp_path) -> None:
    """[Codex レビュー 2026-09-01 P1] 最終行が改行未終端でも JSON としては
    完全にパース可能（＝write 中断ではない）なら、chain は正当として
    `ok=True` のまま `missing_final_newline=True` を追加情報として返す。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

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
    path.write_text(full_content[:-1], encoding="utf-8")

    fresh = Ledger(path)
    e2 = fresh.append({"kind": "meter_call", "row_id": "r2"})
    assert e2.seq == 2

    final_content = path.read_text(encoding="utf-8")
    assert "}{" not in final_content
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
    l2 = Ledger(path)

    e1 = l1.append({"kind": "meter_call", "row_id": "r0"})
    e2 = l2.append({"kind": "meter_call", "row_id": "r1"})
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


def test_ledger_append_cost_independent_of_ledger_size(tmp_path, monkeypatch) -> None:
    """[UNDERSPEC-CAL-D84] regression: `Ledger.append()` must cost O(1) in the
    number of existing entries, not O(n). Before this fix, `append()`
    re-verified the **entire** on-disk chain (`_verify_chain_prefix` over
    every existing line) and re-parsed the whole file on every call — O(n)
    per append, O(n^2) per campaign (production incident: RUN10-CAL-
    20260903-9bcbbf86 c2-baseline, n=11,220 -> 912 ms/append, projected
    ~112 h CPU to finish; see `UNDERSPEC-CAL-D84` in README.md).

    This builds ledgers of two very different sizes purely via
    `Ledger.append()` (itself only tractable at n=2000 if append() is
    already O(1) — a regression back to O(n) would make *this test*
    prohibitively slow) and asserts that the number of raw lines
    `_verify_chain_prefix` is asked to re-verify for the *next* append does
    not grow with the ledger's existing size — it must stay `0` (nothing
    written since this instance's own watermark), regardless of `n_prior`.
    """
    import voice_genesis.calibration.provenance as provenance_mod

    def suffix_len_for_next_append(n_prior: int) -> int:
        path = tmp_path / f"ledger_{n_prior}.jsonl"
        ledger = Ledger(path)
        for i in range(n_prior):
            ledger.append({"kind": "meter_call", "row_id": f"r{i}"})

        seen_lengths: list[int] = []
        real_verify_chain_prefix = provenance_mod._verify_chain_prefix

        def counting_verify_chain_prefix(raw_lines, *args, **kwargs):
            seen_lengths.append(len(raw_lines))
            return real_verify_chain_prefix(raw_lines, *args, **kwargs)

        monkeypatch.setattr(provenance_mod, "_verify_chain_prefix", counting_verify_chain_prefix)
        ledger.append({"kind": "meter_call", "row_id": "final"})
        monkeypatch.undo()

        assert len(seen_lengths) == 1, "append() must call _verify_chain_prefix exactly once"
        return seen_lengths[0]

    small_n_suffix_len = suffix_len_for_next_append(5)
    large_n_suffix_len = suffix_len_for_next_append(300)

    assert small_n_suffix_len == 0
    assert large_n_suffix_len == 0


def test_ledger_append_refuses_when_rolled_back_to_valid_shorter_prefix(tmp_path) -> None:
    """[#345 指摘③] 既に open 済みの `Ledger`（watermark = 3 エントリ分）が、
    その後 disk 上でその watermark より短い、それ自体は chain-valid な
    prefix（先頭 2 エントリのみ）へロールバックされた（restore/sync
    rollback を模する）のを観測した場合、watermark を再同期せず
    `LedgerChainInvalidError` で fail-closed する。旧実装は watermark を 0
    にリセットしてロールバック後の内容を「フォールバック全体検証 OK」として
    受理し、`append()` 末尾で `self._entries` にそれを単純 extend していた
    ため、in-memory キャッシュが `[0,1,2,0,1]` のように disk（`[0,1]`）と
    乖離する状態を作っていた（重複 seq を含む壊れたキャッシュ）。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})
    ledger.append({"kind": "meter_call", "row_id": "r2"})
    assert [e.seq for e in ledger.entries] == [0, 1, 2]

    full_content = path.read_text(encoding="utf-8")
    lines = [ln for ln in full_content.splitlines() if ln.strip()]
    rolled_back_content = lines[0] + "\n" + lines[1] + "\n"
    path.write_text(rolled_back_content, encoding="utf-8")

    # sanity: the rolled-back file is a chain-valid 2-entry ledger on its own
    # (this is the "VALID shorter prefix" case the finding describes — not a
    # truncated/malformed tail).
    fresh_view = Ledger(path)
    assert fresh_view.verify_chain().ok is True
    assert [e.seq for e in fresh_view.entries] == [0, 1]

    with pytest.raises(LedgerChainInvalidError):
        ledger.append({"kind": "meter_call", "row_id": "r3"})

    # fail-closed: nothing written to disk, stale instance's in-memory state
    # untouched (not silently re-synced onto the shortened chain).
    assert path.read_text(encoding="utf-8") == rolled_back_content
    assert [e.seq for e in ledger.entries] == [0, 1, 2]


def test_ledger_append_refuses_when_watermark_entry_same_length_substituted(
    tmp_path,
) -> None:
    """[#345 指摘③] watermark 末尾エントリ（size は不変）が、同じバイト長の
    別内容へ差し替えられた場合も `LedgerChainInvalidError` で fail-closed
    する。ファイルサイズが watermark と一致するため suffix は空文字列となり
    `_verify_chain_prefix` は 0 行を検証して素通りしてしまう —
    watermark 直下 1 行の sha256 再照合がこのケースを捕まえる。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    full_content = path.read_text(encoding="utf-8")
    lines = [ln for ln in full_content.splitlines() if ln.strip()]
    tampered_line = lines[-1].replace('"r1"', '"rX"')
    assert tampered_line != lines[-1]
    assert len(tampered_line.encode("utf-8")) == len(lines[-1].encode("utf-8"))
    lines[-1] = tampered_line
    tampered_content = "\n".join(lines) + "\n"
    assert len(tampered_content.encode("utf-8")) == len(full_content.encode("utf-8"))
    path.write_text(tampered_content, encoding="utf-8")

    with pytest.raises(LedgerChainInvalidError):
        ledger.append({"kind": "meter_call", "row_id": "r2"})

    # fail-closed: nothing written, file left exactly as tampered.
    assert path.read_text(encoding="utf-8") == tampered_content
    assert [e.seq for e in ledger.entries] == [0, 1]


def test_ledger_append_succeeds_after_one_trailing_blank_line(tmp_path) -> None:
    """[#345 指摘③ 追補] chain-valid な台帳の末尾に空行が 1 行追加されても、
    `append()` は偽の `LedgerChainInvalidError` を送出せず成功し、
    `verify_chain()` も `ok=True` のままである。旧実装は watermark の
    バイト範囲を `_v_bytes`（ファイル全体長。末尾空行の分だけ実際の最終
    エントリの終端より後ろにずれる）から逆算していたため、O(1) fingerprint
    再照合が誤ったバイト範囲を読んで正当な台帳を tamper 扱いしていた。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    full_content = path.read_text(encoding="utf-8")
    assert full_content.endswith("\n")
    path.write_text(full_content + "\n", encoding="utf-8")

    pre_append_check = Ledger(path).verify_chain()
    assert pre_append_check.ok is True
    assert pre_append_check.entries_verified == 2

    fresh = Ledger(path)
    e2 = fresh.append({"kind": "meter_call", "row_id": "r2"})
    assert e2.seq == 2
    assert e2.prev_sha == json.loads(full_content.splitlines()[-1])["entry_sha"]

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 3
    assert result.tamper_at_seq is None


def test_ledger_append_succeeds_after_two_trailing_blank_lines(tmp_path) -> None:
    """[#345 指摘③ 追補] 末尾空行が 2 行連続していても同様に `append()` が
    成功し `verify_chain()` も `ok=True` のままであることを確認する
    （1 行分の空行だけを特別扱いしていないことの回帰確認）。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    full_content = path.read_text(encoding="utf-8")
    assert full_content.endswith("\n")
    path.write_text(full_content + "\n\n", encoding="utf-8")

    pre_append_check = Ledger(path).verify_chain()
    assert pre_append_check.ok is True
    assert pre_append_check.entries_verified == 2

    fresh = Ledger(path)
    e2 = fresh.append({"kind": "meter_call", "row_id": "r2"})
    assert e2.seq == 2

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 3
    assert result.tamper_at_seq is None


def test_ledger_append_succeeds_on_blank_only_file(tmp_path) -> None:
    """`[UNDERSPEC-CAL-D88]`(b): a file holding only blank lines (no JSON
    entries at all -- distinct from the trailing-blank-line-*after*-valid-
    entries cases above) is a validated chain of zero entries (`chain.ok`
    True, `entries == []` -- the "genesis watermark", `_v_seq == -1` /
    `_v_last_line_sha256 is None` / `_v_last_line_len == 0`). Before this
    fix, the O(1) last-line fingerprint re-check in `append()`'s `stat_
    unchanged` branch always compared `hashlib.sha256(b"").hexdigest()`
    (there is no prior JSON line to re-read -- `last_len == 0`) against
    `self._v_last_line_sha256 is None`, which can never match, so `append()`
    raised `LedgerChainInvalidError` -- a false failure -- for the very
    first append to a blank-only file."""
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n\n", encoding="utf-8")

    pre_append_check = Ledger(path).verify_chain()
    assert pre_append_check.ok is True
    assert pre_append_check.entries_verified == 0

    ledger = Ledger(path)
    entry = ledger.append({"kind": "meter_call", "row_id": "r0"})
    assert entry.seq == 0
    assert entry.prev_sha == GENESIS_PREV_SHA

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 1
    assert result.tamper_at_seq is None


def test_ledger_append_succeeds_on_completely_empty_file(tmp_path) -> None:
    """Companion to the blank-only-file test above: a genuinely 0-byte file
    (`v_bytes == 0`, not the `stat_unchanged` fingerprint path D88(b)
    touches) was -- and remains -- unaffected by that fix."""
    path = tmp_path / "ledger.jsonl"
    path.write_text("", encoding="utf-8")

    ledger = Ledger(path)
    entry = ledger.append({"kind": "meter_call", "row_id": "r0"})
    assert entry.seq == 0
    assert entry.prev_sha == GENESIS_PREV_SHA

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 1


def test_ledger_append_g1_detects_early_entry_same_length_tamper(tmp_path) -> None:
    """[#345 指摘③ G1, Codex finding P1] 既に open 済みの `Ledger`
    （watermark = 3 エントリ分）に対し、watermark 末尾（seq=2）ではなく
    **より前** の seq=0 エントリが同じバイト長の別内容へ差し替えられた場合。
    旧実装（watermark 末尾 1 行だけの fingerprint 再照合 + suffix のみの
    chain 検証）はサイズも末尾行も不変なため素通りしていた。stat の
    `mtime_ns` は on-disk への書き込みで必ず変化するため、G1 の安価な変更
    検出器がこれを捉え、次の `append()` は on-disk ledger 全体をフル
    再検証してから `LedgerChainInvalidError` で fail-closed する（書き込み
    なし・`self._entries` も不変）。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})
    ledger.append({"kind": "meter_call", "row_id": "r2"})
    assert [e.seq for e in ledger.entries] == [0, 1, 2]

    full_content = path.read_text(encoding="utf-8")
    lines = [ln for ln in full_content.splitlines() if ln.strip()]
    tampered_line = lines[0].replace('"r0"', '"rX"')
    assert tampered_line != lines[0]
    assert len(tampered_line.encode("utf-8")) == len(lines[0].encode("utf-8"))
    lines[0] = tampered_line
    tampered_content = "\n".join(lines) + "\n"
    assert len(tampered_content.encode("utf-8")) == len(full_content.encode("utf-8"))
    path.write_text(tampered_content, encoding="utf-8")

    with pytest.raises(LedgerChainInvalidError) as excinfo:
        ledger.append({"kind": "meter_call", "row_id": "r3"})
    assert excinfo.value.tamper_at_seq == 0

    # fail-closed: nothing written, tampered content untouched, in-memory
    # cache not extended with the bogus tail.
    assert path.read_text(encoding="utf-8") == tampered_content
    assert [e.seq for e in ledger.entries] == [0, 1, 2]


def test_ledger_append_g2_catches_transition_kind_even_with_spoofed_stat(
    tmp_path, monkeypatch
) -> None:
    """[#345 指摘③ G1/G2, Codex finding P1 test (b)] stat（ino/mtime_ns/
    size）が（何らかの理由で）watermark 確立時と見分けが付かないよう
    monkeypatch で偽装された状態で、watermark より前の seq=0 エントリが
    同じバイト長で改ざんされている最悪ケースをシミュレートする。

    - G2: 追記しようとしている payload の `kind` がフェーズ遷移/terminal
      summary（`_TRANSITION_EVENT_KINDS`。ここでは `campaign_closed`）なら、
      stat が unchanged に見えても無条件にフル再検証へ回るため、依然として
      `LedgerChainInvalidError` で fail-closed する — G1 が欺かれていても
      G2 は独立して機能する。
    - 残余露出（意図された仕様）: 続けて `meter_call`（遷移 kind ではない）
      を追記すると、stat が unchanged に見える限り G1 は発火せず、この
      1 回の append は「成功」してしまう。ただし、プロセス再起動後の
      `Ledger(path)`（構築時は常にフル検証）が改竄を確実に検出することを
      末尾で確認し、露出が一時的・境界付きであることを示す。"""
    import voice_genesis.calibration.provenance as provenance_mod

    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})
    ledger.append({"kind": "meter_call", "row_id": "r2"})

    spoofed_ino = ledger._v_ino
    spoofed_mtime_ns = ledger._v_mtime_ns
    spoofed_size = ledger._v_stat_size

    full_content = path.read_text(encoding="utf-8")
    lines = [ln for ln in full_content.splitlines() if ln.strip()]
    tampered_line = lines[0].replace('"r0"', '"rX"')
    assert tampered_line != lines[0]
    assert len(tampered_line.encode("utf-8")) == len(lines[0].encode("utf-8"))
    lines[0] = tampered_line
    tampered_content = "\n".join(lines) + "\n"
    assert len(tampered_content.encode("utf-8")) == len(full_content.encode("utf-8"))
    path.write_text(tampered_content, encoding="utf-8")

    class _FakeStat:
        st_ino = spoofed_ino
        st_mtime_ns = spoofed_mtime_ns
        st_size = spoofed_size

    def fake_fstat(fd):
        return _FakeStat()

    with monkeypatch.context() as m:
        m.setattr(provenance_mod.os, "fstat", fake_fstat)

        # G2 still fires for a transition-kind event even though the cheap
        # G1 stat check is defeated.
        with pytest.raises(LedgerChainInvalidError) as excinfo:
            ledger.append({"kind": "campaign_closed", "row_id": "closer"})
        assert excinfo.value.tamper_at_seq == 0
        assert path.read_text(encoding="utf-8") == tampered_content
        assert [e.seq for e in ledger.entries] == [0, 1, 2]

        # documented residual exposure: a non-transition append is NOT
        # caught while the stat check is defeated (accepted per design
        # ruling; bounded by the next transition/restart, see below).
        entry = ledger.append({"kind": "meter_call", "row_id": "r3"})
        assert entry.seq == 3

    # the residual exposure is bounded, not permanent: a fresh instance
    # (real fstat, full verification at construction) unconditionally
    # detects the still-present tamper.
    result = Ledger(path).verify_chain()
    assert result.ok is False
    assert result.tamper_at_seq == 0


def test_ledger_append_updates_stat_bookkeeping_after_each_write(tmp_path) -> None:
    """[#345 指摘③ G1] `append()` は write→flush→`fsync` の直後に再 `fstat`
    し、`_v_ino`/`_v_mtime_ns`/`_v_stat_size` を実際の on-disk 値へ更新する
    （次回 append() の安価な変更検出器 G1 の baseline）。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)

    ledger.append({"kind": "meter_call", "row_id": "r0"})
    st1 = path.stat()
    assert ledger._v_ino == st1.st_ino
    assert ledger._v_mtime_ns == st1.st_mtime_ns
    assert ledger._v_stat_size == st1.st_size

    ledger.append({"kind": "meter_call", "row_id": "r1"})
    st2 = path.stat()
    assert ledger._v_ino == st2.st_ino
    assert ledger._v_mtime_ns == st2.st_mtime_ns
    assert ledger._v_stat_size == st2.st_size


def test_ledger_append_transition_kind_forces_full_verify_and_succeeds_when_valid(
    tmp_path,
) -> None:
    """[#345 指摘③ G2] フェーズ遷移/terminal summary kind
    （`_TRANSITION_EVENT_KINDS`）を追記する際は、stat が unchanged でも
    無条件にフル chain 再検証を行う。台帳が正当であれば通常どおり成功する
    （G2 が偽陽性で正当な遷移を拒否しないことの確認。Codex finding P1
    test (d)）。"""
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append({"kind": "meter_call", "row_id": "r0"})
    ledger.append({"kind": "meter_call", "row_id": "r1"})

    entry = ledger.append(
        {"kind": "stage_summary", "stage": "c2", "parent_cpu_seconds": 1.0}
    )
    assert entry.seq == 2

    result = Ledger(path).verify_chain()
    assert result.ok is True
    assert result.entries_verified == 3
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
    result = _check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=5)
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
    result = _check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_post_unseal_access_is_allowed(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = {
        "baseline_audit_sha": ledger.append({"kind": "baseline_audit", "artifact_sha": "a" * 64}).entry_sha,
        "candidate_space_sha": ledger.append({"kind": "candidate_space", "artifact_sha": "b" * 64}).entry_sha,
        "selection_rule_sha": ledger.append({"kind": "selection_rule", "artifact_sha": "c" * 64}).entry_sha,
        "selected_candidate_sha": ledger.append({"kind": "selected_candidate", "artifact_sha": "d" * 64, "candidate_id": "candidate-test"}).entry_sha,
    }
    gate3 = ledger.append(
        {
            "kind": "gate3_accepted",
            "approval_content_sha256": "f" * 64,
            "seal_protection_level_accepted": True,
            "approver": "test-approver",
            "approved_at_utc": "2026-09-02T00:00:00Z",
        }
    )
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    unseal = ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selection_freeze_event_sha": frozen.entry_sha,
            "gate3_accepted_sha": gate3.entry_sha,
        }
    )
    ledger.append({"kind": "meter_call", "row_id": "holdout-1"})
    result = _check_leakage(
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
    result = _check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked is None


def test_check_leakage_ignores_non_render_meter_call_entries() -> None:
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "split_metadata", "row_id": "holdout-1"},
        ),
    ]
    result = _check_leakage(entries, holdout_row_ids=["holdout-1"], unseal_seq=None)
    assert result.blocked is None


def test_check_leakage_control_row_ids_excluded_non_control_holdout_still_detected() -> None:
    """Only a frozen negative-control identity receives the exemption; a second
    truth-bearing holdout row in the same entry set remains blocked."""
    control_id = sorted(negative_control_row_ids(build_matrix()))[0]
    truth_row = next(
        mr
        for mr in build_matrix()
        if mr.row.block == "TRUTH_CORE" and mr.row.control_class is None
    )
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "render", "row_id": control_id},
        ),
        LedgerEntry(
            seq=1,
            prev_sha="a" * 64,
            entry_sha="b" * 64,
            payload={"kind": "meter_call", "row_id": control_id},
        ),
        LedgerEntry(
            seq=2,
            prev_sha="b" * 64,
            entry_sha="c" * 64,
            payload={"kind": "meter_call", "row_id": truth_row.row_id},
        ),
    ]
    result = _check_leakage(
        entries,
        holdout_row_ids=[control_id, truth_row.row_id],
        unseal_seq=None,
        control_row_ids=[control_id],
    )
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
    assert path.read_text(encoding="utf-8") == '{"seq":0}\n'


def test_check_leakage_control_row_pure_control_holdout_never_blocks() -> None:
    control_id = sorted(negative_control_row_ids(build_matrix()))[0]
    entries = [
        LedgerEntry(
            seq=0,
            prev_sha="0" * 64,
            entry_sha="a" * 64,
            payload={"kind": "render", "row_id": control_id},
        ),
        LedgerEntry(
            seq=1,
            prev_sha="a" * 64,
            entry_sha="b" * 64,
            payload={"kind": "meter_call", "row_id": control_id},
        ),
    ]
    result = _check_leakage(
        entries,
        holdout_row_ids=[control_id],
        unseal_seq=None,
        control_row_ids=[control_id],
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
    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=0,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_mismatched_unseal_commitments_fail_closed(tmp_path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    commitments = {
        "baseline_audit_sha": ledger.append({"kind": "baseline_audit", "artifact_sha": "a" * 64}).entry_sha,
        "candidate_space_sha": ledger.append({"kind": "candidate_space", "artifact_sha": "b" * 64}).entry_sha,
        "selection_rule_sha": ledger.append({"kind": "selection_rule", "artifact_sha": "c" * 64}).entry_sha,
        "selected_candidate_sha": ledger.append({"kind": "selected_candidate", "artifact_sha": "d" * 64, "candidate_id": "candidate-test"}).entry_sha,
    }
    frozen = ledger.append({"kind": "selection_frozen", **commitments})
    alternate_selected = ledger.append({"kind": "selected_candidate", "artifact_sha": "d" * 64, "candidate_id": "candidate-test"})
    ledger.append(
        {
            "kind": "holdout_unseal",
            **commitments,
            "selected_candidate_sha": alternate_selected.entry_sha,
            "selection_freeze_event_sha": frozen.entry_sha,
        }
    )
    ledger.append({"kind": "render", "row_id": "holdout-1"})
    result = _check_leakage(
        ledger.entries,
        holdout_row_ids=["holdout-1"],
        unseal_seq=None,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_caller_cannot_forge_truth_row_as_control() -> None:
    truth_row = next(
        mr for mr in build_matrix() if mr.row.block == "TRUTH_CORE" and mr.row.control_class is None
    )
    entry = LedgerEntry(
        seq=0,
        prev_sha="0" * 64,
        entry_sha="a" * 64,
        payload={"kind": "render", "row_id": truth_row.row_id},
    )

    result = _check_leakage(
        [entry],
        holdout_row_ids=[truth_row.row_id],
        unseal_seq=None,
        control_row_ids=[truth_row.row_id],
    )

    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
    assert result.control_excluded_count == 0


def _append_split_frozen(ledger, realized, secret):
    return ledger.append(
        {
            "kind": "split_frozen",
            "realized_split_map_hash": realized.realized_sha,
            "seal_commitment": hashlib.sha256(secret).hexdigest(),
        }
    )


def test_check_leakage_rejects_incomplete_declared_holdout_set(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    ledger.append({"kind": "render", "row_id": holdout[-1]})
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout[:-1],
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_requires_verified_realized_split_map() -> None:
    result = Ledger.check_leakage([], holdout_row_ids=[], unseal_seq=None)
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_rejects_tampered_realized_split_map(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    tampered_assignment = dict(realized.assignment)
    tampered_assignment[holdout[0]] = Split.CALIBRATION
    tampered = RealizedSplitMap(
        stratum_factor_names=realized.stratum_factor_names,
        assignment=tampered_assignment,
        swaps=realized.swaps,
        realized_sha=realized.realized_sha,
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=tampered,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_rejects_self_consistent_reduced_matrix_split(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    full_matrix = build_matrix()
    family = full_matrix[0].row.family
    subset_matrix = [row for row in full_matrix if row.row.family == family]
    subset_rows = tuple(
        RowInput(
            row_id=row.row_id,
            family=row.row.family,
            stratum={},
            truth_level=row.row.block,
            generator_impl=row.row.generator_impl,
            boundary_class=row.domain.value,
        )
        for row in subset_matrix
    )
    secret = hashlib.sha256(b"reduced-self-consistent-split").digest()
    realized = realize_split(subset_rows, secret, ())
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    assert holdout
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    ledger.append({"kind": "render", "row_id": holdout[0]})
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=subset_rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_rejects_split_secret_commitment_mismatch(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        {
            "kind": "split_frozen",
            "realized_split_map_hash": realized.realized_sha,
            "seal_commitment": hashlib.sha256(b"different-secret").hexdigest(),
        }
    )
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE


def test_check_leakage_accepts_full_canonical_split_with_matching_commitment(tmp_path) -> None:
    from voice_genesis.calibration.vocab import Split

    rows, secret, realized = _canonical_split_material()
    holdout = sorted(
        row_id for row_id, split in realized.assignment.items() if split == Split.HOLDOUT
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _append_split_frozen(ledger, realized, secret)
    result = Ledger.check_leakage(
        ledger.entries,
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=rows,
        split_secret=secret,
    )
    assert result.blocked is None



def test_check_leakage_rejects_forged_split_row_attributes_even_with_matching_freeze() -> None:
    """A self-consistent split from forged split-driving row metadata is not authoritative."""
    from voice_genesis.calibration.vocab import Split

    canonical_rows, secret, canonical_realized = _canonical_split_material()
    target = canonical_rows[0]
    forged_variants = (
        {"stratum": {"forged": "value"}},
        {"truth_level": "FORGED_TRUTH"},
        {"generator_impl": "FORGED_IMPL"},
        {"boundary_class": "FORGED_BOUNDARY"},
    )

    for overrides in forged_variants:
        forged_rows = list(canonical_rows)
        values = {
            "row_id": target.row_id,
            "family": target.family,
            "stratum": dict(target.stratum),
            "truth_level": target.truth_level,
            "generator_impl": target.generator_impl,
            "boundary_class": target.boundary_class,
        }
        values.update(overrides)
        forged_rows[0] = RowInput(**values)
        forged_realized = realize_split(
            forged_rows, secret, canonical_realized.stratum_factor_names
        )
        split_payload = {
            "kind": "split_frozen",
            "realized_split_map_hash": forged_realized.realized_sha,
            "seal_commitment": hashlib.sha256(secret).hexdigest(),
        }
        split_entry = LedgerEntry(
            seq=0,
            prev_sha=GENESIS_PREV_SHA,
            entry_sha=_entry_sha(0, GENESIS_PREV_SHA, split_payload),
            payload=split_payload,
        )
        holdout = [
            row_id
            for row_id, split in forged_realized.assignment.items()
            if split == Split.HOLDOUT
        ]
        result = Ledger.check_leakage(
            [split_entry],
            holdout_row_ids=holdout,
            unseal_seq=None,
            realized_split_map=forged_realized,
            split_verification_rows=forged_rows,
            split_secret=secret,
        )
        assert result.blocked == BlockedCode.BLOCKED_LEAKAGE, overrides


def test_check_leakage_authenticates_nonempty_frozen_stratification() -> None:
    from voice_genesis.calibration.vocab import Split

    matrix = build_matrix()
    rows = tuple(
        RowInput(
            row_id=matrix_row.row_id,
            family=matrix_row.row.family,
            stratum={"block": matrix_row.row.block},
            truth_level=matrix_row.row.block,
            generator_impl=matrix_row.row.generator_impl,
            boundary_class=matrix_row.domain.value,
        )
        for matrix_row in matrix
    )
    secret = hashlib.sha256(b"canonical-stratified-split").digest()
    realized = realize_split(rows, secret, ("block",))
    holdout = tuple(
        row_id
        for row_id, split in realized.assignment.items()
        if split == Split.HOLDOUT
    )
    split_payload = {
        "kind": "split_frozen",
        "realized_split_map_hash": realized.realized_sha,
        "seal_commitment": hashlib.sha256(secret).hexdigest(),
    }
    split_entry = LedgerEntry(
        seq=0,
        prev_sha=GENESIS_PREV_SHA,
        entry_sha=_entry_sha(0, GENESIS_PREV_SHA, split_payload),
        payload=split_payload,
    )

    result = Ledger.check_leakage(
        (split_entry,),
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=realized,
        split_verification_rows=rows,
        split_secret=secret,
    )

    assert result.blocked is None


def test_check_leakage_rejects_forged_value_for_frozen_stratum_factor() -> None:
    from voice_genesis.calibration.vocab import Split

    matrix = build_matrix()
    rows = [
        RowInput(
            row_id=matrix_row.row_id,
            family=matrix_row.row.family,
            stratum={"block": matrix_row.row.block},
            truth_level=matrix_row.row.block,
            generator_impl=matrix_row.row.generator_impl,
            boundary_class=matrix_row.domain.value,
        )
        for matrix_row in matrix
    ]
    target = rows[0]
    rows[0] = RowInput(
        row_id=target.row_id,
        family=target.family,
        stratum={"block": "FORGED_BLOCK"},
        truth_level=target.truth_level,
        generator_impl=target.generator_impl,
        boundary_class=target.boundary_class,
    )
    secret = hashlib.sha256(b"forged-stratified-split").digest()
    forged_realized = realize_split(rows, secret, ("block",))
    holdout = tuple(
        row_id
        for row_id, split in forged_realized.assignment.items()
        if split == Split.HOLDOUT
    )
    split_payload = {
        "kind": "split_frozen",
        "realized_split_map_hash": forged_realized.realized_sha,
        "seal_commitment": hashlib.sha256(secret).hexdigest(),
    }
    split_entry = LedgerEntry(
        seq=0,
        prev_sha=GENESIS_PREV_SHA,
        entry_sha=_entry_sha(0, GENESIS_PREV_SHA, split_payload),
        payload=split_payload,
    )

    result = Ledger.check_leakage(
        (split_entry,),
        holdout_row_ids=holdout,
        unseal_seq=None,
        realized_split_map=forged_realized,
        split_verification_rows=tuple(rows),
        split_secret=secret,
    )

    assert result.blocked == BlockedCode.BLOCKED_LEAKAGE
