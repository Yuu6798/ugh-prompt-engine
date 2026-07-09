# Controllability PoC Planning — 制御トラックの概念実証計画

**Status**: K0–K2 / K3-1 / K3-1b / K3-2a 完了 — K3-2b の設計指示は MusicGen で充足済み
（§5.5、`musicgen_backend.md` §7.4）。残はフル **Suno** 行列のみ（生成バッチ人手律速）
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
→ **K3-1 で解消**（2026-07-01、§5.3: DCI/MIG の効果量再定式化 +
決定論的演奏者リファレンス実測。Suno 実測は K3-2）。

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

#### 5.2 K2 結果 — 本物 Suno での bpm/brightness grip（2026-06-29）

再現: `python scripts/measure_grip.py --fixture examples/control/k2/suno_rpe_fixture.json`。
コミット済み成果物は `examples/control/k2/`（`suno_rpe_fixture.json` /
`expected_grip.json` / `README.md`）。fixture → grip は決定論で
`tests/test_grip.py::test_k2_suno_fixture_snapshot_bpm_and_brightness_transfer` が固定。
音源は Suno 生成 16 曲（2 ツマミ × 2 水準 × 4 反復、`audio_sha256` で provenance、repo 外）。

| ツマミ | センサー | mean low | mean high | grip | 分類 | K1（玩具）|
|---|---|---:|---:|---:|---|---|
| `bpm` | 観測 BPM | 117.8 | 138.4 | **1.61** | **tight** | 1.61 tight |
| `brightness` | `spectral_centroid` | 2320.7 | 2686.7 | **0.86** | **tight** | 223.5 tight |

**転移の結論**: K1 で tight だった 2 ツマミは**本物 Suno でも tight に転移**。製品級の
ゆるい確率的生成器でも、bpm/brightness は「回すと出力が動く」効くツマミであることを確認。

**手触りで見えた 2 つの計器・生成器の癖**:

1. **bpm は素朴センサーでも tight だが、prior アトラクタが grip を圧縮している**。
   「90 BPM」指定の low クリップは真テンポ ~90–95 だが、抽出器の既定 prior(120) が
   3/4 を ~123–125 に引き上げ（`start_bpm=90` で ~93.75 に回復＝
   `roundtrip_corpus_screen.md` の 117/125 アトラクタの再現）。「140 BPM」も 1/4
   （high_04）が ~123 に落ちた。素朴センサーの d=1.61 に対し、prior 補正した真テンポでは
   d≈6.4 で、**アトラクタが真の分離を圧縮するが tight 閾値は割らない**。K1 の brightness
   帯域比センサーが完全 dead だったのとは違い、bpm 素朴センサーは「鈍るが死なない」。
2. **brightness は borderline tight（d=0.86）で非対称**。Suno は「bright」をよく守る
   （3/4 が絶対 bright 帯 ≥2500 Hz に到達）が、「dark」では centroid を ~2000–2800 までしか
   下げず**絶対 dark 帯（≤1200 Hz）に 0/4**。grip を立てているのは主に bright 側で、
   bright_04 のように「bright 指定で逆に最暗（1967 Hz）」の取りこぼしもある。
   なお legacy 帯域比センサーも本物素材では d≈0.80（K1 では合成 HF 欠落で dead だったが、
   実音源には HF があり盲目が解ける＝**センサー盲は素材依存だった**という K1→K2 の補足）。

**留保**: n=4/水準の小規模方法実証。key は brightness 群で全て A minor だが bpm 群で
ドリフト（C/D major 混在）＝ツマミ非対象のノイズ。直交性（bpm が centroid を動かすか等）は
K3。製品転移の「効果量の絶対水準」までは主張せず、**分類（tight/loose/dead）の転移**を確認する
段階。

**K2-seg（2026-07-05、follow-up）**: compose が実際に送出する未計測プロンプト欄
（active rate / valley depth / Avoid / semantic.core / time signature）を MusicGen
ローカルバッチで一次スクリーニング（tight 0 / loose 2 / dead 3、Avoid の符号反転
attractor が headline finding）。詳細は
[`musicgen_backend.md`](musicgen_backend.md) §7.6。

#### K2-seg Suno 転移バッチ 1（2026-07-09）

MusicGen スクリーン（§7.6）で裁定価値が最も高かった 2 欄（本文 `Avoid:` セグメント /
`semantic.core`）を、実測 Suno 12 曲（3 セル `calm`/`calm_avoid`/`euph` × R=4、
`calm` が両ノブの low セルを共有）へ転移した。fixture・grip・honesty は
[`examples/control/k2_suno_segments/README.md`](../examples/control/k2_suno_segments/README.md)、
再現は `python scripts/measure_grip.py --fixture examples/control/k2_suno_segments/suno_rpe_fixture.json`
（`tests/test_grip.py::test_k2_seg_suno_segments_fixture_snapshot` が固定）。

| ノブ | センサー | mean low | mean high | grip d | 機械分類 | MusicGen 対照 |
|---|---|---:|---:|---:|---|---|
| `semantic_avoid` | `spectral_centroid` | 2438.01 | 3079.39 | **+4.03** | dead（符号逆） | +1.10（同方向・約 1/3.7 の強さ） |
| `semantic_core`（物理） | `onset_density` | 5.428 | 5.607 | **+0.23** | loose（正方向） | −0.70（dead・物理センサー盲） |
| `semantic_core`（CLAP energy） | `contrast_fit` | 0.0485 | 0.2351 | **+2.45** | tight 域 | +1.90（tight 域） |

**本文 Avoid=attractor が Suno でも再現・d=+4.03 は MusicGen +1.10 より強い**:
expected_sign は −1（Avoid が効けば centroid は低下するはず）だが実測は符号が
完全に逆で、MusicGen よりも約 3.7 倍強く内容語（"bright shimmering sparkling
highs"）へ引き寄せられた。事前登録の attractor 専用ルーブリック（発注書 verbatim）
では d≥+0.8 は「attractor 確定」— **suno backend への `omit_body_negative=True`
波及を提案**する（#153 の Suno 波及裁定。本 PR ではコード変更しない、docs 記録の
みで follow-up PR に回す）。留保: 生成は Suno のユーザーオリジナル・カスタムモデル
（ユーザー申告 2026-07-09、標準 stock モデルではない）での実測であり、標準モデル
への一般化追試は follow-up（fixture README honesty (g)。ただし MusicGen でも同符号
の attractor が実測済みで、否定語盲は生成器横断の機序である可能性が高い）。

**core は機種間で物理センサー生存が異なる**: MusicGen は物理 dead（onset_density
盲）× CLAP tight の「センサー盲」構図だったが、Suno は物理 loose（弱いが方向どおり
生存）× CLAP tight 域で、物理センサーの感度そのものが機種依存であることを示す。
config 反映（`device_profiles/suno.yaml` への `semantic.core` 追記）は SEM-1
昇格ゲート（#126）準拠で本 PR ではしない。

**副次観測**（詳細は fixture README）: R2-2f `bpm_prior_disagreement` の live 初発火
（`calm_04`: 候補 161.5/234.91、比 1.4546）、key の相対調ドリフト（A minor 指定に
C major 優勢）、`euph` セルの `spectral_centroid`（3176–3312）が `calm_avoid`
（2960–3167）より高い域に出た `semantic.core`→centroid 交差結合疑い（K3 機種依存
文脈への追加観測）。

### K3: 直交性行列 — DCI/MIG の効果量再定式化

**Status**: K3-1 DONE（2026-07-01、決定論的演奏者リファレンス — 結果は §5.3）。
K3-2a DONE（2026-07-02、K2 既存 fixture からの本物 Suno ミニ行列 — 結果は §5.4）。
K3-1b DONE（2026-07-02、有意性の計器化）。
K3-2b の設計指示 (a)(b)(c) は **MusicGen フル行列で実現済み**（2026-07-03、
5 ツマミ + dead 2 行 + R=8 の自動生成バッチ — 結果は §5.5）。フル **Suno** 行列は
follow-up のまま（生成バッチ人手律速）。

ツマミ i が観測 j を動かさないか（レイヤー独立性）を N×N で測る。K0–K2 が立証した
対角 grip（ツマミ i → センサー i）を非対角へ一般化し、「楽譜が操作盤として成立して
いるか」を disentanglement 指標で定式化する
（[`ai_performer_score_roadmap.md`](ai_performer_score_roadmap.md) PR3 前半）。

#### 統計の定義 — 1 セル = §3 の効果量そのまま

行列セル `M[i,j]` は **ツマミ i の A/B コントラストがセンサー j に立てる符号付き
効果量**（§3 と同一の Cohen's d、ゼロ分散センチネル規則含む）。対角だけ特別な統計を
使わず全セルを d で統一する。この選択には筋の良い性質がある:

- **ランダムドリフトは干渉に立たない**。K2 で観測された「bpm 群での key ドリフト
  （C/D major 混在）」のような群内の揺れは pooled SD（分母）に吸収され、
  low/high 群間で**系統的に**動くものだけが非対角に立つ。干渉＝系統的横効果、
  ノイズ＝群内分散、が統計の形で分離される。
- **カテゴリセンサーの連続化**: key は「**ベースライン key への per-sample 一致
  スコア**」（`key_match_baseline`、mir_eval weighted score ∈[0,1]）を連続センサーに
  する。key ノブの low 水準をベースライン key に一致させると、対角セルは
  「key を離すと一致スコアが下がる」の d（`expected_sign = −1`）になる。
  K1 の categorical 一致率（要求ターゲットへの accuracy）は対角の informativeness
  指標として温存し、K3 行列はこれを**置き換えない**（統計が違う 2 つの読みとして併記）。

対角セルは従来どおり tight/loose/dead（`classify_grip`）、非対角セルは符号不問の
干渉 3 分類とする（閾値は §8 の grip 閾値を流用）:

| \|d\| | 非対角の分類 | 意味 |
|---|---|---|
| < 0.2 | **clean** | 干渉なし（dead 閾値未満＝ノイズフロア） |
| 0.2–0.8 | **weak** | 弱い横効果 |
| ≥ 0.8 | **strong** | 強い干渉。ツマミ i はセンサー j を系統的に汚す |

#### DCI の効果量再定義

DCI（Eastwood & Williams 2018）は regressor の feature importance 行列上に
disentanglement / completeness / informativeness を定義する。K3 は importance を
**効果量絶対値**で置き換える:

```text
importance R[i,j] = clip(floor(|M[i,j]|), cap)
  floor: |d| < 0.2 → 0（dead 閾値をノイズフロアとして流用。小標本の d の揺れが
         エントロピーを偽って持ち上げるのを防ぐ）
  cap:   |d| > 10 → 10（ゼロ分散センチネル ±999 と巨大 d が正規化を支配しないため。
         「10 pooled SD を超えたら決定的」と読む）

disentanglement D_i = 1 − H_norm(R[i,·])   # 行: ツマミ i は単一センサーだけを動かすか
completeness    C_j = 1 − H_norm(R[·,j])   # 列: センサー j は単一ツマミにのみ支配されるか
overall         = 質量重み付き平均（ρ_i = ΣR[i,·] / ΣΣR、DCI 論文の重みに対応）
informativeness = 対角の tight/loose/dead（既存 grip 分類の離散化として位置づけ）
```

行和 0 のツマミは **matrix-dead**（D_i = None、集計から除外）。配線が既知の決定論的
演奏者では、synth が読まないツマミ（`active_rate_target` / `valley_depth_target`）の
行が全ゼロになること自体が**ハーネスの検証**になる（K1 の「繋がっていない
コックピット検出」の行列版）。

**MIG は「効果量ギャップ」として実装する**（正直な命名 — 相互情報量そのものではない）:

```text
effect_size_gap EG_j = (top1_j − top2_j) / top1_j ∈ [0,1]
```

センサー j を動かす最強ツマミが 2 位をどれだけ独走しているか。1.0 = 単独支配、
0.0 = 2 ツマミが同率で動かす（干渉の最悪形）。

#### 設計上の注意 — 非対角 ≠ 常に悪

bpm を上げれば onset が密になる（bpm → onset_density）ような**構造的結合**は音楽の
物理であって操作盤の欠陥ではない。同一潜在因子を別の角度から読むセンサー
（onset_density は bpm のエイリアス）を DCI の正方コアに入れると disentanglement が
偽って下がるため、**コア行列はツマミと 1:1 対応する正方 5×5 に限定**し、エイリアス
候補は **extended 列**（行列と干渉分類には出すが DCI 集計から除外）として観測する。

#### 5.3 K3-1 結果 — 決定論的演奏者の直交性行列（R=5、2026-07-01）

再現: `python scripts/build_k3_fixture.py`（fixture 再生成、約 5 分）→
`python scripts/measure_orthogonality.py --fixture examples/control/k3/synth_performer_matrix_fixture.json`。
コミット済み成果物は `examples/control/k3/`（fixture / `expected_orthogonality.json` /
`orthogonality_map.md`）。fixture → 行列 は決定論で snapshot test 固定。

効果量行列（行=ツマミ、列=センサー、対角=**太字**、strong 干渉=⚠、†=extended 列）:

| knob \ sensor | bpm | key_match_baseline | spectral_centroid | active_rate | valley_depth | onset_density† |
|---|---|---|---|---|---|---|
| bpm | **16.4** | 0 | -11.6 ⚠ | 4.52 ⚠ | -1.64 ⚠ | 3.72 ⚠ |
| key | -0.632 | **-999** | -1.77 ⚠ | 0.642 | 0.841 ⚠ | 0.386 |
| brightness | 0.4 | 0 | **160** | 1.66 ⚠ | 0.894 ⚠ | 3.37 ⚠ |
| active_rate_target | -2.53 ⚠ | 0 | 0.0885 | **0.552** | 0.398 | -0.483 |
| valley_depth_target | 0 | 0 | 0.0655 | 0.828 ⚠ | **-1.03** | -0.134 |

DCI: overall disentanglement **0.375** / overall completeness **0.485** /
mean effect_size_gap **0.551**。列単位では `key_match_baseline` の completeness が
**1.0**（key ノブだけが動かす＝完全にクリーンな独占チャネル）、`valley_depth` が
**0.055**（全ツマミが同程度に揺らす＝最悪。ただし後述のノイズ天井内）。

主要な発見 — 4 点:

1. **干渉にも 2 種類ある**（K1「dead の 2 分類」の行列版）。
   - **生成側の構造的結合**: bpm → active_rate (4.52) / onset_density (3.72) は
     「テンポを上げれば音が密になる」音楽の物理であり、操作盤の欠陥ではない。
     bpm → spectral_centroid (**-11.6**) も玩具で実在する強結合（テンポ上昇で
     centroid が下がる — pulse/chord の時間比が変わりスペクトル重心が動く）。
   - **センサー側の結合**: brightness → onset_density (3.37) は、演奏される音符列
     （密度）が不変なのに onset 検出器がスペクトル内容に反応して読みを変える。
     ツマミは汚していない、センサーが混線している。K3 行列はこの 2 つを区別する
     読みの装置になる（どちらも「非対角が立つ」が、原因層が違う）。
2. **既知 dead 行が経験的ヌル分布をくれる**。synth が読まない `active_rate_target`
   行に最大 |d| = **2.53**（bpm 列）が立った。生成器の確率性が seed 駆動 bpm
   ジッターだけでも、R=5 の群平均差はここまで偽干渉を作る。よって本 fixture の
   読みでは **|d| ≲ 2.5 の非対角セルは seed ノイズと分離不能**（ノイズ天井）。
   `IMPORTANCE_FLOOR` 0.2 は grip 分類の閾値であって干渉の有意性閾値ではない —
   行列は計器であり verdict を出さない（audit と同じ規律）。同じ理由で
   `active_rate_target` の対角 0.552（loose 表示）も spurious であり、**K1 の
   dead 判定が正**（配線が無いことはコードで既知）。本判断は K3-1b で計器出力へ
   昇格した（`known_dead` 宣言行からヌル分布を自動計算し、各セルに noise_margin /
   天井超えフラグを付与。resolved と機械判定されたのは bpm→centroid/active_rate/
   onset_density と brightness→onset_density — 散文の読みと一致）。
3. **cap=10 の副作用が gap に出る**。spectral_centroid 列は brightness (160) と
   bpm (-11.6) が両方 cap=10 に張り付き effect_size_gap = 0（同率支配と表示）。
   実際は桁違いに brightness が強い。gap は cap 感度を持つ指標として読むこと
   （cap を外すと saturated センチネルが正規化を支配するトレードオフの選択）。
4. **「操作盤としての質」が初めて数値になった**。overall disentanglement 0.375 —
   接続が既知の決定論的演奏者ですら、bpm ノブが 4 センサーを同時に動かす
   「汚れた操作盤」である。MIDI が楽譜たり得たのはチャネル直交性が構造で保証
   されていたからで、生成系の楽譜は直交性を**測って**主張する必要がある — その
   計器が本行列。Suno 実測（K3-2）では生成器の確率ノイズが大きいぶんノイズ天井が
   さらに上がるはずで、R の増員か水準差の拡大が必要になる見込み。

#### 5.4 K3-2a 結果 — 本物 Suno のミニ直交性行列（K2 fixture 再利用、R=4、2026-07-02）

K2(#117) の committed fixture は全サンプルに複数センサーを記録済みだったため、
**新規生成ゼロ**で本物 Suno の 2×2 コア + extended 3 列を測れる。再現:
`python scripts/build_k3_suno_mini_fixture.py`（K2 fixture の決定論変換、音声処理なし）→
`python scripts/measure_orthogonality.py --fixture examples/control/k3/suno_mini_matrix_fixture.json`。
成果物は `examples/control/k3/`（`suno_mini_matrix_fixture.json` /
`expected_orthogonality_suno_mini.json` / `orthogonality_map_suno_mini.md`）。
key は K2 バッチにベースライン key の宣言が無いためセンサー化しない（honesty）。

| knob \ sensor | bpm | spectral_centroid | active_rate† | valley_depth† | brightness_band_ratio† |
|---|---|---|---|---|---|
| bpm | **1.61** | 2.33 ⚠ | -0.959 ⚠ | -0.124 | 1.55 ⚠ |
| brightness | -0.34 | **0.863** | -1.39 ⚠ | 1.2 ⚠ | 0.804 ⚠ |

DCI: overall disentanglement **0.051**（玩具 0.375）/ overall completeness 0.224 /
mean effect_size_gap 0.709。非対角 8 セル中 **6 が strong**。

読み（確度の階層を明示する）:

1. **対角は K2 公表値を正確に再現**（bpm 1.61 / brightness 0.863 = §5.2 の
   1.61 / 0.86）。行列は対角 grip を特殊ケースとして含む — 変換の忠実性の検証を兼ねる。
2. **bpm→centroid 結合は本物 Suno にも見えるが、符号が玩具と逆**（玩具 −11.6 =
   速いほど暗い / Suno +2.33 = 速い指定ほど明るいミックス）。結合の**向きが生成器
   固有**であることは、干渉補正が普遍則でなく**機種デバイスプロファイル**
   （[`ai_performer_score_roadmap.md`](ai_performer_score_roadmap.md) PR3 後半）で
   持つべき知識であることの実証的動機になる。
3. **ただし個々の非対角セルは R=4 では未解決**。本ミニ行列には dead ツマミ行
   （内部ヌル分布）が無く、K3-1 の経験的ノイズ天井（seed ジッターのみで
   |d| ≲ 2.5、§5.3 発見 2）を本物生成器の R=4 に当てはめると、最大の非対角
   2.33 も天井内。確度の高い主張は (a) 対角の K2 再現、(b) **集計パターン** —
   非対角の大半が strong 側に立ち disentanglement が玩具の 1/7 に落ちる＝
   「本物 Suno は tight な対角を持ちながら直交性は玩具より大幅に悪い」まで。
   セル単位の結合の確定は K3-2b（dead 行の同梱 + R≥8）で行う。K3-1b で計器は
   この但し書きをそのまま自己申告する — 本ミニ行列は `known_dead` 行を持たず
   `noise.ceiling = None` となり、全セルが `exceeds_noise_ceiling = None`
   （no_ceiling）で unresolved 表示になる（散文の但し書きが出力になった）。
4. **legacy 帯域比センサー（K1 で dead）は実音源で両ツマミに反応**（1.55 / 0.804）。
   K2 の「センサー盲は素材依存」の補足が干渉列としても再確認された。

**K3-2b への設計指示（本ミニ行列からの教訓）**: フル Suno 行列には (a) 生成器が
読まないことが既知の dead ツマミ（例 `valley_depth_target`）を**内部ヌル行として
必ず同梱**する（ノイズ天井を同一バッチ内で実測するため）、(b) R≥8、(c) ベースライン
key の宣言（key センサー化のため）。

#### 5.5 K3-2b 結果 — MusicGen フル直交性行列（自動生成バッチ、R=8、2026-07-03）

設計指示 (a)(b)(c) を第二生成器 MusicGen（`facebook/musicgen-small`、ローカル
CPU バッチ・人手ゼロ）で初めて充足した。5 ツマミ × 2 水準 × R=8 = 80 クリップ。
全行が同一ベーステンプレート（120bpm / C major / neutral timbre / active・valley
数値トークン）を共有し、各行は自ノブのトークンのみ A/B 差し替え。dead 2 行は
`active rate target 0.90-0.95` 等の **MIR 内部指標の数値レンジ文字列**（テキスト
条件付けの語彙外という K3-1b 型宣言）。再現: `examples/control/k3/
musicgen_matrix_plan.yaml` → runbook `generate`/`extract` →
`python scripts/build_k3_musicgen_fixture.py` → `python scripts/
measure_orthogonality.py --fixture examples/control/k3/musicgen_matrix_fixture.json`。
成果物は `examples/control/k3/`（`musicgen_matrix_extract.json` /
`musicgen_matrix_fixture.json` / `expected_orthogonality_musicgen.json` /
`orthogonality_map_musicgen.md`）。

| knob \ sensor | bpm | key_match | centroid | active_rate | valley_depth | band_ratio† |
|---|---|---|---|---|---|---|
| bpm | **0.851** * | -0.096 | 0.127 | 0.085 | 0.242 | -0.334 |
| key | 0.307 | **0.14** | 0.441 | 0.024 | -0.2 | 0.431 |
| brightness | -0.234 | 0.178 | **1.26** * | -0.764 | 0.596 | 0.849 * |
| active_rate_target (dead) | -0.614 | -0.848 | -0.215 | **0.15** | 0.721 | -0.556 |
| valley_depth_target (dead) | 0.063 | 0.028 | 0.44 | -0.656 | **0.499** | -0.413 |

DCI: overall disentanglement **0.323** / completeness 0.355 / mean effect_size_gap
0.449。ノイズ天井（12 ヌルセルの max |d|）= **0.848**。`*` = 天井超え。

読み（確度の階層を明示する）:

1. **ノイズ天井計器（K3-1b）が実生成器で初稼働し、over-reading を実際に防いだ**。
   dead 行が生む見かけの効果は最大 |d|=0.848（active_rate_target→key_match）に
   達する — R=8 の生成ノイズはこの規模。天井を超えて解像したのは
   **対角 2（bpm 0.851 / brightness 1.26）+ 非対角 1（brightness→band_ratio
   0.849）の計 3 セル**。live 行の非対角 15 セルのうち解像はこの 1 つだけで、
   残る 14 セル（K3-2a で strong に見えた種類の異種チャネル間結合を含む）は
   すべて unresolved に抑制された。なお唯一解像した非対角は brightness ノブを
   legacy 帯域比センサー（同じ明るさ次元の第二センサー）が拾ったもので、
   行列位置上はクロスだが異種チャネル間の干渉ではない — **K3-2a 型の
   異種チャネル間結合で天井を超えたものはゼロ**。
2. **K3-2a の bpm→centroid 符号反転問題は MusicGen では裁定不能（unresolved）**:
   +0.127 は天井のはるか内側。「機種ごとに符号が違う」仮説はフル Suno 行列
   （follow-up）か R 増員を待つ — 計器がこの保留を自己申告できるようになったことが
   K3-2a からの前進。
3. **key 対角 0.14 = dead**: "in the key of F sharp major" トークンを MusicGen は
   ほぼ読まない。R3 の key 保存率 0.15（`musicgen_backend.md` §7.3）と独立経路で
   整合 — key は MusicGen では送出不能ノブであり、制御は選抜（R3-3）頼みになる。
4. **dead 宣言の内部整合**: active_rate_target 対角 0.15（宣言どおり不発）。
   valley_depth_target 対角は 0.499（loose 帯）だが自バッチ天井 0.848 以下 =
   ノイズと弁別不能で宣言と矛盾しない。
5. **MusicGen は Suno mini より直交的に見える**（disentanglement 0.323 vs 0.051）が、
   センサー集合・ノブ数・R が異なるため数値の直接比較は要注意 — 確度の高い主張は
   「MusicGen の非対角はノイズ天井を超えない」まで。

---

## 6. パラメータ × センサー対応表（DD-D）

PoC が扱えるのは「観測チャネルを持つツマミ」のみ。

| ツマミ（Composition Score） | センサー（RPE 観測量） | grip 予想 | フェーズ |
|---|---|---|---|
| `physical.bpm` | 観測 BPM（`PhysicalRPE`） | tight | K0 |
| `physical.brightness` | `spectral_centroid`（**正規センサー**、dark ≤1200 / bright ≥2500 Hz。K0 当時の帯域比 `spectral_profile.brightness` はセンサー盲のため 2026-06-12 に再設計 — §5.1） | loose 予想 → tight 実測（§5.1） | K0 |
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

> **歴史的記録（K0 実施済み）**: 本 Memo 中の brightness センサー指定（帯域比）は
> K0 当時のもの。その後センサー盲が判明し、正規センサーは `spectral_centroid` に
> 再設計された（§5.1、2026-06-12）。新規実装は §6 の現行対応表に従うこと。

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
- ~~**fixity 型の導入**~~ → **実装済み（R5）**: `CompositionScore.fixity` と
  `field_fixity(score)` で、物理欄ごとの locked/unlocked を表現する。新フィールド追加時は
  `AGENTS.md` の Schema Admission 手順で fixity と往復一致の実測 or 実測計画を必須化する

---

## 9. 設計ドキュメント索引への登録

本 docs 新設に伴い、以下 2 箇所に 1 行追加すること（[`CLAUDE.md`](../CLAUDE.md)
ドキュメント管理ポリシー）:

- `CLAUDE.md` 設計ドキュメント索引表
- `README.md` 設計ドキュメント表
