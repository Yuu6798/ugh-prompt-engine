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
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

# nnsvs-db-converter 同梱の `lang.sample.json` と同一内容
# （`s1a_conversion_record.md` §2: 日本語ラベルのモーラ分割規則としてそのまま
# 妥当だったため採用。本スクリプトはこの内容を明示的に書き出すことで、外部
# clone 内のサンプルファイルへの暗黙依存を避ける）。
DEFAULT_LANG_DEF = {
    "vowels": ["a", "i", "u", "e", "o", "N", "A", "I", "U", "E", "O"],
    "liquids": {"w": ["k", "g"], "y": True},
}


def stage_song_wavs(pjs_root: Path, staging_dir: Path) -> int:
    """`PJS_corpus_ver1.1/pjsNNN/` 配下の song 系のみをシンボリックリンクで
    `pjsNNN.lab` / `pjsNNN.wav` としてリネームする。冪等（既存リンクは張り
    直す）。戻り値はステージングできたペア数。

    `pjs_root` が相対パスの場合、シンボリックリンクのターゲットは
    `staging_dir` からの相対として解釈されて壊れるため、リンク先は必ず
    `resolve()` した絶対パスで張る。
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
            continue
        for dst, src in ((staging_dir / f"{name}.lab", lab_src), (staging_dir / f"{name}.wav", wav_src)):
            if dst.is_symlink() or dst.exists():
                dst.unlink()
            dst.symlink_to(src)
        n_staged += 1
    return n_staged


def write_lang_def(path: Path) -> None:
    path.write_text(json.dumps(DEFAULT_LANG_DEF, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")


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
             "'<staging-dir>/diffsinger_db/' もここに生成される (新規/上書き可、冪等)",
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

    n_staged = stage_song_wavs(args.pjs_root, args.staging_dir)
    if n_staged == 0:
        print(f"error: no pjsNNN song wav found under {args.pjs_root}", file=sys.stderr)
        return 1

    lang_def_path = args.lang_def or (args.staging_dir / "lang.json")
    write_lang_def(lang_def_path)

    result = run_converter(args.converter_dir, args.staging_dir, lang_def_path, args.python_bin)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        print(f"error: db_converter.py failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode

    out_db = args.staging_dir / "diffsinger_db"
    print(f"n_song_staged={n_staged}")
    print(f"output_dir={out_db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
