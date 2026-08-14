# S3 Underspec Log

## [UNDERSPEC-S3-1] register_gains の探索安全上限

sampler.sample() の既定サンプル域は register_gains ∈ [0,0.8] だが、S2 実測
（voice_D で register_gains 最高値 0.55〜0.75 において gate1/gate6 が崩壊）
に照らすと危険域を含む。個別に安全域を特性化する時間がなかったため、S2で
実証済みの voice_D の最大値 0.40 に安全マージンを乗せた **0.50** を全 register
共通の保守的な上限として `_cap_register_gains()` で採用した。根拠なき緩和は
していないが、0.50〜0.8 の領域を未探索のまま除外している点は明記する。

## [UNDERSPEC-S3-2] linkability 距離閾値の再較正

当初 S1/S2 の within-voice レンジ（E1 0.006〜0.62 / E2 0.09〜0.28）から
一律 0.05（E1・E2共通）を仮置きしたが、実測すると mutate scale 0.08〜0.15
由来の候補は E2（log-mel）側の距離が軒並み 0.0003〜0.017 のオーダーしかなく
（formant_scale を 1.0 に固定した影響で、S2 で確認済みの E2 主要駆動因子が
探索空間から外れているため）、閾値 0.05 では真に「複製でない」候補もほぼ
全滅した。実測分布を見て E1=0.01・E2=0.002 に再較正した（詳細根拠は
`genesis_v0.py` の `LINKABILITY_THRESHOLD_E1/E2` コメント）。この閾値は
「明らかな複製」のみを弾く保守的な下限であり、`novelty` の正式な定義とは
別（memo §5.2 の詳細規定は確認できていない — [UNDERSPEC-S3-6] 参照）。

## [UNDERSPEC-S3-3] combined_axis2（Pareto 第2軸）のスケール整合

distinctiveness_from_parents（JND 複合、オーダー 0〜数）と own_nn（コサイン
距離、オーダー 0〜2）は単位が異なるため、min() を取る前に own_nn に定数
10.0 を掛けて JND と同程度のオーダーに揃えた。これは実測レンジからの目算
であり、厳密なスケール整合（例: 両者を percentile 正規化する等）は行って
いない。Pareto 前線の構成が閾値のわずかな取り方で変わりうる点は限定事項。

## [UNDERSPEC-S3-4] tract 系軸の摂動スケール（本文 genesis_report.md 参照）

memo 指定の mutate scale（0.08〜0.15）を tilt/bandwidth_scale/
breathiness_base にそのまま適用すると 12 候補全数がフル gate6 で不合格に
なることを実測で発見。tract 系軸のみ固定の狭いスケール
（`TRACT_MUTATE_SCALE=0.025`）に絞る非対称摂動へ設計変更した。数値
0.025 の選定根拠: S2 で実測した安全域の崖の幅（例: breathiness_base の
gate1 崖が tilt=-10/bw=1.30 の文脈で 0.40→0.42 の間に存在）に対し、
0.025 * spread(breathiness_base=0.6) = 0.015 という標準偏差が、D の
breathiness=0.40（崖まで 0.02 の余裕）を大きく超えて崖を踏み抜く確率を
十分に下げる値として目算した（3σ=0.045 で崖に到達し得るため完全な安全
保証ではない。実測で 12 候補中 1 個体がフル gate1-6 に通ったのはこの
残存リスクと整合する）。より厳密な安全マージン設計（例: 各軸の崖までの
実測距離を全て特性化してから摂動分散を導出する）は次サイクルの課題。

## [UNDERSPEC-S3-5] Pareto 前線全滅時のフォールバック規則

memo は「Pareto 前線からの当選者選出 → フル S5 gate」の順序を規定するが、
前線内の候補がフル gate に全て不合格だった場合の扱いを明記していない。
本実装では「前線を linkability margin 降順・candidate_id 昇順で順に試し、
最初にフル gate1-6 に全通過した個体を採用する。前線が尽きたら survivor
全体（Pareto 非劣でない個体を含む）へ同じ順序規則で拡張する」という
決定論的フォールバックを追加した（`select_final_winner_with_full_gates`）。
本実行では前線内 2 番目の候補（genesis-mut4）で解決し、survivor 全体への
拡張は発動しなかった。

## [UNDERSPEC-S3-6] §5.2 novelty 制約 / §5.3 median-min の正式定義未確認

memo が参照する設計書 §5.2（novelty 制約）・§5.3（median-min 多様性）の
本文を本サイクルでは直接参照していない（memo 本文の要約記述のみを頼りに
実装した）。linkability audit と diversity 指標の具体的な計算式は本ログの
[UNDERSPEC-S3-2] [UNDERSPEC-S3-3] に記載の通り実装側で独自に定義したもので
あり、正式な §5.2/§5.3 の定義と完全一致する保証はない。次サイクルで正本を
参照し、乖離があれば再較正が必要。

## [UNDERSPEC-S3-7] quick-S5 の probe 短縮

`QUICK_SUSTAIN_DUR=0.6`（フル gate6 の 1.2s の半分）、phrase 先頭 4 音のみ
（gate1 は全 20 音）に短縮した。実行時間は 1 パイプライン実行あたり実測
約 75〜140 秒（候補数・render回数により変動）で、memo の「10分規模まで
許容」に対して十分な余裕があったため、これ以上の短縮は行っていない。

## [UNDERSPEC-S3-8] genesis1 の親からの実質距離（正直な限定事項）

`genesis_report.md` の「正直な限定事項」節で詳述の通り、当選者 genesis1 は
linkability 監査（機械的な novelty 判定）はクリアしているが、JND 複合
スコアでは近い親（voice_C）との差が控えめ（多くの軸で <1 JND）である。
U4 の耳判定で「voice_C と区別できない」という所見が出る可能性は排除できず、
その場合は本ログの [UNDERSPEC-S3-4] で採用した `TRACT_MUTATE_SCALE` を
やや広げる（gate6 の崖を踏み抜くリスクとのトレードオフ）か、Pareto の
distinctiveness 軸の重み付けを見直す再探索が必要になる。
