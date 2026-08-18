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

## embed ファイル直指定モード（`--speaker-embed-file`、review #265 R4 #11）

`s1_gate/forge_triangle.py`（判定材料④: 3 アンカー重心座標補間バッチ鍛造）が
書き出す `candidate_A.emb`〜`candidate_D.emb` は `ritsu`/`pjs`/`user` いずれの
命名規約（`<exp_name>.<speaker>.emb`）にも従わないため、`--speaker` 経路
（`gate_synth.find_speaker_embed` の `*.<speaker>.emb` glob）では合成できない
——「GPU 実行者が判定材料④を ad-hoc Python なしで合成できない」というギャップ
（review #265 R4 #11）。本モードはこれを埋める。

```bash
python voice_genesis/foundry/s1_gate/gate_synth_run4.py run \
    --diffsinger-repo <DiffSinger clone> --ckpt-dir <run4 checkpoints/<exp_name>/> \
    --step <STEP> --exp-name s1_run4_acoustic_v1 \
    --canon-model-dir <NamineRitsu_DiffSinger 展開先> \
    --vocoder-dir <nsf_hifigan.onnx 展開先> \
    --out-dir <出力先> \
    --speaker-embed-file <forge_triangle 出力先>/candidate_A.emb
```

`--speaker`（`*.<speaker>.emb` glob 経路）と `--speaker-embed-file`（ファイル
直指定）は排他（`argparse` の mutually exclusive group で両方明示指定を拒否
する）。`--speaker-embed-label`（省略時はファイル名の stem、例
`candidate_A.emb` → `candidate_A`）は出力ファイル名 suffix・print・summary
record 用のラベル文字列であり、`SPEAKER_TO_SPK_ID` の対象外（`candidate_*`
は run4 3 話者のどれか 1 つではなく三角形内部の合成個体のため spk_id を
持たない）。

**一次ソース確認済みの配線経路**（`gate_synth.py` 側は無変更のまま）:
`run_pipeline(...)`（`gate_synth.py:1055-1064`）は `speaker_name`/
`speaker_embed_vector` を直接引数に取り、`synth_song(...)`
（`gate_synth.py:1284-1295`、`_cmd_run_impl` から `speaker_name=args.speaker,
speaker_embed_vector=speaker_embed_vector` で呼ばれる — `gate_synth.py:2061`）
がそのまま透過する。しかし `_cmd_run_impl` は `speaker_embed_vector` を
外部から直接注入できる引数を持たず、`args.speaker` から
`find_speaker_embed()` → `load_speaker_embed_vector_with_sha()` という固定
経路でしか値を得ない（`gate_synth.py:1874/1882-1885`。いずれも `_cmd_run_impl`
内のモジュールグローバル参照 — private 関数のため外部から差し替え引数を
渡す公開フックが無い）。本モードはこの 2 関数を **本プロセス内・
`gate_synth.cmd_run(args)` 呼び出しの間だけ**（`try/finally` で必ず復元）
差し替え、`find_speaker_embed` は常に `--speaker-embed-file` のパスを返し、
`load_speaker_embed_vector_with_sha` は常に事前ロード済みの候補ベクトル
（`forge_triangle.load_embed_vector_with_sha` で 384 有限 float32 検証済み）を
返すようにする。これにより `_cmd_run_impl` の残り全体（export 判定・
own/canon token 符号化・provenance sha 記録・原子的出力公開・rollback）は
**無改変のまま**再利用され、`run_pipeline` は最終的に本物の
`speaker_embed_vector` 実引数として候補 embed を受け取る。

この手法はモジュール属性の一時差し替え（モンキーパッチ）であり
`gate_synth.py` のファイル自体には一切触れない（要件 (b) 充足）。リスクは
`_cmd_run_impl` が将来 `find_speaker_embed`/`load_speaker_embed_vector_with_sha`
の参照方法を変える（別名 import・クラスメソッド化等）とこの差し替えが
無音で効かなくなる点 — 本環境は onnxruntime 非導入のため
`_cmd_run_impl` を実行するテストで検知できず（GPU 実測未実施の一部として
正直明記する）、フェイク `gate_synth` モジュールでの委譲経路テストのみが
本環境の担保。

## review #265 R6（PR #265 Codex 第6波レビュー、採用済み — #10/#11 の残穴）

- **#13 (P1)**: `gate_synth.run_pipeline` は acoustic.onnx の入力名から
  `stage3_mode` を判定し（`gate_synth.py:1097-1101`）、`canon_ddpm`
  （単一話者・canon 配布モデル）を選ぶと `speaker_embed_vector` は stage3 で
  一切消費されない（`gate_synth.py:1249-1263` の `else` 分岐は
  `speaker_embed_vector` 引数を参照すらしない）。一次ソース確認済み: これは
  `run_pipeline` 側のエラーにならず**静かに無視される**ため、`--speaker-
  embed-file` で候補 A〜D それぞれ異なる embed を渡しても canon モデルでは
  全候補が同一声の WAV になり、ブラインドバッチが（気づかれないまま）
  無意味に成立してしまう。`_require_reflow_multi_speaker_acoustic` が
  委譲前に acoustic.onnx の入力名を検査し（`gate_synth.ort`
  = `gate_synth.py` が `import onnxruntime as ort` 済みのモジュール属性を
  read-only で再利用、別セッションを開くだけで `gate_synth.py` 自体は
  無改変）、`{"spk_embed", "steps"}` を含まなければ「canon モデルは直指定
  モード不可」を明記して fail-closed する。この検査には解決済みの
  acoustic.onnx 実体が要る（export 前は実体が存在しない）ため、
  `--speaker-embed-file` は `--skip-export`（既に export 済みの
  `--acoustic-dir`）を必須とする — 委譲を跨いだ export 実行経路
  （`--skip-export` 無し）はこの検証が構造的にできないため対象外とし、
  fail-closed で拒否する。
- **#14 (P1)**: `gate_synth.cmd_run` の原子公開は `out_dir`（`--step` 指定時
  は `<out_dir>/step_<N>`）と、その `.old`/`.build-<pid>` 退避パスを
  `_swap_step_dir_into_place` で置換する（`gate_synth.py:1753-1762`
  `synth_out_dir`/`build_dir`/`old_dir` の算出を一次ソースとしてそのまま
  再現）。`gate_synth.py` 自身の `_reject_output_collision`
  （`gate_synth.py:1785-1796`）は `--acoustic-dir`/`--canon-model-dir`/
  `--vocoder-dir`/`--ckpt-dir` は保護するが、本ラッパー固有の
  `--speaker-embed-file` を知らないため保護対象に含まれない。
  `--speaker-embed-file` がこれら swap 管理パスの配下に解決されると、
  合成中に入力 embed が破壊されうる。`_reject_speaker_embed_file_swap_
  collision` が `forge_triangle._reject_embed_input_collisions`（#9 と同型の
  双方向包含検査、read-only import で再利用）を `synth_out_dir`/
  `build_dir`/`old_dir`（+ `--step` 指定時は `out_dir` 自身）それぞれへ
  適用し、委譲前に fail-closed で拒否する。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Sequence

_THIS_DIR = Path(__file__).resolve().parent

# --- sibling import: s1_gate/forge_triangle.py（read-only import。384 有限
# float32 embed 検証 `load_embed_vector_with_sha` を再利用する — review #265
# R4 #11 (a)）。forge_triangle.py は onnxruntime に依存しない（numpy +
# s1_dataprep/convert_d3.py の sibling import のみ）ため、`gate_synth.py` と
# 異なりモジュールトップレベルで import して問題ない。
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
import forge_triangle  # noqa: E402

# spk_id map v2（`s1_dataprep/assemble_run4.py` の `SPK_IDS` と同一の表。
# ritsu/pjs/user は run 3/run 4 checkpoint 割当を変更できないため 0/1/2 固定、
# d3synth は run 5 で新設された第 4 話者（合成教師）として次の空き番号 3 —
# DESIGN_S4_run5.md §1.1/§2-4。`--speaker d3synth` は合成教師声そのものの
# 立ちを聴くゲート判定材料の 1 系統（三角形 Identity 空間には入れない —
# DESIGN_S4 §4-4。gate_synth.py 側は `*.d3synth.emb` の文字列 glob で
# そのまま解決するため、export 済み embed さえあれば既存経路で合成される）。
SPEAKER_TO_SPK_ID = {"ritsu": 0, "pjs": 1, "user": 2, "d3synth": 3}
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
    # review #265 R4 #11: `--speaker`/`--speaker-embed-file` の排他は
    # `argparse.add_mutually_exclusive_group()` を使わない。CPython の
    # mutex group 実装（`argparse.py` `take_action`）は
    # `argument_values is not action.default` でしか「明示指定されたか」を
    # 判定しないため、明示指定した値が **たまたまデフォルト値と同一
    # （`--speaker ritsu`）** だと検出をすり抜ける（実測で確認済み・
    # `test_gate_synth_run4.py` に固定）。`--speaker` の既定値を `None` にし、
    # `main()` で両方非 None のときのみ明示的に拒否する（デフォルト適用は
    # 両方省略された場合にのみ `main()` 側で行う）。
    p_run.add_argument(
        "--speaker", choices=SPEAKER_CHOICES, default=None,
        help="reflow 多話者 acoustic 用の話者選択"
             "（run 5 拡張: ritsu/pjs/user/d3synth）。"
             "acoustic ディレクトリの '*.<speaker>.emb' を読み込んで spk_embed を"
             "構築する処理は gate_synth.py 側の実装をそのまま使う（本ラッパーは"
             "choices のみ拡張・spk_id は関与しない。docstring 参照）。"
             "既定 'ritsu'（--speaker/--speaker-embed-file 両方省略時）。"
             "--speaker-embed-file と排他。",
    )
    p_run.add_argument(
        "--speaker-embed-file", default=None,
        help="384-dim float32 spk_embed ファイルを直接指定する（判定材料④:"
             " forge_triangle.py の candidate_A.emb〜D.emb 等）。--speaker と"
             "排他。docstring「embed ファイル直指定モード」参照。",
    )
    p_run.add_argument(
        "--speaker-embed-label", default=None,
        help="--speaker-embed-file 使用時の出力ファイル名 suffix / 記録用ラベル"
             "（省略時はファイル名の stem。例: candidate_A.emb → 'candidate_A'）。"
             "--speaker-embed-file 未指定時は無視される。",
    )
    p_run.set_defaults(func="run")
    return parser


def _reject_speaker_embed_file_swap_collision(args: argparse.Namespace) -> None:
    """review #265 R6 #14: `--speaker-embed-file` が `gate_synth.cmd_run` の
    原子公開が置換する管理パス（`synth_out_dir`/`build_dir`/`old_dir` —
    `gate_synth.py:1753-1762` の算出をそのまま再現）の配下に解決される場合、
    委譲前に fail-closed で拒否する。`forge_triangle._reject_embed_input_
    collisions`（#9 と同型の双方向包含検査）を read-only import で再利用する
    （判定は `forge_triangle.convert_d3.OutputCollisionError`、`ValueError`
    のサブクラス、で送出される）。
    """
    out_dir = Path(args.out_dir)
    synth_out_dir = (out_dir / f"step_{args.step}") if args.step is not None else out_dir
    # gate_synth.py:1761-1762 と同一の算出。cmd_run は本プロセス内で呼ばれる
    # ため os.getpid() は cmd_run 内部の計算値と一致する。
    build_dir = synth_out_dir.parent / f"{synth_out_dir.name}.build-{os.getpid()}"
    old_dir = synth_out_dir.parent / f"{synth_out_dir.name}.old"

    managed_dirs = [synth_out_dir, build_dir, old_dir]
    if out_dir.resolve() != synth_out_dir.resolve():
        managed_dirs.append(out_dir)

    embed_path = Path(args.speaker_embed_file)
    for managed_dir in managed_dirs:
        forge_triangle._reject_embed_input_collisions(managed_dir, [embed_path])


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require_reflow_multi_speaker_acoustic(args: argparse.Namespace, gate_synth) -> str:
    """review #265 R6 #13: acoustic.onnx が `spk_embed`/`steps` 入力を持つ
    reflow 多話者モデルであることを委譲前に検証する（`gate_synth.py:1097-1101`
    `is_reflow_multi_speaker` 判定と同一条件）。無ければ
    `speaker_embed_vector` が `canon_ddpm` 分岐（`gate_synth.py:1249-1263`）で
    黙って無視され、候補 A〜D が同一声のままブラインドバッチとして成立
    してしまう（fail-closed で拒否する）。

    本検査には解決済みの acoustic.onnx 実体が要る。`--skip-export` 無し
    （委譲先の `_cmd_run_impl` が export.py を実行して初めて実体が確定する
    経路）では export 完了前にこの検証ができないため、`--speaker-embed-file`
    は `--skip-export`（+ 既に export 済みの `--acoustic-dir`）を必須とする。

    戻り値は検証に使ったバイト列そのものから計測した acoustic.onnx の
    sha256（review #265 R12 P1）。`gate_synth.py` を変更せずに以下の TOCTOU
    を緩和するための「単一読み取り束縛」ファミリーの標準手当て: 本関数は
    ここで読んだバイト列をそのまま `gate_synth.ort.InferenceSession` へ渡す
    （パス再オープンではなく bytes 渡し——`onnxruntime.InferenceSession` は
    `bytes` を第1引数に取れる）ため、検証した sha256 と検証したモデルの
    入力集合は同一の読み取りに拠って一致することが構造的に保証される。
    しかしこの直後に `gate_synth.cmd_run()` が同じパスを**独立に**再オープン
    するため（`gate_synth.py` は変更しない制約上、その読み取り自体は避けられ
    ない）、呼び出し元 `_run_with_speaker_embed_file` は本関数が返す sha256
    を保持し、`cmd_run` 完了後に同パスを再ハッシュして一致を検証する
    （`_verify_acoustic_onnx_unchanged_after_cmd_run` docstring 参照）。
    """
    if not args.skip_export:
        raise SystemExit(
            "error: --speaker-embed-file requires --skip-export (with --acoustic-dir "
            "pointing at an already-exported acoustic.onnx) so the model's spk_embed/steps "
            "inputs can be verified before delegating — on-the-fly export is not supported "
            "in this mode (fail-closed, review #265 R6 #13)."
        )
    if not args.acoustic_dir:
        raise SystemExit(
            "error: --speaker-embed-file requires --acoustic-dir (paired with --skip-export)."
        )
    acoustic_onnx_path = Path(args.acoustic_dir) / "acoustic.onnx"
    if not acoustic_onnx_path.exists():
        raise SystemExit(
            f"error: --speaker-embed-file requires an already-exported acoustic.onnx at "
            f"{acoustic_onnx_path} (--acoustic-dir); not found."
        )
    acoustic_bytes = acoustic_onnx_path.read_bytes()
    acoustic_sha256 = hashlib.sha256(acoustic_bytes).hexdigest()
    session = gate_synth.ort.InferenceSession(
        acoustic_bytes, providers=["CPUExecutionProvider"],
    )
    input_names = {i.name for i in session.get_inputs()}
    if not {"spk_embed", "steps"}.issubset(input_names):
        raise SystemExit(
            f"error: acoustic.onnx at {acoustic_onnx_path} does not expose 'spk_embed'/'steps' "
            "inputs — canon モデルは直指定モード不可（this is a canon single-speaker DDPM "
            "model; --speaker-embed-file requires a reflow multi-speaker acoustic model, "
            "gate_synth.py is_reflow_multi_speaker check gate_synth.py:1097-1101). Passing "
            "this model would silently ignore speaker_embed_vector in the canon_ddpm branch "
            "(gate_synth.py:1249-1263) and render every candidate identically (fail-closed, "
            "review #265 R6 #13)."
        )
    return acoustic_sha256


def _rollback_or_remove_published_step_dir(synth_out_dir: Path) -> bool:
    """drift 検出時のロールバック本体（review #265 R13 P2）。

    一次ソース確認済み: `gate_synth.py` の `_swap_step_dir_into_place`
    （`gate_synth.py:1531-1645`）は、swap のたびに旧世代を削除せず
    `<synth_out_dir>.old`（`gate_synth.py:1618`
    `old_dir = out_dir.parent / f"{out_dir.name}.old"`）へ退避する意味論を
    持つ。したがって drift 検出時に新規公開分（`synth_out_dir`）を単純
    `rmtree` するだけでは (a) 正本出力が空になり、(b) 有効だった旧世代が
    `.old` に取り残されたままになる（`_swap_step_dir_into_place` 冒頭の
    `.old` 無条件 `shutil.rmtree`——`gate_synth.py:1632`——が次回実行時に
    それを消費してしまい得る）。

    `gate_synth.py` 自身も同じ状況（swap 後段の失敗による取り消し）向けに
    `_rollback_swapped_step_dir`（`gate_synth.py:1648-1694`）を持つ。
    `gate_synth.py` は変更しない制約のため、本関数は private 関数を跨ぎ
    import で再利用するのではなく、その意味論をそのまま複製する（本ファイル
    既存の `_reject_speaker_embed_file_swap_collision` が
    `synth_out_dir`/`build_dir`/`old_dir` の算出を複製している方式と同型）:
    `.old` が存在すればそれが真の旧世代であり、`synth_out_dir` を退けて
    `.old` を `synth_out_dir` へ戻す（復元）。`.old` が存在しなければ今回が
    初回公開だったということであり、`synth_out_dir` を削除して「未公開」
    状態へ戻す（呼び出し元が正本不在をエラーメッセージで明示する）。

    戻り値: 旧世代を `.old` から復元できた場合 `True`、初回公開のため撤去
    のみだった場合 `False`。
    """
    old_dir = synth_out_dir.parent / f"{synth_out_dir.name}.old"
    restored = old_dir.exists()
    if restored:
        if synth_out_dir.exists():
            shutil.rmtree(synth_out_dir)
        old_dir.rename(synth_out_dir)
    elif synth_out_dir.exists():
        shutil.rmtree(synth_out_dir, ignore_errors=True)
    return restored


def _verify_acoustic_onnx_unchanged_after_cmd_run(
    args: argparse.Namespace, verified_sha256: str
) -> None:
    """review #265 R12 P1: `_require_reflow_multi_speaker_acoustic` が検証時
    に計測した acoustic.onnx の sha256（`verified_sha256`）を、
    `gate_synth.cmd_run()` 完了後・公開結果を残す前に同パスの再ハッシュと
    照合する（TOCTOU 対策）。

    `_require_reflow_multi_speaker_acoustic` はディスク上の acoustic.onnx を
    検証してから spk_embed/steps 入力を確認するが、直後に呼ばれる
    `gate_synth.cmd_run()`（`gate_synth.py` は不変のため変更できない）は
    同じパスを**独立に**再オープンして実際の合成へ使う。この 2 回の読み取り
    の間にモデルファイルが差し替わると、「reflow モデルを検証したうえで
    canon モデルで実際に合成する」という状態が成立し得て、選択した
    `--speaker-embed-file` が canon_ddpm 分岐で黙って無視された blind
    バッチが公開されてしまう（review #265 R6 #13 が防いだ穴の TOCTOU 版）。

    `gate_synth.py` の API 契約は変えられない（cmd_run へバイト列を注入する
    フックが無い）ため、ここでは事後の再ハッシュ照合という単一読み取り
    束縛ファミリーの標準的な緩和策を採る: 不一致を検出したら、
    `gate_synth.cmd_run` が既に書き終えている公開結果（`synth_out_dir` —
    `gate_synth.py:1753-1762` の算出をそのまま再現、`--step` 指定時は
    `<out_dir>/step_<N>`）を撤去する。

    review #265 R13 P2: 旧実装は単純 `rmtree` のみだったため、
    `gate_synth.cmd_run`（delegate）の swap が `<synth_out_dir>.old` へ退避
    済みの旧有効世代を残したまま正本を空にしていた（次回実行の `.old`
    無条件 rmtree でその旧世代ごと消失し得る）。`_rollback_or_remove_
    published_step_dir`（本ファイル内、`gate_synth.py` の
    `_swap_step_dir_into_place`/`_rollback_swapped_step_dir` 意味論をそのまま
    複製）に委譲し、`.old` があれば復元、無ければ（初回実行）撤去のみに
    留めて正本不在を明示する。"""
    acoustic_onnx_path = Path(args.acoustic_dir) / "acoustic.onnx"
    actual_sha256 = _sha256_file(acoustic_onnx_path)
    if actual_sha256 == verified_sha256:
        return

    out_dir = Path(args.out_dir)
    synth_out_dir = (out_dir / f"step_{args.step}") if args.step is not None else out_dir
    old_dir = synth_out_dir.parent / f"{synth_out_dir.name}.old"
    restored = _rollback_or_remove_published_step_dir(synth_out_dir)

    if restored:
        disposition = (
            f"rolled back {synth_out_dir} to the previous generation restored from {old_dir} "
            "(gate_synth.py _swap_step_dir_into_place keeps the prior generation at "
            "<synth_out_dir>.old until the next successful swap — gate_synth.py:1618)"
        )
    else:
        disposition = (
            f"removed the newly published {synth_out_dir} — no previous generation existed at "
            f"{old_dir} (this was the first publish to this --out-dir), so the canonical output "
            f"is now ABSENT; re-run once a trustworthy acoustic.onnx is confirmed at "
            f"{acoustic_onnx_path} before relying on {synth_out_dir}"
        )

    raise SystemExit(
        f"error: acoustic.onnx at {acoustic_onnx_path} changed between "
        "_require_reflow_multi_speaker_acoustic's verification and gate_synth.cmd_run's "
        f"independent reopen of the same path (sha256 verified={verified_sha256} != "
        f"post-run={actual_sha256}). The reflow spk_embed/steps check may no longer apply to "
        "the model that was actually used for synthesis — the selected --speaker-embed-file "
        "could have been silently ignored by a swapped-in canon model, producing a blind "
        f"batch. {disposition} (fail-closed, TOCTOU mitigation, review #265 R13 P2)."
    )


def _run_with_speaker_embed_file(args: argparse.Namespace, gate_synth) -> None:
    """`--speaker-embed-file` 経由の委譲（review #265 R4 #11、R6 #13/#14 で
    委譲前検査を追加）。docstring 冒頭「embed ファイル直指定モード」の設計
    をそのまま実装する: `gate_synth` の `find_speaker_embed`/
    `load_speaker_embed_vector_with_sha` を `gate_synth.cmd_run(args)`
    呼び出しの間だけ差し替え、事前ロード・検証済みの候補ベクトルを
    `run_pipeline` の `speaker_embed_vector` 実引数へ届ける。差し替えは必ず
    `try/finally` で復元する（`gate_synth` はプロセス内で共有される
    モジュールオブジェクトのため、復元漏れは後続呼び出しを汚染しうる）。
    """
    embed_path = Path(args.speaker_embed_file)
    # 384 有限 float32 検証込み（P1 と同一 validator を read-only import で再利用）。
    vector, digest = forge_triangle.load_embed_vector_with_sha(embed_path)

    # review #265 R6: #14（swap 保護漏れ）→ #13（canon/単一話者モデル）の順で
    # 委譲前に fail-closed する（いずれも I/O が軽い順 — #14 はパス比較のみ、
    # #13 は onnxruntime セッション構築を伴う）。
    _reject_speaker_embed_file_swap_collision(args)
    acoustic_sha256_verified = _require_reflow_multi_speaker_acoustic(args, gate_synth)

    label = args.speaker_embed_label or embed_path.stem
    print(
        f"| gate_synth_run4: speaker-embed-file={embed_path} "
        f"(label={label!r}, sha256={digest})"
    )

    run_args = argparse.Namespace(**vars(args))
    run_args.speaker = label  # _cmd_run_impl 内では print/出力ファイル名 suffix/summary record にのみ使われる文字列

    original_find_speaker_embed = gate_synth.find_speaker_embed
    original_load_speaker_embed_vector_with_sha = gate_synth.load_speaker_embed_vector_with_sha

    def _fixed_find_speaker_embed(acoustic_dir, speaker, export_basename=None):
        return embed_path

    def _fixed_load_speaker_embed_vector_with_sha(path):
        return vector, digest

    gate_synth.find_speaker_embed = _fixed_find_speaker_embed
    gate_synth.load_speaker_embed_vector_with_sha = _fixed_load_speaker_embed_vector_with_sha
    try:
        gate_synth.cmd_run(run_args)
    finally:
        gate_synth.find_speaker_embed = original_find_speaker_embed
        gate_synth.load_speaker_embed_vector_with_sha = original_load_speaker_embed_vector_with_sha

    # review #265 R12 P1: cmd_run が正常完了した場合のみ、検証時に計測した
    # acoustic.onnx の sha256 と再ハッシュを照合する（TOCTOU 対策。
    # § `_verify_acoustic_onnx_unchanged_after_cmd_run` docstring 参照）。
    # cmd_run が例外を送出した場合はここへ到達せず、上の finally で
    # モンキーパッチ復元のみ行って再送出される。
    _verify_acoustic_onnx_unchanged_after_cmd_run(args, acoustic_sha256_verified)


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.speaker is not None and args.speaker_embed_file is not None:
        raise SystemExit(
            "error: --speaker and --speaker-embed-file are mutually exclusive "
            f"(got --speaker={args.speaker!r} and --speaker-embed-file={args.speaker_embed_file!r})"
        )

    gate_synth = _import_gate_synth()

    if args.speaker_embed_file is not None:
        _run_with_speaker_embed_file(args, gate_synth)
        return

    args.speaker = args.speaker or "ritsu"  # gate_synth.py p_run と同一の既定値
    spk_id = resolve_spk_id(args.speaker)
    print(f"| gate_synth_run4: speaker={args.speaker} (spk_id={spk_id}, spk_id map v2)")
    gate_synth.cmd_run(args)


if __name__ == "__main__":
    main()
