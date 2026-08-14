# 実行ログ要約 v5 — v0.5 改修設計メモ実装（第 5 サイクル、VT-3 v5）

フォアグラウンド実行（run_in_background / & / nohup 不使用）。例外なし。
VT-3 v5 実行時間 約 9.4 秒。レンダラ・推定器・joint fit（v4 の安全機構込み）
は無変更、`measure_v4.py` を無改変のまま再利用（`measure_v5.py` は不要
だったため未作成）。VT-1/VT-2 は v3 以降レンダラ・推定器不変のため
本サイクルも再実行していない（v3 の実測結果が最新かつ有効）。

## band 宣言（design memo v05 §A）

| 軸 | band | probes | 根拠 |
|---|---|---|---|
| breathiness | standard | {C3,E4,A4,C5,C6} | v4でPASS済み。再測不要のためv4結果をre-state |
| vibrato_depth | standard | {C3,E4,A4,C5,C6} | 同上 |
| formant_scale | **low_register** | {G2,C3,E3,G3,C4}(f0 98-262Hz) | 中高音域はf0がF1-F2分離幅(350Hz)と同オーダー以上になり2ピーク分解が情報量的に不可能(v4実測)。低音域は4kHz以下に倍音15本以上を含み2ピーク分解が情報量的に成立する帯域として宣言。中高音域tract特徴はout_of_band（設計書§1.5eと同型の原理: 計器分解能外を「測って失敗」ではなく「計器適用範囲外」として扱う） |
| spectral_tilt | **low_register** | {G2,C3,E3,G3,C4} | formant_scaleと同一根拠（source_tiltの品質もjoint fitの安定性=F1_est相当の分解能に依存するため同一bandを割当） |

out_of_band probes（formant_scale/spectral_tilt）: {E4, A4, C5, C6}
（standard suiteのうちlow_registerに含まれない4点。今回の判定には使用せず
明示的に除外する）。

## ゲート判定表

| VT | 対象 | ゲート条件 | 結果 | 根拠数値 |
|---|---|---|---|---|
| VT-1（再掲、v3 実測を継承） | R0.1 | 122ノード中違反0 | PASS（再掲） | v3実測: 0/122 |
| VT-2（再掲、v3 実測を継承） | v0・R0.1・カナリア | 全条件 | PASS（再掲） | v3実測: 両レンダラ核帯域gate PASS、カナリアPASS |
| VT-3 v5 | breathiness（v4よりre-state） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | **PASS** | grip=3.170, dir=1.0, E=3.170 |
| VT-3 v5 | vibrato_depth（v4よりre-state） | 同上 | **PASS** | grip=9.215, dir=1.0, E=9.215 |
| VT-3 v5 | formant_scale（免除表適用前、低音域band） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | FAIL | grip=2.826, dir=0.70, E=5.051 |
| VT-3 v5 | formant_scale（免除表適用後） | grip_declared≥3.0 ∧ sign 5/5 ∧ E(declared)≤0.5×E(intended) ∧ dir≥0.90 | **FAIL**（宣言は成立、direction_consistencyのみ不成立） | grip_declared=5.051(OK), sign=5/5(OK), E(declared)=1.787≤2.525(OK), **dir=0.70<0.90(NG)** |
| VT-3 v5 | spectral_tilt（免除表適用前、低音域band） | grip≥3.0 ∧ dir≥0.90 ∧ E≥2.0 | FAIL | grip=0.528, dir=0.90, E=9.711 |
| VT-3 v5 | spectral_tilt（免除表適用後） | 同上（案B） | **FAIL**（ratio条件のみ不成立、signは改善） | grip_declared=4.040(OK), sign=5/5(OK, v4の4/5から改善), **E(declared)=18.38>4.86=0.5×E(intended)(NG)** |

**総合: 4 軸中 2 軸 PASS（breathiness, vibrato_depth、非退行維持）。
formant_scale / spectral_tilt は band 変更後もなお未達のまま終了
（memo §B 指示どおり、無理に通さず記録）。**

## fit_mode 分布（design memo v05 §B 必須記録）

| 軸 | joint_2peak | model_selected_1peak | implausible_ratio_fallback | 合計セル数 | 2peak採用率 |
|---|---|---|---|---|---|
| formant_scale (低音域) | 6 | 19 | 0 | 25 | 24% |
| spectral_tilt (低音域) | 3 | 17 | 5 | 25 | 12% |

参考（v4・標準/中高音域混在band、全4軸合算は行っていないため axis 別に
再掲): v4 の formant_scale/spectral_tilt 軸（{C3,E4,A4,C5,C6}）では probe
のうち低音の C3 でのみ 2peak が採用され、E4/A4/C5/C6 は 1peak
フォールバックがほぼ全数だった（v4 run_summary 参照）。低音域 band への
変更で 2peak 採用率は明確に上昇したが、依然として過半数（76%/88%）は
1peak フォールバックのままである。

## 非退行チェックリスト

| 項目 | v4 時点 | v5 実測 | 判定 |
|---|---|---|---|
| breathiness PASS 維持 | grip=3.170 PASS | grip=3.170 PASS（re-state、数値完全一致） | **維持** |
| vibrato_depth PASS 維持 | grip=9.215 PASS | grip=9.215 PASS（re-state、数値完全一致） | **維持** |
| VT-1 PASS 維持 | 0/122違反 | 再実行なし（v3結果継承、レンダラ・推定器不変） | **維持（継承）** |
| VT-2 PASS 維持 | 両レンダラcore gate PASS・カナリアPASS | 再実行なし（同上） | **維持（継承）** |

非退行チェックリストは全項目 PASS。

## v0.2 → v0.3 → v3 → v4 → v5 の grip 全推移表

| 軸 | v0.2 | v0.3 | v3 | v4(no-ex) | v5(no-ex, band変更) | v5免除適用後 |
|---|---|---|---|---|---|---|
| breathiness | 1.025 | 2.358 | **3.170 (PASS)** | **3.170 (PASS)** | **3.170 (PASS, re-state)** | — |
| formant_scale | 0.571 | 1.825 | 2.217 | 1.738 | **2.826** | 5.051（宣言は成立するもdir未達でFAIL） |
| spectral_tilt | 0.819 | 0.638 | 1.068 | 0.574 | 0.528 | 4.040（宣言不成立=ratio超過でFAIL） |
| vibrato_depth | 0.682 | 9.215 | **9.215 (PASS)** | **9.215 (PASS)** | **9.215 (PASS, re-state)** | — |

### 読み方の注記

- **formant_scale は 5 サイクルの中で最も grip の高い値（免除適用後
  5.051）に到達し、免除表の数値条件（grip_declared/sign/ratio）は全て
  満たした。** 最終的に FAIL としたのは `direction_consistency=0.70`
  という **新たに発見された第三の阻害要因**（低音域 probe でも
  1peak/2peakモデル選択が sweep 内で切り替わり formant_centroid の
  定義が不連続に変わる）による。band 変更が「倍音不足」という v4 の
  根因を確かに緩和した証拠（2peak採用率6/25=24%への上昇、grip自体の
  大幅改善）と、それでも解決しきれない残存課題の両方が今回明確になった。
- **spectral_tilt は band 変更の効果がほとんど無かった。** grip は
  v4→v5でほぼ横ばい（0.574→0.528、誤差範囲内の変動）。formant_centroid
  への結合は倍音本数ではなく「tiltがモデル選択率自体を動かす」という
  joint fit の構造的性質に起因するとunderspec_log_v5.mdで結論づけた。
- 5 サイクル通じて vibrato_depth は一貫して高い grip（v0.3以降9.2で
  完全に安定）を維持しており、これは「意図軸がほぼ単一の物理機序
  （F0変調）にのみ対応する」場合に grip 系の判定がいかに素直に機能する
  かを示す対照例になっている。

## 総括（1行結果）

- VT-1（継承）: PASS（v3実測、レンダラ・推定器不変のため未再実行）
- VT-2（継承）: PASS（同上）
- VT-3 v5: **4軸中2軸PASS**（breathiness・vibrato_depthは維持。
  formant_scaleは band変更で大きく前進し免除表の数値条件は全て満たした
  ものの、新たに発見したdirection_consistency不足(0.70<0.90、モデル
  選択の切替による不連続性)でFAIL。spectral_tiltはband変更の効果乏しく
  joint fitのモデル選択構造に起因する結合が残存しFAIL。9.4秒）
