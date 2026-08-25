# RUN9 — Tri-Donor Dual-Founder Common-Teacher Learning

**状態: Preregistered / Phase 3（design_revision 0.4、machine-independent 設計層）。本学習未開始。**

正本設計書: [`DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md`](./DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md)
（uploads 原本とバイト同一・**byte-pin 不変**。sha256 は `RUN9_CONTRACT.yaml` の
`design_doc_sha256` が PINNED で保持する）。v0.1 に対する差分は
[`DESIGN_RUN9_REVISION_0.2.md`](./DESIGN_RUN9_REVISION_0.2.md)（2026-08-24
User 裁定5件、無改変のまま存続）→
[`DESIGN_RUN9_REVISION_0.3.md`](./DESIGN_RUN9_REVISION_0.3.md)（同日、PoR メモ
[`POR_CONCEPT_ADJUDICATION_20260824.txt`](./POR_CONCEPT_ADJUDICATION_20260824.txt)
の編入。無改変のまま存続）→
[`DESIGN_RUN9_REVISION_0.4.md`](./DESIGN_RUN9_REVISION_0.4.md)（2026-08-25、
外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモ
[`DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt`](./DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt)
（byte-pin 不変）の編入 + User 追加裁定「確認メモ /
RUN9 用語整理」の編入）の順で規定する。`design_revision_doc_sha256`/
`por_adjudication_sha256` が PINNED で保持する。

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
   再現入力集合は閉じない（PR #316 第2巡指摘採用）。当初
   `backbone_runtime_bundle_sha` は **PENDING**（bundle 内
   `render_code_commit` が `INFERRED_UNCONFIRMED` —
   Codex bot レビュー PR #316 第1巡指摘採用。ブロッカー(4)参照）だったが、
   現在は **PINNED**（`83f67a30…`）。claim scope は `run9_runtime_inputs`
   節に列挙された値の確定 + 文書バイト同一性のみであり、
   `historical_export_provenance`（RUN6 期の export commit 推定。現在も
   `INFERRED_UNCONFIRMED` 維持）の真理値は主張しない——両節を独立させた
   構造分離（Codex bot レビュー PR #319 第2巡指摘, Fix 3, 採用。詳細は
   下記「解消済み」節および `inputs/backbone_runtime_bundle.json` の
   `claim_scope`）。

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
   `practice_audio_split_manifest_sha` へ改名（改名当時は両方 PENDING
   だった。〔履歴: 「両方 PENDING のまま」→ `practice_audio_split_
   manifest_sha` は 2026-08-25 実 PJS 実行で **PINNED** 化済み（下記
   「解消済み（実 PJS practice split 実行, 2026-08-25）」節参照）。
   `education_technique_lesson_manifest_sha` は引き続き PENDING〕）。
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

## 2026-08-25 外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモの編入（Revision 0.4）— 要約

詳細・逐語は [`DESIGN_RUN9_REVISION_0.4.md`](./DESIGN_RUN9_REVISION_0.4.md) を
正とする。派生設計変更メモ
[`DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt`](./DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt)
（byte-pin 不変）は自称「design_revision 0.2」だが rev 0.2/0.3 が既に
マージ済みのため rev 0.4 として発行する（rev 0.3 冒頭「番号注記」と同型の
系譜処理）。中核仮説・実験条件は不変（NON-ARCHITECTURAL DESIGN
CORRECTION）。CASE A（Lesson Freeze / 本学習開始前）適用。

1. **rights_manifest の4層再編**（変更1・2）: `inputs/rights_manifest.json`
   を `voice_identity_rights`（旧内容=User donor 17件、無改変で移設）/
   `performance_rights`（新設 `performance_source` = {id: PJS, role:
   EXTERNAL_PERFORMANCE_SOURCE} + provenance.performance_author）/
   `composition_rights`（provenance.composition）/
   `recording_master_rights`（provenance.voice_source + synthesis +
   ライセンス実測値 CC BY-SA 4.0 + `interpretations`）の4層へ再編。
   原則3式（Teacher ≠ Voice Identity Owner / Teacher ≠ Performance
   Author / Voice Source ≠ Performance Source）+ 自動解釈禁止文を明記。
   provenance の実値は
   `voice_genesis/foundry/results_f1_2/licenses/pjs_terms_snapshot.md` 等
   から機械検証可能な範囲で充填（performer/composer = Junya Koguchi、
   2026-08-25 User 追加裁定②で確定 — 下記「追加裁定4件」参照。
   recording-master owner は裁定②の確定範囲に含まれず、論文著者性を
   録音物の権利保有の証拠とする過大推論だったため PR #319 第 4 巡指摘
   採用で `<UNRESOLVED_EXTERNAL>` へ差し戻し済み——現在値はこちら。
   下記「②」箇条も参照）、
   なお不明な外部事実欄は `<UNRESOLVED_EXTERNAL>` のまま（捏造禁止。
   `<PENDING_USER_ATTESTATION>` は User 自身が attest すべき欄専用へ
   語彙分離済み）。
2. **Performance Residual / Identity 除外語彙**（変更3・6）:
   `run9_schema.PERFORMANCE_RESIDUAL_VOCAB`（9項目）+
   `IDENTITY_EXCLUDED_TRAIT_VOCAB`（変更3の6項目+変更6の4項目を統合・
   重複吸収した7項目）+ LessonRecord 標準仕様（schema
   `run9-lesson-record/1.0`、`validate_lesson_record()`）を新設。
3. **R9-G1 拡張**（変更5）: v0.1 §19 R9-G1「INPUT_FREEZE_AND_RIGHTS」
   （byte-pin 不変）に対し、意味名 `RIGHTS_AND_PROVENANCE_GATE`・PASS
   条件8項目（`R9_G1_PASS_CONDITIONS`）・FAIL 語彙
   `RIGHTS_PROVENANCE_UNRESOLVED` を rev 0.2 と同型の「読み替え」方式で
   追加（v0.1 本文は書き換えない）。
4. **Common Performance Lesson**（変更4）: v0.1 §14「H2 — Common Teacher
   Transfer」（byte-pin 不変）を、rev 0.4 以降の呼称として「Common
   Performance Lesson」へ読み替える参照規約を確立（教育枝の lesson
   manifest 経由という実態と一致・`TRANSFER_TECHNIQUE`/`INHERIT_TRAIT`
   との名称衝突回避が理由）。
5. **User裁定a/b**（2026-08-25「aとbを承認」）: **a** = rights manifest
   attest は新4層構造に対し次段で確定（User donor 17件の内容・attest
   状態自体は無改変）。**b** = 当初 `inputs/backbone_runtime_bundle.json`
   `render_code_commit.status` を `INFERRED_UNCONFIRMED` → `USER_ATTESTED`
   へ確定する実装としたが、同日中の追加裁定①でこれは過大と判明し是正
   した——詳細・現在の構造は下記「2026-08-25 User 追加裁定4件」①を参照。
6. **performance_source ブロック**（`RUN9_CONTRACT.yaml` 新設欄、
   pin 欄ではなく通常欄）: `{id: PJS, role: EXTERNAL_PERFORMANCE_SOURCE}`
   + `teacher_terminology_note`。

### 2026-08-25 User 追加裁定「確認メモ / RUN9 用語整理」による項目5の緩和

実装着手後、User から用語整理の確認メモが追加で入り、terminology 掃討の
方針が変更された（逐語は `DESIGN_RUN9_REVISION_0.4.md` §7 参照）: **teacher
語の全面置換はしない**——可変 artifact 中の「teacher」出現は維持し、代わり
に非所有注記（「Teacher は運用上の呼称であり Voice 所有者・Voice Identity
Owner を意味しない」旨）を各該当箇所へ追記する方式へ変更した。

- `RUN9_CONTRACT.yaml`: teacher_reference 相当欄は元々存在しない（rev 0.3
  で `interventions` 構造へ既に移行済み）ため置換対象なし。新設
  `performance_source.teacher_terminology_note` が非所有注記の置き場所。
- `inputs/identity_metric_space.json`: `confuser_control.role` /
  `calibration.validity_gates.negative_reference_gate.negative_reference_definition`
  の2箇所に「teacher」の literal な出現を維持したまま非所有注記を追記
  （repin 実施 — `metric_space_sha` は
  `de3a459bdea761850d465caa60a91a16d7a9a39b65652dd409f6e45a20ee1bb4`
  へ更新）。
- 「teacher 語の再出現拒否」validator は実装しない（User 追加裁定 指示5）。
  代わりに `tests/test_run9_contract.py` に、teacher 語を含むファイルが
  非所有注記も併せ持つことを確認する軽量テストを追加。
- 旧称「Common Teacher Transfer」→「Common Performance Lesson」への改名（変更4）は維持。

### 2026-08-25 User 追加裁定4件（①〜③・P0、同日内の是正）

上記実装の着手中に User から4件の追加裁定が入り、同日中に反映した
（design_revision は 0.4 のまま据え置き）。逐語・詳細は
`DESIGN_RUN9_REVISION_0.4.md`「2026-08-25 User 追加裁定4件」節を正とする。

- **P0（帰属表記の訂正、最優先）**: `EXTERNAL_REVIEW_AQUEST_20260825.txt`
  は山崎氏の逐語原文ではなく、山崎氏の指摘を受けて我々が起草した派生
  設計変更メモだった——実在の外部者への誤帰属だったため訂正する。
  `git mv` で
  [`DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt`](./DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt)
  へ改名（内容は1byteも無改変、sha256 =
  `a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091` 不変）。
  山崎氏に帰属するのは外部指摘の核心（VoiceBank に歌い方情報はほぼ含ま
  れないこと、Voice Source の権利者 ≠ Performance の権利・来歴主体、の
  分離指摘）のみ——「変更1」〜「変更8」の具体設計は我々の派生設計。
- **①**: `render_code_commit`（RUN6 の歴史的 export provenance）を
  `USER_ATTESTED` へ昇格したのは過大だった——歴史的事実は遡って attest
  しない方針により `INFERRED_UNCONFIRMED` へ差し戻した。RUN9 が今後の
  render に使用する commit の前方宣言は独立の新設欄
  `run9_render_code_commit`（status: `DECLARED_FOR_RUN9`）が担う。
  `backbone_runtime_bundle_sha` はこの構造変更を反映した実バイト sha
  へ再計算し、`run9_render_code_commit` の確定を根拠に **PINNED** を
  維持（旧値 `b92dac0e...` は同欄コメントに履歴として保持）。
- **②**: `rights_manifest.json` provenance を是正——
  `performance_author.performer`/`composition.composer` に外部資料出典
  付きで **Junya Koguchi** を充填（`voice_source.owner` への同名充填は
  論文著者性を録音物権利保有の証拠と誤認した過大推論だったため PR #319
  第 4 巡指摘採用で `<UNRESOLVED_EXTERNAL>` へ差し戻し）
  （旧 `<PENDING_USER_ATTESTATION>` は誤用だった。performer/composer は
  User 自身が attest できる対象ではなく外部の第三者事実であるため）。
  `composition.lyricist` は未解決のまま placeholder のみ
  `<UNRESOLVED_EXTERNAL>`（外部事実欄専用の新設語彙）へ張り替え。
  `recording_master_rights` に `interpretations` 節を新設し、CC BY-SA
  4.0 の share-alike 義務が合成出力へ及ぶかという法解釈論点を、事実で
  ある `license` 節から分離した。
- **③**: `run9_schema.PERFORMANCE_TRAIT_VOCAB` を
  `PERFORMANCE_RESIDUAL_VOCAB` へ改名（9項目の中身は不変）。RUN9 既存の
  Trait 層（`TRAIT_CONTROL`、Identity/Trait/Technique 三層）との用語
  衝突を避け、§7 確認メモの定義「Performance Residual = Performance
  から抽出された RUN9 内部の数値表現」に統一した。

## RUN9 Phase 3 — machine-independent 設計層の確定

machine-independent（実音源・実 render・実学習を要さない）次段3点を
実装した。詳細は各節・`run9_controlprofile.py` の docstring を正とする。

1. **identity metric space の定義と pin**（Fable 設計判定 — **User は
   マージ前に veto 可能**）: 新規 [`inputs/identity_metric_space.json`](./inputs/identity_metric_space.json)
   （schema `run9-identity-metric-space/1.2`）— feature_extractor = WORLD
   (pyworld、foundry S1〜S3 と同系)・identity_feature = voiced フレームの
   log spectral envelope (sp) 時間平均ベクトルのスカラー平均減算 level
   正規化形状（計算可能域は **全 identity 評価対象レンダー** — r0 birth
   probe・C0/C1 校正テイク・r_practice / r_taught / r_sham・pjs_reference。
   校正母集団と校正 reference は neutral r0 限定のまま = Fix 31/33）
   （**f0 は明示除外** — pitch は Trait/Technique 層の観測軸のため
   identity 距離への交絡を避ける、PoR §2 層分離）・aperiodicity は
   advisory・distance = Euclidean（対称・決定論）・calibration
   = C0 95 パーセンタイル閾値（`theta_cal = P95(D_C0)`）+ C1/正負参照の
   3 校正有効性ゲート + STABLE/SHIFTED 判定式を機械可読に凍結
   （Codex bot レビュー PR #318 第6巡 Fix 18。`run9_schema.
   validate_identity_metric_space_manifest()` が閉じた形状を検証）・
   feasibility_note = 退化時は PoR §9 [C] DESIGN FAILURE/UNOBSERVABLE で
   正直に閉じ事後調整はしない。このファイルの**正規形 sha256** を
   `domains/identity_domain_run9_v1.json` `metric_space_sha` へ **pin
   済み**（af0_anchor_manifest と同一の正規形規約）。**この時点では**
   domain は user anchor が未 pin のまま残っていたため `is_pinned()` は
   `False` だった（意図どおり）——2026-08-25 User attestation 実行により
   user anchor も PINNED 化され、現在は `is_pinned() == True`（domain
   凍結済み、下記「解消済み（2026-08-25 User attestation 実行）」節参照）。
2. **ControlProfile 基盤**（新規モジュール [`run9_controlprofile.py`](./run9_controlprofile.py)）:
   `run9-control-profile/1.0` — `voice_id`/`branch`/`revision`
   （`BRANCH_REVISIONS` 語彙と整合: `r0` 出生中立・`replay`/`r_sham`/
   `r_practice`/`r_taught`）/`parent_revision`/`partitions`
   （`trait_control`/`technique_control` の**2節のみ** — `IDENTITY_STATE`
   は profile schema に構造的に存在しない）/`profile_id`（正規形 JSON
   sha256 先頭16hex）。`build_neutral_profile(voice_id)` が中立 r0 を
   決定論生成し、`derive_profile(parent, branch, updates)` が
   `run9_schema.validate_branch_write()`/`BRANCH_WRITABLE_PARTITIONS` を
   必ず経由して書込境界を機械強制する（EDUCATION→trait は fail-closed・
   CONTROL は非空 updates を拒否）。`Run9ProfileLedger` は VG-E0
   `ledger.py` の append-only 意味論（tmp→fsync→`os.link` 排他 create・
   バイト同一冪等・差異は conflict・重複キー拒否読込・symlink escape
   guard）を run-local に踏襲し、親 revision の実在検証を行う。
   practice trace schema（`run9-practice-trace/1.0`、
   `validate_practice_trace()`）は模倣対象選択・内部差分推定・探索履歴の
   保存要件（PoR §7/改訂C）を凍結 — 中身の生成は harness 実装時。
3. **learning recipe manifest schema**: `run9_schema.
   validate_learning_recipe_manifest()`（schema `run9-learning-recipe/1.0`）
   — 枝別 recipe（`practice_recipe`/`education_recipe` の2節、rev 0.3 の
   枝別原則 — Codex bot レビュー第6巡 Fix B「両枝が同一の recipe を共有
   する」誤記是正後の reason と整合）+ 共通 `seed`（`LEARNING_SEED` =
   909002 固定）+ 各枝内の二体等予算宣言（`equal_budget_within_arm: true`
   必須）。停止規則/試行回数/render 予算は構造のみ凍結（値は build 時）。
   `learning_recipe_sha` は本 PR でも PENDING のまま。規約パス
   `run9_schema.LEARNING_RECIPE_MANIFEST_PATH`
   （`inputs/learning_recipe_manifest.json`）を凍結し、PINNED 時照合を
   practice/education manifest と同型でテスト層へ事前配線した。

## 実行順 §22 に対する現在地マップ

設計書 §22 は 0–20 の実行順を規定する（v0.1 本文の番号は不変。rev 0.3
以降、step 5–20 が三経路化の影響を受ける — 詳細は
`DESIGN_RUN9_REVISION_0.3.md` 改訂A/C）。Phase 3 時点の現在地:

| step | 内容 | 状態 |
|---|---|---|
| 0 | freeze Run Contract | **部分 pin が拡大**（`design_doc_sha256` / `design_revision_doc_sha256` / `por_adjudication_sha256` / `backbone_checkpoint_sha` に加え、rev 0.4 で `backbone_runtime_bundle_sha` も新設の前方宣言欄 `run9_render_code_commit`（status: `DECLARED_FOR_RUN9`、2026-08-25 User 裁定）の確定により **PINNED** 化——bundle 内 `render_code_commit` 自体は RUN6 の歴史的 export provenance として `INFERRED_UNCONFIRMED` のまま（2026-08-25 追加裁定①、両欄は独立）。`interventions` 構造（旧 `single_intervention`）へ改訂済み。新設 `performance_source` 欄も追加済み。他も正直に PENDING。`gate_state()` は依然 `BLOCKED`） |
| 1 | verify repository / dependency pins | 未着手（backbone 側は pin 済み。VG-L0 ハーネス自体の依存 pin は未着手） |
| 2 | verify donor and teacher rights / manifests | **AF0/Ritsu は pin 済み・PJS は役割別2値を整理して解消・User donor は 2026-08-25 User attestation 実行により attest 完了**（`voice_identity_rights.attestation.attested=true`）。PJS 側の recording-master owner/lyricist/share-alike 解釈のみ未解決のまま残る（ブロッカー(1)参照） |
| 3 | build run9 Identity Domain | **af0/ritsu/metric_space_sha/user が全て PINNED**（`domains/identity_domain_run9_v1.json`、2026-08-25 User attestation 実行により `is_pinned() == True` — domain 凍結済み） |
| 4 | generate R9F-01:r0 and R9F-02:r0（INHERIT_TRAIT） | **正式発行済み**（RUN9-BIRTH-PREP-1, 2026-08-25）: `run9_schema.issue_founder_genome_document()` の出力バイトを `founders/R9F-01_genome.json` / `founders/R9F-02_genome.json` としてそのまま書き出し、`RUN9_CONTRACT.yaml` `founder_genome_shas.R9F-01/R9F-02` を各ファイルの raw sha256 で **PINNED** 化した（genome_id = `66f420672a154283` / `63f4b8f24b827cd4`——2026-08-25 Codex bot レビュー PR #320 第2巡 Fix 3 の anchor_hashes.user binding scope 再限定 repin 後の値、下記「解消済み」節参照。値自体は無変更）。詳細は下記「解消済み（RUN9-BIRTH-PREP-1）」節 |
| 5–20 | render / freeze / lesson / learning / evaluation / verdict | **未着手・rev 0.3 で三枝化**（VG-L0 学習ハーネス自体が未実装 — ブロッカー(2)参照。ハーネス実装時に CONTROL/PRACTICE_FROM_AUDIO/TRANSFER_TECHNIQUE の3経路分の render/lesson/learning/evaluation を実装する必要がある — ブロッカー(4)参照）。ただし render 対象となる **P0-P5 Probe Set の実体 manifest は RUN9-PROBE-1（2026-08-25）で起草・PINNED 化済み**（`evaluation/probe_manifest.json`、下記「解消済み（RUN9-PROBE-1）」節参照）——score cell・render 契約・revision_bridge の凍結までであり、実 render の実行自体は未着手のまま |

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
  当時ブロッカー(4)として残存していたが、下記「解消済み（2026-08-25
  外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモの編入、Revision 0.4）」で解消済み）。
- ~~`metric_space_sha` 未 pin~~ → Phase 3: `inputs/identity_metric_space.json`
  （schema `run9-identity-metric-space/1.2`。feature_extractor = WORLD
  (pyworld)・identity_feature = voiced フレームの log spectral envelope
  (sp) 時間平均ベクトルの level 正規化形状 — 計算可能域は全 identity
  評価対象レンダー（r_practice / r_taught / r_sham・pjs_reference 含む。
  校正母集団・校正 reference は neutral r0 限定 = Fix 31/33）・
  **f0 は明示除外**・
  distance = Euclidean・calibration = C0/C1 機械校正を実行可能な式へ
  凍結（Fix 18））を新設し、
  その正規形 sha256 を `domains/identity_domain_run9_v1.json`
  `metric_space_sha` へ **PINNED**（**Fable 設計判定 — User はマージ前に
  veto 可能**）。**この時点では** domain は user anchor が未 pin のまま
  残っていたため `is_pinned()` は `False` だった（意図どおり）——現在は
  下記「解消済み（2026-08-25 User attestation 実行）」のとおり
  `is_pinned() == True`。

**解消済み（2026-08-25 外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモの編入、Revision 0.4）**:
- ~~`render_code_commit` の確定待ち~~ → User 裁定「aとbを承認」の b
  （2026-08-25）を根拠に、RUN9 が今後の render に使用する commit を
  独立の新設欄 `run9_render_code_commit`（status: `DECLARED_FOR_RUN9`）
  として前方宣言で確定。連動して `RUN9_CONTRACT.yaml`
  `backbone_runtime_bundle_sha` も **PINNED** へ昇格（`backbone_checkpoint_sha`
  は元々 PINNED のまま——対象は別欄）。bundle 内 `render_code_commit`
  （RUN6 の歴史的 export provenance）自体は当初 `USER_ATTESTED` へ昇格
  させたが、同日中の追加裁定①により `INFERRED_UNCONFIRMED` へ差し戻した
  ——歴史的事実は遡って attest しない方針のため（両欄は独立。詳細は上記
  「2026-08-25 User 追加裁定4件」①参照）。

**解消済み（2026-08-25 User attestation 実行）**:
- ~~User donor rights attest 待ち~~ → User 裁定「承認する」を根拠に
  `inputs/rights_manifest.json` `voice_identity_rights.attestation` を
  pending 形態から attested 形態へ遷移（`attested_by="Yuu6798"` /
  `attested_at="2026-08-25T06:47:25Z"` / 宣誓文は同ファイル `history` に
  逐語記録）。連動して `rights_class`/`consent_status` を旧
  `PENDING_USER_ATTESTATION` から `USER_ATTESTED_OWN_VOICE` へ、
  `usage_grants.run9_identity_anchor` を `not_granted` から `granted` へ
  更新した（`raw_audio_publication`/`model_general_distribution` は rev 0.2
  改訂4の別承認規定により `not_granted` のまま不変——本 attest の対象外）。
  attest 後の manifest 正規形 sha256 を
  `domains/identity_domain_run9_v1.json` `anchor_hashes.user` へ直接 pin
  （旧 `<PIN_BEFORE_RUN>` → `e2755c3db6283e40e5080c5de75a70bb8b88e275e548848b1e2e5b8f4bb512d1`）
  したことで3 anchor + `metric_space_sha` が全て揃い、`is_pinned()` が
  初めて `True` を返す（domain 凍結）。`run9_schema.build_founder()` は
  現行 domain draft から genome_id を決定論的に計算可能になった
  （当時実測: R9F-01 = `2f7df5a90a6c99c9` / R9F-02 = `9cd047c667dcb96e`）。
  ただし `RUN9_CONTRACT.yaml` `founder_genome_shas` が pin する対象は
  genome_id ではなく永続 genome 文書ファイル（`founders/R9F-0x_genome.json`）
  バイトの sha256 であり、その文書を書き出して正式発行する builder・pin
  手続きは未配線のため同欄は引き続き `PENDING`（下記残存ブロッカー参照
  ではなく、`RUN9_CONTRACT.yaml` 該当欄の reason 参照）。**2026-08-25
  追記（Codex bot レビュー PR #320 第1巡指摘, P1, 採用, Fix 1）**:
  上記 `anchor_hashes.user` の binding は当初 `rights_manifest.json`
  全体の正規形 sha256 だったが、同一文書内の PJS 側外部未解決欄が将来
  解決されると anchor が不要に動く binding scope 過大の欠陥だったため、
  binding 対象を `run9_schema.extract_voice_identity_rights_layer()`
  が返す **voice_identity_rights 層のみ**へ是正・repin した（現行値は
  `domains/identity_domain_run9_v1.json` `anchor_hashes.user` 参照——旧値
  `e2755c3db6283e40e5080c5de75a70bb8b88e275e548848b1e2e5b8f4bb512d1` から
  更新）。連動して genome_id も再計算された（当時実測: R9F-01 =
  `f5ea253804728b3b` / R9F-02 = `72423141c1add7e8`）——出生前（founders/
  未書き出し・render 未実施）のため科学的影響はなく、`is_pinned()` は
  引き続き `True`、`founder_genome_shas` は引き続き `PENDING` のまま不変。
  **2026-08-25 追記（Codex bot レビュー PR #320 第2巡指摘, P1, 採用,
  Fix 3）**: 上記 Fix 1 の binding（voice_identity_rights 層全体）も
  `usage_grants`/`usage_grants_note` を含んでおり、
  `raw_audio_publication`/`model_general_distribution` の別承認（rev 0.2
  改訂4が定める設計上正規の可変状態）が起きるたびに anchor が動く欠陥
  だったため、binding 対象を新設 `run9_schema.
  extract_user_identity_attestation_projection()` が返す「不変
  identity-attestation projection」（entries/attestation/rights_class/
  consent_status/donor_ledger 系の閉じた9キーのみ、`usage_grants`・
  `role`/`note`/`binding_note` 等の散文欄を除外）へさらに再限定・repin
  した（現行値は `domains/identity_domain_run9_v1.json`
  `anchor_hashes.user` 参照——旧値
  `ad54200af4d42433439702946aa65e4a38848f3d8ec0e52140b351e2a9afae6b` から
  更新）。連動して genome_id も再計算された（現行実測: R9F-01 =
  `66f420672a154283` / R9F-02 = `63f4b8f24b827cd4`）——出生前のため
  科学的影響はなく、`is_pinned()` は引き続き `True`。当時
  `founder_genome_shas` は引き続き `PENDING` のまま不変だった（正式発行
  builder が未配線だったため——下記「解消済み（RUN9-BIRTH-PREP-1）」節で
  解消済み）。

**解消済み（RUN9-BIRTH-PREP-1, 2026-08-25）**:
- ~~founder genome 文書の正式発行 builder・pin 手続き未配線~~ →
  `run9_schema.issue_founder_genome_document(founder_id, *, domain,
  rights_manifest)` を新設。内部は必ず `build_founder()` 経由（Fix 6/7 の
  fail-closed ガード——attested 前提条件・anchor grant 検証・user anchor
  実物照合——が発行のたびに毎回実行される）で、直列化を
  `(json.dumps(genome.to_dict(), ensure_ascii=False, indent=2,
  sort_keys=True) + "\n").encode("utf-8")` へ凍結する。同関数の出力バイトを
  そのまま [`founders/R9F-01_genome.json`](./founders/R9F-01_genome.json) /
  [`founders/R9F-02_genome.json`](./founders/R9F-02_genome.json) として
  書き出し（genome_id = `66f420672a154283` / `63f4b8f24b827cd4`、無変更）、
  `RUN9_CONTRACT.yaml` `founder_genome_shas.R9F-01/R9F-02` を各ファイルの
  raw sha256（`R9F-01` = `6c90f571c671b461c1d2735ffb7f3536c4a2a18c25d1a7f610300d3b1b2ded2d`
  / `R9F-02` = `6311fdef3c7657384e5366caa9cb5b429b8d7e334a0b2b84fa011d16a086e757`）
  で **PINNED** 化した。`tests/test_run9_founder_genome_issuance.py`
  が再生成同一性（repo 内ファイル == 関数出力）・
  `founder_genome_from_dict()` 通過・契約照合・fail-closed 継承（取消/
  pending/anchor不一致 manifest 拒否）を検証する。**`gate_state()` は
  依然 `BLOCKED`**（dataset/config/lesson/practice/learning-recipe 等
  VG-L0 ハーネス関連欄が PENDING のままのため——回帰テスト
  `test_gate_state_still_blocked_after_founder_genome_shas_pinned` で
  機械確認済み。誤 READY 化はしていない）。
- ~~practice split builder 未実装~~ → 新規モジュール
  [`practice_split_builder.py`](./practice_split_builder.py) を追加。
  PJS コーパスから pin 被覆 `_song.wav` 集合のみを列挙（`donor_bank_lab.
  corpus_identity_hash()` と同一規約の独立再実装——`pyworld` 依存の
  import 閉包を避けるため）し、corpus identity 照合（fail-closed・
  `expected_corpus_identity` 明示必須）→ `score(song_id) =
  sha256(f"{song_id}|{LEARNING_SEED}")` による全順序割当（`assign_split()`
  純関数、N=100→70/15/15厳密・N<=6でfail-closed）→
  `validate_practice_split_manifest()` 自己適用、という
  `run9-practice-audio-split-manifest/1.0` manifest builder
  （`build_practice_split_manifest()`）を実装した。音響解析（librosa.pyin
  による pitch range・.lab 由来の phrase 数/音素クラス）は別関数
  `build_acoustic_inventory_sidecar()`（契約 pin 対象外、advisory）へ
  型的に分離——manifest builder のデータフローは音響解析結果を一切含まない
  （`tests/test_practice_split_builder.py` の
  `test_sidecar_generation_does_not_change_manifest_bytes` が機械強制）。
  近似重複検出は実装せず manifest 内 note に境界宣言。~~実 PJS コーパスに
  対する実行はまだ行っていない~~ → **2026-08-25 実 PJS 公開配布物から生成
  し解消済み**: PJS corpus ver1.1 zip（CC BY-SA 4.0 公開配布物、Google
  Drive ID `1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_`）を取得し sha256 が契約 pin
  と厳密一致することを確認、plain unzip で展開したコーパスに対して
  builder を実行したところ `expanded_corpus_identity_sha256` も初回試行
  で一致した（song 数 100・展開レシピのずれなし）。生成した
  `inputs/practice_audio_split_manifest.json`（training 70 / validation
  15 / sealed_holdout 15）の raw sha256 で `practice_audio_split_
  manifest_sha` を **PINNED** 化した。実 PJS 音源・展開物は repo 配下へ
  置いていない（作業は session scratchpad 限定）。この生成手続きは
  User の私物マシンや実行時計測を要求しない——公開配布物の sha 検証と
  builder の決定論処理のみで再現できる（User 裁定による scoped 例外の
  成立条件・出所は下記「次フェーズ」節参照）。

**解消済み（RUN9-PROBE-1, 2026-08-25）**:
- ~~P0-P5 probe set の実ファイル manifest 未作成~~ → DESIGN_RUN9 §15
  Probe Set の実体 manifest [`evaluation/probe_manifest.json`](./evaluation/probe_manifest.json)
  （`evaluation/` は本ファイルが初出のディレクトリ）を新設した。schema
  `run9-probe-manifest/1.0`（`run9_schema.validate_probe_manifest()` が
  fail-closed 検証）— P0-P5 全6probe（score cell は本 manifest へ
  self-contained 直書き。新規 score module は作らず、JSON→ScoreNote
  変換ハーネスは machine-dependent の別フェーズへ据え置く）/
  `render_contract`（harness・`backbone_ref` は `RUN9_CONTRACT.yaml`
  `backbone_runtime_bundle_sha` へのフィールド名参照のみ・
  `performance_seed=909001`（学習 seed 909002 と混同しない note 付き）・
  §15 末尾 PCM publication 規律の逐語）/ `revision_bridge`（§15 probe 語彙
  ↔ `inputs/identity_metric_space.json` 語彙の橋渡し7エントリ
  — reference_render / c0_replay_takes / c1_sham_takes /
  positive_reference / negative_reference / pjs_reference /
  evaluated_renders。式・値は同ファイルへの参照のみで重複定義せず、
  negative_reference/pjs_reference は新規 render 不要）/
  `measurement_boundary`（「どう測るか」は本 manifest の対象外という
  境界明文 — identity 軸は `inputs/identity_metric_space.json`
  正本、development/generalization 軸は `measurement_spec_sha`（別欄）が
  別途凍結。〔履歴: 当時 PENDING → RUN9-L0-PIN-1（2026-08-25）で
  identity 軸の extractor カタログを PINNED 化・development/generalization
  軸は VG-L0 ハーネス実装待ちとして NOT_YET_IMPLEMENTED を明示保留、
  下記「解消済み」節参照〕）/ `prohibitions`（render 後の cell・
  水準追加禁止・結果を見た後の probe 変更禁止・測定仕様の変更を本
  manifest で行わない、の3禁則 + render 不能 cell の是正 repin はこの
  禁則の対象外という区別）の閉じたトップレベル構造。P0 cell は
  [`voice_genesis/singer/score.py`](../../singer/score.py)（read-only
  参照）の `build_sakura_score()` 全20ノートを値として逐語転記し、
  cell の `source` に転記元パス + 実 sha256 を記録（`validate_probe_
  manifest()` が実ファイルの実 sha256 と照合する fail-closed チェック
  付き）——全音域が MIDI 57-69 で P0 の中央音域要求 [57, 72] に収まる
  ため断片抽出ではなく全曲をそのまま採用した。`RUN9_CONTRACT.yaml`
  `probe_manifest_sha` を manifest 実バイトの sha256 で **PINNED** 化
  した。`tests/test_run9_probe_manifest.py` が構造検証・実ファイル sha
  照合・P0 逐語照合（`score.py` を直接 import して note 列を突き合わせ）・
  P4 heldout_independence・P5 域・負例群を検証する。**`gate_state()` は
  依然 `BLOCKED`**（当時 `dataset_manifest_sha`/`learning_recipe_sha`/
  `measurement_spec_sha`/`hypothesis_algebra_sha` 等 VG-L0 ハーネス関連
  欄が PENDING のままのため——回帰テスト
  `test_gate_state_still_blocked_after_probe_manifest_sha_pinned` で
  機械確認済み。〔履歴: `measurement_spec_sha` はその後 RUN9-L0-PIN-1
  （2026-08-25、下記「解消済み」節参照）で identity 軸を PINNED 化した
  — `gate_state()` は残る欄（下記「残存」節参照）のため引き続き
  `BLOCKED`〕）。

**解消済み（実 PJS practice split 実行, 2026-08-25）**:
- ~~practice split の実 PJS コーパスに対する実行未実施~~ → PJS corpus
  ver1.1 zip（CC BY-SA 4.0 公開配布物、Google Drive ID
  `1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_`。`voice_genesis/foundry/s1_dataprep/
  README.md` 素材2 / `results_f1_2/licenses/pjs_terms_snapshot.md` に
  記録済みの同一 URL）を取得し、sha256 が
  `practice_split_builder.PJS_SOURCE_ARCHIVE_SHA256` と厳密一致すること
  を確認、plain unzip（リネーム・変換なし）で展開したコーパスに対して
  `practice_split_builder._enumerate_pjs_song_ids()` を実行したところ
  `expanded_corpus_identity_sha256` が初回試行で `EXPANDED_CORPUS_
  IDENTITY_SHA256` と一致した（song 数 100、展開レシピのずれなし）。
  続けて `build_practice_split_manifest()` を実行し
  `inputs/practice_audio_split_manifest.json`（training 70 /
  validation 15 / sealed_holdout 15、`assign_split()` の逐語アルゴリズム
  どおり厳密70/15/15）を決定論形式で書き出した。`RUN9_CONTRACT.yaml`
  `practice_audio_split_manifest_sha` を同ファイルの raw sha256 で
  **PINNED** 化した。音響 sidecar（`build_acoustic_inventory_sidecar()`）
  は environment-dependent float の懸念があるため本作業では生成していない
  （advisory・契約 pin 対象外——`RUN9_CONTRACT.yaml` の pin 判定に一切
  影響しない）。実 PJS 音源・展開物は repo 配下へ置いていない（作業は
  session scratchpad 限定）。**`gate_state()` は依然 `BLOCKED`**
  （`dataset_manifest_sha`/`education_technique_lesson_manifest_sha`/
  `learning_recipe_sha`/`config_sha` 等 VG-L0 ハーネス関連欄が PENDING
  のままのため）。**User 裁定による scoped 例外**〔履歴: 当初「この work
  は『マシン依存（実音源 = Codex/User）』ではなく…Claude 側の通常実装
  ルートで完了可能だった」と、権限根拠を示さず一般規則であるかのように
  記していた——Codex bot レビュー PR #323 第3巡指摘（P2, 部分採用）で
  是正: CLAUDE.md:71-75 の一般分類は不変のまま、本件は User が本 PR
  セッション（2026-08-25）で直接承認・実行指示した scoped 例外である旨、
  および成立条件（公開配布物 + sha 完全一致検証）を下記「次フェーズ」節
  へ明記した〕。詳細・成立条件・権限の出所は下記「次フェーズ」節参照。

**解消済み（RUN9-L0-PIN-1, 2026-08-25）**:
- ~~`seed_policy_sha` 未 pin~~ → [`inputs/seed_policy_manifest.json`](./inputs/seed_policy_manifest.json)
  （schema `run9-seed-policy/1.0`）を新規起草し、RUN9 が現に消費する3つの
  独立した乱数 seed（`performance_seed` = 909001 = DESIGN_RUN9 §9.2/§9.3の
  凍結値 / `learning_seed` = 909002 = `run9_schema.py:119 LEARNING_SEED` /
  `gate_synth_runtime_seed` = 42 = `gate_synth.py:149 SEED`、消費点
  `ort.set_seed`/`record["seed"]` は `gate_synth.py:1213-1214`）を role・
  消費点(file:line)・他 seed からの独立宣言込みで全数登録した。
  `run9_schema.validate_seed_policy_manifest()` が未知 seed_id・欠落・
  値不一致を fail-closed 拒否し、`load_pinned_seed_policy_manifest()`
  （probe manifest と同型の3層防御・read-once 消費関数）を新設した。
  `RUN9_CONTRACT.yaml` `seed_policy_sha` を manifest 実バイトの sha256 で
  **PINNED** 化した。
- ~~`failure_abort_criteria_sha` 未 pin~~ → [`inputs/failure_abort_criteria.json`](./inputs/failure_abort_criteria.json)
  （schema `run9-failure-abort-criteria/1.0`）に DESIGN_RUN9 §30 Stop Rules
  の20項目を逐語収載し、各項目を `enforcement: MACHINE`（既存 pin 済み
  機構への `condition` 参照。10件 — 例: #12 は `founder_genome_shas` との
  raw sha 照合、#5 は同一入力2回実行の byte 一致照合）/
  `enforcement: PROCEDURAL`（§22 のどの step で誰が判定するかの
  `checkpoint`。10件）へ分類した。校正未実施の数値閾値を要する項目
  （#14 mandatory metric degeneracy / #16 Identity drift beyond
  non-inferiority）は `deferred_threshold_ref: "hypothesis_algebra_sha"`
  で参照するのみとし、数値は発明していない（`hypothesis_algebra_sha` は
  引き続き PENDING）。§30 末尾の停止後救済禁止6項目（new weights/new
  teacher/new Founder/new metric threshold/new Lesson channel/new
  optimizer search）も逐語収載した。`run9_schema.validate_failure_abort_
  criteria()`（rule_id 1..20 の厳密連番 + 逐語一致 + enforcement 語彙の
  fail-closed 検証）+ `load_pinned_failure_abort_criteria()` を新設し、
  `RUN9_CONTRACT.yaml` `failure_abort_criteria_sha` を **PINNED** 化した。
- ~~`measurement_spec_sha` 未 pin~~ → [`inputs/measurement_spec_manifest.json`](./inputs/measurement_spec_manifest.json)
  （schema `run9-measurement-spec/1.0`）を新規起草した。測定仕様は
  identity 軸と development/generalization 軸の2つに分かれる
  （`evaluation/probe_manifest.json#measurement_boundary` が明文化する
  既存の境界、本 manifest はこれを変更しない）。identity 軸は
  revision_bridge の7 metric-path（PINNED 済み `probe_manifest_sha` 側）
  それぞれについて、extractor（WORLD/pyworld、
  `voice_genesis/foundry/adapter/donor_bank.py:190-196
  analyze_donor_world()` — grep で実在確認済み）+ normalization
  （`level_normalization`）の参照カタログを追加した（式・閾値そのものは
  `inputs/identity_metric_space.json` を正本のまま重複定義しない）。
  development/generalization 軸（P4/P5、DESIGN_RUN9 §16.3
  DevelopmentalVector の9指標 + §14 C4 GENERALIZED_GAIN）は対応する
  extractor が VG-L0 学習ハーネス未実装のため repo に実在せず（grep 確認:
  pitch_gain/voicing_gain/duration_gain/energy_contour_gain/attack_gain/
  phrase_end_gain/lyrics_delta/artifact_delta/identity_delta/
  GENERALIZED_GAIN のいずれも `*.py` 実装なし——コメント・テスト文字列中
  の言及のみ）、閉じた metric 名の語彙のみを `NOT_YET_IMPLEMENTED` として
  正直に凍結した（extractor・数値をここで発明していない——ハーネス実装後
  に別途 design_revision で確定する）。`run9_schema.validate_measurement_
  spec_manifest()` + `load_pinned_measurement_spec_manifest()` を新設し、
  `RUN9_CONTRACT.yaml` `measurement_spec_sha` を **PINNED** 化した。
- **`gate_state()` は依然 `BLOCKED`**（`attempt_id`/`repository_commit_sha`/
  `dataset_manifest_sha`/`dataset_row_order_sha`/`config_sha`/
  `dependency_pins_sha`/`execution_profile_sha`/`expected_speaker_map_sha`/
  `education_technique_lesson_manifest_sha`/`learning_recipe_sha`/
  `hypothesis_algebra_sha` の pre-run 必須11欄が引き続き PENDING のため
  ——`tests/test_run9_contract.py` の回帰テストで機械確認済み）。

**再現レシピ（逐語・実行可能、Codex bot レビュー PR #323 第5巡指摘, P2,
採用, Fix 5）**: fresh checkout の読者が上記 PINNED バイトを実際に再生成
できる手順を逐語で示す（2026-08-25 本セッションで実測済み・初回一致）。
**一括実行する場合は、以下のステップ列を貼る前に必ず最初に実行する**
（Codex bot レビュー PR #323 第10巡指摘, Fix 10a, P2, 採用 — 個々の
ステップは非零 exit を返すが、シェルのデフォルト挙動ではその非零 exit
がスクリプト全体を止めない。これを明示しないと「不一致なら停止」という
本レシピの主張と実挙動が食い違う。第11巡指摘（Fix 11, P2, 採用）で
作業ディレクトリの作成もここへ集約した——**git 操作（step 4a の
`git log`/`git worktree` 等）は repo root で実行しつつ、zip・展開物・
生成物というデータは一貫して `$workdir` 側に置く分離**が本レシピ全体の
方針であり、この分離により「実 PJS 音源・展開物は repo 配下へ置いて
いない」という本 README の宣言（上記「解消済み（実 PJS practice split
実行）」節）と実際のレシピ挙動が一致する）:
```bash
set -euo pipefail
workdir="$(mktemp -d)"
export PJS_WORKDIR="$workdir"
```

**依存導入**（Codex bot レビュー PR #323 第13巡指摘, P2, 採用, Fix 13 —
clean Python 環境では上記ブロックの実行後も `gdown` を含め依存が一切
未導入のため、下記 step 1 の `import gdown` はもちろん、step 4c の
`import practice_split_builder`（→ `run9_schema` を import）が
`ModuleNotFoundError` で止まる。producer tree の実ソースを本セッションで
確認したところ、生成経路（`build_practice_split_manifest` /
`dump_practice_split_manifest_bytes`）が要求する top-level import は
`practice_split_builder.py` の `numpy` と `run9_schema.py` の `PyYAML`
（`pyproject.toml:13-25` の該当行）のみ——`librosa` は acoustic inventory
sidecar 専用関数 `_measure_pitch_range_hz` 内のローカル import で、本
生成経路には到達しない。以下のいずれかを、このステップ列より先に実行
する。第14巡指摘, P2, 採用, Fix 14 — 推奨コマンド `pip install
-e ".[dev]"` 単体では `gdown` が入らず（`pyproject.toml` の本体依存にも
`dev` extra にも `gdown` は含まれていない——本セッションで実ファイルを
再確認済み）、推奨側を選んだ読者が step 1 の `import gdown` で
`ModuleNotFoundError` に陥る欠陥があった。`pyproject.toml` へ `gdown` を
追加する案は不採用——`gdown` は本レシピ（PJS 公開配布物の再現取得）
専用であり、プロジェクト本体が実行時に要求する依存ではないため、
本体依存表を汚染しない。代わりに推奨コマンド自体へ `gdown` を追記し、
1コマンドで完結させた）:
```bash
# 推奨: リポジトリ標準の導入手順（repo root で実行）+ 本レシピ専用の
# `gdown`（`pyproject.toml` の本体依存にも `dev` extra にも含まれない
# ため、CLAUDE.md Commands 節のコマンドへ追記する形で1コマンド化）
pip install -e ".[dev]" gdown

# 代替（最小・実ソース確認済みの閉包のみ。リポジトリ全体の開発環境が
# 不要で本レシピの実行だけが目的の場合）
pip install numpy pyyaml gdown
```

1. **取得**（`gdown` 未導入なら `pip install gdown`。ミラー入手でも可
   ——要件は次段の sha 一致のみ。Codex bot レビュー PR #323 第11巡指摘,
   P2, 採用, Fix 11 — 出力先を repo root 直下の相対パスから `$workdir`
   内へ変更した。旧版は repo root で実行することを要求しながら zip を
   CWD 直下へ書いており、実行のたびに 275MB の実音源が checkout 内へ
   untracked のまま残る欠陥があった）:
   ```
   python3 -c "
   import gdown
   gdown.download(
       'https://drive.google.com/uc?id=1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_',
       output='$PJS_WORKDIR/PJS_corpus_ver1.1.zip', quiet=False,
   )
   "
   ```
2. **検証**（Codex bot レビュー PR #323 第7巡指摘, P2, 採用, Fix 7a —
   非対話実行では `sha256sum` を素の digest 出力のまま使うと、ファイルが
   読める限り常に exit 0 を返し不一致を検出しない致命的欠陥だった。
   `sha256sum -c -` は期待値との不一致で非零 exit を返す。第10巡指摘
   （Fix 10a, P2, 採用）: `set -euo pipefail`（上記）を実行し忘れた場合
   でもこの1ステップ単体で確実に停止するよう、`|| exit 1` を明示併記
   する——ステップ列の「不一致なら中止」という主張と実挙動を一致させる
   二重の安全策。第11巡指摘（Fix 11）で照合対象を `$workdir` 内へ変更）:
   ```
   echo "683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca  $workdir/PJS_corpus_ver1.1.zip" | sha256sum -c - || exit 1
   # 不一致なら "FAILED" + 非零 exit（$?）で停止。後続手順を実行しないこと。
   ```
3. **展開**（plain unzip・オプション無し・リネーム/変換一切なし。
   Codex bot レビュー PR #323 第11巡指摘, P2, 採用, Fix 11 — 展開先を
   repo root 直下の `./extracted` から `$workdir/extracted` へ変更した
   ——275MB の実 WAV コーパスが checkout 内に untracked のまま残る
   経路を閉じる）:
   ```
   unzip -q "$workdir/PJS_corpus_ver1.1.zip" -d "$workdir/extracted"
   # corpus_root = $workdir/extracted/PJS_corpus_ver1.1
   #   （pjsNNN/pjsNNN.lab + pjsNNN/pjsNNN_song.wav を含む階層）
   ```
4. **生成（producer tree で実行）**（Codex bot レビュー PR #323 第8巡
   指摘, P2, 採用, Fix 8 — 現在 checkout の `run9_schema.py` を import
   すると、`assign_split()` が消費する `LEARNING_SEED` や検証ロジックが
   producer revision と異なる可能性がある。記録・照合済みの producer
   sha は `practice_split_builder.py` 単体のみで、`run9_schema.py` 側の
   seed/検証/出力パス変更ではこの sha 照合が green のままレシピが pin
   バイトを再現できなくなり得る——依存閉包全体（上記「依存閉包の範囲」
   参照）を実際に checkout してから実行する）:

   a. producer revision を特定（既記載の「第一の再現ポインタ」。Codex
      bot レビュー PR #323 第10巡指摘, Fix 10c, P2, 採用 — `--depth 1`
      等の shallow clone では `git log --follow` が shallow 境界を
      返し、実際の producing commit と食い違う。shallow か判定し、
      shallow なら先に完全履歴へ展開してから実行する。**本ステップは
      repo root で実行する**——`git log`/`git fetch` は checkout 内の
      `.git` を対象とする操作であり、データを置く `$workdir` とは
      別軸）:
      ```
      if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
        git fetch --unshallow
      fi
      producer_rev=$(git log --follow --format=%H -1 -- voice_genesis/evolution/run9_dual_founder_pjs/inputs/practice_audio_split_manifest.json)
      echo "$producer_rev"
      ```
   b. producer tree を worktree として取り出す（`$workdir` 内・
      Codex bot レビュー PR #323 第10巡指摘, Fix 10b, P1, 採用 —
      固定パス `/tmp/pjs_producer` は中断・並行実行時に `git worktree
      add` の衝突/失敗を招くため、上記で作成済みの一意な `$workdir` の
      下へ配置する。**本ステップも repo root で実行する**——
      `git worktree add` 自体は checkout の `.git` に対する操作であり、
      取り出し先パスが `$workdir` 配下であることと矛盾しない）:
      ```
      git worktree add "$workdir/producer_tree" "$producer_rev"
      ```
      **本手順は現在 checkout が producer revision と一致している場合
      でも省略せずそのまま実行する**——分岐は不要（Codex bot レビュー
      PR #323 第12巡指摘, P2, 採用, Fix 12。〔履歴: Fix 8（第8巡）で
      「現在 checkout が producer revision と一致する場合は in-place
      実行が等価であり worktree 手順は省略してよい」という近道注記を
      導入したが、第12巡までの改訂（Fix 9 の heredoc 化・Fix 10b/11 の
      `$workdir` 集約）で step 4c/4d は無条件に `$workdir/producer_
      tree/...` を参照するようになり、近道に従うと `git worktree add`
      を飛ばした読者が `ModuleNotFoundError`（4c）/存在しない worktree
      への `remove` 失敗（4d）に陥る矛盾が生じた。worktree 手順自体は
      checkout が producer revision と一致していても問題なく動作する
      ため in-place 代替の2系統を維持する価値がなく、近道注記を撤去し
      単一経路（worktree 手順を常に実行）へ一本化した → 第12巡で解消〕。
   c. worktree 内の `run9_dual_founder_pjs` を `sys.path` 先頭にして
      実行し、`$workdir/extracted` のコーパスを入力に、出力を worktree
      外の `$workdir` へ書き出す（現在 checkout の `PRACTICE_MANIFEST_
      PATH` を直接上書きせず、生成に使ったコードと出力先を分離する。
      Codex bot レビュー PR #323 第9巡指摘, P2, 採用, Fix 9 — 旧版は
      コードフェンスが素の Python のままで、逐語シェル実行の主張と
      矛盾していた。`python3 - <<'EOF' … EOF`（クォート付き heredoc
      デリミタでシェル変数展開を防ぐ）へ改め、そのままシェルへ貼れる
      形にした。第10/11巡指摘（Fix 10b/Fix 11）で worktree・出力・入力
      コーパスをすべて `$workdir` 側へ揃えたが、クォート付きデリミタは
      維持したまま——heredoc 内へは環境変数 `PJS_WORKDIR`（上記
      `export`）を `os.environ` 経由で渡し、シェル変数展開に頼らない）:
      ```bash
      python3 - <<'EOF'
      import os
      import sys

      workdir = os.environ["PJS_WORKDIR"]
      sys.path.insert(0, os.path.join(workdir, "producer_tree", "voice_genesis", "evolution", "run9_dual_founder_pjs"))
      import practice_split_builder as psb

      manifest = psb.build_practice_split_manifest(
          os.path.join(workdir, "extracted", "PJS_corpus_ver1.1"),
          expected_corpus_identity=psb.EXPANDED_CORPUS_IDENTITY_SHA256,
      )
      with open(os.path.join(workdir, "output.json"), "wb") as f:
          f.write(psb.dump_practice_split_manifest_bytes(manifest))
      EOF
      ```
      （`expected_corpus_identity` に渡した `psb.EXPANDED_CORPUS_
      IDENTITY_SHA256` はモジュール冒頭でハードコード転記された定数
      ——照合対象はこのコード自体に焼き込まれており、外部ファイルからの
      読み込みではない。**必須引数**——省略不可・デフォルト値なしで、
      渡した値と展開コーパスから再計算した `expanded_corpus_identity_
      sha256` が厳密一致しない場合 `Run9ValidationError` で fail-closed
      拒否する）。
   d. worktree を片付ける（`$workdir` 全体の削除は step 5 の照合完了後
      ——出力ファイル・展開コーパス・zip がまだ `$workdir` 内にある
      ため）:
      ```
      git worktree remove "$workdir/producer_tree"
      ```
5. **照合**（`$workdir` 内の生成物が pin 値と一致することを確認——
   Fix 7a の `sha256sum -c -`/python `assert` 形式・Fix 10a の
   `|| exit 1` 併記を維持。Fix 10b で照合対象を `$workdir/output.json`
   へ変更）:
   ```
   echo "fd06000888736e87bba867b48fdf5651cf7c53b152121a318d1e10f11373f1e6  $workdir/output.json" | sha256sum -c - || exit 1
   # （= RUN9_CONTRACT.yaml practice_audio_split_manifest_sha の pin 値。
   #    不一致なら "FAILED" + 非零 exit。）
   ```
   一致確認後、必要なら現在 checkout の `PRACTICE_MANIFEST_PATH`
   （`voice_genesis/evolution/run9_dual_founder_pjs/inputs/
   practice_audio_split_manifest.json`）へ配置する（`cp` 等。producer
   revision が現在 checkout と一致する通常運用ではこの配置は不要——
   コミット済みファイルがすでに同一バイトのため）。`row_order_sha256`
   の照合も同ファイルに対して同様に行う:
   ```
   python3 -c "
   import json
   d = json.load(open('$workdir/output.json'))
   assert d['row_order_sha256'] == '6b8435bcf006e9dc90bd5272671da84ee7c82baaaad497ea2926a811e6e9d45a', d['row_order_sha256']
   print('row_order_sha256 OK')
   "
   ```
   最後に作業ディレクトリを片付ける（Codex bot レビュー PR #323 第11巡
   指摘, P2, 採用, Fix 11 — この1コマンドが zip・展開コーパス・
   worktree 出力（producer_tree はステップ d で既に remove 済み）を
   すべて含む `$workdir` を一括で除去するため、成功時に repo checkout
   側へ実音源由来のファイルは一切残らない）:
   ```
   rm -rf "$workdir"
   ```

**producer pin の意味論**（指摘の「producer script/revision を artifact
と別途 pin せよ」への回答 = 追加機構は不要と整理）: `practice_split_
builder.py` は manifest（`inputs/practice_audio_split_manifest.json`）と
**同一リポジトリ・同一コミットで版管理**されている——別ファイルへの
producer pin を新設しなくても、manifest を生成したコミット自体が
producer の版を一意に定める。加えて `PJS_SOURCE_ARCHIVE_SHA256`/
`EXPANDED_CORPUS_IDENTITY_SHA256`（照合対象の期待値）はモジュール冒頭に
ハードコード定数として直接埋め込まれており、`LEARNING_SEED`
（`run9_schema.py`、`assign_split()` が消費）を含め producer 側のロジック
や定数を変更すれば、再生成したバイト列の sha256 が変わり `RUN9_CONTRACT.
yaml` の pin 値との照合で機械的に検出される——この構造自体が producer
pin として機能する（fail-closed）。したがって本レシピは「pin と同一
バイトを再生成する手順」であり、producer 変更後の再生成は新しい repin
（PR レビュー経由での pin 値更新）として扱う——変更前後のバイトを
黙って同一 pin の下に混在させることは構造的にできない。

**依存閉包の範囲**（Codex bot レビュー PR #323 第8巡指摘, P2, 採用,
Fix 8 — 明確化）: 生成の依存閉包は `practice_split_builder.py` 単体
ではなく、**producer revision 時点の `voice_genesis/evolution/
run9_dual_founder_pjs/` パッケージ全体**である。`build_practice_split_
manifest()` は同ディレクトリの `run9_schema.py`（`Run9ValidationError`/
`_require_no_duplicate_list_items`/`_compute_canonical_pin_sha256` 等）
を import し、`assign_split()` が消費する `LEARNING_SEED` もこのファイル
定義。記録・照合している builder sha256（下記）は**「split ロジック
本体の同一性の証跡」に過ぎず、この閉包全体を覆わない**——
`run9_schema.py` 側の `LEARNING_SEED`/検証ロジック/出力パス定数が変わって
も builder sha256 の照合は green のままレシピが pin バイトを再現できなく
なり得る（`practice_split_builder.py` は無変更のため）。閉包全体を pin
するのは特定ファイルの sha 列挙ではなく、下記「生成」ステップが行う
**producer tree からの実行**である——`run9_schema.py` はコメント編集
だけで頻繁に変わるファイルであり（本 PR の Fix 2 が実例——コメントのみの
是正でもファイル実バイトの sha256 は変わる）、依存閉包の全ファイルを
個別に sha pin する方式は再 pin の頻度が高く脆いため不採用とした。

**producer revision の具体記録**（Codex bot レビュー PR #323 第6巡指摘,
P2, 採用, Fix 6 — 第5巡の整理は「生成コミット自体が producer pin」と
述べたが、その生成コミットの具体値を記録しておらず、後日 checkout の
読者にとって `git rev-parse HEAD` は現在のコミットを返すだけで、
pin 済みバイトを実際に作った実装を特定・再実行できないという残欠陥
だった。**第7巡指摘（P2, 部分採用, Fix 7b）で優先順位と主張範囲を
是正**——詳細は下記）:

- **第一の再現ポインタ = `git log --follow`（汎用手順、完全履歴の
  checkout で常に有効）**〔Codex bot レビュー PR #323 第10巡指摘（Fix
  10c, P2, 採用）: `--depth 1` 等の shallow clone では `git log
  --follow` が shallow 境界（浅履歴の先頭コミット）を返し、実際の
  producing commit を返さない——「常に有効」の記述は精密化した。shallow
  clone 判定・unshallow 手順は下記 step 4a の逐語コマンド参照〕:
  ```
  git log --follow -- voice_genesis/evolution/run9_dual_founder_pjs/inputs/practice_audio_split_manifest.json
  ```
  manifest バイトを最後に変えたコミットが、その時点の producer revision
  ——git 履歴自体が台帳であり、下記の固定 40hex を手作業で最新に保つ必要
  はない。**squash merge でも merge commit でも、完全履歴の checkout
  なら常にこのコマンド1つで producer revision が得られる**——後続の
  builder sha256 照合と合わせて使う一次手順とする。
- **生成時点の builder 内容 sha256**（履歴の取り込み方式に依存しない
  ——`git log --follow` で特定した producer revision の tree から直接
  照合できる値）:
  `894451c953d5eb5b50448687480ede9b7b808c8c2c620a97b63978704e37d479`
  （本 README 執筆時点でも同一——`practice_split_builder.py` は本節
  記載の生成イベント以降無変更）。
- **生成イベントの attestation（証跡・降格記録）**: 生成コミット
  （`inputs/practice_audio_split_manifest.json` の初コミット +
  `RUN9_CONTRACT.yaml` `practice_audio_split_manifest_sha` PINNED 化を
  含むコミット）は本 PR 側の `bf056ae635b2435e6888b85091c65626a9b0e3a3`
  （2026-08-25 push 済み・不変）。〔Codex bot レビュー PR #323 第7巡
  指摘（P2, 部分採用）: この値を「fresh clone から checkout 可能な
  再現パス」と主張していたのは過大——本 PR が **merge commit** で
  main へ取り込まれれば `bf056ae…` は main の履歴からも到達可能になる
  が、**squash merge** の場合 PR 側 object は main から到達不能になり、
  読者は別途 PR ref の fetch を要する。マージ方式は README が保証できる
  事項ではないため、本値は「checkout 保証付きの再現パス」ではなく
  **「この sha256 群を生成した実際のイベントを指し示す attestation
  （証跡）」へ降格する**——実行可能な再現パスとしては上記「第一の再現
  ポインタ」（`git log --follow`）を使うこと。指摘が示した反例
  `7dad04d…` は本 PR のいかなる local/remote object にも実在せず
  （`git cat-file -t 7dad04d` は該当なし・`git ls-remote` にも出現しな
  い）、`bf056ae…` は本 PR head の直系祖先である（`git merge-base
  --is-ancestor bf056ae635b2435e6888b85091c65626a9b0e3a3 <PR head>` は
  真——`bf056ae→e9f953b→6c4845a→d5f4dc0→ed18194→d85eb4f→f694701` の
  線形連鎖）——「reviewed commit の祖先でない」という指摘の副次的主張
  は事実誤認だった（1854b92 との merge-base は「main の現 tip との」
  merge base であり、`bf056ae…` が本 PR 側でまだ main に取り込まれて
  いないことの帰結にすぎず、祖先関係の否定にはならない）。ただし
  **核心（checkout 可能性はマージ方式依存であり保証できない）は正しい
  ため採用**——上記の降格・優先順位変更で対応した。
- **更新規約**: `practice_split_builder.py` または `LEARNING_SEED` を
  変更して manifest を再生成する場合は、その変更を repin（PR レビュー
  経由での `practice_audio_split_manifest_sha` 更新）として扱い、
  同じ PR で本節の producer 記録（生成コミット sha・builder sha256）も
  同時に更新する——`tests/test_run9_contract.py` の
  `test_fix323_6_readme_builder_sha_matches_actual_file` が、記載
  builder sha256 と実ファイルの実測 sha256 との不一致を fail-closed で
  検出し、この同時更新を機械強制する。

**残存**:

1. **PJS 側の残る未解決欄**（User donor 側は上記の通り 2026-08-25 attest
   完了・解消済み）: `inputs/rights_manifest.json`（rev 0.4 で4層構造
   `voice_identity_rights`/`performance_rights`/`composition_rights`/
   `recording_master_rights` へ再編済み。Fable 起草済み）の現状（Codex bot
   レビュー PR #319 第2巡 Fix 5/6 で層別必須キー閉集合・語彙の仕分けを
   確定済み）:
   - **確定済み**: PJS の performer（`performance_rights.provenance.
     performance_author.performer`）・composer（`composition_rights.
     provenance.composition.composer`）は Junya Koguchi と外部資料出典
     付きで記録済み（2026-08-25 User 追加裁定②）。recording license も
     CC BY-SA 4.0 で機械検証済み（`recording_master_rights.license`）。
   - **未解決（recording master の owner）**: `recording_master_rights.
     provenance.voice_source.owner` は `<UNRESOLVED_EXTERNAL>` を維持 —
     論文著者性は録音物の権利保有の証拠でなく、権利者を名指しする
     disclosure が原典に見当たらないため（PR #319 第 4 巡指摘採用。
     裁定②の確定範囲は performer/composer のみ）。
   - **未解決（PJS 側・外部第三者事実）**: `lyricist`
     （`composition_rights.provenance.composition.lyricist`）は repo
     内・原典いずれにも個別クレジットの記録がなく `<UNRESOLVED_EXTERNAL>`
     のまま。`performance_rights`/`composition_rights` の
     `rights_class`/`consent_status` も、PJS 内部使用が rights-clean
     curriculum 要件を満たすかの最終確認（R9-G1 tooling の職務）が未了の
     ため `UNRESOLVED_EXTERNAL`（Fix 6: 旧 `PENDING_USER_ATTESTATION` は
     User が attest すべき欄と外部第三者事実欄の語彙を混同する誤用
     だったため張り替え済み——`voice_identity_rights` のみが
     `USER_ATTESTED_OWN_VOICE`（attest 完了後の現在値）を保持する対象、
     両者の仕分けの根拠は rights_manifest.json `history` 参照）。CC BY-SA
     4.0 の share-alike 義務が合成音声出力へ及ぶかという法解釈も
     `recording_master_rights.interpretations.
     share_alike_applies_to_synthesis_output`（`UNSETTLED_LEGAL_
     INTERPRETATION`）として未確定のまま分離保持されている。
   - **参考（User 帰属欄・引き続き not_granted）**: `voice_identity_rights.
     usage_grants` のうち `raw_audio_publication`/`model_general_
     distribution` は `run9_identity_anchor` とは別承認のため、2026-08-25
     の attest 完了後も引き続き `not_granted` のまま（rev 0.2 改訂4。RUN9
     の実行自体はこの2件を必要としないため本 blocker には数えない）。
2. **VG-L0 学習ハーネス（実行部）未実装**: rev 0.3 で三枝化された
   PRACTICE_FROM_AUDIO / TRANSFER_TECHNIQUE エッジ（書き込み先は改訂1・
   rev 0.3 改訂A で Performance ControlProfile と規定済み）の**基盤**
   （`run9_controlprofile.py`: profile schema・書込境界の機械強制
   `derive_profile()`・append-only 台帳 `Run9ProfileLedger`）は Phase 3
   で実装済みだが、実際に音声を処理して特徴抽出・差分推定・探索を行う
   ハーネス本体（builder が `derive_profile()` を呼ぶ実処理）は未着手。
   **ControlProfile Entry Gate**（旧 Adapter Entry Gate。改訂1
   §対応マップ項目1 — `control-layer ceiling evidence or explicit User
   waiver` は循環要求のため削除済み・不足時の状態名は
   `BLOCKED_CONTROLPROFILE_ENTRY`）の残る要件（calibrated Identity audit
   route / learning replay harness / rights-clean curriculum / fixed
   compute budget / frozen recipe / rollback path）はどれも準備段階に
   すら入っていない。
3. **PJS Performance Lesson / learning recipe の実体 build 未実施**（PRACTICE
   split は 2026-08-25 実 PJS 実行で解消済み — 下記参照。EDUCATION 側 /
   learning recipe は引き続き残存）: 改訂3で pin 方針（source archive pin /
   expanded corpus pin とは別の Lesson manifest を生成し pin）は確定した
   が、Lesson build 自体は VG-L0 ハーネス実装待ち。rev 0.3 でこの pin は
   EDUCATION 用 Technique lesson を指すと明確化され
   `education_technique_lesson_manifest_sha` へ改名（User 外部レビュー
   PR #317 P1-2 採用）——引き続き `PENDING`。~~PRACTICE 用の教師音声
   train/validation/sealed-holdout split manifest~~（正解 parameter を
   含まない生素材の分割、PoR §12）は
   [`practice_split_builder.py`](./practice_split_builder.py)
   （`build_practice_split_manifest()`/`assign_split()`、
   RUN9-BIRTH-PREP-1 §B）を実 PJS corpus ver1.1（CC BY-SA 4.0 公開配布物、
   sha256 検証済み）に対して実行し、`practice_audio_split_manifest_sha`
   （`RUN9_CONTRACT.yaml`、PR #317 Codex bot レビュー第2巡 Fix 6 で新設
   → P1-2 で改名）を **PINNED** 化した（2026-08-25、training 70 /
   validation 15 / sealed_holdout 15）。Phase 3 で
   `learning_recipe_sha`（schema `run9-learning-recipe/1.0`: 枝別 recipe
   `practice_recipe`/`education_recipe` の2節 + 共通 seed 909002 + 各枝
   `equal_budget_within_arm: true`）の**構造**も凍結した — PRACTICE を
   除く2欄（EDUCATION manifest / learning recipe manifest）は実体
   manifest の**生成**が VG-L0 ハーネス実装待ちのため PENDING のまま。
   manifest 自体の最低要件は
   `run9_schema.PRACTICE_MANIFEST_REQUIRED_KEYS`/
   `EDUCATION_MANIFEST_REQUIRED_KEYS`/`validate_practice_split_
   manifest()`/`validate_education_lesson_manifest()`/
   `validate_learning_recipe_manifest()` が凍結済み。
4. **education builder 未実装 / practice builder は実 PJS 実行まで完了**
   （RUN9-BIRTH-PREP-1 で配線 → 2026-08-25 実 PJS 実行で更新）: 情報境界
   （`run9_schema.PRACTICE_FORBIDDEN_INPUTS` /
   `PRACTICE_ALLOWED_DATA_INPUTS` / `PRACTICE_REQUIRED_AUTONOMOUS_
   OPERATIONS` / `PRACTICE_FORBIDDEN_EXTERNAL_ASSISTANCE` /
   `EDUCATION_ALLOWED_CHANNELS` / `EDUCATION_FORBIDDEN_INPUTS`）・
   書込境界（`BRANCH_WRITABLE_PARTITIONS` + `run9_controlprofile.
   derive_profile()`/`validate_branch_write()` による機械強制）・
   manifest 最低要件（`PRACTICE_MANIFEST_REQUIRED_KEYS`/
   `EDUCATION_MANIFEST_REQUIRED_KEYS`）・practice trace schema
   （`run9_controlprofile.SCHEMA_PRACTICE_TRACE`）・ControlProfile
   append-only 台帳（`run9_controlprofile.Run9ProfileLedger`）と結果分類
   （`BIRTH_OUTCOMES` 等6分類）の**基盤は Phase 3 で実装済み**。PRACTICE
   側は `practice_split_builder.py` を実 PJS コーパスに対して実行し
   `inputs/practice_audio_split_manifest.json` を実体発行済み。
   EDUCATION 側の builder（Technique extractor / Lesson builder）、および
   両枝を実際に呼び出して音声処理・特徴抽出・探索を行う学習ループ本体は
   未着手。

## 次フェーズ（machine-dependent）

Phase 3 で machine-independent な設計・schema・contract・validator は
一通り確定した。残 pin（machine-dependent、実測が必要なもの）:

- ~~**User anchor attest**~~ → 2026-08-25 User attestation 実行（User 裁定
  「承認する」）により解消済み。`anchor_hashes.user` を PINNED 化し、
  `is_pinned() == True` へ到達（上記「解消済み（2026-08-25 User
  attestation 実行）」参照——現行 pin 値・binding scope は同日 Codex bot
  レビュー PR #320 第2巡 Fix 3 是正後の値（第1巡 Fix 1 からのさらなる
  再限定）。詳細は同節参照）。
- ~~**`founder_genome_shas` の正式発行**~~ → RUN9-BIRTH-PREP-1（2026-08-25）
  で解消済み。`run9_schema.issue_founder_genome_document()` の出力バイトを
  `founders/R9F-0x_genome.json` として書き出し、`RUN9_CONTRACT.yaml`
  `founder_genome_shas.R9F-01/R9F-02` を各ファイルの raw sha256 で
  **PINNED** 化した（genome_id = `66f420672a154283` / `63f4b8f24b827cd4`、
  無変更）。詳細は上記「解消済み（RUN9-BIRTH-PREP-1）」節参照。
- ~~**`backbone_runtime_bundle_sha` PINNED 化待ち**~~ → 2026-08-25 User
  承認 b + 裁定① により解消済み。確定したのは歴史的 `render_code_commit`
  （RUN6 export 推定）自体ではなく、独立の前方宣言欄
  `run9_render_code_commit`（status: `DECLARED_FOR_RUN9`）——
  `render_code_commit` は `INFERRED_UNCONFIRMED` のまま（両欄は独立）。
  `backbone_runtime_bundle_sha` PINNED の根拠はこの前方宣言の確定（上記
  「解消済み（2026-08-25 外部指摘（AQUEST 山崎信英氏）を受けた派生設計
  変更メモの編入）」参照）。
- ~~**P0-P5 probe set manifest 未作成**~~ → RUN9-PROBE-1（2026-08-25）で
  解消済み。[`evaluation/probe_manifest.json`](./evaluation/probe_manifest.json)
  を起草し、`RUN9_CONTRACT.yaml` `probe_manifest_sha` を実バイト sha256
  で **PINNED** 化した。詳細は上記「解消済み（RUN9-PROBE-1）」節参照
  ——render 契約・revision_bridge の凍結までであり、実 render の実行
  自体は VG-L0 ハーネス実装待ちのまま（残存ブロッカー(2)）。
- ~~**practice manifest の実体 build**~~ → 2026-08-25 解消済み・
  **User 裁定による scoped 例外（分類の一般改訂ではない）**: 当初本節
  （machine-dependent 見出し配下）に置いていたが、本 PR セッション中
  （2026-08-25）に Claude が「practice split は公開 PJS 配布物 + sha
  完全一致検証で足り、User 手元コーパス不要」という事実確認を提示し、
  **User が「1を実行できるなら実行してください」と実行そのものを直接
  指示した**（本節の再分類・実行は User 裁定に基づく行為であり、Claude
  の単独判断による role split 上書きではない）。
  〔履歴: 当初の記述は「誤分類だった」「CLAUDE.md の…区分には該当しない
  ……公開配布物 + sha 検証で完結する work は Claude 側の通常実装ルートで
  完了可能」と、権限根拠を示さずに一般規則であるかのような書き方をして
  いた——Codex bot レビュー PR #323 第3巡指摘（P2, 部分採用）: 実音源処理
  を Codex/User 経路に留める CLAUDE.md:71-75 の一般分類を、根拠なく
  README が上書きして見え、後続セッションへ規約違反を誤って指示しうる
  という懸念は正当（将来汚染として採用）。ただし再分類の削除は不採用
  ——本件は User が本セッションで直接承認・実行指示した scoped な事実
  であり、削除は事実を消す逆方向の汚染になる。採った対応は出所と適用
  範囲の明記（本文）〕。
  **本 scoped 例外の成立条件**（`practice_audio_split_manifest` の生成
  作業のみに限定・他の実音源作業へ一般化しない）: (a) 入力が CC BY-SA
  4.0 の公開配布物（PJS corpus ver1.1、User の私物音源ではない）
  であること、(b) `pjs_source_archive_sha256`（配布 zip 全体）+
  `expanded_corpus_identity_sha256`（展開後コーパス identity）の完全
  一致検証により、実行環境・実音源そのものへの機械依存（どのマシンで
  展開したか・誰の手元にあるか）が構造的に消えること——不一致なら
  builder は fail-closed 拒否し部分続行しない設計そのものが、この条件を
  担保する。
  **CLAUDE.md:71-75 の一般分類は不変**: 「マシン依存（実音源・実重み
  ハッシュ・Suno 生成・G4 ライセンス目視）= Codex / User」という一般規則
  自体は正しいまま変更していない。本節は上記2条件を満たす1作業
  （practice split manifest 生成）に限った User 裁定済み scoped 例外の
  記録であり、CLAUDE.md/AGENTS.md 側の一般政策を書き換える一般規則
  ではない——一般政策の改訂自体は User 権限であり、本 PR では
  CLAUDE.md/AGENTS.md を一切改変していない。

  実際 `practice_split_builder.py`（`build_practice_split_manifest()`）を
  実行したところ `expanded_corpus_identity_sha256` は初回試行で一致し
  （song 数 100）、`inputs/practice_audio_split_manifest.json`
  （training 70 / validation 15 / sealed_holdout 15）を生成、
  `practice_audio_split_manifest_sha` を **PINNED** 化した。EDUCATION 側
  （`education_technique_lesson_manifest.json`）は上記 scoped 例外の
  対象外——builder 自体が未着手のまま machine-dependent（VG-L0 ハーネス
  実装待ち）に残る（残存ブロッカー(3)(4)）。
- **learning recipe manifest の実体 build**: `learning_recipe_manifest.json`
  （schema `run9-learning-recipe/1.0`、`run9_schema.LEARNING_RECIPE_
  MANIFEST_PATH` が規約パスを凍結済み・`validate_learning_recipe_
  manifest()` が構造を検証）の実体生成（残存ブロッカー(3)）。
- **identity metric space の実測校正**: `inputs/identity_metric_space.json`
  の `calibration`（`freeze_threshold`/`validity_gates`/`decision_rule`）が
  定める閾値生成（C0 95 パーセンタイル・C1 sham 副作用・positive/negative
  reference からの実測 threshold freeze）は spec の事前登録のみで、実測は
  birth probe 実行後（Founder 生成待ち）。`worked_example` は synthetic
  illustration であり実測ではない。

上記の残 pin を除く machine-dependent な実装作業:

- **practice/education harness 実装**: VG-L0 学習ハーネスの一部として、
  PRACTICE_FROM_AUDIO（Founder 自身の自律特徴抽出・差分推定・制御探索
  ループ、PoR §3.2 の基本ループ）と TRANSFER_TECHNIQUE（Technique
  extractor / Lesson builder → Founder 提示 → 自声再現、PoR §3.3 の基本
  ループ）を実装する。`PRACTICE_FORBIDDEN_INPUTS`/`EDUCATION_FORBIDDEN_INPUTS`
  を実行時検証で強制する構造（禁止入力が builder の入力経路に構造的に
  現れない設計）が要件。harness は `run9_controlprofile.derive_profile()`
  を通じて `Run9ProfileLedger` へ publish する（書込境界は Phase 3 で
  既に機械強制済み — builder は境界検証を再実装しない）。
- **6分類の実測パイプライン**: `BIRTH_OUTCOMES`〜`IDENTITY_OUTCOMES` を
  実際の render/measurement 結果から機械判定するロジックの実装（現状は
  語彙の凍結のみ）。`REQUIRED_GAIN_FIELDS`（held-out gain 必須4欄）の
  実測パイプラインも同様。identity 距離の実測は
  `inputs/identity_metric_space.json` の spec（WORLD sp 平均ベクトル・
  Euclidean 距離）を実装する。
- **C0/C1 の実 render**: `CONTROL_CONDITIONS` の両条件を実際に render し
  `control_conditions_satisfied()` で評価 readiness を確認するパイプ
  ライン。`run9_controlprofile.derive_profile(..., control_condition=...)`
  が C0/C1 それぞれの ControlProfile revision（`replay`/`r_sham`）を
  既に生成できる — harness はこれを呼ぶだけでよい。
- **practice trace の実体生成**: `run9_controlprofile.SCHEMA_PRACTICE_
  TRACE`/`validate_practice_trace()` の最低要件を満たす実トレースの記録
  （模倣対象選択・内部差分推定・探索履歴）は harness 実装時。
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

## ディレクトリ構成（Phase 3 時点）

```
run9_dual_founder_pjs/
├── DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md   # 正本（バイト同一コピー・不変）
├── DESIGN_RUN9_REVISION_0.2.md                                 # v0.1 差分メモ（2026-08-24 User 裁定5件、無改変・存続）
├── DESIGN_RUN9_REVISION_0.3.md                                 # rev 0.2 差分メモ（同日、PoR メモ編入。三経路分離・三層観測等。無改変・存続）
├── DESIGN_RUN9_REVISION_0.4.md                                 # rev 0.3 差分メモ（2026-08-25、外部指摘（AQUEST 山崎信英氏）を受けた派生設計変更メモの編入 + User用語整理裁定編入）
├── POR_CONCEPT_ADJUDICATION_20260824.txt                       # PoR 裁定ソース（uploads 原本とバイト同一・不変）
├── DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt                         # 派生設計変更メモ（AQUEST 山崎信英氏、byte-pin 不変）
├── RUN9_CONTRACT.yaml                                          # §23 Run Contract（部分 pin。af0/ritsu/backbone/backbone_runtime_bundle/por_adjudication/branch_write_policy/founder_genome_shas は PINNED。interventions・human_audit_mode・performance_source 欄）
├── README.md                                                   # 本ファイル
├── run9_schema.py                                              # domain / TRI_CROSSOVER / contract / 書込境界 / manifest validator / R9-G1・LessonRecord・rights 4層・founder genome 文書発行 他の run-local 正本
├── run9_controlprofile.py                                      # Phase 3: ControlProfile schema / derive_profile 書込境界機械強制 / Run9ProfileLedger / practice trace schema
├── practice_split_builder.py                                   # RUN9-BIRTH-PREP-1 §B: PRACTICE_FROM_AUDIO split manifest builder + advisory 音響 inventory sidecar
├── domains/
│   └── identity_domain_run9_v1.json                            # af0/ritsu/metric_space_sha/user が全て PINNED（2026-08-25 User attestation 実行・is_pinned()==True）
├── founders/                                                    # RUN9-BIRTH-PREP-1: issue_founder_genome_document() の出力バイトそのままの正式発行済み genome 文書
│   ├── R9F-01_genome.json                                       # genome_id = 66f420672a154283（AF0_DOMINANT）
│   └── R9F-02_genome.json                                       # genome_id = 63f4b8f24b827cd4（USER_DOMINANT）
├── inputs/
│   ├── af0_anchor_manifest.json                                # AF-P0 正典証拠の複合参照 manifest（anchor_hashes.af0 の入力）
│   ├── rights_manifest.json                                    # rev 0.4: 4層構造（voice_identity_rights/performance_rights/composition_rights/recording_master_rights）。voice_identity_rights は User donor rights（2026-08-25 attested、USER_ATTESTED_OWN_VOICE。anchor_hashes.user の入力）
│   ├── backbone_runtime_bundle.json                            # RUN6 backbone の checkpoint/config/vocoder/render commit（render_code_commit=INFERRED_UNCONFIRMED・run9_render_code_commit=DECLARED_FOR_RUN9）+ canon model assets 一式
│   ├── branch_write_policy.json                                # 枝別書込境界 manifest（State partition・writable集合・不変artifact一覧。PINNED）
│   └── identity_metric_space.json                              # Phase 3: identity metric space 事前登録 spec（正規形 sha256 が metric_space_sha を pin。rev 0.4: teacher 非所有注記追記・repin）
├── tests/
│   ├── test_run9_contract.py                                   # §27 最低テストの静的検証可能サブセット + Revision 0.2/0.3/0.4 対応テスト + User 外部レビュー P1/P2 対応テスト + Phase 3 item 1/3 テスト
│   ├── test_run9_controlprofile.py                             # Phase 3: run9_controlprofile.py の最低テスト（書込境界・ledger append-only/冪等/conflict・neutral profile 決定論・practice trace）
│   ├── test_run9_founder_genome_issuance.py                    # RUN9-BIRTH-PREP-1 §A: founder genome 文書発行の最低テスト（再生成同一性・契約照合・fail-closed継承）
│   └── test_practice_split_builder.py                          # RUN9-BIRTH-PREP-1 §B: practice_split_builder.py の最低テスト（決定論・件数境界・sidecar不干渉。合成 fixture のみ・実PJS非同梱）
└── results/
    └── .gitignore                                              # 実測結果は非同梱（§25 Atomic Results Bundle 用の空ディレクトリ）
```

設計書 §24 が推奨する `founders/` / `lesson/` / `learning/` / `evaluation/`
は、それぞれに実体を置く段階（実行順 step 4 以降）になってから作成する。
内容の伴わない骨組みは実装が進んだという誤った印象を与えるため、
先行して同梱しない。
