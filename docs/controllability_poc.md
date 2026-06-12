# Controllability PoC Planning — 制御トラックの概念実証計画

**Status**: PLANNING
**Created**: 2026-06-02
**Upstream**: [`composition_score_product_brief.md`](composition_score_product_brief.md)（プロダクト定義）
**Relates to**: [`ai_music_daw_vision.md`](ai_music_daw_vision.md), [`roadmap_goal1.md`](roadmap_goal1.md)（観測トラック）, [`validation.md`](validation.md)

---

## 本ドキュメントの位置付け

本ドキュメントは **制御トラック（control track）の概念実証計画** である。
観測トラック（[`roadmap_goal1.md`](roadmap_goal1.md) の Q 系列）とも、Composition
Score 実装計画（[`composition_poc_planning.md`](composition_poc_planning.md) の C 系列）
とも別系統の、新しい設計軸を立ち上げる。

フェーズ ID は **K 系列**（K0, K1, …）を用いる。

```text
Q 系列 (roadmap_goal1.md)  — 音楽を「測る」          : 観測トラック
C 系列 (composition_poc)   — Score を「書く / 変換する」: 作曲言語トラック
K 系列 (本文書)            — パラメータで「操作する」  : 制御トラック ← New
```

---

## 1. この計画を生んだ設計判断（壁打ち結論）

2026-06-02 の方向性壁打ちで、プロジェクトの重心に関する読み替えが確定した。

> **意味層・物理層のパラメータは「正しく演奏されたかを評価する値」ではない。
> 操作可能なパラメータとして出力を制御するための値である。**
> **評価する値ではなく、制御する値。**

この読み替えにより、成功基準が丸ごと入れ替わる。

| | 測定フレーム（旧） | 制御フレーム（新） |
|---|---|---|
| パラメータの正体 | 体温計（出力を測る） | ハンドル（出力を動かす） |
| 良いパラメータとは | 精度高く再現される | 回すと出力が動く（grip がある） |
| 成功基準 | accuracy（指定に一致） | grip / 単調性 / 直交性 / 再適用性 |
| 監査・ΔE の役割 | 合否を出す裁判官（主役） | 効きを返す計器（制御ループの従） |
| 作曲とは | — | どのツマミをロックし、どれを開けるかの設計 |

中心命題を一文で固定する:

> **このプロダクトは「AI 音楽を意味・物理・構造の層で操作するコントロール
> サーフェス」である。パラメータは効くツマミであり、監査はその効き具合を返す
> 計器であり、作曲とは "どのツマミをロックしどれを開けるか" を設計することである。**

### 1.1 「演奏 vs シンセ」軸の解消

「正確性 vs 創造性」は一本のダイヤルではない。独立した多数のツマミがあり、
**どれをロック（不変量）しどれを開ける（自由帯）かのパターン**が作曲である。

- 全ツマミをロック → 優秀なシンセ（忠実だが死んでいる）
- 全ツマミを開放 → AI の幻覚（生命はあるが「あなたの曲」ではない）
- **一部だけロックし残りを開ける → 演奏**

不変量＝ロックしたフェーダー、自由帯＝アンロックのフェーダー。すべて一つの
コントロールサーフェス上にある。この計画はそのサーフェスを実在させる第一歩。

---

## 2. PoC が証明すべき唯一のこと

**「制御パラメータには grip があるか」＝ ツマミを回すと出力が実際に動くか、を測れること。**

制御フレームは「ツマミが出力に対して権限を持つ」を暗黙の前提にしている。
だが Suno のようなゆるい結合では、**回しても何も起きない死んだツマミ**が混じる。
**ツマミが繋がっていないコックピットは、測定ツールより質が悪い**（操縦している
気になるだけ）。よって制御トラックの Q0 にあたる最初の PoC は、**grip の実測**で
なければならない。

PoC の問いは「grip を最大化する」ではなく「**grip は測れるのか / 効果量が信号
として立つのか**」。これが言えて初めて、効くツマミの地図（K1）に進める。

---

## 3. grip の定義（DD-B）

生成器は確率的なので、grip は **A/B コントラストの効果量**で定義する。
単発生成（n=1）は信号とノイズを分離できず無意味。**反復必須。**

ツマミ `k` を low / high の 2 水準に設定し、各水準で `R` 回生成する。
各サンプルから対応する観測量 `x`（センサー）を RPE 抽出で得る。

```text
grip(k) = (mean(x_high) - mean(x_low)) / pooled_sd(x)
```

（Cohen's d 型の効果量。pooled_sd は両群の標準偏差をプール。）

判定:

| grip の効果量 | 符号 | 分類 | 意味 |
|---|---|---|---|
| 大（\|d\| が大） | 期待方向と一致 | **tight** | 効くツマミ。操作パネルに残す |
| 小だが有意 | 期待方向と一致 | **loose** | ぼんやり効く。表現改善余地 |
| ≈0 または符号逆 | — | **dead** | 死んだツマミ。捨てるか prompt 表現を再設計 |

MusicGen（安価・seed 再現）なら 3–5 水準のスイープに拡張して**応答勾配（slope）**
も取れるが、最小方法実証（K0）では 2 水準コントラストに固定する。

**連続ツマミとカテゴリツマミで grip 統計を分ける**。上の符号付き効果量は
**連続観測**（bpm / brightness / active_rate_target / valley_depth）向け。
`key` / `mode` のような**カテゴリツマミ**は効果量に乗らないので、
「**要求ターゲットへの per-sample 一致スコア**」（`mir_eval.key.evaluate` 由来、
∈[0,1]、[`validation.md`](validation.md) §2 と同方式）の平均＝**一致率**で grip を測る。
`key_confidence`（検出自信度）は**使わない** — 自信満々に外した key を grip 成功と
誤判定するため。一致率が偶然水準なら dead、高ければ tight。

一致率の閾値（K1 で確定）: mir_eval weighted score のランダム期待値は 24 キー一様で
約 0.08。チャンス水準の ~4 倍 **0.3 を loose 下限**、操作パネルに残せる **0.7 を
tight 下限**とする（`src/svp_rpe/control/grip.py` の `MATCH_TIGHT_MIN` /
`MATCH_LOOSE_MIN`）。

**ゼロ分散時の挙動（決定論のため明示）**。丸めにより群内分散が 0 になり
`pooled_sd = 0` になりうる（例: low の全サンプルが同一 BPM、high も同一 BPM）。
`inf` / `NaN` を避け snapshot を安定させるため、`ε = 1e-9` として次を規定する:

| 条件 | 分類 | grip 値 |
|---|---|---|
| `pooled_sd < ε` かつ `\|mean_high − mean_low\| < ε` | **dead** | `0.0`（分離なし） |
| `pooled_sd < ε` かつ `\|mean_high − mean_low\| ≥ ε` | **saturated tight** | 符号付き番兵値 `±GRIP_SATURATED`（固定定数、例 `999.0`） |
| `pooled_sd ≥ ε` | §3 の効果量で分類 | `(mean_high − mean_low) / pooled_sd` |

これにより出力は常に有限・決定論で、同 fixture → 同 grip が保証される。
カテゴリツマミ（一致率）は除算が無いので本規則の対象外。

---

## 4. 設計判断ログ

### 確定（Claude 設計判断、異議あれば改訂可）

**DD-A 決定論契約の守り方** — 生成（MusicGen / Suno）はリポジトリ外・非決定論。
よって **grip 計算の入力となる数値 fixture（サンプルごとの RPE 特徴量 JSON）を
コミット**し、grip 計算（効果量算出）を in-repo の決定論・単体テスト対象とする。
**音源ファイル自体はコミットしない**（[`validation.md`](validation.md) §8 の
「実音源は ignore、サマリのみコミット」方針と一致）。MusicGen の seed 再現性は
あくまでローカル再生成の利便であり、リポジトリの決定論契約は「fixture → grip 計算」
区間で担保する。

**DD-B grip 指標** — §3 の A/B コントラスト効果量。反復必須。

**DD-C 対角のみ** — PoC は「ツマミ i → 観測 i」の grip だけ測る。直交性
（ツマミ i が観測 j を動かさないか、N×N クロス行列）は K3（follow-up）。

**DD-D センサーのあるツマミに限定** — 観測チャネルを持つツマミのみ対象。
`semantic.core`（"introspective night drive" 等）のように対応する RPE 観測量が
無いツマミは PoC 対象外。物理寄りツマミを先行。

**DD-E 新規トラック** — 本 docs を新設し K 系列として独立管理。観測（Q）・作曲（C）
とは別系統。

### ユーザー判断（2026-06-02 壁打ちで確定）

**G1 生成器 = 両方・段階的** — まず MusicGen で grip 測定ハーネスと方法論を安価に
確立（K0–K1）→ Suno/Udio 級で少数バッチを手動生成し転移を確認（K2）。
方法の検証（自動）とドメイン妥当性（手動）を分離する。

**G2 範囲 = 最小方法実証から** — 最初の PoC（K0）は 1–2 ツマミ × 2 水準 × ~5 反復。
「grip は測れるのか」だけを最速で証明し、スケールは方法が立証されてから。

---

## 5. フェーズ構成（K 系列）

### K0: 最小方法実証 — grip は測れるか

**目的**: grip 測定ハーネスを立て、効果量が信号として立つことを 2 ツマミで示す。

| 項目 | 内容 |
|---|---|
| 生成器 | MusicGen（ローカル、seed 固定） |
| ツマミ | `physical.bpm`（tight 予想） / `physical.brightness`（loose 予想）の 2 つ |
| 水準 | 各 2 水準（例 bpm: 90 / 140、brightness: dark / bright） |
| 反復 | 各水準 R=5（seed を変えて 5 サンプル） |
| センサー | bpm → 観測 BPM、brightness → `spectral_profile.brightness`（帯域比） |
| 成果物 | `scripts/measure_grip.py` / 数値 fixture / grip 表（2 ツマミ分） |

**完了基準**:
- `bpm` ツマミが大きい効果量（期待方向）を示す＝**grip が実在し測れる**ことの証明
- grip 計算が fixture 入力に対して決定論（同 fixture → 同効果量、snapshot test green）
- 2 ツマミの grip 分類（tight/loose/dead）が表として出力される

> K0 のゴールは grip の大小ではなく「**測定方法が機能する**」こと。たとえ
> brightness が dead と出ても、それが安定して dead と測れれば K0 は成功。

### K1: 代表マップ初版 — 効くツマミの地図

**Status**: DONE (2026-06-12, 決定論的演奏者リファレンス) — 結果は §5.1。
MusicGen が重依存のため、参照生成器を C4 の決定論的シンセ演奏者
（`scripts/compose_e2e_demo.perform` + seed 駆動 bpm ジッター）で代替した。
MusicGen / Suno 実測での地図更新は K2 に引き継ぐ。

**目的**: ツマミを ~5 個に広げ、tight/loose/dead のスペクトルを張る。

候補ツマミ（§6 参照、すべて現行 `PhysicalLayer` に存在するフィールド）:
`bpm`, `key/mode`, `brightness`, `active_rate_target`, `valley_depth_target`。
`density_curve` はスキーマ未定義のため K1 では扱わず、フィールド追加後（§8）の
候補とする。

**完了基準**: 5 ツマミの grip 分類が出揃い、「操作パネルに残すツマミ / 捨てる
ツマミ / 表現を直すツマミ」の初版判断が docs に記録される。

#### 5.1 K1 結果 — grip 代表マップ初版（決定論的演奏者、R=5、2026-06-12）

再現: `python scripts/build_k1_fixture.py`（fixture 再生成、約 3 分）→
`python scripts/measure_grip.py --fixture examples/control/k1/synth_performer_rpe_fixture.json`。
コミット済み成果物は `examples/control/k1/`（fixture / `expected_grip.json` /
`grip_map.md`）。fixture → grip は決定論で snapshot test 固定。

| ツマミ | センサー | grip | 分類 | 初版判断 |
|---|---|---|---:|---|
| `bpm` | 観測 BPM | 1.61 | **tight** | **残す**。90→140 指定で観測 90.4→127.3 と動く（高水準の検出は低めに出る — センサー側の癖として記録） |
| `key` | 要求 key 一致率 | 1.00 | **tight** | **残す**。C major / F# minor とも全サンプル一致 |
| `brightness` | `spectral_centroid`（正規センサー） | 223.5 | **tight** | **残す**。センサー再設計済み（下記） |
| `brightness_band_ratio`（legacy） | `spectral_profile.brightness`（帯域比） | 0.00 | **dead** | 旧センサーの盲目の証拠として保持 |
| `active_rate_target` | active rate | −1.14 | **dead** | **接続する**。演奏者がこのフィールドを読まない=繋がっていないツマミの実例 |
| `valley_depth_target` | novelty valley depth | 0.06 | **dead** | **接続する**。同上（§6 の dead 予想どおり） |

主要な発見 — **dead には 2 種類ある**:

1. **ツマミが死んでいる**（`active_rate_target` / `valley_depth_target`）: 生成側に
   そのフィールドを読む経路が無い。grip 測定が「繋がっていないコックピット」を
   正しく検出した（§2 の存在意義そのもの）。
2. **センサーが盲目**（旧 `brightness` 帯域比）: 帯域比センサー（4kHz 以上の
   エネルギー比）は 0 のまま動かないが、**同一サンプル**を `spectral_centroid` で
   観測すると 957→1236 Hz と明確に動く（grip 223.5）。ツマミは生きており、
   センサーの観測帯が合っていない。C4（`composition_poc_report.md` §4）の発見の
   追試にあたる。
   → **解決済み（2026-06-12 センサー再設計）**: brightness の正規センサーを
   `spectral_centroid` へ変更し、`semantic_rules.yaml` に dark（≤1200 Hz）/
   bright（≥2500 Hz）の明示帯を新設（既存 `hyp.melancholic` の centroid_max 2000
   と無矛盾な絶対校正）。audit 針・semantic_ci compare・本地図の 3 消費者を
   centroid に統一し、旧帯域比はフィールドとして残置（dark/bright 判定には不使用、
   `docs/metrics.md` 参照）。合成 5 曲の por_surface には `dark` ラベルが付くように
   なる — サイン波スタックは絶対値として dark のため妥当。

本マップは決定論的演奏者（接続が既知）に対する測定なので、tight/dead の正解が
わかっている状態でハーネスが正しく分類できることの検証を兼ねる。確率的生成器
（MusicGen / Suno）での実地図は K2 で取得する。

### K2: Suno 転移検証

**目的**: K1 が tight/loose と判定したツマミを Suno/Udio 級で手動少数生成し、
grip 分類が転移するかを確認する。

**完了基準**: 少なくとも 2 ツマミについて MusicGen の grip 分類と Suno 級の
観測が一致 / 不一致を表で記録。製品の本命生成器での grip 妥当性メモを残す。

### K3（follow-up）: 直交性行列

ツマミ i が観測 j を動かさないか（レイヤー独立性）を N×N で測る。
PoC の対角 grip が立証されてから着手。

---

## 6. パラメータ × センサー対応表（DD-D）

PoC が扱えるのは「観測チャネルを持つツマミ」のみ。

| ツマミ（Composition Score） | センサー（RPE 観測量） | grip 予想 | フェーズ |
|---|---|---|---|
| `physical.bpm` | 観測 BPM（`PhysicalRPE`） | tight | K0 |
| `physical.brightness` | `spectral_profile.brightness`（帯域比 high/(low+mid+high)） | loose | K0 |
| `physical.key` / mode | 要求 key/mode への一致スコア（`mir_eval.key.evaluate`、∈[0,1]、validation §2 方式。`key_confidence` ではない） | tight（一致率） | K1 |
| `physical.active_rate_target` | active rate（密度プロキシ） | medium | K1 |
| `physical.valley_depth_target` | novelty valley depth | dead 予想 | K1 |
| `physical.stereo_width` | stereo correlation / width | 未知 | K1 以降 |
| `physical.density_curve` | RMS / onset 密度の時間勾配 | medium | **要スキーマ追加**（K1 後） |
| `semantic.core` | 対応観測なし | — | **対象外** |

> **スキーマ制約**: K1 のツマミは現行 `PhysicalLayer`（`src/svp_rpe/compose/models.py`、
> `extra="forbid"`）が定義するフィールドに限る — `bpm` / `key` / `time_signature` /
> `active_rate_target` / `valley_depth_target` / `brightness` / `stereo_width`。
> `density_curve` は**スキーマ未定義**のため、ツマミ化するには先に `PhysicalLayer` への
> フィールド追加が必要（§8 参照）。それまで K1 の「密度」枠は既存の
> `active_rate_target` を用いる。

---

## 7. Codex 向け Design Memo（K0、ペースト可）

````markdown
# Design Memo: K0 — grip 測定ハーネス最小実証

## Phase
controllability_poc.md K0（制御トラックの概念実証 第一歩）

## Goal
MusicGen 出力に対し、Composition Score の物理ツマミ 2 個（bpm / brightness）を
2 水準 × R 反復で振り、A/B コントラストの効果量として grip を算出する最小
ハーネスを作る。「grip は測れる」を bpm ツマミで実証する。

## Acceptance Criteria
- [ ] `scripts/measure_grip.py` が、ツマミ・水準・反復数・生成済みサンプルの
      RPE 特徴量 fixture を入力に、ツマミごとの効果量 grip を出力する
- [ ] grip 計算が fixture に対して決定論（同入力 → 同出力）で、snapshot test が green
- [ ] bpm ツマミが大きい効果量（期待方向）を示すことを fixture で確認
- [ ] grip 分類（tight/loose/dead）の閾値ロジックが単体テストされている
- [ ] 音源ファイルはコミットしない。数値 fixture（per-sample 特徴量 JSON）のみコミット

## Implementation Approach
- 新規 `src/svp_rpe/control/`（または既存 eval 配下）に grip 計算純関数を置く:
  `grip_effect_size(low: list[float], high: list[float]) -> float`（pooled SD の
  Cohen's d 型）と `classify_grip(d: float, expected_sign: int) -> Literal["tight","loose","dead"]`
- 生成（MusicGen 実行）と RPE 抽出は **ハーネス外の前段**とし、その出力を
  数値 fixture（`examples/control/k0/*.json`）として受け取る。grip 計算は fixture のみ依存
- センサーは既存 RPE 抽出を再利用: bpm → 観測 BPM、brightness →
  `spectral_profile.brightness`（帯域比。`spectral_centroid` ではない —
  `semantic_rules.py` が brightness ラベルに使うのはこの帯域比フィールド）
- 決定論契約は「fixture → grip」区間で担保（DD-A）

## Risks
- MusicGen 統合自体は重い。K0 では「生成は前段・fixture は手動投入可」とし、
  ハーネスは fixture 駆動に限定して MusicGen 自動実行をブロッキング要件にしない
- R=5 は効果量推定に小さい。K0 は方法実証目的なので可。K1 で再検討
- spectral centroid の絶対値はチューニング依存。grip は群間差なので絶対値非依存だが
  正規化方針を明記すること

## Test Strategy
- 単体: `grip_effect_size` の既知入力（分離した 2 群／重なる 2 群）での値、
  `classify_grip` の閾値境界、符号逆ケースの dead 判定
- 単体（ゼロ分散、§3 規則）: 群内分散 0 かつ平均一致 → dead (`0.0`)、
  群内分散 0 かつ平均差あり → saturated tight (`±GRIP_SATURATED`)。`inf`/`NaN` を
  返さず有限・決定論であることを assert
- 回帰: 固定 fixture → 固定 grip 表の snapshot
- 既存テストへの影響: なし（新規モジュール、既存 RPE/eval 不変）

## Scope
- IN: `src/svp_rpe/control/`（新規）, `scripts/measure_grip.py`, `examples/control/k0/`,
  `tests/test_grip.py`
- OUT: `rpe/`, `eval/scorer_*`, `compose/` の既存ロジック（変更しない）

## Allowed Dependencies
なし（numpy は既存依存で足りる。MusicGen は本 Memo の自動実行要件に含めない）
````

---

## 8. 未解決の設計判断（次に詰める候補）

- ~~**grip 閾値の数値**~~ → **確定**: 連続ツマミは \|d\|≥0.8 tight / 0.2–0.8 loose /
  <0.2 dead（K0 で `GRIP_TIGHT_MIN`/`GRIP_LOOSE_MIN` として実装済み）、カテゴリツマミは
  一致率 ≥0.7 tight / 0.3–0.7 loose / <0.3 dead（K1、§3 参照）。確率的生成器の実データ
  （K2）で再校正の余地あり
- **MusicGen 統合の実体**: K0 を fixture 駆動で逃がした後、K1 で生成を自動化する際の
  バックエンド設計（[`learned_models_policy.md`](learned_models_policy.md) の optional
  extra 隔離方針に従うか）
- **`density_curve` のスキーマ追加可否**: 現行 `PhysicalLayer` は `extra="forbid"` で
  `density_curve` を持たない（ブリーフ §6 の example YAML とは乖離）。ツマミ化するなら
  `PhysicalLayer` へのフィールド追加 + ブリーフ整合が前提。K1 は既存 `active_rate_target`
  で密度枠を埋め、`density_curve` は本判断の決着後に回す
- **density_curve のセンサー定義**: 時間勾配をどの窓・どの統計量で取るか（スキーマ追加後に確定）
- **K2 の Suno 手動運用**: 生成バッチの記録様式（manifest テンプレート、何サンプルで
  転移を「確認」とみなすか）
- **fixity 型の導入**: grip 地図が固まった後、CompositionScore にロック/アンロックの
  型（fixity）を持たせるスキーマ再設計（制御サーフェス化の本丸、本 PoC の下流）

---

## 9. 設計ドキュメント索引への登録

本 docs 新設に伴い、以下 2 箇所に 1 行追加すること（[`CLAUDE.md`](../CLAUDE.md)
ドキュメント管理ポリシー）:

- `CLAUDE.md` 設計ドキュメント索引表
- `README.md` 設計ドキュメント表
