# Genome Architecture S3 — 実装ノート

`VoiceGenesis_Genome_Architecture_S3_Design_v1_1.md`（User 起草・凍結）の実装対応表。

**この文書は設計をしない。** 設計書の各節がどのコードに落ちたかを示すだけ。
契約から逸脱する判断が必要になった場合は実装せず `status = BLOCKED` で停止する（§20）。

---

## 1. S3 が答える問い（§1）

> F0 / Duration / Energy / Release のうち、**何個が独立 gene として
> 操作・実現・再現できたか**。

これ以外は測らない。品質問題・語尾破綻・WORLD の改良は範囲外（§2, §19, §24）。

## 2. 入力 — S2 の凍結事実（§0, §5）

正本は `planb_real/results/ladder_manifest.json`。S3 はこれを **read-only** で読む。

| 項目 | 値の出所 |
|---|---|
| pair set | `pairs[]`（全 pair を使う。耳での除外・選別はしない） |
| `context_id` | `pair["probe_kind"]` を **exact string** で使用。再分類禁止 |
| `context_phones` | manifest の値 |
| `identity_ap_scale` | manifest の値 |
| pin | `input_manifest_sha256` = manifest ファイルの sha256 |

`probe_kind` が欠落した pair は `s3_spec.context_id()` が `KeyError` を上げ、
`s3_runner.load_frozen()` 経由で停止する。

## 3. 条件 — B0/F/D/E/R の 5 つだけ（§6）

`s3_spec.CONDITIONS`:

| 条件 | toggles | 意味 |
|---|---|---|
| `B0` | 全 OFF | baseline |
| `F` | `f0=True` | GENE-F0 |
| `D` | `duration=True` | GENE-DURATION |
| `E` | `energy=True` | GENE-ENERGY |
| `R` | `release=True` | GENE-RELEASE |

組合せ条件（F+D 等）は作らない。gene も増やさない（§19）。

## 4. Pair-Level Gate（§8）→ `s3_gates.py`

| Gate | 実装 | 証拠 |
|---|---|---|
| P1 STRUCTURAL_ISOLATION | `p1_structural_isolation()` | 対象トグルのみ ON + `pb_gates.gate_tripwire` の proxy 記録（条件ごとに `s3_runner.run_pair` で採取）+ `pr_performance.assert_no_spectral_payload` |
| P2 INTERVENTION_NONZERO | `p2_intervention_nonzero()` | `s3_runner.intervention_amounts()` が測る **control 側**の移植量 |
| P3 REALIZED_IN_OUTPUT | `p3_realized_in_output()` | B0 と gene 条件の **既存 4 metric** の比較のみ |
| P4 DETERMINISM | `p4_determinism()` | same-process 反復 + 別プロセス（`s3_runner.py --recompute`）の sample sha256 |

P3 で使う metric は `s3_spec.GENE_METRIC` に凍結。新しい計器は足さない（§8, §19）。

| gene | metric | 良い向き |
|---|---|---|
| f0 | `f0_dev_rmse_cents` | lower |
| duration | `note_split_mae_ms` | lower |
| energy | `energy_corr` | higher |
| release | `taper_rmse_db` | lower |

## 5. Verdict（§9–§11）

pair verdict は 4 状態のみ、この順で決まる:

```text
P1 fail                     -> FAILED
P2 fail                     -> NOT_EVALUABLE
P3 pass and P4 pass         -> SUPPORTED
それ以外                      -> UNSUPPORTED
```

gene verdict も 4 状態のみ、優先順位は `FAILED → NOT_EVALUABLE → SUPPORTED → UNSUPPORTED`。
閾値は `s3_spec.Criteria`（変更禁止）:

```text
min_evaluable_pairs    = 3
min_evaluable_contexts = 2
min_support_ratio      = 0.75
min_supported_contexts = 2
min_supported_genes    = 2
```

S3 overall は `supported_gene_count >= 2` で PASS（§11, §23）。
どちらの結果でも **S2 の成功判定は変更しない**。

## 6. モジュール（§15）

```text
genome_s3/
├── DESIGN_GENOME_S3.md   この文書
├── s3_spec.py            凍結定義のみ（音響処理・I/O・動的閾値を置かない）
├── s3_runner.py          frozen 読み込み → 5 条件生成 → SHA → realization metrics
├── s3_gates.py           §8–§11 をそのまま実装（判定を増やさない）
├── s3_report.py          results/s3_results.json + results/S3_RECORD.md
├── tests/test_genome_s3.py
└── results/.gitignore    WAV を commit しない
```

`planb/` と `planb_real/` は **import するだけ**で変更しない。

## 7. 再現性記録（§17）

`results/s3_results.json` の `reproducibility[]` に 1 出力 = 1 行で保存する:
`pair_key` / `context_id` / `gene` / `condition` / `toggle_state` /
`identity_sha256` / `performance_sha256` / `sample_sha256` / `wav_sha256` / `code_commit`。

WAV 本体は commit しない（`results/.gitignore`）。
波音リツ 歌声データベース利用規約 第3条1（転載禁止）にも該当するため恒久除外。

## 8. 実行

```bash
# 全 pair × 5 条件を生成して判定・記録まで
python voice_genesis/foundry/genome_s3/s3_report.py

# 別プロセス決定論の再計算だけ（P4 が内部で呼ぶ）
python voice_genesis/foundry/genome_s3/s3_runner.py --recompute

# テスト（実素材が無い環境では integration は skip）
python -m pytest voice_genesis/foundry/genome_s3/tests -q
```

終了コード: `0` = S3 PASS / `1` = S3 FAIL(NOT ESTABLISHED) / `3` = BLOCKED。

## 9. 停止規則（§20）

以下では実装を進めず `S3Stop` で停止し、**原因・影響・最小修正案だけ**を記録する。

1. S2 frozen inputs が見つからない
2. source SHA 不一致
3. pair manifest が変化
4. 既存 `PerformanceToggles` と 4 gene 定義が合わない
5. Performance path へ spectral payload が入る
6. deterministic repeat が不一致
7. 判定に新 metric が必要
8. 既存コード修正が必要だが scope 外

## 10. 範囲外（§2, §19, §24）

S3 では以下を **やらない**: gene 追加 / 閾値変更 / metric 追加 / 音質補正 /
WORLD 改良 / `AP_SCALE` 調整 / 耳による pair 除外 / 効く pair だけの採用 /
語尾破綻の是正 / S2 の再裁定 / S4 への自動進行。
