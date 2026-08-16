"""test_gate_synth_swap_backup.py — review #264 R25 P1 再現テスト
(gate_synth.py:1364-1365, レビュー本文直書き)。

`_swap_step_dir_into_place`（`s1_gate/gate_synth.py`）は `<out_dir>.old` が
既存の場合、それが本ツールが過去の swap で作った退避物である保証なしに
無条件で `shutil.rmtree` していた。決定論的なパスへ本ツールと無関係な
ディレクトリ（手動作業の残骸・別ツールの出力等）が偶然存在していた場合、
検証なしに丸ごと削除してしまう破壊経路（`convert_pjs.py` の
`_swap_into_place` も同型。同時修正 — `tests/test_s1_dataprep_ph_dur_
duration.py` 参照）。

修正: 本ツールが公開する `build_dir` へ固定内容のマーカーファイルを常に
埋め込み、rename でパスが遷移してもマーカーごと運ばれる性質を使って
「本ツールが過去に作った退避物か」を検証する。マーカー不一致は
`SystemExit` で fail-closed 拒否する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "s1_gate"))

import gate_synth as gs  # noqa: E402


def test_swap_step_dir_into_place_rejects_unrelated_old_dir(tmp_path: Path) -> None:
    """本ツールが作った退避物であることを示すマーカーを持たない
    `<out_dir>.old` は fail-closed 拒否され、無変更のまま残る（削除
    されない）。"""
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "existing.txt").write_text("old generation", encoding="utf-8")

    old_dir = tmp_path / "out.old"
    old_dir.mkdir()
    (old_dir / "unrelated.txt").write_text("not created by this tool", encoding="utf-8")

    build_dir = tmp_path / "out.build-123"
    build_dir.mkdir()
    (build_dir / "new.txt").write_text("new generation", encoding="utf-8")

    with pytest.raises(SystemExit):
        gs._swap_step_dir_into_place(build_dir, out_dir)

    # 無関係な old_dir は削除されず中身も無変更のまま。
    assert old_dir.exists()
    assert (old_dir / "unrelated.txt").read_text(encoding="utf-8") == "not created by this tool"
    # out_dir も swap されず旧世代のまま公開は起きていない。
    assert (out_dir / "existing.txt").exists()
    assert not (out_dir / "new.txt").exists()


def test_swap_step_dir_into_place_rotates_own_backup_across_three_runs(tmp_path: Path) -> None:
    """本ツール自身が過去の swap で作った `.old`（マーカー付き）は、通常
    どおりローテートされて削除される（正常系の回帰ガード）。

    マーカーは `build_dir` -> `out_dir` -> `.old` と rename でしか運ばれ
    ないため、`.old` にマーカーが乗るのは「`out_dir` 自体が本ツールの
    swap 経由で作られていた」場合のみ。初回 swap（`out_dir` 未存在）の
    直後にできる `out_dir` にはこの時点でまだマーカーが付いているが、
    `.old` はまだ存在しない。2 回目 swap で初めて `.old`（マーカー付き）
    が生まれ、3 回目 swap でそのローテートを検証できる。
    """
    out_dir = tmp_path / "out"  # 初回 swap 前は未存在（クリーンな初回実行）
    old_dir = tmp_path / "out.old"

    build_dir1 = tmp_path / "out.build-1"
    build_dir1.mkdir()
    (build_dir1 / "gen1.txt").write_text("gen1", encoding="utf-8")
    gs._swap_step_dir_into_place(build_dir1, out_dir)
    assert (out_dir / "gen1.txt").exists()
    assert not old_dir.exists()  # 初回は退避対象が無い

    build_dir2 = tmp_path / "out.build-2"
    build_dir2.mkdir()
    (build_dir2 / "gen2.txt").write_text("gen2", encoding="utf-8")
    gs._swap_step_dir_into_place(build_dir2, out_dir)
    assert (out_dir / "gen2.txt").exists()
    assert old_dir.exists()
    assert (old_dir / "gen1.txt").exists()  # 2回目で初めて .old (マーカー付き) が生まれる

    build_dir3 = tmp_path / "out.build-3"
    build_dir3.mkdir()
    (build_dir3 / "gen3.txt").write_text("gen3", encoding="utf-8")
    gs._swap_step_dir_into_place(build_dir3, out_dir)  # 3回目: 前回の .old を安全にローテート

    assert (out_dir / "gen3.txt").exists()
    assert old_dir.exists()
    assert (old_dir / "gen2.txt").exists()
    assert not (old_dir / "gen1.txt").exists()


def test_swap_step_dir_into_place_first_run_has_no_old_dir_to_verify(tmp_path: Path) -> None:
    """初回実行（`.old` が存在しない）では検証自体が発生せず、通常どおり
    公開される。"""
    out_dir = tmp_path / "out"  # まだ存在しない
    build_dir = tmp_path / "out.build-1"
    build_dir.mkdir()
    (build_dir / "gen0.txt").write_text("gen0", encoding="utf-8")

    gs._swap_step_dir_into_place(build_dir, out_dir)

    assert (out_dir / "gen0.txt").exists()
    assert not (tmp_path / "out.old").exists()
