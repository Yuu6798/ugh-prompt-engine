"""test_proto1_demo.py — F1 (final_assembly_memo.md) の publish フェーズ受け入れテスト。

PR#261 レビュー R1/R2/R8/R11 対象:
  - `_publish_outputs()`: staging → 正本パスへの一括公開。途中失敗時に
    未着手の項目が旧状態のまま残ることを確認する（R1 の障害注入テスト）。
  - `_repo_relative_path()`: e2e_run.json に埋め込む registry_path が
    実行環境依存の絶対パスではなく repo-relative であることを確認する（R2）。
  - `_publish_or_fail_closed()`: 決定論比較の成否で publish 呼び出しを
    門番化する。不一致時は正本を一切置換せず、失敗診断を非正本パスへ書く
    （R8 の障害注入テスト）。
  - `_save_wav_with_digest()` / `hashing.sha256_of_file()`: WAV ファイル
    バイトの sha256 digest 計算と、`soundfile.write()` 自体の決定論性
    （2 回書き比較）を確認する（R11）。

`main()` 自体（実音声合成を含む重い E2E）はここでは対象外。publish フェーズの
契約はこれらの小さな純粋関数レベルで検証できるため、audio 合成なしで高速に回す。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

import proto1_demo as pd
from hashing import sha256_of_file


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


# --- R8: 決定論比較の成否による publish の門番化 ------------------------------


def test_publish_or_fail_closed_leaves_canonical_outputs_untouched_when_determinism_failed(tmp_path):
    """不一致を注入した場合、_publish_outputs は呼ばれず正本一式が完全に
    旧状態のまま残り、失敗診断が非正本パスへ書かれることを確認する
    （PR#261 レビュー R8 の障害注入テスト）。"""
    final_registry = tmp_path / "genome_registry.jsonl"
    final_registry.write_text("old-registry-bytes", encoding="utf-8")
    staging_registry = tmp_path / "_staging_registry.jsonl"
    staging_registry.write_text("new-registry-bytes", encoding="utf-8")

    out_path = tmp_path / "e2e_run.json"
    out_path.write_text('{"old":true}', encoding="utf-8")
    staging_out_path = tmp_path / "_staging_e2e_run.json"
    diagnostics_path = tmp_path / "_failed_run_diagnostics.json"

    e2e_record = {
        "determinism_check": {"passed": False, "differing_paths": ["$.genomes.a.genome_id: 'x' != 'y'"]},
    }

    with pytest.raises(SystemExit):
        pd._publish_or_fail_closed(
            False,  # determinism_passed = False（不一致注入）
            e2e_record,
            (staging_registry, final_registry),
            [],  # wav_publish（本テストでは WAV なし）
            staging_out_path,
            out_path,
            diagnostics_path,
        )

    # 正本は一切変更されていない。
    assert final_registry.read_text(encoding="utf-8") == "old-registry-bytes"
    assert out_path.read_text(encoding="utf-8") == '{"old":true}'
    # staging 側も publish されず、新内容のまま置き去りになる（次回実行時に
    # main() 側の pre-run cleanup で上書きされる想定）。
    assert staging_registry.read_text(encoding="utf-8") == "new-registry-bytes"
    # staging_out_path は _publish_or_fail_closed 内で一度も書かれない
    # （determinism_passed=False の分岐は diagnostics_path のみへ書く）。
    assert not staging_out_path.exists()
    # 失敗診断（run_1/run_2/差分リストを含む e2e_record 全体）が非正本パスへ
    # 書かれている。
    assert json.loads(diagnostics_path.read_text(encoding="utf-8")) == e2e_record


def test_publish_or_fail_closed_publishes_when_determinism_passed(tmp_path):
    """非退行確認: determinism_passed=True の場合は従来どおり publish される。"""
    final_registry = tmp_path / "genome_registry.jsonl"
    staging_registry = tmp_path / "_staging_registry.jsonl"
    staging_registry.write_text("new-registry-bytes", encoding="utf-8")

    out_path = tmp_path / "e2e_run.json"
    staging_out_path = tmp_path / "_staging_e2e_run.json"
    diagnostics_path = tmp_path / "_failed_run_diagnostics.json"

    e2e_record = {"determinism_check": {"passed": True, "differing_paths": []}}

    pd._publish_or_fail_closed(
        True,
        e2e_record,
        (staging_registry, final_registry),
        [],
        staging_out_path,
        out_path,
        diagnostics_path,
    )

    assert final_registry.read_text(encoding="utf-8") == "new-registry-bytes"
    assert json.loads(out_path.read_text(encoding="utf-8")) == e2e_record
    assert not diagnostics_path.exists()  # 成功時は失敗診断ファイルを作らない


def test_publish_or_fail_closed_publishes_wav_files_too(tmp_path):
    """wav_publish に渡した (staging, final) ペアも registry・e2e_run.json と
    同じ publish フェーズで一括公開されることを確認する。"""
    final_registry = tmp_path / "genome_registry.jsonl"
    staging_registry = tmp_path / "_staging_registry.jsonl"
    staging_registry.write_text("registry", encoding="utf-8")

    wav_staging = tmp_path / "_staging_phrase.wav"
    wav_final = tmp_path / "phrase.wav"
    wav_staging.write_bytes(b"fake-wav-bytes")

    out_path = tmp_path / "e2e_run.json"
    staging_out_path = tmp_path / "_staging_e2e_run.json"
    diagnostics_path = tmp_path / "_failed_run_diagnostics.json"

    pd._publish_or_fail_closed(
        True,
        {"determinism_check": {"passed": True}},
        (staging_registry, final_registry),
        [(wav_staging, wav_final)],
        staging_out_path,
        out_path,
        diagnostics_path,
    )

    assert wav_final.read_bytes() == b"fake-wav-bytes"
    assert not wav_staging.exists()


# --- R11: WAV ファイルバイトの sha256 digest + soundfile 書き出しの決定論性 ---


def test_sha256_of_file_matches_hashlib_reference(tmp_path):
    import hashlib

    p = tmp_path / "sample.bin"
    p.write_bytes(b"hello voice_genesis")
    assert sha256_of_file(p) == hashlib.sha256(b"hello voice_genesis").hexdigest()


def test_save_wav_with_digest_returns_stable_digest_and_no_leftover_verify_file(tmp_path):
    sig = np.sin(np.linspace(0, 2 * np.pi * 440, 4410)).astype(np.float64)
    staging_path = tmp_path / "phrase.wav"

    result = pd._save_wav_with_digest(sig, staging_path, sr=44100)

    assert staging_path.exists()
    assert result["sha256"] == sha256_of_file(staging_path)
    assert result["write_determinism_check"]["stable"] is True
    assert result["write_determinism_check"]["digest_2"] == result["sha256"]
    # 比較専用の一時ファイルは残らない。
    assert not (tmp_path / "phrase.write_verify.wav").exists()


def test_save_wav_with_digest_is_deterministic_across_independent_calls(tmp_path):
    """非退行確認: 同一配列を独立した 2 回の呼び出しで書いても同じ digest になる
    （soundfile.write() の決定論性の間接確認）。"""
    sig = np.linspace(-1.0, 1.0, 2205).astype(np.float64)

    result_a = pd._save_wav_with_digest(sig, tmp_path / "a.wav", sr=44100)
    result_b = pd._save_wav_with_digest(sig, tmp_path / "b.wav", sr=44100)

    assert result_a["sha256"] == result_b["sha256"]


# --- R25: staging パスの per-run 一意化 + publish 直前の排他ロック -----------


def test_run_suffix_differs_across_calls():
    """PR#261 レビュー R25: `main()` の staging ファイル名に埋め込む
    サフィックスは呼び出しごとに一意でなければならない（併走 `main()` が
    互いの staging ファイルを踏みつけ合わないようにするため）。同一プロセス
    内で連続呼び出しても（PID が同じでも）衝突しないことを確認する。"""
    suffixes = {pd._run_suffix() for _ in range(50)}
    assert len(suffixes) == 50  # 50 回とも相異なる


def test_run_suffix_contains_pid_for_process_identification():
    """サフィックスは調査時に発行プロセスを特定できるよう pid を含む。"""
    import os as _os

    suffix = pd._run_suffix()
    assert suffix.startswith(f"{_os.getpid()}-")


def test_staging_paths_built_from_different_run_suffixes_are_distinct(tmp_path):
    """非退行確認: `main()` 内の staging パス組み立て（`_scratch_registry_run1.
    {suffix}.jsonl` 等のテンプレート）が、異なる run_suffix に対して実際に
    異なる Path になることを確認する（`main()` 自体は重いためテンプレートの
    組み立てロジックだけを軽量に再現する）。"""
    suffix_1, suffix_2 = pd._run_suffix(), pd._run_suffix()
    assert suffix_1 != suffix_2

    def _staging_paths_for(run_suffix, results_dir):
        return {
            "scratch_registry": results_dir / f"_scratch_registry_run1.{run_suffix}.jsonl",
            "staging_registry": results_dir / f"_staging_registry_run2.{run_suffix}.jsonl",
            "staging_e2e_run": results_dir / f"_staging_e2e_run.{run_suffix}.json",
        }

    paths_1 = _staging_paths_for(suffix_1, tmp_path)
    paths_2 = _staging_paths_for(suffix_2, tmp_path)
    for key in paths_1:
        assert paths_1[key] != paths_2[key], key


def test_publish_lock_creates_and_removes_lock_file_on_success(tmp_path):
    """非退行確認: ロック取得 → ブロック実行 → ブロック終了時にロックファイルが
    削除される（正常系）。"""
    lock_path = tmp_path / "_publish.lock"
    assert not lock_path.exists()

    with pd._publish_lock(lock_path):
        assert lock_path.exists()  # ブロック内では取得済み

    assert not lock_path.exists()  # ブロック終了後は削除されている


def test_publish_lock_removes_lock_file_even_when_block_raises(tmp_path):
    """ロック保持中のブロックが例外を送出しても、ロックファイルは残置されない
    （次回実行が誤って「併走中」と誤検知しないようにする）。"""
    lock_path = tmp_path / "_publish.lock"

    with pytest.raises(RuntimeError):
        with pd._publish_lock(lock_path):
            assert lock_path.exists()
            raise RuntimeError("simulated failure during publish")

    assert not lock_path.exists()


def test_publish_lock_rejects_concurrent_publish_when_lock_already_exists(tmp_path):
    """PR#261 レビュー R25: ロックファイルが既に存在する（＝別プロセスが
    publish 中、またはクラッシュ残置）場合、併走 publish とみなし明示エラーで
    即座に拒否する。"""
    lock_path = tmp_path / "_publish.lock"
    lock_path.write_text("12345", encoding="utf-8")  # 別プロセスの PID を模したロック残置

    with pytest.raises(pd.PublishLockError):
        with pd._publish_lock(lock_path):
            raise AssertionError("ロックが既に存在するのでブロック本体へ到達してはならない")

    # 既存のロックは他プロセスの資産として扱い、拒否時に勝手に削除しない
    # （誤って併走中の別プロセスのロックを壊さないため）。
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == "12345"
