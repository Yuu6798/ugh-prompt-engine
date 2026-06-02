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
| センサー | bpm → 観測 BPM、brightness → spectral centroid |
| 成果物 | `scripts/measure_grip.py` / 数値 fixture / grip 表（2 ツマミ分） |

**完了基準**:
- `bpm` ツマミが大きい効果量（期待方向）を示す＝**grip が実在し測れる**ことの証明
- grip 計算が fixture 入力に対して決定論（同 fixture → 同効果量、snapshot test green）
- 2 ツマミの grip 分類（tight/loose/dead）が表として出力される

> K0 のゴールは grip の大小ではなく「**測定方法が機能する**」こと。たとえ
> brightness が dead と出ても、それが安定して dead と測れれば K0 は成功。

### K1: 代表マップ初版 — 効くツマミの地図

**目的**: ツマミを ~5 個に広げ、tight/loose/dead のスペクトルを張る。

候補ツマミ（§6 参照、すべて現行 `PhysicalLayer` に存在するフィールド）:
`bpm`, `key/mode`, `brightness`, `active_rate_target`, `valley_depth_target`。
MusicGen 上で grip 表を初版化する。`density_curve` はスキーマ未定義のため
K1 では扱わず、フィールド追加後（§8）の候補とする。

**完了基準**: 5 ツマミの grip 分類が出揃い、「操作パネルに残すツマミ / 捨てる
ツマミ / 表現を直すツマミ」の初版判断が docs に記録される。

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
| `physical.brightness` | spectral centroid | loose | K0 |
| `physical.key` / mode | key 検出スコア | tight | K1 |
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
- センサーは既存 RPE 抽出を再利用: bpm → 観測 BPM、brightness → spectral centroid
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

- **grip 閾値の数値**: tight / loose / dead を分ける効果量の境界値（K0 の実データを
  見てから確定。仮に \|d\|>0.8 tight / 0.2–0.8 loose / <0.2 dead）
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
