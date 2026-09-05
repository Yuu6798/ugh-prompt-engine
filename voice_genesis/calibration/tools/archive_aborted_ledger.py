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
の凍結ディレクトリは不変のまま」）。

R9 fix（PR #346 round 9、`[UNDERSPEC-CAL-D79]` 系）: 上記は従来「呼び出し側が
対象 campaign を限定する責務を負う」という運用契約のみで、本モジュール自体は
対象を選ばなかった。誤って closed campaign に対して呼ばれた場合の
fail-closed を実装で保証するため、`ensure_archived()` は原本
（`ledger.jsonl`。存在する場合は常にこの分岐が最初に走る）または既に検証済みの
公開物（`ledger.jsonl.gz`。原本が既に無い、または壊れている場合のみ）の
どちらかに `kind == "campaign_closed"` の event を見つけた時点で、**一切の
書き込み・削除を行う前に** `ArchiveError` を送出する（後者の分岐で原本が
既に無い/壊れている場合は、検証済み gz から原本を復元してから停止する —
closed campaign の「凍結ディレクトリは不変のまま」という前提を能動的に
回復する）。

R10 fix（PR #346 round 10、Codex P1 採用）: 上記の復元は従来
`Path.write_bytes()` の直書きだったため、復元処理自体が kill されると
truncated な `ledger.jsonl` が残り得た——次回起動時は「原本が存在する」
ため復元分岐に入らず、truncated な原本がそのまま正典として扱われる詰み
状態になっていた。復元も (1) staging ファイル（`ledger.jsonl.restoring`）
へ書く → (2) fsync → (3) `os.rename` で原本パスへ公開、の 3 段で原子化した
（既存の gz/sidecar staging と同じ規約）。回復規則も追補: 起動時に
`ledger.jsonl.restoring` staging 残骸があれば無条件で破棄してからやり直す。
さらに、gz+sidecar が検証を通り closed event を含む場合、原本が**存在は
するが**検証済み gz の sha256 と一致しない（＝上記の truncated 復元残骸、
または他要因での破損）ケースも「復元すべき原本欠落」と同一に扱い、
staging 経由で再復元する（従来は「原本が存在する」の一点のみで復元をスキップ
しており、この壊れた原本ケースを回復できなかった）。

R12 fix（PR #346 round 12、Codex 採用）: R11 は「公開済み gz+sidecar が
揃った後・原本削除前 (4) に中断」した状態からの**回復**（次回起動時の再
判定）は塞いだが、フレッシュな archive 1 回の実行そのもの——原本 bytes
読み取り (`original_bytes = ledger_path.read_bytes()`) から staging 書込・
検証・公開・原本 `unlink()` に至る一続きの区間——は `provenance.Ledger.
append()` と排他制御を共有しておらず、この区間の途中で他プロセス/他
`Ledger` インスタンスが append すると、その追記分は本モジュールの誰にも
検出されないまま最後の `unlink()` で恒久喪失し得た（R11 が塞いだ「削除
直前の中断からの回復」より広い、「読み取り開始 〜 削除」全体の窓）。

本 fix は (a) この区間全体を `_ledger_write_lock()`（当時: `ledger_path`
自身の fd 上の `fcntl.flock(LOCK_EX)`——`Ledger.append()` が使うのと同一の
ロック対象・同一の blocking 契約。**R14 でロック対象を変更、下記参照**）で
保持し、区間中の `Ledger.append()` を待たせる（フレッシュ archive はロック
外側の他プロセスに対して透過的に直列化される）ことで race そのものを閉じ、
(b) 原本 `unlink()` の直前に
もう一段、原本の sha256 を publish 直後の snapshot と再照合する防御的
二重チェックを加える——ロック契約に従わない直接書き込み（本設計が保護
対象外と宣言する「台帳の外側で動く敵対的な実行者」を除けば通常発生し
得ないが、bug/運用ミスに対する belt-and-suspenders）を検出するため。
再照合が食い違った場合は R11 が既に定義した唯一の判定規則
（`_reconcile_diverged_original()`——byte-prefix 拡張 + chain 検証なら
その内容で archive を作り直す、そうでなければ `ArchiveError` で両方を
保全する）へ合流させる（二重規則を作らない）。

ロック取得の待ち方針（blocking、タイムアウトなし）は `Ledger.append()`
自身の流儀（`provenance.py` 参照: `fcntl.flock(LOCK_EX)` を `LOCK_NB` 無し
で呼ぶ）にそのまま合わせた——archive は破棄裁定済み campaign への短時間の
一括後処理であり、恣意的なタイムアウト値を新設するより、単に待たせて
安全側に倒す方が単純かつ一貫している。

R14 fix（PR #346 round 14、Codex 採用, "Coordinate appenders before
unlinking the locked inode"）: R12 の `_ledger_write_lock()` は
`ledger_path`（`ledger.jsonl`）自身の fd に `flock` していた。この
ロック対象は本モジュールが同じ区間の最後で `ledger_path` 自身を
`unlink()` する運用と衝突する: `provenance.Ledger.append()` は
`self.path`（`ledger_path` と同一パス）を `open()` してからその fd に
`flock` を要求していたため、archiver がロック保持中に appender が
`open()` すると、appender は unlink 前の旧 inode の fd で flock 待ちに
入り、archiver の `unlink()` でパス名が消えた**後**にこの flock を
獲得してしまう——その後 appender が書き込む内容は、gz snapshot にも
ファイルシステム上のどのパスにも存在しない、切り離された inode へ行かれ、
entry が恒久的に失われる（R12 が閉じた「読み取り開始〜削除」の窓の
**内側**に潜んでいた、ロック対象そのものの欠陥）。

本 fix は `_ledger_write_lock()` のロック対象を `ledger_path` 自身から
同ディレクトリの安定した専用ロックファイル（`_ledger_lock_path()`:
`ledger_path.parent / (ledger_path.name + ".lock")`。`ledger_path` 自身
とは異なり、本モジュールも `Ledger.append()` も一切 unlink/rename しない）
へ変更した。`provenance.Ledger.append()` 側も同じ計算式でロック対象を
決定し、**ledger 本体を open する前に** このロックを取得するよう改めた
（`provenance.py` の `Ledger.append()` docstring 参照）。両者が同じ安定
ファイルを、ledger 本体を触る前に先に取り合うことで、「unlink 済みの
inode で flock を獲得する」という経路自体が構造的に存在しなくなる——
R12 の (a)(b) の枠組み（区間全体の排他 + 削除直前の sha 再照合）は
そのまま維持し、ロック対象のみを差し替える。

R15 fix（PR #346 round 15、Codex 採用, "Lock recovery before reading the
residual ledger"）: R12/R14 が排他化したのは `ensure_archived()` の
「原本 bytes 読み取り 〜 原本 unlink」区間（(1)-(4)。**フレッシュ archive
経路**）のみだった。検証済み公開物 (`ledger.jsonl.gz` + sidecar) が既に
揃っている場合の**回復分岐**——残存する `ledger.jsonl` を読み、その
sha256 を公開済み sidecar の宣言値と照合し、食い違えば
`_reconcile_diverged_original()` で byte-prefix 拡張か判定して再 archive
するか、一致すれば単に `unlink()` する——はロックを取得せずに実行されて
いた。この分岐の読み取り開始（sha 照合の起点）から `unlink()`/再 archive
の完了までの間に `Ledger.append()` が割り込むと、新規 entry が (a) sha
一致判定を素通りしたまま無条件 `unlink()` に巻き込まれて恒久喪失するか、
(b) 乖離として検出されても `_reconcile_diverged_original()` が読んだ
snapshot に含まれず、その再 archive 後に古い gz へ置換されてしまう
（append 自体は生き残るが、直後に snapshot ベースの gz で上書きされ、
gz 側からは見えなくなる）——という、R12 が閉じたはずの「読み取り〜削除」
race の**回復経路版**が未閉塞のまま残っていた。

本 fix は `_ledger_write_lock(ledger_path)` の取得位置を `ensure_archived()`
の入口——closed-campaign 検査の直後、`gz_path`/`sidecar_path`/`ledger_path`
を読む前——に統一し、回復判定ブロックと (1)-(4) のフレッシュ archive
ブロックの両方を単一の `with` の下に置いた（(1)-(4) 側が独自に持っていた
内側の `with _ledger_write_lock(ledger_path):` は、同一ロックファイルへの
二重 `flock` が自己デッドロックするため削除し、外側の 1 箇所に統合）。
`Ledger.append()` 側は R14 により「ledger 本体を open する前に」同じ安定
ロックを取得する契約になっているため、この統一によって
`ensure_archived()` がどちらの分岐（回復・フレッシュ archive）を辿るに
せよ、append は「ロック区間の外側で完全に先行して完了し、この関数の
どの読み取りにも既に反映されている」か「この関数の判定・削除がすべて
確定してロックが解放されるまで待たされる」のいずれかに必ず整列する。

R17 fix（PR #346 round 17、Codex 採用, "Lock before checking for campaign
closure"）: R15 は回復判定ブロックと (1)-(4) のフレッシュ archive
ブロックの両方をロック区間へ統合したが、その直前に置かれた
closed-campaign 検査（本 docstring の R9 fix 節）自体は、統合後もなお
`_ledger_write_lock()` を取得する前に `ledger_path.read_bytes()` を実行
していた。この検査が「closed ではない」と判定した直後・ロック獲得前の
一瞬に、別スレッド/プロセスの `Ledger.append()` が `campaign_closed`
event を追記すると、以後の回復判定・snapshot・検証・公開・unlink は
すべてロック内で直列化されているにも関わらず、closed 化した campaign を
そのまま archive してしまう——R9 が塞いだはずの「closed campaign を
誤って archive する」窓が、ロック取得前のこの検査自体にだけ再び開いて
いた。

本 fix は `_ledger_write_lock(ledger_path)` の取得位置をこの closed-
campaign 検査より前に繰り上げ、検査・回復判定・snapshot・検証・公開・
unlink の全区間を単一の `with` の下に統合した。`Ledger.append()` は
R14 により ledger 本体を open する前に同じ安定ロックの獲得を待たされる
契約になっているため、この検査自体も「append が完全に先行して完了し
反映された状態」か「この関数の判定・削除がすべて確定してロックが解放
されるまで待たされた状態」のいずれかでしか実行されなくなる。
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from voice_genesis.calibration.provenance import Ledger

LEDGER_FILENAME = "ledger.jsonl"
GZ_FILENAME = "ledger.jsonl.gz"
SIDECAR_FILENAME = "ledger.jsonl.sha256"

#: 同一ディレクトリ内 staging 名（固定名 — 中断後の回復が同じ名前を探せる
#: ように、`tempfile` のランダムサフィックスは使わない）。
_STAGING_GZ_FILENAME = "ledger.jsonl.gz.archiving"
_STAGING_SIDECAR_FILENAME = "ledger.jsonl.sha256.archiving"
#: R10 fix: 検証済み gz からの原本復元（`_restore_original_from_verified_gz()`）
#: も同じ「同一ディレクトリ内固定名 staging → fsync → rename」規約で原子化
#: する（上記 2 つと同型）。
_STAGING_LEDGER_FILENAME = "ledger.jsonl.restoring"

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


def _ledger_lock_path(ledger_path: Path) -> Path:
    """`ledger_path` に対応する安定ロックファイルのパス（同一ディレクトリ、
    固定名 `<ledger名>.lock`）。`provenance.Ledger.append()` が使うのと
    完全に同じ計算式——`self.path.parent / (self.path.name + ".lock")`
    （R14 fix）——をここでも独立実装する。ロック対象が一致していることが
    唯一の正当性根拠であり、`LEDGER_FILENAME` を経由する限り両モジュールは
    常に同じファイル名（`ledger.jsonl.lock`）を導出する。"""
    return ledger_path.parent / (ledger_path.name + ".lock")


@contextlib.contextmanager
def _ledger_write_lock(ledger_path: Path) -> Iterator[None]:
    """R14 fix (Codex PR #346 round 14 採用, "Coordinate appenders before
    unlinking the locked inode"): `provenance.Ledger.append()` と同一の
    安定ロックファイル（`_ledger_lock_path(ledger_path)`。`ledger_path`
    自身ではない）上での `fcntl.flock(LOCK_EX)`、`LOCK_NB` 無しの blocking
    待ち——を取得し、`with` ブロックを抜けるまで保持する。

    R12 時点の実装は `ledger_path` 自身の fd に `flock` していた。この
    ロック対象は archiver が本関数の `with` ブロック終端で `ledger_path`
    自身を `unlink()` する運用と衝突する: `Ledger.append()` 側が
    `ledger_path` を（unlink 前なので同一 inode で）`open()` した直後に
    archiver がロックを保持したまま `unlink()` すると、その appender は
    旧 inode の fd で flock 待ちに入り、unlink でパス名が消えた**後**に
    flock を獲得してしまう——書き込みは gz snapshot にもファイルシステム上
    のどのパスにも存在しない、切り離された inode へ行われ、entry が恒久的
    に失われる（round 14 指摘）。

    本 fix は、ロック対象を `ledger_path` 自身ではなく **同ディレクトリの
    別ファイル**（archiver も `Ledger.append()` も一切 unlink/rename しない
    専用ロックファイル）に変更し、かつ両者とも **ledger 本体を open する
    前に** このロックを取得することで、この区間の競合を構造的に閉じる:
    archiver がこのロックを保持している間、appender は `ledger_path` を
    open する前にまず同じロックの獲得を待たされる（`ledger_path` がまだ
    unlink されていない古い inode を掴んでから待たされることがない）ため、
    「unlink 済みの inode で flock を獲得する」経路が存在しなくなる。

    `ledger_path` は呼び出し時点で存在している前提（呼び出し側は原本の
    存在を既に確認済みであること）は変わらない。ロックファイル自体は
    `ledger_path` の削除や置換に一切連動せず、`ensure_archived()` の
    実行をまたいで残り続ける（`c0_freeze.py` の `.publish.lock` と同じ
    「常設の専用ロックファイル」規約）。"""
    lock_path = _ledger_lock_path(ledger_path)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _reconcile_diverged_original(
    *,
    campaign_dir: Path,
    ledger_path: Path,
    gz_path: Path,
    sidecar_path: Path,
    staging_gz_path: Path,
    staging_sidecar_path: Path,
    leftover_bytes: bytes,
    leftover_sha: str,
    published_bytes: bytes,
    published_sha: str,
) -> str:
    """R11 が定義した唯一の判定規則（PR #346 round 11 採用）: 現在 on-disk の
    原本 (`leftover_bytes`) が、既に検証済み/直前に公開した archive の中身
    (`published_bytes`) と食い違う場合にどう扱うか。

    R12（round 12）はこの規則を、フレッシュ archive 実行中の削除直前
    再照合（`ensure_archived()` 本体）とも共有する——同じ状況に 2 つの
    規則を作らない。

    `leftover_bytes` が `published_bytes` の chain-検証済み・厳密な
    byte-prefix 拡張であれば、それを正として archive を作り直し（gz+sidecar
    を新しい内容で置換）、新しい sha256 を返す。それ以外（改竄・無関係な
    ledger との取り違え）は `ArchiveError` を送出する——呼び出し側は原本・
    公開物のどちらも削除・上書きしてはならない（fail-closed、§V4 R11）。
    """
    is_extension = _is_strict_prefix_extension(leftover_bytes, published_bytes)
    chain_ok = False
    if is_extension:
        try:
            _, leftover_chain = Ledger.load_with_verification(ledger_path)
        except Exception:  # noqa: BLE001 - 検証不能は re-archive しない
            chain_ok = False
        else:
            chain_ok = leftover_chain.ok
    if is_extension and chain_ok:
        _discard_if_exists(staging_gz_path)
        _discard_if_exists(staging_sidecar_path)
        return _write_and_publish_archive(
            leftover_bytes,
            campaign_dir,
            gz_path,
            sidecar_path,
            staging_gz_path,
            staging_sidecar_path,
        )
    raise ArchiveError(
        f"{campaign_dir}: leftover ledger.jsonl (sha256={leftover_sha!r}) "
        f"does not match the verified published archive "
        f"(sha256={published_sha!r}) and is not a valid, chain-verified, "
        "strict byte-prefix extension of it — refusing to delete or "
        "overwrite either side (fail-closed, §V4 R11)"
    )


def _is_strict_prefix_extension(candidate: bytes, published: bytes) -> bool:
    """`candidate` が `published` の byte-exact prefix を持ち、かつ真に長いか
    （R11 fix 用 — `ensure_archived()` 参照）。

    JSONL は行区切りのため、`published` 自体が publish 時点で既に
    chain-検証済み（well-formed）であれば、その先頭バイト列と byte-exact に
    一致する `candidate` の対応区間の行境界も同一になる。よって、この
    prefix 判定に加えて `candidate` 全体の chain 検証（呼び出し側で行う）が
    通れば、`candidate` は `published` の全 entry をそのまま含んだ上で
    entry を追加した厳密な拡張だと判断してよい（追加のエントリ数比較は
    不要）。"""
    return len(candidate) > len(published) and candidate[: len(published)] == published


def _write_and_publish_archive(
    data: bytes,
    campaign_dir: Path,
    gz_path: Path,
    sidecar_path: Path,
    staging_gz_path: Path,
    staging_sidecar_path: Path,
) -> str:
    """`data`（非圧縮 ledger バイト列）を §V4 の 3 段階（staging へ書く →
    実伸長して検証 → fsync 済み staging を rename で公開）で gz+sidecar と
    して原子的に公開する。`gz_path`/`sidecar_path` に既存の公開物があれば
    `os.rename` がそのまま置換する（R11 fix の re-archive 経路が既存の
    stale な公開物を置き換えるのに使う）。公開直後にもう一度検証してから
    非圧縮バイト列の sha256 を返す。呼び出し側は、この関数が正常終了した
    後にのみ `data` の出所（原本ファイル等）を削除してよい。"""
    sha256_hex = _sha256_bytes(data)

    staging_gz_path.write_bytes(_write_gzip_bytes(data))
    _fsync_file(staging_gz_path)
    staging_sidecar_path.write_text(_sidecar_text(sha256_hex), encoding="utf-8")
    _fsync_file(staging_sidecar_path)
    _fsync_dir(campaign_dir)

    verified_sha = _verify_gz_sidecar_pair(staging_gz_path, staging_sidecar_path)
    if verified_sha != sha256_hex:  # pragma: no cover - defensive only
        raise ArchiveError(
            "staging verification sha256 does not match source bytes "
            f"(verified={verified_sha!r}, source={sha256_hex!r})"
        )

    os.rename(staging_gz_path, gz_path)
    os.rename(staging_sidecar_path, sidecar_path)
    _fsync_dir(campaign_dir)

    republished_sha = _verify_gz_sidecar_pair(gz_path, sidecar_path)
    if republished_sha != sha256_hex:  # pragma: no cover - defensive only
        raise ArchiveError(
            "published artifact failed post-publish verification — refusing to "
            "delete the original ledger"
        )
    return sha256_hex


def _ledger_bytes_contain_campaign_closed(data: bytes) -> bool:
    """R9 fix (PR #346 round 9): does decompressed/raw `ledger.jsonl`
    content `data` contain a `kind == "campaign_closed"` event anywhere?

    A closed campaign's ledger is the immutable canonical record (module
    docstring) and must never be archived. This is a lightweight,
    line-by-line JSON scan — not a full `Ledger`/chain load — because it
    must run before any staging write even exists yet (see the call site at
    the top of `ensure_archived()`), and because a malformed/unparseable
    line here is not this function's concern (every other consumer of this
    same content — `_verify_gz_sidecar_pair()`'s `Ledger.load_with_
    verification()` call, or a subsequent normal archive run — already
    fail-closes on structural corruption independently; silently skipping
    an unparseable line here can only ever miss a `campaign_closed` marker
    that a later, stricter parse would also choke on, never accept one that
    should have been rejected).
    """
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        payload = raw.get("payload")
        if isinstance(payload, dict) and payload.get("kind") == "campaign_closed":
            return True
    return False


def _restore_original_from_verified_gz(
    ledger_path: Path,
    gz_path: Path,
    campaign_dir: Path,
    staging_ledger_path: Path,
    *,
    decompressed: bytes | None = None,
) -> None:
    """R9 fix: `ledger_path` is missing or corrupt (a prior, already-completed
    archive — from before this closed-campaign guard existed, a manual
    deletion, or R10's truncated-restore-residue case below) but the
    verified public `gz_path` turns out to hold a `campaign_closed` ledger.
    Restore the original from the gz's own decompressed bytes (already
    sidecar-sha256-verified by the caller) so the directory returns to
    having its canonical, unarchived original present — the closed-campaign
    invariant this module must never violate — before the caller raises
    `ArchiveError` and stops.

    R10 fix (PR #346 round 10, Codex P1 採用): the restore itself must be
    atomic — a prior direct `Path.write_bytes(ledger_path, ...)` could be
    killed mid-write and leave a truncated `ledger.jsonl` in place, which
    the top-of-function guard would then treat as "the original exists" on
    the next run and refuse to restore further (a stuck state requiring
    manual deletion). This now follows the same 3-step protocol as the
    gz/sidecar staging above: (1) write to a same-directory staging path,
    (2) fsync the staging file and the directory, (3) `os.rename()` onto
    `ledger_path` (atomic within the same filesystem) and fsync the
    directory again so the rename itself is durable.
    """
    if decompressed is None:
        decompressed = gzip.decompress(gz_path.read_bytes())
    staging_ledger_path.write_bytes(decompressed)
    _fsync_file(staging_ledger_path)
    _fsync_dir(campaign_dir)
    os.rename(staging_ledger_path, ledger_path)
    _fsync_dir(campaign_dir)


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
    staging_ledger_path = campaign_dir / _STAGING_LEDGER_FILENAME

    # R10 fix: discard any leftover restore-staging residue unconditionally,
    # first — a prior `_restore_original_from_verified_gz()` call that was
    # interrupted between writing the staging file and the `os.rename()`
    # leaves this behind. It is never itself read for any decision below
    # (the restore always regenerates it from the verified gz), so
    # discarding it here is a pure no-op when absent and a safe "redo from
    # scratch" when present (same staging convention as the gz/sidecar
    # archiving staging discarded further below).
    _discard_if_exists(staging_ledger_path)

    # R17 fix (PR #346 round 17, Codex 採用, "Lock before checking for
    # campaign closure"): R15 は回復判定ブロックと (1)-(4) のフレッシュ
    # archive ブロックの両方をロック区間へ統合したが、その直前に置かれた
    # closed-campaign 検査自体は `_ledger_write_lock()` を取得する前に
    # `ledger_path.read_bytes()` を実行していた。この検査が「closed では
    # ない」と判定した直後、ロック獲得前の一瞬に別スレッド/プロセスの
    # `Ledger.append()` が `campaign_closed` event を追記すると、以後の
    # 回復判定・snapshot・検証・公開・unlink はすべてロック内で直列化
    # されているにも関わらず、closed 化した campaign をそのまま archive
    # してしまう——R9 が塞いだはずの「closed campaign を誤って archive
    # する」窓が、ロック取得前のこの検査自体にだけ再び開いていた。
    #
    # 本 fix は `_ledger_write_lock()` の取得位置をこの検査より前に移し、
    # closed-campaign 検査・回復判定・snapshot・検証・公開・unlink の
    # 全区間を単一の `with` の下に統合した。`Ledger.append()` は R14 に
    # より ledger 本体を open する前に同じ安定ロックの獲得を待たされる
    # ため、この検査自体も「append が完全に先行して完了し反映された
    # 状態」か「この関数の判定・削除がすべて確定してロックが解放される
    # まで待たされた状態」のいずれかでしか実行されなくなる。
    with _ledger_write_lock(ledger_path):
        # R9 fix (PR #346 round 9 採用): a CLOSED campaign's ledger is the
        # immutable canonical record (module docstring) and must never be
        # archived. Checked here, first, before any of the recovery-branch
        # logic below runs and before any write/staging/delete this function
        # could otherwise perform — a caller mistake (pointing this tool at a
        # still-canonical closed campaign whose original is still present)
        # fails closed with the directory completely untouched. R17 fix:
        # this read now happens under the stable lock acquired above — see
        # the R17 fix note above this `with` for why an unlocked read here
        # was unsafe.
        if ledger_path.is_file() and _ledger_bytes_contain_campaign_closed(
            ledger_path.read_bytes()
        ):
            raise ArchiveError(
                f"{campaign_dir}: ledger.jsonl contains a campaign_closed event — "
                "closed campaigns are immutable canonical records and must never "
                "be archived"
            )

        # R15 fix (PR #346 round 15, Codex 採用, "Lock recovery before reading
        # the residual ledger"): 安定ロック（`_ledger_write_lock()`。R14 で
        # `Ledger.append()` と共有するよう変更した同一ロック）の下で、以下の
        # 回復判定ブロックと (1)-(4) のフレッシュ archive ブロックの
        # **両方**を実行する（R17 でロック取得位置を上記 closed-campaign
        # 検査の前まで繰り上げたため、この検査も同じロック区間に含まれる）。
        #
        # R12/R14 が排他化したのは (1)-(4) の区間（原本 bytes 読み取り〜
        # 原本 unlink）のみで、回復判定ブロック（検証済み公開物が既にある場合の
        # 残存 `ledger.jsonl` 読取・sha 照合・`_reconcile_diverged_original()`
        # 経由の再 archive・`unlink`）はロック外で実行されていた。この区間の
        # 途中で `Ledger.append()` が割り込むと、(a) 追記が sha 不一致として
        # 検出されないまま無条件 `unlink()` に飲まれて恒久喪失するか、
        # (b) 検出されて `_reconcile_diverged_original()` が再 archive を
        # 始めた直後に別の追記が来て、その新規追記が再 archive の snapshot にも
        # 含まれず次の `unlink()` で失われる、という R12 と同型の race が
        # 回復経路にも存在した。
        #
        # ロック取得位置を関数入口（closed-campaign 検査の前。R17）に統一
        # することで、以後の closed-campaign 検査・回復判定・再 archive・
        # (1)-(4) のフレッシュ archive は常にこの単一のロック保持区間の
        # 内側で直列に実行される——`Ledger.append()` は `ledger_path` を
        # open する前に同じロックの獲得を待たされるため、この関数がどの
        # 分岐を辿るにせよ、append は「完全に先行して完了し読み取りに
        # 反映される」か「この関数の判定・削除がすべて確定してから実行
        # される」のいずれかに必ず整列する（正典喪失ゼロ）。同一ロック
        # ファイルへの二重 `flock` は自己デッドロックするため、旧来 (1)-(4)
        # だけを囲んでいた内側の `with _ledger_write_lock(ledger_path):` は
        # 削除し、この外側の `with` 一つに統合した。
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
                # R9 fix: defense-in-depth against a verified public gz/sidecar
                # pair whose ledger is actually a CLOSED campaign's. Reachable
                # both when `ledger_path` no longer exists (the top-of-function
                # guard above already caught every case where an intact original
                # is present) and — R10 fix — when it exists but is corrupt
                # (truncated restore residue from a prior killed
                # `_restore_original_from_verified_gz()` run, or other damage):
                # such a stale original would otherwise silently pass as "the
                # original exists" forever, permanently blocking recovery.
                # `gz_path`'s bytes are already sidecar-sha256- and
                # chain-verified by `_verify_gz_sidecar_pair()` above, so
                # decompressing them again here is safe to trust.
                decompressed_gz = gzip.decompress(gz_path.read_bytes())
                if _ledger_bytes_contain_campaign_closed(decompressed_gz):
                    original_missing = not ledger_path.is_file()
                    original_corrupt = not original_missing and (
                        _sha256_bytes(ledger_path.read_bytes()) != published_sha
                    )
                    restored = original_missing or original_corrupt
                    if restored:
                        _restore_original_from_verified_gz(
                            ledger_path,
                            gz_path,
                            campaign_dir,
                            staging_ledger_path,
                            decompressed=decompressed_gz,
                        )
                    restore_note = ""
                    if original_corrupt:
                        restore_note = (
                            " (original was present but did not match the verified "
                            "archive's sha256 — treated as broken restore residue and "
                            "replaced from the verified archive)"
                        )
                    elif restored:
                        restore_note = " (original restored from the verified archive)"
                    raise ArchiveError(
                        f"{campaign_dir}: archived ledger.jsonl.gz contains a "
                        "campaign_closed event — closed campaigns are immutable "
                        "canonical records and must never be archived; refusing "
                        "to remove anything further" + restore_note
                    )
                # R11 fix (PR #346 round 11 採用, 2026-09-05): 公開済み gz+sidecar
                # が揃った後・原本削除前 (4) に中断し、その後残存 `ledger.jsonl`
                # へ追記があった場合、修正前はここで無条件 `unlink` していたため
                # 追記分が恒久喪失した。原本を消す前に、残存原本の sha256 を
                # 公開済み sidecar の sha256 と突き合わせる。
                if ledger_path.is_file():
                    leftover_bytes = ledger_path.read_bytes()
                    leftover_sha = _sha256_bytes(leftover_bytes)
                else:
                    leftover_bytes = None
                    leftover_sha = None

                if leftover_sha is not None and leftover_sha != published_sha:
                    # 一致しない — 残存原本が公開済み archive から乖離している。
                    # (i) 残存原本の chain 検証を行い、(ii) 公開済み ledger の
                    # 厳密な byte-prefix 拡張（先頭部分が byte 一致・真に長い）
                    # であることの両方を満たす場合に限り、追記が正当な続きだと
                    # みなして原本を正とし archive を作り直す（staging → 検証 →
                    # rename で既存の gz/sidecar を置換）。それ以外（不整合 —
                    # 改竄・無関係な ledger との取り違え等）は何も削除せず
                    # `ArchiveError` で停止し、両方を保全する。
                    new_sha = _reconcile_diverged_original(
                        campaign_dir=campaign_dir,
                        ledger_path=ledger_path,
                        gz_path=gz_path,
                        sidecar_path=sidecar_path,
                        staging_gz_path=staging_gz_path,
                        staging_sidecar_path=staging_sidecar_path,
                        leftover_bytes=leftover_bytes,
                        leftover_sha=leftover_sha,
                        published_bytes=decompressed_gz,
                        published_sha=published_sha,
                    )
                    ledger_path.unlink()
                    return ArchiveResult(
                        campaign_dir=campaign_dir,
                        gz_path=gz_path,
                        sidecar_path=sidecar_path,
                        sha256=new_sha,
                        action="archived",
                    )

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
        # R12 fix: 原本 bytes 読み取り → staging → 検証 → 公開 → 原本 unlink の
        # 一連を、`Ledger.append()` と同一の排他ロック下で実行する（この区間の
        # 途中で他の `Ledger.append()` が割り込んで書いた内容を、検出しないまま
        # 最後の `unlink()` で恒久喪失することを防ぐ。モジュール docstring の
        # R12 fix 節参照）。R15 fix: このロックは関数入口の外側 `with` で
        # 既に取得済みのため、ここでの再取得は行わない。
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

        # R12 fix: 原本削除の直前に、保持中のロックの下でもう一度原本の
        # sha256 を再照合する（防御的二重チェック。上記ロックにより
        # `Ledger.append()` 経由の書き込みはこの区間で発生し得ないはずだが、
        # ロック契約に従わない直接書き込みがあっても、削除だけは常にこの
        # 再照合を通してから行う）。食い違えば R11 が定義した唯一の判定規則
        # (`_reconcile_diverged_original`) に合流させる——二重規則を作らない。
        current_bytes = ledger_path.read_bytes()
        current_sha256 = _sha256_bytes(current_bytes)
        if current_sha256 != original_sha256:
            final_sha256 = _reconcile_diverged_original(
                campaign_dir=campaign_dir,
                ledger_path=ledger_path,
                gz_path=gz_path,
                sidecar_path=sidecar_path,
                staging_gz_path=staging_gz_path,
                staging_sidecar_path=staging_sidecar_path,
                leftover_bytes=current_bytes,
                leftover_sha=current_sha256,
                published_bytes=original_bytes,
                published_sha=original_sha256,
            )
        else:
            final_sha256 = original_sha256

        # (4) 検証済み公開物が揃った後にのみ原本を削除する。
        ledger_path.unlink()

    return ArchiveResult(
        campaign_dir=campaign_dir,
        gz_path=gz_path,
        sidecar_path=sidecar_path,
        sha256=final_sha256,
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
