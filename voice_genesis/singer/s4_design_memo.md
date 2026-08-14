# S4 設計メモ — gate6 安全域の拡張（score-informed QC 計測）と第三の歌手の再鍛造

背景: S3b 耳判定（`results_s3b/s3b_ear_record.md`）で複合 0.9 JND ≈ 知覚
境界と較正された。明確な第三の歌手（床 2.0）には gate6 安全域の拡張が必要。
S2/S3 の実測より、安全域を縛る主因は**計測経路の脆弱性**:
(1) GAIN_FLOOR × formant_scale の二値トレードオフ（U1 診断）、
(2) 高 breathiness/vibrato での F0 トラック劣化が vibrato_depth 計測へ
クロストークする（voice_B gate6 未達と同根）。

## W1. 原理: gate6 を score-informed QC に再設計する

gate6 は「レンダラが Genome の命令に従っているか」の製造検査（QC）であり、
ブラインド分析ではない。**検査者は命令値（楽譜 F0・commanded vibrato）を
知ってよい** — ブラインド推定の性能はVT-2（Phase 0 bench）が別途保証して
いる。この分離を明文化し、gate6 の計測を score-informed 化する:

1. **periodicity**: フレーム毎の探索ではなく **commanded F0（ビブラート
   込みの命令 F0 軌跡）から導出したラグ**で正規化自己相関を評価する。
   F0 推定の誤りが periodicity 計測へ混入する経路を根絶
2. **vibrato_depth**: F0 トラックを commanded F0 の ±200 cents 窓内に
   制限して追跡（窓外れフレームは NaN 棄却・棄却率記録）。ノイズ起因の
   オクターブ跳びが深度計測を汚染する経路を遮断
3. 実装は `gate_checks.py` 無改変、`gate_checks_v2.py` 新設（gate1-5 は
   従来のまま。gate6 のみ v2 計測へ差替えた `full_gates_v2` を定義）。
   **命名と report で score-informed であることを必ず宣言**（provenance:
   `measured (score-informed QC)`）

## W2. GAIN_FLOOR の適応化

U1 診断の二値トレードオフ（floor 高→periodicity 崩壊 / 低→オクターブ誤り）
は、score-informed 計測（W1）でオクターブ誤り側の懸念が消えるため解消される
見込み。よって:

1. まず W1 適用後に GAIN_FLOOR を従来値のまま再走査し、それだけで
   formant_scale 安全域が開くか確認
2. 開かない場合のみ GAIN_FLOOR の適応化（commanded F0 帯域を避けて floor を
   置く等）を実装。レンダラ変更になるため gate1-5 の非退行（voice_A/C/D）を
   必ず再確認

## W3. 安全域の再走査

W1（+必要なら W2）適用後、gate6-v2 で安全域ボックスを再測する:
- 走査軸: formant_scale [0.85, 1.20] / tilt [-18, -5] / bandwidth_scale
  [0.7, 1.4] / breathiness_base [0, 0.5] / register_gains 上限 / vibrato
- 1 次元走査 + 「有望コーナーの 2-3 点の多次元スポットチェック」
  （S3 の教訓: 1 次元安全でも多次元で崖を踏む）
- **voice_B の gate6 再測**も行う（S1 の未達が v2 計測で解消するか =
  クロストーク仮説の直接検証）
- 新安全域ボックスを表として凍結

## W4. 第三の歌手の再鍛造

新ボックスで genesis_v1（多世代探索、G=4、床 2.0、規約は S3b と同一)を
再実行。当選者（または fail-closed 最良個体）にフル gate1-5 + gate6-v2 を
適用し、`sakura_genesis3.wav` を生成。系譜・再現性照合も従来どおり。

## W5. 成果物

- `singer/gate_checks_v2.py`（+必要なら formant_tv.py の適応 floor —
  既存ファイルは無改変で v2 派生を新設）
- `singer/results_s4/{safe_box_v2.md, genesis_report_c.md,
  sakura_genesis3.wav, lineage_genesis3.json, underspec_log_s4.md}`
- voice_B 再測結果と gate1-5 非退行表を genesis_report_c.md に含める
- 実行様式: 従来どおり（読み取り専用リポジトリ・singer/ 配下のみ・
  フォアグラウンド・決定論・15 分規模まで）
