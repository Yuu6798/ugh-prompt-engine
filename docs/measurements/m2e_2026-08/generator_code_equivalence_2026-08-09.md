# Generator Code Hash 等価表 Attestation — 前任 hash `5cc0d5f9…`

**裁定日:** 2026-08-09
**裁定者:** Fable 5 設計セッション（`session_01XBrHyRfRBAGgS9gGHtiKdg`）
**参照:** PR #254 Codex P1 指摘（`scripts/run_melody_accuracy.py` の
`_generator_code_sha256()` がファイル bytes 全体を hash するため、本 PR の変更で
store_A（M2e r6 実測・1280 セル・84.5h）の recorded generator hash と現行 checkout の
loaded hash が食い違い、`_cell_record_mismatches()` / `_require_matching_generator_code()`
がいずれも resume・照合を拒否して全セル再測定を強制する問題）

---

## 1. 等価表エントリ

```python
GENERATOR_CODE_EQUIVALENT_SHA256S: Dict[str, str] = {
    "5cc0d5f9bba92ce8aa679eeebc32845e7702b6ac8e2bb1f561ba37c37ab965a4": (
        "docs/measurements/m2e_2026-08/generator_code_equivalence_2026-08-09.md"
    ),
}
```

前任 hash `5cc0d5f9bba92ce8aa679eeebc32845e7702b6ac8e2bb1f561ba37c37ab965a4` は、
**commit `32288aa8`**（"M2e r4/r5: P=2 校正確定・R_max rev.8（18→21・条件付き User
決裁）・S 測定方法明確化・shard map commit（N_shards=19）"）時点の
`_generator_code_sha256()` 閉包 hash である。M2e r6 帯（store_A・1280 セル・84.5h）は
この checkout で凍結して測定された。

## 2. 裁定の適用範囲

等価受理を適用するのは **永続化成果物の照合 3 箇所**のみ（`scripts/run_melody_accuracy.py`）:

| 箇所 | 役割 | 受理時の痕跡 |
|---|---|---|
| `_cell_record_mismatches` | セルレコード（`store_A`）の resume 可否判定 | 呼び出し元 `run_accuracy` の report に `generator_code_predecessors` |
| `_require_matching_generator_code` | `evaluate` に渡す report 群の 3 段照合 | `verdict["generator_code_predecessors"]` |
| `aggregate_m2e_census`（`_require_homogeneous_census_inputs`） | 複数 verdict の集計可否判定 | `census["generator_code_predecessors"]` |

**適用しないもの**: `_require_fresh_process_report_provenance`（測り直し子プロセスの
report 照合）。この照合は「同一 checkout・同一瞬間」に `sys.executable` で本ファイル
自身を子プロセス起動して得た report と、親プロセスが読み込んだ `_LOADED_GENERATOR_
CODE_SHA256` の比較であり、過去の永続化レコードの resume ではない。ここに等価表を
適用すると、測定経路が実行中に本当に差し替わった（=別ファイルが実行された）ケースの
検知そのものを握り潰すため、厳格一致のまま維持する。

## 3. 裁定範囲を全区間（`32288aa8` → 本ブランチ HEAD）へ拡大した理由

当初想定は「本 PR の diff（`1dbf966` + `42378bb` + 本コミット）だけが per-cell 測定
経路に触れていないことの attestation」だったが、前任 hash の出所確認（下記 §5）の
過程で、`32288aa8`（r6 実測時点の checkout）から本 PR のベースコミット `ffc9220`
までの間に、**閉包（69 ファイル）に触れた中間コミットが 1 件存在する**ことが判明した:

- `8b3f737` — `fix(m3d): Codexレビュー第9R対応 — sidecar同一バイトhash・rhythm
  negative spec是正・起動数のpair基準会計`
  （`scripts/build_melody_bench.py` へ任意フィールド `note_durs_sec` を新設。
  M3d 用ベンチ生成スクリプトへの完全後方互換な追加で、未指定時は従来どおりスカラー
  一様。M2e の per-cell 測定経路（音声読み込み→抽出→metrics 計算→セルレコード
  書き込み）には非該当）

したがって、attestation の裁定範囲は「本 PR の diff のみ」では不十分であり、
**`32288aa8` から本ブランチ HEAD までの全区間**を対象に、閉包へ触れた全コミットの
非該当理由を個別に記録する必要がある。以下がその全数掃討である。

## 4. 閉包（69 ファイル）へ触れたコミットの全数掃討

`32288aa8..HEAD`（本ブランチの現行 HEAD。本 attestation 作成時点で
`42378bb`＋本コミット）の区間で、`_generator_code_paths()` が hash する 69 ファイル
（`scripts/run_melody_accuracy.py` / `scripts/build_melody_bench.py` /
`src/svp_rpe/**` の first-party 閉包）を touch したコミットは以下の 3 件のみ
（`git log --oneline 32288aa8..HEAD -- <69 ファイル>` で列挙。列挙方法は §5 参照）。

| コミット | 変更内容 | per-cell 測定経路への該当性 |
|---|---|---|
| `8b3f737` | `scripts/build_melody_bench.py` に任意フィールド `note_durs_sec` 追加（M3d ベンチ生成用・後方互換） | **非該当**。M2e の `run_accuracy` / `evaluate_m2_bars` / セル resume 経路は `build_melody_bench.py` を実行時に呼ばない（M3d 専用のオフライン fixture 生成ツール）。§1 に記載の理由 |
| `1dbf966` | `fix(m2e): preprocessing カテゴリ不変量から per-clip stem_sha256 を分離` | **非該当**。`_run_external_category` のカテゴリ不変量集約（`split_preprocessing_invariants`）のみ変更。音声読み込み・抽出・metrics 計算・セルレコード書き込みの各ステップは無変更 |
| `42378bb` | `fix(m2e): run 間決定論証拠として stem/bundle を model stack 署名へ復帰` | **非該当**。`_row_model_stack_signature` / `_require_homogeneous_model_stack` / `_require_execution_evidence` の検証レイヤのみ変更。per-cell 測定経路は無変更 |

本コミット（等価表新設・比較箇所の等価受理判定・attestation 文書追加）自体も、
新規ヘルパー/定数の追加と `_cell_record_mismatches` /
`_require_matching_generator_code` / `_require_homogeneous_census_inputs` の
検証レイヤのみの変更であり、per-cell 測定経路（音声読み込み→抽出→metrics 計算→
セルレコード書き込み）には触れていない。

## 5. 検証方法

### 5.1 前任 hash の再現確認

`git worktree add --detach <tmp> 32288aa8` で該当コミットをチェックアウトし、
その版の `scripts/run_melody_accuracy.py` を実際に `importlib.util.
spec_from_file_location` でロードして `_generator_code_sha256()` を実行した
（手計算・簡易 sha256sum ではなく実装をそのまま実行）。`sys.path` は worktree 自身の
`src/` を優先させ、editable install（`pip install -e .` によるメイン checkout への
`.pth` 解決）へ迂回しないよう明示的に固定した。

検証方法の正当性は、同じ手法を現行 HEAD 側で実行し、結果が `_LOADED_GENERATOR_CODE_
SHA256`（モジュールロード時に自動計算される値）と一致することで確認済み。

結果:

```
32288aa8 での _generator_code_sha256() = 5cc0d5f9bba92ce8aa679eeebc32845e7702b6ac8e2bb1f561ba37c37ab965a4
```

前任 hash と完全一致。閉包の構成ファイル集合（69 本）も HEAD と完全に同一
（`_generator_code_paths()` の相対パス一覧に差分なし）——差分は
`run_melody_accuracy.py` 自身と `build_melody_bench.py` の bytes のみ。

### 5.2 到達可能性

`git branch -a --contains 32288aa8` は `main` / `origin/main` /
`claude/m2e-r4-calibration-p-value-rtj3nh` / `origin/claude/m2e-r4-calibration-
p-value-rtj3nh` を返す。`git merge-base --is-ancestor 32288aa8 origin/main` および
`... HEAD` はいずれも成功——`32288aa8` は mainline 上の到達可能なコミットであり、
孤立した側ブランチではない。

### 5.3 閉包 touch コミットの列挙方法

```bash
git log --oneline 32288aa8..HEAD -- <69 ファイルの相対パス一覧>
```

（69 ファイルのパス一覧は `_generator_code_paths()` を worktree 越しに実行して得た
ものと同一。パス一覧自体を pathspec として `git log --` に渡すことで、69 ファイルの
うち **いずれか 1 本でも** touch したコミットのみを漏れなく抽出する。）

## 6. 運用規則

**測定経路に触れる変更を入れるコミットでは、必ず `GENERATOR_CODE_EQUIVALENT_
SHA256S` の全エントリを削除して再裁定すること。** 等価表は「コード変更の免罪符」
ではなく、diff スコープを本 attestation で個別に裁定した例外である。新しい前任
hash を追加する場合も、本文書と同型の attestation（対象コミット範囲・非該当理由・
検証方法・裁定者・参照 Issue/PR）を新規作成し、`GENERATOR_CODE_EQUIVALENT_SHA256S`
のコメントから参照すること。
