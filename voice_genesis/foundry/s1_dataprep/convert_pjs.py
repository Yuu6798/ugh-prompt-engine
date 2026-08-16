"""D1: PJS (Phoneme-balanced Japanese Singing-voice corpus) -> DiffSinger 変換 CLI。

`UtaUtaUtau/nnsvs-db-converter`（外部 clone、`--converter-dir` で指定。pin は
`README.md` 参照）の `db_converter.py` を呼び出し、PJS の song wav を
DiffSinger acoustic 学習形式（`transcriptions.csv`: ph_seq/ph_dur/ph_num/
note_seq/note_dur）へ変換する。変換規則・障害の詳細は `s1a_conversion_record.md`
に逐語記録済みのため、本スクリプトは手順の再現のみを担う。

PJS は `pjsNNN.lab` に対し `pjsNNN_song.wav`/`pjsNNN_speech.wav` という命名の
ため、`db_converter.py` が期待する `pjsNNN.wav`（`.lab` と同名）と食い違い、
そのままでは `FileNotFoundError` になる（`glob('**/{lab.stem}.wav')` が 0 件
ヒット）。本スクリプトは PJS 本体には一切触れず、song wav のみをシンボリック
リンクで `pjsNNN.wav` へリネームしたステージングディレクトリを作ってから
変換器を呼ぶ（発話系 `_speech.wav` はステージングに含めない）。

symlink 群 + 変換出力（`diffsinger_db/`）は毎回 fresh な
`<staging-dir>.build-<pid>` に完全構築してから、成功時のみ `--staging-dir`
と原子的に swap する（review #263 R3 P1）。これにより途中失敗時の新旧世代
混在を防ぐと同時に、`pjs_root` から消えた曲の symlink が再実行後も
`--staging-dir` に残り続ける問題も解消する（build dir は毎回空から始まる
ため）。

`--lang-def` が `--staging-dir` 配下を明示的に指す場合、書き込み先を
`build_dir` 配下の同じ相対位置へ再マップする（review #263 R4 P2）。fresh
build では `--staging-dir` がまだ存在せず書き込みが失敗し、旧 `--staging-dir`
が残っている場合は build 外＝旧世代側に書かれて `_swap_into_place` の
`.old` 退避で消えてしまうため（`--staging-dir` 外を指す場合は従来通り）。

review #263 R5 で 2 件追加修正: (1) `.lab`/`_song.wav` の一方が欠けた song
ディレクトリを黙って skip せず、欠落曲名を全収集してから swap 前に
fail-closed 拒否する（契約=PJS 全 100 曲。完全素材では挙動不変）。
(2) `--staging-dir` が `--pjs-root`/`--converter-dir` と衝突・内包関係に
ある場合、symlink 構築を始める前に fail-closed で拒否する
（`build_dataset.py` R4 の `OutputCollisionError` と同型）。

review #263 R7 で 3 件追加修正: (1) 衝突ガードの包含判定を双方向化し、
保護 root が出力配下にある場合（例: `--staging-dir=/tmp/work`,
`--pjs-root=/tmp/work/pjs`）も拒否する。(2) `--staging-dir` 外を指す
`--lang-def` もガード対象に含め、書き込み自体も一時ファイル→
`os.replace()` の staged 方式にする。(3) `_swap_into_place` の 2 段
rename を `try/except BaseException` で保護し、新世代 rename 失敗時は
退避済み旧世代を元パスへ復元してから再送出する。

review #263 R9 で 2 件追加修正: (1) `stage_song_wavs` が「存在する
ディレクトリのみ検査」していたため、抽出が `pjsNNN` ディレクトリ自体を
丸ごと落とした場合（`.lab`/`_song.wav` の片方欠けではなく曲ディレクトリ
そのものが無い）を検出できなかった（実測: pjs001-pjs099 のみで実行すると
`(99, [])` が返り縮小コーパスが成功報告される）。期待 ID 集合
（`PJS_EXPECTED_IDS` = pjs001-pjs100）に対して走査する方式へ変更し、
存在しないディレクトリも `missing_pairs` へ含める（`convert_pjs.py` と
`convert_ritsu.py` の同型修正）。(2) 衝突ガードが `--staging-dir` 本体
のみを検査し、`_swap_into_place` が実際に削除・rename する派生パス
（`<staging-dir>.old`・`<staging-dir>.build-<pid>`）を対象外にしていた。
例えば `--staging-dir=/tmp/published`, `--pjs-root=/tmp/published.old`
は事前チェックを素通りしたのち、公開時に `old_dir` の `rmtree` が
`pjs_root`（保護入力）そのものを削除する。派生パスもガード対象に含める。

review #263 R10 で 1 件追加修正: `--lang-def` が `--staging-dir` 外の既存
ファイルを指す場合、`write_lang_def` は swap の外側（`_swap_into_place` に
よる新旧世代の原子的差し替えの対象外）で直接上書きする。この上書き後、
converter 実行〜swap 完了のいずれかが失敗すると、外部 lang-def だけが新
内容のまま取り残され、コーパスと外部 lang-def の世代が食い違う。上書き前に
元内容をメモリへバックアップし、以降のいずれかの失敗で元へ復元する
（成功時のみ確定）。

review #263 R11 で 1 件追加修正: 上記 R10 のバックアップは `--lang-def` が
**既存ファイル**を指す場合のみ機能し、**新規パス**（元ファイルなし）を指す
場合はバックアップが `None` のまま `_restore_lang_def_backup` が何もせず、
`write_lang_def` が新規作成したファイルが失敗経路でも残留してしまっていた。
書き込み前に対象パスの存在有無を記録し、新規パスだった場合は失敗時に
作成物を `unlink` して「実行前は存在しなかった」状態へ戻す（既存ファイル
ケースの復元動作は無変更）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# nnsvs-db-converter 同梱の `lang.sample.json` と同一内容
# （`s1a_conversion_record.md` §2: 日本語ラベルのモーラ分割規則としてそのまま
# 妥当だったため採用。本スクリプトはこの内容を明示的に書き出すことで、外部
# clone 内のサンプルファイルへの暗黙依存を避ける）。
DEFAULT_LANG_DEF = {
    "vowels": ["a", "i", "u", "e", "o", "N", "A", "I", "U", "E", "O"],
    "liquids": {"w": ["k", "g"], "y": True},
}

# PJS_corpus_ver1.1 の契約 = pjs001-pjs100 の 100 曲（review #263 R9 P2）。
# `stage_song_wavs` はこの期待 ID 集合に対して走査することで、抽出が
# `pjsNNN` ディレクトリ自体を丸ごと落とした場合も検出できる。
PJS_EXPECTED_IDS: Tuple[str, ...] = tuple(f"pjs{i:03d}" for i in range(1, 101))


class OutputCollisionError(ValueError):
    """P1 修正 (review #263 R5): `--staging-dir` が `--pjs-root`/
    `--converter-dir` と衝突する場合に送出する（fail-closed。`_swap_into_place`
    による rename/`.old` 退避・rmtree の前に検出する。`build_dataset.py` R4 の
    `OutputCollisionError` と同型判定（record スクリプト群の既存慣例に倣い、
    共有モジュール新設ではなく各ファイル内へコピペ実装。`f1_4_record_2026-08-15.md`
    §1 の Design Memo 判断を踏襲）。"""


def _reject_output_collision(out_paths: Sequence[Path], protected_roots: Sequence[Path]) -> None:
    """`out_paths`（resolve 後）を相互および `protected_roots`（存在する
    もののみ、resolve 後）と照合し、衝突があれば公開前に fail-closed で
    拒否する。

    `--staging-dir` が `--pjs-root`/`--converter-dir` と一致・内包関係に
    ある場合、`_swap_into_place` の旧 `staging_dir` を `.old` へ退避する
    処理や次回実行時の `.old` rmtree が、PJS コーパスや converter clone
    そのものを破壊し得る（`build_dataset.py` `_reject_output_collision`
    と同一の resolved 比較ロジック）。

    review #263 R7 P1: 包含判定は双方向で行う。出力が保護 root 配下にある
    場合（従来判定）に加え、**保護 root が出力配下にある場合**（例:
    `--staging-dir=/tmp/work`, `--pjs-root=/tmp/work/pjs`）も拒否する。
    後者を見落とすと、公開時に保護 root ごと `.old` へ退避されてしまい
    （`staging_dir.rename(old_dir)` は `staging_dir` 配下の `pjs_root` も
    丸ごと退避先へ連れて行く）、以後 symlink ターゲットが指す絶対パスが
    消失した状態でコーパスが破壊される。
    """
    resolved_outs = [(p, p.resolve()) for p in out_paths]

    for i, (p_i, r_i) in enumerate(resolved_outs):
        for p_j, r_j in resolved_outs[i + 1 :]:
            if r_i == r_j:
                raise OutputCollisionError(
                    f"output paths collide with each other: {p_i} == {p_j}（fail-closed で拒否）"
                )

    for root in protected_roots:
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for p, r in resolved_outs:
            if r == root_resolved:
                raise OutputCollisionError(
                    f"output path {p} collides with protected input root {root}（fail-closed で拒否）"
                )
            try:
                r.relative_to(root_resolved)
            except ValueError:
                pass
            else:
                raise OutputCollisionError(
                    f"output path {p} is inside protected input root {root}（fail-closed で拒否）"
                )
            try:
                root_resolved.relative_to(r)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"protected input root {root} is inside output path {p}"
                f"（fail-closed で拒否。出力側の公開処理が保護 root を巻き込む）"
            )


def stage_song_wavs(
    pjs_root: Path, staging_dir: Path, missing_pairs: Optional[List[str]] = None
) -> int:
    """`PJS_corpus_ver1.1/pjsNNN/` 配下の song 系のみをシンボリックリンクで
    `pjsNNN.lab` / `pjsNNN.wav` としてリネームする。冪等（既存リンクは張り
    直す）。戻り値はステージングできたペア数。

    `pjs_root` が相対パスの場合、シンボリックリンクのターゲットは
    `staging_dir` からの相対として解釈されて壊れるため、リンク先は必ず
    `resolve()` した絶対パスで張る。

    [P2 修正] (review #263 R5) `.lab` か `_song.wav` の一方が欠けた song
    ディレクトリを黙って skip すると、契約（PJS 全 100 曲）を満たさない
    不完全コーパスのまま成功報告してしまう。`missing_pairs` を渡した場合、
    欠落した song 名を収集するのみに留め（例外は送出しない）、呼び出し元が
    `_swap_into_place`（公開）の前に一括で fail-closed 判定できるようにする。

    [P2 修正] (review #263 R9) R5 の対応は「`pjs_root` 配下に実在するディレ
    クトリのみ」を検査していたため、抽出処理が `pjsNNN` ディレクトリ自体を
    丸ごと落とした場合（`.lab`/`_song.wav` の片方だけが欠けたのではなく
    曲ディレクトリそのものが存在しない）は `missing_pairs` に一切現れず
    `n_staged` も非ゼロのまま成功してしまう（実測: pjs001-pjs099 のみで
    `(99, [])` が返る）。`pjs_root.iterdir()` で発見したディレクトリを走査
    するのではなく、契約どおりの期待 ID 集合 `PJS_EXPECTED_IDS`
    （pjs001-pjs100）に対して走査することで、ディレクトリ丸ごと欠落と
    片方欠けの双方を同じ `missing_pairs` 経路で検出する。
    """
    pjs_root = pjs_root.resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    n_staged = 0
    for name in PJS_EXPECTED_IDS:
        d = pjs_root / name
        lab_src = d / f"{name}.lab"
        wav_src = d / f"{name}_song.wav"
        if not lab_src.exists() or not wav_src.exists():
            if missing_pairs is not None:
                missing_pairs.append(name)
            continue
        for dst, src in ((staging_dir / f"{name}.lab", lab_src), (staging_dir / f"{name}.wav", wav_src)):
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(src)
        n_staged += 1
    return n_staged


def write_lang_def(path: Path) -> None:
    """`DEFAULT_LANG_DEF` を `path` へ書く。

    review #263 R7 P1: `path` が `--staging-dir` 外（保護入力の外）を指す
    場合、この書き込みは `_swap_into_place` の atomic swap の外側で発生する
    唯一の書き込みになる。直接 `write_text` すると変換器プロセスが書き込み
    途中で kill された場合等に破損 JSON が残り得るため、`path` と同一
    ディレクトリに一時ファイルを作ってから `os.replace()` で原子的に
    差し替える（POSIX の `rename(2)` は同一ファイルシステム内で atomic）。
    """
    tmp_path = path.parent / f".{path.name}.tmp-{os.getpid()}"
    tmp_path.write_text(json.dumps(DEFAULT_LANG_DEF, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def _restore_lang_def_backup(path: Path, backup: Optional[bytes], *, created_new: bool = False) -> None:
    """[P2 修正] (review #263 R10, R11) `--lang-def` が `--staging-dir` 外を
    指す場合の巻き戻しヘルパー。`backup` が非 `None` なら元内容へ復元する。
    `backup` が `None` かつ `created_new=True`（= 書き込み前に存在しなかった
    新規パスへ `write_lang_def` が新規作成した）場合は、作成されたファイルを
    削除して「実行前は存在しなかった」状態へ戻す。それ以外（`backup=None` かつ
    `created_new=False` — 対象外パス、または `--staging-dir` 配下で
    build_dir 経由の swap/rmtree に委ねる既存ファイル）は何もしない。

    `--staging-dir` 外を指す `--lang-def` への書き込みは、`_swap_into_place`
    による新旧世代の原子的 swap の対象外（swap 前に直接上書きする）。この
    書き込み後、converter 実行〜swap 完了のいずれかが失敗すると:
    - 元ファイルが存在していた場合: 新内容のまま取り残され、コーパスと外部
      lang-def の世代が食い違う（呼び出し元がバックアップした元内容へ復元）
    - 元ファイルが存在しなかった場合（review #263 R11）: 新規作成された
      ファイルがそのまま残留し、「このパスには元々何もなかった」という実行前
      状態と食い違う（作成物を削除して原状回復する）
    いずれも成功時のみ確定（この関数は呼ばれない＝バックアップ/新規作成の
    どちらも破棄しない）。
    """
    if backup is not None:
        path.write_bytes(backup)
    elif created_new:
        path.unlink(missing_ok=True)


def _resolve_lang_def_path(lang_def_arg: Optional[Path], staging_dir: Path, build_dir: Path) -> Path:
    """`--lang-def` の実書き込み先を決める（P2 修正・review #263 R4）。

    `--lang-def` 省略時は既定どおり `build_dir / "lang.json"`。明示指定時、
    それが `staging_dir` 配下を指す場合は build 中に存在しない（fresh build
    では `<staging>.build-<pid>` しか無く書き込みが FileNotFoundError になる）
    か、旧 `staging_dir` が残っていれば build 外＝旧世代側に書かれてしまい
    `_swap_into_place` の `.old` 退避で消える（コーパスと一緒に staged→
    published されない）。`staging_dir` 配下を指す出力先は同じ相対位置の
    `build_dir` 配下へ再マップし、コーパスと一緒に swap されるようにする。
    `staging_dir` 外を指す場合は従来通りそのパスへ書く。
    """
    if lang_def_arg is None:
        return build_dir / "lang.json"
    try:
        rel = lang_def_arg.resolve().relative_to(staging_dir.resolve())
    except ValueError:
        return lang_def_arg
    return build_dir / rel


def run_converter(
    converter_dir: Path, staging_dir: Path, lang_def_path: Path, python_bin: str
) -> "subprocess.CompletedProcess[str]":
    """`db_converter.py -T 0 -L <lang_def> -m -c -B <staging_dir>` を実行する。

    フラグの意味（`s1a_conversion_record.md` §2 の 3 回目=最終版と同一）:
    `-T 0` 無音しきい値、`-L` 言語定義、`-m` MIDI 推定（note_seq/note_dur 取得）、
    `-c` ph_num 付与、`-B` breath 検出（AP 音素。付けないと binarize の
    `check_coverage()` が AP=0 件で `BinarizationError` になる）。

    `converter_dir`/`staging_dir`/`lang_def_path` のいずれかが相対パスの場合、
    `cwd=converter_dir` で実行すると `cwd` 変更後に相対パスが再解釈されて
    壊れる（`converter_dir` は `nnsvs-db-converter/nnsvs-db-converter/...` の
    二重連結、`staging_dir`/`lang_def_path` は呼び出し元の cwd ではなく
    `converter_dir` 基準で誤って再解釈される）ため、コマンド構築前に全て
    `resolve()` して絶対パス化する。
    """
    converter_dir = converter_dir.resolve()
    staging_dir = staging_dir.resolve()
    lang_def_path = lang_def_path.resolve()
    cmd = [
        python_bin, str(converter_dir / "db_converter.py"),
        "-T", "0", "-L", str(lang_def_path), "-m", "-c", "-B",
        str(staging_dir),
    ]
    return subprocess.run(cmd, cwd=str(converter_dir), capture_output=True, text=True, check=False)


def _swap_into_place(build_dir: Path, staging_dir: Path) -> None:
    """`build_dir`（symlink 群 + `diffsinger_db/` を完全に構築済み）を
    `staging_dir` へ原子的に差し替える。

    途中失敗時の新旧世代混在防止（review #263 R3 P1）: 旧 `staging_dir` は
    削除せず `<staging_dir>.old` へ退避してから `build_dir` を最終名へ
    rename する。`build_dir` を常に空の状態から作り直すことで、
    `pjs_root` から消えた曲の symlink が再実行後も残り続ける問題も併せて
    解消する。

    review #263 R7 P2（`gate_synth.py` の同型ヘルパーと同一のファミリー
    修正）: 2 段 rename は独立した 2 操作のため、旧世代の退避
    （`staging_dir` -> `.old`）が成功した後に新世代の rename
    （`build_dir` -> `staging_dir`）が失敗（`KeyboardInterrupt` 含む）すると、
    正規パス `staging_dir` が消失したまま旧世代は `.old` にしか存在しない
    状態になる。新世代 rename を `except BaseException` で保護し、失敗時は
    退避済みの旧世代を `staging_dir` へ復元してから再送出する。

    review #263 R8 P2（`gate_synth.py` の同型ヘルパーと同一のファミリー
    修正）: R7 の保護は新世代 rename のみを try で囲んでいたため、退避
    rename（`staging_dir` -> `.old`）が完了した直後・try 進入前にも中断窓が
    残っていた。加えて、退避完了の判定を `evicted_old = True` という後続
    代入で行っていたため、`rename(2)` 自体は成功しているのに代入文自体が
    実行されない極めて狭い窓も理論上残る。退避 rename から公開 rename
    までの遷移全体を単一の try/except BaseException で覆うと同時に、
    「退避が完了したか」の判定をフラグ変数ではなく `old_dir.exists()` と
    いう実ファイルシステム状態の観測へ置き換える（このメソッド冒頭で
    `.old` を消去済みのため、except 到達時点で `old_dir` が存在するのは
    今回の退避 rename が成功した場合のみであり、フラグの代入タイミングに
    依存しない）。これにより両方の中断窓を閉じる。
    """
    old_dir = staging_dir.parent / f"{staging_dir.name}.old"
    if old_dir.exists():
        shutil.rmtree(old_dir)
    try:
        if staging_dir.exists():
            staging_dir.rename(old_dir)
        build_dir.rename(staging_dir)
    except BaseException:
        if old_dir.exists() and not staging_dir.exists():
            old_dir.rename(staging_dir)
        raise


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pjs-root", type=Path, required=True,
        help="展開済み 'PJS_corpus_ver1.1/' のパス (pjsNNN/ の親ディレクトリ)",
    )
    parser.add_argument(
        "--converter-dir", type=Path, required=True,
        help="clone 済み UtaUtaUtau/nnsvs-db-converter のパス (pin: README.md 参照)",
    )
    parser.add_argument(
        "--staging-dir", type=Path, required=True,
        help="song wav のリネーム済みステージング先。変換出力 "
             "'<staging-dir>/diffsinger_db/' もここに生成される (新規/上書き可、冪等)。"
             "内部では '<staging-dir>.build-<pid>' に完全構築してから成功時のみ"
             "原子的に swap する (review #263 R3 P1)",
    )
    parser.add_argument(
        "--lang-def", type=Path, default=None,
        help="言語定義 JSON の出力先 (既定: <staging-dir>/lang.json)",
    )
    parser.add_argument(
        "--python-bin", default=sys.executable,
        help="db_converter.py を実行する python (既定: 現在の interpreter)",
    )
    args = parser.parse_args(argv)

    staging_dir: Path = args.staging_dir

    # [P1 修正] (review #263 R5) --staging-dir が --pjs-root/--converter-dir
    # と重なる場合、symlink 構築や swap を始める前（公開開始前）に fail-closed
    # で拒否する。--staging-dir は成功時に丸ごと rename/`.old` 退避/rmtree
    # されるため、保護入力と重なっていると PJS コーパスや converter clone
    # そのものを破壊し得る。
    #
    # [P1 修正] (review #263 R7) --lang-def が --staging-dir 外を指す場合、
    # `_resolve_lang_def_path` はそのパスへ直接書く（swap の外側の書き込み）。
    # このパスが --pjs-root/--converter-dir と一致・内包関係にあると、
    # 変換前に保護入力（例: 曲の .lab ラベル）を JSON で上書きしてしまう
    # ため、--staging-dir 外を指す --lang-def もここで衝突ガード対象に含める
    # （--staging-dir 配下を指す場合は build_dir 経由で swap に含まれ、
    # 既存の --staging-dir チェックで保護済みのため対象外）。
    #
    # [P1 修正] (review #263 R9) 上記は --staging-dir 本体のみを検査して
    # いたが、`_swap_into_place` が実際に削除・rename するのは
    # `<staging-dir>.old`（毎回 rmtree）と `<staging-dir>.build-<pid>`
    # （成功時に --staging-dir へ rename）という派生パスであり、これらは
    # チェック対象外だった。例えば `--staging-dir=/tmp/published`,
    # `--pjs-root=/tmp/published.old` は本チェックを素通りしたのち、公開時
    # に `old_dir` の `rmtree` が `pjs_root`（保護入力）そのものを削除する。
    # 派生パスを算出してガード対象に加える（build_dir は後段の実際の構築
    # 先と同じ pid 由来の同一パスを再利用し、二重定義を避ける）。
    old_dir = staging_dir.parent / f"{staging_dir.name}.old"
    build_dir = staging_dir.parent / f"{staging_dir.name}.build-{os.getpid()}"

    guarded_outputs: List[Path] = [staging_dir, old_dir, build_dir]
    if args.lang_def is not None:
        try:
            args.lang_def.resolve().relative_to(staging_dir.resolve())
        except ValueError:
            guarded_outputs.append(args.lang_def)
    try:
        _reject_output_collision(guarded_outputs, protected_roots=[args.pjs_root, args.converter_dir])
    except OutputCollisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    swapped = False
    try:
        missing_pairs: List[str] = []
        n_staged = stage_song_wavs(args.pjs_root, build_dir, missing_pairs)
        if n_staged == 0:
            print(f"error: no pjsNNN song wav found under {args.pjs_root}", file=sys.stderr)
            return 1
        if missing_pairs:
            # [P2 修正] (review #263 R5) .lab/_song.wav の一方が欠けた song
            # ディレクトリを黙って skip すると縮小コーパス（契約=100 曲未満）
            # のまま成功報告してしまうため、swap 前に一括で fail-closed 拒否
            # する（完全な素材では missing_pairs は常に空のため挙動不変）。
            print(
                f"error: {len(missing_pairs)} pjsNNN song dir(s) missing .lab or "
                f"_song.wav under {args.pjs_root} (fail-closed, not staged/published): "
                + ", ".join(missing_pairs),
                file=sys.stderr,
            )
            return 1

        lang_def_path = _resolve_lang_def_path(args.lang_def, staging_dir, build_dir)

        # [P2 修正] (review #263 R10, R11) lang_def_path が --staging-dir 外を
        # 指す場合（= args.lang_def と同一パス）、write_lang_def はここで
        # swap の外側で直接書く。以降の converter 実行〜swap 完了のいずれかが
        # 失敗した場合に原状復帰できるよう、書き込み前の状態を記録しておく
        # （成功時のみ確定）:
        #   - 既存ファイルだった場合: 上書き前の内容をバックアップし、失敗時に
        #     復元する（review #263 R10）。
        #   - 新規パス（元ファイルなし）だった場合: 失敗時に write_lang_def が
        #     新規作成したファイルを削除し、「元々存在しなかった」状態へ戻す
        #     （review #263 R11。バックアップを取らないだけでは新規作成分が
        #     残留し続けてしまうため）。
        lang_def_is_external = args.lang_def is not None and lang_def_path == args.lang_def
        lang_def_pre_existed = lang_def_is_external and lang_def_path.exists()
        lang_def_backup: Optional[bytes] = None
        if lang_def_pre_existed:
            lang_def_backup = lang_def_path.read_bytes()
        lang_def_created_new = lang_def_is_external and not lang_def_pre_existed

        write_lang_def(lang_def_path)

        try:
            result = run_converter(args.converter_dir, build_dir, lang_def_path, args.python_bin)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode != 0:
                print(f"error: db_converter.py failed (exit {result.returncode})", file=sys.stderr)
                _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
                return result.returncode

            _swap_into_place(build_dir, staging_dir)
            swapped = True
        except BaseException:
            _restore_lang_def_backup(lang_def_path, lang_def_backup, created_new=lang_def_created_new)
            raise
    finally:
        if not swapped:
            shutil.rmtree(build_dir, ignore_errors=True)

    out_db = staging_dir / "diffsinger_db"
    print(f"n_song_staged={n_staged}")
    print(f"output_dir={out_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
