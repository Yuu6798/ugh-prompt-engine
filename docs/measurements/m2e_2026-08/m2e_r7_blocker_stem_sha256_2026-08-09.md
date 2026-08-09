# r7 step0 fail-closed 停止報告 — カテゴリ集約の stem_sha256 同一性検査と M2e stem アームの構造的不整合

- **発生日時**: 2026-08-09T00:25:52Z（JST 09:25）
- **状態**: **r7 停止中**（store_A 無傷・store_B 未作成・セルの再測定は一切発生していない）
- **区分**: 設計と実装の照合事項（実行側でのコード変更は行わず設計側判読を待つ）

## 何が起きたか

r7 step0（HANDOFF「1 水準まるごと」run による run report 生成。store_A から 100% resume する工程）
の最初の実行（+12dB・repeat 0）が非ゼロ exit (2) で fail-closed 停止した。

```
RuntimeError: run_accuracy: category 'V_remix_real_stem' の clips が
provenance_preprocessing で 不一致 (fail-closed)
```

- 実行コマンド: `build/m2e/run_r7_step0_reports.sh`（HANDOFF §5 の逐語形。
  `--categories V_remix_real_direct V_remix_real_stem --level +12dB
  --cell-store build/m2e/store_A --repeat-index 0 --pin-threads`）
- console: `build/m2e/r7_step0_console.log`
- 例外送出点: `scripts/run_melody_accuracy.py` L5181–5186（`_run_external_category` の
  カテゴリ集約時検査）

## 原因（機械集計済み）

L5181 の検査は「カテゴリ内の全 clip_row の `provenance_preprocessing` が
JSON 単位で同一」を要求する。しかし M2e の stem アームでは
**`stem_sha256` が per-mix（clip×bed ごと）に固有** —— demucs の分離出力は
ミックスごとに異なるため、これは測定の正しい姿である。

機械集計（V_remix_real_stem / +12dB / repeat 0 の 80 セル）:
- `provenance_preprocessing` の distinct 数: **80 / 80**（全セル相異）
- フィールド別差分: `preprocessing` / `separation_model` / `separation_version` /
  `separation_weights_sha256`（=bf1218da…・凍結値と一致）/ `separation_code_sha256` /
  `separation_code_packages` は**全セル同一**。相異は **`stem_sha256` のみ**。

つまり分離器スタックの pin は完全に均質で、différence は「分離出力そのもの」だけ。
検査が per-clip 固有量（stem_sha256）をカテゴリ不変量として扱っている。

## なぜ r6 では発火しなかったか

- r6 の shard 実行機は run report を出さない（HANDOFF: 「shard モードは run report /
  verdict / census のいずれも出さない」）ため、このカテゴリ集約検査 (L5181) を通らない。
- 本検査の導入は M2c 期（commit 61876c85・V_direct 経路）。V_direct には
  preprocessing が無く（None）、全 clip で自明に同一 → 素通りしていた。
- **複数 clip × per-clip stem のカテゴリがこの集約を通るのは M2e が初**であり、
  ここで初めて顕在化した（凍結定義と実装の読み合わせでしか捕まらない型・HANDOFF §3.4'）。

## 影響範囲（読み合わせ結果）

1. `_run_external_category` L5181（今回の停止点）: category row の
   `provenance_preprocessing` を clip_rows[0] から採る前提の同一性検査。
2. `_row_model_stack_signature` L6461–: category row の署名に
   `preprocessing.get("stem_sha256")` を含める。per-clip 固有量なので、
   仮に 1 を通しても repeats 間比較（run r0 vs r1 は同一 stem のはず）は
   成立し得るが、category row にどの clip の stem を載せるかが未定義になる。
3. `_require_homogeneous_model_stack` L6515–: fullstack カテゴリの row に
   `stem_sha256` の存在（sha256 形式）を要求。category row 単位。

## 実行側の見立て（判断は設計側）

検査の意図（「分離器スタックが run 間で同一」）は per-clip の
`stem_sha256` **一致検証**（resume 時の §8.7 digest 照合・これは既に通っている）と、
カテゴリ集約時の**分離器 pin（weights/code/version）同一性**で担保でき、
`stem_sha256` はカテゴリ不変量から外して per-clip 束（例: 全 clip の stem_sha256 の
sorted list への digest）として category row に載せる形が考えられる——が、
これは検証レイヤの変更なので実行側では触らない。

## 現況

- r7 は step0 の入口で停止したまま。**store_A / 台帳 / fixtures に変更なし**。
- `build/m2e/run_r7_step0_reports.sh` / `build/m2e/run_r7_evaluate.sh`（チャンク実行機）は
  準備済み・未走行（evaluate は 1 セルも測っていない）。
- 設計側の裁定（検査の改修方針 or 代替経路）を受けてから再開する。
