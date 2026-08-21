# S3 RECORD — Performance Gene Isolation & Independent Transplant

- schema: `voicegenesis-genome-s3/1.1`
- source_commit: `8e2391ee9325cc8648f7dfb5132441a2b3947a6b` (clean worktree)
- input_manifest_sha256: `78fcd8cc6e3f2b7c9edae72e0894f552b9cdfb0087d44b1df0d45402063fec4f`
- pairs: 8 / conditions: B0, D, E, F, R
- context_phones: 22 / identity_ap_scale: 0.25

## Overall

**PASS** — supported_gene_count = 4 (必要 2): duration, energy, f0, release

> **Genome Architecture S3 PASS — Performance の複数形質が、複数 pair・複数文脈の実コーパス上で、独立操作可能かつ再現可能な gene として部分分解できた。**

どちらの場合も S2 の成功判定は変更しない。

## Gene-Level

| gene | verdict | evaluable | supported | ratio | eval ctx | sup ctx | struct fail | det fail |
|---|---|---|---|---|---|---|---|---|
| f0 | SUPPORTED | 8 | 6 | 0.750 | 4 | 3 | 0 | 0 |
| duration | SUPPORTED | 6 | 6 | 1.000 | 3 | 3 | 0 | 0 |
| energy | SUPPORTED | 8 | 8 | 1.000 | 4 | 4 | 0 | 0 |
| release | SUPPORTED | 6 | 6 | 1.000 | 3 | 3 | 0 | 0 |

閾値（事前登録・変更禁止）: {'min_evaluable_pairs': 3, 'min_evaluable_contexts': 2, 'min_supported_contexts': 2, 'min_support_ratio': 0.75, 'min_supported_genes': 2}

## Pair-Level

### f0 — metric `f0_dev_rmse_cents` (lower is better)

| pair | context | verdict | P1 | P2 amount | P3 B0 → gene | P4 |
|---|---|---|---|---|---|---|
| `terminal_ri|2018#215|pjs003#65` | terminal_ri | SUPPORTED | pass | 273.756 cent | 95.271 → 19.871 | pass |
| `terminal_i|1st_color#86|pjs001#54` | terminal_i | SUPPORTED | pass | 264.134 cent | 71.947 → 26.569 | pass |
| `terminal_N|1st_color#478|pjs002#61` | terminal_N | UNSUPPORTED | pass | 15.904 cent | 28.529 → 29.819 | pass |
| `medial_ri|1st_color#104|pjs002#42` | medial_ri | SUPPORTED | pass | 256.548 cent | 80.341 → 32.820 | pass |
| `terminal_ri|2018#598|pjs065#29` | terminal_ri | SUPPORTED | pass | 203.876 cent | 43.281 → 16.384 | pass |
| `terminal_i|1st_color#218|pjs001#73` | terminal_i | SUPPORTED | pass | 351.069 cent | 134.414 → 50.094 | pass |
| `terminal_N|BRD#664|pjs017#27` | terminal_N | UNSUPPORTED | pass | 75.337 cent | 49.013 → 53.669 | pass |
| `medial_ri|1st_color#357|pjs002#54` | medial_ri | SUPPORTED | pass | 354.003 cent | 98.937 → 47.006 | pass |

### duration — metric `note_split_mae_ms` (lower is better)

| pair | context | verdict | P1 | P2 amount | P3 B0 → gene | P4 |
|---|---|---|---|---|---|---|
| `terminal_ri|2018#215|pjs003#65` | terminal_ri | SUPPORTED | pass | 8.655 ms | 9.726 → 0.903 | pass |
| `terminal_i|1st_color#86|pjs001#54` | terminal_i | SUPPORTED | pass | 26.601 ms | 26.670 → 0.830 | pass |
| `terminal_N|1st_color#478|pjs002#61` | terminal_N | NOT_EVALUABLE | pass | 0.000 ms *(構造的 no-op)* | 1.770 → 1.770 | pass |
| `medial_ri|1st_color#104|pjs002#42` | medial_ri | SUPPORTED | pass | 27.094 ms | 27.913 → 1.680 | pass |
| `terminal_ri|2018#598|pjs065#29` | terminal_ri | SUPPORTED | pass | 37.698 ms | 37.560 → 0.500 | pass |
| `terminal_i|1st_color#218|pjs001#73` | terminal_i | SUPPORTED | pass | 53.904 ms | 54.031 → 1.531 | pass |
| `terminal_N|BRD#664|pjs017#27` | terminal_N | NOT_EVALUABLE | pass | 0.000 ms *(構造的 no-op)* | 1.845 → 1.845 | pass |
| `medial_ri|1st_color#357|pjs002#54` | medial_ri | SUPPORTED | pass | 43.298 ms | 43.025 → 0.525 | pass |

### energy — metric `energy_corr` (higher is better)

| pair | context | verdict | P1 | P2 amount | P3 B0 → gene | P4 |
|---|---|---|---|---|---|---|
| `terminal_ri|2018#215|pjs003#65` | terminal_ri | SUPPORTED | pass | 29.757 dB | 0.360 → 0.953 | pass |
| `terminal_i|1st_color#86|pjs001#54` | terminal_i | SUPPORTED | pass | 9.003 dB | 0.430 → 0.902 | pass |
| `terminal_N|1st_color#478|pjs002#61` | terminal_N | SUPPORTED | pass | 4.457 dB | 0.208 → 0.760 | pass |
| `medial_ri|1st_color#104|pjs002#42` | medial_ri | SUPPORTED | pass | 5.583 dB | -0.143 → 0.557 | pass |
| `terminal_ri|2018#598|pjs065#29` | terminal_ri | SUPPORTED | pass | 9.078 dB | 0.755 → 0.852 | pass |
| `terminal_i|1st_color#218|pjs001#73` | terminal_i | SUPPORTED | pass | 4.661 dB | 0.298 → 0.645 | pass |
| `terminal_N|BRD#664|pjs017#27` | terminal_N | SUPPORTED | pass | 4.052 dB | 0.067 → 0.815 | pass |
| `medial_ri|1st_color#357|pjs002#54` | medial_ri | SUPPORTED | pass | 27.319 dB | 0.274 → 0.980 | pass |

### release — metric `taper_rmse_db` (lower is better)

| pair | context | verdict | P1 | P2 amount | P3 B0 → gene | P4 |
|---|---|---|---|---|---|---|
| `terminal_ri|2018#215|pjs003#65` | terminal_ri | SUPPORTED | pass | 7.161 dB | 10.796 → 2.725 | pass |
| `terminal_i|1st_color#86|pjs001#54` | terminal_i | SUPPORTED | pass | 8.729 dB | 8.406 → 1.481 | pass |
| `terminal_N|1st_color#478|pjs002#61` | terminal_N | SUPPORTED | pass | 0.477 dB | 9.473 → 0.860 | pass |
| `medial_ri|1st_color#104|pjs002#42` | medial_ri | NOT_EVALUABLE | pass | 7.156 dB *(構造的 no-op)* | 2.519 → 2.519 | pass |
| `terminal_ri|2018#598|pjs065#29` | terminal_ri | SUPPORTED | pass | 11.569 dB | 10.346 → 2.731 | pass |
| `terminal_i|1st_color#218|pjs001#73` | terminal_i | SUPPORTED | pass | 2.752 dB | 9.335 → 0.611 | pass |
| `terminal_N|BRD#664|pjs017#27` | terminal_N | SUPPORTED | pass | 2.087 dB | 3.132 → 0.199 | pass |
| `medial_ri|1st_color#357|pjs002#54` | medial_ri | NOT_EVALUABLE | pass | 15.209 dB *(構造的 no-op)* | 8.261 → 8.261 | pass |

## Notes

- WAV 本体は commit しない（`results/.gitignore`）。SHA のみ記録する。
- 本記録は S3 の契約（設計書 v1.1）だけを対象とし、範囲外の品質問題は修正も測定もしていない。
