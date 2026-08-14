# 実行ログ要約 v2 — v0.3 改修設計メモ実装（VT-1/2/3 v2）

全てフォアグラウンド実行（run_in_background / & / nohup 不使用）。例外は
発生していない。合計実行時間 約 36 秒（VT-1 v2: 10.6s / VT-2 v2: 0.9s /
VT-3 v2: 24.8s）。

## ゲート判定表

| VT | 対象 | ゲート条件 | 結果 | 根拠数値 |
|---|---|---|---|---|
| VT-1 v2 | R0.1（正式） | 122ノート中 plausibility violation (r_median<0.35) が 0 | **PASS** | n_violations=0/122 |
| VT-1 v2 | R0 v0（参考） | 同上 | PASS（参考） | n_violations=0/122 |
| VT-2 v2 | R0.1・自前推定器 コア帯域(C2-C7) | canary PASS ∧ オクターブ誤り0(全ノート≤100c) ∧ median≤20c | **FAIL** | canary=PASS(全4点≤30c)／median=8.55c(≤20c 満たす)／**MIDI93,96 で |err| 505.8c, 1203.6c（オクターブ誤り 1 件が閾値600c超）** |
| VT-3 v2 | config1 (R0 v0 + 修復計器 + grip v2) breathiness | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | FAIL | grip=0.845, dir=1.0, E=3.09 |
| VT-3 v2 | config1 formant_scale | 同上 | FAIL | grip=1.699, dir=1.0, E=2.07 |
| VT-3 v2 | config1 spectral_tilt | 同上 | FAIL | grip=0.729, dir=0.75, E=3.23 |
| VT-3 v2 | config1 vibrato_depth | 同上 | **PASS** | grip=4.284, dir=1.0, E=9.35 |
| VT-3 v2 | config2 (R0.1 + 修復計器 + grip v2) breathiness | 同上 | FAIL | grip=2.358, dir=1.0, E=2.62 |
| VT-3 v2 | config2 formant_scale | 同上 | FAIL | grip=1.825, dir=0.8, E=2.69 |
| VT-3 v2 | config2 spectral_tilt | 同上 | FAIL | grip=0.638, dir=1.0, E=10.64 |
| VT-3 v2 | config2 vibrato_depth | 同上 | **PASS** | grip=9.215, dir=1.0, E=9.21 |

VT-2 v2 詳細: core_band n_notes=21, n_octave_errors(|err|>600c)=1(MIDI96のみ。
MIDI93 は 505.8c で 600c 閾値未満だが「全ノード|err|<=100c」条件には抵触),
gate_no_octave_errors_le100c=False, gate_median_le20c=True(median=8.55c),
margin_band(MIDI99)=measured のみ 24.55c（gate対象外）。

## v0.2 測定 → 構成(1) → 構成(2) の grip 3 点比較

| 軸 | v0.2 grip (sweep_wide, z-score) | (1) v0 レンダラ + 修復計器 + grip v2 | (2) R0.1 + 修復計器 + grip v2 | (1)→(2) 差分の解釈 |
|---|---|---|---|---|
| breathiness | 1.025 | 0.845 | **2.358** | レンダラ改修（R0.1のノイズtilt整合）の効果が最大。定義改修だけでは横ばい/微減、レンダラ修正が主要因 |
| formant_scale | 0.571 | 1.699 | 1.825 | 定義改修（z-score→JND効果量）で v0.2比 3倍化。レンダラ改修の追加効果は小さい |
| spectral_tilt | 0.819 | 0.729 | 0.638 | 定義改修でも横ばい、レンダラ改修でむしろ悪化（後述の collinearity 参照） |
| vibrato_depth | 0.682 | 4.284 | **9.215** | 定義改修で v0.2比 6倍化しゲート通過、レンダラ改修でさらに倍増 |

注: v0.2 は z-score 正規化・intended=hnr_db（breathiness軸）、v0.3 は JND
効果量・intended=periodicity（breathiness軸）と定義自体が異なるため、
数値の直接比較は「同じ物差しでの厳密な進歩」ではなく「軸の相対順位・
オーダーの変化」として読むこと。gate 閾値も v0.2(>=3.0のみ)と
v0.3(grip>=3.0 ∧ dir>=0.90 ∧ E>=2.0の3条件)で異なる。

## VT-1 v2: plausibility 不変条件（design memo §D）

- コマンド: `python vt1_v2.py`　実行時間: 10.6秒
- R0.1（正式gate対象）・R0 v0（参考）の両方で voice_A/voice_B × MIDI 36-96
  (122ノート) を検査、**両方とも違反 0 で PASS**。
- v0 側も違反0だった理由: VT-1 v1 で発見した breathiness 縮退（register_gain
  過大による周期性消滅）は既に `voice_r0.py` 自体に緊急パッチ済みだったため
  （0.95クランプが元から入っている）。よってこの v0 は「対策前の生のバグ
  入りv0」ではなく「その場しのぎのクランプ適用済みv0」であることに注意。
  §D の不変条件が「検出力を持つか」の実証は、この 122 ノート全数計測が
  非有限値検査だけでは見えない縮退（r_median<0.35）を独立に測っていること
  自体で担保されている（クランプが外れれば即座に検出できる構造）。
- 出力: `results_v2/vt1_plausibility.json`

## VT-2 v2: Phase 0 ゲート数値凍結（design memo §E）

- コマンド: `python vt2_v2.py`　実行時間: 0.9秒
- 対象: voice_A・R0.1、MIDI 36-99（22ノート、3半音刻み）、探索上限
  fmax=2900Hz（>=2800Hz要件を満たす）
- **カナリア: PASS**（4点の独立純音、全て30cent以内。ハーネス自体の
  真値配列・比較ロジックは健全と確認）
- **コア帯域(C2-C7) gate: FAIL**。median|err|=8.55cent（<=20cent条件は満たす）
  だが、MIDI93(B6付近, true=1760Hz)で -505.8cent、MIDI96(C7, true=2093Hz)で
  -1203.6cent の大誤差が発生し「全ノード|err|<=100cent」条件に抵触。
- **重要な新規発見**: この2ノートの誤差は v0.2/v1 の bench（v0レンダラ、
  同一fmin/fmax設定）では発生していなかった（v1実測: MIDI93 err=+41.0cent、
  MIDI96 err=-11.3cent、いずれも50cent以内）。診断的に同一Genome・同一
  measure.pyでレンダラのみv0とR0.1を切り替えて比較したところ、v0では
  MIDI93/96とも誤差50cent以内を維持する一方、R0.1では同じ2ノードで
  600cent超のオクターブ誤りに転落することを確認した。fmax(2200〜3200で
  スイープ)の影響ではなくレンダラ変更そのものが原因と特定した。
  §C-1のノイズtilt整合修正はbreathiness→centroid entanglementの低減には
  成功している（C3でcentroid変化+65.3%→-7.3%に改善）が、副作用として
  高音域（短周期・自前推定器の探索境界近傍）での周期構造を変化させ、
  もともと脆弱だった領域（v0.2 VT-2でも要注意帯だった）の推定精度を悪化
  させたと考えられる。この因果の特定と対策は本メモのスコープ外のため、
  実測結果のみを記録し次サイクルへ申し送る（underspec_log_v2.md参照）。
- margin帯（MIDI99, D#7）は measured のみ: 24.55cent（gate対象外）。
- 出力: `results_v2/bench_f0_v2.json`

## VT-3 v2: Grip Matrix v2（design memo §A, §F）

- コマンド: `python vt3_v2.py`　実行時間: 24.8秒
- 2構成 × 4軸 = 8セルを測定。**vibrato_depth軸のみ両構成でPASS**、他3軸
  （breathiness/formant_scale/spectral_tilt）は両構成ともFAIL。
- **breathiness軸はレンダラ改修（R0.1）で最も改善**（grip 0.845→2.358、
  1.8pt増）。それでもgate閾値3.0には届かない。E(intended)=2.62は
  E>=2.0条件を満たしており「効いてはいるが支配的ではない」段階。
- **formant_scale軸は定義改修（z-score→JND効果量）で最も改善**
  （v0.2の0.571から一気に1.7〜1.8へ）。レンダラ改修の追加効果は限定的。
- **spectral_tilt軸はレンダラ改修でむしろ悪化**（0.729→0.638）。両構成で
  dominant_side_featureが一貫してspectral_centroidであり、formant_scale
  軸でもdominant_sideが一貫してspectral_tiltになっている。これは
  「tiltを変えればcentroidも動く」「formantを動かせば回帰計測されるtiltも
  動く」という測定上の構造的共線性であり、計器アーティファクト（§B）とも
  純粋な物理干渉とも言い切れない中間的な現象として記録した
  （underspec_log_v2.md参照、次サイクルの特徴量再設計への申し送り）。
- 計器分解能開示（§A-4）: 全8セルで`any_below_instrument_resolution=False`
  （intended効果はいずれも3σ_meas以上動いており、測定ノイズで説明できる
  範囲を超えている）。ただしcaveatセル数（オクターブ跳躍系外れ値棄却率
  >5%）はconfig2(R0.1)の方がconfig1(v0)より一貫して多い
  （breathiness 3→6、formant_scale 1→3、spectral_tilt 0→4、
  vibrato_depth 2→6）。これはVT-2 v2で発見したR0.1高音域回帰と整合する
  傾向であり、R0.1が計測の頑健性を全体的にやや低下させていることを
  示唆する。
- 出力: `results_v2/grip_report_v2.json`

## 総括（1行結果）

- VT-1 v2: **PASS**（R0.1正式gate、v0参考も含め122/122ノードで
  plausibility違反0、10.6秒）
- VT-2 v2: **FAIL**（canaryはPASSだがコア帯域でMIDI93/96がオクターブ誤り、
  R0.1のノイズtilt整合修正が高音域推定の新規回帰を引き起こしたことを特定）
- VT-3 v2: **4軸中1軸のみPASS**（vibrato_depthのみ両構成でgate通過。
  breathinessはR0.1改修で0.845→2.358まで改善したがgate未達。
  spectral_tilt/formant_scaleは特徴量間の構造的共線性が残存）
