# 実行ログ要約 — UGH Voice Genesis Engine v0.2 仮想テスト

全てフォアグラウンド実行（run_in_background / & / nohup 不使用）。例外は
発生していない（各スクリプトが正常終了、非有限値・クラッシュなし）。

## VT-1: R0 Diagnostic Synth 仕様準拠再実装

- コマンド: `python vt1_check.py`
- 実行時間: 1.9 秒
- 対象: voice_A / voice_B、MIDI 36-96（C2-C7）全 61 半音
- 結果: **PASS** — 全 122 ノート（2 声 × 61 音）で非有限値なし・無音なし・
  クリップなし
- 出力: `results/vt1_check.json`、代表 6 音 × 2 声のサンプル WAV
  `results/sample_wav/`
- 補足: 実装過程で `breathiness` の register 別ゲイン初期値が過大
  （register 項単独で 1.0 超）となり、高音域でノイズが倍音を凌駕して
  周期性が事実上消滅する縮退を発見・修正した（詳細は underspec_log.md
  #6）。VT-1 のクリップ/無音チェックはこの種の縮退は検出しない
  （振幅としては正常範囲内のため）ことに注意 — 実際に発見できたのは
  VT-2 の F0 推定を通してだった。

## VT-2: Phase 0 Measurement Bench ゲート模擬

- コマンド: `python vt2_bench.py`
- 実行時間: 50.8 秒（librosa.pyin ×2 帯域 ×21 ノートが主要因）
- 対象: voice_A、MIDI 36,39,...,96（21 ノート、3 半音刻み）
- 結果: **参考データ取得完了**（"許容誤差内" の閾値自体が設計書に
  数値指定なしのため pass/fail は閾値ごとの参考表として記録）

  | 推定器 | median\|cents_err\| | max\|cents_err\| | ≤20c | ≤50c | ≤100c (/21) |
  |---|---|---|---|---|---|
  | 自前 (時間領域 YIN 式) | 8.7 | 50.4 | 17 | 20 | 21 |
  | librosa.pyin fmin=50/fmax=1000 | 15.0 | 1895.0 | 13 | 15 | 16 |
  | librosa.pyin fmin=60/fmax=2200 | 10.6 | 1209.4 | 13 | 19 | 20 |

  - **帯域限定計器の失敗モード実証（設計書 §9 の主張の再現に成功）**:
    fmin=50/fmax=1000 の pyin は MIDI 84（C6, 1046.5Hz）で fmax を超えた
    瞬間に誤差が -1195 cents（ほぼ1オクターブ）に跳躍し、以降 C7 まで
    全て誤差 1000+ cents で破綻したまま回復しない。ただし失敗の様相は
    「NaN・unvoiced 判定」ではなく「自信ありげな誤った周波数を返し続ける」
    形で現れた（`n_nan_or_unvoiced=0`）——これは §9 が想定していたであろう
    「検出不能」ではなく「サイレントな誤検出」であり、実運用上はより
    危険な失敗モードである。
  - 全帯域 pyin (60-2200Hz) も MIDI 96（C7, 2093Hz≈fmax）付近で
    -1209 cents の破綻を1点起こしており、既存分析系は「範囲を広げれば
    済む」わけではなく、fmax 境界近傍そのものが弱点であることが分かった。
  - 自前推定器は全 21 ノートで 100 cents 以内に収まり、50 cents 以内でも
    20/21（95%）——実装過程での複数回の再設計（underspec_log.md #13）を
    経て、既存の帯域限定計器より一貫して安定した。ただし「決定論的に
    ゼロ誤差」ではなく、依然として数十 cents の系統誤差が残る。
- 出力: `results/bench_f0.json`

## VT-3: Grip Matrix well-posedness 検証

- コマンド: `python vt3_grip.py`
- 実行時間: 5.6 秒
- 対象: voice_A、probe suite {C3,E4,A4,C5,C6}、4 軸 × 5 sweep 点
- 結果: **4 軸すべてが grip_ratio>=3.0 かつ方向一致率>=90% のゲートを
  通過しなかった**（sweep_wide 定義）

  | 軸 | intended feature | grip_ratio (sweep_wide) | grip_ratio (per_note) | 方向一致率 | ゲート |
  |---|---|---|---|---|---|
  | breathiness | hnr_db | 1.025 | 0.874 | 80% | FAIL |
  | formant_scale | spectral_centroid | 0.571 | 1.385 | 100% | FAIL |
  | spectral_tilt | spectral_tilt_db_per_oct | 0.819 | 0.582 | 75% | FAIL |
  | vibrato_depth | vibrato_depth_cents | 0.682 | 0.978 | 68%\* | FAIL |

  \* vibrato_depth の direction_consistency はレポートでは 1.0 だが、
  raw_feature_matrices に外れ値（後述）を含むため過大評価の可能性あり。

  - **z-score 母集団定義への依存性は実測で確認された**（underspec_log.md
    #14）: 軸によって `sweep_wide` と `per_note` の grip_ratio が 2 倍以上
    異なる（例: formant_scale は 0.57 → 1.39、spectral_tilt は
    0.82 → 0.58）。定義を変えても gate 通過には至らないが、順位や
    「あとどれだけで通過か」の印象は大きく変わる。
  - **side-effect の支配特徴はほぼ全軸で `hnr_db`**。これが物理的な
    軸間干渉（設計書が最初から懸念していたもの）なのか、HNR 近似の
    計器アーティファクト（フォルマント/tilt が動くと harmonic-band の
    切り出し自体がずれる）なのかは本 VT だけでは切り分けられない
    （underspec_log.md 末尾を参照）。
  - vibrato_depth 軸では、C3（最低音）× 高 vibrato_depth sweep 点で
    自前推定器が周期的にオクターブ誤りに近い外れ値（約 3 倍音、
    748.79 cents 相当の std）を出し、この軸の grip 計測自体の信頼性を
    損なっている。低音域 × 大深度 vibrato は現状の計器では
    instrument-validity caveat 付きの measured として扱うべき。
- 出力: `results/grip_report.json`（軸ごとの生特徴行列・両定義の
  grip_ratio・方向一致率を含む）

## 総括（1行結果）

- VT-1: **PASS** — voice_A/voice_B、C2-C7 全 61 半音で破綻なく生成（1.9秒）
- VT-2: **参考データ取得完了** — 自前推定器 max 50.4 cents / 帯域限定
  pyin は C6 超で最大 1895 cents 破綻（§9 の主張どおりの失敗を再現、
  ただし失敗様相はサイレントな誤検出）
- VT-3: **4/4 軸で grip ゲート未通過**（grip_ratio 0.57-1.03、gate 閾値
  3.0 に遠く届かず）— z-score 定義依存性を実測、side-effect は HNR に
  集中（物理的干渉か計器アーティファクトかは要追加検証）
