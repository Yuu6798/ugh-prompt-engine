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

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR))
sys.path.insert(0, str(_TESTS_DIR.parent / "s1_gate"))

from _optional_runtime_stubs import stub_onnxruntime_if_missing  # noqa: E402

with stub_onnxruntime_if_missing():
    import gate_synth as gs  # noqa: E402
sys.modules.pop("gate_synth", None)


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


def test_swap_step_dir_into_place_migrates_unmarked_pre_existing_canonical(
    tmp_path: Path,
) -> None:
    """review #264 R28 P2 再現・是正確認: マーカー導入前に作られた
    （マーカー無し）canonical `out_dir` に対する最初の swap で、退避される
    `.old` へこの swap 自身がマーカーを付与するため、続く rerun がその
    `.old` を正しく「本ツール由来」と認識して手動掃除なしに成功する。

    従来（R28 修正前）は、アップグレード後最初の swap が「マーカー無しの
    旧 canonical」を検証なしに（`.old` が既存でないため R25 のガードは
    素通り）そのまま `.old` へ退避していたため、`.old` 自体にもマーカーが
    無いまま残り、次の rerun がその自作バックアップを「無関係」と誤認して
    fail-closed 拒否していた（＝以後 rerun のたびに手動掃除が必要になる
    恒久的な自己ロック）。
    """
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "pre_marker_gen.txt").write_text("pre-marker generation", encoding="utf-8")
    assert not (out_dir / gs._SWAP_BACKUP_MARKER_NAME).exists()  # マーカー導入前を模す

    old_dir = tmp_path / "out.old"

    build_dir1 = tmp_path / "out.build-1"
    build_dir1.mkdir()
    (build_dir1 / "gen1.txt").write_text("gen1", encoding="utf-8")
    gs._swap_step_dir_into_place(build_dir1, out_dir)  # アップグレード後最初の swap

    assert old_dir.exists()
    assert (old_dir / "pre_marker_gen.txt").exists()
    # 退避された .old には、この swap 自身のスタンプによりマーカーが付いている。
    assert (
        old_dir / gs._SWAP_BACKUP_MARKER_NAME
    ).read_text(encoding="utf-8") == gs._SWAP_BACKUP_MARKER_CONTENT

    build_dir2 = tmp_path / "out.build-2"
    build_dir2.mkdir()
    (build_dir2 / "gen2.txt").write_text("gen2", encoding="utf-8")
    # 修正前はここで SystemExit（無関係な .old として拒否）していた。
    gs._swap_step_dir_into_place(build_dir2, out_dir)  # 手動掃除なしで成功すること

    assert (out_dir / "gen2.txt").exists()
    assert old_dir.exists()
    assert (old_dir / "gen1.txt").exists()
    assert not (old_dir / "pre_marker_gen.txt").exists()


def test_swap_step_dir_into_place_rejects_marker_symlink_in_out_dir_without_following_it(
    tmp_path: Path,
) -> None:
    """review #264 R29 P1 再現・是正確認 (gate_synth.py:1526 スレッド)。

    マーカー導入前に作られた既存 `out_dir`（R28 P2 のスタンプ経路）に
    `_SWAP_BACKUP_MARKER_NAME` という名前の symlink が（本ツール外で偶然/
    悪意を持って）存在すると、旧実装の `write_text()` はこれを追従して
    開き、リンク先の外部ファイルを truncate/新規作成してしまう
    data-destruction path だった（`convert_pjs.py` の `_swap_into_place` も
    同型。同時修正 — `tests/test_s1_dataprep_ph_dur_duration.py` の
    `test_swap_into_place_rejects_marker_symlink_in_staging_dir_without_
    following_it` 参照）。修正後は symlink を検出して fail-closed 拒否し、
    外部ターゲットは無傷のまま残ることを確認する。
    """
    external_target = tmp_path / "external_target.txt"
    external_target.write_text("do not touch me", encoding="utf-8")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "pre_marker_gen.txt").write_text("pre-marker generation", encoding="utf-8")
    marker_symlink = out_dir / gs._SWAP_BACKUP_MARKER_NAME
    marker_symlink.symlink_to(external_target)

    build_dir = tmp_path / "out.build-1"
    build_dir.mkdir()
    (build_dir / "gen1.txt").write_text("gen1", encoding="utf-8")

    with pytest.raises(SystemExit):
        gs._swap_step_dir_into_place(build_dir, out_dir)

    # 外部ターゲットは無傷のまま（truncate/上書きされていない）。
    assert external_target.read_text(encoding="utf-8") == "do not touch me"
    # out_dir 自体も swap されず旧世代のまま（symlink も残置される）。
    assert (out_dir / "pre_marker_gen.txt").exists()
    assert not (out_dir / "gen1.txt").exists()
    assert marker_symlink.is_symlink()


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
