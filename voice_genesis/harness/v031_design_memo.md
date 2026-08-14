# v0.3.1 改修設計メモ — 第 3 サイクル（特徴量直交化・推定器強化・breathiness 到達）

対象: v0.3 再テスト（`v03_retest_report.md`）の申し送り 3 課題。
本メモが VT v3 の唯一の仕様正本。逸脱・補充は underspec_log_v3.md に記録。
gate 原則: **前サイクルで PASS した項目を落とさないこと（非退行条件）** を
全テストに追加する。

## A. 特徴量セット v2（凍結）— 共線性の構造的解消

共線性の根因は「広帯域 spectral_centroid」と「全倍音回帰 spectral_tilt」が
定義上絡み合うこと。声帯源（source）と声道（tract）の物理分離に合わせて
特徴量を分解する。

| feature | 定義 | 単位 | ref_scale |
|---|---|---|---|
| mean_f0 | 変換 1200*log2(f0) | cents | 25 |
| **formant_centroid**【新】 | ケプストラム平滑化したスペクトル包絡の 300–4000 Hz ピーク上位 2 点の幾何平均を log2 で | octave | 0.10 |
| **source_tilt**【新】 | 倍音振幅回帰を **k=1..min(8, 0.9×F1_est/f0)** の低次倍音のみに制限した勾配（声帯源傾斜。声道ピークの汚染を回避）。使用可能倍音が 3 本未満のノート（高 F0）は H1−H2 (dB) に自動フォールバックし、`tilt_estimator: "h1h2"` を記録 | dB/oct（H1−H2 時は dB、ref 2.0） | 1.5 |
| periodicity | v0.3 と同一（フレーム正規化自己相関） | dB | 3.0 |
| rms | v0.3 と同一 | dB | 1.0 |
| vibrato_depth | v0.3 と同一（頑健版） | cents | 10 |

- intended 対応: formant_scale → formant_centroid、spectral_tilt → source_tilt。
  breathiness → periodicity、vibrato_depth → vibrato_depth_cents（変更なし）。
- 広帯域 spectral_centroid は特徴量セットから**除外**する（formant_centroid と
  source_tilt に情報が分解されるため。除外は versioned instrument change
  としてレポートに明記）。side 集合 = 6 特徴から intended を除く 5。
- F1_est はケプストラム包絡の最下ピーク。包絡平滑化の lifter 次数等の
  実装自由度は underspec_log_v3.md に記録。

## B. F0 推定器強化（音を戻すのではなく計器を強くする）

R0.1 高音域回帰（MIDI 93/96）の判定: R0.1 の音響は正当（tilt 整合ノイズは
意図どおり）で、露出したのは推定器の短周期域の脆弱性。よってレンダラは
変更せず推定器を強化する。

1. **短ラグ精密化**: YIN 差分関数の最小点に放物線補間を適用（整数ラグ量子化
   誤差の除去。高 F0 ほど効く）。
2. **オクターブ曖昧性解消（スペクトル櫛照合)**: 候補ラグ l に対し
   {l, 2l, l/2} の各候補 f0 で倍音櫛エネルギー（FFT 上の k·f0 ピーク和、
   k=1..5、帯域上限まで）を比較し、最大の候補を採用する。時間領域候補を
   スペクトル証拠で検証する二重化。
3. 探索範囲・カナリア・ゲート数値は v0.3 §E のまま凍結。

gate（VT-2 v3）: **v0 と R0.1 の両レンダラ**で、コア帯域 C2–C7 全ノート
|err| ≤ 100 cents（オクターブ誤り 0）かつ median ≤ 20 cents、カナリア PASS。
旧推定器で通っていた v0 側を落とさないこと（非退行）。

## C. breathiness の gate 到達（残り 0.6 の解消）

手順を固定する:

1. まず A+B 適用後の計測で breathiness 軸を再測（レンダラは R0.1 のまま）。
   特徴量直交化により side 計上が変わるため、これだけで gate 到達の可能性がある。
2. 未達の場合のみ、レンダラ微修正を **1 回だけ** 許可（R0.2）:
   息ノイズのスペクトル整形を「グローバル tilt 一致」から「register 別の
   実効 tilt + 高次減衰に一致」へ精密化する（現行 R0.1 は register 混合前の
   基準 tilt に整合しており、高 register で倍音側と乖離が残る）。
   修正した場合は VT-1 v3 / VT-2 v3 を R0.2 で再実行し全ゲート再確認
   （前サイクルの教訓: レンダラ変更は必ず全ゲートを再通過させる）。
3. それでも未達なら数値と残存 side 内訳を記録して終了（無理に通さない。
   fail-closed の記録が成果物）。

## D. 実験マトリクス（VT v3）

| 構成 | レンダラ | 推定器 | 特徴量 | 目的 |
|---|---|---|---|---|
| (a) | R0.1 | 強化版 | v0.3 旧セット | 推定器強化の単独寄与（caveat セル数の減少を確認） |
| (b) | R0.1 | 強化版 | **v2 新セット** | 本命。4 軸 gate 判定 |
| (c) 条件付き | R0.2 | 強化版 | v2 新セット | §C-2 発動時のみ |

- VT-1 v3: 最終採用レンダラ（R0.1 または R0.2）で 122 ノート plausibility
  再確認（非退行）。
- VT-3 v3 の gate・ref_scale・σ_meas 反復・caveat 規約は v0.3 §A を継承
  （特徴量セットのみ v2 に差替え）。
- 非退行チェックリスト: vibrato_depth 両構成 PASS 維持 / VT-1 PASS 維持 /
  VT-2 カナリア PASS 維持 / v0 レンダラの VT-2 全数 ≤100c 維持。

## E. 成果物

- `measure_v3.py`（特徴量セット v2 + 推定器強化）、必要時 `voice_r0_2.py`
- `vt1_v3.py` / `vt2_v3.py` / `vt3_v3.py`
- `results_v3/{vt1_plausibility_v3,bench_f0_v3,grip_report_v3}.json`
- `results_v3/run_summary_v3.md`（gate 判定表 + 非退行チェックリスト +
  v0.3→v3 の grip 推移表）
- `results_v3/underspec_log_v3.md`
