"""test_gate_synth_diffsinger_repo_pin.py — review #264 R24 P2 再現テスト。

`s1_gate/gate_synth.py` の非 `--skip-export` 経路は、従来 export subprocess
完了から数百行後（synth ループ開始直前）に `diffsinger_repo` の HEAD/dirty
状態を初めて 1 回だけ読んでいた。export 実行〜この読み取りまでの間に
checkout が進む/元に戻ると、「ONNX は旧バイトから生成されたのに summary は
後から読んだ別リビジョンを pin する」偽 pin が起き得た。

修正は export subprocess 起動の直前直後で HEAD/dirty を取得し、両者が
一致することを確認してから初めて pre 値を summary の正式 pin として採用する
（不一致は `SystemExit` で fail-closed）。

本テストは実際の DiffSinger exporter・実 export subprocess は使わず、
`_git_head_and_dirty`（取得）と `_check_diffsinger_repo_stable`（照合）を
直接検証する（`cmd_run` 全体を実際の音声合成込みで動かすのは重いため、
設計判断は「pre/post 取得関数を monkeypatch して不一致時の fail-closed と
サマリ記録を固定する」— PR #264 R24 レビュー対応の Design Memo 参照）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_gate"))

import gate_synth as gs  # noqa: E402


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(repo_dir), check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo_dir), check=True)
    (repo_dir / "README.md").write_text("placeholder\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo_dir), check=True)


def test_git_head_and_dirty_reports_head_and_clean_state(tmp_path: Path) -> None:
    """クリーンな git checkout では HEAD が commit sha、dirty が False。"""
    repo_dir = tmp_path / "diffsinger_repo"
    _init_git_repo(repo_dir)
    expected_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    head, dirty = gs._git_head_and_dirty(repo_dir)

    assert head == expected_head
    assert dirty is False


def test_git_head_and_dirty_detects_dirty_working_tree(tmp_path: Path) -> None:
    """作業ツリーに未コミットの変更があれば dirty が True。"""
    repo_dir = tmp_path / "diffsinger_repo"
    _init_git_repo(repo_dir)
    (repo_dir / "README.md").write_text("modified\n", encoding="utf-8")

    _head, dirty = gs._git_head_and_dirty(repo_dir)

    assert dirty is True


def test_git_head_and_dirty_returns_unavailable_for_non_git_dir(tmp_path: Path) -> None:
    """git リポでないディレクトリでは fail-closed にせず 'unavailable' を返す
    （R20 の既存挙動を維持）。"""
    non_git_dir = tmp_path / "not_a_repo"
    non_git_dir.mkdir()

    head, dirty = gs._git_head_and_dirty(non_git_dir)

    assert head == "unavailable"
    assert dirty == "unavailable"


def test_check_diffsinger_repo_stable_accepts_matching_pre_post(tmp_path: Path) -> None:
    """pre/post の HEAD/dirty が完全一致すれば例外を送出しない（正常系）。"""
    repo_dir = tmp_path / "diffsinger_repo"
    gs._check_diffsinger_repo_stable(repo_dir, "abc123", False, "abc123", False)  # no raise


def test_check_diffsinger_repo_stable_rejects_head_change(tmp_path: Path) -> None:
    """export 中に HEAD が進んだ/元に戻った場合は fail-closed で SystemExit
    する（R24 P2 の再現対象そのもの: 偽 pin の防止）。
    """
    repo_dir = tmp_path / "diffsinger_repo"
    with pytest.raises(SystemExit):
        gs._check_diffsinger_repo_stable(repo_dir, "abc123", False, "def456", False)


def test_check_diffsinger_repo_stable_rejects_dirty_state_change(tmp_path: Path) -> None:
    """HEAD は同一でも dirty 状態が export 前後で変化した場合も fail-closed
    する（checkout が一時的に別状態を経由した可能性を否定できないため）。
    """
    repo_dir = tmp_path / "diffsinger_repo"
    with pytest.raises(SystemExit):
        gs._check_diffsinger_repo_stable(repo_dir, "abc123", False, "abc123", True)


def test_check_diffsinger_repo_stable_skips_when_pre_unavailable(tmp_path: Path) -> None:
    """pre 側が 'unavailable'（git 情報取得失敗）の場合は照合不能として
    fail-closed にはしない（R20 の既存挙動を維持 — git 情報が無くても export
    自体は継続する）。
    """
    repo_dir = tmp_path / "diffsinger_repo"
    gs._check_diffsinger_repo_stable(
        repo_dir, "unavailable", "unavailable", "abc123", False
    )  # no raise


def test_check_diffsinger_repo_stable_skips_when_post_unavailable(tmp_path: Path) -> None:
    """post 側が 'unavailable' の場合も同様に照合不能として素通りする。"""
    repo_dir = tmp_path / "diffsinger_repo"
    gs._check_diffsinger_repo_stable(
        repo_dir, "abc123", False, "unavailable", "unavailable"
    )  # no raise
