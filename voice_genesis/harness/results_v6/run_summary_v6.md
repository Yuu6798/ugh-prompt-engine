# 実行ログ要約 v6 — v0.6 改修設計メモ実装（第 6 サイクル・grip 完成の最終試行）

フォアグラウンド実行（run_in_background / & / nohup 不使用）。例外なし。
VT-3 v6 実行時間 約 8.6 秒。`measure_v4.py` は無改変のまま再利用、拡張は
`measure_v6.py` として新設。VT-1/VT-2 はレンダラ・推定器が v3 以降不変
のため本サイクルも再実行していない（v3 実測が引き続き有効）。

**本サイクルは design memo v06 が明記する「grip 完成の最終試行」。
以降のレンダラ・推定器・joint fit の変更は行わない方針で結果を確定する。**

## band 宣言・凍結原理（design memo v05 §A / v06 §A、継承）

| 軸 | band | probes | 凍結 |
|---|---|---|---|
| breathiness | standard | {C3,E4,A4,C5,C6} | 対象外（v4でPASS済み、re-state） |
| vibrato_depth | standard | {C3,E4,A4,C5,C6} | 対象外（同上） |
| formant_scale | low_register | {G2,C3,E3,G3,C4}(f0 98-262Hz) | **適用**: probeごとにsweep中央点(index2)でv4既存規則(BIC+F2/F1妥当性ゲート)により次数・アンカーを1回決定、sweep全点+σ_meas3反復をその凍結構造(次数固定・アンカー±30%局所窓固定・モデル選択再実行なし)で再フィット |
| spectral_tilt | low_register | {G2,C3,E3,G3,C4} | 同上 |

## ゲート判定表

| VT | 対象 | ゲート条件 | 結果 | 根拠数値 |
|---|---|---|---|---|
| VT-1（継承、v3実測） | R0.1 | 122ノード中違反0 | PASS（継承） | v3実測: 0/122 |
| VT-2（継承、v3実測） | v0・R0.1・カナリア | 全条件 | PASS（継承） | v3実測: 両レンダラcore gate PASS、カナリアPASS |
| VT-3 v6 | breathiness（v4よりre-state） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | **PASS** | grip=3.170, dir=1.0, E=3.170 |
| VT-3 v6 | vibrato_depth（v4よりre-state） | 同上 | **PASS** | grip=9.215, dir=1.0, E=9.215 |
| VT-3 v6 | formant_scale（凍結、免除表適用前） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | FAIL | grip=2.471, **dir=0.60**, E=4.354 |
| VT-3 v6 | formant_scale（凍結、免除表適用後） | grip_declared≥3.0 ∧ sign 5/5 ∧ E(declared)≤0.5×E(intended) ∧ dir≥0.90 | **FAIL**（数値条件は全成立、direction_consistencyのみ不成立） | grip_declared=4.354(OK), sign=5/5(OK), E(declared)=1.762≤2.177(OK), **dir=0.60<0.90(NG)** |
| VT-3 v6 | spectral_tilt（凍結、免除表適用前） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | **PASS**（免除表不要） | grip=4.357, dir=0.9, E=10.471, dominant_side=periodicity |

**総合: 4 軸中 3 軸 PASS（breathiness, vibrato_depth, **spectral_tilt(NEW)**）。
formant_scale のみ未達のまま最終確定（memo §B 指示どおり無理に通さず記録、
本サイクルをもって grip 完成試行を終了する）。**

## 凍結構造の記録（design memo v06 §B 必須記録、probe 別）

### formant_scale / spectral_tilt 共通（sweep 中央点=formant_scale 1.0 相当、
spectral_tilt -10.0dB/oct 相当で決定。両軸とも同一 probe suite・同一
デフォルトGenomeから出発するため凍結構造は軸間で同一値になる）

| probe | n_peaks | anchor_peaks (Hz) | 決定時 fit_mode | 決定時倍音数K |
|---|---|---|---|---|
| G2 | 2 | 822.13, 1126.98 | joint_2peak | 40 |
| C3 | 1 | 840.05 | joint_2peak_model_selected_1peak | 40 |
| E3 | 1 | 1081.56 | joint_2peak_model_selected_1peak | 40 |
| G3 | 1 | 1093.12 | joint_2peak_model_selected_1peak | 40 |
| C4 | 1 | 897.59 | joint_2peak_model_selected_1peak | 30 |

## fit_mode 分布（sweep 全 25 セル、design memo v06 §B 必須記録）

| 軸 | frozen_2peak | frozen_1peak | 合計 | 2peak採用率 |
|---|---|---|---|---|
| formant_scale | 5 (G2の全sweep点) | 20 | 25 | 20% |
| spectral_tilt | 5 (G2の全sweep点) | 20 | 25 | 20% |

**凍結の主目的（sweep内でのモデル切替の除去）は達成**: v5 では probe 内で
sweep点ごとにモード（1peak/2peak）が切り替わっていたが、v6 では probe
ごとに sweep 全体を通して単一モードに固定されている（G2は常に2peak、
他4probeは常に1peak）ことを確認した。

## 非退行チェックリスト

| 項目 | v5 時点 | v6 実測 | 判定 |
|---|---|---|---|
| breathiness PASS 維持 | grip=3.170 PASS | grip=3.170 PASS（re-state、完全一致） | **維持** |
| vibrato_depth PASS 維持 | grip=9.215 PASS | grip=9.215 PASS（re-state、完全一致） | **維持** |
| VT-1 PASS 維持 | 0/122違反 | 再実行なし（v3結果継承） | **維持（継承）** |
| VT-2 PASS 維持 | 両レンダラcore gate PASS・カナリアPASS | 再実行なし（同上） | **維持（継承）** |

非退行チェックリストは全項目 PASS。加えて **spectral_tilt が新規に PASS
へ到達**（3/4→3/4のまま軸の入れ替わりではなく、v5時点で未達だった軸が
新たにPASSに転じた正味の前進）。

## v0.2 → v0.3 → v3 → v4 → v5 → v6 の grip 全推移表

| 軸 | v0.2 | v0.3 | v3 | v4 | v5(no-ex/免除後) | v6(no-ex/免除後) |
|---|---|---|---|---|---|---|
| breathiness | 1.025 | 2.358 | **3.170 (PASS)** | **3.170 (PASS)** | **3.170 (PASS)** | **3.170 (PASS)** |
| formant_scale | 0.571 | 1.825 | 2.217 | 1.738 | 2.826 / 5.051(dir=0.70でNG) | 2.471 / 4.354(**dir=0.60でNG、悪化**) |
| spectral_tilt | 0.819 | 0.638 | 1.068 | 0.574 | 0.528 | **4.357 (PASS, 免除不要)** |
| vibrato_depth | 0.682 | 9.215 | **9.215 (PASS)** | **9.215 (PASS)** | **9.215 (PASS)** | **9.215 (PASS)** |

### 読み方の注記（最終サイクルの総括）

- **spectral_tilt は 6 サイクル目にしてついに PASS に到達した。** v4/v5
  で観測された巨大な副作用（formant_centroidが最大18超で意図効果自体を
  上回っていた）は、joint fit のモデル選択（1peak/2peak切替）が sweep
  内で不連続に起きることによる**計器アーティファクトであり、真の物理
  結合ではなかった**ことが、凍結により初めて実証的に切り分けられた。
  これは v0.3 以来追いかけてきた「共線性の根因診断」が最終的に正しい
  結論（モデル選択由来のアーティファクト）に到達したことを意味する。
- **formant_scale は逆に、凍結後もなお（悪化さえして）PASS に届かな
  かった。** 免除表の数値条件（grip_declared・符号一致・E比率）は全て
  満たしており、grip の「大きさ」としては十分（4.354、gate閾値3.0を
  上回る）。唯一の障壁は direction_consistency（0.60）であり、これは
  spectral_tiltと異なりモデル切替由来ではなく、**固定モデル構造下でも
  なお残るピーク位置推定自体のノイズ**に起因すると診断した
  （underspec_log_v6.md参照）。5サイクルにわたる原因追及の結果、
  「アルゴリズムの構造的欠陥」から「計器の測定分解能そのものの限界」
  へと問題の所在が明確に絞り込まれた。
- memo の指示（本サイクルが grip 完成の最終試行）に従い、これ以上の
  レンダラ・推定器変更は行わず、formant_scale 未達を fail-closed の
  open issue として受け入れて終了する。

## Open Issue（試作品1号統合への申し送り）

- **formant_scale 軸の grip gate は未達のまま終了する。** 免除表の
  従属条件は全て満たしており、残る唯一の障壁は方向一致率
  （0.60、閾値0.90）。原因は測定ノイズ（±30%局所窓内でのローレンツ
  ピーク位置推定のノート間ばらつき）であり、モデル選択の不連続性という
  過去5サイクルの主要な仮説はここでは否定された。次の取り組みが必要な
  場合、(a) probe suite自体をさらに増やしてノイズを平均化する、
  (b) ピーク位置推定自体の分解能を上げる（帯域幅B_iの同時推定、倍音
  ピーク検出の窓幅見直し等）のいずれかが候補になるが、これは本メモの
  スコープ外として記録するのみに留める。

## 総括（1行結果）

- VT-1（継承）: PASS（v3実測、レンダラ・推定器不変のため未再実行）
- VT-2（継承）: PASS（同上）
- VT-3 v6: **4軸中3軸PASS**（breathiness・vibrato_depthは維持、
  **spectral_tiltが新規PASS**=sweep内計器凍結によりモデル選択由来の
  見かけ結合を解消。formant_scaleのみ未達で最終確定（免除表の数値条件は
  全て満たすが方向一致率0.60が閾値0.90に届かず、原因はモデル切替でなく
  純粋な測定ノイズと診断）。8.6秒）
