# acceptance_report.md — 試作品 1 号 最終受け入れ判定

対象マイルストーン（設計書 §12「実装バックログ」より原文引用）:

> **最初のマイルストーン（更新）**：VG-001〜VG-010 ＋ VG-016 で、「声の品質」
> ではなく「Voice Genome を触ると、意図した音響特徴が**測定上も耳上も**動き、
> かつその声が誰にも照合されないことを版管理された手続きで確認できる」こと
> を証明する。ここが成立した時点で本構想の最小 PoR が実証される。

本レポートは `final_assembly_memo.md` F2 の指示に従い、Done 条件・evidence・
判定を VG 項目ごとに表にまとめ、正直会計セクションと総合判定を付す。
判定は evidence が指す実測値のみに基づく。

## 1. VG 項目別 Done 条件 × Evidence × 判定

| ID | Task | Done 条件（設計書 §12） | Evidence（ファイル + 数値） | 判定 |
|---|---|---|---|---|
| VG-001 | VoiceGenome v0.2 dataclass / JSON schema | schema validation test | `tests/test_genome.py`（22 件 pass）: round-trip 等価性、境界値、`out_of_physio_range` フラグ付与、不正型拒否（`GenomeValidationError`）を検証 | **PASS** |
| VG-002 | Probe Score Suite 生成 | 固定 WAV/MIDI/phoneme fixture | `tests/test_probes.py`（8 件 pass）+ `probes.py`（5 probe 凍結定義、cross-range probe 含む）。`results_final/e2e_run.json` 実測例（genome b）: sustain=3ノート/hash `3ff579f7...`、register_sweep=46ノート/hash `304ceda1...`、vibrato=1ノート/hash `5e6f7c4c...`、phrase=8ノート/hash `94ef1ef5...`、cross_range=2ノート/hash `ca058f18...`。同一 Genome→同一 hash を F1-6 の 2 回実行決定論照合で実測確認 | **PASS** |
| VG-003 | F0/loudness/spectral/formant analyzer | 既知信号（R0 合成 GT）で C2–C7 unit test | `vt_harness/results_v3/bench_f0_v3.json`（VT-2 v3）: `overall_gate_pass=true`（`canary PASS and (v0 core gate) and (R0.1 core gate)`）。R0.1: `n_octave_errors=0`, `median_abs_cents_err=8.55`, `max_abs_cents_err=41.46`。v0: `median_abs_cents_err=8.68`, `max_abs_cents_err=56.21`。両レンダラとも C2–C7 core band で違反なし | **PASS**（VT-2 継承。本サイクルで再実行はしていない） |
| VG-004 | Harmonic+Noise excitation | F0 追従・aliasing test | P6 実測（`results_p1/report_data.json`）: aliasing A4=-67.94dB, C3=-92.07dB, C6=-68.63dB（いずれも閾値 -40dB 未満で PASS）。F0 追従は VG-003 と同じ VT-2 evidence を共有 | **PASS** |
| VG-005 | Time-varying formant filter | vowel/formant sweep test | P6 実測: formant_scale 0.85→1.15 の 7 点掃引で `direction_consistency=0.833`（閾値 0.60 以上、PASS。厳密な単調性ではなく方向一致率で判定する理由は `underspec_log_p1.md` [UNDERSPEC-P1-13b]）。grip 記録: `vt_harness/results_v6/grip_report_v6.json` の `formant_scale` 軸は `direction_consistency=0.60`（grip gate 単体としては閾値 0.90 未達、§2 正直会計参照） | **PASS（P6 render health）/ grip gate は open issue**（詳細は §2） |
| VG-006 | Register mixer v0 | 連続 scale で transition test | P6 実測: register_sweep probe（46 ノート）の中央 50% 窓 RMS 隣接差、最大 0.558dB（閾値 6.0dB を大きく下回る） | **PASS** |
| VG-007 | Diagnostic Renderer R0 | 1音・母音ロングトーン生成 | R0.1 実装済み（`vt_harness/voice_r0_1.py`、正典化）。`vt_harness/results_v3/vt1_plausibility_v3.json`（VT-1 v3）: R0.1 / v0 いずれも `n_notes=122, n_violations=0, gate_pass=true`（`non_regression_vs_v0.3.regression=false`） | **PASS**（VT-1 継承） |
| VG-008 | Grip evaluator | sweep → 感度比 report | `vt_harness/results_v6/grip_report_v6.json`・`run_summary_v6.md`（grip-v2/band-v4/frozen-v6）: 4 軸中 3 軸 gate PASS（breathiness grip=3.170, vibrato_depth grip=9.215, spectral_tilt grip=4.357）。formant_scale は数値条件（grip_declared=4.354, sign=5/5, E_declared比率）は全成立だが `direction_consistency=0.60<0.90` で gate 未認定（open issue、§2） | **3/4 軸 PASS**（evaluator 自体は機能。1 軸は測定分解能起因の open issue） |
| VG-009 | Genome sampler/mutation | seed reproducibility | `tests/test_sampler.py`（13 件 pass）: 同一 seed→同一 Genome、境界フラグ、crossover 決定論。F1-6 実測: `results_final/e2e_run.json.determinism_check.passed=true`（2 回実行で genome_id・全波形 hash・監査判定が完全一致、`differing_paths=[]`） | **PASS** |
| VG-010 | Registry + lineage log | parent/seed/version 保存 | `tests/test_registry.py`（13 件 pass）+ `results_final/genome_registry.jsonl`（4 エントリ、`genome-registry/0.1`）。F1-5 実演: `lineage(d)` = `[a(362f443334a4,sample), c(78da62714c26,mutate), d(798e3c453476,crossover)]`（`results_final/e2e_run.json.run_2.lineage_of_d`）。各エントリは `parents`/`seed`/`version`(=genome.schema_version)/`renderer_version`/`audit.reference_set_hash`/`audit.linkability_report_id` を保存 | **PASS** |
| VG-016 | Reference-set registry | `reference-set/0.1` sidecar の作成・版管理・再監査トリガー | `tests/test_reference_set.py`（15 件 pass）+ 実測: `results_p1/report_data.json.p5_reference_set_sidecar`（`schema_version=reference-set/0.1`, `sha256` 付き）+ `p5_stale_audit_trigger`（現行 hash でマーキング→0件、別 hash でマーキング→4件全て `stale_audit=true`、再監査トリガーの発火を実測確認） | **PASS** |

**pytest 総計**: `python -m pytest tests -q` で **91 件全 pass**
（`results_p1/_pytest_final_q.txt` / 本サイクルでの再実行でも regression なしを確認済み）。

## 2. 正直会計セクション（必須）

### grip formant_scale（open issue）

`final_assembly_memo.md` の原文（この節はメモの文言に従う旨の指示に基づき、
以下は原文をそのまま転記）:

> grip formant_scale: 効果は実証済み（E=5.05、免除表の数値条件全成立）だが
> 方向一致率 0.60 < 0.90（ピーク位置推定ノイズ起因と診断済み）につき
> **gate 未認定の open issue**。3/4 軸認定 + 全 4 軸で意図効果の実在は確認、
> という事実をそのまま書く

**出典確認の脚注**: 上記の `E=5.05` は `vt_harness/results_v6/grip_report_v6.json`
の v6 確定値とは一致しない（v6 実測: `E_intended = grip_declared = 4.3537`、
`sign_consistency=5/5`、`E_declared(source_tilt)=1.7616 <= 0.5*E_intended=2.1768`
はいずれも成立、`direction_consistency=0.60 < 0.90` のみ不成立で
`final_gate_pass=false`）。`E=5.05` は v5 サイクル時点の免除後値
（`run_summary_v6.md` 全推移表: `v5(no-ex/免除後)=2.826 / 5.051`）に近く、
本メモ執筆時点の値の取り違えである可能性が高い。**本レポートの判定・総合
判定は、メモの丸めた文言ではなく `vt_harness/results_v6/grip_report_v6.json`
から直接読み取った v6 一次 evidence の数値（`grip_declared=4.3537`,
`dir=0.60`）を根拠とする**（詳細は `underspec_log_final.md`
[UNDERSPEC-F-9]）。数値の取り違えとは無関係に、「3/4 軸は grip gate 認定、
formant_scale は効果の実在は確認済みだが方向一致率のみ不成立」という定性的
結論そのものは v5/v6 いずれの数値でも変わらない。

### 耳上（human listening / ABX）

`not_observed`。本実行環境（フォアグラウンド・非対話バッチ実行）では
人間による聴取評価（ABX 等）を実施できない。設計書 §12 マイルストーンの
「測定上**も耳上も**動き」のうち、**耳上の要件は本サイクルでは検証できて
いない**。この欠落は隠さず明示する。

### 実在 speaker embedding による novelty

`machine_dependent`。`results_final/e2e_run.json` の linkability 監査は
`standin-gallery-v1`（8 個の合成スタンドイン Genome）に対して実施されて
おり、実在歌手の音声で訓練された識別 embedding は本環境では利用不能なため
未実装（`reference_set.py` の `coverage_notes` に明記済み）。手続き
（gallery enrollment → probe 監査 → チャンス帯比較 → gate 判定 →
`reference_set_hash`/`linkability_report_id` の記録 → `stale_audit`
再監査トリガー）そのものは実測で機能を確認したが、**この手続きが実在人物
に対しても同様に機能する保証はない**（識別 embedding の差し替えを要する）。

設計書 §7.5 の判定但し書き（原文転記）:

> 7. 判定の但し書き:
>    本監査は「参照集合内の誰にも接近していない」ことの工学的確認であり、
>    独創性・著作隣接権・パブリシティ権に関する法的判定ではない。

本レポートの linkability 監査結果（4 Genome 中 3 個 PASS: b, c, d。
a は E2 系統でチャンス帯超過により FAIL、詳細は `e2e_run.json` 参照）も
この但し書きの範囲内でのみ解釈されるべきであり、法的な独創性判定ではない。

### residual gate（§8 RQ）

`not_applicable`。本試作品は DSP-only（R0.1 加算合成 + フォルマントフィル
タ）であり neural residual を搭載していない。設計書 §8 の Residual Identity
Quarantine Gate（RQ-1〜RQ-4）は residual の on/off 比較を前提とするため、
residual が存在しない本試作品では**設計により対象が空集合**であり、
fail-closed とする必要もない。`genome.py` の `AuditSection.residual_gate_passed`
は全 Genome で `None`（"not_applicable" の意味、`underspec_log_p1.md`
[UNDERSPEC-P1-8] で規定済み）のまま一貫している。

### Phase 0 ゲート（設計書 §9 Stage Gate）

**PASS**。Phase 0 の Stage Gate は「同一入力で安定測定 かつ 推定器が
C2–C7 全域で合成 GT に対し許容誤差内」。§1 の VG-003/VG-004 evidence
（`vt_harness/results_v3/bench_f0_v3.json` の `overall_gate_pass=true`、
VT-1 v3 の `0/122 violations`）がそのまま Phase 0 ゲートの evidence になる。

## 3. 総合判定

VG-001〜VG-010 ＋ VG-016 の 11 項目中、**10 項目は evidence 実測により
無条件で PASS**（VG-001, 002, 003, 004, 006, 007, 009, 010, 016、および
VG-008 は evaluator 機能そのものは PASS）。VG-005/VG-008 に関連する grip の
`formant_scale` 軸のみ、効果の実在（`E_intended=4.3537`, 符号一致 5/5）は
確認済みだが方向一致率不足（`0.60<0.90`）により gate 未認定の open issue
として残る（原因はモデル選択由来ではなくピーク位置推定自体の測定ノイズと
6 サイクルにわたる調査で診断済み、`vt_harness/results_v6/run_summary_v6.md`
参照）。この 1 点を除けば「Voice Genome を触ると、意図した音響特徴が
**測定上**動く」ことは 4 軸中 3 軸で grip gate を通し、残り 1 軸も P6 の
簡易確認（direction_consistency 基準）では PASS しており、実測で確認された。
「その声が誰にも照合されないことを版管理された手続きで確認できる」ことも、
`results_final/e2e_run.json` で 4 Genome に対する linkability 監査
（E1/E2 二系統、チャンス帯 95 パーセンタイル比較、`reference_set_hash`/
`linkability_report_id` の Genome への記録、`stale_audit` 再監査トリガーの
実測）として実演され、2 回の独立実行で genome_id・全波形 hash・監査判定が
完全に一致する決定論性も確認された。**ただし**この照合手続きは合成スタン
ドイン gallery に対してのみ実証されたものであり（`machine_dependent`）、
かつマイルストーン原文が要求する「**耳上**」の検証は本環境で `not_observed`
のまま残る。以上より、**マイルストーンは「測定上・版管理された手続き上」の
要件については実証されたが、「耳上」要件が未検証であり、かつ novelty 監査
が実在話者ではなく合成スタンドインに限定されている 2 点により、マイルス
トーンの完全な成立は宣言できず、「測定サイドは成立、耳上サイドは
not_observed につき保留」の部分的達成と判定する**。次サイクルで人間聴取
評価（ABX 等）と実在話者ベースの identity embedding 導入（あるいはそれが
不可能な旨の明示的な受容）が、完全成立の残りの条件になる。

**2026-08-14 訂正**: 上記「2 回の独立実行で genome_id・全波形 hash・監査
判定が完全に一致する決定論性」の記述自体（実測結果・PASS 判定）は変わら
ないが、その決定論照合の除外フィールドは `created_at` の 1 件のみではなく
`created_at` と `registry_path` の 2 件である。本節執筆時点では
`results_final/e2e_run.json.determinism_check` の開示文言が前者のみを
述べていたため、`registry_path`（run ごとに異なる一時/正本ファイルパス
文字列。実行環境依存で内容非依存のため除外）が黙って比較対象外になって
いる実態が本文へ反映されていなかった。詳細は `underspec_log_final.md`
[UNDERSPEC-F-8] と PR #261 レビュー C1・R15 スレッドを参照。
