# Genre Calibration & Semantic Vocabulary Expansion — Planning

**Status**: PLANNING
**Created**: 2026-06-24
**Upstream**: [`learned_models_policy.md`](learned_models_policy.md)（学習モデル隔離ポリシー）, [`ai_music_daw_vision.md`](ai_music_daw_vision.md)（SVP = AI 音楽の MIDI ビジョン）
**Relates to**: [`roadmap_goal1.md`](roadmap_goal1.md)（Q1-5 spectral 計器拡張）, [`metrics.md`](metrics.md)（意味層スコアリング）, [`roundtrip_corpus_screen.md`](roundtrip_corpus_screen.md)（content-addressed corpus 方式の先例）

---

## 本ドキュメントの位置付け

本ドキュメントは **意味層の語彙域拡張（特にジャンル / 楽器の識別）** をどう前に進めるかの
**計画**である。「なぜ意味層を拡張するか」「ルールベースの限界とは何か」の原則は
上位文書に従う:

```text
ai_music_daw_vision.md      — 最長期ビジョン（SVP as AI music MIDI）
learned_models_policy.md    — 学習モデル隔離ポリシー（rule evidence 非混入）
genre_calibration_planning.md — 本文書（語彙拡張をどう校正データで成立させるか）
```

---

## 1. 問題の発見（2026-06-24 セッション）

実音源 2 曲を分析・再生成するセッションで、意味層の **ジャンル判定が体系的に外れる**
ことが具体例つきで確認された。

| 対象 | 真の正体 | ツールの `cultural_context` | 帰結 |
|---|---|---|---|
| 「Portals」（A. Silvestri, フルオーケストラ） | cinematic / orchestral | `bass-music` のみ（誤） | 生成プロンプトが "bass music / electronic" となり、Suno が **electro dance 化** |
| 「UZA」（AKB48, エレクトロロック） | electronic / dance | `bass-music`（概ね妥当） | 再現度高（域内） |

### 根本原因

`src/svp_rpe/rpe/semantic_rules.py` の `_infer_cultural_context()` /
instrumentation 推定が **Python ハードコードで、かつ条件が狭すぎる**:

```python
if low_ratio > 0.4:      contexts.append("bass-music")          # Portals が発火
if valley_depth > 0.3:   contexts.append("cinematic/orchestral") # Portals=0.16 で不発
```

低音の強いオーケストラは `bass-music` に吸い込まれ、orchestral 判定は
`valley_depth>0.3` という別軸の狭い条件で発火しなかった。
これは **ルール設計の問題**であり、`config/semantic_rules.yaml` の
ルールエンジン（`_labels_from_rules` / `_emit_labels` / evidence 生成）は
既に汎用なので、設計で直せる範囲にある。

### 物理的に識別は可能か（手元データの実測）

| 曲 | harmonic_ratio | percussive_ratio | spectral_centroid | dynamic_range_db |
|---|---|---|---|---|
| Portals（管弦） | 0.81 | 0.19 | 1588 Hz | 18.9 |
| UZA（電子） | 0.71 | 0.29 | 3735 Hz | 3.98 |

Q1-5（#91）で追加済みの `harmonic_ratio` / `percussive_ratio` /
`spectral_bands` に、管弦＝倍音的・電子＝パーカッシブという**差は出ている**。
ただし harmonic_ratio 単独（0.81 vs 0.71）では分離が弱く、centroid との
組合せが要る。**点が 2 つでは線が引けない** → 校正データが必要、という結論。

---

## 2. 拡張の 3 段階（コスト順）

| Tier | 内容 | 実装場所 | コスト | 律速 |
|---|---|---|---|---|
| **Tier 1** | YAML にラベルルール追記（aggressive / orchestral 等） | `config/semantic_rules.yaml` | 軽（コード変更なし） | 既存物理特徴の表現力 |
| **Tier 2** | `cultural_context`/`instrumentation` を config 化 + Q1-5 新特徴で再校正 | `semantic_rules.py` リファクタ + YAML | コードは軽 / **校正が重** | **校正データ** |
| **Tier 3** | 学習モデルで本物のジャンル/楽器認識（PANNs 等） | `rpe/learned/` + `LearnedAudioAnnotations` | 重 | 決定論性 + OSS license（[`learned_models_policy.md`](learned_models_policy.md)） |

**本命は Tier 2**。`_feature_value` に `getattr` フォールバックがあるため
`harmonic_ratio` 等は**コード変更なしで条件キーに使える**。重いのは実装量でなく、
閾値を defensible に決める **校正データの調達**。

---

## 3. 校正データの再定義（本計画の核心）

**校正データ = 「ジャンル/楽器の正解ラベルが付いた、十分な曲数の音源」**。
`harmonic > X → orchestral` の X を引くには、ジャンルを横断した多数の
（物理特徴, ラベル）ペアが要る。

### 従来の律速と、その回避

| データ種類 | ライセンス | 問題 |
|---|---|---|
| 自作合成音（`synth_01`〜） | 自由 | 音色が単調で本物の倍音構造を再現できない |
| 実在の市販曲 | 不可 | リポジトリ同梱・再配布・CI 再現が不可 |

→ 従来はここで「licensing 律速」として停止していた（Q1-5 Phase 2 のブロッカー）。

### 突破口: Suno 生成によるラベル付きコーパス（採用方針）

意図的にジャンルを狙って Suno で生成した曲を校正データに使う。利点:

1. **ライセンス律速が消える** — 生成物は利用可・リポジトリ管理下に置ける
2. **ラベル付けが自動** — `orchestral` と打って生成した曲はラベル＝orchestral が確定（**プロンプト = ラベル**）
3. **量とジャンル網羅が安い** — 既存の「生成→`extract`→分析」パイプラインがそのまま使える

### 生成器バイアスへの対処（必須の留保）

Suno-orchestral ≠ 本物-orchestral。本セッションで証拠が出ている:
**本物 Portals の dynamic_range=18.9dB に対し Suno 再現は 9.84dB**。
Suno で引いた閾値は本物で外す危険がある（circularity）。

ただし **このプロダクトのビジョンは AI 音楽の分析**（`ai_music_daw_vision.md`）であり、
分析対象が Suno/AI 生成音楽なら Suno 校正は**バイアスでなく正解**（in-distribution）。

**設計上の両取り**:

1. **本体 = Suno 生成コーパス**（ジャンル別量産・ラベル自動・license free）で閾値を引く
2. **少数の本物アンカー**（実曲数曲）を検証用に置き、generator bias を測る/補正する
3. ラベル準拠は保証されない（key/genre が生成で流れる実例あり）ため、**採用前に耳で軽く確認**

### 保存方式

音源本体はコミットせず **content-addressed（sha256 ピン + Drive 所在）** で管理。
R1-audio（#94/#97）の `fetch_corpus.py` → manifest 方式を踏襲する。

---

## 4. フェーズ計画

```text
Phase A: config 化リファクタ（Claude 単体可・校正データ非依存）
Phase B: ジャンル校正コーパス構築 + 閾値導出（Suno 生成 + 本物アンカー）
Phase C: 本物アンカーでの bias 検証 + follow-up 補正
```

### Phase A — `cultural_context`/`instrumentation` の config 化

**校正データに依存しない機械的リファクタ**。先行して着手できる。

- `_infer_cultural_context()` / instrumentation 推定の Python ハードコードを
  `config/semantic_rules.yaml` の宣言的ルールへ移管（既存 `_labels_from_rules` を再利用）
- `harmonic_ratio` / `percussive_ratio` / `spectral_bands` を条件キーとして解決可能にする
  （スカラは getattr で既に解決可。`spectral_bands` のネスト欄は `_feature_value` に getter 追記）
- 暫定閾値は手元データ（Portals/UZA/synth 5 本/R1 corpus）から引き、
  **「暫定・検証は Phase B/C」と doc とコメントに明記**（honesty 規律）
- config 二重コピー同期（`config/` ↔ `src/svp_rpe/config/`）
- `tests/test_semantic_layer.py` に orchestral/electronic 判定の回帰テスト追加

**実績（2026-06-24, PR #99）**: config 化は **振る舞い保存**で着手（旧出力を変えず
snapshot 不変）。当初の暫定 orchestral ルール（`harmonic_ratio>=0.78`）は **Phase B に defer**。
理由は実装中の検証で **harmonic_ratio が orchestral の判別子として不十分**と判明したため:
純合成テスト音は `harmonic_ratio=1.000`（本物管弦 Portals=0.813 を上回る）で、test corpus
synth 5 本すべてを誤って orchestral 判定した。倍音性だけでは「管弦」と「整数次倍音の
合成音」を分離できない。→ Phase B の閾値導出は harmonic 単独でなく多特徴（centroid 帯域・
percussive 比・dynamics 等）の組合せを校正データ上で検討する。

### Phase B — ジャンル校正コーパス構築

- **Phase A の知見を入力とする**: harmonic_ratio 単独は orchestral 判別に不十分
  （純合成=1.0）。多特徴の組合せと、本物アンカー（実 orchestral 録音）が必須

- 集めるジャンル（最小セット案）: orchestral / electronic-dance / rock / acoustic /
  hiphop / ambient — 各 N 曲（N は Phase A の暫定線の不確実性を見て決定、目安 5–10）
- プロンプト→ラベル対応表を manifest に記録
- ラベル準拠の耳チェック手順を通過したもののみ採用
- 物理特徴を `extract` で測り、ジャンル別の分布（散布図）から閾値を導出

**律速**: Suno 生成バッチ（人間作業）。ただし「今日の延長」で集まる軽さ。

#### Phase B-1 実績 — box-first harness（2026-06-25）

B-1 では閾値を確定せず、ジャンル校正コーパスを食う箱と分析器を先に実装する。
R1-audio の content-addressed box-first 方針と同じく、データ量産や Drive 取得は後続に
残し、manifest / replay / report の契約を固定する。

- `examples/calibration/genre/manifest.yaml` を seed とし、Portals/UZA の real anchor 2 本を
  measured-only で登録する。両ジャンルとも n=1 なので report は `insufficient` を出し、
  閾値候補は出さない。
- `svprpe genre-calibrate <manifest> --format text|json` が、ジャンル別 feature 統計と
  pair separability / threshold candidate を決定論的に出力する。
- `spectral_bands.<band>` を semantic rule condition key として解決可能にし、B-2 で
  magnitude 7 帯域を rule 条件へ使える入口を作る。既存 scalar key の挙動は不変。

#### Phase B-1b 実績 — misfire audit（2026-06-25）

B-1b では閾値を直さず、現行 `semantic_rules.yaml` の `cultural_context` /
`instrumentation` が校正 manifest をどう分類するかだけを計測する before ベースラインを
追加する。`svprpe genre-audit <manifest> --format text|json` は production と同じ
`load_config("semantic_rules")` + `_backfill_genre_sections` 経路でルールを適用し、
`genre_label` × predicted `cultural_context` の混同表と per-sample 予測を出す。

この audit は計器であって裁判官ではない。pass/fail や verdict は出さず、既知の
Portals 型 misfire（orchestral seed が `low_ratio > 0.4` により `bass-music` へ吸われる）
を B-2 前に固定する。`mismatch` は期待 context との交差がない sample を探すための
記述マーカーに留め、正解率や合否集計は出さない。

#### Phase B-2 実績 — brightness split for orchestral / bass-music（2026-06-25）

B-2 では B-1/B-1b の箱を使って、`low_ratio > 0.4` 単独で Portals 型の管弦素材が
`bass-music` に吸われる誤判定を是正する。実測上、orchestral は低域が厚いが暗く
（`high_ratio` 0.0001-0.0121）、electronic-dance は低域が厚く明るい
（`high_ratio` 0.0219-0.0494）。その中点として暫定 `high_ratio=0.017` を採用し、
厚い素材だけを明暗で二分する。

- `bass-music`: `{low_ratio_gt: 0.4, high_ratio_gt: 0.017}`
- `cinematic/orchestral`: `{low_ratio_gt: 0.4, high_ratio_lt: 0.017}`
- 既存の `valley_depth > 0.3` による `cinematic/orchestral` 経路は温存する。

この閾値は暫定であり、Q1-5 Ph2 で rule 族の **判別軸**（高域 brightness）を power 比
（`spectral_profile.high_ratio`）から magnitude `spectral_bands.brilliance` へ移すときに
再校正する。B-2 では `high_ratio` が必須欄で後方互換性が高いことを優先し、Optional な
`spectral_bands` にはまだ依存しない（B-3 で brilliance を主軸化、後述 Q1-5 Ph2 実測で
`low_ratio` ゲートは power 据え置き・`mid_ratio` は seed 未発火で繰越と確定）。

**残課題**: Phase C で本物アンカーを増やし、B-2 の 0.017 暫定線が real anchor でも
通用するかを検証する。Q1-5 Ph2 では magnitude ベースの brightness 指標（brilliance）へ
移行する（`low_ratio` ゲートは power 据え置き、`mid_ratio` は seed 未発火で繰越、後述実測参照）。

#### Phase B-3-rock 実績 — brilliance 3-way banding で rock を分離（2026-06-26）

3 ジャンル目として rock を追加（Suno 生成 5 本: classic/hard/indie/blues/punk、
耳チェック済、`examples/calibration/genre/manifest.yaml`）。今回は full sha256 と
prompt 本文を記録し、orchestral/EDM の prefix-only / PENDING 積み残しと同型の欠落を
rock では作らない方針とした。

**生成忠実度の所見**: key は 5/5 完全保存（E major / A minor / C major / E minor /
D major）、BPM は 4/5 正確。punk のみ extract=123.05（真値≈181、生 librosa
`beat_track` で start_bpm 120/160/180 すべて 181.45 を回復）で、Suno は prompt の
175 を忠実再現するが extractor がグリッド過小報告。しかも `bpm_octave_ambiguous=False`
で 1.42× を取りこぼした（R2-2 検出近傍 1.4-2.2× 内の miss 事例、要 follow-up）。

**分離の核心**: `spectral_bands.brilliance` 単独で 3 ジャンルが**重なりゼロの 3 バンド**に
分かれる。

| ジャンル | brilliance 実測レンジ | gap |
|---|---|---|
| cinematic/orchestral | 0.0134 – 0.0954 | |
| | | 0.0954 → 0.139 |
| **rock** | **0.139 – 0.1964** | |
| | | 0.1964 → 0.2119 |
| bass-music (EDM) | 0.2119 – 0.2508 | |

旧 B-3 の単一閾値 `brilliance=0.1537` は rock クラスタ中央を貫き、
classic/hard/blues（>0.1537）→ bass-music、indie/punk（<0.1537）→ orchestral に
**裂いていた**（audit で実証。`genre_label=rock` に expectation が無く mismatch すら
立たない死角も同時に発覚）。gap 中点で 2 線に置換（`genre-calibrate` の閾値候補
0.1172 / 0.204 と一致）:

- `cinematic/orchestral`: `{low_ratio_gt: 0.4, spectral_bands.brilliance_lt: 0.117}`
- `rock`（新規）: `{low_ratio_gt: 0.4, spectral_bands.brilliance_gt: 0.117, spectral_bands.brilliance_lt: 0.204}`
- `bass-music`: `{low_ratio_gt: 0.4, spectral_bands.brilliance_gt: 0.204}`
- `src/svp_rpe/calibration/audit.py` の `GENRE_CONTEXT_EXPECTATIONS` に `"rock": {"rock"}` を追加。

**回帰ゼロ**: orchestral 6 本は全て brilliance ≤0.0954（<0.117）、EDM 5 本は全て
≥0.2119（>0.204）でバンド内に残り、audit confusion は orchestral 6 / EDM 5 のまま
不変。rock 5 本は新バンドで rock に着地（mismatch=False）。

**補助軸と留保**: harmonic_ratio も rock（0.708-0.796）が orchestral（0.925-0.988）/
EDM（0.823-0.880）双方より低く（d=2.7-3.4）分離を補強するが、実アンカー uza(0.71)
と衝突するため**主軸は brilliance、harmonic は補助**とした。bands 欠落時の power
fallback（high_ratio）は rock/EDM の high_ratio が重なる（rock 0.018-0.042 vs EDM
0.022-0.049）ため 3-way 不可で 2 値据え置き＝**rock 判定は `spectral_bands` 必須**。
Phase C で本物 rock アンカーを足し、0.117/0.204 暫定線の generator バイアスを検証する。

#### Q1-5 Ph2 実測 — `low_ratio` ゲートは power 据え置き closeout、`mid_ratio` は評価不能で繰越（2026-06-28）

B-2/B-3 の `// Q1-5 Ph2 で再校正` 注記（rule 族を power 比から magnitude 7 帯域へ移す）
のうち、**残っていた low/mid_ratio** を seed corpus で `genre-calibrate` し、pair separability
を実測した。

**特徴量ごとの実効サンプル数（重要）**: `genre-calibrate` は非 null 値のみで比較する。
唯一の実アンカー `portals`（real orchestral）は `low_ratio`/`high_ratio` 等の scalar しか持たず
`mid_ratio` も `spectral_bands.*` も未計測のため、特徴量で n が異なる:

| 特徴量 | orchestral | rock | electronic-dance | 実アンカー grounding |
|---|---|---|---|---|
| `low_ratio`（power） | **n=6**（portals 含む） | n=5 | n=5 | あり（portals） |
| `mid_ratio`（power） | n=5（Suno のみ） | n=5 | n=5 | **なし（全 Suno）** |
| `spectral_bands.*`（mag） | n=5（Suno のみ） | n=5 | n=5 | **なし（全 Suno）** |

⚠️ **`low_ratio` ゲート所見だけが実アンカーで裏打ちされ、magnitude 判別軸
（`brilliance`/`low_mid`/`sub_bass`）と `mid_ratio` の所見は全ジャンル Suno 生成のみ**
（実アンカー grounding ゼロ）。後者は generator bias に対し未検証で、Phase C の実アンカー
投入は「ゲート境界の拡張」だけでなく **magnitude 判別軸の bias 検証**にも必須。

| 特徴量 | 3 低域厚ジャンル間 | Cohen's d | 役割 |
|---|---|---|---|
| `low_ratio`（power, <300Hz） | 全ペア **overlap** | 1.3–2.1 | **ゲート**（全 >0.4 で admit、判別しない） |
| `mid_ratio`（power, 300-4kHz） | 全ペア **overlap** | 1.6–2.7 | 補助、判別器でない |
| `spectral_bands.bass`（mag, 60-250Hz） | 全ペア **overlap** | 0.8–1.9 | magnitude 低域でも判別器でない |
| `spectral_bands.sub_bass`（mag, 20-60Hz） | rock/EDM のみ candidate、他 overlap | 0.8–2.2 | **部分的**（rock↔EDM のみ分離） |
| `spectral_bands.low_mid`（mag, 250-500Hz） | orchestral 対 rock/EDM が candidate、rock/EDM は overlap | 0.5–3.0 | **部分的**（orchestral を分離、rock↔EDM は不可） |
| `spectral_bands.brilliance`（mag, 6-20kHz） | 全ペア **candidate** | 3.3–7.0 | **唯一の全ペア判別器**（B-3 で magnitude 化済） |

**結論（Q1-5 Ph2: `low_ratio` ゲートは closeout、`mid_ratio` は評価不能で繰越）**:
高域 brightness は #91 で power が defective（power high_ratio 2-5% が magnitude 30-36% と
矛盾）と判明し B-3 で magnitude `brilliance` へ移行が**必須**だった。一方 low は性質が違う
（mid は後述の通り本 seed では評価対象に乗らない）:

1. power `low_ratio` は 3 低域厚ジャンルを全て admit（min>0.4）する**ゲート**であって
   判別器ではない（ジャンル間は overlap）。`mid_ratio` も判別軸としては overlap。
2. magnitude 低域に替えても、**3 ペア全てを分離する低域バンドは無い**。`bass` は全ペア
   overlap、`sub_bass` は rock↔EDM のみ、`low_mid` は orchestral を rock/EDM から分けるが
   rock↔EDM は overlap、と**いずれも部分的**。全 3 ペアを単独で分離するのは高域
   `brilliance`（d 最大 7）のみで、これは B-3 で既に判別軸として採用済み。
3. ゲートの本質は「低域厚 vs 非低域厚（general）」の境界だが、その境界を magnitude で
   再導出するには **general/非低域アンカー**が要る。現 seed は低域厚ジャンルのみで
   非低域素材を欠くため、`low_ratio>0.4` に対応する magnitude 閾値を校正できない。
4. **`mid_ratio` は本 seed では評価対象に乗らない**（評価不能であって「不要」ではない）。
   production の mid ルール（`perc.mid_focused` = `mid_ratio_min: 0.45`、`instr.mid_focused`
   = `mid_ratio_gt: 0.5`）に対し、seed の `mid_ratio` 実測は orchestral 0.224-0.437 /
   rock 0.166-0.314 / EDM 0.080-0.243 で**全て閾値 0.45/0.5 未満**＝mid-focused パスは
   一度も発火しない。低域厚ジャンルだけの seed では mid-focused/general 素材を欠くため、
   `mid_ratio` の power/magnitude 是非はそもそも測れない。

よって閉じられるのは **`low_ratio` ゲートの closeout のみ**（power 据え置き + ゲート境界の
magnitude 化は general アンカー待ち）。`mid_ratio` は **据え置くが「評価不能のため繰越」**
と明示する（mid-focused/general アンカーが入るまで migration の是非を判断しない）。
Q1-5 Ph2 の全ペア判別軸は `brilliance`（B-3 完了）で実質完了。**ただし** `low_mid`/`sub_bass`
が部分的な genre signal を持つことは実データで確認できたので、これらは「捨てる」のではなく
**Phase C の補助軸候補**として残す（brilliance を主軸に、rock↔EDM など overlap が残る
ペアの補強に使えるか、本物アンカーで検証する）。残課題は「Phase C で general/非低域
（および mid-focused）アンカーを足し、ゲート境界の magnitude 化・`mid_ratio` 是非・補助軸の
採否を検証」であり、現 seed での power→magnitude の機械的な書き換えではない。回帰固定:
`tests/test_genre_calibration.py::test_low_mid_power_bands_stay_power_q1_5_ph2`（`mid_ratio` が
seed で production 閾値 0.45 に届かない＝未発火も assert）。#106 と同型の「測って変更不要/
評価不能と判明」型 finding。

### Phase C — 本物アンカー検証

- 実曲を各ジャンル数曲、検証専用アンカーとして content-addressed 登録
- Suno で引いた閾値が本物アンカーで通用するか測定、bias の大きい指標（例: dynamics）を補正

#### Phase C 着手 — 実 J-POP 3 本で generator bias を初観測（2026-06-28）

repo 初の**実 grounding を持つ `spectral_bands` アンカー**を取得（それ以前は magnitude 軸が
全て Suno 由来＝Q1-5 Ph2 の Suno-only caveat）。ユーザー提供の実録音 3 本（J-POP dance-pop:
安室奈美恵 / SPEED / 湘南乃風、M4A→WAV→`extract`）を `genre_label: j-pop`・`generator: real`
で manifest に登録（measured + full sha256、audio は repo 外＝再配布なしで licensing 非該当）。

| 曲 | 実サブジャンル | low_ratio | brilliance | dyn_range_db | LUFS |
|---|---|---|---|---|---|
| 安室 Don't wanna cry | 90s dance-pop/TK | 0.603 | 0.162 | 6.97 | -11.0 |
| SPEED Body & Soul | dance/teen pop | 0.485 | 0.174 | 8.32 | -6.1 |
| 湘南乃風 バブル | reggae/dancehall pop | 0.469 | 0.150 | 6.86 | -5.7 |

**所見**:
1. **`low_ratio>0.4` ゲートは本物で通用** — 3 本とも >0.4。#108 の「ゲートは power で健全」
   closeout を本物データが裏付け。
2. **本物 dance-pop は全部「rock」帯に着地＝Suno EDM の over-brightening を実証** — j-pop の
   brilliance は 0.150–0.174 で Suno rock 帯（0.117–0.204）に入り、**Suno EDM 帯（≥0.2119）に
   届かない**（j-pop max 0.174 < Suno EDM min 0.212、重なりゼロ）。ダンス系の実曲なのに Suno EDM
   の明度に達しない＝**Suno は EDM を本物のダンス曲より明るく描く**。現行ルールは本物 dance-pop を
   `rock` に誤分類する。これは Portals が harmonic 軸を崩したのと同型の、brilliance 軸での
   generator-bias 実証（B-3 の閾値は Suno スプレッド依存）。
3. **交絡: マスタリング音圧** — brilliance はジャンルだけでなく LUFS（-5.7〜-11.0）と
   マスタリング世代を混ぜて測る。本物 orchestral/classical（静か・広ダイナミクス）は別挙動の
   はずで、校正に loudness 正規化を入れるべきかという方法論的論点が浮上（要 follow-up）。

**含意（Phase C への引き継ぎ）**: (a) これは新ジャンル `j-pop` の seed であると同時に、
EDM 閾値の bias 証拠でもある。(b) **既存 3 ジャンル（orchestral/rock/EDM）の閾値を本物で
検証するには、それぞれの本物録音が別途必要**（この 3 本は J-POP で、3 ジャンルの直接検証では
ない）。(c) brilliance の loudness 正規化と、本物コーパスでの閾値再校正が次の作業。
回帰固定: `tests/test_genre_calibration.py::test_real_jpop_anchors_below_suno_edm_brightness`。

#### Phase C matched-pair — 本物 J-POP × Suno J-POP で generator bias を genre-controlled 実証（2026-06-28）

#109 の「本物 J-POP vs Suno EDM」はジャンル交絡があり、brilliance 差が bias かジャンル差か
切り分けられなかった。これを潰すため、本物 3 本に**同ジャンル・同キー・同 BPM を狙った Suno**
を 1 本ずつ生成し（プロンプトから実在人物名 "Tetsuya Komuro" / レゲエ奏法語 "skank" を
Suno のアーティスト名フィルタ回避で除去）、`genre_label: j-pop-suno`・`generator: suno` で登録。
matched-pair なら残差は **real vs Suno の純粋な生成器指紋**。

**制御の成立**: key/mode 3/3 一致（G minor / G major / D# major）、BPM 2/3 一致（99 / 136、
SPEED のみ 123→129）＝ジャンル・キー・テンポは揃った。

**本物→Suno の系統差（3 ペア全て同方向）**:

| 指標 | 安室 Δ | SPEED Δ | バブル Δ | 向き |
|---|---|---|---|---|
| **brilliance** | +0.092 | +0.111 | +0.059 | Suno が明るい ⬆️ |
| spectral_centroid | +990 | +829 | +268 | Suno が明るい ⬆️ |
| low_ratio | +0.104 | +0.116 | +0.369 | Suno が低域厚い ⬆️ |
| mid_ratio | −0.134 | −0.148 | −0.363 | Suno が中域薄い ⬇️ |

**結論**:
1. **over-brightening を genre-controlled で確定** — 同じ J-POP・同キーなのに Suno は brilliance を
   +0.06〜0.11 押し上げる。本物 j-pop（brilliance mean 0.162, max 0.174＝rock 帯）と
   j-pop-suno（mean 0.250, min 0.209＝EDM 帯）は **`genre-calibrate` で candidate 分離
   （d=3.76, 重なりゼロ）**。#109 の「Suno は EDM を明るく描く」が、ジャンル統制下で
   「Suno はジャンルを問わず明るく描く＝生成器全般の指紋」と強化された。
2. **スペクトル形状の指紋＝スマイリー EQ** — Suno は一貫して 低域↑・中域↓・高域↑（low_ratio +,
   mid_ratio −, brilliance/centroid +）。本物の中域のある自然なバランスをドンシャリに誇張する。
   4 軸すべて同方向＝強い系統バイアス。
3. **方向の定まらない軸**（harmonic_ratio / dynamic_range_db）はマスタリング・楽曲依存。特に
   dynamics は本物の音圧戦争（ブリックウォール）を Suno が一貫再現しない（loudness 交絡と整合）。

**含意**: (a) Suno 由来の brilliance 閾値（B-3 の 0.117/0.204）は **+0.06〜0.11 の系統オフセット**を
含む可能性が高く、本物校正では下方補正が要る。(b) 校正コーパスを Suno で量産する戦略（Tier 2）は
**この指紋を bias として明示計上**しないと閾値が Suno 方向へ系統的にずれる。(c) 次は本物
orchestral/rock/EDM でも同じ matched-pair を取り、指紋がジャンル横断で一定か（補正係数化できるか）
検証する。回帰固定:
`tests/test_genre_calibration.py::test_suno_jpop_overbrightens_vs_real_matched_pair`。

---

## 5. リスクと留意点

| リスク | 対処 |
|---|---|
| 生成器バイアス（Suno≠本物） | 本物アンカーで bias を測定・補正（Phase C）。AI 音楽分析が目的なら許容 |
| Suno のラベル不準拠（key/genre 流れ） | 採用前の耳チェックを手順化 |
| 暫定閾値の過信 | Phase A/B-2 の閾値は「暫定」と明記、回帰テストは方向性のみ固定（精密値は固定しない） |
| 学習モデルへの誘惑 | Tier 3 は `learned_models_policy.md` の隔離原則厳守。rule evidence に混入させない |
| ルールベースの原理的限界 | `estimation_disclaimer`（意味層は真値でない）を維持。本計画は解像度向上であり意味理解ではない |

---

## 6. 次アクション

1. 本計画を PR でマージ（docs single）
2. **Phase A の Design Memo を起案**（AGENTS.md §1 形式）→ Codex 実装 or Claude fix-up
3. Phase B の Suno 生成バッチ仕様（ジャンル・曲数・プロンプト→ラベル）を確定
