"""`tools/archive_aborted_ledger.py` の原子的置換 + 中断回復の検証
（設計正本 `DESIGN_VG_METER_CAL_DEBT_v1.1.md` §V4）。

各段階間（staging 書込後 / 検証後 / 公開後 / 原本削除前）で中断した状態を
直接構成し、`ensure_archived()` の再実行が回復規則どおりに完結すること・
どの中断状態でも「原本 or 検証済み公開物」のどちらかが必ず存在すること
（正本喪失ゼロ）を固定する。"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import threading
from pathlib import Path

import pytest

from voice_genesis.calibration.provenance import (
    Ledger,
    LedgerArchivedError,
    LedgerChainInvalidError,
)
from voice_genesis.calibration.tools import archive_aborted_ledger as archive


def _build_tiny_ledger(campaign_dir: Path, n: int = 5) -> bytes:
    """有効な chain を持つ小さな ledger.jsonl を作り、その生バイト列を返す。"""
    campaign_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    ledger = Ledger(ledger_path)
    for i in range(n):
        ledger.append({"kind": "test_event", "i": i})
    return ledger_path.read_bytes()


def _has_authoritative_copy(campaign_dir: Path) -> bool:
    """「原本 or 検証済み公開物のどちらかが必ず存在する」という不変条件を
    直接検査するヘルパー。公開物がある場合は実際に検証を通す。"""
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    if ledger_path.is_file():
        return True
    if gz_path.is_file() and sidecar_path.is_file():
        try:
            archive._verify_gz_sidecar_pair(gz_path, sidecar_path)
        except archive.ArchiveError:
            return False
        return True
    return False


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_archives_fresh_ledger_and_matches_zcat_sha256sum(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    result = archive.ensure_archived(campaign_dir)

    assert result.action == "archived"
    assert result.sha256 == expected_sha
    assert not (campaign_dir / archive.LEDGER_FILENAME).exists()
    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    assert gz_path.is_file()
    assert sidecar_path.is_file()

    # `zcat ledger.jsonl.gz | sha256sum` 相当の機械検証。
    decompressed = gzip.decompress(gz_path.read_bytes())
    assert decompressed == original_bytes
    assert hashlib.sha256(decompressed).hexdigest() == expected_sha
    sidecar_sha, sidecar_name = sidecar_path.read_text(encoding="utf-8").split(None, 1)
    assert sidecar_sha == expected_sha
    assert sidecar_name.strip() == archive.LEDGER_FILENAME

    # no leftover staging artifacts
    assert not (campaign_dir / archive._STAGING_GZ_FILENAME).exists()
    assert not (campaign_dir / archive._STAGING_SIDECAR_FILENAME).exists()
    assert _has_authoritative_copy(campaign_dir)


def test_archiving_twice_is_idempotent(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    first = archive.ensure_archived(campaign_dir)
    assert first.action == "archived"

    second = archive.ensure_archived(campaign_dir)
    assert second.action == "already_archived"
    assert second.sha256 == expected_sha
    assert _has_authoritative_copy(campaign_dir)


def test_deterministic_gzip_bytes_for_same_input() -> None:
    """mtime=0 固定により、同一入力から常に同一バイト列の gz が作られる
    （再現性。§V4 の運用規約に付随する期待）。"""
    data = b"hello world\n" * 10
    first = archive._write_gzip_bytes(data)
    second = archive._write_gzip_bytes(data)
    assert first == second
    assert gzip.decompress(first) == data


# ---------------------------------------------------------------------------
# 故障注入: 各段階間で中断した状態を直接構成し、再実行で完結することを確認
# ---------------------------------------------------------------------------


def test_recovers_from_interruption_after_staging_write(tmp_path: Path) -> None:
    """(1) staging 書込直後で中断 — 原本のみが正、staging は破棄されて
    最初からやり直される。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    staging_gz = campaign_dir / archive._STAGING_GZ_FILENAME
    staging_sidecar = campaign_dir / archive._STAGING_SIDECAR_FILENAME
    staging_gz.write_bytes(archive._write_gzip_bytes(original_bytes))
    staging_sidecar.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")

    assert _has_authoritative_copy(campaign_dir)  # original still present

    result = archive.ensure_archived(campaign_dir)
    assert result.action == "archived"
    assert result.sha256 == expected_sha
    assert not staging_gz.exists()
    assert not staging_sidecar.exists()
    assert _has_authoritative_copy(campaign_dir)


def test_recovers_from_interruption_after_verification_before_publish(tmp_path: Path) -> None:
    """(2) staging 検証成功直後で中断（rename 未実施）— (1) 後の中断と
    到達可能状態は同一（検証はファイルを変更しない）ため、同じ回復規則が
    働くことを確認する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    staging_gz = campaign_dir / archive._STAGING_GZ_FILENAME
    staging_sidecar = campaign_dir / archive._STAGING_SIDECAR_FILENAME
    staging_gz.write_bytes(archive._write_gzip_bytes(original_bytes))
    staging_sidecar.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")
    # 検証自体を明示的に走らせておく（副作用が無いことの確認を兼ねる）。
    verified_sha, verified_bytes = archive._verify_gz_sidecar_pair(staging_gz, staging_sidecar)
    assert verified_sha == expected_sha
    assert verified_bytes == original_bytes

    result = archive.ensure_archived(campaign_dir)
    assert result.action == "archived"
    assert result.sha256 == expected_sha
    assert _has_authoritative_copy(campaign_dir)


def test_recovers_from_interruption_after_publish_before_original_delete(
    tmp_path: Path,
) -> None:
    """(3) rename による公開が完了したが (4) 原本削除の前で中断 — 公開物が
    検証を通るので、再実行は残存原本を除去するだけで完了する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")
    # 原本 (ledger.jsonl) はまだ削除されていない状態を模す。
    assert (campaign_dir / archive.LEDGER_FILENAME).is_file()

    result = archive.ensure_archived(campaign_dir)
    assert result.action == "already_archived"
    assert result.sha256 == expected_sha
    assert not (campaign_dir / archive.LEDGER_FILENAME).exists()
    assert _has_authoritative_copy(campaign_dir)


def test_recovers_from_interruption_mid_rename_only_gz_published(tmp_path: Path) -> None:
    """rename の 2 手のうち片方（gz）だけが公開され sidecar 側が未公開の
    まま中断 — 「両方揃って初めて公開」という判定により、原本が正のまま
    不完全な公開物は破棄されてやり直される。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    # sidecar は公開されていない（rename の途中で中断）。

    assert (campaign_dir / archive.LEDGER_FILENAME).is_file()

    result = archive.ensure_archived(campaign_dir)
    assert result.action == "archived"
    assert result.sha256 == expected_sha
    assert _has_authoritative_copy(campaign_dir)


def test_recovers_from_interruption_mid_rename_only_sidecar_published(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    sidecar_path.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")
    # gz は公開されていない。

    result = archive.ensure_archived(campaign_dir)
    assert result.action == "archived"
    assert result.sha256 == expected_sha
    assert _has_authoritative_copy(campaign_dir)


def test_corrupt_published_pair_with_original_present_is_discarded_and_redone(
    tmp_path: Path,
) -> None:
    """公開物が両方存在するが検証に失敗する（sidecar 改竄等）場合でも、
    原本が残っていれば原本を正として安全にやり直せる。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    # sidecar に誤った sha256 を書く（改竄/破損を模す）。
    sidecar_path.write_text(archive._sidecar_text("f" * 64), encoding="utf-8")

    result = archive.ensure_archived(campaign_dir)
    assert result.action == "archived"
    assert result.sha256 == expected_sha
    assert _has_authoritative_copy(campaign_dir)


# ---------------------------------------------------------------------------
# R22 fix (PR #346 round 22 finding (3) 採用, "Preserve invalid archives
# until the original verifies"): 無効な公開物 (gz/sidecar 全部または片方) を
# 削除する前に、残存 `ledger.jsonl` 自身の chain 検証を必ず通す。原本が
# 検証を通らない限り、たとえ公開物側の検証が失敗していても一切削除しない
# （その公開物が唯一の回復可能なコピーであり得るため）。
# ---------------------------------------------------------------------------


def test_invalid_pair_with_truncated_original_refuses_and_preserves_all_files(
    tmp_path: Path,
) -> None:
    """(a) 有効な gz（それ自体は伸長でき chain も通る）+ 宣言 sha256 が
    食い違う（＝ペア検証失敗の原因）sidecar + 途中で切り詰められた元
    ledger が残った状態。gz は唯一の回復可能なコピーであり得るため、修正前
    のように「原本が存在する」というだけで無条件削除してはならない。修正後
    は削除ゼロで `LedgerArchiveRefusedError`（`ArchiveError` のサブクラス）を
    送出し、3 ファイルとも byte 単位で無傷のまま残す。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    recoverable_bytes = _build_tiny_ledger(campaign_dir, n=3)

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_bytes_before = archive._write_gzip_bytes(recoverable_bytes)
    gz_path.write_bytes(gz_bytes_before)
    # sidecar は無関係な sha256 を宣言する（stale/mismatched — ペア検証は
    # 伸長ではなく sha256 不一致で失敗する）。
    stale_sidecar_text = archive._sidecar_text("a" * 64)
    sidecar_path.write_text(stale_sidecar_text, encoding="utf-8")

    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    truncated_bytes = recoverable_bytes[: len(recoverable_bytes) // 2]
    ledger_path.write_bytes(truncated_bytes)

    with pytest.raises(archive.LedgerArchiveRefusedError) as excinfo:
        archive.ensure_archived(campaign_dir)
    assert isinstance(excinfo.value, archive.ArchiveError)  # 既存語彙も維持

    # 削除ゼロ・変更ゼロ: 3 ファイルとも byte 単位で無傷のまま。
    assert gz_path.read_bytes() == gz_bytes_before
    assert sidecar_path.read_text(encoding="utf-8") == stale_sidecar_text
    assert ledger_path.read_bytes() == truncated_bytes
    assert not (campaign_dir / archive._STAGING_GZ_FILENAME).exists()
    assert not (campaign_dir / archive._STAGING_SIDECAR_FILENAME).exists()


def test_invalid_pair_with_valid_original_still_rearchives_as_before(
    tmp_path: Path,
) -> None:
    """(b) 無効ペア + 有効な原本 — 従来どおり原本を正として安全に
    re-archive できることの回帰確認（`_require_canonical_original_or_refuse`
    が「原本が canonical なら削除して続行」の経路を壊していないことを
    明示的に固定する。`test_corrupt_published_pair_with_original_present_
    is_discarded_and_redone` と同型だが、本テストは finding (3) のテスト
    観点 (b) 用に独立して残す）。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=3)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    # sidecar に誤った sha256 を書く（改竄/破損を模す — ペア検証失敗）。
    sidecar_path.write_text(archive._sidecar_text("f" * 64), encoding="utf-8")

    result = archive.ensure_archived(campaign_dir)

    assert result.action == "archived"
    assert result.sha256 == expected_sha
    assert not (campaign_dir / archive.LEDGER_FILENAME).exists()
    assert _has_authoritative_copy(campaign_dir)


def test_partial_pair_with_truncated_original_refuses_and_preserves_all_files(
    tmp_path: Path,
) -> None:
    """(c) 監査で発見した同型の穴: 公開物が片方のみ存在する分岐
    （`elif has_gz or has_sidecar:`）も、原本の chain 検証前に不完全な公開物
    を削除していた。有効な gz 単体（sidecar は未公開のまま — rename 途中の
    中断を模す）+ 切り詰められた元 ledger が残る場合、この gz が唯一の
    回復可能なコピーであり得るため、同じ規則で削除を拒否する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    recoverable_bytes = _build_tiny_ledger(campaign_dir, n=3)

    gz_path = campaign_dir / archive.GZ_FILENAME
    gz_bytes_before = archive._write_gzip_bytes(recoverable_bytes)
    gz_path.write_bytes(gz_bytes_before)
    # sidecar は公開されていない（rename の途中で中断した状態を模す）。

    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    truncated_bytes = recoverable_bytes[: len(recoverable_bytes) // 2]
    ledger_path.write_bytes(truncated_bytes)

    with pytest.raises(archive.LedgerArchiveRefusedError):
        archive.ensure_archived(campaign_dir)

    assert gz_path.read_bytes() == gz_bytes_before
    assert not (campaign_dir / archive.SIDECAR_FILENAME).exists()
    assert ledger_path.read_bytes() == truncated_bytes
    assert not (campaign_dir / archive._STAGING_GZ_FILENAME).exists()
    assert not (campaign_dir / archive._STAGING_SIDECAR_FILENAME).exists()


def test_orphaned_state_without_original_or_valid_publish_raises(tmp_path: Path) -> None:
    """原本も無く、公開物も検証を通らない — 正本喪失。手順どおりに実行して
    いれば到達しない状態だが、到達したら fail-closed で `ArchiveError` を
    送出し、沈黙して先へ進まないことを固定する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    campaign_dir.mkdir(parents=True)

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(b'{"broken": true}\n'))
    sidecar_path.write_text(archive._sidecar_text("0" * 64), encoding="utf-8")

    with pytest.raises(archive.ArchiveError):
        archive.ensure_archived(campaign_dir)


def test_nothing_present_at_all_raises(tmp_path: Path) -> None:
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    campaign_dir.mkdir(parents=True)
    with pytest.raises(archive.ArchiveError):
        archive.ensure_archived(campaign_dir)


# ---------------------------------------------------------------------------
# R9 fix (PR #346 round 9 採用): CLOSED (`campaign_closed`) campaigns are the
# immutable canonical record (module docstring) and must never be archived —
# `ensure_archived()` now enforces this itself, fail-closed, rather than
# leaning on caller discipline alone.
# ---------------------------------------------------------------------------


def _build_closed_ledger(campaign_dir: Path, n: int = 3) -> bytes:
    """有効な chain を持つ小さな ledger.jsonl に `campaign_closed` event を
    追加して返す（生バイト列）。"""
    campaign_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    ledger = Ledger(ledger_path)
    for i in range(n):
        ledger.append({"kind": "test_event", "i": i})
    ledger.append({"kind": "campaign_closed"})
    return ledger_path.read_bytes()


def test_ensure_archived_refuses_campaign_closed_original_and_leaves_files_untouched(
    tmp_path: Path,
) -> None:
    """(a) closed campaign を含む合成 ledger — `ArchiveError` を送出し、
    ディレクトリの中身が一切変化しない（staging すら作られない）ことを
    固定する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-closed"
    original_bytes = _build_closed_ledger(campaign_dir)
    before = sorted(p.name for p in campaign_dir.iterdir())

    with pytest.raises(archive.ArchiveError, match="campaign_closed"):
        archive.ensure_archived(campaign_dir)

    after = sorted(p.name for p in campaign_dir.iterdir())
    assert after == before
    assert (campaign_dir / archive.LEDGER_FILENAME).read_bytes() == original_bytes


def test_ensure_archived_refuses_and_restores_original_when_archived_copy_is_closed(
    tmp_path: Path,
) -> None:
    """回復経路: 本ガード追加前に閉鎖 campaign が誤って archive 済み
    （`ledger.jsonl` は既に無く、検証済み `ledger.jsonl.gz` + sidecar のみ
    存在する）状態を直接構成する。`ensure_archived()` は `ArchiveError` を
    送出しつつ、検証済み gz から原本を復元し、公開物には触れない。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-closed"
    original_bytes = _build_closed_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    ledger_path.unlink()  # 本ガード追加前に完了していた archive を模す。

    gz_bytes_before = gz_path.read_bytes()
    sidecar_text_before = sidecar_path.read_text(encoding="utf-8")

    with pytest.raises(archive.ArchiveError, match="campaign_closed"):
        archive.ensure_archived(campaign_dir)

    # 原本が復元され、公開物には一切手を付けていない。
    assert ledger_path.is_file()
    assert ledger_path.read_bytes() == original_bytes
    assert gz_path.read_bytes() == gz_bytes_before
    assert sidecar_path.read_text(encoding="utf-8") == sidecar_text_before


def test_ensure_archived_refuses_closed_original_even_with_valid_archived_copy_present(
    tmp_path: Path,
) -> None:
    """原本 (`ledger.jsonl`) が closed campaign のまま残っている限り、
    たとえ検証済みの公開物一式が既に揃っていても — 通常なら
    `already_archived` として原本を消して完了する状況 — 先頭の原本ガードが
    先に発火し、原本の削除も staging への書き込みも一切行わない。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-closed"
    original_bytes = _build_closed_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")

    with pytest.raises(archive.ArchiveError, match="campaign_closed"):
        archive.ensure_archived(campaign_dir)

    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    assert ledger_path.is_file()
    assert ledger_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# R10 fix (PR #346 round 10, Codex P1 採用): 復元経路の原子化。旧実装は
# `Path.write_bytes()` の直書きで、kill されると truncated な
# `ledger.jsonl` が残り、次回起動は「原本が存在する」ため復元を拒否 —
# 手動削除まで正典が壊れて見える詰み状態だった。
# ---------------------------------------------------------------------------


def test_restore_original_from_verified_gz_is_atomic_via_staging(tmp_path: Path) -> None:
    """`_restore_original_from_verified_gz()` 単体: staging ファイル
    （`ledger.jsonl.restoring`）を経由して `os.rename()` で公開し、呼び出し
    完了後は staging が残らないこと（原子性の直接固定）。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-restore"
    campaign_dir.mkdir(parents=True)
    payload = b'{"kind": "campaign_closed"}\n'
    gz_path = campaign_dir / archive.GZ_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(payload))
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    staging_ledger_path = campaign_dir / archive._STAGING_LEDGER_FILENAME

    archive._restore_original_from_verified_gz(
        ledger_path, gz_path, campaign_dir, staging_ledger_path
    )

    assert ledger_path.read_bytes() == payload
    assert not staging_ledger_path.exists()


def test_ensure_archived_discards_leftover_restore_staging_and_recovers(
    tmp_path: Path,
) -> None:
    """復元処理が (1) staging 書込直後・(2) `os.rename()` 未実施で kill
    された状態を直接構成する。回復規則どおり、次回起動は staging 残骸を
    無条件で破棄してから正しく復元し直す（staging の中身がゴミでも
    構わない — 再実行は常に検証済み gz から作り直すため）。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-closed"
    original_bytes = _build_closed_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    ledger_path.unlink()  # 本ガード追加前に完了していた archive を模す。

    staging_ledger = campaign_dir / archive._STAGING_LEDGER_FILENAME
    staging_ledger.write_bytes(b"garbage-left-behind-by-a-killed-restore")

    with pytest.raises(archive.ArchiveError, match="campaign_closed"):
        archive.ensure_archived(campaign_dir)

    assert not staging_ledger.exists()
    assert ledger_path.is_file()
    assert ledger_path.read_bytes() == original_bytes


def test_ensure_archived_replaces_truncated_restore_residue_original(
    tmp_path: Path,
) -> None:
    """旧実装（`write_bytes()` 直書き）の復元が kill され、truncated な
    `ledger.jsonl` が残った状態を直接構成する。原本が「存在する」というだけ
    で復元をスキップしていた旧挙動と異なり、検証済み gz の sha256 と一致
    しない原本は「壊れた復元残骸」として検出され、staging 経由で再復元
    される——手動削除なしに詰み状態から回復できることを固定する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-closed"
    original_bytes = _build_closed_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")

    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    # 旧実装の直書きが途中で kill された状態を模す: 先頭部分だけの
    # truncated な内容（`campaign_closed` の最終行を含まない）。
    truncated = original_bytes[: len(original_bytes) // 3]
    ledger_path.write_bytes(truncated)
    assert ledger_path.is_file()
    assert hashlib.sha256(ledger_path.read_bytes()).hexdigest() != expected_sha

    with pytest.raises(archive.ArchiveError, match="campaign_closed"):
        archive.ensure_archived(campaign_dir)

    assert ledger_path.is_file()
    assert ledger_path.read_bytes() == original_bytes  # truncation healed
    assert not (campaign_dir / archive._STAGING_LEDGER_FILENAME).exists()


# ---------------------------------------------------------------------------
# R11 fix (PR #346 round 11 採用, 2026-09-05): 公開済み gz+sidecar が揃った
# 後・原本削除前 (4) に中断し、その後残存 `ledger.jsonl` へ追記があった場合、
# 旧実装は次回 `ensure_archived()` が公開物のみ検証して新しい原本を無条件
# `unlink()` していたため、追記分が恒久喪失していた。
# ---------------------------------------------------------------------------


def test_ensure_archived_rearchives_leftover_original_with_genuine_append(
    tmp_path: Path,
) -> None:
    """(a) 公開済み gz+sidecar が揃った後・原本削除前に中断し、その後残存
    `ledger.jsonl` へ正当な（chain 継続する）追記があった場合、旧実装は
    無条件 `unlink()` で追記分を失っていた。新実装は残存原本が公開済み
    archive の chain-検証済み byte-prefix 拡張だと判定し、原本を正として
    archive を作り直し、新しい sha256 が sidecar に反映される。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=5)
    published_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(published_sha), encoding="utf-8")

    # 原本削除前に、さらなる append があった状態を模す。
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    assert ledger_path.is_file()
    Ledger(ledger_path).append({"kind": "test_event", "i": 100})
    extended_bytes = ledger_path.read_bytes()
    extended_sha = hashlib.sha256(extended_bytes).hexdigest()
    assert extended_bytes.startswith(original_bytes)
    assert extended_sha != published_sha

    result = archive.ensure_archived(campaign_dir)

    assert result.action == "archived"
    assert result.sha256 == extended_sha
    assert not ledger_path.exists()
    assert gzip.decompress(gz_path.read_bytes()) == extended_bytes
    sidecar_sha, sidecar_name = sidecar_path.read_text(encoding="utf-8").split(None, 1)
    assert sidecar_sha == extended_sha
    assert sidecar_name.strip() == archive.LEDGER_FILENAME
    assert not (campaign_dir / archive._STAGING_GZ_FILENAME).exists()
    assert not (campaign_dir / archive._STAGING_SIDECAR_FILENAME).exists()
    assert _has_authoritative_copy(campaign_dir)


def test_ensure_archived_refuses_inconsistent_leftover_original_and_keeps_both(
    tmp_path: Path,
) -> None:
    """(b) 残存原本が公開済み archive の byte-prefix 拡張ではない（無関係な
    ledger と取り違えた/改竄された）場合は、原本にも公開物にも一切手を
    付けず `ArchiveError` で停止し、両方を保全する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=5)
    published_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_bytes_before = archive._write_gzip_bytes(original_bytes)
    sidecar_text_before = archive._sidecar_text(published_sha)
    gz_path.write_bytes(gz_bytes_before)
    sidecar_path.write_text(sidecar_text_before, encoding="utf-8")

    # 残存原本を、公開済み archive とは無関係な chain へ丸ごと置き換える
    # （byte-prefix にならない不整合）。R19 fix（本テストファイル対象の
    # 変更）後は `Ledger.append()` 自体が「path 不在 + 検証済み archive ペア
    # 存在」を `LedgerArchivedError` で拒否するため、この不整合原本は
    # 無関係な別ディレクトリで独立に作った ledger の bytes をそのまま
    # 書き込んで用意する — ここで検査したいのは `ensure_archived()` 側の
    # byte-prefix 不整合検出であり、`Ledger.append()` の新規作成可否では
    # ない（それは R19 の対象そのもので、他所で別途検証する）。
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    ledger_path.unlink()
    scratch_ledger_path = tmp_path / "scratch-unrelated" / "ledger.jsonl"
    Ledger(scratch_ledger_path).append({"kind": "unrelated_event", "i": 999})
    mismatched_bytes = scratch_ledger_path.read_bytes()
    ledger_path.write_bytes(mismatched_bytes)
    assert mismatched_bytes != original_bytes
    assert not mismatched_bytes.startswith(original_bytes)

    with pytest.raises(archive.ArchiveError):
        archive.ensure_archived(campaign_dir)

    # 両方とも一切変更されていない（fail-closed）。
    assert ledger_path.read_bytes() == mismatched_bytes
    assert gz_path.read_bytes() == gz_bytes_before
    assert sidecar_path.read_text(encoding="utf-8") == sidecar_text_before


def test_ensure_archived_matching_leftover_original_is_discarded_as_before(
    tmp_path: Path,
) -> None:
    """(c) 残存原本の sha256 が公開済み sidecar と一致する場合は、従来どおり
    原本を除去するだけで完了する（回帰確認 —
    `test_recovers_from_interruption_after_publish_before_original_delete`
    と同型だが、R11 の新規分岐が「一致」経路に副作用を持ち込んでいないこと
    を明示的に固定する）。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=5)
    published_sha = hashlib.sha256(original_bytes).hexdigest()

    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(published_sha), encoding="utf-8")
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    assert ledger_path.read_bytes() == original_bytes  # sha256 一致

    result = archive.ensure_archived(campaign_dir)

    assert result.action == "already_archived"
    assert result.sha256 == published_sha
    assert not ledger_path.exists()
    assert _has_authoritative_copy(campaign_dir)


# ---------------------------------------------------------------------------
# R12 fix (PR #346 round 12 採用): 原本 bytes 読み取り → staging → 検証 →
# 公開 → 原本 unlink の区間を `provenance.Ledger.append()` と同一の排他
# ロックの下で実行し、かつ unlink 直前に原本の sha256 を再照合する
# （食い違えば R11 の判定規則 `_reconcile_diverged_original()` へ合流させる）。
# ---------------------------------------------------------------------------


def _compute_extended_bytes(original_bytes: bytes, tmp_path: Path, i: int = 999) -> bytes:
    """`original_bytes` に 1 エントリ追記した chain-valid な拡張バイト列を、
    実際の campaign_dir には一切触れずに（スクラッチ用の別ディレクトリで）
    計算する。"""
    scratch_dir = tmp_path / f"scratch-extend-{i}"
    scratch_dir.mkdir()
    scratch_ledger = scratch_dir / archive.LEDGER_FILENAME
    scratch_ledger.write_bytes(original_bytes)
    Ledger(scratch_ledger).append({"kind": "test_event", "i": i})
    return scratch_ledger.read_bytes()


def test_ledger_write_lock_blocks_concurrent_append(tmp_path: Path) -> None:
    """`_ledger_write_lock()` が保持するロックは `provenance.Ledger.append()`
    と同一（R14 fix: `ledger.jsonl` 自身の fd ではなく、`_ledger_lock_path()`
    が指す同ディレクトリの安定ロックファイル上の `fcntl.flock(LOCK_EX)`）で
    ある——`Ledger.append()` も同じ計算式でこのロックファイルを先に取得する
    ため、archive 側がロックを保持している間、別スレッドの `append()` は
    ロック解放まで待たされる（blocking、タイムアウトなし——
    `Ledger.append()` 自身の流儀に合わせた設計判断）。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    _build_tiny_ledger(campaign_dir, n=1)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME

    appended = threading.Event()

    def _do_append() -> None:
        Ledger(ledger_path).append({"kind": "test_event", "i": 1})
        appended.set()

    with archive._ledger_write_lock(ledger_path):
        thread = threading.Thread(target=_do_append)
        thread.start()
        # ロックが効いていなければこの間に append は完了してしまう。
        blocked_in_time = not appended.wait(timeout=0.3)
        assert blocked_in_time, "append() was not blocked by the held archive lock"

    thread.join(timeout=5)
    assert appended.is_set()
    assert not thread.is_alive()
    # ロック解放後は正常に追記され、chain は依然として有効。
    _, chain = Ledger.load_with_verification(ledger_path)
    assert chain.ok


# ---------------------------------------------------------------------------
# R14 fix (PR #346 round 14 採用): "Coordinate appenders before unlinking
# the locked inode" — ロック対象を `ledger.jsonl` 自身の fd から同ディレクト
# リの安定した専用ロックファイルへ変更し、appender/archiver 双方が **ledger
# 本体を open する前に** このロックを取得する。旧実装は appender が
# `ledger.jsonl` を（unlink 前なので同一 inode で）先に open してから flock
# を要求していたため、archiver がロック保持中に割り込むと、appender は
# 旧 inode の fd で flock 待ちに入り、archiver の unlink でパス名が消えた
# **後** にこの flock を獲得し、切り離された inode へ書き込んで entry が
# 恒久喪失し得た。
# ---------------------------------------------------------------------------


def test_lock_path_is_stable_dedicated_file_not_ledger_itself(tmp_path: Path) -> None:
    """R14 fix: `_ledger_write_lock()`/`Ledger.append()` が実際に取り合う
    ロック対象は `ledger.jsonl` 自身ではなく、同ディレクトリの専用ロック
    ファイル（`ledger.jsonl.lock`）である——このファイルは archiver の
    `unlink()` 対象に一切含まれない。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    _build_tiny_ledger(campaign_dir, n=1)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME

    lock_path = archive._ledger_lock_path(ledger_path)
    assert lock_path == campaign_dir / "ledger.jsonl.lock"
    assert lock_path != ledger_path

    with archive._ledger_write_lock(ledger_path):
        assert lock_path.is_file()
        # ロック保持中に原本を unlink しても、ロックファイル自体は無関係
        # （archiver の実運用どおり: unlink は `ledger_path` のみに作用する）。
        ledger_path.unlink()
        assert lock_path.is_file()
    assert lock_path.is_file()  # ロック解放後も常設ファイルとして残る


def test_appender_refused_not_lost_after_archiver_unlinks_under_stable_lock(
    tmp_path: Path,
) -> None:
    """R14 の核心回帰: archiver がロック保持中に、既存の（構築済み・古い
    watermark を持つ）`Ledger` インスタンスで `append()` を試みるスレッドを
    起こすと、そのスレッドはロック解放までブロックされ（stable lock file
    経由）、archiver が実際の archive 手順（`_write_and_publish_archive` →
    `unlink`）を完了させてロックを解放した**後**にようやく進行する。この
    時点で `ledger_path` は既に unlink 済みのため、appender が新たに
    `open("a+b")` するのは「まっさらな新しい inode」であり、`current_size
    (0) < 保持していた watermark (>0)` の既存 rollback 検知
    (`LedgerChainInvalidError`) が働いて **fail-closed で拒否される**。

    修正前の実装（`ledger_path` 自身の fd に flock）では、appender が
    archiver のロック取得前に `ledger_path` を open 済みだった場合、その
    fd は unlink 後も同じ（切り離された）inode を指し続け、この fd への
    書き込みは gz snapshot にもどのファイルパスにも存在しない場所へ消えて
    いた——本テストは「書き込みが起きない・拒否される」ことを固定する。
    """
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=3)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    staging_gz_path = campaign_dir / archive._STAGING_GZ_FILENAME
    staging_sidecar_path = campaign_dir / archive._STAGING_SIDECAR_FILENAME

    # 「archive 実行前から生きていた appender」を模す: この Ledger インスタン
    # スの watermark は archiving 前の 3 エントリを指したまま。
    stale_ledger = Ledger(ledger_path)
    assert stale_ledger.entries[-1].seq == 2

    append_errors: list[BaseException] = []
    started = threading.Event()

    def _do_append() -> None:
        started.set()
        try:
            stale_ledger.append({"kind": "test_event", "i": 99})
        except BaseException as exc:  # noqa: BLE001 - captured for the main thread
            append_errors.append(exc)

    with archive._ledger_write_lock(ledger_path):
        thread = threading.Thread(target=_do_append)
        thread.start()
        assert started.wait(timeout=5), "append() thread never started"
        thread.join(timeout=0.3)
        assert thread.is_alive(), "append() was not blocked by the held archiver lock"

        # 実運用の ensure_archived() が unlink 直前に行う手順そのもの
        # （公開 → 検証済み gz/sidecar → 原本 unlink）を、同じロックの下で
        # 実行する。
        archive._write_and_publish_archive(
            original_bytes,
            campaign_dir,
            gz_path,
            sidecar_path,
            staging_gz_path,
            staging_sidecar_path,
        )
        ledger_path.unlink()

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(append_errors) == 1, f"expected exactly one refusal, got {append_errors}"
    assert isinstance(append_errors[0], LedgerChainInvalidError)

    # アーカイブは無傷: 失われた追記はどこにも存在しない（gz にも、恒久化
    # した on-disk 状態にも）。
    assert gzip.decompress(gz_path.read_bytes()) == original_bytes
    if ledger_path.exists():
        # "a+b" は open 時に新しい（空の）inode を作り得るが、append() は
        # 書き込みに至る前に fail-closed するため中身は空のままである。
        assert ledger_path.read_bytes() == b""


# ---------------------------------------------------------------------------
# R15 fix (PR #346 round 15 採用): "Lock recovery before reading the residual
# ledger" — 検証済み公開物 (gz+sidecar) が既にある場合の回復分岐（残存
# `ledger.jsonl` の sha 照合・`_reconcile_diverged_original()` 経由の再
# archive・unlink）は、修正前は `_ledger_write_lock()` を取得せずに実行
# されていた。R12/R14 は (1)-(4) のフレッシュ archive 区間のみを排他化して
# おり、この回復分岐は素通りだった。
# ---------------------------------------------------------------------------


def test_recovery_branch_blocks_concurrent_append_and_rejects_stale_appender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R15 の核心回帰: 公開済み gz+sidecar が揃い、原本削除 (4) の前で中断
    した状態（`already_archived` 回復分岐）で `ensure_archived()` を実行する
    と、回復分岐の判定（`_verify_gz_sidecar_pair()` 呼び出し時点）で既に
    安定ロックを保持している——このロック保持中、archiver 起動前から生きて
    いた `Ledger` インスタンス（stale watermark）の `append()` はブロック
    され続け、archiver が回復分岐を完了してロックを解放した後にようやく
    進行する。この時点で `ledger_path` は既に `unlink()` 済みのため、
    `append()` は「まっさらな新しい inode」を open することになり、
    `current_size (0) < 保持していた watermark (>0)` の rollback 検知で
    fail-closed 拒否される（R14 と同型の安全な結果 — 正典喪失ゼロ）。

    修正前（回復分岐がロック未保持）では、この race 窓の中で append が
    割り込むと、回復分岐の sha 照合がそれを検出しないまま無条件 `unlink()`
    に巻き込み、追記内容を恒久喪失し得た。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=3)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME

    # 公開済み gz+sidecar が揃っているが、原本削除 (4) の前で中断した状態
    # を模す（`test_recovers_from_interruption_after_publish_before_
    # original_delete` と同じ構成 — recovery 側の "already_archived" 分岐
    # に入る）。
    gz_path.write_bytes(archive._write_gzip_bytes(original_bytes))
    sidecar_path.write_text(archive._sidecar_text(expected_sha), encoding="utf-8")
    assert ledger_path.is_file()

    # 「archiver 起動前から生きていた appender」を模す: watermark は
    # archiving 前の 3 エントリを指したまま。
    stale_ledger = Ledger(ledger_path)
    assert stale_ledger.entries[-1].seq == 2

    entered_recovery = threading.Event()
    release_recovery = threading.Event()
    real_verify = archive._verify_gz_sidecar_pair
    call_count = {"n": 0}

    def _blocking_verify(gz: Path, sidecar: Path) -> tuple[str, bytes]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 回復分岐（`has_gz and has_sidecar`）の最初の検証呼び出し —
            # R15 fix が正しければ、この時点で既に安定ロックを保持している。
            entered_recovery.set()
            release_recovery.wait(timeout=5)
        return real_verify(gz, sidecar)

    monkeypatch.setattr(archive, "_verify_gz_sidecar_pair", _blocking_verify)

    archiver_result: dict[str, archive.ArchiveResult] = {}

    def _run_archiver() -> None:
        archiver_result["value"] = archive.ensure_archived(campaign_dir)

    archiver_thread = threading.Thread(target=_run_archiver)
    archiver_thread.start()
    assert entered_recovery.wait(timeout=5), "archiver never entered the recovery branch"

    append_errors: list[BaseException] = []
    append_done = threading.Event()

    def _do_append() -> None:
        try:
            stale_ledger.append({"kind": "test_event", "i": 99})
        except BaseException as exc:  # noqa: BLE001 - captured for the main thread
            append_errors.append(exc)
        finally:
            append_done.set()

    append_thread = threading.Thread(target=_do_append)
    append_thread.start()

    # archiver が回復分岐でロックを保持している間、append は完了できない
    # はず（修正前は回復分岐がロック未保持だったため、ここで完了してしまい
    # 得た）。
    blocked_in_time = not append_done.wait(timeout=0.3)
    assert blocked_in_time, "append() was not blocked by the recovery branch's lock"

    release_recovery.set()
    archiver_thread.join(timeout=5)
    append_thread.join(timeout=5)
    assert not archiver_thread.is_alive()
    assert not append_thread.is_alive()

    result = archiver_result["value"]
    assert result.action == "already_archived"
    assert result.sha256 == expected_sha

    # append は archiver 完了後（unlink 済みの旧 inode に対する fail-closed
    # 拒否）として安全に終わる。
    assert len(append_errors) == 1, f"expected exactly one refusal, got {append_errors}"
    assert isinstance(append_errors[0], LedgerChainInvalidError)

    # アーカイブは無傷: 拒否された追記はどこにも存在せず、gz は元の 3
    # エントリを欠落なく保全している（正典喪失ゼロ）。
    assert gzip.decompress(gz_path.read_bytes()) == original_bytes
    if ledger_path.exists():
        # "a+b" は open 時に新しい（空の）inode を作り得るが、append() は
        # 書き込みに至る前に fail-closed するため中身は空のままである。
        assert ledger_path.read_bytes() == b""
    assert _has_authoritative_copy(campaign_dir)


# ---------------------------------------------------------------------------
# R17 fix (PR #346 round 17 採用, "Lock before checking for campaign
# closure"): R15 が回復判定ブロックと (1)-(4) のフレッシュ archive ブロック
# の両方をロック区間へ統合した後もなお、その *直前* に置かれていた
# closed-campaign 検査自体（R9 fix 節）は `_ledger_write_lock()` を取得する
# 前に `ledger_path.read_bytes()` を実行していた。この検査完了直後・ロック
# 獲得前の一瞬に別スレッドが `campaign_closed` を append すると検出されず、
# closed campaign を誤って archive してしまっていた。
# ---------------------------------------------------------------------------


def test_closed_campaign_appended_before_lock_acquisition_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R17 の核心回帰: `ensure_archived()` が実際に安定ロックを取得しよう
    とする直前（＝修正前の実装ならば closed-campaign 検査が既に完了して
    しまっていたはずの瞬間）に割り込ませた
    `Ledger.append({"kind": "campaign_closed"})` を、archiver が正しく
    検出して `ArchiveError` を送出し、gz/sidecar を一切作らず・原本も
    archiver 自身によっては一切変更しないことを固定する。

    修正前（closed-campaign 検査がロック取得より前に実行される実装）では、
    この位置での append は検査完了より後に発生するため検出されず、archiver
    はそのまま closed campaign を archive してしまっていた（本テストはその
    回帰を再現する: `_ledger_write_lock` の実際の獲得だけを遅延させ、検査
    そのものの実行位置がロックの内側か外側かで結果が変わることを固定する）。
    """
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=3)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME

    real_lock = archive._ledger_write_lock
    about_to_lock = threading.Event()
    release_lock_attempt = threading.Event()

    @contextlib.contextmanager
    def _delayed_lock(path: Path):
        about_to_lock.set()
        assert release_lock_attempt.wait(timeout=5), "test never released the lock attempt"
        with real_lock(path):
            yield

    monkeypatch.setattr(archive, "_ledger_write_lock", _delayed_lock)

    archiver_errors: list[BaseException] = []

    def _run_archiver() -> None:
        try:
            archive.ensure_archived(campaign_dir)
        except BaseException as exc:  # noqa: BLE001 - captured for the main thread
            archiver_errors.append(exc)

    archiver_thread = threading.Thread(target=_run_archiver)
    archiver_thread.start()
    assert about_to_lock.wait(timeout=5), "archiver never reached the lock acquisition point"

    # archiver はまだ安定ロックを取得していない（`_delayed_lock` が
    # `release_lock_attempt` 待ちでブロック中）。この隙に、独立した別の
    # `Ledger` インスタンス（別スレッド相当）から `campaign_closed` を
    # 通常運用どおり append する——このロック取り合い自体は正常に完了する。
    Ledger(ledger_path).append({"kind": "campaign_closed"})

    release_lock_attempt.set()
    archiver_thread.join(timeout=5)
    assert not archiver_thread.is_alive()

    assert len(archiver_errors) == 1, f"expected exactly one ArchiveError, got {archiver_errors}"
    assert isinstance(archiver_errors[0], archive.ArchiveError)
    assert "campaign_closed" in str(archiver_errors[0])

    # gz/sidecar は一切作られておらず、原本は append 済みの内容のまま
    # archiver によって一切変更されていない（正典喪失ゼロ）。
    assert not gz_path.exists()
    assert not sidecar_path.exists()
    assert ledger_path.is_file()
    on_disk = ledger_path.read_bytes()
    assert on_disk.startswith(original_bytes)
    assert on_disk != original_bytes
    assert not (campaign_dir / archive._STAGING_GZ_FILENAME).exists()
    assert not (campaign_dir / archive._STAGING_SIDECAR_FILENAME).exists()
    assert not (campaign_dir / archive._STAGING_LEDGER_FILENAME).exists()


def test_ensure_archived_rearchives_when_original_extended_during_archival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """フレッシュ archive の実行中（公開直後・原本削除の前）に、ロック契約に
    従わない直接書き込みで原本が chain-valid に拡張された場合でも、削除
    直前の再照合がそれを検出し、拡張分を失わずに archive を作り直す
    （R11 の判定規則との合流）。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=5)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    extended_bytes = _compute_extended_bytes(original_bytes, tmp_path)
    extended_sha = hashlib.sha256(extended_bytes).hexdigest()

    real_rename = archive.os.rename
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME

    def _sneaky_rename(src: object, dst: object) -> None:
        real_rename(src, dst)
        if Path(dst) == sidecar_path:
            # publish (3) 完了直後・(4) 原本削除前に割り込む「ロックを
            # 経由しない書き込み」を模す。
            ledger_path.write_bytes(extended_bytes)

    monkeypatch.setattr(archive.os, "rename", _sneaky_rename)

    result = archive.ensure_archived(campaign_dir)

    assert result.action == "archived"
    assert result.sha256 == extended_sha
    assert not ledger_path.exists()
    gz_path = campaign_dir / archive.GZ_FILENAME
    assert gzip.decompress(gz_path.read_bytes()) == extended_bytes
    sidecar_sha, _ = sidecar_path.read_text(encoding="utf-8").split(None, 1)
    assert sidecar_sha == extended_sha
    assert not (campaign_dir / archive._STAGING_GZ_FILENAME).exists()
    assert not (campaign_dir / archive._STAGING_SIDECAR_FILENAME).exists()
    assert _has_authoritative_copy(campaign_dir)


def test_ensure_archived_refuses_when_original_diverges_unrelatedly_during_archival(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(反対ケース) 割り込んだ内容が公開直後の snapshot の byte-prefix
    拡張ではない（改竄/無関係な取り違え相当）場合は、削除を拒否し
    `ArchiveError` を送出して原本・公開物のどちらも変更しない。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir, n=5)
    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    unrelated_bytes = b'{"unrelated": true}\n'

    real_rename = archive.os.rename
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME

    def _sneaky_rename(src: object, dst: object) -> None:
        real_rename(src, dst)
        if Path(dst) == sidecar_path:
            ledger_path.write_bytes(unrelated_bytes)

    monkeypatch.setattr(archive.os, "rename", _sneaky_rename)

    with pytest.raises(archive.ArchiveError):
        archive.ensure_archived(campaign_dir)

    # どちらも保全される: 公開物は publish 時点の原本のまま、原本は割り込み
    # 内容のまま — どちらも削除・上書きされていない。
    gz_path = campaign_dir / archive.GZ_FILENAME
    assert gzip.decompress(gz_path.read_bytes()) == original_bytes
    assert ledger_path.read_bytes() == unrelated_bytes


def test_cli_main_reports_failure_for_campaign_closed_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(b) CLI `main()` 経由でも同様に拒否され、非 0 exit code + `FAILED` を
    stderr へ出す。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-closed"
    _build_closed_ledger(campaign_dir)

    rc = archive.main([str(campaign_dir)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "campaign_closed" in err


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_main_archives_one_campaign_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    original_bytes = _build_tiny_ledger(campaign_dir)
    expected_sha = hashlib.sha256(original_bytes).hexdigest()

    rc = archive.main([str(campaign_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "archived" in out
    assert expected_sha in out


def test_cli_main_reports_failure_without_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    campaign_dir.mkdir(parents=True)
    rc = archive.main([str(campaign_dir)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FAILED" in err


# ---------------------------------------------------------------------------
# R19: ensure_archived() -> Ledger.append() 統合（"Refuse to recreate a
# ledger after archival"）
# ---------------------------------------------------------------------------


def test_ledger_append_after_ensure_archived_does_not_create_contradicting_artifact(
    tmp_path: Path,
) -> None:
    """`ensure_archived()` が原本 `ledger.jsonl` を検証済み `ledger.jsonl.gz`
    + sidecar へ置換した後、そのディレクトリを指す `Ledger.append()` は
    genesis ledger を黙って新規作成してはならない（R19 が塞いだ経路: 修正前
    は append が成功を報告しつつ、その event は公開済み gz に無く、次回
    `ensure_archived()` が新規 ledger.jsonl を非 prefix 乖離として拒否する
    ——矛盾する 2 つの provenance artifact が残っていた）。

    本テストは、修正後の `append()` が `LedgerArchivedError` で fail-closed
    し、(1) `ledger.jsonl` が作成されないこと、(2) 公開済み gz/sidecar が
    無傷のまま残ること、(3) 以後の `ensure_archived()` 呼び出しが矛盾なく
    `already_archived` を返し続けることを固定する。"""
    campaign_dir = tmp_path / "RUN10-CAL-fake-abort"
    _build_tiny_ledger(campaign_dir, n=3)

    result = archive.ensure_archived(campaign_dir)
    assert result.action == "archived"

    ledger_path = campaign_dir / archive.LEDGER_FILENAME
    gz_path = campaign_dir / archive.GZ_FILENAME
    sidecar_path = campaign_dir / archive.SIDECAR_FILENAME
    assert not ledger_path.is_file()
    gz_bytes_before = gz_path.read_bytes()
    sidecar_text_before = sidecar_path.read_text(encoding="utf-8")

    stray_ledger = Ledger(ledger_path)
    with pytest.raises(LedgerArchivedError):
        stray_ledger.append({"kind": "meter_call", "row_id": "post-archive"})

    # append 自体が何も書いていない: ledger.jsonl は依然として不在で、
    # 公開済み gz/sidecar も一切変更されていない。
    assert not ledger_path.is_file()
    assert gz_path.read_bytes() == gz_bytes_before
    assert sidecar_path.read_text(encoding="utf-8") == sidecar_text_before

    # 矛盾する artifact が残っていないことの直接証拠: 再度 ensure_archived()
    # を呼んでも矛盾なく `already_archived` を返し、公開物はそのまま。
    follow_up = archive.ensure_archived(campaign_dir)
    assert follow_up.action == "already_archived"
    assert follow_up.sha256 == result.sha256
    assert not ledger_path.is_file()
    assert gz_path.read_bytes() == gz_bytes_before
    assert sidecar_path.read_text(encoding="utf-8") == sidecar_text_before
