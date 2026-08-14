# Underspecification Log v2 — v0.3 改修設計メモ実装

`v03_design_memo.md`（§A〜G）を実装する過程で、メモの記述だけでは一意に
決定できなかった箇所、および実装のために自分で置いた補助的な設計判断を
列挙する。

| # | 対応メモ§ | 欠落内容 | 自分が置いた仮定 |
|---|---|---|---|
| 1 | §A-1/§A-2 | 各特徴量を「共通の物理単位空間へ写像してから差分を取る」変換の具体式が示されていない（表は ref_scale の単位のみ規定）。 | `measure_v2.py` の `FEATURE_TRANSFORM_KEYS` として明示: mean_f0 → `1200*log2(f0_hz)`（原点は任意。差分だけが実 cents になる）、spectral_centroid → `log2(centroid_hz)`（§A-1 の指示どおり）、rms → `20*log10(rms)`（dB）、spectral_tilt / periodicity / vibrato_depth は元々の単位のまま恒等変換。E_note・σ_meas・direction_consistency は全てこの変換後の値で計算する。 |
| 2 | §A-4 | 「Genome 固定・jitter/noise seed のみ変えた 3 反復レンダ」の具体的な実現方法が示されていない（`render_note` は seed を明示引数に取らず、`genome.microprosody.jitter_seed + midi` から内部導出する設計のため）。 | 3 反復それぞれで `genome.microprosody.jitter_seed` に固定オフセット `{0, 1009, 2003}` を加えた Genome のコピーを作り、他は一切変更せずレンダリングする。 |
| 3 | §A-4 | `below_instrument_resolution` 判定の σ_meas を probe ごとに使うか、軸全体で集約した 1 値にするかが示されていない。 | probe ごとの σ_meas をその probe 自身の ΔF（sweep 端点差）と比較する（probe 単位で判定）。軸レベルには `any_below_instrument_resolution`（5 probe 中 1 つでも該当すれば true）として集約し、詳細は `below_instrument_resolution_per_probe` に probe 別で保持する。 |
| 4 | §A-4 | `measured_with_caveat`（F0 トラックのオクターブ跳躍系外れ値検出）の具体的な検出基準が示されていない。 | `measure_v2.vibrato_depth_robust` の外れ値棄却率（±600 cents 超棄却）が 5% を超えたセルを `measured_with_caveat=true` とする（§B-2 の棄却機構をそのまま流用）。 |
| 5 | §E | bench のカナリア（既知合成 GT）に使う具体的な周波数点・許容誤差が示されていない。 | 4 点の純音サイン波（82.4 / 261.63 / 987.77 / 2489.0 Hz、レンダラ・Genome を一切経由しない）を選定。許容誤差は 30 cents とした（自前推定器がコア帯域で示す通常の系統誤差（実測最大 50 cent 級）を誤検知せず、オクターブ級（600cent超）の破綻は確実に検知する水準として設定）。 |
| 6 | §E | bench の対象レンダラ（v0 R0 か R0.1 か）が明記されていない。 | R0.1 を採用（v0.3 全体が R0.1 への移行を主眼とするため）。**この選択により、v0 では発生しなかった F0 推定オクターブ誤りが MIDI 93 / 96（B6/C7 付近）で新たに発生することを発見した**（詳細は run_summary_v2.md）。R0.1 特有の新規回帰であり、v0 で bench した場合はこの回帰は見えない。 |
| 7 | §D | plausibility 不変条件テストの対象レンダラ（v0 か R0.1 か）が明記されていない。 | R0.1 を正式 gate 対象とし、v0（既に breathiness 0.95 クランプ適用済みの現行 `voice_r0.py`）も参考データとして併記した。 |
| 8 | §C-1 | 息ノイズへの tilt+高次減衰フィルタの「連続スペクトルへの拡張式」が示されていない（元の式は離散調波番号 k=1,2,3,... に対してのみ定義）。 | `voice_r0_1._noise_tilt_envelope_gain` で harmonic number を `k=freq/f0` の連続変数として拡張。ただし `k<1`（基音未満の周波数帯）では tilt が負のとき利得が発散するため `k` を 1.0 に下側クランプした（離散モデルが元々 k>=1 でしか評価されないことと整合）。 |

## 実装過程で判明した、メモの想定と異なる実測結果（記録のみ・メモの逸脱ではない）

- **R0.1 適用後、VT-2 v2 の bench で MIDI 93 / 96 に新規のオクターブ誤り
  （それぞれ -505.8 cents, -1203.6 cents）が発生した。** v0 レンダラでは
  同一 fmin/fmax 設定で両ノートとも 50 cents 以内に収まっていたことを
  診断的に確認済み（run_summary_v2.md 参照）。§C-1 の修正（ノイズに
  tilt+高次減衰整形を適用）自体は breathiness→centroid entanglement の
  低減という意図した効果を出しているが（C3 で +65.3%→-7.3%）、副作用として
  高音域の周期性構造が変化し自前 F0 推定器の弱点（短周期・境界近傍での
  部分周期整合）を再び露出させたと考えられる。この因果関係の特定・対策は
  本メモの範囲外のため、実測のみ記録し次サイクルへの申し送りとする。
- **spectral_tilt 軸・formant_scale 軸で、dominant side feature が
  それぞれ spectral_centroid / spectral_tilt になる構造が両構成
  （v0 と R0.1 の双方）で一貫して観測された。** これは計器アーティファクト
  というより、①spectral tilt を変えれば定義上スペクトル重心も動く、
  ②フォルマント位置を変えれば倍音包絡から回帰計測する tilt も動く、という
  本質的な特徴量間の共線性（collinearity）である可能性が高い。§B の計器
  修復（periodicity / vibrato_depth）はこの collinearity を対象にしておらず、
  grip v2 も「側特徴量の集合」自体の独立性は仮定として要求している
  （§A-2 の凍結側特徴集合）。この収束を解消するには特徴量セットの再設計
  （例: tilt を「フォルマント補正後の残差」として測る等）が必要で、
  本メモのスコープ外として次サイクルへの申し送りとする。
