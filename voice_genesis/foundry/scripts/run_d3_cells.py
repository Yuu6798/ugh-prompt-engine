"""D3 コーパス（40 セル: sakura/umi/d3_sustain/d3_kana × 10 seed）再生成 CLI。

`S3_RUN4_RUNBOOK.md` §2.2 (b) の手順 2〜3（render 全セル + 全数照合）を GPU
実行者が 1 コマンドで実行できるようにする実行スクリプト。マニフェスト正本 =
`results_s3/d3_manifest.json`（殻: scores/seeds/tripwires の事前登録）+
`results_s3/d3_manifest_results.json`（40 セル実測 sha256 = 照合先）。

```
python voice_genesis/foundry/scripts/run_d3_cells.py \\
    --voicebank-root <波音リツ強連続音Ver1.5.1 展開先> \\
    --out-dir <出力ディレクトリ>
```

## 出力レイアウト

```
<out-dir>/
├── specs/                spec 変種（10 本、seed ごと）
├── .staging_render/      render の作業用 staging（全 40 セル PASS 後に render/ へ
│                         atomic swap されて消滅する。実行中に中断・失敗した
│                         run の残骸は次回実行の冒頭で削除される — 本置き場を
│                         汚さない使い捨てディレクトリ）
├── render/               wav + timing csv が同一 stem で同居（40 wav + 40 csv）。
│                         全数照合が全数 PASS した run でのみ更新される
├── cache/                donor bank npz/pkl キャッシュ（render.py --cache-dir）
└── verify_report.txt
```

`render/` に wav と timing csv を**同一ディレクトリへ同居**させるのは、
`s1_dataprep/convert_d3.py` の `discover_pairs()` が単一ディレクトリ非再帰
（`*.wav`/`*.csv` を stem で突き合わせ）で pair を発見する契約のため
（PR #265 R8 指摘 #20）。runbook 手順 4 は本スクリプト実行後、
`--render-dir <out-dir>/render` をそのまま渡せる。

## 手順（設計 = 本ファイル docstring が正、実装が一次ソース）

0. **containment preflight**（review #265 R11 P1）: 削除・書き込みを一切
   開始する前に、管理下の出力パス（`--out-dir`・`.staging_render/`・
   `render/`・swap 退避先 `render.bak_pre_swap`）が入力（`--voicebank-root`・
   `--preset`・`--manifest`・`--results`）と衝突していないかを検査する
   （`_reject_output_collision` — `s1_dataprep/convert_d3.py`/
   `s1_dataprep/convert_pjs.py`/`s1_gate/gate_synth.py` 既存の publisher と
   同型のコピペ実装）。1 件でも衝突すれば非 0 exit で即座に停止する
   （`--voicebank-root`/`--preset`/`--manifest`/`--results` が `<out-dir>`
   配下（または `render/` 配下）に置かれていた場合、次項以降の
   `.staging_render/` 無条件クリーンアップや atomic swap の退避/rmtree が
   これらの入力を巻き込んで削除し得るため）。
0.5. **schema + 登録グリッド検査**（review #265 R13 P2）: `--manifest`/
   `--results` の json.loads 直後、`schema` フィールドが
   `MANIFEST_SCHEMA`/`RESULTS_SCHEMA` と完全一致することを検査する
   （`validate_schema_versions` — `s1_dataprep/convert_user.py` の台帳
   schema 検証と同型）。続けて `index_results_cells` が `(score, seed)` の
   重複エントリを検出し（R12）、さらに `validate_results_cell_grid` が
   `results` の `cells` キー集合を `manifest` の `scores` × `seeds` 直積
   （期待 40 キー）と完全一致比較する。登録グリッド外の一意な余剰セルは
   `main()` の検証ループ（`scores`/`seeds` の直積を走査）が一度も訪問しない
   ため、素通しすると矛盾する results でも 40/40 PASS が出得る——欠落・
   余剰いずれも render 開始前に fail-closed で停止する。
1. **spec 変種生成**（`build_spec_variants`）: base preset
   （既定 `adapter/presets/ritsu_neutral.json`）のテキストから `"seed"` の
   数値部分のみをテキスト置換で差し替える（`json.dumps` によるJSON
   再シリアライズは行わない — インデント・キー順・改行など他のバイトは
   一切変更しない。B2 実測で `spec_sha256` の同一性が確立済みの方式）。
   base preset の元の seed 値と一致するセル（実データでは seed=11）の
   変種は base preset とバイト同一であることを assert する（違えば
   `SpecIdentityError` を送出しレンダ前に停止する — これは「テキスト
   置換の実装自体」の自己診断であり、次項の spec digest 照合とは目的が
   異なる。後者は「今回生成した spec の実バイトが manifest 正本の想定と
   一致するか」を全 40 セルへ横展開する）。
2. **tripwire 先行**: (sakura, 11) と (umi, 11) を最初に render し、wav
   sha256 を `d3_manifest.json` の tripwires と照合する。render 失敗・
   sha256 不一致のいずれも「環境ドリフト・全セル無効」として即座に
   非 0 exit で停止する（残り 38 セルは render しない）。tripwire を含め
   render 出力は本置き場 `render/` ではなく `.staging_render/` へ書く
   （後述レビュー指摘 P1 参照 — tripwire 失敗時に本置き場を傷つけない）。
3. **残り 38 セル**を manifest の `scores`/`seeds` 列挙順（score 外側 /
   seed 内側）で render する（同じく `.staging_render/` へ）。個別セルの
   render 失敗は記録のうえ次のセルへ進む（fail-closed だが 1 セル失敗で
   全体を止めない — 全違反を収集してから §4 で報告する house 慣行）。
4. **全数照合**: 40 セルの spec / wav / timing csv sha256 を
   `d3_manifest_results.json` と全数照合し（`spec_sha256`/`wav_sha256`/
   `timing_csv_sha256` の 3 者。参照先は `.staging_render/`）、per-cell
   PASS/FAIL 表を stdout と `<out-dir>/verify_report.txt` へ出力する。
   1 件でも不一致（またはレンダ未達成）なら非 0 exit で終了する。
   **このセッションで render に失敗したセルは、その照合結果を無条件
   FAIL に強制する**（PR #265 R8 指摘 #21: 既存 `--out-dir` への再実行で
   当該セルの render が失敗しても、前回実行の残存 wav/csv が sha256 一致
   して false PASS になることを防ぐ — `render_cell` が render 前に当該
   セルの既存 staging 出力を削除し、かつ render 失敗セルの集合を記録して
   §4 で強制上書きする二重の防御）。
5. **atomic swap（全数 PASS 時のみ）**: §4 で 40 セル全数 PASS した場合
   に限り、`.staging_render/` を `render/` へ 2 段 rename で atomic に
   差し替える（`_swap_staging_render_into_place` — `gate_synth.py`
   `_swap_step_dir_into_place`/`convert_pjs.py` `_swap_into_place` と同型の
   「旧世代を backup へ退避 → 新世代を rename → backup 削除。新世代
   rename が失敗（`KeyboardInterrupt` 含む）すれば backup から復元」
   パターン）。1 件でも FAIL があれば swap を一切行わず、`render/` は
   run 開始前のバイト列のまま無変更で残る（本レビュー指摘 P1: 既に
   有効な `--out-dir` への再実行で途中セルが失敗しても、旧世代の有効な
   コーパスを失わない）。

## 家風

- **決定論・wall-clock 不使用**: レポートに現在時刻等の非決定的な値を
  書き込まない（sha256 一致/不一致という決定論的事実のみを記録する）。
- **render 呼び出しは `python -m adapter.render`**（`sys.executable` 経由）
  を `cwd=<foundry ディレクトリ>`（本ファイル自身の場所から解決する絶対
  パス — 呼び出し元の cwd に依存しない）で実行し、渡す全パス
  （`--voice`/`--out`/`--timing-out`/`--voicebank-root`/`--cache-dir`）は
  絶対パスへ解決してから渡す。
- **`render.py`/`converter` 群には一切触れない**（本ファイルは新規追加の
  みで、既存ファイルへの変更は含まない）。
- **staging + atomic swap**: 本置き場（`render/`）は「全数 PASS した run
  の完成品」のみを反映する。個別セルの render は常に使い捨ての
  `.staging_render/` へ書き、本置き場への反映は §5 の swap 一箇所に
  限定する（`gate_synth.py`/`convert_pjs.py` 既存の house pattern と同型）。
- **containment preflight**: 削除・rename/rmtree を伴う出力操作の前には
  必ず §0 の `_reject_output_collision` を通す（`gate_synth.py`/
  `convert_pjs.py`/`convert_d3.py` 既存 publisher と同じ house pattern）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# `scripts/` の 1 つ上 = `voice_genesis/foundry/`（`adapter.render` を
# `python -m` で解決できる cwd）。呼び出し元の cwd に依存しない絶対パス解決。
FOUNDRY_DIR = Path(__file__).resolve().parent.parent

DEFAULT_PRESET = FOUNDRY_DIR / "adapter" / "presets" / "ritsu_neutral.json"
DEFAULT_MANIFEST = FOUNDRY_DIR / "results_s3" / "d3_manifest.json"
DEFAULT_RESULTS = FOUNDRY_DIR / "results_s3" / "d3_manifest_results.json"

# D3 は donor=ritsu 固定（manifest `voicebank_pin` が波音リツ強連続音のみを
# 前提とする設計のため、CLI では公開しない）。
DONOR = "ritsu"

# P2 修正 (review #265 R13): `--manifest`/`--results` の schema フィールドの
# 期待値（`results_s3/d3_manifest.json`/`d3_manifest_results.json` の実測値と
# 一致確認済み）。`s1_dataprep/convert_user.py` `LEDGER_SCHEMA`/`load_ledger`
# の台帳 schema 検証と同型の fail-closed を、読み込み直後に行う。
MANIFEST_SCHEMA = "d3-manifest/0.1"
RESULTS_SCHEMA = "d3-manifest-results/0.1"

# tripwire セル: `d3_manifest.json` の `tripwires` キー名（`sakura_seed11_...`/
# `umi_seed11_...`）と対応する固定値。
TRIPWIRE_SEED = 11
TRIPWIRE_SCORES: Tuple[str, ...] = ("sakura", "umi")


class SpecFieldError(ValueError):
    """base preset テキストから `"seed"` フィールドを一意に特定できない場合
    （0 件または複数件ヒット）に送出する。テキスト置換の対象が曖昧なままの
    書き込みを防ぐ fail-closed ガード。"""


class SpecIdentityError(ValueError):
    """base preset の元の seed 値と一致するセルの spec 変種が base preset と
    バイト同一でない場合に送出する（本来テキスト置換のみなら理論上起こり
    得ないが、環境・エンコーディングのドリフトを検出する最終防衛線）。"""


class SchemaMismatchError(ValueError):
    """P2 修正 (review #265 R13): `--manifest`/`--results` の `schema`
    フィールドが `MANIFEST_SCHEMA`/`RESULTS_SCHEMA` と完全一致しない場合に
    送出する（`s1_dataprep/convert_user.py` `LedgerSchemaError`/
    `load_ledger` と同型の fail-closed）。

    schema フィールドが欠落・誤記・未来版であっても、`scores`/`seeds`/
    `cells` 等のキー名が表面上互換であれば `validate_manifest_consistency`/
    `index_results_cells` はそのまま消費してしまう。本例外は他の一切の
    検査・render に先立ち、読み込み直後に検出する。"""


class ManifestConsistencyError(ValueError):
    """`d3_manifest.json` と `d3_manifest_results.json` の
    scores/seeds/cells_total が一致しない場合に送出する（render 前の
    fail-closed 事前検査）。"""


class DuplicateCellError(ValueError):
    """P2 修正 (review #265 R12): `d3_manifest_results.json` の `cells` に
    同一 `(score, seed)` の重複エントリがある場合に送出する（fail-closed）。

    旧実装の `index_results_cells` は `dict` へ index する際に重複キーを
    後勝ちで黙って畳んでいたため、同一 `(score, seed)` に矛盾する 2 つの
    セル記述（例: 異なる `wav_sha256`）が含まれていても、後方の記述だけが
    採用され前方は無検査のまま消える——矛盾を含む results でも 40/40 PASS
    が出得た。本例外は index 構築時（render・検証いずれよりも前）に検出し、
    黙って畳む前に停止する。"""


class CellGridMismatchError(ValueError):
    """P2 修正 (review #265 R13): `d3_manifest_results.json` の `cells`
    キー集合（`(score, seed)`）が、登録済み manifest の `scores` × `seeds`
    直積（期待 40 キー）と完全一致しない場合に送出する（index 構築後・
    render 開始前の fail-closed）。

    R12 の `DuplicateCellError` は同一キーの重複エントリを検出するが、
    「一意だが登録グリッド外の余剰セル」（例: `score="obsolete", seed=1`）は
    素通しする。`main()` は `manifest["scores"]`/`manifest["seeds"]` の直積を
    走査して render・検証するため、この種の余剰セルは検証ループが一度も
    訪問せず、矛盾を含む results でも 40/40 PASS が出得た。本例外は
    余剰・欠落のいずれも fail-closed で検出し、render 前に停止する。"""


class OutputCollisionError(ValueError):
    """P1 修正 (review #265 R11): 本スクリプトが削除・rename する管理下出力
    パス（`--out-dir`・`.staging_render/`・`render/`・swap 用退避先
    `render.bak_pre_swap`）が、resolve 済みの入力（`--voicebank-root`・
    `--preset`・`--manifest`・`--results`）と衝突する場合に送出する
    （fail-closed。削除・書き込み開始前の preflight で検出する）。

    `.staging_render/` の無条件クリーンアップ（`main()` 冒頭の
    `shutil.rmtree(staging_dir)`）と、全数 PASS 時の `_swap_staging_render_into_place`
    （旧世代 `render/` を `.bak_pre_swap` へ退避 → rmtree）は、いずれも
    `--out-dir` 配下または `render/` 配下に入力（特に `--voicebank-root`
    のような巨大な展開済みディレクトリ、または `--preset`/`--manifest`/
    `--results` のような単一ファイル）が置かれていた場合、これらの入力を
    巻き込んで削除し得る。`s1_dataprep/convert_d3.py`/`s1_dataprep/convert_pjs.py`/
    `s1_gate/gate_synth.py` の `OutputCollisionError`/`_reject_output_collision`
    と同型判定（record スクリプト群の既存慣例に倣い、共有モジュール新設では
    なく各ファイル内へコピペ実装する）。"""


def _reject_output_collision(
    out_paths: Sequence[Path],
    protected_roots: Sequence[Path],
    protected_files: Sequence[Optional[Path]] = (),
) -> None:
    """`out_paths`（resolve 後）を相互および `protected_roots`/`protected_files`
    （存在するもののみ、resolve 後）と照合し、衝突があれば削除・書き込み
    開始前に fail-closed で拒否する（`convert_d3.py`/`convert_pjs.py`/
    `gate_synth.py` の同名ヘルパーと同一の resolved 比較ロジック。双方向の
    内包判定を含む）。

    `protected_roots` はディレクトリ全体（配下への包含・逆包含も検査）を
    保護するのに対し、`protected_files` は単一ファイルを完全一致 + 「保護
    ファイルが `out_path` 配下にある」場合のみ検査する（`out_path` が保護
    ファイルの単なる兄弟であるだけなら誤検知しない）。`None`/未存在の要素は
    スキップする。
    """
    resolved_outs = [(p, p.resolve()) for p in out_paths]

    for i, (p_i, r_i) in enumerate(resolved_outs):
        for p_j, r_j in resolved_outs[i + 1 :]:
            if r_i == r_j:
                raise OutputCollisionError(
                    f"output paths collide with each other: {p_i} == {p_j}（fail-closed で拒否）"
                )

    for f in protected_files:
        if f is None:
            continue
        f_path = Path(f)
        if not f_path.exists():
            continue
        f_resolved = f_path.resolve()
        for p, r in resolved_outs:
            if r == f_resolved:
                raise OutputCollisionError(
                    f"output path {p} collides with protected input file {f}（fail-closed で拒否）"
                )
            try:
                f_resolved.relative_to(r)
            except ValueError:
                continue
            raise OutputCollisionError(
                f"protected input file {f} is inside output path {p}"
                f"（fail-closed で拒否。出力側の削除/rename 処理が保護ファイルを"
                f"巻き込む）"
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
                f"（fail-closed で拒否。出力側の削除/rename 処理が保護 root を"
                f"巻き込む）"
            )


# ---------------------------------------------------------------------------
# 1. spec 変種生成（テキスト置換のみ・JSON 再シリアライズ禁止）
# ---------------------------------------------------------------------------

_SEED_FIELD_RE = re.compile(r'"seed"\s*:\s*(-?\d+)')


def find_seed_field(text: str) -> Tuple[int, int, int]:
    """`text` 中の唯一の `"seed"` フィールドの数値部分の span を返す。

    戻り値 = (現在値, 数値部分の開始オフセット, 終了オフセット)。
    フィールドが 0 件または複数件ヒットした場合は `SpecFieldError` を送出
    する（置換対象が一意に定まらない状態での書き込みを防ぐ）。
    """
    matches = list(_SEED_FIELD_RE.finditer(text))
    if len(matches) != 1:
        raise SpecFieldError(
            f'expected exactly one "seed" field in preset text, found {len(matches)} '
            "(ambiguous text-replacement target — fail-closed, nothing written)"
        )
    m = matches[0]
    start, end = m.span(1)
    return int(m.group(1)), start, end


def substitute_seed(text: str, new_seed: int) -> str:
    """`"seed"` フィールドの数値部分のみをテキスト置換する。

    `json.loads`/`json.dumps` による再シリアライズは行わない — インデント・
    キー順・末尾改行など、`"seed"` の数値以外のバイトは一切変更しない。
    """
    _original, start, end = find_seed_field(text)
    return text[:start] + str(new_seed) + text[end:]


@dataclass(frozen=True)
class SpecVariant:
    seed: int
    path: Path
    sha256: str


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of(path: Path) -> str:
    return sha256_of_bytes(path.read_bytes())


def build_spec_variants(
    base_preset_path: Path, seeds: Sequence[int], specs_dir: Path
) -> Dict[int, SpecVariant]:
    """base preset から seed ごとの spec 変種を生成し `specs_dir` へ書く。

    元の seed 値と一致するセル（実データでは seed=11）の変種は base preset
    とバイト同一であることを assert する（違えば `SpecIdentityError` を
    送出して停止する — レンダ開始前に検出する）。
    """
    base_bytes = base_preset_path.read_bytes()
    base_text = base_bytes.decode("utf-8")
    original_seed, _start, _end = find_seed_field(base_text)

    specs_dir.mkdir(parents=True, exist_ok=True)
    stem = base_preset_path.stem

    variants: Dict[int, SpecVariant] = {}
    for seed in seeds:
        text = substitute_seed(base_text, seed)
        variant_bytes = text.encode("utf-8")
        if seed == original_seed and variant_bytes != base_bytes:
            raise SpecIdentityError(
                f"seed={seed} spec variant is not byte-identical to base preset "
                f"{base_preset_path} (text-substitution invariant broken — halting, "
                "environment/tooling drift suspected)"
            )
        out_path = specs_dir / f"{stem}_seed{seed}.json"
        out_path.write_bytes(variant_bytes)
        variants[seed] = SpecVariant(seed=seed, path=out_path, sha256=sha256_of_bytes(variant_bytes))
    return variants


# ---------------------------------------------------------------------------
# 2. manifest / results 読み込み・整合性事前検査
# ---------------------------------------------------------------------------


def validate_schema_versions(manifest: dict, results: dict) -> None:
    """P2 修正 (review #265 R13): `manifest["schema"]`/`results["schema"]` が
    `MANIFEST_SCHEMA`/`RESULTS_SCHEMA` と完全一致することを検査する
    （`convert_user.load_ledger` の台帳 schema 検証と同型の fail-closed）。

    `main()` は json.loads 直後・他の一切の検査（`validate_manifest_
    consistency`/`index_results_cells`/`validate_results_cell_grid`）より
    前にこれを呼ぶ。欠落・誤記・未来版の schema を、キー名が表面上互換だと
    いう理由で無警告のまま消費しない。"""
    manifest_schema = manifest.get("schema")
    if manifest_schema != MANIFEST_SCHEMA:
        raise SchemaMismatchError(
            f"manifest schema {manifest_schema!r} does not match expected "
            f"{MANIFEST_SCHEMA!r} (fail-closed — refusing to read an unknown/legacy/"
            "future-version manifest)"
        )
    results_schema = results.get("schema")
    if results_schema != RESULTS_SCHEMA:
        raise SchemaMismatchError(
            f"results schema {results_schema!r} does not match expected "
            f"{RESULTS_SCHEMA!r} (fail-closed — refusing to read an unknown/legacy/"
            "future-version results file)"
        )


def validate_manifest_consistency(manifest: dict, results: dict) -> None:
    """`manifest`（殻）と `results`（実測正本）の scores/seeds/cells_total が
    一致することを検査する（render 前の fail-closed 事前検査）。"""
    scores = manifest.get("scores")
    seeds = manifest.get("seeds")
    if not isinstance(scores, list) or not isinstance(seeds, list):
        raise ManifestConsistencyError('manifest must have list fields "scores" and "seeds"')

    expected_total = len(scores) * len(seeds)
    if manifest.get("cells_total") != expected_total:
        raise ManifestConsistencyError(
            f'manifest cells_total={manifest.get("cells_total")!r} does not match '
            f"len(scores)*len(seeds)={expected_total}"
        )
    if results.get("registered_manifest_scores") != scores:
        raise ManifestConsistencyError(
            "results.registered_manifest_scores does not match manifest.scores "
            f"({results.get('registered_manifest_scores')!r} != {scores!r})"
        )
    if results.get("registered_manifest_seeds") != seeds:
        raise ManifestConsistencyError(
            "results.registered_manifest_seeds does not match manifest.seeds "
            f"({results.get('registered_manifest_seeds')!r} != {seeds!r})"
        )


def index_results_cells(results: dict) -> Dict[Tuple[str, int], dict]:
    """`results["cells"]` を `(score, seed)` キーの辞書へ索引化する。

    P2 修正 (review #265 R12): 同一 `(score, seed)` の重複エントリを検出
    したら、後勝ちで黙って畳む前に `DuplicateCellError` で fail-closed する
    （§ `DuplicateCellError` docstring 参照）。呼び出し元 `main()` はこの
    関数を render 開始（spec 変種生成・tripwire render）よりも前に呼ぶため、
    重複が検出されれば render・検証のどちらも一切開始されない。
    """
    idx: Dict[Tuple[str, int], dict] = {}
    duplicate_keys: List[Tuple[str, int]] = []
    for cell in results.get("cells", []):
        key = (cell["score"], cell["seed"])
        if key in idx:
            duplicate_keys.append(key)
            continue
        idx[key] = cell
    if duplicate_keys:
        raise DuplicateCellError(
            "d3_manifest_results.json contains duplicate (score, seed) cell "
            f"entries — refusing to silently fold them (fail-closed, nothing "
            f"rendered/verified): {sorted(set(duplicate_keys))}"
        )
    return idx


def validate_results_cell_grid(
    results_idx: Dict[Tuple[str, int], dict], scores: Sequence[str], seeds: Sequence[int]
) -> None:
    """P2 修正 (review #265 R13): `results_idx`（`index_results_cells` の
    出力）のキー集合が、`scores` × `seeds` の直積と完全一致することを検査
    する（余剰・欠落いずれも fail-closed。`CellGridMismatchError` docstring
    参照）。呼び出し元 `main()` はこの関数を index 構築の直後・render 開始
    （spec 変種生成・tripwire render）よりも前に呼ぶため、グリッド不一致が
    検出されれば render・検証のどちらも一切開始されない。
    """
    expected = {(score, seed) for score in scores for seed in seeds}
    actual = set(results_idx)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        raise CellGridMismatchError(
            "d3_manifest_results.json cells do not match the registered scores x seeds "
            f"grid exactly ({len(expected)} expected key(s)) — missing={missing} "
            f"extra={extra} (fail-closed, nothing rendered/verified)"
        )


# ---------------------------------------------------------------------------
# 3. render 呼び出し（`python -m adapter.render`・cwd 非依存の絶対パス解決）
# ---------------------------------------------------------------------------


@dataclass
class RenderOutcome:
    ok: bool
    returncode: Optional[int]
    stdout: str
    stderr: str
    wav_path: Path
    timing_path: Path


def render_cell(
    *,
    score: str,
    spec_path: Path,
    out_wav: Path,
    out_timing: Path,
    voicebank_root: Path,
    cache_dir: Path,
    foundry_dir: Path = FOUNDRY_DIR,
    donor: str = DONOR,
    python_exe: str = sys.executable,
) -> RenderOutcome:
    """`python -m adapter.render` を `cwd=foundry_dir` で呼び出す。

    渡す全パスは絶対パスへ解決済みであることを前提とする（呼び出し元の
    cwd に依存しない）。

    render 前に `out_wav`/`out_timing` の既存ファイルを削除する（PR #265
    R8 指摘 #21 由来。呼び出し元 `main()` は本レビュー指摘 P1 以降、
    `out_wav`/`out_timing` に常に使い捨ての `.staging_render/` 配下のパスを
    渡すため、通常は run 開始時点で空である想定だが、この事前削除は
    「今回の render がこのセルについて失敗すれば `out_wav`/`out_timing` は
    存在しない状態のまま残る」という不変条件を保つ最終防衛線として残す
    — render.py は成功時のみ atomic に書くため）。
    """
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    out_timing.parent.mkdir(parents=True, exist_ok=True)
    if out_wav.exists():
        out_wav.unlink()
    if out_timing.exists():
        out_timing.unlink()
    cmd = [
        python_exe,
        "-m",
        "adapter.render",
        "--score",
        score,
        "--voice",
        str(spec_path),
        "--out",
        str(out_wav),
        "--donor",
        donor,
        "--voicebank-root",
        str(voicebank_root),
        "--cache-dir",
        str(cache_dir),
        "--timing-out",
        str(out_timing),
    ]
    proc = subprocess.run(cmd, cwd=str(foundry_dir), capture_output=True, text=True)
    ok = proc.returncode == 0 and out_wav.exists() and out_timing.exists()
    return RenderOutcome(
        ok=ok,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        wav_path=out_wav,
        timing_path=out_timing,
    )


# ---------------------------------------------------------------------------
# 4. 全数照合（合成フィクスチャでも単体テスト可能な純粋関数）
# ---------------------------------------------------------------------------


@dataclass
class CellVerification:
    score: str
    seed: int
    status: str  # "PASS" | "FAIL"
    reasons: List[str] = field(default_factory=list)
    wav_sha256: Optional[str] = None
    timing_sha256: Optional[str] = None


def verify_cell(
    score: str,
    seed: int,
    wav_path: Path,
    timing_path: Path,
    expected: Optional[dict],
    *,
    spec_sha256: Optional[str] = None,
) -> CellVerification:
    """`wav_path`/`timing_path` の実測 sha256 を `expected`（
    `d3_manifest_results.json` の対応セル辞書）と照合する。

    `spec_sha256` が渡された場合（`main()` の全数照合は常に渡す — build
    済みの spec 変種の実測 sha256）、`expected["spec_sha256"]` とも照合する
    （本レビュー指摘 P2: これを省くと preset バイトが空白のみ等で漂移して
    も wav/timing バイトが同一なら PASS と誤って報告できてしまい、pin
    されていない入力に対する虚偽の attestation になる。既存の
    `build_spec_variants` 内 seed=11 バイト同一性 assert とは別目的
    — あちらは「テキスト置換の実装自体が正しいか」の自己診断、こちらは
    「今回生成した spec の実バイトが manifest 正本の想定と一致するか」を
    全 40 セルへ横展開する検証）。`spec_sha256=None`（省略）の場合はこの
    照合をスキップする（`main()` 以外からの直接呼び出し・単体テストの
    後方互換のため）。
    """
    reasons: List[str] = []
    wav_sha: Optional[str] = None
    timing_sha: Optional[str] = None

    if expected is None:
        return CellVerification(
            score, seed, "FAIL", ["no expected cell in results manifest (unknown score/seed)"]
        )

    if not wav_path.exists():
        reasons.append("wav missing (render did not produce output)")
    else:
        wav_sha = sha256_of(wav_path)
        expected_wav = expected.get("wav_sha256")
        if wav_sha != expected_wav:
            reasons.append(f"wav_sha256 mismatch (got {wav_sha}, expected {expected_wav})")

    if not timing_path.exists():
        reasons.append("timing csv missing (render did not produce output)")
    else:
        timing_sha = sha256_of(timing_path)
        expected_timing = expected.get("timing_csv_sha256")
        if timing_sha != expected_timing:
            reasons.append(
                f"timing_csv_sha256 mismatch (got {timing_sha}, expected {expected_timing})"
            )

    if spec_sha256 is not None:
        expected_spec = expected.get("spec_sha256")
        if spec_sha256 != expected_spec:
            reasons.append(
                f"spec_sha256 mismatch (got {spec_sha256}, expected {expected_spec})"
            )

    status = "PASS" if not reasons else "FAIL"
    return CellVerification(score, seed, status, reasons, wav_sha, timing_sha)


def format_report(
    verifications: Sequence[CellVerification], tripwire_lines: Sequence[str]
) -> str:
    """stdout / `verify_report.txt` 共通のレポート整形（決定論 — 現在時刻等の
    非決定的な値は含めない）。"""
    lines: List[str] = ["D3 corpus verification report", ""]
    lines.extend(tripwire_lines)
    lines.append("")
    header = f'{"score":<12}{"seed":>6}  {"status":<6}  reasons'
    lines.append(header)
    lines.append("-" * max(len(header), 40))
    n_pass = 0
    for v in verifications:
        if v.status == "PASS":
            n_pass += 1
        reason_text = "; ".join(v.reasons)
        lines.append(f"{v.score:<12}{v.seed:>6}  {v.status:<6}  {reason_text}")
    lines.append("")
    lines.append(f"TOTAL: {n_pass}/{len(verifications)} PASS")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 4b. staging → render atomic swap（本レビュー指摘 P1）
# ---------------------------------------------------------------------------


def _swap_staging_render_into_place(staging_dir: Path, render_dir: Path) -> None:
    """`staging_dir`（全 40 セルの render 出力を書き込んだ使い捨てディレクトリ）
    を `render_dir`（本置き場）へ原子的に差し替える。

    呼び出し元 `main()` は §4 の全数照合が全数 PASS した場合にのみこの関数を
    呼ぶ契約（1 件でも FAIL があれば呼ばない — `render_dir` は無変更のまま
    残す）。`render_dir` の既存内容（前回実行までの有効なコーパス）を
    `<render_dir>.bak_pre_swap` へ退避してから `staging_dir` を `render_dir`
    へ rename する 2 段 rename パターン
    （`s1_gate/gate_synth.py` `_swap_step_dir_into_place` /
    `s1_dataprep/convert_pjs.py` `_swap_into_place` と同型 — POSIX
    `rename(2)` はディレクトリの置換を atomic に行う）。新世代 rename が
    失敗（`KeyboardInterrupt` 含む）すれば、退避済みの旧世代を `render_dir`
    へ復元してから再送出する（旧世代の退避が完了していない場合は復元対象が
    無いためそのまま再送出する）。成功時は退避先を削除する。
    """
    backup_dir = render_dir.parent / f"{render_dir.name}.bak_pre_swap"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    try:
        if render_dir.exists():
            render_dir.rename(backup_dir)
        staging_dir.rename(render_dir)
    except BaseException:
        if backup_dir.exists() and not render_dir.exists():
            backup_dir.rename(render_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--voicebank-root", required=True, type=Path,
        help="波音リツ強連続音Ver1.5.1 の展開先ディレクトリ",
    )
    parser.add_argument("--out-dir", required=True, type=Path, help="出力先ディレクトリ")
    parser.add_argument(
        "--preset", type=Path, default=DEFAULT_PRESET,
        help=f"base preset JSON（既定: {DEFAULT_PRESET}）",
    )
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST,
        help=f"D3 manifest 殻（既定: {DEFAULT_MANIFEST}）",
    )
    parser.add_argument(
        "--results", type=Path, default=DEFAULT_RESULTS,
        help=f"D3 manifest 実測正本（既定: {DEFAULT_RESULTS}）",
    )
    args = parser.parse_args(argv)

    voicebank_root = args.voicebank_root.resolve()
    out_dir = args.out_dir.resolve()
    preset_path = args.preset.resolve()
    manifest_path = args.manifest.resolve()
    results_path = args.results.resolve()
    cache_dir = out_dir / "cache"

    if not voicebank_root.exists():
        print(f"ERROR: --voicebank-root does not exist: {voicebank_root}", file=sys.stderr)
        return 1
    if not preset_path.exists():
        print(f"ERROR: --preset does not exist: {preset_path}", file=sys.stderr)
        return 1
    if not manifest_path.exists():
        print(f"ERROR: --manifest does not exist: {manifest_path}", file=sys.stderr)
        return 1
    if not results_path.exists():
        print(f"ERROR: --results does not exist: {results_path}", file=sys.stderr)
        return 1

    specs_dir = out_dir / "specs"
    # wav + timing csv は同一ディレクトリへ同居させる（PR #265 R8 指摘 #20:
    # `s1_dataprep/convert_d3.py` の `discover_pairs()` が単一ディレクトリ
    # 非再帰・stem 突き合わせで pair を発見する契約のため。runbook 手順 4 は
    # `--render-dir <out-dir>/render` をそのまま渡せる）。
    render_dir = out_dir / "render"
    staging_dir = out_dir / ".staging_render"
    # `_swap_staging_render_into_place` が旧世代 `render_dir` を退避する先
    # （P1 修正 (review #265 R11) の preflight 対象にも含める — この派生パス
    # も rename/rmtree の対象になり得るため）。
    backup_dir = render_dir.parent / f"{render_dir.name}.bak_pre_swap"

    # P1 修正 (review #265 R11): 削除・書き込みを一切開始する前に、管理下の
    # 出力パス（`--out-dir`・`.staging_render/`・`render/`・swap 退避先）が
    # 入力（`--voicebank-root`・`--preset`・`--manifest`・`--results`）と
    # 衝突していないかを preflight で検査する（§ `OutputCollisionError`
    # docstring 参照。既存の `if staging_dir.exists(): shutil.rmtree(...)`
    # 無条件クリーンアップ、および全数 PASS 後の atomic swap の双方が入力を
    # 巻き込み得るため、その手前で拒否する）。
    try:
        _reject_output_collision(
            [out_dir, staging_dir, render_dir, backup_dir],
            protected_roots=[voicebank_root],
            protected_files=[preset_path, manifest_path, results_path],
        )
    except OutputCollisionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = json.loads(results_path.read_text(encoding="utf-8"))

    # P2 修正 (review #265 R13): schema 完全一致は読み込み直後・他の一切の
    # 検査より前に検査する（`SchemaMismatchError` docstring 参照）。
    try:
        validate_schema_versions(manifest, results)
    except SchemaMismatchError as exc:
        print(f"ERROR: schema check failed: {exc}", file=sys.stderr)
        return 1

    try:
        validate_manifest_consistency(manifest, results)
    except ManifestConsistencyError as exc:
        print(f"ERROR: manifest/results consistency check failed: {exc}", file=sys.stderr)
        return 1

    scores: List[str] = manifest["scores"]
    seeds: List[int] = manifest["seeds"]
    tripwires: dict = manifest["tripwires"]

    try:
        results_idx = index_results_cells(results)
    except DuplicateCellError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # P2 修正 (review #265 R13): 登録グリッド外の余剰セル・欠落セルを
    # render 開始前に fail-closed で検出する（`CellGridMismatchError`
    # docstring 参照）。
    try:
        validate_results_cell_grid(results_idx, scores, seeds)
    except CellGridMismatchError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # `specs_dir`/`render_dir`/`staging_dir`/`backup_dir` は preflight 検査
    # のため上で既に確定済み（P1 修正 (review #265 R11)）。
    #
    # 本レビュー指摘 P1（R8）: 全セルの render 出力はまずこの使い捨て
    # staging へ書き、§4 の全数照合が全数 PASS した場合にのみ `render_dir`
    # へ atomic swap する（`_swap_staging_render_into_place`）。既に有効な
    # `render_dir` を持つ `--out-dir` へ再実行して途中セル（tripwire 含む）
    # が失敗しても、render 呼び出しが `render_dir` に触れることは一切ないため
    # 旧世代の有効なコーパスを喪失しない。
    #
    # 前回の中断・失敗 run が残した staging 残骸を掃除してから始める（新旧
    # 混在した staging を「今回 render しなかったセルも PASS 扱い」で
    # スワップしてしまう事故を防ぐ）。
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    # --- 1. spec 変種生成 ---------------------------------------------------
    try:
        variants = build_spec_variants(preset_path, seeds, specs_dir)
    except (SpecFieldError, SpecIdentityError) as exc:
        print(f"ERROR: spec variant generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"generated {len(variants)} spec variant(s) in {specs_dir}")

    if TRIPWIRE_SEED not in variants:
        print(
            f"ERROR: manifest seeds {seeds!r} do not include tripwire seed={TRIPWIRE_SEED}",
            file=sys.stderr,
        )
        return 1

    # --- 2. tripwire 先行 -----------------------------------------------
    tripwire_lines: List[str] = []
    for score in TRIPWIRE_SCORES:
        spec = variants[TRIPWIRE_SEED]
        out_wav = staging_dir / f"{score}_seed{TRIPWIRE_SEED}.wav"
        out_timing = staging_dir / f"{score}_seed{TRIPWIRE_SEED}.csv"
        print(f"--- tripwire render: {score} seed={TRIPWIRE_SEED} ---")
        outcome = render_cell(
            score=score,
            spec_path=spec.path,
            out_wav=out_wav,
            out_timing=out_timing,
            voicebank_root=voicebank_root,
            cache_dir=cache_dir,
        )
        if not outcome.ok:
            print(
                f"ERROR: tripwire render failed for {score} seed={TRIPWIRE_SEED} "
                f"(returncode={outcome.returncode})",
                file=sys.stderr,
            )
            print(outcome.stdout)
            print(outcome.stderr, file=sys.stderr)
            print(
                "環境ドリフト・全セル無効: tripwire render failed — halting before "
                "remaining cells",
                file=sys.stderr,
            )
            return 1

        actual_sha = sha256_of(out_wav)
        expected_key = f"{score}_seed{TRIPWIRE_SEED}_wav_sha256"
        expected_sha = tripwires.get(expected_key)
        match = actual_sha == expected_sha
        line = f"tripwire {score} seed={TRIPWIRE_SEED}: expected={expected_sha} actual={actual_sha} match={match}"
        tripwire_lines.append(line)
        print(line)
        if not match:
            print(
                f"ERROR: 環境ドリフト・全セル無効 — tripwire wav sha256 mismatch for "
                f"{score} seed={TRIPWIRE_SEED} — halting before remaining cells",
                file=sys.stderr,
            )
            return 1

    print("tripwire check PASSED — proceeding to remaining cells")

    # --- 3. 残り 38 セル ------------------------------------------------
    # このセッションで render に失敗したセルを記録する（PR #265 R8 指摘
    # #21）。§4 でこの集合に載っているセルは、ディスク上の状態に関わらず
    # 無条件 FAIL にする（`render_cell` の事前削除と合わせた二重の防御）。
    render_failures: Dict[Tuple[str, int], str] = {}
    already_rendered = {(score, TRIPWIRE_SEED) for score in TRIPWIRE_SCORES}
    for score in scores:
        for seed in seeds:
            if (score, seed) in already_rendered:
                continue
            spec = variants[seed]
            out_wav = staging_dir / f"{score}_seed{seed}.wav"
            out_timing = staging_dir / f"{score}_seed{seed}.csv"
            print(f"--- rendering {score} seed={seed} ---")
            outcome = render_cell(
                score=score,
                spec_path=spec.path,
                out_wav=out_wav,
                out_timing=out_timing,
                voicebank_root=voicebank_root,
                cache_dir=cache_dir,
            )
            if not outcome.ok:
                reason = f"render subprocess failed this run (returncode={outcome.returncode})"
                print(f"FAIL: {score} seed={seed}: {reason}")
                print(outcome.stderr, file=sys.stderr)
                render_failures[(score, seed)] = reason
            else:
                print(f"OK: {score} seed={seed}")

    # --- 4. 全数照合 ------------------------------------------------------
    verifications: List[CellVerification] = []
    for score in scores:
        for seed in seeds:
            failure_reason = render_failures.get((score, seed))
            if failure_reason is not None:
                # このセッションで render が失敗した事実を最優先する — たとえ
                # ディスク上に（前回実行由来の）sha256 一致ファイルが残って
                # いても、無条件 FAIL にする（stale artifact による false
                # PASS を許さない）。
                verifications.append(
                    CellVerification(score, seed, "FAIL", [failure_reason])
                )
                continue
            out_wav = staging_dir / f"{score}_seed{seed}.wav"
            out_timing = staging_dir / f"{score}_seed{seed}.csv"
            expected = results_idx.get((score, seed))
            # 本レビュー指摘 P2: wav/timing csv だけでなく、今回生成した spec
            # 変種の実測 sha256 も `expected["spec_sha256"]` と照合する
            # （`variants` は §1 で全 seed 分生成済み — この score/seed の
            # seed に対応する変種の sha256 を渡す）。
            verifications.append(
                verify_cell(
                    score, seed, out_wav, out_timing, expected,
                    spec_sha256=variants[seed].sha256,
                )
            )

    report_text = format_report(verifications, tripwire_lines)
    print(report_text)

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "verify_report.txt"
    report_path.write_text(report_text, encoding="utf-8")
    print(f"wrote {report_path}")

    n_fail = sum(1 for v in verifications if v.status == "FAIL")
    if n_fail:
        print(f"RESULT: {n_fail} cell(s) FAILED verification", file=sys.stderr)
        print(
            f"RESULT: {render_dir} left unchanged — staging {staging_dir} was NOT "
            "swapped into place (本レビュー指摘 P1: 1 件でも FAIL があれば本置き場は"
            "無変更)",
            file=sys.stderr,
        )
        return 1

    # --- 5. atomic swap（全数 PASS 時のみ） -------------------------------
    _swap_staging_render_into_place(staging_dir, render_dir)
    print(f"swapped {staging_dir} -> {render_dir}")
    print(f"RESULT: all {len(verifications)} cells PASSED verification")
    return 0


if __name__ == "__main__":
    sys.exit(main())
