# S4 RECORD — Multi-Gene Co-expression & Retention PoC

- schema: `voicegenesis-genome-s4/1.0`
- s3_results_sha256: `65b91402f2b6ead2b8d3269455413e6bd0ae575d66a33a52f0a2367c91e55cd0`
- s35_results_sha256: `5dcbc4329d9e7343e0f48d96665be5faa0f9406425a6552249eb9b5fcebb95ba`
- input_manifest_sha256: `78fcd8cc6e3f2b7c9edae72e0894f552b9cdfb0087d44b1df0d45402063fec4f`
- commit: `b6596db68c88422f776c6de061471d86754196f9` (clean worktree: True)
- closure digest: `9e00aa6412f6d4ee5a7ab3bd5e7cceb7f0fc1dba39eb861b3a9964b76620907c` (21 files)
- 素材: **relocatable rematerialization**（凍結 manifest の絶対パスは変更せず、走行時メモリ上でのみ新 root へ写像）
  - `pjs_corpus`: archive `683c00253ee35a62…`（検証 済） / 展開物集約 `60c5a31cf9e9bc3e…` (303 files) / new_root `/tmp/claude-0/-home-user-ugh-prompt-engine/0af06e53-6f38-53ad-9662-a107f94f5678/scratchpad/voicegenesis_materials/pjs_ex/PJS_corpus_ver1.1`
  - `ritsu_singing_db`: archive `3bb2e8e287a5cc88…`（検証 済） / 展開物集約 `f634340640b80881…` (220 files) / new_root `/tmp/claude-0/-home-user-ugh-prompt-engine/0af06e53-6f38-53ad-9662-a107f94f5678/scratchpad/voicegenesis_materials/ritsu_ex/üuögë╣âèâcüvë╠É║âfü[â^âxü[âXVer2.0.2`
- conditions: B0, F, D, FD / candidate pairs: 6

## Overall

**NOT_ESTABLISHED**

> **S4 NOT ESTABLISHED — gene 単独成立は維持されるが、複合発現時の知覚または Identity 保持を本条件では確認できなかった。**

どの結果でも S2 PASS / S3 PASS / S3.5 の結果は変更しない（§16）。

## Mechanistic Gate（§11）

**PASS** — candidate 6 / evaluable 6 / combinable 6 / ratio 1.000

| check | 結果 |
|---|---|
| candidate_pairs | pass |
| distinct_contexts | pass |
| support_ratio | pass |
| supported_contexts | pass |
| no_structural_failures | pass |
| no_determinism_failures | pass |
| no_s3_replay_mismatches | pass |

閾値（事前登録・変更禁止 §24）: {'min_candidate_pairs': 4, 'min_contexts': 2, 'min_support_ratio': 0.75, 'min_supported_contexts': 2}

contexts: medial_ri, terminal_i, terminal_ri / supported contexts: medial_ri, terminal_i, terminal_ri

## Pair-Level（§9 / §10）

| pair | context | verdict | F0 alone | F0 with D | F0 retention | Dur alone | Dur with F | Dur retention | FD distinct | id margin |
|---|---|---|---|---|---|---|---|---|---|---|
| `terminal_ri|2018#215|pjs003#65` | terminal_ri | COMBINABLE | +75.400 | +74.240 | +0.985 | +8.823 | +8.823 | +1.000 | yes | +15.507 |
| `terminal_i|1st_color#86|pjs001#54` | terminal_i | COMBINABLE | +45.378 | +43.062 | +0.949 | +25.840 | +25.840 | +1.000 | yes | +8.374 |
| `medial_ri|1st_color#104|pjs002#42` | medial_ri | COMBINABLE | +47.521 | +52.462 | +1.104 | +26.233 | +26.233 | +1.000 | yes | +11.922 |
| `terminal_ri|2018#598|pjs065#29` | terminal_ri | COMBINABLE | +26.897 | +29.359 | +1.092 | +37.060 | +37.060 | +1.000 | yes | +7.331 |
| `terminal_i|1st_color#218|pjs001#73` | terminal_i | COMBINABLE | +84.320 | +82.353 | +0.977 | +52.500 | +52.500 | +1.000 | yes | +8.218 |
| `medial_ri|1st_color#357|pjs002#54` | medial_ri | COMBINABLE | +51.931 | +56.064 | +1.080 | +42.500 | +42.500 | +1.000 | yes | +9.315 |

- 効果量は全て **lower-is-better metric の差**（正 = 改善）。F0 = `f0_dev_rmse_cents`、Duration = `note_split_mae_ms`。
- retention は **診断値**であり Gate に使わない（§9.3）。`>1` 相乗 / `0〜1` 弱まるが残る / `<=0` 消失または逆転。

## Perceptual Gate（§13〜§15）

**NOT_ESTABLISHED** — ABX 3/4 正解 / Identity 2/2 YES

- gene retention: PERCEPTUAL_COEXPRESSION_NOT_ESTABLISHED
- identity: IDENTITY_PRESERVED

## Notes

- WAV / private key / answers は commit しない（`results/.gitignore`）。
- 本記録は S4 の契約（設計書 v1.0）だけを対象とし、範囲外の品質問題は修正も測定もしていない（§2）。

### observed_but_out_of_scope

- ABX の X は A か B と byte-identical なので、聴取者が 3 ファイルを sha256 で突き合わせれば聴かずに正答できる。commitment 方式が守るのは「実験者が回答後に正解を変えないこと」であって聴取者の自己申告ではない（S3.5 と同じ既知の性質）。プロトコル変更は §24 で禁じられているため実装では手を付けず、記録にのみ残す。
- ABX 4 問の偶然一致確率は 1/16。本 Gate は統計的有意差ではなく工学的進行 Gate である（設計書 §15.1 が明記）。
- 第 1 走行の実測: `note_split_mae_ms` は F0 トグルに対して**完全に不感**で、全 6 pair で metric(B0) == metric(F) かつ metric(D) == metric(FD) が厳密に成立した。したがって §9.2 の Duration 側増分 (metric(F) - metric(FD)) は §9.1 の単独増分 (metric(B0) - metric(D)) と数値的に同一で、duration_retention は構造上つねに 1.000 になる。つまり **Duration 軸の機械 Gate は「F0 背景でも残るか」を実質的に検定していない**（合成経路上、F0 トグルは note の尺に触れないため）。F0 軸は同一でない（例: 75.400 -> 74.240, retention 0.985）ので、機械側で共発現を実測しているのは F0 軸のみ。Duration 軸の共発現は §13.2 の ABX が担う。metric の変更は §24 で禁止されているため実装では手を付けず記録に残す。
