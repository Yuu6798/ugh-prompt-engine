# DESIGN S1 — P2-PoC: 自前 base × 2 話者統合学習（B 案）

- 日付: 2026-08-15
- 位置づけ: `FOUNDRY_ROADMAP.md` S1。S0 通過（`results_s0/s0_record_2026-08-15.md`）を受けた実行設計
- 状態: **User 承認待ち**（GPU 実行 = 費用発生を含むため）
- 一次材料（scratchpad・非コミット）: `s1_survey.md` / `s1_base_license_survey.md` / `s1a_conversion_record.md`

## 0. 前提の更新（当初 ROADMAP からの変更点）

1. **選択肢 C（コミュニティ base fine-tune）は法的に全滅** — Qixuan は規約が fine-tune を
   名指し禁止 + PyTorch ckpt 非配布、openvpi 公式 pretrained は実在せず、Tiger は
   CC BY-NC-ND + Commons Clause（派生禁止）、多言語 base は配布重みなし
   （逐語引用 = s1_base_license_survey.md）
2. → **B 案採用: リツ素材で自前 base を作る**。リツ原音は商用可・改変可（権利者規約
   逐語確認済み・results_s0 参照）、PJS は CC BY-SA 4.0。**学習データの全構成要素が
   license-clean** になり、外部規約リスクが構成上消える
3. PJS 側のデータ準備は検証済み: nnsvs-db-converter 経由で 287 セグメント・
   binarize 全件通過（S1a スパイク）

## 1. 設計の核: S1 と S2 の部分統合（2 話者統合学習）

base→fine-tune の逐次 2 段でなく、**リツ + PJS の 2 話者を spk_embed 付きで
1 回の学習に統合**する（openvpi/DiffSinger の multi-speaker 機構）。

- 同一学習 1 回で **2 アンカー**（リツ声・PJS 声）が同時に立つ
- spk_embed の補間 = **S2「2 アンカー間の補間候補」の第一軸がそのまま手に入る**
  （工房一周・2 個体識別・「誰でもない声」判定への直結）
- 費用は from-scratch 1 回分と同じ。逐次 2 段（base 学習→fine-tune）より安い

## 2. スコープ最小化: acoustic のみ学習、variance はリツ公式 ONNX を流用

S0 検分より、推論チェーンは linguistic→dur→pitch（variance 系）と acoustic が分離
している。S1 では:

- **学習するのは acoustic モデルのみ**（音色・質感の担い手 = 耳ゲートの対象そのもの）
- **linguistic/dur/pitch はカノン氏配布のリツ DiffSinger ONNX を流用**（規約: 改変・
  再配布・商用可。results_s0 のライセンス会計参照）
- 制約: **音素 inventory をリツ公式辞書（617 語彙）に一致させる**。PJS 側の自前辞書
  （S1a・community 辞書と 1 音素差まで確認済み）をリツ辞書へ写像する差分表を作る
- vocoder は当面 NSF-HiFiGAN（CC BY-NC-SA・非商用留保は S0 会計と同じ）。
  商用パス用 BigVGAN v2 44kHz（MIT）への差し替えは deferred（§5）

## 3. データ工場（本環境・$0）

| 層 | 素材 | 変換 | 状態 |
|---|---|---|---|
| D1 | PJS 歌唱 100 曲・26.86 分 | nnsvs-db-converter → transcriptions.csv 287 seg | **検証済み**（S1a） |
| D2 | リツ VCV 録音（A3/F4・oto 1,237 エントリ×2） | oto アラインメント→音素粒度 ph_seq/ph_dur 変換器（新設スパイク S1b） | 未着手 |
| D3 | F1.4 合成レンダ（旋律多様性補完） | 既存レンダラ | **deferred**（最小スケール原則: D1+D2 で不足が実測されたときのみ） |

- D2 変換器は既存 oto 解析資産（donor_bank_utau）を read-only 流用して scratchpad で作る
- D2 の総収量（実効分数・音素被覆）を実測し record に記録（学習可否の入力）

## 4. GPU 実行（User 側・Vast.ai/RunPod 4090 スポット）

- 学習: acoustic from-scratch・2 話者・目安 40K steps（Tiger 実例準拠）。
  **早期打ち切りゲート**: 5K / 10K / 20K steps で checkpoint を引き上げ、本環境で
  ONNX export → CPU 推論 → さくら/うみ合成 → **User 耳判定**（S0 と同じ軸:
  日本語/滑らかさ/歌声/ノイズ）。5K 時点で発声が立っていなければ設定見直しで打ち切り
- runbook は実行者非依存で書く（M2e provisioning runbook の流儀)。環境構築 pin・
  データ tarball の sha256・学習 config・checkpoint 回収手順を含む
- 費用見積: 4090 スポット $0.3–0.5/h。40K steps の実時間は一次事例未発見のため
  **6–24h = $2–12 のレンジ見積り**（早期ゲートで下振れ優先。上限 $15 で打ち切り）

## 5. Deferred（backfill 原則 — 不足が実測されたときのみ）

- 自前 variance（dur/pitch）学習（リツ ONNX 流用で不足する場合）
- F1.4 合成レンダの学習投入（D1+D2 で音素/旋律被覆が不足する場合）
- BigVGAN v2 への vocoder 差し替え（商用リリース判断が立った場合）
- PJS 話者の単独 fine-tune 仕上げ（統合学習で PJS 声の再現が弱い場合）

## 6. Acceptance Criteria（S1 出口）

- [ ] D2 変換器が動き、リツ VCV の実効分数・音素被覆が record に記録される
- [ ] 統合 transcriptions.csv（2 話者・spk_id 付き）が binarize 全件通過
- [ ] GPU runbook（実行者非依存・pin 付き）が `docs/` または `foundry/` に置かれる
- [ ] 学習が回り、早期ゲート checkpoint の CPU 合成 WAV が User に届く
- [ ] **S1 ゲート（耳判定）**: 自前学習の合成が「土俵に乗る」か — 軸別
  （日本語/滑らかさ/歌声/ノイズ）で判定。全軸可なら S2 へ、不可なら不足軸を
  特定して §5 の backfill を検討
- [ ] `results_s1/s1_record_<date>.md`（統計・費用実測・耳判定逐語・Open Questions）

## 7. Scope

- IN: scratchpad でのデータ工場（D1/D2）・runbook 起草・`results_s1/` record
- OUT: `src/svp_rpe/**`・既存 adapter/singer コード変更（read-only 流用のみ）・
  D3/variance 学習/vocoder 差し替え（§5 deferred）・GPU 費用の $15 超過
