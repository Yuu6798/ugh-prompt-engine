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
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

# nnsvs-db-converter 同梱の `lang.sample.json` と同一内容
# （`s1a_conversion_record.md` §2: 日本語ラベルのモーラ分割規則としてそのまま
# 妥当だったため採用。本スクリプトはこの内容を明示的に書き出すことで、外部
# clone 内のサンプルファイルへの暗黙依存を避ける）。
DEFAULT_LANG_DEF = {
    "vowels": ["a", "i", "u", "e", "o", "N", "A", "I", "U", "E", "O"],
    "liquids": {"w": ["k", "g"], "y": True},
}


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
                continue
            raise OutputCollisionError(
                f"output path {p} is inside protected input root {root}（fail-closed で拒否）"
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
    """
    pjs_root = pjs_root.resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    song_dirs = sorted(p for p in pjs_root.iterdir() if p.is_dir() and p.name.startswith("pjs"))
    n_staged = 0
    for d in song_dirs:
        name = d.name
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
    path.write_text(json.dumps(DEFAULT_LANG_DEF, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


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
    """
    old_dir = staging_dir.parent / f"{staging_dir.name}.old"
    if old_dir.exists():
        shutil.rmtree(old_dir)
    if staging_dir.exists():
        staging_dir.rename(old_dir)
    build_dir.rename(staging_dir)


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
    try:
        _reject_output_collision([staging_dir], protected_roots=[args.pjs_root, args.converter_dir])
    except OutputCollisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    build_dir = staging_dir.parent / f"{staging_dir.name}.build-{os.getpid()}"
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
        write_lang_def(lang_def_path)

        result = run_converter(args.converter_dir, build_dir, lang_def_path, args.python_bin)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.returncode != 0:
            print(f"error: db_converter.py failed (exit {result.returncode})", file=sys.stderr)
            return result.returncode

        _swap_into_place(build_dir, staging_dir)
        swapped = True
    finally:
        if not swapped:
            shutil.rmtree(build_dir, ignore_errors=True)

    out_db = staging_dir / "diffsinger_db"
    print(f"n_song_staged={n_staged}")
    print(f"output_dir={out_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
