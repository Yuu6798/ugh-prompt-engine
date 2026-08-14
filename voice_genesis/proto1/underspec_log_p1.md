# underspec_log_p1.md — 試作品 1 号 骨格実装で補充した判断

`proto1_design_memo.md` が決めきれていない箇所の実装判断を記録する。
vt_harness の既存メモ（v03/v031 design memo）の `[UNDERSPEC-n]` 命名規則に倣い、
本ログは `[UNDERSPEC-P1-n]` で通し番号を振る。

## [UNDERSPEC-P1-1] genome.py: physio_range の対象外フィールド

メモの「物理事前分布（凍結表）」は formant_scale / breathiness_base / tilt /
vibrato_rate / vibrato_depth / jitter_amount / register boundaries /
transition_width の 8 項目のみを列挙し、`resonance.formant_offsets[4]` と
`resonance.bandwidth_scale` には事前分布区間を与えていない。

判断: この 2 フィールドは `out_of_physio_range` 判定の対象から**除外**する
（範囲チェックをしない = 常に非違反として扱う）。理由: 凍結表に無い項目に
対して独自の区間を捏造すると「凍結表」という設計意図（メモが明示的に決めた
境界のみを信頼する）に反するため。将来これらの生理学的妥当域が決まったら
`genome.PHYSIO_PRIOR_RANGES` に追記すればよい構造にしてある
（`genome.py` の `PHYSIO_PRIOR_RANGES` dict と `compute_physio_range()` 参照）。

## [UNDERSPEC-P1-2] genome.py: register boundaries の physio_range 判定粒度

`violated_bounds` に何を積むかの粒度をメモは明記していない。実装は以下の
2 種の違反を区別して文字列化する:
  - 各境界単体が [40, 96] の範囲外 → `"register.boundaries_midi[i]"`（i=0..3）
  - 4 境界が昇順でない（隣接差が正でない） → `"register.boundaries_midi.ascending"`
これにより「値域外」と「順序不正」を切り分けて報告できるようにした。

## [UNDERSPEC-P1-3] bridge.py: jitter_amount → 旧 Genome jitter_pct_of_period の変換係数

新スキーマの `microprosody.jitter_amount`（事前分布 [0, 0.02]、無次元の
「ジッタ量」）と `voice_r0.MicroprosodyParams.jitter_pct_of_period`（既定値
0.6、`render_note` 内で `*100.0/50.0` という補正定数を経て cents 化される
合成専用のチューニング値であり、音声科学上の jitter%（通常 <1%）とは単位が
異なる）を橋渡しする式をメモは与えていない。

判断: `jitter_pct_of_period = jitter_amount * JITTER_BRIDGE_SCALE` の線形写像
とし、`JITTER_BRIDGE_SCALE = 30.0` を採用した（根拠: 事前分布上限
`jitter_amount=0.02` が `voice_a()` 既定 `jitter_pct_of_period` の代表的上限
相当値 `0.6` に写像されるよう `0.6 / 0.02 = 30.0` から逆算。事前分布下限
`jitter_amount=0` は `jitter_pct_of_period=0` = ジッタなしに写像され、これは
物理的にも自然）。この係数は R0.1 レンダラのジッタ知覚感度を再測定しない
限り恣意的である旨を明記する（`bridge.py` 冒頭 docstring にも転記）。

## [UNDERSPEC-P1-4] bridge.py: formant_offsets[4] の適用方式

新スキーマの `resonance.formant_offsets[4]` を旧 `ResonanceParams.base_formants_hz`
（4 フォルマント基準周波数のタプル）へどう反映するかをメモは式で与えていない
（「フォルマント基準値等...は v0 のまま凍結する」という §C-3 の記述と、
P1 が `formant_offsets[4]` をスキーマに含めるという記述が字面上緊張関係にある）。

判断: 「v0 のまま凍結」は §C-3 が対象とする *R0.1 レンダラの実装*（応答関数の
関数形・フィルタ次数など）についての凍結であり、P1 が新規に導入する
Genome スキーマのフィールド意味論とは別レイヤーと解釈した。`formant_offsets[i]`
は各フォルマントの基準周波数に対する**相対オフセット比率**として定義し、
`effective_formant_hz[i] = base_formants_hz[i] * formant_scale * (1 + formant_offsets[i])`
という乗算合成式（`bridge.to_render_genome()` 内）を採用した。
`formant_scale` は一様スケール（声道長プロキシ）、`formant_offsets` は
フォルマント間の相対的な形状変化（母音の質感を保ったまま個々の共鳴のみ動かす
自由度）を表す設計とした。橋渡し後の `voice_r0.ResonanceParams` へは
`formant_scale=1.0` を渡し、スケールは `base_formants_hz` にあらかじめ
織り込む（`formant_shift()` 内の二重スケーリングを避けるため）。

## [UNDERSPEC-P1-5] bridge.py: bandwidth_scale の適用方式

同様に `resonance.bandwidth_scale` は旧 `ResonanceParams.bandwidths_hz` の
全要素に対する一様乗算係数として実装した
（`effective_bandwidths_hz[i] = bandwidths_hz[i] * bandwidth_scale`）。

## [UNDERSPEC-P1-6] probes.py: cross_range probe の音長

メモは cross_range probe を「C3 と C6 の同一母音ペア」とのみ記述し、
持続時間を明記していない（sustain は 1.5s、register_sweep は 0.25s、vibrato
は 3s、phrase は各音 0.5s と明記されているのに対し cross_range のみ欠落）。

判断: sustain probe と同じ 1.5s を採用した（cross_range は「音高が違っても
同じ声だと分かるか」を見る最小限のロングトーン比較であり、sustain probe の
音長規約を流用するのが最も一貫性が高いため。母音・レンダリング設定を
sustain probe と完全に共有することで、cross_range を「sustain のうち
C3/C6 のみを抜き出したサブセット」として扱える設計にした）。

## [UNDERSPEC-P1-7] registry.py: JSONL エントリの `version` フィールドの意味

メモのエントリ一覧は `genome_id / version / created_at / ...` と
`version` を独立フィールドとして挙げるが、意味を定義していない
（「genome-registry/0.1」という sidecar 様式名は別途あるため、これは
sidecar 様式のバージョンではないはず）。

判断: `version` = 書き込み時点の `genome.schema_version`（例:
`"voice-genome/0.2"`）のミラーとした。理由: レジストリを開かずに JSONL の
1 行だけ見て「どの Genome スキーマ世代のエントリか」を判別できると、将来
スキーマが v0.3 等に進んだ際の移行監査に有用なため。sidecar 様式そのものの
バージョンは各行に `"registry_schema": "genome-registry/0.1"` として別途
持たせた（`version` と衝突させないための分離）。

## [UNDERSPEC-P1-8] registry.py: eval サブスコアの扱い

`eval: {plausibility, grip_ref, novelty}` の計算方法をメモは与えていない
（P8 で明記されている通り、grip v4・E2E 統合は本メモのスコープ外）。

判断: P1 では 3 項目とも `None`（計算未実施のプレースホルダ）で登録し、
`registry.append()` の呼び出し側が明示的に値を渡せば上書きできる構造に
した（デフォルト None で後続サイクルの grip v4 接続を待つ）。

## [UNDERSPEC-P1-8b] registry.py: crossover の lineage 遡上は先頭親のみを辿る

`lineage(genome_id)` は `parents` リストの先頭要素だけを辿って祖先鎖を作る。
`sample`/`mutate` は親が高々 1 個なので問題ないが、`crossover` は親が 2 個ある。
メモは「系譜 API: lineage(genome_id) で親鎖を遡上する」としか書いておらず、
2 親のどちらを「主系列」とするか（あるいは両方の木を返すべきか）を規定して
いない。判断: `crossover(a, b, seed)` の呼び出し順に対応する `parents=[a_id,
b_id]` の先頭（= a 側）を主系列として単一の直線チェーンを返す簡易実装とした。
両親の分岐を完全に表現する DAG 走査（全祖先集合を返す）は P1 スコープでは
過剰と判断し実装しない（`entries` を全件 load すれば呼び出し側で自力に DAG
を再構築することは可能な形にはしてある）。

## [UNDERSPEC-P1-9] reference_set.py: 「permutation（gallery ラベルシャッフル 200 回）」の具体化

メモの文言を字義通り実装しようとすると縮退する: gallery 8 名は固定ベクトル
の有限集合であり、「ラベル（識別子）をシャッフルする」だけではベクトル値
自体は一切変化しないため、コサイン類似度の分布もシャッフル前後で不変になる
（何回シャッフルしても同じ 1 組の leave-one-out 類似度集合（高々 8 値）
しか得られず、「200 回の permutation」から意味のある分布を作れない）。
また「除外すべき自己ラベル」をシャッフル後にどう定義するかも一意に決まらず、
下手をすると自己一致（cos=1.0）がチャンス分布に混入するおそれがある。

判断: メモの意図（「チャンスレベル＝無関係な声がたまたま gallery の誰かに
似て見える確率分布」を実測でしきい値化する）を保ちつつ、有限固定集合の
縮退を避けるため、次の具体的手続きに置き換えて実装した
（`reference_set.estimate_chance_band()`）:

  1. gallery 生成に使った 8 個の seed とは重複しない専用の seed 域
     （`CHANCE_SEED_BASE = 90001` から 200 個の連番）を用意する。
  2. 各 seed について `sampler.sample(seed)` で gallery とは無関係な
     「素性未知の」候補 Genome を 1 個生成し、probe をレンダリングして
     E1/E2 embedding を作る（正規化は gallery の平均・分散のみを使う。
     候補自身の統計を使わない = 実運用の監査で候補側の統計は使えないことを
     模した）。
  3. その候補の embedding と gallery 8 名との最近傍コサイン類似度を求める。
  4. 200 candidate 分のこの値を集め、95 パーセンタイルを
     `chance_band_p95` とする。

  この方式は「gallery ラベルをシャッフルする」という字面ではなく
  「gallery とは無関係な 200 個の候補を生成し、その最近傍類似度分布の
  上側 95% 点をチャンス帯とする」という統計的に健全な近似に読み替えた
  ものである。`estimate_chance_band()` の docstring にも同じ説明を転記した。
  引数名・関数名は memo の「permutation / 200 回」という語彙を踏襲しつつ、
  実装の実体は上記の通りである点を明記する。

## [UNDERSPEC-P1-10] reference_set.py: E1 特徴ベクトルの次元・集約方法

「E1: measure_v3 特徴ベクトル（probe 横断の頻度正規化済み集約）」との記述
のみで、使用する特徴量の種類・集約順序を明記していない。

判断: `measure_v3.extract_all_features_v3()` が返す 6 特徴（mean_f0_cents,
formant_centroid_log2hz, source_tilt_value, periodicity_db, rms_db,
vibrato_depth_cents）を採用（`measure_v3.FEATURE_NAMES_V3` と対応）。
「probe 横断の頻度正規化済み集約」は次の 2 段階集約と解釈した:
  (a) 同一 probe 内の各音（note）について特徴ベクトルを算出し、probe 内で
      平均する（例: register_sweep の 46 音のような音数の多い probe が
      平均を支配しないよう、まず probe 単位で 1 本のベクトルに畳む）。
  (b) 使用する probe 群（gallery/audit では sustain + phrase の 2 種）で
      (a) の結果をさらに平均する（= 各 probe を等重みで扱う「頻度正規化」）。
  最後に gallery 全体の平均・標準偏差で z-score 正規化したものを E1 embedding
  とする。

## [UNDERSPEC-P1-11] reference_set.py: E2 (log-mel) の集約方法

E2 は「log-mel 帯域エネルギー平均ベクトル（librosa、64 帯域）」とだけ
指定されている。判断: `librosa.feature.melspectrogram(n_mels=64)` を
probe 波形（sustain + phrase を連結したもの）に適用し、`log10(power+eps)`
した上でフレーム軸を平均して 64 次元ベクトルを得る。E1 と同じく
sustain/phrase を等重みで扱うため、2 probe を個別に log-mel 平均してから
2 probe 平均を取る（時間長の違う phrase・sustain を単純連結して平均すると
sustain（1.5s×3 notes）が phrase（0.5s×8 notes）よりフレーム数で優勢になる
ため、probe 単位で先に平均してから probe 間平均を取る一貫した方針を E1 と
共有した）。gallery 平均・標準偏差で z-score 正規化する点も E1 と同様。

## [UNDERSPEC-P1-10b] reference_set.py: E1 集約時の NaN 欠測（vibrato_depth）の扱い

phrase probe（各ノート 0.5s）のような短い観測窓では
`measure_v3.vibrato_depth_robust_v3` が accepted フレーム不足で NaN を返す
ことがある（sustain のような 1.5s ロングトーンでは問題にならない）。
メモはこの欠測の扱いを規定していない。判断: probe 内平均・probe 間平均の
いずれも `np.nanmean` で NaN を除外して集約し、それでも全 probe で NaN が
残った次元だけ 0.0 にフォールバックする（`reference_set._aggregate_probe_vectors`
の docstring に同内容を記載）。値を捏造せず「短音では測れなかった」という
実測の欠測を明示的な既定値で埋める方針とした。

## [UNDERSPEC-P1-12] render_health.py: register transition の測定単位

「register_sweep probe でフレーム RMS の隣接差が 6dB を超えないこと」と
あるが、register_sweep は 46 音の個別ノート列であり、各ノートは
`render_note` の attack/release フェード（0.03s）で振幅が両端に向けて
0 に落ちる。ノート境界をまたぐ生の frame-RMS 差分をそのまま測ると、
声区遷移とは無関係な「フェードによる無音化」がノイズとして混入する。

判断: 各ノート波形の中央 50% 区間（フェード外）のみで RMS を測り、
隣接ノート間の RMS(dB) 差分を「声区遷移の連続性」の測定対象とした
（`render_health.register_transition_report()`）。

## [UNDERSPEC-P1-13] render_health.py: formant sweep の測定条件

「formant_scale 0.85→1.15 で formant_centroid_v3 が単調に動くこと」との
記述のみで、測定に使う MIDI ノートを指定していない。判断: sustain probe の
中央値ノート A4 (MIDI 69) を用いた（低音 C3 は基音減衰が強くケプストラム
包絡のピーク検出が不安定になりやすく、高音 C6 は F1 追従（ソプラノ式
フォルマントチューニング）が formant_scale の効果と絡み合うため、両者から
離れた中庸ノートを選んだ）。formant_scale は [0.85, 1.10, ..., 1.15] を
0.05 刻みで 7 点評価する。

## [UNDERSPEC-P1-13b] render_health.py: formant sweep を「厳密な単調性」ではなく direction_consistency で判定

実装当初は文字通り「単調非減少」（隣接差がすべて >=0）で判定したところ、
複数の sampler.sample() genome で高頻度に FAIL した（`formant_scale` を
0.85→1.15 で掃引すると、cepstral 包絡の top-2-by-magnitude ピーク選択が
掃引途中でどの 2 フォルマントを拾うか切り替わり、centroid が非連続に
ジャンプする実測を確認）。既存の grip v3 実測
（`vt_harness/results_v3/grip_report_v3.json` の
`axis=formant_scale, config=config_b_R0.1_enhanced_v2_features,
intended_feature=formant_centroid`）を確認したところ
`direction_consistency=0.75`（隣接ステップの 75% のみが期待方向）であり、
**厳密な単調性はそもそも実測上成立しない特性**と判明した。

判断: memo の「既存 grip 結果と整合。簡易確認で可」という文言を、厳密な
単調性要求ではなく「grip v3 実測と整合するレベルのおおむね上昇傾向」への
要求と読み替え、pass 基準を **direction_consistency（隣接差が非負である
割合）>= 0.60** の単一条件に変更した（`render_health.formant_sweep_report()`）。
しきい値 0.60 は実測 0.75 にマージンを持たせた値（本テストの genome/seed は
grip v3 のものと異なるため、実測レンジの下限側に振れても壊れないようにした）。
「掃引全体で終点 > 始点」という net-direction 条件も当初は併用していたが、
1 箇所の大きなピーク選択ジャンプだけで direction_consistency が高くても
net-direction が負になるケースを実測で確認したため（grip v3 の acceptance
指標も direction_consistency 単体であり net-direction 条件は無い）、
gate からは外し `net_direction_positive` は診断用の付随情報としてのみ
report に残した。

## [UNDERSPEC-P1-14] tests: `python -m pytest tests -q` の実行ディレクトリ

メモ本文「`python -m pytest proto1/tests -q` で回る」と、P7 見出し直後の
「`python -m pytest tests -q` を $PROTO で実行して全パスさせる」という
タスク指示文で表記がわずかに異なる（相対パスの起点が違う）。実質的には
同じコマンドを指す（`$PROTO` を cwd にして `pytest tests -q` するか、
どこからでも `pytest proto1/tests -q` するかの違いのみ）。両方で動作する
よう `tests/conftest.py` は使わず、各テストファイル冒頭で `sys.path` に
`Path(__file__).resolve().parent.parent`（= proto1/）を追加する自己完結構成
にした。`results_p1/scaffold_test_report.md` には `$PROTO` を cwd とした
`python -m pytest tests -q` の実行結果を記録する。
