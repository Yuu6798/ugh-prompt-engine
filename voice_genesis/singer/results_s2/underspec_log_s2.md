# S2 Underspec Log

## [UNDERSPEC-S2-1] between/within 判定時の「within」定義

`s2_identity_design_memo.md` T1-1 は「between > within が成立するか」を
求めるが、C/D 2 声のうち within をどちらの値で判定するか明記していない。
**より厳しい基準として、2 声のうち大きい方（`max(within_x, within_y)`）を
採用**した（`identity_metrics.measure_separation` 実装）。T1（A/B）・T3（C/D）
とも同じ定義で一貫させている。

## [UNDERSPEC-S2-2] E1 embedding の rms 次元縮退バグ

T1 実測で発見: `reference_set.py` の gallery（R0.1・8個体）は各ノート出力を
一定ピーク値に正規化する設計のため、E1 の rms 次元の gallery 標準偏差が
極端に小さい（実測 std≈0.072、他次元は 0.5〜760）。これにより z-score が
他次元の 1〜2 桁大きくなり、コサイン距離が実質「rms 次元の符号一致」だけで
決まる状態だった。`reference_set.py` は無改変のまま、`identity_metrics.py`
側で (1) z-score を ±5 にクリップ、(2) それでも符号反転で歪む rms 次元を
コサイン距離計算から除外、の 2 段階で頑健化した（詳細は
`identity_metrics.py` の `Z_SCORE_CLIP` / `E1_DEGENERATE_DIMS` コメント）。
JND 会計表（per-feature 個別集計）では rms を従来通り記載しているため、
この頑健化は「集約 embedding での識別」にのみ影響し、JND 会計自体の
値には影響しない。

## [UNDERSPEC-S2-3] gate6 と formant_scale の非両立（構造的発見）

memo は「formant_scale/tilt を identity の主軸として voice_C/D を設計」と
指定していたが、実測で `resonance.formant_scale` を voice_A の既定値 1.0 から
±0.01 動かしただけで S5 gate6（grip 非退行クイックチェック、S1 で凍結）の
breathiness 側が不通過になることを発見した（fs=0.99: grip=3.003 通過、
fs=0.98: grip=1.875 不通過）。原因は gate6 breathiness クイックチェックの
「periodicity が breathiness sweep にどれだけ反応するか」という測定量が
formant フィルタの共鳴点配置に強く依存し、fs が 1.0 の較正点から外れると
急激に鈍化するため（`E_intended` が主要因、side-feature の暴走ではない —
実測でも確認済み）。

**この発見は memo の T2-1（「vowel 目標への希釈」仮説）とは異なる原因**で
あり、identity 写像そのものの弱さではなく、**S5 gate6 が voice_A 近傍の
狭いパラメータ域でのみ較正されている**という制約に起因する。voice_B
（S1 で gate6 breathiness のみ不通過）もこの制約の別の顕れだったと解釈できる。

対応: `formant_scale` を 1.0 に固定し、`tilt`（安全域を実測で走査）と
`bandwidth_scale`（Genome の凍結表に含まれないフィールドで、gate6 実測での
安全域が formant_scale よりずっと広い: 0.80〜1.30）を主な tract 対比軸に
差し替えた。`bandwidth_scale` は声道フォルマント帯域幅（共鳴の鋭さ）を表す
既存の Genome フィールドであり、新規フィールド追加ではない（Genome 契約は
維持）。

**次サイクルへの示唆**: `formant_scale` を identity 軸として本格的に使いたい
場合、(a) gate6 grip クイックチェックの較正域を formant_scale の広い範囲で
再検証・必要なら再閾値化する、または (b) `render_sustained_vowel` 側の
periodicity-breathiness 応答を formant_scale に対してより頑健にする、の
いずれかが前提条件になる。今回は S5 gate1-6 が凍結（変更禁止）のため、
Genome/レンダラ側のパラメータ選択で回避した。

## [UNDERSPEC-S2-4] breathiness_base の gate1 崖

D 側の `noise.breathiness_base` を上げるほど E2（log-mel）分離の margin が
改善する（実測で確認済み、E2 が breathiness による声質の広がりに敏感な
ことを示唆）。しかし 0.42 付近に鋭い崖があり、それを超えると gate1（F0
追従）の `max_abs_cents_err` が 22.9c → 1218.2c へ急激に悪化する（背景ノイズ
過大で該当ノートの F0 推定器が破綻するため）。0.40 を安全上限として採用した。
この崖の正確な位置は tilt/bandwidth_scale の組み合わせに依存する（実測: 
tilt=-7/bw=1.20 では崖が 0.48〜0.50 付近、tilt=-10/bw=1.30 では 0.40〜0.42
付近 — 組み合わせごとに個別実測が必要で、単純な閉形式の安全域予測式は
今回導出していない）。

## [UNDERSPEC-S2-5] E1_DEGENERATE_DIMS の一般化可能性

rms 次元の縮退は `reference_set.py` の gallery が R0.1（ピーク正規化あり）で
レンダリングされていることに起因する、と推定した（確定的な原因究明では
なく、観測された std パターンからの合理的推論）。他の次元（mean_f0 /
formant_centroid / source_tilt / periodicity / vibrato_depth）は今回の
gallery で十分な分散を示したため除外対象に加えていないが、gallery の
sampler 実装が変わった場合はこの判定を再実施する必要がある。
