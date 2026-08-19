<!-- 収載ヘッダ（リポジトリ管理者注記・原文は下記より）-->

> **状態: 採用済み Evaluation Theory Note（User 提供・2026-08-19）。**
> 壁打ち議論（SPR 叩き台 → Fable 精緻化）を User が v0.3 補論として
> まとめたものを逐語収載。**本書が SPR の理論正本**であり、実装側の
> 配線契約は [`../evolution/DESIGN_SPR.md`](../evolution/DESIGN_SPR.md)
> （本書と同日に統合再構築済み・実装で数値/schema が要る場合はそちらが正）。
>
> 整合注記: §2 の総合点化禁止は M3 裁定 → VG-E0 schema 強制と一致。
> §7 の単独介入原則は DESIGN_S3_backfill の多介入既定不可と一致。
> §4 の Plasticity descriptor を Archive の behavior 空間へ合流させるには
> VG-E0（N=5 三角格子・凍結）の版番改訂が必要 — VG-E1 以降で裁定。

---

# VoiceGenesis v0.3 補論A
## 評価軸・選択圧・Selection Pressure Routing

- 文書種別: 補論 / Evaluation Theory Note
- 対象: VoiceGenesis Evolution Theory v0.3
- 作成日: 2026-08-19

---

## 0. 目的

VoiceGenesis v0.3 では人工歌手の変化を次の4回路へ分離する。

- E: Evolution / Reproduction
- L: Individual Learning
- T: Teaching / Skill Transfer
- S: Shared Backbone Learning

評価軸を増やしすぎると、選択圧が分散して「何を残したいのか」が不明瞭になる。
そこで本補論では、評価を Gate / Absolute / Relative / Developmental に分離しつつ、
それらを同格の選択圧として扱わない。

核心は次である。

> 評価軸を増やすのではなく、各回路・各タイミングに一つの主選択圧を割り当てる。

これを **Selection Pressure Routing** と呼ぶ。

---

## 1. 評価値の4分類

### 1.1 Gate — 採用資格

Gate は能力スコアではなく制約。

例:
- Rights / Provenance
- Artifact
- Replay
- Determinism
- Identity audit
- Hidden-evaluator audit

定義:

G(v) ∈ {PASS, FAIL}

高品質でも Gate FAIL なら不採用。
Gate を他スコアで相殺しない。

### 1.2 Absolute — 現在の絶対性能

Population 内順位に依存しない固定基準に対する性能。

例:
- Pitch
- Intelligibility
- Naturalness
- Stability
- Artifact rate

Absolute は原則として **Quality Floor** に使う。

例:
- Pitch >= θP
- Naturalness >= θN
- Artifact <= θA

0.91 と 0.93 の差だけで繁殖率を大きく変えない。

### 1.3 Relative — 集団内の比較優位

Relative は個体単独では定義できない。

R(v | P, N)

P = Population
N = Niche / comparison context

例:
- niche 内順位
- pairwise A/B 勝率
- novelty percentile
- human preference

Relative は Singer 固有の固定能力ではなく、
Singer × Comparison Context で決まる。

### 1.4 Developmental — 成長性能

現在能力ではなく状態変化量。

D(v, C) = A(v_after) - A(v_before)

C = Curriculum / Lesson / Training budget

例:
- LearningGain
- TransferGain
- skill別改善量
- 学習速度
- 単位GPU時間あたり改善量

したがって「今強い」と「伸びる」は分離して扱う。

---

## 2. 4分類を同格の選択圧にしない

誤った設計は、

Score = wG*G + wA*A + wR*R + wD*D

のような総合点化である。

問題:
- Gate が能力点へ変換される
- Absolute と Relative が二重計上される
- 高完成度と高可塑性の意味が潰れる
- 何が選択理由だったか追跡しにくい

v0.3 では次のように整理する。

- Gate = 制約
- Absolute = 生存 Floor
- Relative = 成熟個体の主選択圧
- Developmental = 行動形質 / 成長形質

つまり、

**2つの制約 + 1つの主選択圧 + 1つの発達形質**

として扱う。

---

## 3. Selection Pressure Routing

### 3.1 出生時 — E回路 / Viability Pressure

出生直後には強い品質選択をかけない。

問うのは「優秀か」ではなく「育成対象として成立しているか」。

主選択圧:

P_birth = Viability

確認:
- Rights PASS
- Artifact critical failure なし
- 最低限の発声成立
- Replay可能
- Genome / provenance 有効

原則:

> 出生では殺しすぎない。

未熟だが高可塑性の個体を出生時に淘汰しないため。

### 3.2 個体学習時 — L回路 / Self-Improvement Pressure

L回路では個体同士を競わせない。

比較対象:

Singer:r0 vs Singer:r1

主選択圧:

P_L = LearningGain

例: attack 学習なら、

- AttackGain > 0
- Identity = PASS
- Naturalness 非悪化
- Pitch 非悪化
- Replay = PASS

L回路は **自己との競争**。

### 3.3 教育時 — T回路 / Transferability Pressure

T回路では Singer ではなく Lesson を競わせる。

例:

Lesson X -> A
Lesson X -> B
Lesson X -> C

観測:
- Gain_A
- Gain_B
- Gain_C
- Identity drift
- Failure rate
- target niche

主選択圧:

P_T = Transferability

ただし総合1点にせず、
- General Lesson
- Niche-Specific Lesson

へ分けて保存する。

原則:

> 教育では歌手を競わせず、教材・技能差分を競わせる。

### 3.4 成熟後 — E回路 / Niche-Relative Reproductive Pressure

最も強いダーウィン的選択圧は成熟後にかける。

前提:
- Gate PASS
- Absolute Quality Floor PASS
- 標準育成予算を消化

主選択圧:

P_E = NicheRelativeAdvantage

処理:

Quality Floor
-> Niche assignment
-> Relative competition
-> Elite / QD Archive
-> Parent pool

異なる niche を単一総合点で比較しない。

原則:

> 成熟後に初めて他個体と強く競わせる。

### 3.5 S回路 — Population-Level Pressure

S回路は個体を選ぶ回路ではない。

対象:
- Shared Backbone
- Evaluator
- Species-level checkpoint

主選択圧:

P_S = PopulationLevelImprovement

見るもの:
- Quality-Constrained Coverage
- Critical niche survival
- Hidden/Human alignment
- Hack recurrence
- Average failure rate
- Replay stability

S回路の問いは、
「最高個体がさらに強くなったか」ではなく、
**生態系全体の成立領域が広がったか**。

---

## 4. Developmental Score の位置づけ

Developmental を繁殖点へ直接加算しない。

例:

A:
- Current Quality = 高
- Plasticity = 低

B:
- Current Quality = 中
- Plasticity = 高

この場合、

- A = high-performance elite
- B = high-plasticity lineage

として別 behavior descriptor / archive 特性へ保存する。

つまり Plasticity は Selection score ではなく
**Behavior Descriptor** として扱う。

---

## 5. 選択圧の強度

概念的には、

P_birth < P_T ≲ P_L < P_E

とする。

- Birth: 弱い圧。Viabilityのみ。
- L: 中程度。悪いRevisionを捨てる。
- T: 中程度。教材を選別する。
- E mature: 最も強い。繁殖系統を選ぶ。
- S: 強度ではなく低頻度・高影響。

---

## 6. 評価の時間分離

同じ個体に4種類の圧を同時にかけない。

- 出生時 -> 生存判定
- 学習時 -> Revision採用
- 教育時 -> Lesson採用
- 成熟時 -> 繁殖選抜
- 世代間 -> Backbone / Evaluator更新

この時間分離によって、
Gate / Absolute / Relative / Developmental が
同時に選択圧化するのを防ぐ。

---

## 7. 因果評価

評価は点数付けだけでなく、
何が改善を生んだかを特定する計器として使う。

変数:
- G = Genome / Evolution
- L = Individual Learning
- T = Teaching
- S = Shared Backbone
- A = Absolute Performance
- R = Relative Position
- D = Developmental Change

構造:

G, L, T, S -> A -> R

Developmental:

D = A_after - A_before

因果帰属時は一度に1種類の Edge だけを変更する。

例:

A = parent revision, no learning
B = same parent + LEARN only

固定:
- Genome hash
- Backbone hash
- score
- seed
- Probe
- ExecutionProfile

差分 ΔA を L 回路へ帰属する。

E/T/S も同様に単独介入で測る。

---

## 8. スポーツとの対応

| VoiceGenesis | スポーツ |
|---|---|
| Gate | 競技規則・公認条件・ドーピング規定 |
| Absolute | タイム・距離・重量 |
| Relative | 順位・ランキング・採点 |
| Developmental | シーズン前後の伸び |
| Birth Viability | 競技参加可能性 |
| L pressure | 自己ベスト更新 |
| T pressure | コーチング法・教材の有効性 |
| Mature E pressure | 大会・代表選考 |
| S pressure | スポーツ科学・用具・共通知識の進歩 |

---

## 9. 最終評価モデル

Singer を単一 TotalScore で保存しない。

EvaluationState(v) = {
  GateState,
  AbsoluteVector,
  RelativeContext,
  DevelopmentalVector
}

ただし選択時には全部を同時使用しない。

最終ルーティング:

- Gate -> 資格条件
- Absolute -> Quality Floor
- Developmental -> Plasticity / growth descriptor
- Relative -> Mature population の主選択圧

回路別の主圧:

- E_birth -> Viability
- L -> Learning Gain
- T -> Transferability
- E_mature -> Niche-relative reproductive advantage
- S -> Population-level improvement

---

## 10. Selection Pressure Routing の原則

1. 一回路一主選択圧
2. Gate は能力点ではない
3. Absolute は主に Floor
4. Relative は成熟個体の繁殖選抜に使う
5. Developmental は Current Quality へ直接加算しない
6. 出生時の選択圧を弱くする
7. 学習は自己比較
8. 教育は Lesson 比較
9. 種更新は Population で判定
10. すべての評価を Revision / Edge へ帰属可能にする

---

## 11. 結論

VoiceGenesis v0.3 の評価設計で重要なのは、
何種類のスコアを持つかではない。

重要なのは、

**どのタイミングで、どの回路に、どの選択圧をかけるか**

である。

したがって v0.3 の評価論は、

Gate / Absolute / Relative / Developmental

という評価軸の分離と、

E / L / T / S

という変化回路の分離を組み合わせた

**Selection Pressure Routing**

として定義する。

最短の原則:

> 出生では殺しすぎない。  
> 学習では昨日の自分と競わせる。  
> 教育では教材を競わせる。  
> 成熟後に他個体と強く競わせる。  
> 種の更新は生態系全体で判定する。

この構造によって、選択圧を分散させず、
品質・多様性・可塑性・教育可能性を一つの総合点へ潰すことなく、
VoiceGenesis の進化・発達・文化伝達を因果的に追跡できる。
