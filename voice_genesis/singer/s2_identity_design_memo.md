# S2 設計メモ — Genome 知覚的分離度の強化（「別の歌手」に聞こえるか）

背景: Phase 2 ゲートは通過（`results_s1/phase2_gate_record.md`）。併記所見
= voice_A/B が耳で「別の歌手」に聞こえない。設計書 §5.3 の警告
（Genome 分散の音響的収束）の耳による初観測。本サイクルはこれを
**測って → 原因を特定して → 写像を強化して → 再判定素材を作る**。

## T1. まず測る（耳の所見の計器化）

1. **identity 分離度の定量化**: proto1/reference_set.py の E1/E2 スタンド
   イン embedding（読み取り import）で次を計測:
   - between（歌手違い・内容同一）: voice_A vs voice_B の同一フレーズ
     レンダ間距離（フレーズ 1・2 それぞれ）
   - within（歌手同一・内容違い）: 同一 voice のフレーズ 1 vs フレーズ 2 間距離
   - **分離判定: between > within が E1/E2 両系統・両フレーズで成立するか**。
     現状 A/B で不成立（耳の所見と整合）なら、それが定量的出発点になる
2. **JND 会計**: 同一ノート（例: 各フレーズ先頭 3 ノートの母音核）で
   A/B 間の特徴差（formant_centroid / source tilt 実測 / periodicity /
   vibrato_depth / mean_f0）を JND 参照スケール（v0.3 表）で割った効果量
   として算出。**「A と B は実際どの軸で何 JND 離れているのか」の表**を作る。
   仮説: A/B は breathiness・vibrato 中心の差で、歌唱文脈ではマスクされ、
   tract 系（formant/tilt）の差が小さい — 表で検証する

## T2. 写像・Genome の強化（T1 の結果に基づき実施）

1. **identity 担持次元の写像ゲイン確認**: formant_scale / formant_offsets /
   tilt→open quotient が R0.9 の歌唱経路で母音目標表に確実に作用している
   か（vowel 目標が支配して identity 変換が希釈されていないか）をコードと
   実測の両方で確認。希釈があれば写像を修正（Genome 契約は維持）
2. **対照的な歌手ペアの設計**: 新 Genome ペア voice_C / voice_D を定義:
   - voice_C「大きく暗い声道」: formant_scale ≈0.87、tilt 深め（-13）、
     breathiness 低、vibrato 控えめ（rate 5.0 / depth 30c）
   - voice_D「小さく明るい声道」: formant_scale ≈1.15、tilt 浅め（-6）、
     breathiness 中（0.25 程度。B の教訓: 極端値の重ね掛けは計測クロス
     トークを招くため中庸に）、vibrato 速め（rate 6.3 / depth 55c）
   - いずれも物理事前分布内（out_of_physio_range を立てない）
3. 差が最大になるよう **T1 の JND 表を見ながら**調整してよいが、
   1 パラメータでも表の値の根拠なしに動かさないこと（調整過程を記録）

## T3. 受け入れ条件（機械 → 耳の順）

1. **機械 identity 分離**: voice_C/D で T1-1 の between > within が
   E1/E2 両系統・両フレーズで成立（margin も記録）。スタンドイン計器で
   ある旨の caveat 併記は従来どおり
2. **JND 会計**: C/D 間の tract 系特徴差が **3 JND 以上**（formant_centroid
   ≥0.3 oct 相当 or source tilt ≥4.5 dB/oct 相当のいずれか）
3. **S5 機械ゲート**: voice_C / voice_D の両方で gate1–6 全通過
   （gate6 は breathiness / vibrato_depth の 2 軸。B の轍を踏まない設計を
   T2-2 で織り込み済み）
4. 全通過後、`sakura_voiceC.wav` / `sakura_voiceD.wav` を耳判定素材として
   出す（判定者 = User。「別の歌手に聞こえるか」）。A/B も比較用に再掲

## T4. 成果物

- `singer/identity_metrics.py`（T1 計測）、Genome 定義の追加（voice_C/D）、
  必要なら写像修正（修正箇所と理由を記録）
- `singer/results_s2/identity_report.md`（T1 の現状 A/B 計測表 +
  C/D の受け入れ実測 + JND 会計表 + S5 ゲート表）
- `singer/results_s2/sakura_voiceC.wav` / `sakura_voiceD.wav`
- `singer/results_s2/underspec_log_s2.md`
- 実行様式: 従来どおり（読み取り専用リポジトリ・singer/ 配下のみ書き込み・
  フォアグラウンド・決定論・数分規模）
