# 実行ログ要約 v4 — v0.4 改修設計メモ実装（第 4 サイクル、VT-3 v4）

フォアグラウンド実行（run_in_background / & / nohup 不使用）。例外なし。
VT-3 v4 実行時間 約 17.3 秒。レンダラ・F0 推定器は v3 から変更していないため
（memo v04 前文の指示どおり）、VT-1/VT-2 は**再実行せず v3 の結果を再掲**する。

## ゲート判定表

| VT | 対象 | ゲート条件 | 結果 | 根拠数値 |
|---|---|---|---|---|
| VT-1（再掲、v3 実測を継承） | R0.1 | 122ノード中違反0 | PASS（再掲） | v3実測: n_violations=0/122 |
| VT-2（再掲、v3 実測を継承） | v0・R0.1・カナリア | 全条件 | PASS（再掲） | v3実測: 両レンダラ核帯域gate PASS、カナリアPASS |
| VT-3 v4 | breathiness（免除表適用前） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | **PASS** | grip=3.170, dir=1.0, E=3.170 |
| VT-3 v4 | formant_scale（免除表適用前） | 同上 | FAIL | grip=1.738, dir=0.75, E=3.367 |
| VT-3 v4 | formant_scale（免除表適用後） | grip_declared≥3.0 ∧ 宣言側 sign 5/5 ∧ E(declared)≤0.5×E(intended) ∧ dir≥0.90 ∧ E≥2.0 | **FAIL**（従属条件のみ不成立） | grip_declared=3.367(≥3.0 OK), sign=5/5(OK), E(declared)=1.937 > 0.5×3.367=1.684(NG) |
| VT-3 v4 | spectral_tilt（免除表適用前） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | FAIL | grip=0.574, dir=0.95, E=10.471 |
| VT-3 v4 | spectral_tilt（免除表適用後） | 同上（案B） | **FAIL**（二重に不成立） | grip_declared=9.459(OK), sign=4/5=80%(NG, <5/5要件), E(declared)=18.240 > 0.5×10.471=5.236(NG) |
| VT-3 v4 | vibrato_depth（免除表適用前） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | **PASS** | grip=9.215, dir=1.0, E=9.215 |

**総合: 4 軸中 2 軸 PASS（breathiness, vibrato_depth）。formant_scale /
spectral_tilt は未達のまま終了（memo §C 指示どおり、無理に通さず記録）。**

## 免除表（design memo v04 §B）

| 軸 | 宣言 side | expected_sign | 符号一致 | E(declared) | 0.5×E(intended) | 判定 | 機序説明 |
|---|---|---|---|---|---|---|---|
| formant_scale | source_tilt | +1 | 5/5 (100%) | 1.937 | 1.684 | **NG**（1.937 > 1.684、比57.5%で超過） | formant_scale はフォルマント周波数と共に励振帯域の実効エネルギー分布も変えるため、joint fit の tilt 項（声道非依存の声源傾斜）がわずかに追従して動く（声道-声源の残存結合） |
| spectral_tilt | formant_centroid | +1 | 4/5 (80%) | 18.240 | 5.236 | **NG**（符号一致も比も不成立。E(declared)はE(intended)自体より大きい） | spectral_tilt はスペクトル全体の明るさを変えるため、ケプストラム包絡由来の初期ピーク検出や1ピークモデルへのモデル選択率が変化し、formant_centroidの代表点がわずかに移動する |

免除表は「1軸1エントリ」を厳守し、gate を通過済みの breathiness /
vibrato_depth には宣言を付けていない。

## 非退行チェックリスト

| 項目 | v3 時点 | v4 実測 | 判定 |
|---|---|---|---|
| breathiness PASS 維持 | grip=3.170 PASS | grip=3.170 PASS（intended=periodicityで特徴定義不変のため数値も完全一致） | **維持** |
| vibrato_depth PASS 維持 | grip=9.215 PASS | grip=9.215 PASS（同上、vibrato_depth特徴も不変） | **維持** |
| VT-1 PASS 維持 | 0/122違反 | 再実行なし（レンダラ・推定器不変のためv3結果を継承、再掲） | **維持（再掲）** |
| VT-2 PASS 維持 | v0・R0.1ともコア帯域gate PASS、カナリアPASS | 再実行なし（同上） | **維持（再掲）** |

非退行チェックリストは全項目 PASS。breathiness / vibrato_depth の grip
数値が v3 と完全一致しているのは、これら 2 軸の intended 特徴
（periodicity / vibrato_depth_cents）が v3→v4 で定義変更されていない
（v04 memo §A: 「他4特徴はv3のまま」）ためで、想定どおりの結果である。

## v0.2 → v0.3 → v3 → v4 の grip 全推移表

| 軸 | v0.2 (z-score) | v0.3 config2 (JND効果量) | v3 config(b) (v2特徴+強化推定器) | v4 (joint fit, 免除表適用前) | v4 最終（免除表適用後） |
|---|---|---|---|---|---|
| breathiness | 1.025 | 2.358 | **3.170 (PASS)** | **3.170 (PASS)** | **3.170 (PASS)** |
| formant_scale | 0.571 | 1.825 | 2.217 | 1.738 | FAIL（僅差、従属条件不成立） |
| spectral_tilt | 0.819 | 0.638 | 1.068 | 0.574 | FAIL（宣言側効果が意図効果を上回る） |
| vibrato_depth | 0.682 | 9.215 | **9.215 (PASS)** | **9.215 (PASS)** | **9.215 (PASS)** |

### 読み方の注記（重要な逆行の記録）

**formant_scale / spectral_tilt は v3→v4 で grip が悪化した（2.217→1.738、
1.068→0.574）。** これは想定外の結果であり、率直に記録する。

- joint fit（案A）は「F1_est が動くと source_tilt の回帰対象倍音集合が
  連動して変わる」という v3 までの **逐次依存** は設計どおり解消した
  （全倍音を常に使う、F1 は初期値としてのみ参照）。
- しかし実測では、joint fit が formant_centroid と source_tilt を
  **同時に** 最適化することの副作用として、両者が互いの残差を奪い合う
  形で **新しい・より強い直接的結合** が生まれた
  （spectral_tilt 軸で E(formant_centroid)=18.24 は v3 の対応する
  E(spectral_centroid)=8.08 の 2 倍以上、しかも intended 自身の効果量
  E(source_tilt)=10.47 すら上回っている）。
- 根本原因は上記 underspec_log_v4.md の「発見した回帰と対策」で詳述した
  とおり、probe suite の大半のノードで 2 ピークモデルが不安定なため
  1 ピークモデルへフォールバックしており、その単一ピークが
  formant_centroid と source_tilt の両方の変動を同時に「奪い合って」
  説明してしまう（1 ピークでは声道と声源の分離という joint fit 本来の
  狙いを達成できていない）ことにあると考えられる。
- **教訓**: 「逐次依存の切断」という設計原理は正しい方向でも、実際の
  分離能力（2 ピーク分解能）がデータ（probe suite の倍音本数・
  フォルマント分離幅）に対して不足していると、切断した依存が別の経路
  （同時最適化における自由度の奪い合い）で再結合しうる。次サイクルへの
  申し送り事項として、probe suite 側（例: より低い音域を追加する）か、
  モデル側（帯域幅も含めた本格的な多峰性分解）のいずれかの見直しが
  必要と考えられる。

## VT-3 v4 詳細

- コマンド: `python vt3_v4.py`　実行時間: 17.3秒
- 構成: R0.1（v0.3で確定・変更なし）+ 強化推定器（v3で確定・変更なし）+
  特徴量セット v3（formant_centroid_v4 / source_tilt_v4 のみ同時推定へ
  差替え、他4特徴は measure_v3 を無改変で再利用）。
- caveatセル数は全軸 0/25（v3から維持、推定器強化の効果が持続）。
- 免除表を適用してもなお2軸が未達のまま残った。§C の指示（"無理に通さず、
  残存値と機序を記録して終了"）に従い、これ以上のレンダラ変更・追加の
  免除宣言は行わずここで終了する。
- 出力: `results_v4/grip_report_v4.json`（軸ごとの生特徴行列・免除表詳細・
  fit_mode/fit_residual_rms のセル別記録を含む）

## 総括（1行結果）

- VT-1（再掲）: PASS（v3実測を継承、レンダラ・推定器不変のため未再実行）
- VT-2（再掲）: PASS（同上）
- VT-3 v4: **4軸中2軸PASS**（breathiness・vibrato_depthは維持、
  formant_scale・spectral_tiltは免除表を適用してもなお未達 — 特に
  spectral_tiltはjoint fitが新たな強い結合（formant_centroidが
  intended自身より大きく動く）を生んだことが根本原因。17.3秒）
