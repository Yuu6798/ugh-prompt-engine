# v0.3 改修設計メモ — grip 再定義・計器修復・R0.1・ゲート数値凍結

対象: UGH Voice Genesis Engine v0.2 の仮想テストで実証された欠陥の改修仕様。
本メモが再テスト（VT v2）の唯一の仕様正本。実装は本メモに従い、逸脱・補充が
必要になった箇所は underspec_log_v2.md に記録する。

## A. grip v2 — JND 参照効果量による再定義（§7.2 差替え）

v0.2 の z-score 正規化は「単調な応答をスケール不問で同一 z 幅に引き伸ばす」
構造欠陥により廃止。代わりに **物理単位に係留した効果量** を用いる。

### A-1. 特徴量と参照スケール表（凍結定数 v0.3-VT）

各特徴量に、心理音響的に「1 単位の意味ある変化」とみなす参照スケールを宣言する。

| feature | 単位 | ref_scale | 根拠（概算 JND） |
|---|---|---|---|
| mean_f0 | cents | 25 | 音高弁別閾の安全側 |
| spectral_centroid | octave (log2 Hz) | 0.10 | 明るさ変化の可聴閾概算 |
| spectral_tilt | dB/oct | 1.5 | 音色傾斜の可聴変化 |
| periodicity | dB | 3.0 | 息っぽさの可聴変化 |
| rms | dB | 1.0 | ラウドネス JND |
| vibrato_depth | cents | 10 | 変調深度の可聴変化 |

この表は gate 仕様の一部として凍結。改訂は evidence 付きでのみ可（v0.3 勧告 2 の
「数値改訂手続き」と同型）。**centroid は Hz でなく log2 Hz で測る**。

### A-2. 効果量と grip

- probe suite: {C3, E4, A4, C5, C6}（v0.2 と同一）
- 軸ごとに sweep 5 点（v0.2 と同一の値域）
- probe ノートごとの効果量: `E_note(f) = |f(sweep_max) - f(sweep_min)| / ref_scale(f)`
- 集約: `E(f) = median over probe notes of E_note(f)`（外れ probe に頑健）
- **side 特徴集合の凍結**: 全 6 特徴から intended を除いた 5 特徴。軸ごとに
  レポートへ明記（集合が動くと gate の意味が変わるため）
- `grip(axis) = E(intended) / max( max_j E(side_j), 1.0 )`
  分母の床 1.0 = 「JND 未満の副作用は支配性判定に算入しない」

### A-3. gate（凍結数値）

1. `grip >= 3.0`
2. 方向一致率 `>= 0.90`（下記 A-4 の頑健測定に基づく。probe ノートごとに
   連続 sweep 点間の intended 符号一致を数え、全 probe プール）
3. `E(intended) >= 2.0`（意図効果それ自体が JND の 2 倍以上動くこと。
   効かないツマミが「副作用も無いから合格」する抜け道を塞ぐ）

### A-4. 計器分解能開示（instrument-validity caveat の grip への適用）

- 各 probe ノート × 中央 sweep 点で、Genome 固定・jitter/noise seed のみ変えた
  **3 反復レンダ**から特徴ごとの再現性ノイズ σ_meas を推定する。
- `|ΔF| < 3σ_meas` の intended 効果は `below_instrument_resolution: true` を
  付して報告（gate 判定には使うが caveat を必ず併記）。
- F0 トラックにオクターブ跳躍外れ値が検出されたセルは
  `measured_with_caveat: true` を付す。

## B. 計器修復（VT-3 で発見された 2 アーティファクトの除去）

1. **periodicity（HNR 近似の差替え）**: 固定倍音帯域窓の HNR は
   フォルマント/tilt/vibrato の変化で帯域切り出し自体が動く計器
   アーティファクトを持つ。フレーム毎の正規化自己相関に差し替える:
   フレーム毎に YIN 式で周期推定 → その lag での正規化自己相関 r →
   `periodicity_db = 10*log10( r / (1 - r) )`（r は [0.01, 0.99] にクランプ）→
   ノート代表値はフレーム median。vibrato のような遅い F0 変調に頑健。
2. **vibrato_depth の頑健化**: フレーム F0 系列から、系列 median に対し
   ±600 cents を超えるフレームを外れ値として棄却（棄却率もレポート）。
   深度 = 受理フレームの cents 系列の頑健 std（MAD × 1.4826）。

## C. レンダラ改修 R0.1（VT で実証された実欠陥の修正）

1. **tilt 整合ノイズ**: 息ノイズに倍音源と同じ spectral tilt + 高次減衰
   フィルタを（フォルマント包絡の前に）適用する。v0 ではノイズが倍音より
   平坦なスペクトルを持ち、breathiness ノブが spectral centroid を大きく
   動かす実 entanglement を作っていた（C3 で centroid +65% を実測）。
2. **breathiness 上限の仕様化**: 応答関数合成後の breathiness は 0.95 で
   クランプ（VT-1 で発見した周期性消滅縮退の恒久対策。v0 の暫定クランプを
   正式仕様に昇格）。
3. その他のパラメータ（register 境界・フォルマント値等）は v0 のまま凍結
   （比較可能性のため）。

## D. plausibility 不変条件（§7.4 の運用化、VT-1 v2）

全ノート検査に追加: フレーム periodicity ratio r の median が
**r >= 0.35** を下回るノートは plausibility violation としてフラグ。
（VT-1 v1 の無音・クリップ検査はこの縮退を検出できなかった実証に基づく）
voice_A / voice_B × MIDI 36–96 の 122 ノートで違反 0 が gate。

## E. Phase 0 ゲート数値凍結（VT-2 v2）

- 検証帯域: **MIDI 36–99**（C2 〜 D#7 = 運用上端 C7 + 3 半音マージン）。
  自前推定器の探索上限は 2800 Hz 以上に設計する。
- gate（コア帯域 C2–C7）:
  1. オクターブ誤り 0（全ノート |err| <= 100 cents）
  2. median |err| <= 20 cents
  3. マージン帯（C7 超〜D#7）も measured として記録（gate はコア帯域のみ、
     マージン帯は境界余裕の evidence）
- **カナリア要件**: bench 実行のたびに合成 GT（真 F0 既知）を必ず含め、
  推定器の主張と GT の乖離を機械照合する（サイレント破綻対策）。

## F. 実験マトリクス（VT-3 v2）

計器修復と grip 再定義の寄与を分離するため 2 構成で測る:

| 構成 | レンダラ | 計器 | grip 定義 |
|---|---|---|---|
| (1) baseline再測 | R0 (v0) | 修復済み計器 | grip v2 |
| (2) 改修後 | R0.1 | 修復済み計器 | grip v2 |

4 軸（breathiness / formant_scale / spectral_tilt / vibrato_depth）×両構成の
grip・方向一致率・E(intended)・side 内訳・caveat フラグを記録。
(1)→(2) の差分が「レンダラ改修の効果」、v0.2 結果→(1) の差分が
「定義・計器改修の効果」として読める。

## G. 成果物

- `voice_r0_1.py`（R0.1）、`measure_v2.py`、`vt1_v2.py`、`vt2_v2.py`、`vt3_v2.py`
- `results_v2/vt1_plausibility.json` / `bench_f0_v2.json` / `grip_report_v2.json`
- `results_v2/run_summary_v2.md`（gate 判定表つき）
- `results_v2/underspec_log_v2.md`（本メモで決めきれていなかった箇所の記録)
