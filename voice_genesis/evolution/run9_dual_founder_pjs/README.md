# RUN9 — Tri-Donor Dual-Founder Common-Teacher Learning

**状態: Preregistered / Phase 0.3（design_revision 0.3）。本学習未開始。**

正本設計書: [`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`](./DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md)
（uploads 原本とバイト同一・**byte-pin 不変**。sha256 は `RUN9_CONTRACT.yaml` の
`design_doc_sha256` が PINNED で保持する）。v0.1 に対する差分は
[`DESIGN_RUN9_REVISION_0.2.md`](./DESIGN_RUN9_REVISION_0.2.md)（2026-08-24
User 裁定5件、無改変のまま存続）→
[`DESIGN_RUN9_REVISION_0.3.md`](./DESIGN_RUN9_REVISION_0.3.md)（同日、PoR メモ
[`POR_CONCEPT_ADJUDICATION_20260824.txt`](./POR_CONCEPT_ADJUDICATION_20260824.txt)
の編入。`design_revision_doc_sha256`/`por_adjudication_sha256` が PINNED で
保持する）の順で規定する。

AF0・Ritsu・User Donor の三点 Identity から `TRI_CROSSOVER` で二体の
Founder 候補（`R9F-01` = AF0 優勢、`R9F-02` = User 優勢）を出生
（**INHERIT_TRAIT**）させ、以後 r0 から **CONTROL**（無介入 replay）/
**PRACTICE_FROM_AUDIO**（稽古 — PJS 教師音声そのものから自律的に差分を
学習）/ **TRANSFER_TECHNIQUE**（教育 — 抽出済み Technique lesson を明示的
に受け取り再現）の三経路へ分岐させ、声（Identity）・発声形質（Trait）・
歌唱技術（Technique）がどの経路でどこまで移動・獲得されるかを分離観測する
実験（rev 0.3、PoR §1）。詳細は設計書 §0/§1、および
[`DESIGN_RUN9_REVISION_0.3.md`](./DESIGN_RUN9_REVISION_0.3.md) を参照。

## RUN9 v0.3 の最小実験図（PoR §16）

```
Identity donors
 AF0 / Ritsu / User
        |
        | INHERIT_TRAIT
        v
  +-------------+          +-------------+
  |  R9F-01:r0  |          |  R9F-02:r0  |
  +------+------+          +------+------+
         |                        |
   +-----+-----+            +-----+-----+
   |     |     |            |     |     |
   v     v     v            v     v     v
CTRL PRACT EDU             CTRL PRACT EDU
      |     |                    |     |
      |     +-- Technique ------+     |
      |         supplied               |
      |
      +-- PJS audio observed directly
          learner extracts differences autonomously
```

各枝を機械評価: Identity / Trait / Technique / Gain / Replay /
Generalization（単一 Total Score への集約は恒久禁止）。書き込み先は
両介入枝とも Founder 別 versioned Performance ControlProfile
（rev 0.2 改訂1を踏襲）だが、**書き込める state partition は枝ごとに
異なる**（`run9_schema.BRANCH_WRITABLE_PARTITIONS`: CONTROL=`[]` /
PRACTICE_FROM_AUDIO=`[TRAIT_CONTROL, TECHNIQUE_CONTROL]` /
TRANSFER_TECHNIQUE=`[TECHNIQUE_CONTROL]`。`IDENTITY_STATE` は全枝で
書込不可）。CONTROL 枝は内部に C0（`NO_LEARNING_REPLAY`）/ C1
（`ZERO_CONTROLPROFILE_SHAM`）の2条件を持つ。Revision 系列は
CONTROL/C0→`replay` / CONTROL/C1→`r_sham` / PRACTICE_FROM_AUDIO→
`r_practice` / TRANSFER_TECHNIQUE→`r_taught`（`run9_schema.BRANCH_REVISIONS`）。

## 2026-08-24 User 裁定5件（Revision 0.2）— 要約

詳細・逐語は [`DESIGN_RUN9_REVISION_0.2.md`](./DESIGN_RUN9_REVISION_0.2.md) を正とする。

1. **学習アーキテクチャ**: `LEARN_PERFORMANCE` の書き込み先を Performance
   Adapter から Founder ごとの versioned **Performance ControlProfile**
   （非ニューラル・明示制御パラメータ）へ変更。Adapter への自動昇格は禁止。
   凍結対象（Backbone/Genome/Identity coordinate/speaker embedding/model
   weights）は不変。v0.1 §13.2 の正規の scope downgrade 記録手続きに基づく。
2. **AF0 anchor 規約**: `anchor_hashes.af0` は `inputs/af0_anchor_manifest.json`
   の正規形 sha256 を pin（**PINNED 済み**）。WAV 実体再生成時は
   `SHA256SUMS.txt` との全件一致必須・不一致時は repin せず停止。
3. **PJS provenance 規約**: source archive pin と expanded corpus pin を
   役割別に区別（**どちらも正しい値・矛盾ではなかった** — 旧ブロッカー(3)
   は誤認と判明し解消）。RUN9 消費用の Lesson manifest は別途生成し
   `lesson_sha` として pin。
4. **User donor rights 規約**: `inputs/rights_manifest.json`
   （Fable 起草・User attest 方式）を追加。`rights_class`/`consent_status`
   = `PENDING_USER_ATTESTATION`。raw 公開・モデル一般配布は別承認
   （初期値 `not_granted`）。
5. **Shared Backbone**: RUN6 phase B 40K checkpoint を採用
   （`backbone_checkpoint_sha` **PINNED 済み**、直接記録4件一致。RUN7 は
   教師交代混入回避のため不使用）。`inputs/backbone_runtime_bundle.json`
   に config/speaker map/phoneme dictionary/vocoder/render 用 DiffSinger
   commit に加え、**render フロー（`gate_synth.py --canon-model-dir`）が
   消費する canon model assets**（linguistic/duration/pitch predictor
   ONNX + phoneme vocabulary、`NamineRitsu_DiffSinger.zip` 由来）と
   acoustic export companions（dsconfig.yaml/phonemes.json/speaker
   embedding）まで含めて記録する — acoustic checkpoint/ONNX だけでは
   再現入力集合は閉じない（PR #316 第2巡指摘採用）。ただし
   `backbone_runtime_bundle_sha` 自体は **PENDING**（bundle 内
   `render_code_commit` が `INFERRED_UNCONFIRMED` —
   Codex bot レビュー PR #316 第1巡指摘採用。ブロッカー(5)参照）。

## 2026-08-24 PoR メモ編入（Revision 0.3）— 要約

詳細・逐語は [`DESIGN_RUN9_REVISION_0.3.md`](./DESIGN_RUN9_REVISION_0.3.md) を正とする。
PoR メモは自称 "v0.2 design revision input" だが、rev 0.2（上記5裁定）が
既にマージ済みのため本編入は rev 0.3 として発行する（意味上は User の言う
「v0.2」に相当）。rev 0.2 文書は無改変のまま存続し、矛盾しない内容
（ControlProfile 方式・§対応マップ・AF0/PJS/User rights/Backbone の pin
規約）はそのまま有効。

1. **改訂A（三経路分離 + 書き込み境界 + C0/C1 分離）**: 単一
   `LEARN_PERFORMANCE` エッジを廃し、Founder r0 から **CONTROL**（無介入
   replay。内部に C0=`NO_LEARNING_REPLAY`/C1=`ZERO_CONTROLPROFILE_SHAM`
   の2条件）/ **PRACTICE_FROM_AUDIO**（稽古）/ **TRANSFER_TECHNIQUE**
   （教育）の三枝へ分岐。両介入枝の書き込み先は Founder 別 versioned
   Performance ControlProfile（rev 0.2 改訂1を踏襲）だが、書き込める
   state partition は枝ごとに固定（`STATE_PARTITIONS` =
   IDENTITY_STATE/TRAIT_CONTROL/TECHNIQUE_CONTROL、
   `BRANCH_WRITABLE_PARTITIONS` — 詳細は下記「User 外部レビュー対応」
   節）。r0 は in-place 更新せず、各枝は独立 Revision（C0=`replay`/
   C1=`r_sham`/`r_practice`/`r_taught`）として保存。交配の正式 Edge 名は
   **INHERIT_TRAIT**（operator は `TRI_CROSSOVER/1.0` のまま — genome_id
   決定論を壊さない）。
2. **改訂B（三層観測）**: Identity / Trait / Technique の三層を機械評価の
   観測軸として採用。「PJS へ近づいた」を単一 Total Score で判断せず、
   どの層が近づいたかで解釈を分ける（Technique だけ=技術獲得の証拠、
   Trait も=稽古による形質学習候補、Identity まで=drift として別記録）。
3. **改訂C（情報境界、3分割語彙）**: PRACTICE は PJS 音声そのものを許可・
   正解 parameter/Technique label/speaker embedding を禁止。「データ入力」
   「必須の Founder-local 処理」「明示的に禁止する外部支援」を3分割語彙
   （`PRACTICE_ALLOWED_DATA_INPUTS`/`PRACTICE_REQUIRED_AUTONOMOUS_
   OPERATIONS`/`PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE`）で固定。
   EDUCATION は timing/pitch trajectory 等の Technique channel を許可・
   speaker embedding/Identity coordinate/**learner の PJS raw audio 直接
   参照**を禁止。この非対称性自体が実験変数（PoR §11）。
4. **改訂D（比較構造・結果分類・科学結果と運用状態の分離）**: PoR §4 の
   5比較（出生差/稽古効果/教育効果/稽古教育差/個体差）+ §13 の6分類
   （BIRTH/PRACTICE/EDUCATION/SEPARATION/FOUNDER_RESPONSE/IDENTITY）を
   結果 schema 語彙として凍結。v0.1 §20 の `transfer_status` 語彙は
   superseded。**scientific_outcomes（6分類）/ run_status / archive_status
   / promotion_status を完全分離**し、RUN9 単体からは昇格値へ到達
   できない（`PROMOTION_STATUSES` = 単一値）。held-out gain 4欄
   （`REQUIRED_GAIN_FIELDS`）は必須。
5. **改訂E（公平性・失敗分類・holdout）**: PRACTICE と EDUCATION は情報量
   が異なるため「同じ入力」としない（枝内二体等予算）。失敗は
   IMPLEMENTATION_FAILURE（修正可）/ SCIENTIFIC_NULL（凍結）/
   DESIGN_FAILURE（新 revision）の3分類。train-only gain と held-out
   gain は別結果として記録。
6. **改訂F（人間知覚 Gate の非必須化 + human_audit_mode）**: v0.1 §17 の
   Mandatory with Audit Fallback を、機械評価 + claim ceiling 明記へ変更。
   人間知覚評価は後続 Run へ送る（v0.1 §28 Human Audit は optional 化）。
   `human_audit_mode`（既定 `DISABLED`、`ADVISORY_PREDECLARED` で
   protocol sha を pre-run PINNED 必須化）で監査意図を事前固定。
7. **改訂G（機械的校正の定義）**: RUN9 が要求する「校正」は人間知覚との
   一致証明ではなく、C0 replay 分布/C1 sham 副作用/positive・negative
   reference/metric version/threshold generation rule に対する機械的
   校正。未校正なら `UNCALIBRATED` とし Identity 保持を主張しない。
8. **改訂H（Non-Claim / Rights Boundary）**: 技術的分離が成立しても
   法的・契約上の許諾は自動成立しない。AQUEST 由来素材は明示許諾まで
   input へ追加しない、等 PoR §15 の権利境界を継承。

## User 外部レビュー対応（2026-08-24, CHANGES_REQUESTED → 全項目採用）

PR #317 head `71eeccadf3f1f7ee49d9cc90763ced8a506abc67` に対する User 本人
レビュー（P1×4 + P2×5、全件採用・逐語準拠）の要約。詳細・逐語は
[`DESIGN_RUN9_REVISION_0.3.md`](./DESIGN_RUN9_REVISION_0.3.md) 各節を正とする。

**P1（必須修正、4件）**:

1. **P1-1 書き込み境界**: 新規 [`inputs/branch_write_policy.json`](./inputs/branch_write_policy.json)
   （schema `run9-branch-write-policy/1.0`）が state partition
   （IDENTITY_STATE/TRAIT_CONTROL/TECHNIQUE_CONTROL）・枝別 writable 集合
   ・全枝不変 artifact リストを機械可読で保持。`run9_schema.py` の
   `STATE_PARTITIONS`/`IMMUTABLE_STATE_PARTITIONS`/
   `BRANCH_WRITABLE_PARTITIONS`/`BRANCH_IMMUTABLE_ARTIFACTS` が正本、
   `validate_branch_write(branch, partition)` が fail-closed 検証、
   `validate_branch_write_policy_manifest()` が manifest と定数の完全
   一致を強制（改変 manifest は load 失敗）。`branch_write_policy_sha`
   を pre-run pin として新設し **PINNED 済み**。
2. **P1-2 split pin の明示化**: `lesson_sha` →
   `education_technique_lesson_manifest_sha`、`practice_split_sha` →
   `practice_audio_split_manifest_sha` へ改名（両方 PENDING のまま）。
   manifest 最低要件は `PRACTICE_MANIFEST_REQUIRED_KEYS`/
   `EDUCATION_MANIFEST_REQUIRED_KEYS` + `validate_practice_split_
   manifest()`/`validate_education_lesson_manifest()`（schema 欄の
   自己宣言で種別を区別・取り違えは拒否・holdout∩training 混入拒否・
   Founder 別分岐構造拒否）。
3. **P1-3 C0/C1 分離**: `CONTROL_CONDITIONS` =
   (`NO_LEARNING_REPLAY`, `ZERO_CONTROLPROFILE_SHAM`)。
   `BRANCH_REVISIONS["CONTROL"]` を条件別2値
   （`NO_LEARNING_REPLAY`→`replay`、`ZERO_CONTROLPROFILE_SHAM`→
   `r_sham`）へ再構成。`control_conditions_satisfied()` が両条件の
   存在を評価 readiness の前提として判定。
4. **P1-4 結果と昇格の分離**: `RUN_STATUSES`/`ARCHIVE_STATUSES`/
   `PROMOTION_STATUSES` を凍結し scientific_outcomes（6分類）から完全
   分離。v0.1 §20 overall PASS 系・§21 昇格・freeze-only-on-PASS 規則・
   test item 52・verdict template の読み替えを矛盾解決表へ追加。
   6分類から単一 PASS/TotalScore を導出する関数は実装しない
   （RUN9 単体では promotion_status が昇格値を取り得ない）。

**P2（設計・契約への反映、5件）**:

- **P2-1**: `PRACTICE_ALLOWED_INPUTS`（旧・削除済み）を
  `PRACTICE_ALLOWED_DATA_INPUTS`/`PRACTICE_REQUIRED_AUTONOMOUS_
  OPERATIONS`/`PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE` へ3分割し、
  actor 境界（データ vs 動作 vs 禁止支援）を機械可読に固定。practice
  trace 保存要件を明記。
- **P2-2**: `human_audit_mode`（pin 欄でなく通常欄、既定 `DISABLED`）を
  contract へ追加。`ADVISORY_PREDECLARED` 時は
  `human_evaluation_protocol_sha` PINNED を `gate_state()` が要求。
- **P2-3**: 「機械的校正の定義」節を新設（人間知覚一致証明ではなく
  C0/C1/reference/metric version/threshold 生成規則に対する校正）。
- **P2-4**: `REQUIRED_GAIN_FIELDS`（4欄、必須）+
  `OPTIONAL_GENERALIZATION_FIELDS`（3欄、任意）を凍結。
- **P2-5**: 「Non-Claim / Rights Boundary（AQUEST 接続）」節を新設
  （PoR §15 の5項目を逐語継承）。

**不変制約（遵守確認済み）**: PoR txt / v0.1 / rev 0.2 / 既存
AF0・Ritsu・rights・backbone pin 値・`TRI_CROSSOVER/1.0`・genome_id 計算
は無変更。既存 Codex bot レビュー3件（第1〜3巡）の修正も退行なし。

## 実行順 §22 に対する現在地マップ

設計書 §22 は 0–20 の実行順を規定する（v0.1 本文の番号は不変。rev 0.3
以降、step 5–20 が三経路化の影響を受ける — 詳細は
`DESIGN_RUN9_REVISION_0.3.md` 改訂A/C）。Phase 0.3 時点の現在地:

| step | 内容 | 状態 |
|---|---|---|
| 0 | freeze Run Contract | **部分 pin が拡大**（`design_doc_sha256` / `design_revision_doc_sha256` / `por_adjudication_sha256` / `backbone_checkpoint_sha` が新たに PINNED。`backbone_runtime_bundle_sha` は bundle 内 `render_code_commit` が INFERRED_UNCONFIRMED のため PENDING のまま。`interventions` 構造（旧 `single_intervention`）へ改訂済み。他も正直に PENDING。`gate_state()` は依然 `BLOCKED`） |
| 1 | verify repository / dependency pins | 未着手（backbone 側は pin 済み。VG-L0 ハーネス自体の依存 pin は未着手） |
| 2 | verify donor and teacher rights / manifests | **AF0/Ritsu は pin 済み・PJS は役割別2値を整理して解消**。**User donor のみ rights attest 待ち**（ブロッカー(1)参照） |
| 3 | build run9 Identity Domain | **af0/ritsu が PINNED、user/metric_space_sha はプレースホルダのまま**（`domains/identity_domain_run9_v1.json`、`is_pinned() == False`） |
| 4 | generate R9F-01:r0 and R9F-02:r0（INHERIT_TRAIT） | **未着手**（`run9_schema.build_founder()` は未 pin domain を構造的に ValueError で拒否する — step 3→4 の機械強制。user anchor 未 pin のため依然ブロック） |
| 5–20 | render / freeze / lesson / learning / evaluation / verdict | **未着手・rev 0.3 で三枝化**（VG-L0 学習ハーネス自体が未実装 — ブロッカー(3)参照。ハーネス実装時に CONTROL/PRACTICE_FROM_AUDIO/TRANSFER_TECHNIQUE の3経路分の render/lesson/learning/evaluation を実装する必要がある — ブロッカー(6)参照） |

## ブロッカー一覧（正直な現状）

**解消済み（2026-08-24 User 裁定）**:
- ~~AF0 canonical Body hash がローカル results のみ~~ → `inputs/af0_anchor_manifest.json`
  経由で `anchor_hashes.af0` を PINNED 化（AF-P0 の NOT_ESTABLISHED 判定・
  Duration/Energy/AG-alpha 非保持は不変のまま継承）。
- ~~PJS corpus sha256 の二値不一致~~ → 誤認と判明。source archive pin
  （zip 全体）と expanded corpus pin（前処理後コーパス）という**別の対象**
  を指す2つの正しい値であり、矛盾する同一対象への2値ではなかった。
- ~~backbone checkpoint 選定未~~ → RUN6 phase B 40K checkpoint を採用し
  `backbone_checkpoint_sha` を PINNED（`backbone_runtime_bundle_sha` は
  ブロッカー(5)参照 — 未解消）。

**残存**:

1. **User donor rights attest 待ち**: `inputs/rights_manifest.json`
   （Fable 起草済み）は `rights_class`/`consent_status` =
   `PENDING_USER_ATTESTATION`。User の確認前は `anchor_hashes.user` を
   pin しない（DESIGN_RUN9_REVISION_0.2.md 改訂4）。raw 音源公開・モデル
   一般配布は rights anchor 使用可否とは別承認（初期 `not_granted`）。
2. **`metric_space_sha` 未 pin**: identity domain の3つ目の必須 pin
   （anchor_hashes 3件とは別欄）。校正/採用する metric space の選定が
   未着手。
3. **VG-L0 学習ハーネス未実装**: rev 0.3 で三枝化された
   PRACTICE_FROM_AUDIO / TRANSFER_TECHNIQUE エッジ（書き込み先は改訂1・
   rev 0.3 改訂A で Performance ControlProfile と規定済み）ともにハーネス
   自体の実装は未着手。**ControlProfile Entry Gate**（旧 Adapter Entry
   Gate。改訂1 §対応マップ項目1 — `control-layer ceiling evidence or
   explicit User waiver` は循環要求のため削除済み・不足時の状態名は
   `BLOCKED_CONTROLPROFILE_ENTRY`）の残る要件（calibrated Identity audit
   route / learning replay harness / rights-clean curriculum / fixed
   compute budget / frozen recipe / rollback path）はどれも準備段階に
   すら入っていない。
4. **PJS Performance Lesson / Practice split build 未実施**: 改訂3で
   pin 方針（source archive pin / expanded corpus pin とは別の Lesson
   manifest を生成し pin）は確定したが、Lesson build 自体は VG-L0
   ハーネス実装待ち。rev 0.3 でこの pin は EDUCATION 用 Technique lesson
   を指すと明確化され `education_technique_lesson_manifest_sha` へ改名
   （User 外部レビュー PR #317 P1-2 採用）。PRACTICE 用の教師音声
   train/validation/sealed-holdout split manifest（正解 parameter を
   含まない生素材の分割、PoR §12）も同様に `practice_audio_split_
   manifest_sha`（`RUN9_CONTRACT.yaml`、PR #317 Codex bot レビュー第2巡
   Fix 6 で新設 → P1-2 で改名）として既に pin 欄が新設済み — ただし
   split manifest 自体の**生成**は lesson build と同じく VG-L0 ハーネス
   実装待ちのため、両欄とも PENDING のまま。manifest 自体の最低要件は
   `run9_schema.PRACTICE_MANIFEST_REQUIRED_KEYS`/
   `EDUCATION_MANIFEST_REQUIRED_KEYS` + `validate_practice_split_
   manifest()`/`validate_education_lesson_manifest()` が凍結済み。
5. **`render_code_commit` の確定待ち**（Codex bot レビュー PR #316 第1巡
   指摘採用）: `inputs/backbone_runtime_bundle.json` の
   `render_code_commit`（`openvpi/DiffSinger @ e2307b1...`）は
   `status: "INFERRED_UNCONFIRMED"` — run4〜8 全体での単一リビジョン一貫
   使用・反証なしという状況証拠のみで、RUN6 export の直接記録
   （`results_s5/s5_record_2026-08-20.md`）自体にはこの commit が明記され
   ていない。**直接記録の発掘、または User attestation で確定するまで
   `backbone_runtime_bundle_sha` は PENDING のまま**（`backbone_checkpoint_sha`
   単体は直接記録4件一致のため PINNED 継続 — 対象を混同しない）。
6. **practice/education builder 未実装**（rev 0.3 新設）: 情報境界
   （`run9_schema.PRACTICE_FORBIDDEN_INPUTS` /
   `PRACTICE_ALLOWED_DATA_INPUTS` / `PRACTICE_REQUIRED_AUTONOMOUS_
   OPERATIONS` / `PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE` /
   `EDUCATION_ALLOWED_CHANNELS` / `EDUCATION_FORBIDDEN_INPUTS`）・
   書込境界（`BRANCH_WRITABLE_PARTITIONS`）・manifest 最低要件
   （`PRACTICE_MANIFEST_REQUIRED_KEYS`/`EDUCATION_MANIFEST_REQUIRED_KEYS`）
   と結果分類（`BIRTH_OUTCOMES` 等6分類）の語彙は凍結済みだが、これらを
   import して実際に PRACTICE_FROM_AUDIO / TRANSFER_TECHNIQUE を実行する
   builder・評価器は本 Phase 0.3 では未着手
   （語彙の凍結のみが完了した段階）。

## 次フェーズ（machine-dependent）

- **practice/education harness 実装**: VG-L0 学習ハーネスの一部として、
  PRACTICE_FROM_AUDIO（Founder 自身の自律特徴抽出・差分推定・制御探索
  ループ、PoR §3.2 の基本ループ）と TRANSFER_TECHNIQUE（Technique
  extractor / Lesson builder → Founder 提示 → 自声再現、PoR §3.3 の基本
  ループ）を実装する。`PRACTICE_FORBIDDEN_INPUTS`/`EDUCATION_FORBIDDEN_INPUTS`
  を実行時検証で強制する構造（禁止入力が builder の入力経路に構造的に
  現れない設計）が要件。
- **6分類の実測パイプライン**: `BIRTH_OUTCOMES`〜`IDENTITY_OUTCOMES` を
  実際の render/measurement 結果から機械判定するロジックの実装（現状は
  語彙の凍結のみ）。`REQUIRED_GAIN_FIELDS`（held-out gain 必須4欄）の
  実測パイプラインも同様。
- **practice_audio_split_manifest.json / education_technique_lesson_
  manifest.json の実体生成**: `PRACTICE_MANIFEST_REQUIRED_KEYS`/
  `EDUCATION_MANIFEST_REQUIRED_KEYS` の最低要件を満たす実 manifest の
  生成（上記ブロッカー(4)参照）。
- **branch write policy の実行時強制**: `validate_branch_write()` を
  practice/education builder の書込経路へ実際に配線する（現状は
  builder 自体が未実装のため関数の存在のみ）。
- **C0/C1 の実 render**: `CONTROL_CONDITIONS` の両条件を実際に render し
  `control_conditions_satisfied()` で評価 readiness を確認するパイプ
  ライン。
- これらは全て実音源・実 render・実学習を要する machine-dependent 作業
  であり、Claude 側での設計・語彙凍結の範囲を超える
  （CLAUDE.md の「マシン依存 = Codex / User」区分に該当）。

**erratum（設計書内部の記述不一致、Codex bot レビュー PR #315 第6巡指摘1
— 上記5裁定とは別件）**: DESIGN_RUN9 §6 は `parent_designs` を5件宣言する
が、同じ設計書 §23 の Run Contract 雛形は3件しか列挙しておらず、依存2件
（VoiceGenesis Singing Baseline v0.1 / VoiceGenesis Supplement A・
Selection Pressure Routing）が欠落していた。設計書は byte-pin 済みのため
一切編集せず、完全側の §6 を正として `RUN9_CONTRACT.yaml` の
`parent_designs` を5件へ是正した（v0.2 改訂時に §23 を §6 へ同期すべき）。

## 設計判断の記録

**`TRI_CROSSOVER` を `voice_genesis/evolution/operators.py`（VG-E0）へ
追加せず run-local（`run9_schema.py`）にした理由**:

- 設計書 §8「既存 VG-E0 の凍結三角形は `ritsu / pjs / user` である。
  PJSを教師専用にしAF0を加えるRUN9では、既存schema・既存台帳をin-place
  変更しない」という明示的な指示に従う。
- `voice_genesis/evolution/models.py` の `ANCHOR_NAMES = ("ritsu", "pjs",
  "user")` / `VALID_OPERATORS` は他の多数の VG-E0 モジュール（`simplex.py`
  / `operators.py` / `ledger.py` / `archive.py` / `bootstrap.py` とその
  台帳データ）が前提とする凍結値であり、`af0` を追加する4点化や `pjs` を
  anchor から teacher へ役割変更する改訂は、VG-E0 の genome_id 計算・
  lineage 判定・archive セルグリッドの意味論を破壊し、既存台帳の
  再検証を要求する非互換変更になる。
- RUN9 は新しい run-local domain `run9-af0-ritsu-user/1.0`
  （anchor_order: af0, ritsu, user）を独立実装することで、VG-E0 の
  schema バージョンを上げずに三点構成を差し替えられる。`run9_schema.py`
  はモジュールレベルで `models.py`/`simplex.py` を import しない
  （`tests/test_run9_contract.py` の回帰テストがこれを直接検証する）。
- 副次的な利点: PJS を Identity anchor 空間から構造的に排除する要件
  （設計書 §27 item 10「PJS coordinate is structurally impossible」）を、
  run9 独自の `Run9Coords`（af0/ritsu/user の3フィールドのみ）で型レベル
  から強制できる。VG-E0 の `Coords` 型（ritsu/pjs/user）を流用すると
  pjs フィールドが常に存在してしまい、この禁止をコード構造で表現できない。

**改訂1（Performance ControlProfile）を CompositionScore の
`control_profile`（`docs/control_profile.md`）と混同しない**:

両者は偶然の同名だが**別スキーマ・別ドメイン**。CompositionScore 側は
生成器（Suno/MusicGen）ごとの `grip_class` 自己記述ブロックであり、RUN9
側は VoiceGenesis Founder の Performance 制御パラメータの版付き集合。
`DESIGN_RUN9_REVISION_0.2.md` 改訂1に明記済み。

## ディレクトリ構成（Phase 0.3 時点）

```
run9_dual_founder_pjs/
├── DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md   # 正本（バイト同一コピー・不変）
├── DESIGN_RUN9_REVISION_0.2.md                                 # v0.1 差分メモ（2026-08-24 User 裁定5件、無改変・存続）
├── DESIGN_RUN9_REVISION_0.3.md                                 # rev 0.2 差分メモ（同日、PoR メモ編入。三経路分離・三層観測等）
├── POR_CONCEPT_ADJUDICATION_20260824.txt                       # PoR 裁定ソース（uploads 原本とバイト同一・不変）
├── RUN9_CONTRACT.yaml                                          # §23 Run Contract（部分 pin。af0/ritsu/backbone/por_adjudication/branch_write_policy は PINNED。interventions・human_audit_mode 欄）
├── README.md                                                   # 本ファイル
├── run9_schema.py                                              # domain / TRI_CROSSOVER / contract / 書込境界 / manifest validator 他の run-local 正本
├── domains/
│   └── identity_domain_run9_v1.json                            # af0/ritsu PINNED・user/metric_space_sha はプレースホルダ
├── inputs/
│   ├── af0_anchor_manifest.json                                # AF-P0 正典証拠の複合参照 manifest（anchor_hashes.af0 の入力）
│   ├── rights_manifest.json                                    # User donor rights（PENDING_USER_ATTESTATION）
│   ├── backbone_runtime_bundle.json                            # RUN6 backbone の checkpoint/config/vocoder/render commit + canon model assets 一式
│   └── branch_write_policy.json                                # 枝別書込境界 manifest（State partition・writable集合・不変artifact一覧。PINNED）
├── tests/
│   └── test_run9_contract.py                                   # §27 最低テストの静的検証可能サブセット + Revision 0.2/0.3 対応テスト + User 外部レビュー P1/P2 対応テスト
└── results/
    └── .gitignore                                              # 実測結果は非同梱（§25 Atomic Results Bundle 用の空ディレクトリ）
```

設計書 §24 が推奨する `founders/` / `lesson/` / `learning/` / `evaluation/`
は、それぞれに実体を置く段階（実行順 step 4 以降）になってから作成する。
内容の伴わない骨組みは実装が進んだという誤った印象を与えるため、
先行して同梱しない。
