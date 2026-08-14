# 試作品 1 号 最終統合メモ — E2E デモ + 受け入れ判定

ゴール: 設計書 §12 マイルストーン
「Voice Genome を触ると意図した音響特徴が測定上動き、その声が誰にも
照合されないことを、版管理された手続きで確認できる」の一括実証と
受け入れチェックリスト判定。配置は proto1/ 配下、リポジトリ読み取り専用。

## F1. E2E デモ（`proto1_demo.py`）

1 コマンドで以下を決定論実行し、`results_final/e2e_run.json` に全記録:

1. **創生**: `sample(seed=101)`, `sample(seed=202)` → `mutate(101系, seed=303)`
   → `crossover(101系mut, 202系, seed=404)` の計 4 Genome（系譜の実演）
2. **レンダ**: 各 Genome で probe suite 全 5 種（P2）をレンダし波形 hash 記録
3. **検査**（Genome ごと）:
   - plausibility: sustain/phrase/cross_range の各レンダで periodicity
     r_median >= 0.35（vt_harness VT-1 と同一床）
   - novelty: reference-set（standin-gallery-v1）に対する linkability 監査
     （E1/E2 両系統、チャンス帯 p95）。reference_set_hash と
     linkability_report_id を Genome の audit フィールドへ記録
4. **grip の参照записи**: grip はエンジン × 軸の性質（Genome 個体の性質では
   ない）ため、registry エントリの `eval.grip_ref` には
   `vt_harness/results_v6/grip_report_v6.json` への参照 + gate 意味論
   バージョン（"grip-v2/band-v4/frozen-v6"）を記録する（値のコピーではなく
   参照。読み取りのみ）
5. **登録**: 4 Genome を registry へ lineage 付きで登録。
   `lineage(crossover個体)` の遡上結果を demo 出力に含める
6. **決定論検証**: デモ全体を 2 回実行し、genome_id・全波形 hash・監査
   判定が一致すること（created_at のみ差異許容）を機械照合
7. **成果 WAV**: 監査 PASS した個体 1 つの phrase probe レンダを
   `results_final/prototype1_phrase_*.wav` として保存（試作品 1 号の
   「歌声」実演。cross_range ペアの WAV も保存）

## F2. 受け入れ判定（`results_final/acceptance_report.md`）

VG 項目ごとに Done 条件・evidence（ファイルパス + 数値）・判定を表で記す:

| 項目 | Done 条件（設計書 §12） | 判定材料 |
|---|---|---|
| VG-001 | schema validation test | test_genome 22 件 |
| VG-002 | 固定 fixture | test_probes + manifest hash |
| VG-003 | C2-C7 unit test | vt_harness VT-2 v3（両レンダラ PASS） |
| VG-004 | F0 追従・aliasing test | P6 aliasing 実測 + VT-2 |
| VG-005 | vowel/formant sweep test | P6 formant sweep + grip 記録 |
| VG-006 | 連続 scale で transition test | P6 register transition 実測 |
| VG-007 | R0 正典化 | R0.1 + VT-1 122/122 |
| VG-008 | sweep → 感度比 report | grip v6: 3/4 軸 gate PASS |
| VG-009 | seed reproducibility | test_sampler + F1-6 決定論検証 |
| VG-010 | parent/seed/version 保存 | test_registry + F1-5 lineage 実演 |
| VG-016 | sidecar 版管理・再監査トリガー | test_reference_set + stale_audit 実測 |

**正直会計セクション（必須）**:
- grip formant_scale: 効果は実証済み（E=5.05、免除表の数値条件全成立）だが
  方向一致率 0.60 < 0.90（ピーク位置推定ノイズ起因と診断済み）につき
  **gate 未認定の open issue**。3/4 軸認定 + 全 4 軸で意図効果の実在は確認、
  という事実をそのまま書く
- 耳上（human listening / ABX）: `not_observed`（本環境で実施不能）
- 実在 speaker embedding による novelty: `machine_dependent`（スタンドイン
  2 系統で手続きのみ実証。§7.5 の但し書き（法的判定ではない）を転記）
- residual gate（§8 RQ）: 本試作品は DSP-only で neural residual 不搭載
  → `not_applicable`（設計により空集合、fail-closed の必要なし）
- Phase 0 ゲート: PASS（vt_harness v3 実測の参照）

最後に総合判定（マイルストーン成立/不成立）を 1 段落で宣言する。
判定は evidence の指す実測値のみに基づくこと。

## F3. 成果物

- `proto1_demo.py` / `results_final/e2e_run.json` /
  `results_final/prototype1_phrase_*.wav`（+ cross_range WAV）/
  `results_final/acceptance_report.md` / `results_final/underspec_log_final.md`
- 実行様式: 全てフォアグラウンド、決定論、数分規模。
