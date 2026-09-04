"""`tools/archive_aborted_ledger.py` の原子的置換 + 中断回復の検証
（設計正本 `DESIGN_VG_METER_CAL_DEBT_v1.1.md` §V4）。

各段階間（staging 書込後 / 検証後 / 公開後 / 原本削除前）で中断した状態を
直接構成し、`ensure_archived()` の再実行が回復規則どおりに完結すること・
どの中断状態でも「原本 or 検証済み公開物」のどちらかが必ず存在すること
（正本喪失ゼロ）を固定する。"""

from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

import pytest

from voice_genesis.calibration.provenance import Ledger
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
    verified_sha = archive._verify_gz_sidecar_pair(staging_gz, staging_sidecar)
    assert verified_sha == expected_sha

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
