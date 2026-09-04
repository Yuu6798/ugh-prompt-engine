"""破棄 campaign ledger の原子的 gzip 保全（設計正本 `DESIGN_VG_METER_CAL_DEBT_v1.1.md` §V4）。

破棄（abort）裁定済み campaign の `ledger.jsonl`（当該 campaign の唯一の正本）
を、非圧縮バイト列の sha256 を記録した sidecar `ledger.jsonl.sha256` 付きで
`ledger.jsonl.gz` へ置換して保全する。原本同一性は
``zcat ledger.jsonl.gz | sha256sum`` と sidecar の照合で機械検証できる
（chain 検証は伸長後に従来どおり可能）。

**置換は原子的に行う**（§V4, Codex レビュー第 5 巡 P1 採用）: (1) 同一
ディレクトリ内の staging 名で `ledger.jsonl.gz`/sidecar を書き、(2) staging の
gz を実際に伸長して sidecar sha256・原本バイト列・ledger chain の 3 点を
検証し、(3) fsync 後に `os.rename` で公開してから (4) 原本を削除する。

中断からの回復規則（`ensure_archived()` が毎回まず行う）:

- 公開済み `ledger.jsonl.gz` + sidecar が両方存在し、かつ検証を通る場合
  **のみ**、残存する原本 `ledger.jsonl` を除去してよい（= 既に完了）。
- それ以外（公開物が未完成・未検証・存在しない）は、原本を正としステージング
  ファイル・不完全な公開物を破棄してから (1) からやり直す。
- 原本が既に無く、かつ公開物も検証を通らない状態は "orphaned"
  （正本喪失）であり `ArchiveError` を送出する（本設計が想定しない状態 —
  step (4) は step (3) の rename が両ファイルとも成功した後にしか実行され
  ないため、正しい手順に従う限り到達しない）。

**閉鎖（CAMPAIGN_CLOSED）campaign には絶対に適用しない**（§V4「閉鎖 campaign
の凍結ディレクトリは不変のまま」。本モジュールは対象を選ばないため、
呼び出し側が対象 campaign を限定する責務を負う）。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from voice_genesis.calibration.provenance import Ledger

LEDGER_FILENAME = "ledger.jsonl"
GZ_FILENAME = "ledger.jsonl.gz"
SIDECAR_FILENAME = "ledger.jsonl.sha256"

#: 同一ディレクトリ内 staging 名（固定名 — 中断後の回復が同じ名前を探せる
#: ように、`tempfile` のランダムサフィックスは使わない）。
_STAGING_GZ_FILENAME = "ledger.jsonl.gz.archiving"
_STAGING_SIDECAR_FILENAME = "ledger.jsonl.sha256.archiving"

#: gzip の非決定メタデータ（mtime・元ファイル名）を固定し、同一入力から
#: 常に同一バイト列の `.gz` を作る（再現性確保。§V4 の「機械検証できる」を
#: 安定させる副次効果でもある）。
_GZIP_MTIME = 0
_GZIP_FILENAME_FIELD = ""


class ArchiveError(RuntimeError):
    """原子的置換の前提が崩れている、または検証に失敗した場合に送出する。

    fail-closed: このエラーが送出された時点で、原本 (`ledger.jsonl`) と
    検証済み公開物 (`ledger.jsonl.gz` + sidecar) のうち少なくとも一方は
    必ず無傷のまま残っている（本モジュールが呼び出し側に代わって原本を
    削除するのは、公開物の検証に成功した直後のみ）。
    """


@dataclass(frozen=True)
class ArchiveResult:
    """`ensure_archived()` の戻り値。"""

    campaign_dir: Path
    gz_path: Path
    sidecar_path: Path
    #: 非圧縮 ledger バイト列の sha256（sidecar に記録した値と同一）。
    sha256: str
    #: "archived"（本呼び出しで新規に圧縮・公開した）
    #: "already_archived"（呼び出し前から検証済み公開物があった。原本の
    #: 残存除去のみ行った可能性がある）
    action: str


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_gzip_bytes(data: bytes) -> bytes:
    """`mtime=0`・空 filename field で gzip 圧縮する（同一入力 -> 同一バイト列。
    `GzipFile` の既定 compresslevel=9 も固定しているため決定論的）。"""
    import io

    buf = io.BytesIO()
    with gzip.GzipFile(
        filename=_GZIP_FILENAME_FIELD, mode="wb", fileobj=buf, mtime=_GZIP_MTIME
    ) as gz:
        gz.write(data)
    return buf.getvalue()


def _sidecar_text(sha256_hex: str) -> str:
    """`sha256sum -c` 互換の 2 カラム形式（sha256, 半角空白 2 個, 対象
    ファイル名）。sidecar 自体は圧縮せず常に `LEDGER_FILENAME` を対象名とする
    — `zcat ledger.jsonl.gz > ledger.jsonl && sha256sum -c ledger.jsonl.sha256`
    がそのまま通る。"""
    return f"{sha256_hex}  {LEDGER_FILENAME}\n"


def _parse_sidecar(text: str) -> tuple[str, str]:
    line = text.splitlines()[0] if text.splitlines() else ""
    parts = line.split(None, 1)
    if len(parts) != 2:
        raise ArchiveError(f"malformed sidecar line: {line!r}")
    sha_hex, filename = parts
    sha_hex = sha_hex.strip()
    filename = filename.strip()
    if len(sha_hex) != 64:
        raise ArchiveError(f"malformed sidecar sha256 field: {sha_hex!r}")
    return sha_hex, filename


def _verify_gz_sidecar_pair(gz_path: Path, sidecar_path: Path) -> str:
    """`gz_path`/`sidecar_path` の組を検証する: (a) sidecar が読める・形式が
    正しい、(b) gz が実伸長できる、(c) 伸長結果の sha256 が sidecar の宣言と
    一致する、(d) 伸長結果を一時ファイルへ書いて
    `Ledger.load_with_verification()` の chain 検証が通る。

    いずれか 1 つでも欠ければ `ArchiveError` を送出する。成功時は伸長した
    非圧縮バイト列の sha256（== sidecar の宣言値）を返す。
    """
    if not gz_path.is_file():
        raise ArchiveError(f"missing gz artifact: {gz_path}")
    if not sidecar_path.is_file():
        raise ArchiveError(f"missing sidecar artifact: {sidecar_path}")

    sidecar_sha, sidecar_filename = _parse_sidecar(sidecar_path.read_text(encoding="utf-8"))
    if sidecar_filename != LEDGER_FILENAME:
        raise ArchiveError(
            f"sidecar target filename mismatch: expected {LEDGER_FILENAME!r}, "
            f"got {sidecar_filename!r}"
        )

    try:
        decompressed = gzip.decompress(gz_path.read_bytes())
    except OSError as exc:  # gzip raises OSError/BadGzipFile on corrupt input
        raise ArchiveError(f"gz artifact does not decompress: {gz_path}: {exc}") from exc

    actual_sha = _sha256_bytes(decompressed)
    if actual_sha != sidecar_sha:
        raise ArchiveError(
            f"decompressed content sha256 mismatch: sidecar declares {sidecar_sha!r}, "
            f"actual is {actual_sha!r}"
        )

    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(gz_path.parent), prefix=".archive-verify-", suffix=".jsonl"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(decompressed)
        _, chain = Ledger.load_with_verification(tmp_path)
        if not chain.ok:
            raise ArchiveError(
                f"decompressed ledger chain does not verify (ok=False, "
                f"tamper_at_seq={chain.tamper_at_seq}, truncated_tail={chain.truncated_tail})"
            )
    finally:
        tmp_path.unlink(missing_ok=True)

    return actual_sha


def _discard_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def ensure_archived(campaign_dir: Path) -> ArchiveResult:
    """§V4 の原子的置換を、中断からの回復を含めて `campaign_dir` へ適用する。

    何度呼んでも安全（既に完了していれば `action="already_archived"` を返す
    だけで、staging の破棄・原本の残存除去以外の副作用を持たない）。
    """
    campaign_dir = campaign_dir.resolve()
    ledger_path = campaign_dir / LEDGER_FILENAME
    gz_path = campaign_dir / GZ_FILENAME
    sidecar_path = campaign_dir / SIDECAR_FILENAME
    staging_gz_path = campaign_dir / _STAGING_GZ_FILENAME
    staging_sidecar_path = campaign_dir / _STAGING_SIDECAR_FILENAME

    has_gz = gz_path.is_file()
    has_sidecar = sidecar_path.is_file()

    # --- 回復判定: 公開物が両方あれば検証を試みる。 ---
    if has_gz and has_sidecar:
        try:
            published_sha = _verify_gz_sidecar_pair(gz_path, sidecar_path)
        except ArchiveError:
            if not ledger_path.is_file():
                # 公開物は壊れていて、原本も無い — 正本喪失。手順どおりに
                # 実行していればここには到達しない（原本削除は公開検証成功
                # の後にしか行わないため）。fail-closed で停止する。
                raise
            # 原本が正 — 壊れた公開物・残存 staging を破棄してやり直す。
            _discard_if_exists(gz_path)
            _discard_if_exists(sidecar_path)
        else:
            # 公開物は検証済み。staging の残骸と、残存していれば原本を除去
            # して完了とする（原本を消す前に必ずここで検証を通している）。
            _discard_if_exists(staging_gz_path)
            _discard_if_exists(staging_sidecar_path)
            _discard_if_exists(ledger_path)
            return ArchiveResult(
                campaign_dir=campaign_dir,
                gz_path=gz_path,
                sidecar_path=sidecar_path,
                sha256=published_sha,
                action="already_archived",
            )
    elif has_gz or has_sidecar:
        # 公開物が片方だけ — rename の途中で中断した状態。原本が正である
        # 限り、不完全な公開物は破棄してやり直す。
        if not ledger_path.is_file():
            raise ArchiveError(
                "partial published artifact found (only one of gz/sidecar present) "
                "but original ledger.jsonl is also missing — cannot recover safely: "
                f"gz={gz_path} (exists={has_gz}), sidecar={sidecar_path} (exists={has_sidecar})"
            )
        _discard_if_exists(gz_path)
        _discard_if_exists(sidecar_path)

    if not ledger_path.is_file():
        raise ArchiveError(
            f"neither a verified published artifact nor the original ledger is "
            f"present in {campaign_dir} — nothing to archive and nothing to recover"
        )

    # --- ここから (1)-(4)。原本が正であることが確定している。 ---
    _discard_if_exists(staging_gz_path)
    _discard_if_exists(staging_sidecar_path)

    original_bytes = ledger_path.read_bytes()
    original_sha256 = _sha256_bytes(original_bytes)

    # (1) staging へ gz + sidecar を書く。
    staging_gz_path.write_bytes(_write_gzip_bytes(original_bytes))
    _fsync_file(staging_gz_path)
    staging_sidecar_path.write_text(_sidecar_text(original_sha256), encoding="utf-8")
    _fsync_file(staging_sidecar_path)
    _fsync_dir(campaign_dir)

    # (2) staging を実伸長して sidecar sha・原本バイト列・chain を検証する。
    verified_sha = _verify_gz_sidecar_pair(staging_gz_path, staging_sidecar_path)
    if verified_sha != original_sha256:  # pragma: no cover - defensive only
        raise ArchiveError(
            "staging verification sha256 does not match original ledger bytes "
            f"(verified={verified_sha!r}, original={original_sha256!r})"
        )

    # (3) fsync 済みの staging を rename で公開する。
    os.rename(staging_gz_path, gz_path)
    os.rename(staging_sidecar_path, sidecar_path)
    _fsync_dir(campaign_dir)

    # 公開直後にもう一度検証する（defense-in-depth: rename 自体が壊れる
    # ことは通常想定しないが、原本削除の前提を軽く二重化しておく）。
    republished_sha = _verify_gz_sidecar_pair(gz_path, sidecar_path)
    if republished_sha != original_sha256:  # pragma: no cover - defensive only
        raise ArchiveError(
            "published artifact failed post-publish verification — refusing to "
            "delete the original ledger"
        )

    # (4) 検証済み公開物が揃った後にのみ原本を削除する。
    ledger_path.unlink()

    return ArchiveResult(
        campaign_dir=campaign_dir,
        gz_path=gz_path,
        sidecar_path=sidecar_path,
        sha256=original_sha256,
        action="archived",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m voice_genesis.calibration.tools.archive_aborted_ledger",
        description=(
            "Archive a discarded (abort-adjudicated) campaign's ledger.jsonl as a "
            "gzip + sha256 sidecar pair, atomically, per DESIGN_VG_METER_CAL_DEBT_"
            "v1.1.md §V4. Never point this at a CAMPAIGN_CLOSED campaign directory."
        ),
    )
    parser.add_argument(
        "campaign_dir",
        nargs="+",
        type=Path,
        help="one or more discarded campaign directories to archive",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    exit_code = 0
    for campaign_dir in args.campaign_dir:
        try:
            result = ensure_archived(campaign_dir)
        except ArchiveError as exc:
            print(f"FAILED {campaign_dir}: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        print(f"{result.action} {campaign_dir}: sha256={result.sha256}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "LEDGER_FILENAME",
    "GZ_FILENAME",
    "SIDECAR_FILENAME",
    "ArchiveError",
    "ArchiveResult",
    "ensure_archived",
]
