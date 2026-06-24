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

**受け入れ条件**: Portals 相当の特徴（harmonic 高・centroid 中低）が
`bass-music` 単独でなく orchestral/cinematic を含むようになる。既存テスト全 pass。

### Phase B — ジャンル校正コーパス構築

- 集めるジャンル（最小セット案）: orchestral / electronic-dance / rock / acoustic /
  hiphop / ambient — 各 N 曲（N は Phase A の暫定線の不確実性を見て決定、目安 5–10）
- プロンプト→ラベル対応表を manifest に記録
- ラベル準拠の耳チェック手順を通過したもののみ採用
- 物理特徴を `extract` で測り、ジャンル別の分布（散布図）から閾値を導出

**律速**: Suno 生成バッチ（人間作業）。ただし「今日の延長」で集まる軽さ。

### Phase C — 本物アンカー検証

- 実曲を各ジャンル数曲、検証専用アンカーとして content-addressed 登録
- Suno で引いた閾値が本物アンカーで通用するか測定、bias の大きい指標（例: dynamics）を補正

---

## 5. リスクと留意点

| リスク | 対処 |
|---|---|
| 生成器バイアス（Suno≠本物） | 本物アンカーで bias を測定・補正（Phase C）。AI 音楽分析が目的なら許容 |
| Suno のラベル不準拠（key/genre 流れ） | 採用前の耳チェックを手順化 |
| 暫定閾値の過信 | Phase A の閾値は「暫定」と明記、回帰テストは方向性のみ固定（精密値は固定しない） |
| 学習モデルへの誘惑 | Tier 3 は `learned_models_policy.md` の隔離原則厳守。rule evidence に混入させない |
| ルールベースの原理的限界 | `estimation_disclaimer`（意味層は真値でない）を維持。本計画は解像度向上であり意味理解ではない |

---

## 6. 次アクション

1. 本計画を PR でマージ（docs single）
2. **Phase A の Design Memo を起案**（AGENTS.md §1 形式）→ Codex 実装 or Claude fix-up
3. Phase B の Suno 生成バッチ仕様（ジャンル・曲数・プロンプト→ラベル）を確定
