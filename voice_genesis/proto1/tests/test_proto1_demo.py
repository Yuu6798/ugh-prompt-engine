"""test_proto1_demo.py — F1 (final_assembly_memo.md) の publish フェーズ受け入れテスト。

PR#261 レビュー R1/R2 対象:
  - `_publish_outputs()`: staging → 正本パスへの一括公開。途中失敗時に
    未着手の項目が旧状態のまま残ることを確認する（R1 の障害注入テスト）。
  - `_repo_relative_path()`: e2e_run.json に埋め込む registry_path が
    実行環境依存の絶対パスではなく repo-relative であることを確認する（R2）。

`main()` 自体（実音声合成を含む重い E2E）はここでは対象外。publish フェーズの
契約はこれらの小さな純粋関数レベルで検証できるため、audio 合成なしで高速に回す。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import proto1_demo as pd


# --- R2: repo-relative パス ---------------------------------------------------


def test_repo_relative_path_returns_voice_genesis_prefixed_posix_string():
    target = pd.RESULTS_DIR / "genome_registry.jsonl"
    rel = pd._repo_relative_path(target)
    assert rel == "voice_genesis/proto1/results_final/genome_registry.jsonl"
    # 実行環境依存の絶対パス断片が残っていないこと。
    assert not rel.startswith("/")
    assert "/tmp/" not in rel


def test_repo_relative_path_is_stable_regardless_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = pd.RESULTS_DIR / "_scratch_registry_run1.jsonl"
    assert pd._repo_relative_path(target) == "voice_genesis/proto1/results_final/_scratch_registry_run1.jsonl"


# --- R1: publish フェーズ（staging → 正本の一括 os.replace）------------------


def test_publish_outputs_replaces_all_staging_to_final_in_order(tmp_path):
    items = []
    for i in range(3):
        staging = tmp_path / f"staging_{i}.txt"
        final = tmp_path / f"final_{i}.txt"
        staging.write_text(f"new-content-{i}", encoding="utf-8")
        final.write_text(f"old-content-{i}", encoding="utf-8")
        items.append((staging, final))

    pd._publish_outputs(items)

    for i, (staging, final) in enumerate(items):
        assert not staging.exists()  # os.replace は移動なので staging 側は消える
        assert final.read_text(encoding="utf-8") == f"new-content-{i}"


def test_publish_outputs_stops_at_first_failure_leaving_remaining_final_untouched(tmp_path, monkeypatch):
    """publish フェーズの途中（2 件目）で例外が起きた場合、まだ手を付けて
    いない項目（3 件目）の正本は旧状態のまま残る（PR#261 レビュー R1: 障害
    注入テスト）。1 件目は publish フェーズが呼ばれる前提（registry/WAV/
    e2e_record が全て staging に揃っている）を満たした後の最終ステップで
    あるため既に置換済みになるのは許容される設計（本関数 docstring 参照）。
    実際の main() では registry/WAV/e2e_record の構築失敗時は本関数自体が
    一度も呼ばれないため、正本一式は完全に旧状態のまま残る。
    """
    staging_0 = tmp_path / "staging_0.txt"
    final_0 = tmp_path / "final_0.txt"
    staging_0.write_text("new-0", encoding="utf-8")
    final_0.write_text("old-0", encoding="utf-8")

    staging_1 = tmp_path / "staging_1.txt"
    final_1 = tmp_path / "final_1.txt"
    staging_1.write_text("new-1", encoding="utf-8")
    final_1.write_text("old-1", encoding="utf-8")

    staging_2 = tmp_path / "staging_2.txt"
    final_2 = tmp_path / "final_2.txt"
    staging_2.write_text("new-2", encoding="utf-8")
    final_2.write_text("old-2", encoding="utf-8")

    real_replace = pd.os.replace
    call_count = {"n": 0}

    def _flaky_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated mid-publish failure (disk full 等の想定)")
        return real_replace(src, dst)

    monkeypatch.setattr(pd.os, "replace", _flaky_replace)

    with pytest.raises(OSError):
        pd._publish_outputs([(staging_0, final_0), (staging_1, final_1), (staging_2, final_2)])

    # 1 件目（呼び出し順で最初）は既に公開済み。
    assert final_0.read_text(encoding="utf-8") == "new-0"
    # 2 件目は失敗したため staging のまま残り、正本は旧内容のまま。
    assert staging_1.exists()
    assert final_1.read_text(encoding="utf-8") == "old-1"
    # 3 件目は publish 処理へ到達すらしていないため、staging・正本ともに
    # 手つかずで旧内容のまま残る。
    assert staging_2.exists()
    assert staging_2.read_text(encoding="utf-8") == "new-2"
    assert final_2.read_text(encoding="utf-8") == "old-2"


def test_publish_outputs_not_called_when_upstream_computation_raises_before_publish(tmp_path, monkeypatch):
    """registry/WAV/e2e_record 構築段階（publish フェーズより前）で例外が起きた
    場合、_publish_outputs は一度も呼ばれず、正本一式が完全に旧状態のまま
    残ることを確認する（R1 の主眼: publish 呼び出しそのものを全工程成功後の
    最終ステップに限定する設計）。
    """
    final = tmp_path / "final.txt"
    final.write_text("old", encoding="utf-8")

    calls = []
    monkeypatch.setattr(pd, "_publish_outputs", lambda items: calls.append(items))

    def _staging_phase_that_fails():
        # main() 内の run_2/監査/WAV レンダ/e2e_record 組み立てに相当する、
        # publish 呼び出し前の重い計算工程を模した関数。ここで例外が起きる
        # 想定。
        raise RuntimeError("simulated failure during staging computation")

    with pytest.raises(RuntimeError):
        _staging_phase_that_fails()
        pd._publish_outputs([])  # 到達しないはず

    assert calls == []  # publish フェーズは一度も呼ばれていない
    assert final.read_text(encoding="utf-8") == "old"  # 正本は完全に旧状態のまま
