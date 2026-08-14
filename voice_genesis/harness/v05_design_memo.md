# v0.5 改修設計メモ — 第 5 サイクル（軸別測定帯域による grip 完成）

対象: formant_scale / spectral_tilt の gate 到達（第 2 試行）。
v4 の教訓: joint fit の失敗根因は **probe suite の中高音域における倍音
サンプリング不足**（f0 がフォルマント分離幅と同オーダー以上 → 2 ピーク
分解が情報量的に不可能 → 1 ピーク縮退 → 自由度の奪い合い）。
アルゴリズムでなく測定帯域を直す。

## A. 軸別測定帯域（gate 意味論 v4）

- 各 grip 軸は **measurement band** を宣言する。band 外は svp-rpe の band
  語彙に倣い `out_of_band` として除外を明示（黙って落とさない）。
- 帯域割当（凍結）:
  - breathiness / vibrato_depth: 従来 suite {C3,E4,A4,C5,C6}（変更なし。
    PASS 済みのため再測不要、v4 結果を再掲）
  - **formant_scale / spectral_tilt: 低音域 suite {G2, C3, E3, G3, C4}**
    （f0 98–262 Hz。4kHz 以下に倍音 ≥15 本、f0 < F1–F2 分離幅 350Hz、
    2 ピーク分解が情報量的に成立する帯域）
- 宣言の根拠（レポートに転記必須): フォルマント推定の分解能は倍音間隔で
  律速される。中高音域の tract 特徴は `out_of_band`（計器分解能外）で
  あり「測って失敗」ではなく「計器適用範囲外」として扱う。設計書 §1.5e
  （極端音域は監査方式を切り替える）と同型の原理。

## B. 測定と判定

- レンダラ R0.1・強化推定器・joint fit（v4 の安全機構込み）を無変更で使用。
  低音 suite では 2 ピークモデルの採用率が上がるはず — `fit_mode` 分布を
  必ず記録し、1 ピーク縮退が残る場合はその割合を明示。
- sweep 値域・σ_meas 3 反復・caveat 規約・E/grip/方向一致の定義は v0.3 §A
  を継承（probe 集合のみ差替え）。
- 免除表規約は v04 §B のまま（1 軸 1 エントリ・sign 全数一致・
  E(declared) <= 0.5×E(intended)）。まず免除なしで判定し、未達の軸のみ宣言。
- 終了状態: 4/4 PASS が目標。未達が残る場合は無理に通さず、残存値・機序・
  fit_mode 分布を記録して終了（fail-closed 記録が成果物）。

## C. 成果物

- `vt3_v5.py`（measure_v4 流用。新規 measure 実装は不要のはず。必要になった
  場合のみ `measure_v5.py` を作り理由を underspec_log_v5.md に記録）
- `results_v5/grip_report_v5.json` / `run_summary_v5.md`（gate 判定表・
  band 宣言と根拠・免除表・fit_mode 分布・v0.2→v5 全推移・非退行確認）/
  `underspec_log_v5.md`
