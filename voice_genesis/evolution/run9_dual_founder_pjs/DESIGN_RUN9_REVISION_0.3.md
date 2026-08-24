# DESIGN RUN9 — Revision 0.3

- **裁定日:** 2026-08-24
- **裁定者:** User
- **design_revision:** 0.2 → 0.3
- **裁定ソース:** [`POR_CONCEPT_ADJUDICATION_20260824.txt`](./POR_CONCEPT_ADJUDICATION_20260824.txt)
  （「RUN9 v0.2 PoR整理・設計裁定メモ」、uploads 原本とバイト同一・
  **byte-pin 不変**。sha256 =
  `56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007`
  — `RUN9_CONTRACT.yaml` の `por_adjudication_sha256` が PINNED で保持する）

**番号注記**: PoR メモは自称 "v0.2 design revision input"（メモ冒頭の
`状態: Concept Adjudication / v0.2 design revision input` 参照）だが、
リポジトリ上は 2026-08-24 の別の裁定5件（`DESIGN_RUN9_REVISION_0.2.md`、
PR #316）が既に発行・マージ済みで `design_revision` は既に `"0.2"` を
名乗っている。同じ番号を PoR メモの内容で上書きすると、マージ済み rev
0.2 が固定した pin（`design_doc_sha256` / `design_revision_doc_sha256` /
`backbone_checkpoint_sha` 等）の版管理上の意味が曖昧になる。したがって
**本編入は rev 0.3 として発行する**（意味上は User の言う「v0.2」に相当
する内容である）。マージ済み rev 0.2 文書
（[`DESIGN_RUN9_REVISION_0.2.md`](./DESIGN_RUN9_REVISION_0.2.md)）は
**無改変のまま存続**し、その内容（ControlProfile 方式・§対応マップ・
AF0/PJS/User rights/Backbone の pin 規約）は、本 PoR と矛盾しない限り
そのまま有効であり続ける。v0.1 本文
（[`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`](./DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md)）
も同様に無改変・byte-pin 不変のまま。

旧 revision（"0.1"・"0.2"）を宣言する contract は design_revision 0.3
以降 fail-closed で拒否される（`run9_schema.DESIGN_REVISION` の凍結値
照合）— これは意図どおりの拒否であり、実装バグではない。

---

## 改訂 A — LEARN_PERFORMANCE 単一エッジの三経路分離

v0.1 §13.1「RUN9 の primary learning mode は `LEARN_PERFORMANCE`」
（rev 0.2 改訂1で書き込み先を Performance ControlProfile へ変更済み）を、
PoR §1/§3/§4/§16 に従い次のとおり改める:

> `LEARN_PERFORMANCE` という単一 Edge を廃し、Founder r0 から3枝へ分岐
> する構造へ変更する:
>
> 1. **CONTROL**（無介入 replay）— 学習 step を実行しない対照条件。
>    rev 0.2 改訂1「C1 Zero ControlProfile / Sham Transition」の対照
>    意味論をそのまま担う枝。
> 2. **PRACTICE_FROM_AUDIO**（稽古）— PJS 教師音声そのものを Founder へ
>    提示し、Founder 自身が観測・特徴抽出・自己との差分推定・制御変更を
>    自律的に行う（PoR §3.2）。
> 3. **TRANSFER_TECHNIQUE**（教育）— PJS 教師音声から抽出・構造化した
>    Technique 表現を Founder へ明示的に与え、Founder 自身の声でその型を
>    再現できるかを見る（PoR §3.3）。
>
> 両介入枝（PRACTICE_FROM_AUDIO / TRANSFER_TECHNIQUE）とも、書き込み先は
> rev 0.2 改訂1が定めた Founder 別 versioned **Performance
> ControlProfile** をそのまま踏襲する（PoR §10 が承認する制御可能領域の
> 方式 — Shared Backbone / Genome / 出生 Identity を凍結し、Founder ごとの
> versioned ControlProfile 等の制御可能領域を更新する）。
>
> **r0 は in-place 更新しない**（PoR §10 最優先の不変条件）。各枝は
> `BRANCH_REVISIONS`（`run9_schema.py`）が定める独立 Revision 系列
> （CONTROL → `replay`、PRACTICE_FROM_AUDIO → `r_practice`、
> TRANSFER_TECHNIQUE → `r_taught`）として保存する。
>
> **INHERIT_TRAIT** を出生エッジの正式名として採用する（PoR §3.1「交配」）。
> ただし operator 自体は引き続き `TRI_CROSSOVER/1.0`
> （`run9_schema.OPERATOR_ID`）のまま変更しない — INHERIT_TRAIT は
> TRI_CROSSOVER の計算規約（座標変換・genome_id 決定論）に一切影響しない
> 設計文書/結果分類上のラベルである。genome_id 決定論を壊さないことは
> rev 0.3 全体を通じて最優先の不変条件とする。

### 情報伝達経路の対応表（PoR §3 → 機械可読名）

| PoR 呼称 | 機械可読 Edge 名 | 書き込み先 | Revision 系列 |
|---|---|---|---|
| 交配 | `INHERIT_TRAIT`（`BIRTH_EDGE`） | Founder r0 の出生（TRI_CROSSOVER） | — （r0 自体） |
| 稽古 | `PRACTICE_FROM_AUDIO`（`INTERVENTION_EDGES[0]`） | Founder 別 versioned Performance ControlProfile | `r_practice` |
| 教育 | `TRANSFER_TECHNIQUE`（`INTERVENTION_EDGES[1]`） | Founder 別 versioned Performance ControlProfile | `r_taught` |
| （無介入対照） | `CONTROL`（`CONTROL_BRANCH`） | 学習 step を実行しない | `replay` |

---

## 改訂 B — Identity / Trait / Technique 三層観測

PoR §2 の観測対象三層を、rev 0.3 における機械評価の観測軸として採用する
（実装上の名称は変更可能だが、意味上は最低限この三層を分離する — PoR §2
冒頭）:

> **[A] Identity / Genome** — 「誰の声として出生したか」を表す安定した
> 個体基盤。speaker identity、親由来の Identity 構成、Genome 座標など。
>
> **[B] Phenotype / Trait** — 「現在どのような声・発声形質を持つか」を
> 表す層。声色傾向、息の使い方、響き、onset/release、register 運用、
> 発声の癖など、出生時にも与えられ得るが、後天的変化も起こり得る。
>
> **[C] Technique / Form** — 「その声をどう使って歌うか」を表す型・技術。
> timing、duration、pitch trajectory、dynamics、vibrato、phrasing、
> phrase-end、breath placement 等。
>
> Identity・Trait・Technique を完全に独立した物理量だとは仮定しない。
> RUN9 の目的は、まず機械的に分離可能な観測軸として扱い、各経路がどの層を
> 動かしたかを測定可能にすることである（PoR §2 重要）。

### 「PJS へ近づいた」の層分離解釈規約（PoR §7 の例示3パターン）

単一 Total Score では「PJS へ近づいたか」を判断しない（v0.1 §27 item 40
の禁則を rev 0.3 でも継承）。どの層が近づいたかによって解釈を分ける:

1. **Technique だけ近づいた** → 技術獲得の証拠。
2. **Trait も近づいた** → 稽古による形質学習候補。
3. **Identity まで大きく移動** → Identity drift として別記録（成功とは
   扱わない — Identity の安定性は別軸で評価する）。

機械評価では最低限次の8軸を別記録する（PoR §7 列挙、単一 Total Score
への集約は禁止のまま）:

1. Identity distance / identity change
2. Trait change
3. Technique distance to teacher target
4. Control replay noise
5. Practice gain
6. Education reproduction gain
7. Generalization gain（実装可能な範囲）
8. Founder 間 response difference

---

## 改訂 C — PRACTICE / EDUCATION の情報境界

PoR §3.2/§3.3/§11 の非対称な情報境界を、rev 0.3 の実験核心的制約として
凍結する。機械可読 id は `run9_schema.py` の
`PRACTICE_FORBIDDEN_INPUTS` / `PRACTICE_ALLOWED_INPUTS` /
`EDUCATION_ALLOWED_CHANNELS` / `EDUCATION_FORBIDDEN_INPUTS` を正本とする
（将来の practice/education builder がこれを import して検証する）。

### PRACTICE_FROM_AUDIO（稽古）

**禁止**（Founder へ明示的に渡してはいけないもの、PoR §3.2）:

- PJS speaker embedding
- PJS Identity coordinate
- 「vibrato=この値」等の正解 Technique parameter
- 教師内部モデルの parameter dump

**許可**（PoR §3.2）:

- PJS 音声そのものを聞く
- Founder 自身が音声解析を行う
- Founder 自身が何を模倣すべきか決める
- 自分との差分を内部的に推定する
- 許可された可変領域を自律探索する

### TRANSFER_TECHNIQUE（教育）

**許可**（構造化された Technique lesson 経由で渡してよい候補、PoR §3.3）:

timing / phoneme・note duration relation / pitch trajectory /
dynamics・energy trajectory / onset・release pattern / vibrato pattern /
phrasing / phrase-end control / breath placement

**禁止**（PoR §3.3 + §11）:

- PJS speaker embedding
- PJS Identity coordinate
- PJS そのものの声質 latent
- formant そのものの継承 target
- spectral envelope を Identity として複製する情報
- Founder の Identity を PJS へ置換するための parameter
- **learner（Founder）自身が PJS raw audio を直接参照すること**
  （原則禁止 — lesson 生成器だけが PJS audio から Technique を抽出する。
  PoR §11「原則として learner 自身は PJS raw audio を直接参照しない」）

**この非対称性が実験変数である**（PoR §11 末尾）: PRACTICE は「見て、
自分で違いを理解して近づく」、EDUCATION は「型を教えられ、その型を自分の
声で実行する」を表す。両者を「同じ入力」として扱わない（改訂E §8参照）。

---

## 改訂 D — 比較構造と結果分類

### 比較構造（PoR §4）

二体の Founder は出生後、同一の r0 から三経路（CONTROL / PRACTICE /
EDUCATION）へ分岐する。R9F-01 と R9F-02 の双方に同じ三経路を与え、最低限
次の5比較を行う:

- **A. 出生差** — R9F-01:r0 vs R9F-02:r0
- **B. 稽古効果** — r_practice - r0
- **C. 教育効果** — r_taught - r0
- **D. 稽古と教育の差** — r_practice vs r_taught
- **E. 個体差** — 同じ稽古・教育を受けたときの R9F-01 と R9F-02 の応答差

### 結果分類（PoR §13、6分類を語彙として凍結）

一つの PASS だけで全現象を代表させず、最低限次を独立判定する
（`run9_schema.py` の `BIRTH_OUTCOMES` / `PRACTICE_OUTCOMES` /
`EDUCATION_OUTCOMES` / `SEPARATION_OUTCOMES` /
`FOUNDER_RESPONSE_OUTCOMES` / `IDENTITY_OUTCOMES` が凍結値を保持する）:

| 分類 | 値 |
|---|---|
| BIRTH | `ESTABLISHED` / `NOT_ESTABLISHED` |
| PRACTICE | `GAIN_ESTABLISHED` / `NO_GAIN` / `UNOBSERVABLE` |
| EDUCATION | `TRANSFER_ESTABLISHED` / `NO_TRANSFER` / `UNOBSERVABLE` |
| SEPARATION | `MACHINE_EVIDENCE_SUPPORTED` / `MIXED` / `NOT_ESTABLISHED` |
| FOUNDER_RESPONSE | `DIFFERENTIAL_RESPONSE` / `COMMON_RESPONSE` / `UNDETERMINED` |
| IDENTITY | `STABLE_BY_MACHINE_METRIC` / `SHIFTED` / `UNCALIBRATED` |

RUN9 の価値は「全部成功したか」だけではない。例えば「稽古では
Trait + Technique が動く」「教育では Technique だけが動く」という差が
確認できれば、それ自体が重要な結果である（PoR §13 末尾）。

**v0.1 §20 の `transfer_status` 語彙は本結果分類ファミリーにより
superseded とする**。v0.1 本文は byte-pin 不変のまま残るが、rev 0.3
以降の結果は本表の語彙を用いる。

---

## 改訂 E — 公平性・失敗分類・holdout

### 公平性（PoR §8）

R9F-01 / R9F-02 で条件を変えない。必須共通条件（同じ PJS 素材 / 同じ
train・validation・holdout split / 同じ探索空間 / 同じ候補生成規則 /
同じ試行回数 / 同じ render 予算 / 同じ評価器 / 同じ停止規則 / 同じ計算
予算）は各枝内で二体等予算として適用する。**PRACTICE と EDUCATION は
情報量が本質的に異なるため、両者を「同じ入力」とはしない** — 比較時は
各経路内で二体の予算を等しくし、PRACTICE と EDUCATION の情報境界そのもの
（改訂C）を実験変数として明示する。

片方だけ結果を見て追加探索・追加学習することは禁止する（v0.1 の同種規律
と同じ）。

### 失敗時の扱い（PoR §9、3分類を凍結）

`run9_schema.FAILURE_CLASSES` が凍結値を保持する:

- **IMPLEMENTATION FAILURE**（コードバグ・hash不一致・renderer故障・
  metric実装ミス・契約どおりに処理されていない場合）→ 修正可。同じ
  design revision で新 attempt。修正理由と diff を記録する。
- **SCIENTIFIC NULL / NO DIFFERENCE**（実装も評価も設計どおり正常に
  動いたが、差分が生まれない場合）→ 結果として凍結。成功するまで
  parameter や threshold を調整しない。「差分未成立」は有効な科学結果。
- **DESIGN FAILURE / UNOBSERVABLE**（制御層では対象を表現できない、
  評価器では原理的に区別できない、稽古の情報設計では差分生成が成立しない
  等、設計自体が観測不能な場合）→ 現 revision を凍結し、新 design
  revision で再構築する。

原則: 「コードが設計どおり動いていないなら直す。設計どおり動いた結果、
差がないならその結果を受け入れる。」結果を見た後の lesson 変更・
threshold 緩和・片方だけの探索追加で同じ attempt を救済してはならない。

### Holdout と評価漏洩（PoR §12）

稽古・教育とも、train 素材と評価素材を分離する。

- **PRACTICE**: training split の PJS 音声は観察可能。sealed holdout の
  教師音声/評価 target は学習中に使用禁止。
- **EDUCATION**: training split から生成した Technique lesson のみ使用。
  sealed holdout Technique は学習終了後に開封。

RUN9 では一般化の精密証明までは要求しないが、**train-only gain と
held-out gain は別結果として必ず記録する**。

---

## 改訂 F — 人間知覚 Gate の非必須化

v0.1 §17「Mandatory with Audit Fallback」（perceptual identity retention
/ PJS leakage の blind human audit routing）を、rev 0.3 では次のとおり
改める（PoR §6/§7）:

> RUN9 v0.3 は原理確認を優先する。以下はこの段階の必須成功条件にしない
> （PoR §6 列挙）: 人間が「同一人物の声」と知覚することの高精度証明 /
> 人間が「歌が上手くなった」と評価すること / 商用品質 / 自然さ・魅力・
> 感情表現の完成度 / 人間による大規模 blind audit / 任意の話者・任意の
> 教師への一般化 / 次世代交配への適格性 / 最適 Founder の選抜。
>
> **人間知覚 Gate は必須にしない**。機械評価の限界は claim ceiling に
> 明記し、後続 Run で校正する（PoR §7 末尾）。人間知覚による精密化は
> 後続 Run へ送る。

v0.1 §28「Human Audit」（Mandatory with Audit Fallback）は rev 0.3 では
**optional 化**する。実施する場合の運用手順（`MAX_HUMAN_AUDIT_PAIRS`
=12 の上限等、v0.1 §17/§28 の枠組み自体）は変更しないが、RUN9 v0.3 の
成功条件からは外す。

---

## v0.1 / rev 0.2 との矛盾解決表

以下の v0.1 条項は rev 0.3 が上書きする。他の v0.1 条項（凍結対象列挙
= Backbone/Genome/Identity coordinate/speaker embedding/model weights、
TRI_CROSSOVER operator、genome_id 決定論など）は rev 0.2 と同様に不変の
まま継承する。

| v0.1 条項 | rev 0.3 による上書き内容 |
|---|---|
| §13 単一 mode（`LEARN_PERFORMANCE`、rev 0.2 改訂1で ControlProfile 化済み） | 改訂A: CONTROL / PRACTICE_FROM_AUDIO / TRANSFER_TECHNIQUE の三枝へ分岐。両介入枝の書き込み先は引き続き Performance ControlProfile（rev 0.2 踏襲） |
| §20 結果分類（`transfer_status` 語彙） | 改訂D: PoR §13 の6分類（BIRTH/PRACTICE/EDUCATION/SEPARATION/FOUNDER_RESPONSE/IDENTITY）へ superseded |
| §23 単一介入エッジ（`single_intervention.changed_edge` = `LEARN_PERFORMANCE`） | 改訂A: `interventions`（`edges` = [`PRACTICE_FROM_AUDIO`, `TRANSFER_TECHNIQUE`] + `control_branch` = `CONTROL`）構造へ改訂。旧形式は contract loader が fail-closed で拒否する |
| §17 Mandatory with Audit Fallback（perceptual identity / PJS leakage の人間監査） | 改訂F: 機械評価 + claim ceiling 明記へ変更。人間知覚 Gate は必須ではない |
| §28 Human Audit | 改訂F: optional 化（実施手順の枠組み自体は不変） |
| §19 R9-G5（BIRTH_IDENTITY_SEPARATION） | 改訂F（PR #317 Codex bot レビュー第2巡 Fix 5 採用）: R9-G5 は**機械計測の出生分離ゲートとして存続**する — 判定基準は v0.1 §10.3 の第一基準（between-founder distance > within-founder replay distance の 95th percentile）そのもので、これは replay-noise 対照の機械計測であり人間聴取を要さない。rev 0.3 改訂 F により **blind human audit への fallback routing は除去**し、機械計測で分離が成立しない場合は PoR §13 の BIRTH: NOT_ESTABLISHED（または計測不能時 DESIGN FAILURE / UNOBSERVABLE）として正直に閉じる（人間聴取による救済はしない）。Birth Promotion の G0–G5 要件は機械判定版 G5 で読み替え |

rev 0.2（`DESIGN_RUN9_REVISION_0.2.md`）の改訂1〜5は、上記の矛盾しない
範囲でそのまま有効:

- 改訂1（Performance ControlProfile 方式・§対応マップ11項目）→ 改訂A の
  両介入枝の書き込み先として引き続き適用。
- 改訂2（AF0 anchor 規約）/ 改訂3（PJS provenance 規約）/ 改訂5
  （Shared Backbone）→ Identity/Backbone 側の pin 規約であり PoR とは
  独立、無改変で有効。
- 改訂4（User donor rights 規約）→ 無改変で有効。

---

## design_revision 系譜（byte-pin sha256 記録）

| revision | 文書 | sha256（実バイト） |
|---|---|---|
| v0.1（正本、無改変） | `DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md` | `b1f6901c0ba8bcfcbd61170aa672c95e96a37d082fce5e3f12f245bc4faaae1e` |
| 0.2（無改変・存続） | `DESIGN_RUN9_REVISION_0.2.md` | `406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb` |
| 0.3（本文書） | `DESIGN_RUN9_REVISION_0.3.md` | `RUN9_CONTRACT.yaml` の `design_revision_doc_sha256` が PINNED で保持（本文書は本文書自身の sha256 を内部に書けないため実測は contract 側を正とする） |
| PoR 裁定ソース（無改変・byte-pin） | `POR_CONCEPT_ADJUDICATION_20260824.txt` | `56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007` |
