"""test_gate_synth_diffsinger_repo_pin.py — review #264 R24 P2 / R25 P2 再現テスト。

`s1_gate/gate_synth.py` の非 `--skip-export` 経路は、従来 export subprocess
完了から数百行後（synth ループ開始直前）に `diffsinger_repo` の HEAD/dirty
状態を初めて 1 回だけ読んでいた。export 実行〜この読み取りまでの間に
checkout が進む/元に戻ると、「ONNX は旧バイトから生成されたのに summary は
後から読んだ別リビジョンを pin する」偽 pin が起き得た。

修正は export subprocess 起動の直前直後で HEAD/dirty を取得し、両者が
一致することを確認してから初めて pre 値を summary の正式 pin として採用する
（不一致は `SystemExit` で fail-closed）。

review #264 R25 P2: dirty を bool 1 個へ縮約すると、export 前後で checkout が
既に dirty のまま**内容だけ**変化した（dirty→dirty のまま porcelain
status/diff 内容が変わった）ケースを pre/post 照合が見逃す
（`(HEAD, True)` が両者とも同じ値のため一致判定を素通りする）。
`git status --porcelain` の生テキスト sha256 と `git diff HEAD` の生バイト列
sha256 を追加し、これらも含めて完全一致を要求するよう強化した。

本テストは実際の DiffSinger exporter・実 export subprocess は使わず、
`_git_head_and_dirty`（取得）と `_check_diffsinger_repo_stable`（照合）を
直接検証する（`cmd_run` 全体を実際の音声合成込みで動かすのは重いため、
設計判断は「pre/post 取得関数を monkeypatch して不一致時の fail-closed と
サマリ記録を固定する」— PR #264 R24/R25 レビュー対応の Design Memo 参照）。

review #264 R27 P2 追加: `run_export_acoustic()` が canonical への swap を
自分ではもう行わず、post snapshot 取得 + 安定性検査 + swap の orchestration
は新設の `_publish_exported_acoustic()` が担う（exporter 安定性検査前に
ONNX が公開されてしまう窓を閉じる）。本ファイル末尾のテスト群がこの
orchestration を、フェイク exporter に checkout 自体を動かさせる実 git
状態遷移で固定する。
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR))
sys.path.insert(0, str(_TESTS_DIR.parent / "s1_gate"))

from _optional_runtime_stubs import stub_onnxruntime_if_missing  # noqa: E402

with stub_onnxruntime_if_missing():
    import gate_synth as gs  # noqa: E402
sys.modules.pop("gate_synth", None)


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
    """クリーンな git checkout では HEAD が commit sha、dirty が False、
    status_sha256/diff_sha256 は固定値（空 status/空 diff）になる。"""
    repo_dir = tmp_path / "diffsinger_repo"
    _init_git_repo(repo_dir)
    expected_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir),
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    head, dirty, status_sha256, diff_sha256 = gs._git_head_and_dirty(repo_dir)

    assert head == expected_head
    assert dirty is False
    assert isinstance(status_sha256, str) and len(status_sha256) == 64
    assert isinstance(diff_sha256, str) and len(diff_sha256) == 64


def test_git_head_and_dirty_detects_dirty_working_tree(tmp_path: Path) -> None:
    """作業ツリーに未コミットの変更があれば dirty が True。"""
    repo_dir = tmp_path / "diffsinger_repo"
    _init_git_repo(repo_dir)
    (repo_dir / "README.md").write_text("modified\n", encoding="utf-8")

    _head, dirty, _status_sha256, _diff_sha256 = gs._git_head_and_dirty(repo_dir)

    assert dirty is True


def test_git_head_and_dirty_detects_content_change_within_dirty_tree(tmp_path: Path) -> None:
    """review #264 R25 P2 の再現: dirty のまま**内容だけ**が変化した場合、
    dirty bool は両方とも True のまま変わらないが、status_sha256/diff_sha256
    は変化を検出できる。"""
    repo_dir = tmp_path / "diffsinger_repo"
    _init_git_repo(repo_dir)
    (repo_dir / "README.md").write_text("modified once\n", encoding="utf-8")

    _head1, dirty1, status_sha1, diff_sha1 = gs._git_head_and_dirty(repo_dir)

    (repo_dir / "README.md").write_text("modified again, different content\n", encoding="utf-8")

    _head2, dirty2, status_sha2, diff_sha2 = gs._git_head_and_dirty(repo_dir)

    assert dirty1 is True and dirty2 is True  # bool だけでは区別できない
    assert diff_sha1 != diff_sha2  # だが diff_sha256 は内容変化を検出する


def test_git_head_and_dirty_returns_unavailable_for_non_git_dir(tmp_path: Path) -> None:
    """git リポでないディレクトリでは fail-closed にせず 'unavailable' を返す
    （R20 の既存挙動を維持）。"""
    non_git_dir = tmp_path / "not_a_repo"
    non_git_dir.mkdir()

    head, dirty, status_sha256, diff_sha256 = gs._git_head_and_dirty(non_git_dir)

    assert head == "unavailable"
    assert dirty == "unavailable"
    assert status_sha256 == "unavailable"
    assert diff_sha256 == "unavailable"


def test_check_diffsinger_repo_stable_accepts_matching_pre_post(tmp_path: Path) -> None:
    """pre/post の HEAD/dirty/status_sha256/diff_sha256 が完全一致すれば
    例外を送出しない（正常系）。"""
    repo_dir = tmp_path / "diffsinger_repo"
    gs._check_diffsinger_repo_stable(
        repo_dir,
        "abc123", False, "s1", "d1",
        "abc123", False, "s1", "d1",
    )  # no raise


def test_check_diffsinger_repo_stable_rejects_head_change(tmp_path: Path) -> None:
    """export 中に HEAD が進んだ/元に戻った場合は fail-closed で SystemExit
    する（R24 P2 の再現対象そのもの: 偽 pin の防止）。
    """
    repo_dir = tmp_path / "diffsinger_repo"
    with pytest.raises(SystemExit):
        gs._check_diffsinger_repo_stable(
            repo_dir,
            "abc123", False, "s1", "d1",
            "def456", False, "s1", "d1",
        )


def test_check_diffsinger_repo_stable_rejects_dirty_state_change(tmp_path: Path) -> None:
    """HEAD は同一でも dirty 状態が export 前後で変化した場合も fail-closed
    する（checkout が一時的に別状態を経由した可能性を否定できないため）。
    """
    repo_dir = tmp_path / "diffsinger_repo"
    with pytest.raises(SystemExit):
        gs._check_diffsinger_repo_stable(
            repo_dir,
            "abc123", False, "s1", "d1",
            "abc123", True, "s1", "d1",
        )


def test_check_diffsinger_repo_stable_rejects_dirty_content_change(tmp_path: Path) -> None:
    """review #264 R25 P2 の再現: HEAD/dirty が両方とも pre/post で同一
    （dirty のまま）でも、status_sha256/diff_sha256 が異なれば fail-closed
    する（dirty のまま export.py 自体や import 先が書き換わったケースを
    bool 縮約では検出できなかった穴）。
    """
    repo_dir = tmp_path / "diffsinger_repo"
    with pytest.raises(SystemExit):
        gs._check_diffsinger_repo_stable(
            repo_dir,
            "abc123", True, "status-v1", "diff-v1",
            "abc123", True, "status-v1", "diff-v2",
        )


def test_check_diffsinger_repo_stable_skips_when_pre_unavailable(tmp_path: Path) -> None:
    """pre 側が 'unavailable'（git 情報取得失敗）の場合は照合不能として
    fail-closed にはしない（R20 の既存挙動を維持 — git 情報が無くても export
    自体は継続する）。
    """
    repo_dir = tmp_path / "diffsinger_repo"
    gs._check_diffsinger_repo_stable(
        repo_dir,
        "unavailable", "unavailable", "unavailable", "unavailable",
        "abc123", False, "s1", "d1",
    )  # no raise


def test_check_diffsinger_repo_stable_skips_when_post_unavailable(tmp_path: Path) -> None:
    """post 側が 'unavailable' の場合も同様に照合不能として素通りする。"""
    repo_dir = tmp_path / "diffsinger_repo"
    gs._check_diffsinger_repo_stable(
        repo_dir,
        "abc123", False, "s1", "d1",
        "unavailable", "unavailable", "unavailable", "unavailable",
    )  # no raise


# ----------------------------------------------------------------------------
# review #264 R27 P2 再現テスト: `_publish_exported_acoustic()`
#
# `run_export_acoustic()` は canonical (`out_path`) への swap をもう自分では
# 行わず、staging に留め置いたまま返す。`_publish_exported_acoustic()` が
# post snapshot 取得 + `_check_diffsinger_repo_stable()` の成功を確認して
# から初めて swap を行う orchestration を担う。ここでは実際の DiffSinger
# checkout（git repo）を用意し、フェイク exporter 自体に checkout を動かさせる
# ことで「安定性検査が実際に失敗する状況で、canonical が汚染されないこと」を
# 実測する（monkeypatch ではなく実 git 状態遷移で固定する）。
# ----------------------------------------------------------------------------

def _make_diffsinger_export_repo(repo_dir: Path, export_py_src: str) -> None:
    _init_git_repo(repo_dir)
    # `python scripts/export.py` の実行自体が `scripts/__pycache__/*.pyc` を
    # 副生成し、`run_export_acoustic()` 自身も `checkpoints/<exp_name>/` を
    # diffsinger_repo 直下へ書き出す（exporter に消費させる ckpt/config の
    # 置き場）。これらを追跡すると「exporter は checkout を一切動かして
    # いないのに status_sha256 が pre/post で変わる」偽陽性を安定性検査に
    # 混入させてしまう（本テストの関心事 = R27 の swap 順序と無関係な
    # confound）。実運用の DiffSinger checkout も通常この 2 つを gitignore
    # 済みの前提のため、それに揃える。
    (repo_dir / ".gitignore").write_text(
        "__pycache__/\n*.pyc\ncheckpoints/\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add gitignore"], cwd=str(repo_dir), check=True
    )
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (repo_dir / "scripts" / "export.py").write_text(export_py_src, encoding="utf-8")


def _make_ckpt_dir(base: Path, name: str, ckpt_content: bytes, config_content: bytes) -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "model_ckpt_steps_5000.ckpt").write_bytes(ckpt_content)
    (d / "config.yaml").write_bytes(config_content)
    return d


_EXPORT_PY_STABLE = textwrap.dedent(
    """\
    import argparse
    from pathlib import Path

    def main():
        p = argparse.ArgumentParser()
        p.add_argument("mode")
        p.add_argument("--exp", required=True)
        p.add_argument("--ckpt", required=True)
        p.add_argument("--out", required=True)
        args = p.parse_args()
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.exp}.onnx").write_bytes(b"ONNX-STABLE")

    if __name__ == "__main__":
        main()
    """
)

_EXPORT_PY_MUTATES_CHECKOUT = textwrap.dedent(
    """\
    import argparse
    from pathlib import Path

    def main():
        p = argparse.ArgumentParser()
        p.add_argument("mode")
        p.add_argument("--exp", required=True)
        p.add_argument("--ckpt", required=True)
        p.add_argument("--out", required=True)
        args = p.parse_args()
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.exp}.onnx").write_bytes(b"ONNX-FROM-UNSTABLE-CHECKOUT")
        # exporter 実行中に checkout 自体が動く（例: 依存パッケージの
        # setup.py 実行や別プロセスの同時書き込みを模す）。
        (Path(__file__).resolve().parent.parent / "README.md").write_text(
            "mutated-during-export\\n", encoding="utf-8"
        )

    if __name__ == "__main__":
        main()
    """
)


def test_publish_exported_acoustic_swaps_canonical_on_stable_checkout(
    tmp_path: Path,
) -> None:
    """検査成功時: staging が canonical (`out_path`) へ swap されること。"""
    repo_dir = tmp_path / "diffsinger_repo"
    _make_diffsinger_export_repo(repo_dir, _EXPORT_PY_STABLE)
    ckpt_dir = _make_ckpt_dir(tmp_path, "ckpt", b"CKPT", b"CONFIG")
    out_dir = tmp_path / "out"

    pre = gs._git_head_and_dirty(repo_dir)
    staging_dir, out_path, _, _ = gs.run_export_acoustic(
        repo_dir, ckpt_dir, "s1_gate", 5000, out_dir
    )

    rollback_state: dict = {"onnx_out_path": None}
    gs._publish_exported_acoustic(repo_dir, staging_dir, out_path, *pre, rollback_state)

    assert out_path.exists()
    assert (out_path / "acoustic.onnx").read_bytes() == b"ONNX-STABLE"
    assert not staging_dir.exists()
    # review #264 R30 P2: 公開完了は本関数内の finally から記帳される
    # （呼び出し元の後続代入には依存しない）。
    assert rollback_state["onnx_out_path"] == out_path


def test_publish_exported_acoustic_leaves_canonical_untouched_on_unstable_checkout(
    tmp_path: Path,
) -> None:
    """review #264 R27 P2 の再現本体: exporter 実行中に checkout が動いて
    安定性検査が fail-closed で拒否する場合、canonical (`out_path`) の旧世代
    が「新 ONNX + 旧 gate summary」の混成へ差し替わってはならない — 旧世代
    (あれば) が無傷のまま残り、staging は掃除される。"""
    repo_dir = tmp_path / "diffsinger_repo"
    _make_diffsinger_export_repo(repo_dir, _EXPORT_PY_STABLE)
    ckpt_dir_a = _make_ckpt_dir(tmp_path, "ckpt_a", b"CKPT-A", b"CONFIG-A")
    out_dir = tmp_path / "out"

    # 1 回目: 正常発行して旧世代を作る。
    pre1 = gs._git_head_and_dirty(repo_dir)
    staging_dir1, out_path, _, _ = gs.run_export_acoustic(
        repo_dir, ckpt_dir_a, "s1_gate", 5000, out_dir
    )
    rollback_state1: dict = {"onnx_out_path": None}
    gs._publish_exported_acoustic(repo_dir, staging_dir1, out_path, *pre1, rollback_state1)
    old_generation_bytes = (out_path / "acoustic.onnx").read_bytes()
    assert old_generation_bytes == b"ONNX-STABLE"
    assert rollback_state1["onnx_out_path"] == out_path

    # 2 回目: exporter 自体が checkout を動かす（安定性検査が fail-closed で
    # 拒否するはずのシナリオ）。
    (repo_dir / "scripts" / "export.py").write_text(
        _EXPORT_PY_MUTATES_CHECKOUT, encoding="utf-8"
    )
    ckpt_dir_b = _make_ckpt_dir(tmp_path, "ckpt_b", b"CKPT-B", b"CONFIG-B")
    pre2 = gs._git_head_and_dirty(repo_dir)
    staging_dir2, out_path2, _, _ = gs.run_export_acoustic(
        repo_dir, ckpt_dir_b, "s1_gate", 5000, out_dir
    )
    assert out_path2 == out_path
    # staging には新世代が既に書き出されている（swap 前）。
    assert (staging_dir2 / "acoustic.onnx").read_bytes() == b"ONNX-FROM-UNSTABLE-CHECKOUT"

    rollback_state2: dict = {"onnx_out_path": None}
    with pytest.raises(SystemExit):
        gs._publish_exported_acoustic(repo_dir, staging_dir2, out_path2, *pre2, rollback_state2)

    # canonical は旧世代のまま無傷 — 新 ONNX が混成公開されていない。
    assert (out_path / "acoustic.onnx").read_bytes() == old_generation_bytes
    # staging は掃除され、リークしていない。
    assert not staging_dir2.exists()
    # review #264 R30 P2 回帰ガード: 検査失敗で `_swap_step_dir_into_place()`
    # 自体を一度も呼ばない経路では、`rollback_state` へ書き込まれない
    # （早期削除を「swap 完了」と混同しない）。
    assert rollback_state2["onnx_out_path"] is None
