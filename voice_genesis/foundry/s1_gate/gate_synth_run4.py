"""S3 run 4 ゲート判定材料② — spk3（user）アンカー単独合成の薄いラッパー。

**GPU 実測未実施・run 4 の 5K 早期ゲートが初実行**（本ファイルは onnxruntime を
本開発環境に持たないため、ロジック層（引数パース・speaker 解決・
`gate_synth.cmd_run` への委譲引数の組み立て）しか本環境で検証できていない。
実合成の実行は run 4 クロー側の GPU 環境が初回実測になる）。

対応: `S3_RUN4_RUNBOOK.md` §5②・`DESIGN_S3_backfill.md` §4・§8「クロー側で
要確認」#4。

## 背景（ギャップ）

`s1_gate/gate_synth.py`（**本ファイルは変更しない** — canon 回帰 5 本が本環境
では onnxruntime 未導入により実行不能なため、`gate_synth.py` に触れる変更は
実測担保なしに行わない設計判断。`S3_RUN4_RUNBOOK.md` §8 冒頭）の `run`
サブコマンドは `--speaker choices=["ritsu", "pjs"]` にハードコードされており
（`gate_synth.py:2444`）、run 4 で追加された第 3 話者 `user`（`spk_id=2`。
`s1_dataprep/assemble_run4.py` が確定させた spk_id 割当 = ritsu:0/pjs:1/user:2）
を選べない。

## 一次ソース確認済みの委譲構造（`gate_synth.py` の内部構造を変更せず再利用する）

`gate_synth.py` を読むと、`--speaker` は **spk_id 整数を一切経由しない**、
純粋な文字列ベースのファイル名 glue であることが分かる:

- `find_speaker_embed(acoustic_dir, speaker, export_basename)`
  （`gate_synth.py:644-673`）は `acoustic_dir.glob(f"*.{speaker}.emb")` で
  export 済み話者 embedding（`<exp_name>.<speaker>.emb`、384-dim float32 raw）
  を探すだけで、`speaker` の値が `"ritsu"`/`"pjs"` であることを検証しない
  （呼び出し元の `argparse.choices` が唯一の制約）。
- `cmd_run` 経由で `_cmd_run_impl(args, rollback_state)`
  （`gate_synth.py:1701-2308`）が `args.speaker` をそのままこの glob へ渡す。
- `spk_id`（0/1/2 の整数、学習時の話者インデックス）は `gate_synth.py` の
  どこにも登場しない。それが必要になるのは学習/export 側
  （`build_dataset.py`・`export.py`）であり、export 済み `*.<speaker>.emb`
  ファイルの中身（384 次元ベクトル）がどの `spk_id` から export されたかは
  ファイル名の対応関係（`<exp_name>.user.emb` が `spk_id=2` の embedding で
  ある保証）で担保される。**したがって `gate_synth.py` 自体には `user` を
  通す経路がすでに存在する**（`--speaker` の choices というバリデーション層
  だけが `user` を拒否している）。

→ 本ラッパーは `argparse.choices` を `("ritsu", "pjs", "user")` へ拡張した
**同型 CLI**（`gate_synth.py` の `run` サブコマンドと同じ引数集合）を提供し、
構築した `argparse.Namespace` をそのまま `gate_synth.cmd_run(args)`
（rollback wrapper 込み。`gate_synth.py:2310-2354`）へ委譲する。合成本体の
ロジック（export・phoneme mapping・acoustic 推論・vocoder 呼び出し・原子的
出力公開）は一切再実装しない — `gate_synth.py` を read-only import するのみ。

## 遅延 import の理由

`gate_synth.py` はモジュールトップレベルで `import onnxruntime as ort`
する（`gate_synth.py:133`）。本開発環境には onnxruntime が未導入のため、
モジュールレベルで `gate_synth` を import すると即座に
`ModuleNotFoundError` になる（既存の `tests/test_gate_synth_*.py` 5 本が
同じ理由で本環境ではコレクションエラーになる — pre-existing、本ファイルの
変更とは無関係）。本ラッパーはこれを踏まえ `_import_gate_synth()` 内でのみ
`gate_synth` を import する（CLI 実行時 = `main()` 到達時にのみ発生）。
これにより「モジュール読み込み（`import gate_synth_run4`）と CLI 引数
パース・speaker 解決のロジック層テスト」は onnxruntime 無しで本環境でも
実行できる。実合成（`gate_synth.cmd_run` 呼び出しそのもの）は onnxruntime
を要求するため本環境では実測不可のまま（GPU 実測は run 4 クロー側が初回）。

## SPEAKER_TO_SPK_ID の位置づけ

`SPEAKER_TO_SPK_ID` は `gate_synth.cmd_run` への委譲には使わない
（前述のとおり `gate_synth.py` は spk_id を要求しない）。CLI 実行時の
情報表示・呼び出し側の record 記帳用の参照テーブルとして提供する
（`s1_dataprep/assemble_run4.py` が固定した spk_id 割当と一致させる）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

_THIS_DIR = Path(__file__).resolve().parent

# run 4 の 3 話者 spk_id 割当（`s1_dataprep/assemble_run4.py` が固定した表と
# 同一。ritsu/pjs は run 3 までの checkpoint 割当を変更できないため 0/1 固定、
# user は run 4 で新規追加された第 3 話者として次の空き番号 2）。
SPEAKER_TO_SPK_ID = {"ritsu": 0, "pjs": 1, "user": 2}
SPEAKER_CHOICES: tuple = tuple(SPEAKER_TO_SPK_ID)


def resolve_spk_id(speaker: str) -> int:
    """`SPEAKER_TO_SPK_ID` から spk_id を引く。`gate_synth.cmd_run` への委譲
    引数には使わない（docstring 参照）— CLI 表示・record 記帳専用。"""
    try:
        return SPEAKER_TO_SPK_ID[speaker]
    except KeyError as exc:
        raise ValueError(
            f"unknown speaker {speaker!r}; expected one of {SPEAKER_CHOICES}"
        ) from exc


def _import_gate_synth():
    """`gate_synth.py` を遅延 import する（モジュールトップレベルで
    `import onnxruntime` するため、本関数を呼ぶまで onnxruntime は要求
    されない。docstring 冒頭「遅延 import の理由」参照）。

    既存 sibling スクリプト（`s1_dataprep/assemble_run4.py` の
    `build_dataset`/`convert_d3` import）と同じ `sys.path` 経由の慣用パターン
    を踏襲する。
    """
    if str(_THIS_DIR) not in sys.path:
        sys.path.insert(0, str(_THIS_DIR))
    import gate_synth as _gate_synth  # noqa: E402 (遅延 import)

    return _gate_synth


def build_arg_parser() -> argparse.ArgumentParser:
    """`gate_synth.py` の `run` サブコマンド（`gate_synth.py:2422-2447`）と
    同一の引数集合を提供する（`--speaker` の choices のみ `user` を追加）。
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser(
        "run", help="export(任意) -> さくら/うみ合成 -> WAV（gate_synth.py run への委譲）"
    )
    p_run.add_argument("--diffsinger-repo", help="openvpi/DiffSinger clone (e2307b1)")
    p_run.add_argument("--ckpt-dir", help="回収した checkpoints/<exp_name>/ (ckpt+config.yaml)")
    p_run.add_argument("--exp-name", default="s1_gate")
    p_run.add_argument("--step", type=int)
    p_run.add_argument(
        "--skip-export", action="store_true",
        help="export.py を走らせず --acoustic-dir の acoustic.onnx をそのまま使う（事前検証用）",
    )
    p_run.add_argument(
        "--acoustic-dir",
        help="--skip-export 時: acoustic.onnx (+任意で *.phonemes.json) の所在",
    )
    p_run.add_argument("--canon-model-dir", required=True, help="NamineRitsu_DiffSinger 展開先")
    p_run.add_argument("--vocoder-dir", required=True, help="nsf_hifigan.onnx 展開先")
    p_run.add_argument("--out-dir", required=True)
    p_run.add_argument("--song", default="sakura,umi", help="カンマ区切り (sakura,umi)")
    p_run.add_argument(
        "--notes-limit", type=int, default=None,
        help="先頭 N ノートのみ合成（S0 互換検証用。省略時は全曲）",
    )
    p_run.add_argument(
        "--tokens", choices=["own", "canon"], default="own",
        help="acoustic への tokens 符号化方式。既定 'own' は fail-closed（gate_synth.py と同一契約）。",
    )
    p_run.add_argument(
        "--singer-dir", default=None,
        help="score.py/score_umi.py の所在（既定: gate_synth.py の既定と同一）",
    )
    p_run.add_argument(
        "--speaker", choices=SPEAKER_CHOICES, default="ritsu",
        help="reflow 多話者 acoustic 用の話者選択（run 4 拡張: ritsu/pjs/user）。"
             "acoustic ディレクトリの '*.<speaker>.emb' を読み込んで spk_embed を"
             "構築する処理は gate_synth.py 側の実装をそのまま使う（本ラッパーは"
             "choices のみ拡張・spk_id は関与しない。docstring 参照）。",
    )
    p_run.set_defaults(func="run")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    spk_id = resolve_spk_id(args.speaker)
    print(f"| gate_synth_run4: speaker={args.speaker} (spk_id={spk_id}, run4 3-speaker table)")

    gate_synth = _import_gate_synth()
    gate_synth.cmd_run(args)


if __name__ == "__main__":
    main()
