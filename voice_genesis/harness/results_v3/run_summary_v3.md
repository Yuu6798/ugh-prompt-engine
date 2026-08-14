# 実行ログ要約 v3 — v0.3.1 改修設計メモ実装（第 3 サイクル、VT-1/2/3 v3）

全てフォアグラウンド実行（run_in_background / & / nohup 不使用）。例外なし。
合計実行時間 約 41 秒（VT-1 v3: 10.6s / VT-2 v3: 1.7s / VT-3 v3: 28.5s）。

**§C 手順の結果: R0.2 は発動しなかった。** §A（特徴量セット v2）+ §B
（推定器強化）適用後の構成 (b) で breathiness 軸が単独で gate 到達
（grip_ratio=3.170 ≥ 3.0）したため、§C-2 のレンダラ微修正は不要と判定した。
よって `voice_r0_2.py` は作成していない（作成不要のため未作成であり、
省略ではない旨をここに明記する）。

## ゲート判定表

| VT | 対象 | ゲート条件 | 結果 | 根拠数値 |
|---|---|---|---|---|
| VT-1 v3 | R0.1（正式・最終採用レンダラ） | 122ノード中 plausibility violation (r_median<0.35) が 0 | **PASS** | n_violations=0/122 |
| VT-1 v3 | R0 v0（参考） | 同上 | PASS（参考） | n_violations=0/122 |
| VT-2 v3 | R0.1・強化推定器 コア帯域(C2-C7) | 全ノード\|err\|≤100c(オクターブ誤り0) ∧ median≤20c | **PASS** | n_octave_errors=0, max=41.46c, median=8.55c |
| VT-2 v3 | v0・強化推定器 コア帯域(C2-C7) | 同上（非退行必須） | **PASS** | n_octave_errors=0, max=56.21c, median=8.68c |
| VT-2 v3 | カナリア（4点純音） | 全点 ≤30c | **PASS** | 最大誤差14.08c(82.4Hz) |
| VT-2 v3 | 総合 | カナリアPASS ∧ v0コアPASS ∧ R0.1コアPASS | **PASS** | — |
| VT-3 v3 | config(a) R0.1+強化推定器+旧特徴セット breathiness | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | FAIL | grip=2.858, dir=1.0, E=3.170 |
| VT-3 v3 | config(a) formant_scale | 同上 | FAIL | grip=1.825, dir=0.8, E=2.692 |
| VT-3 v3 | config(a) spectral_tilt | 同上 | FAIL | grip=0.638, dir=1.0, E=10.630 |
| VT-3 v3 | config(a) vibrato_depth | 同上 | **PASS** | grip=9.215, dir=1.0, E=9.215 |
| VT-3 v3 | config(b) R0.1+強化推定器+v2新特徴セット breathiness | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | **PASS** | grip=3.170, dir=1.0, E=3.170 |
| VT-3 v3 | config(b) formant_scale | 同上 | FAIL | grip=2.217, dir=0.75, E=5.539 |
| VT-3 v3 | config(b) spectral_tilt | 同上 | FAIL | grip=1.068, dir=1.0, E=8.082 |
| VT-3 v3 | config(b) vibrato_depth | 同上 | **PASS** | grip=9.215, dir=1.0, E=9.215 |
| VT-3 v3 | config(c) R0.2 | §C-2 発動時のみ | **対象外**（未発動） | breathinessがconfig(b)で既にPASSしたため |

## 非退行チェックリスト（design memo v031 §D）

| 項目 | v0.3 時点 | v3 実測 | 判定 |
|---|---|---|---|
| vibrato_depth 両構成 PASS 維持 | v0.3 config1/2 とも PASS(grip 4.28/9.21) | config(a)/(b) とも PASS(grip 9.21/9.21) | **維持** |
| VT-1 PASS 維持 | v0.3: R0.1/v0 とも0/122違反 | v3: R0.1/v0 とも0/122違反 | **維持** |
| VT-2 カナリア PASS 維持 | v0.3: 4点とも≤30c | v3: 4点とも≤30c（最大14.08c） | **維持** |
| v0 レンダラの VT-2 全数 ≤100c 維持 | v0.3: v0はMIDI93/96含め全て≤100c（当時の焦点はR0.1側の回帰） | v3: v0 全21ノード≤100c（max=56.21c） | **維持** |
| （新規解消）R0.1 の VT-2 全数 ≤100c | v0.3: **FAIL**（MIDI93 -505.8c, MIDI96 -1203.6c） | v3: **PASS**（max=41.46c、オクターブ誤り0） | **回帰を解消** |

非退行チェックリストは全項目 PASS。加えて v0.3 で唯一残っていた既知回帰
（R0.1 高音域のオクターブ誤り）を本サイクルで解消した。

## v0.2 → v0.3 → v3 の grip 推移表

| 軸 | v0.2 (z-score) | v0.3 config2 (R0.1+repaired+grip v2) | v3 config(a) (R0.1+強化推定器+旧特徴) | v3 config(b) (R0.1+強化推定器+v2新特徴) | 判定 |
|---|---|---|---|---|---|
| breathiness | 1.025 | 2.358 | 2.858 | **3.170 (PASS)** | 3サイクルかけてgate到達 |
| formant_scale | 0.571 | 1.825 | 1.825 | 2.217 | 特徴量分解で改善したがgate未達 |
| spectral_tilt | 0.819 | 0.638 | 0.638 | 1.068 | 特徴量分解で改善したがgate未達（旧来最下位から脱するも道半ば） |
| vibrato_depth | 0.682 | 9.215 | 9.215 | 9.215 | 一貫してPASS（v0.3以降変動なし） |

読み方の注記:
- v0.3→v3 config(a) は「推定器強化の単独寄与」を見る列（特徴量セットは
  旧のまま）。breathinessの2.358→2.858の上昇（+0.5pt）がその寄与にあたる
  （measure_v2の周期性/vibrato計算は元々F0系列に依存するため、推定器強化
  で高音域回帰が解消したことがbreathiness・formant_scale等にも波及した）。
- v3 config(a)→config(b) は「特徴量セット再設計の単独寄与」を見る列。
  breathinessは2.858→3.170（+0.31pt、gate到達）、formant_scaleは
  1.825→2.217（+0.39pt）、spectral_tiltは0.638→1.068（+0.43pt）と、
  3軸とも定義変更（formant_centroid / source_tilt への分解）で改善方向。
  ただしformant_scale・spectral_tiltはgate閾値3.0にはまだ届いていない。
- vibrato_depthはintended特徴が全構成で同一（vibrato_depth_cents）かつ
  side特徴の入れ替え（spectral_centroid⇔formant_centroid等）の影響を
  受けなかったため、v0.3以降完全に横ばい。

## VT-1 v3 詳細

- コマンド: `python vt1_v3.py`　実行時間: 10.6秒
- 最終採用レンダラ R0.1（§C-2非発動のためv0.3から変更なし）で
  voice_A/voice_B × MIDI 36-96（122ノード）を強化推定器ベースの
  periodicity（measure_v3.periodicity_track_v3）で再検査、**違反0でPASS**。
  v0参考も0違反。非退行を確認。
- 出力: `results_v3/vt1_plausibility_v3.json`

## VT-2 v3 詳細

- コマンド: `python vt2_v3.py`　実行時間: 1.7秒
- 対象: voice_A、MIDI 36-99（22ノード）、探索範囲 fmin=55/fmax=2900Hz
  （§E要件 fmax≥2800Hz を満たす）、**v0とR0.1の両レンダラ**で実施。
- **カナリア PASS**（4点純音、全て30cent以内、最大14.08cent@82.4Hz）。
- **R0.1コア帯域(C2-C7) gate PASS**: オクターブ誤り0、
  max\|err\|=41.46cent（MIDI66）、median\|err\|=8.55cent。
  v0.3では同じ帯域でMIDI93(-505.8c)・MIDI96(-1203.6c)のオクターブ誤りが
  発生していたが、**§B推定器強化（放物線補間は既存流用、スペクトル櫛
  照合を新規実装）により両ノードとも100cent以内に収まった**
  （MIDI93: 14.59c, MIDI96: -9.27c）。
- **v0コア帯域 gate PASS**（非退行）: オクターブ誤り0、max=56.21cent
  （MIDI81）、median=8.68cent。v0.3時点でも既にv0側は問題なかったため、
  推定器強化後もこれを維持していることを確認した。
- margin帯（MIDI99, D#7）は両レンダラともmeasuredのみ記録
  （R0.1: 24.55〜31.14cent、v0: 65.6〜84.6cent、gate対象外）。
- 出力: `results_v3/bench_f0_v3.json`

## VT-3 v3 詳細

- コマンド: `python vt3_v3.py`　実行時間: 28.5秒
- config(a)（推定器強化の単独寄与、旧特徴セット）: vibrato_depthのみPASS。
  caveatセル数は全軸で0/25（v0.3 config2では2〜6/25あった）——**推定器強化
  によりオクターブ跳躍系の外れ値棄却率が全面的に改善したことを確認**
  （config(b)でも同様に全軸0/25）。
- config(b)（本命、v2新特徴セット）: **breathiness軸がgate PASS**
  （grip=3.170, dir=1.0, E=3.170、全条件クリア）。§C手順のステップ1
  （A+B適用後の再測）だけでgateに到達したため、§C-2（R0.2）は不要と判定。
  formant_scale（grip=2.217）・spectral_tilt（grip=1.068）は未達のまま
  残った。
- **共線性の構造的解消の効果は部分的**: formant_scale軸のdominant側特徴が
  v0.3の`spectral_centroid`→v3の`source_tilt`に、spectral_tilt軸のdominant
  側特徴が同じくv0.3の`spectral_centroid`→v3の`formant_centroid`に変わった
  （つまり以前の「centroidとtiltが絡み合う」問題そのものは解消された）が、
  代わりに**新設のformant_centroidとsource_tiltが互いに支配的な副作用源に
  なる新しい共線性**が生じている。声道由来の2特徴（フォルマント位置と
  声源傾斜）が測定上まだ十分に直交していないことを示しており、次サイクル
  への課題として記録する。
- **新規に観測された副作用**: breathiness軸（config b）のdominant_side_
  featureが`mean_f0`になった（v0.3では`spectral_centroid`だった）。
  breathinessを上げるとF0推定にも軽微な影響が出ている可能性があり
  （E(mean_f0)自体はgate閾値未満で実害はないが）、今後breathinessを
  さらに追い込む場合は注視が必要。
- 計器分解能開示: 全8セル(config a+b)で`any_below_instrument_resolution`
  =False（intended効果はいずれも測定ノイズで説明できる範囲を超えて動いて
  いる）。caveatセルは前述の通り全軸0/25で、v0.3からの明確な改善。
- 出力: `results_v3/grip_report_v3.json`

## 総括（1行結果）

- VT-1 v3: **PASS**（R0.1正式gate、v0参考含め122/122ノードでplausibility
  違反0、非退行確認、10.6秒）
- VT-2 v3: **PASS**（v0・R0.1の両レンダラでコア帯域全数≤100c・median≤20c・
  カナリアPASS。v0.3で唯一残っていたR0.1高音域オクターブ誤り(MIDI93/96)を
  推定器強化により解消、1.7秒）
- VT-3 v3: **4軸中2軸PASS**（breathinessが本サイクルでgate到達
  =grip3.170、vibrato_depthは維持=grip9.215。formant_scale/spectral_tiltは
  特徴量分解で改善したが未達のまま残存、次サイクル申し送り、28.5秒）
