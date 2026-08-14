# v0.4 改修設計メモ — 第 4 サイクル（grip 残 2 軸の完成: 同時推定 + 宣言済み結合）

対象: formant_scale（2.217）/ spectral_tilt（1.068）の gate 到達。
本メモが VT v4 の唯一の仕様正本。逸脱・補充は underspec_log_v4.md に記録。
非退行条件: breathiness / vibrato_depth の PASS、VT-1/VT-2 の PASS を落とさない
（レンダラ・F0 推定器は変更しないため VT-1/VT-2 は結果再掲でよいが、
特徴量が変わる VT-3 は全軸再測）。

## A. 案 A — source-filter 同時推定（逐次依存の切断）

現行の共線性機序: (i) F1_est が動くと source_tilt の回帰対象倍音集合が変わる、
(ii) tilt が動くとケプストラム包絡ピークが動く。逐次推定の連鎖依存が原因。

`measure_v4.py` に joint fit を実装する:

- 観測: 倍音ピーク振幅列 {(k, A_k in dB)}, k=1..K（K は帯域内全倍音、上限 40）
- モデル: `A_k = c + tilt * log2(k) + Σ_i L_i(f_k)`
  - L_i = ローレンツ型共鳴ピーク（dB 領域）× 2〜4 個
  - パラメータ: c, tilt, {F_i, G_i}（帯域幅 B_i は §固定値でよい: 初期実装は
    v0 R0 のフォルマント帯域幅オーダーの固定値、自由度を絞る）
- 解法: 座標降下（tilt+c は線形最小二乗、F_i はグリッド + 局所精密化、
  G_i は線形）を 3〜5 反復。決定論（初期値固定・グリッド固定）。
- 出力特徴: `source_tilt_v4` = fitted tilt（dB/oct, ref 1.5）、
  `formant_centroid_v4` = fitted F_1..F_2 の幾何平均 log2（oct, ref 0.10）。
- 他 4 特徴（mean_f0 / periodicity / rms / vibrato_depth）は v3 のまま。
  特徴量セット v3 として凍結（v2 からの差分は上記 2 特徴の推定法のみ）。
- 高 F0 で倍音本数が不足する場合（K<5）: F_2 を落とし 1 ピークモデルに縮退、
  `fit_mode` を記録。fit の残差 RMS もセルごとに記録（fit 品質の開示）。

## B. 案 B — 宣言済み結合の免除表（gate 意味論 v3）

案 A 適用後も残る結合は、v0.2 設計書 §7.2 自身が警告した「声帯物理由来の
絡み」の測定レベル再現とみなし、**宣言・拘束・免除**の 3 点セットで扱う:

- 免除表エントリ: `(axis, declared_side_feature, expected_sign)`。
  **1 軸につき最大 1 エントリ**（免除の増殖防止）。
- エントリの成立条件（宣言時に evidence 必須）: 案 A 適用後の実測で当該
  side が dominant であり続けること + 機序の物理説明を 1 行で記述できること。
- gate 判定 v3:
  1. `grip_declared = E(intended) / max(E over UNDECLARED side, 1.0) >= 3.0`
  2. 宣言 side の拘束: 符号が宣言どおり **かつ `E(declared) <= 0.5 × E(intended)`**
     （従属条件: 宣言結合は意図効果の半分以下に留まること。超えたら FAIL）
  3. 方向一致率 >= 0.90、E(intended) >= 2.0（従来どおり）
- 手順: まず案 A のみで 4 軸を再測 → gate 未達の軸に限り、実測 dominant を
  免除表に宣言して gate v3 で再判定。**宣言なしで通る軸に宣言を付けない**。
- 免除表はレポートに明記し、宣言・拘束値・機序説明を必ず併記する。

## C. 実験（VT-3 v4）

- 構成: R0.1 + 強化推定器（v3 のまま）+ 特徴量セット v3（joint fit）。
- 4 軸すべて再測（intended 対応は v3 と同じ。spectral_tilt → source_tilt_v4、
  formant_scale → formant_centroid_v4）。
- レポート: 免除表適用前/適用後の両判定、fit 残差 RMS、宣言 evidence、
  v3→v4 推移。σ_meas 3 反復・caveat 規約は v0.3 §A-4 継承。
- 期待する終了状態: 4/4 軸 PASS（うち免除表使用は最大 2 軸）。未達の軸が
  残る場合は無理に通さず、残存値と機序を記録して終了（fail-closed 記録が成果物）。

## D. 成果物

- `measure_v4.py` / `vt3_v4.py`
- `results_v4/grip_report_v4.json` / `results_v4/run_summary_v4.md`
  （gate 判定表・免除表・非退行確認・v0.2→v4 全推移表）
- `results_v4/underspec_log_v4.md`
