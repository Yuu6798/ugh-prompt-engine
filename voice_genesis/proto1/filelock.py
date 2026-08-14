"""filelock.py — O_EXCL ベースの単純なプロセス間排他ロック（PR#261 レビュー R37）。

`registry.py::GenomeRegistry.append()` / `reference_set.py::LinkabilityAuditLog.
append()` / `mark_stale()`（`_rewrite()` 経由）の read-modify-replace 全区間
（検証 → `load_all()` による既存全件読み込み → staging 書き込み →
`os.replace()` 置換）をプロセス間で直列化するための共通ヘルパー。

`proto1_demo.py::_publish_lock()`（R25 で正本 publish 直前の排他制御として
先行導入）と同型のパターン: `os.O_CREAT | os.O_EXCL` によるロックファイル
作成はアトミックな「無ければ作る」操作のため、既に存在すれば並行アクセス
とみなし即座に `FileLockError` で拒否する。ブロック終了時（成功・例外
いずれでも）にロックファイルを削除する。

R37 が新設された背景: append() の各種検証（R28/R30 のラウンドトリップ・
R33/R5 の重複検出等）は「検証時点での `load_all()` 結果」を前提にして
いるが、検証から実際の書き込み（staging 構築 → `os.replace()`）までの
間に別プロセスが割り込んで書き込みを完了させると、検証済みの前提
（例: 検証時点では存在しなかった genome_id/report_id が、実際の書き込み
直前には既に存在する）が古くなったまま正本を置換してしまう
read-modify-write レースが理論上あり得た。ロックで「検証 → load_all →
staging → os.replace」の区間全体を直列化することでこれを閉じる
（R25 の `_publish_lock` が正本 publish 直前の一点のみを排他したのに対し、
R37 は読み込みに基づく検証を含む区間全体を排他する点が異なる）。
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Iterator, Union


class FileLockError(RuntimeError):
    """ロックファイルが既に存在する（並行アクセスの可能性。PR#261 レビュー R37）。"""


@contextlib.contextmanager
def file_lock(lock_path: Union[str, Path]) -> Iterator[None]:
    """`lock_path` を排他ロックとして獲得する（`<正本>.lock` の命名規約を
    呼び出し元に委ねる）。既に存在すれば `FileLockError` を即座に送出する。
    """
    lock_path = Path(lock_path)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise FileLockError(
            f"ロックファイルが既に存在する（並行アクセスの可能性: {lock_path}）。"
            "別のプロセスが同時にこの正本を書き込み中でないか確認すること。前回実行が"
            "クラッシュして残置されたものであれば、内容（PID）を確認の上で手動削除して"
            "から再実行すること。"
        ) from exc
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
