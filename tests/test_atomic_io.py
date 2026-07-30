"""tests/test_atomic_io.py — `svp_rpe.utils.atomic_io` の単体テスト。

7 箇所に散らばっていた atomic write / bundle publish 実装（`cli/observe_cmd.py`
/ `cli/recast_cmd.py` / `cli/builds_root.py` / `recast/backend.py` (2件) /
`recast/state.py` / `recast/plan.py`）を集約した先の共通実装そのものを検証する。
既存の `tests/test_recast_backend.py` の atomic publish テストと同じスタイル
（`tmp_path`、モック不使用 — 失敗注入は `monkeypatch.setattr("os.replace", ...)`
のみ、`from __future__ import annotations`、型ヒント）に合わせる。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from svp_rpe.utils.atomic_io import (
    atomic_publish_bundle,
    atomic_write_bytes,
    atomic_write_text,
)


class _InjectedBaseException(BaseException):
    """`Exception` を継承しない例外（`KeyboardInterrupt`/`SystemExit` 系の
    模擬）。rollback が `except BaseException` で確実に効くことを検証する
    ための注入専用例外 — `tests/test_recast_backend.py` と同じ道具。"""


# --- atomic_write_bytes / atomic_write_text ---------------------------------


def test_atomic_write_bytes_creates_file_and_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "output.bin"

    atomic_write_bytes(target, b"hello-bytes")

    assert target.read_bytes() == b"hello-bytes"
    # staging tempfile は残らない。
    leftovers = list(target.parent.glob("output.bin.*.tmp"))
    assert leftovers == []


def test_atomic_write_bytes_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "output.bin"
    target.write_bytes(b"old-content")

    atomic_write_bytes(target, b"new-content")

    assert target.read_bytes() == b"new-content"


def test_atomic_write_text_encodes_and_delegates(tmp_path: Path) -> None:
    target = tmp_path / "report.json"

    atomic_write_text(target, "こんにちは", encoding="utf-8")

    assert target.read_text(encoding="utf-8") == "こんにちは"


def test_atomic_write_bytes_failure_leaves_original_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き込み途中で失敗しても、既存ファイルは無傷のまま残る（部分書き込みの
    半端なファイルが目的の path として観測されることはない）。"""
    target = tmp_path / "output.bin"
    target.write_bytes(b"original-content")

    def failing_replace(src: object, dst: object) -> None:
        raise OSError("simulated disk full during publish")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        atomic_write_bytes(target, b"new-content")

    assert target.read_bytes() == b"original-content"
    # staging tempfile も cleanup 済み。
    leftovers = list(tmp_path.glob("output.bin.*.tmp"))
    assert leftovers == []


def test_atomic_write_bytes_cleans_up_staging_file_when_target_never_existed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "output.bin"

    def failing_replace(src: object, dst: object) -> None:
        raise OSError("simulated failure before the file ever existed")

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError):
        atomic_write_bytes(target, b"new-content")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


# --- atomic_publish_bundle: normal write / publish ---------------------------


def test_atomic_publish_bundle_writes_all_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "bundle"

    result = atomic_publish_bundle(
        output_dir,
        {"a.json": b"a-content", "b.json": b"b-content"},
        protected_inputs=(),
    )

    assert result == output_dir
    assert (output_dir / "a.json").read_bytes() == b"a-content"
    assert (output_dir / "b.json").read_bytes() == b"b-content"


def test_atomic_publish_bundle_overwrites_existing_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    (output_dir / "a.json").write_bytes(b"old-a")
    (output_dir / "b.json").write_bytes(b"old-b")

    atomic_publish_bundle(
        output_dir, {"a.json": b"new-a", "b.json": b"new-b"}, protected_inputs=()
    )

    assert (output_dir / "a.json").read_bytes() == b"new-a"
    assert (output_dir / "b.json").read_bytes() == b"new-b"


def test_atomic_publish_bundle_removes_stale_filenames_not_in_contents(tmp_path: Path) -> None:
    output_dir = tmp_path / "takes"
    output_dir.mkdir()
    (output_dir / "take-01.wav").write_bytes(b"old-wav")
    (output_dir / "take.json").write_bytes(b"old-provenance")

    atomic_publish_bundle(
        output_dir,
        {"take-01.mp3": b"new-mp3", "take.json": b"new-provenance"},
        protected_inputs=(),
        stale_filenames=("take-01.wav", "take-01.mp3"),
    )

    assert not (output_dir / "take-01.wav").exists()
    assert (output_dir / "take-01.mp3").read_bytes() == b"new-mp3"
    assert (output_dir / "take.json").read_bytes() == b"new-provenance"


# --- atomic_publish_bundle: path traversal guard ------------------------------


@pytest.mark.parametrize(
    "bad_name", ["../victim", "/etc/passwd", "a/b", "..", ""], ids=repr
)
def test_atomic_publish_bundle_rejects_unsafe_content_name(
    tmp_path: Path, bad_name: str
) -> None:
    """`contents` のキーが `output_dir` を脱出しうる名前（相対トラバーサル /
    絶対パス / ネストしたパス区切り / 空文字 / `".."`）だと、staging への
    書き込みより前に `ValueError` で拒否する（Codex P2 review 指摘: 従来は
    `output_dir / name` の join がそのまま外部ファイルへ届き、rename 前に
    上書き・失敗時 rollback が上書き後のバイト列を復元してしまっていた）。
    外部の被害候補ファイルは無傷のまま残ることを確認する。"""
    output_dir = tmp_path / "bundle"
    victim = tmp_path / "victim"
    victim.write_bytes(b"original-victim-content")

    with pytest.raises(ValueError):
        atomic_publish_bundle(
            output_dir,
            {bad_name: b"malicious-overwrite"},
            protected_inputs=(),
        )

    assert victim.read_bytes() == b"original-victim-content"


def test_atomic_publish_bundle_rejects_unsafe_stale_filename(tmp_path: Path) -> None:
    """`stale_filenames`（削除対象）側も同じ字句検証を通す — トラバーサル名を
    渡しても、削除処理より前に `ValueError` で拒否され、外部ファイルは消えない。"""
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    victim = tmp_path / "victim"
    victim.write_bytes(b"original-victim-content")

    with pytest.raises(ValueError):
        atomic_publish_bundle(
            output_dir,
            {"a.json": b"content"},
            protected_inputs=(),
            stale_filenames=("../victim",),
        )

    assert victim.read_bytes() == b"original-victim-content"
    assert not (output_dir / "a.json").exists()


def test_atomic_publish_bundle_requires_protected_inputs_keyword(tmp_path: Path) -> None:
    """`protected_inputs` は既定値なしの keyword-only 必須引数（Codex P2
    review 指摘: 渡し忘れで衝突検出が黙って無効化されるのを fail-closed に
    防ぐ）。省略すると呼び出し時点で `TypeError` になる。"""
    output_dir = tmp_path / "bundle"

    with pytest.raises(TypeError):
        atomic_publish_bundle(output_dir, {"a.json": b"content"})  # type: ignore[call-arg]


# --- atomic_publish_bundle: collision guard -----------------------------------


def test_atomic_publish_bundle_rejects_output_colliding_with_protected_input(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "bundle"
    protected = tmp_path / "bundle" / "a.json"

    with pytest.raises(ValueError, match="collides with a protected input path"):
        atomic_publish_bundle(
            output_dir,
            {"a.json": b"content"},
            protected_inputs=[protected],
        )

    # 衝突検査は staging へ書く前に行うため、`output_dir` 自体は（他の呼び出し
    # 元と同じく無条件に）作成されるが、中には何も書かれていない。
    assert output_dir.exists()
    assert list(output_dir.iterdir()) == []


def test_atomic_publish_bundle_skips_collision_check_when_protected_inputs_empty(
    tmp_path: Path,
) -> None:
    """`protected_inputs` が空かつ `always_check_collision=False`（既定）の
    場合、衝突/`is_dir` 検査そのものをスキップする（`atomic_publish_bytes_bundle`
    / `_atomic_publish_text_bundle` の「空リストで意図的にガード無効化できる」
    設計を維持する）。既存ターゲットがディレクトリでも例外にならない —
    `os.replace` はディレクトリを snapshot 側の新規名へそのまま rename できる
    ため（destination が未存在なら directory rename も成功する)、検査なしでは
    既存ディレクトリが黙って新しい bundle ファイルへ置き換わる。"""
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    (output_dir / "a.json").mkdir()

    atomic_publish_bundle(output_dir, {"a.json": b"content"}, protected_inputs=())

    assert (output_dir / "a.json").is_file()
    assert (output_dir / "a.json").read_bytes() == b"content"


def test_atomic_publish_bundle_always_check_collision_rejects_existing_directory(
    tmp_path: Path,
) -> None:
    """`always_check_collision=True`（`cli/builds_root.py:_publish_artifacts_atomically`
    の契約）は `protected_inputs` が空でも `is_dir()` 存在チェックを行う。"""
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    (output_dir / "a.json").mkdir()

    with pytest.raises(ValueError, match="existing directory"):
        atomic_publish_bundle(
            output_dir,
            {"a.json": b"content"},
            protected_inputs=(),
            always_check_collision=True,
        )


# --- atomic_publish_bundle: rollback on failure -------------------------------


def test_atomic_publish_bundle_partial_failure_restores_old_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    (output_dir / "a.json").write_bytes(b"old-a")
    (output_dir / "b.json").write_bytes(b"old-b")

    real_replace = os.replace
    calls = {"failed_once": False}

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst) == str(output_dir / "b.json") and not calls["failed_once"]:
            calls["failed_once"] = True
            raise OSError("simulated disk full while publishing b.json")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    with pytest.raises(OSError):
        atomic_publish_bundle(
            output_dir,
            {"a.json": b"new-a", "b.json": b"new-b"},
            protected_inputs=[tmp_path / "unrelated-input.txt"],
        )

    # a.json 側の rename は成功していたが、b.json 側の失敗を受けて両方とも
    # 旧内容へロールバックされている(新 a だけが生き残らない)。
    assert (output_dir / "a.json").read_bytes() == b"old-a"
    assert (output_dir / "b.json").read_bytes() == b"old-b"


def test_atomic_publish_bundle_base_exception_during_rename_still_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    (output_dir / "a.json").write_bytes(b"old-a")
    (output_dir / "b.json").write_bytes(b"old-b")

    real_replace = os.replace
    calls = {"failed_once": False}

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst) == str(output_dir / "b.json") and not calls["failed_once"]:
            calls["failed_once"] = True
            raise _InjectedBaseException("simulated KeyboardInterrupt-like interruption")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    with pytest.raises(_InjectedBaseException):
        atomic_publish_bundle(
            output_dir,
            {"a.json": b"new-a", "b.json": b"new-b"},
            protected_inputs=[tmp_path / "unrelated-input.txt"],
        )

    assert (output_dir / "a.json").read_bytes() == b"old-a"
    assert (output_dir / "b.json").read_bytes() == b"old-b"


def test_atomic_publish_bundle_catch_oserror_only_lets_base_exception_propagate_without_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`catch=OSError`（`cli/builds_root.py:_publish_artifacts_atomically` の
    契約）は非 `OSError` の中断は rollback せずそのまま伝播させる — つまり
    `catch` パラメータが実際に呼び出し元ごとの拾う例外型を切り替えている
    ことを検証する。"""
    output_dir = tmp_path / "bundle"
    output_dir.mkdir()
    (output_dir / "a.json").write_bytes(b"old-a")
    (output_dir / "b.json").write_bytes(b"old-b")

    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        if str(dst) == str(output_dir / "b.json"):
            raise _InjectedBaseException("simulated interruption not caught by catch=OSError")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    with pytest.raises(_InjectedBaseException):
        atomic_publish_bundle(
            output_dir,
            {"a.json": b"new-a", "b.json": b"new-b"},
            protected_inputs=(),
            catch=OSError,
        )

    # rollback しない契約なので a.json は new 側へ publish 済みのまま残る。
    assert (output_dir / "a.json").read_bytes() == b"new-a"
