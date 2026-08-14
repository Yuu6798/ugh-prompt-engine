# 試作品 1 号 完成報告 — UGH Voice Genesis Engine 歌唱工房 PoC

日付: 2026-08-13 / 最終判定: Fable（設計・判定）、実装・実行: Sonnet 委譲
リポジトリ: 全工程で読み取り専用（変更・コミット・PR なし）。
成果物はすべて scratchpad（`vt_harness/` = 計器・grip 系、`proto1/` = 工房本体）。

---

## 1. 最終判定

**試作品 1 号は、仮想検証の範囲で PoC として成立**と判定する。

設計書 §12 マイルストーン「Voice Genome を触ると、意図した音響特徴が
測定上も耳上も動き、かつその声が誰にも照合されないことを版管理された
手続きで確認できる」に対して:

| 要件 | 判定 | 根拠 |
|---|---|---|
| Genome → 音響特徴が**測定上**動く | **成立** | grip gate 認定 3/4 軸（breathiness 3.17 / vibrato 9.22 / spectral_tilt 4.36）。formant_scale も効果の実在は確認（E=4.35、符号 5/5）、方向一致率のみ未達の open issue |
| 誰にも照合されないことの確認**手続き** | **成立**（スタンドイン範囲） | linkability 監査 E1/E2 二系統 + チャンス帯 p95 + reference_set_hash ペア記録 + stale_audit 再監査トリガー、すべて実測動作 |
| **版管理された手続き** | **成立** | reference-set/0.1・genome-registry/0.1 sidecar、content hash、系譜遡上、2 回実行で genome_id・全波形 hash・監査判定が完全一致する決定論 |
| **耳上**も動く | not_observed | 人間聴取は本環境で実施不能（隠さず明記） |
| 実在話者への非照合 | machine_dependent | 実在歌手で訓練された識別 embedding は本環境で調達不能。手続きのみ実証 |

VG-001〜VG-010 + VG-016 の 11 項目は **10 項目無条件 PASS + VG-008 は
3/4 軸認定**。pytest 91/91 パス。詳細は
`proto1/results_final/acceptance_report.md`（一次 evidence への全ポインタ付き）。

## 2. 試作品 1 号ができること（工房の一巡）

`proto1_demo.py` の 1 コマンドで以下が決定論的に一巡する:

1. **創生**: sample × 2 → mutate → crossover の 4 Genome（家系図付き）
2. **演奏**: 各 Genome で probe suite 5 種（持続 / 声区スイープ / ビブラート /
   8 音フレーズ = 最小の歌唱実演 / C3-C6 クロスレンジ）をレンダ
3. **検査**: plausibility（周期性床、ノート単位）+ linkability 監査
4. **登録**: registry へ系譜・seed・レンダラ版・監査結果・reference_set_hash
   を記録。`lineage()` で crossover 個体から祖先を遡上可能
5. **実演音声**: 監査 PASS 個体の phrase / cross_range WAV を出力

特筆事項: デモ実行中、**sample 個体 1 つ（genome a）が E2 系統の
linkability でチャンス帯を超過し正しく FAIL** した — 監査ゲートが
「たまたま gallery の誰かに近い声」を実際に弾けることの実地実証になった。

## 3. ここまでの経緯（7 サイクル）

| サイクル | 内容 | 帰結 |
|---|---|---|
| 1 | 設計書 v0.2 精査 + 初回仮想テスト | grip 定義の構造欠陥（z-score）を実証、未規定 17 件 |
| 2 | grip v2（JND 効果量）+ 計器修復 + R0.1 | ゲートが機能開始。R0.1 の高音回帰を新ゲートが捕捉 |
| 3 | 特徴量直交化 + 推定器強化 | Phase 0 全数 PASS、breathiness 到達、2/4 |
| 4 | source-filter 同時推定 | 失敗を正直記録（1 ピーク縮退 = データ情報量制約） |
| 5 | 軸別測定帯域（band 宣言） | formant 前進、阻害がモデル選択不連続に絞られる |
| 6 | sweep 内計器凍結 | **spectral_tilt 免除なし PASS、3/4 確定**、formant は測定ノイズ起因の open issue として終端 |
| 7 | 工房骨格（91 テスト）+ E2E 統合 + 受け入れ判定 | **試作品 1 号成立** |

## 4. 完全成立への残条件（次セッション以降）

1. **耳上検証**: 人間 ABX（cross_range ペア WAV は §7.1 の実験素材として
   出力済み）
2. **実在話者 embedding の導入**（または不採用の明示的受容）と reference-set
   の実データ化 — stale_audit トリガーにより過去 Genome の再監査が自動要求
   される設計は実装済み
3. formant_scale の方向一致率: sweep 点あたり反復レンダ平均などの
   分散低減策（診断済み: ピーク位置推定ノイズが根因）
4. 設計文書 v0.3 への反映（サイクル 1–6 の全所見・凍結数値・underspec
   計 40+ 件の仕様化）

## 5. 成果物索引

- **工房本体**: `proto1/`（genome / probes / sampler / registry /
  reference_set / plausibility / render_health / bridge / hashing、
  tests 91 件、`proto1_demo.py`）
- **受け入れ判定**: `proto1/results_final/acceptance_report.md`
- **E2E 記録**: `proto1/results_final/e2e_run.json`、`genome_registry.jsonl`
- **試作品の歌声**: `proto1/results_final/prototype1_phrase_5daa285c4dba.wav`、
  `prototype1_cross_range_pair_5daa285c4dba.wav`
- **計器・grip 系**: `vt_harness/`（R0/R0.1、measure v2–v6、VT 全結果、
  設計メモ v0.3〜v0.6、underspec ログ全冊）
