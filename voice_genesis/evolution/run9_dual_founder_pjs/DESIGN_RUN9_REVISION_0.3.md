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
> versioned ControlProfile 等の制御可能領域を更新する）。ただし
> **書き込み境界は下記「書き込み境界（P1-1）」節が定める型付き分割に
> 従う** — PRACTICE_FROM_AUDIO と TRANSFER_TECHNIQUE が同一
> ControlProfile を更新するからといって、両者が同じ内容を書けるわけでは
> ない。
>
> **r0 は in-place 更新しない**（PoR §10 最優先の不変条件）。各枝は
> `BRANCH_REVISIONS`（`run9_schema.py`）が定める独立 Revision 系列
> （CONTROL → C0=`replay`/C1=`r_sham`（下記「CONTROL 内部の C0/C1 分離」
> 参照）、PRACTICE_FROM_AUDIO → `r_practice`、TRANSFER_TECHNIQUE →
> `r_taught`）として保存する。
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
| 稽古 | `PRACTICE_FROM_AUDIO`（`INTERVENTION_EDGES[0]`） | Founder 別 versioned Performance ControlProfile（TRAIT_CONTROL/TECHNIQUE_CONTROL のみ） | `r_practice` |
| 教育 | `TRANSFER_TECHNIQUE`（`INTERVENTION_EDGES[1]`） | Founder 別 versioned Performance ControlProfile（TECHNIQUE_CONTROL のみ） | `r_taught` |
| （無介入対照 C0） | `CONTROL`（`CONTROL_BRANCH`）/ `NO_LEARNING_REPLAY` | 学習 step を実行しない | `replay` |
| （無介入対照 C1） | `CONTROL`（`CONTROL_BRANCH`）/ `ZERO_CONTROLPROFILE_SHAM` | 中立 ControlProfile 付与のみ（学習 step なし） | `r_sham` |

### 書き込み境界（User 外部レビュー PR #317 P1-1 採用）

**現状の不備**: 上表のとおり PRACTICE_FROM_AUDIO と TRANSFER_TECHNIQUE が
同一の Performance ControlProfile を書き込み先とするだけでは、
「ControlProfile が Technique しか表現しない場合、稽古による Trait 学習を
原理的に観測できない」「ControlProfile が Trait も表現する場合、教育枝
までTraitを書き換えられ、型だけの伝達ではなくなる」のどちらかに陥る —
入力境界（改訂C）だけが非対称で、書き込み境界が対称だった。

**修正**: ControlProfile の可変領域を最低限、型付きで分割する
（`run9_schema.STATE_PARTITIONS`）。

1. `IDENTITY_STATE` — 誰の声として出生したか（Genome/anchor構成・出生時
   の声質方向）。
2. `TRAIT_CONTROL` — 明示的に許可された発声制御領域の後天的変化を表す
   制御パラメータ領域。
3. `TECHNIQUE_CONTROL` — 歌唱の型・技術を表す制御パラメータ領域。

枝ごとの書込許可（`run9_schema.BRANCH_WRITABLE_PARTITIONS`、
`inputs/branch_write_policy.json` が同内容を人間可読な形で保持し、
両者の一致を loader が強制する）:

| 枝 | writable partitions |
|---|---|
| `CONTROL` | `[]`（空 — 学習 step を実行しないため） |
| `PRACTICE_FROM_AUDIO` | `[TRAIT_CONTROL, TECHNIQUE_CONTROL]` |
| `TRANSFER_TECHNIQUE` | `[TECHNIQUE_CONTROL]` |

`IDENTITY_STATE` は `run9_schema.IMMUTABLE_STATE_PARTITIONS` によりどの
枝からも書込不可（EDUCATION が TRAIT_CONTROL または IDENTITY_STATE へ
書き込もうとした場合、`run9_schema.validate_branch_write()` が
fail-closed で `Run9ValidationError` を送出する）。

全枝で次を不変とする（`run9_schema.BRANCH_IMMUTABLE_ARTIFACTS`。
state partition ではなく ControlProfile の外側にある永続 artifact）:
Shared Backbone / Founder Genome / Identity coordinate / speaker
embedding / model weights / r0 bytes。

**PRACTICE で許す Trait 変化の定義**（修正指示6）: `TRAIT_CONTROL`
partition が表す Trait 変化は、speaker embedding や Genome の変更**では
ない**。あくまで「明示的に許可された発声制御領域の後天的変化」に限定
される — PRACTICE_FROM_AUDIO が `TRAIT_CONTROL` を writable とすることは、
Founder の identity そのものを動かす経路を開くことを意味しない
（`IDENTITY_STATE`/`BRANCH_IMMUTABLE_ARTIFACTS` は引き続き不変）。

### CONTROL 内部の C0/C1 分離（User 外部レビュー PR #317 P1-3 採用）

旧 rev 0.3 は CONTROL を「無介入 replay」の単一概念として扱う一方、
rev 0.2 改訂1の「C1 Zero ControlProfile / Sham Transition」の意味も
同じ CONTROL 枝に担わせていた。しかし旧設計（v0.1）では次は別の対照
である:

- **C0 No-Learning Replay**（`run9_schema.CONTROL_CONDITIONS[0]` =
  `NO_LEARNING_REPLAY`）: r0 をそのまま再 render し、renderer/backend/
  PCM の自然変動（noise floor）を測る。
- **C1 Zero ControlProfile / Sham Transition**
  （`CONTROL_CONDITIONS[1]` = `ZERO_CONTROLPROFILE_SHAM`）: 中立
  ControlProfile を付与し、ControlProfile 機構を通すこと自体の副作用を
  測る（学習 step は実行しない）。

この二つを一つに混同すると、render/replay noise と profile 適用機構の
副作用を分離できない。**CONTROL 枝の内部に二つの必須 control condition
を持たせる**:

```
CONTROL
  ├─ C0_NO_LEARNING_REPLAY        -> revision "replay"
  └─ C1_ZERO_CONTROLPROFILE_SHAM  -> revision "r_sham"
```

評価用途:

- **C0**: natural replay/noise floor の校正。Practice/Education gain の
  基準ノイズは C0 由来の値から算出する。
- **C1**: ControlProfile 適用機構の副作用校正。profile 機構の副作用は
  C1−C0 として別記録する（C1 単体の値ではなく C0 との差分）。

**評価 readiness**: `run9_schema.control_conditions_satisfied()` が
`CONTROL_CONDITIONS` の全件（C0 と C1 の両方）が揃っているかを判定する
— **C0/C1 の片方が欠けた attempt は評価 READY にならない**。gain 基準
ノイズ（C0 由来）と profile 副作用記録（C1−C0）はどちらも両条件の存在を
前提とするため。

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
`PRACTICE_FORBIDDEN_INPUTS` / `PRACTICE_ALLOWED_DATA_INPUTS` /
`PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS` /
`PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE` / `EDUCATION_ALLOWED_CHANNELS` /
`EDUCATION_FORBIDDEN_INPUTS` を正本とする（将来の practice/education
builder がこれを import して検証する）。

### PRACTICE_FROM_AUDIO（稽古）

**禁止（データ入力として渡してはいけないもの）**（PoR §3.2）:

- PJS speaker embedding
- PJS Identity coordinate
- 「vibrato=この値」等の正解 Technique parameter
- 教師付与の Technique label（例:「これは vibrato の見本」等のラベル付け
  そのものの供与 — 正解 parameter 本体とは別に、ラベルという形での
  Technique 情報供与も禁止する。PR #317 Codex bot レビュー第2巡 Fix 4/
  第3巡 Fix 7 採用: `run9_schema.PRACTICE_FORBIDDEN_INPUTS` の
  `teacher_technique_label` と同期）
- 教師内部モデルの parameter dump

**3分割の意味論**（User 外部レビュー PR #317 P2-1 採用）: 旧
`PRACTICE_ALLOWED_INPUTS` は「データ」（音声そのもの）と「動作」（特徴
抽出・目標選択・差分推定・探索）を1つのタプルに混在させており、将来の
builder がこれを単純な入力 allowlist として扱うと「Founder 自身が差分
抽出する」という actor 境界（誰が/何を渡され/何を自分でするか）を正確に
強制できなかった（動作結果だけを外部から渡して「入力として許可されて
いる」と偽装できてしまう）。3つの語彙へ分割する（旧 1 タプルは廃止・
後方互換エイリアスなし）:

**許可データ入力**（`PRACTICE_ALLOWED_DATA_INPUTS`。Founder へ実際に
渡してよい「情報」そのもの）:

- PJS training audio（training split の教師音声そのもの）
- Founder 自身の self-render（自分の現在の出力）
- 全枝共通の score/lyrics/task context（教師 Performance から導出した
  正解情報ではない場合のみ）

**必須の Founder-local 処理**（`PRACTICE_REQUIRED_AUTONOMOUS_OPERATIONS`。
外部から代行させてはならない、Founder 自身が実行しなければならない
動作 — 「同じ feature extractor コードを利用する」こと自体は禁止しない
が、抽出という行為自体は Founder 側で実行されなければならない）:

- feature extraction（特徴抽出）
- imitation target selection（模倣対象の選択）
- self/teacher difference estimation（自己と教師の差分推定）
- candidate generation（候補生成）
- allowed-range search（許可された可変領域内の探索）

**明示的に禁止する外部支援**
（`PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE`。教師側で既に計算済みの
「答え」を PRACTICE learner へ入力として渡す経路 — Founder 自身の自律
処理を代行・迂回してしまうため禁止）:

- precomputed_teacher_technique_features（教師側で事前計算した
  Technique 特徴量）
- externally_selected_imitation_target（外部が選んだ模倣対象）
- teacher_derived_diff_vector（教師由来の差分ベクトル）
- correct_target_trajectory（正解の目標軌道）
- teacher_loss_gradient（教師由来の損失勾配）
- education_lesson_reference（EDUCATION 用 lesson への参照 — 稽古と
  教育の情報経路を混同させない）
- speaker/identity embedding
- teacher internal parameter dump

**practice trace の保存**: Founder が選択した模倣対象・内部差分・探索
履歴は、practice trace として保存する（結果の分離観測に必要な監査証跡
— PoR §7 の「どの層が動いたか」を事後に検証可能にする）。

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

### 科学結果・運用状態・保存・昇格の完全分離（User 外部レビュー PR #317 P1-4 採用）

**問題**: rev 0.3 の矛盾解決表は当初 `transfer_status` のみを
supersede しており、v0.1 §20 の overall PASS / PASS_WITH_RESIDUAL・
§21 の CANONICAL_LEARNED_REVISION / Parent Pool 候補登録・freeze only
on PASS / PASS_WITH_RESIDUAL・PASS 系 verdict template・「結果と昇格の
結合」を明示的に置き換えていなかった。このままでは将来の実装者が、6分類
を記録した後に旧 PASS へ再集約し、そのPASSから昇格を導出できてしまう。

**修正**: 科学結果と運用状態を完全に分離する4つの語彙を導入する
（互いに素であり、いずれの語彙からも他方を機械的に導出する関数は
`run9_schema.py` に存在しない — 「6分類 → 単一 PASS/TotalScore」を生成
する関数は意図的に実装しない）。

1. **scientific_outcomes**（上表の6分類 = BIRTH/PRACTICE/EDUCATION/
   SEPARATION/FOUNDER_RESPONSE/IDENTITY）— 何が観測されたかの科学的
   記述。
2. **run_status**（`run9_schema.RUN_STATUSES` = `COMPLETE` /
   `BLOCKED` / `IMPLEMENTATION_FAILED` / `DESIGN_FAILED`）— 実行が
   どう終わったかだけを示し、科学的優劣・PASS/FAIL を表さない。
   overall PASS を「科学的総合結果」として使わない。
3. **archive_status**（`run9_schema.ARCHIVE_STATUSES` = 単一値
   `IMMUTABLE_ARCHIVED` のみ）— 保存するかどうかの分岐自体を存在させ
   ない。gain 成立・NO_GAIN/NO_TRANSFER・SCIENTIFIC_NULL・
   DESIGN_FAILURE/UNOBSERVABLE・Identity SHIFTED・incomplete/failed
   attempt の証拠を含む**全 terminal attempt**が対象。
4. **promotion_status**（`run9_schema.PROMOTION_STATUSES` = 単一値
   `ARCHIVE_ONLY_PENDING_USER_RULING` のみ）— RUN9 単体の結果からは
   絶対に昇格値へ到達できないことを語彙レベルで保証する。

**RUN9 v0.3 で禁止する4項目**:

- 自動 Parent Pool 登録
- CANONICAL_LEARNED_REVISION への自動昇格
- 片方だけの優良 Founder 選抜
- PASS からの自動繁殖適格判定

Parent Pool 登録・CANONICAL_LEARNED_REVISION への昇格が必要になった
場合は、別の User ruling pin（新しい design_revision を要する別の
User 裁定）を要する — `PROMOTION_STATUSES` を拡張することそのものが
新しい裁定行為である。

**保存と昇格の区別を明記する**:

- **attempt evidence archive**（`archive_status`）: 全 terminal
  outcome（NO_GAIN/DESIGN_FAILURE/UNOBSERVABLE を含む）で無条件に
  作成する。「freeze only on PASS / PASS_WITH_RESIDUAL」は、少なくとも
  科学証拠の保存についてはこの規律で **supersede** する — DESIGN_FAILURE
  であっても証拠は削除・不作成にできない。
- **promotion/canonical freeze**（`promotion_status`）: 後続 User 裁定
  後のみ作成する。RUN9 v0.3 単体の実行では作成されない。

### held-out gain の必須化（User 外部レビュー PR #317 P2-4 採用）

PoR §7 item 7「Generalization gain（実装可能な範囲）」という努力目標的
な表現と、改訂E「train-only gain と held-out gain は別記録」という必須
規律を混同しない。**held-out gain は「実装可能なら」ではなく、RUN9 の
最低限の評価漏洩防止として必須**である。

必須4欄（`run9_schema.REQUIRED_GAIN_FIELDS`）:

- `practice_train_gain`
- `practice_heldout_gain`
- `education_train_gain`
- `education_heldout_gain`

任意・後続精密化欄（`run9_schema.OPTIONAL_GENERALIZATION_FIELDS` —
RUN9 単体では一般化の精密証明を要求しないため、実装できる範囲でのみ
記録すればよい）:

- `broad_generalization_gain`
- `cross_song_generalization`
- `cross_register_generalization`

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

### human_audit_mode の事前固定（User 外部レビュー PR #317 P2-2 採用）

旧実装の `gate_state()` は `human_evaluation_protocol_sha` を、status が
PENDING/BLOCKED であっても一律に無視していた。RUN9 v0.3 が機械判定を
優先する方針自体は正しいが、これでは「監査を実施しない」と「監査を予定
したが準備できていない」を区別できず、結果を見た後に advisory audit を
追加する余地が残る。

`RUN9_CONTRACT.yaml` に明示モード欄 `human_audit_mode`（pin 欄ではなく
通常欄。語彙 `run9_schema.HUMAN_AUDIT_MODES`）を追加する:

- **DISABLED**（既定値。今回の User 裁定に従う）: `human_evaluation_
  protocol_sha` は Gate 対象外（`CONTRACT_OPTIONAL_PIN_FIELDS` のまま）。
- **ADVISORY_PREDECLARED**: `human_evaluation_protocol_sha` が pre-run で
  **PINNED 必須**（`gate_state()` が optional 除外を差し戻す）。

条件（規律・機械強制ではなく運用規律として明記）:

- holdout 開封後の `human_audit_mode` 変更は禁止する。
- 人間監査を SCIENTIFIC_NULL や Identity SHIFTED の救済に使わない —
  advisory audit の結果で `NO_GAIN`/`SHIFTED` 等の機械判定結果を
  覆さない。

---

## 改訂 G — 機械的校正の定義（User 外部レビュー PR #317 P2-3 採用）

rev 0.2 は `calibrated Identity audit route` を Entry Gate に残していた
一方、rev 0.3 は人間知覚 Gate を非必須にし（改訂F）、IDENTITY 結果に
`UNCALIBRATED`（未校正）を許している（改訂D）。この二つの関係を明示する。

**RUN9 v0.3 で要求する「校正」は、人間知覚との一致証明ではない**。次の
機械的校正として定義する:

- C0 replay 分布（`NO_LEARNING_REPLAY` から得られる natural noise の
  分布）
- C1 sham 副作用（`ZERO_CONTROLPROFILE_SHAM` から得られる profile
  適用機構の副作用）
- positive/negative reference（既知の陽性・陰性対照サンプル）
- metric version（測定指標の版）
- threshold generation rule（閾値の生成規則そのもの）

**結果規則**:

1. 校正済み machine metric のみが `STABLE_BY_MACHINE_METRIC`
   （IDENTITY_OUTCOMES）を出せる。
2. 未校正なら `UNCALIBRATED` とする。
3. `UNCALIBRATED` のときは Identity 保持を主張しない — Practice/
   Education の差分値は記録できても、「Identity を保ったまま獲得した」
   という主張はしない。
4. threshold は sealed holdout 開封前に freeze する（改訂E の holdout
   規律と同じ「結果を見た後に閾値を緩めない」原則の threshold への
   適用）。

## 改訂 H — Non-Claim / Rights Boundary（AQUEST 接続、User 外部レビュー PR #317 P2-5 採用）

PoR §15「AQUEST 許諾検討との接続」を rev 0.3 へ明示的に継承する。以下は
本 Run が確立する Non-Claim（RUN9 が主張しないこと）と権利境界である:

1. 技術的に Identity/Trait/Technique を分離できても、法的・契約上の
   許諾が自動成立するわけではない。
2. 教育 lesson が speaker embedding を含まなくても、元音声の利用権確認
   は別途必要である。
3. 稽古で raw audio を聞かせる経路は、入力音声の許諾範囲に拘束される。
4. AQUEST 由来素材は明示許諾が得られるまで RUN9 input へ追加しない。
5. 本 Run の分離結果は、将来「何を入力し、何を保存し、何を移すか」を
   説明する技術資料であり、**許諾の代替ではない**。

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
| §20 overall PASS / PASS_WITH_RESIDUAL | 改訂D（User 外部レビュー PR #317 P1-4 採用）: overall PASS を「科学的総合結果」として使わない。scientific_outcomes（6分類）と run_status（`RUN_STATUSES`）を分離し、run_status は実行完了状態だけを示す。PASS/PASS_WITH_RESIDUAL という単一集約語彙自体を rev 0.3 では使用しない |
| §21 CANONICAL_LEARNED_REVISION / Parent Pool 候補登録 | 改訂D: promotion_status（`PROMOTION_STATUSES` = 単一値 `ARCHIVE_ONLY_PENDING_USER_RULING`）へ superseded。RUN9 単体の結果からは昇格値へ到達できない。自動 Parent Pool 登録・CANONICAL_LEARNED_REVISION への自動昇格は rev 0.3 で禁止（別の User ruling pin が必要） |
| results bundle の freeze only on PASS / PASS_WITH_RESIDUAL 規則 | 改訂D: archive_status（`ARCHIVE_STATUSES` = 単一値 `IMMUTABLE_ARCHIVED`）が supersede する。少なくとも科学証拠の保存については、PASS 系判定の有無に関わらず全 terminal outcome（NO_GAIN/DESIGN_FAILURE/UNOBSERVABLE/Identity SHIFTED を含む）で無条件に archive を作成する |
| §27 test item 52（該当する PASS 系検証項目） | 改訂D: 「単一 PASS からの昇格可否判定」を検証する項目は、「6分類から単一 TotalScore/PASS を自動生成しない」「promotion_status が RUN9 結果だけでは昇格値にならない」という rev 0.3 の必須テストへ読み替える |
| PASS 系 verdict template | 改訂D: verdict は scientific_outcomes（6分類の値）・run_status・archive_status・promotion_status の4つを独立に報告する形へ改める。単一 verdict 文字列（PASS/FAIL 等）へ集約するテンプレートは rev 0.3 では使用しない |

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
