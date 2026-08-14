# S3 設計メモ — Genesis Graph v0（VG-015）: 探索による新歌手の鍛造

ゴール: 設計書 §5 の中核主張「Genome を分岐・評価・淘汰して未知の歌手を
探索し、再現可能に固定できる」の初実証。工房が**手作業でなく探索で**
新しい歌手を 1 人鍛造し、系譜付きで registry に凍結し、歌わせる。
配置: singer/ 配下（vt_harness/・proto1/ は読み取りのみ）。リポジトリ
読み取り専用。補充判断は underspec_log_s3.md。

## U1. 前段: gate6 較正域の拡張（formant_scale の復権）

S2 の発見「formant_scale ±0.01 で gate6 崩壊」の機序を診断してから探索に
入る（探索空間の広さに直結するため先にやる）:

1. 診断: formant_scale=0.95 の genome_base で gate6 breathiness grip が
   崩れる経路を特定する（仮説候補: (a) フォルマント移動で periodicity 計測の
   倍音帯域整合が崩れる計器問題、(b) GAIN_FLOOR との相互作用、(c) 実物理）。
   probe 別・特徴別の生データで切り分ける
2. 対処は診断結果に従う: 計器問題なら測定側の頑健化（measure 系は新設
   ファイルで、既存は無改変）、レンダラ問題なら R0.9 の写像修正（Genome
   契約・S5 既存ゲートの非退行必須）、実物理なら formant_scale の安全域を
   実測で宣言して探索境界に使う（無理に広げない）
3. 成功条件: formant_scale ∈ [0.92, 1.10] 程度で gate6 が保つこと。
   達成できない場合はその旨を記録し、U2 は S2 実証済みの安全域
   （tilt / bandwidth_scale / breathiness_base / register_gains / vibrato）
   のみで実施（fail-closed、探索は止めない）

## U2. Genesis Graph v0 の探索実行

- **初期集団**: 親 = voice_C / voice_D + sampler.sample の gate-safe 域
  サンプル 2 個の計 4
- **分岐**: mutate（scale 0.08〜0.15）と crossover を混ぜて **12 候補**を
  決定論 seed で生成（proto1/sampler.py を読み取り import。橋渡しは
  singer 側の既存 bridge 経路）
- **評価**（候補ごと。すべて既存計器の再利用）:
  1. quick-S5: 短縮 probe（sustain C3/A4/C6 + phrase 先頭 4 音）で
     plausibility・F0 追従・aliasing（フル gate は当選者のみ）
  2. linkability 監査: standin-gallery-v1 に対し E1/E2（proto1/reference_set
     読み取り import、rms 次元除外の S2 頑健化を適用）。不合格は即淘汰
     （novelty 制約 = §5.2）
  3. 多様性: §5.3 の median-min 距離（E1/E2 の観測レベル）を集団に対して算出
  4. 親からの distinctiveness: voice_C/D との JND 会計（tract 系 +
     声質系の合成、S2 の表と同一手法）
- **淘汰**: 単一スコアに潰さず **Pareto**（plausibility 系 ↔ distinctiveness/
  多様性）で非劣解を残し、その中から「linkability margin が最も安全な個体」
  を当選者に決定論選出（タイブレーク規則を明記）
- **凍結**: 当選 Genome を proto1 registry（singer 実行用の新規 registry
  ファイル。proto1/results_final の正本 registry には追記しない — 読み取り
  専用維持）に系譜（親・op・seed・評価値・linkability_report_id・
  reference_set_hash）付きで登録。**同一 seed で再実行して同一当選者に
  到達することを機械照合**（§5「再現可能に固定」の実証）
- **実演**: 当選者にフル S5 ゲート（gate1-6）を掛け、通過を確認してから
  「さくらさくら」を歌わせ `sakura_genesis1.wav` を出力

## U3. 成果物

- `singer/genesis_v0.py`（探索本体）+ 必要な補助
- `singer/results_s3/genesis_report.md`（U1 診断結果・12 候補の評価表・
  Pareto 前線・当選者と系譜・再現性照合・当選者のフル S5 表）
- `singer/results_s3/sakura_genesis1.wav`（新歌手の歌唱）
- `singer/results_s3/lineage_genesis1.json`（系譜の機械可読版）
- `singer/results_s3/underspec_log_s3.md`
- 実行様式: 従来どおり。12 候補 × 短縮評価のため実行時間は数分〜10 分規模
  を許容（それを超える場合は候補の probe をさらに短縮してよい。短縮内容は記録）

## U4. 耳判定（実装後）

当選者の歌唱を User に提示し「C/D とも違う、新しい歌手に聞こえるか」を
判定してもらう。成立なら Genesis Graph v0 実証完了、不成立なら
distinctiveness 評価の重みを耳所見で再較正して再探索。
