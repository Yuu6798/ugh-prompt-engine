# RUN9-L0-HARNESS-3c rev 0.6 — Identity Decision Protocol 事前登録 実装記録

Design 根拠: User 裁定「RUN9 User裁定 — Identity Calibration Degeneracy /
design_revision 0.6」（2026-08-27、repo 内収載
[`USER_ADJUDICATION_20260827_IDENTITY_REV06.txt`](./USER_ADJUDICATION_20260827_IDENTITY_REV06.txt)、
§1-§9 が governs）+ Fable 起草の実装指示（`h3c_rev06_design_spec.md`）。
第2 PR フェーズ1（design_revision 0.5 → 0.6）。

**本記録が明示的に宣言する境界**: **本 harness は事前登録のみで実測ゼロ
である。C0/C1 exact-replay attestation・positive reference audit・d12
Birth Identity Separation・PJS confuser 距離・post-learning identity
retention のいずれも本 PR では render・計測を一切実行していない。**
Birth Gate の実行は裁定 §8 が明示的に区切る「別途」の工程であり、本 PR
（第2 PR、事前登録一式の実装）マージ後に別途行う。

---

## 総合判定

**7件の成果物すべてを実装完了。design_revision 0.5 → 0.6 昇格の三点同期
（contract トップレベル欄 / `run9_schema.DESIGN_REVISION` 定数 /
`design_revision_doc_sha256` pin）+ `hypothesis_algebra_sha` の
H1-H6閾値校正欄→ Identity decision protocol pin 欄への用途確定完了。
`ruff check .` clean、`pytest` は run9 サブツリー 2584 件全 pass（新設
26 件を含む）。実装前グラウンディングで probe_manifest.json の repin は
不要と判定（該当する正典矛盾なし）、failure_abort_criteria.json は
Birth Gate 関連 2 rule の stale 文言を是正し repin した。**

| # | 成果物 | 状態 |
|---|---|---|
| 1 | `DESIGN_RUN9_REVISION_0.6.md` | 新規作成・完了 |
| 2 | `USER_ADJUDICATION_20260827_IDENTITY_REV06.txt` | 新規作成・裁定逐語 byte 一致確認済み |
| 3 | `inputs/identity_decision_protocol_v0.6.json` | 新規作成・§P 構造どおり |
| 4 | `run9_schema.py`: validate/load + outcome_detail 定数 | 新規実装・テスト green |
| 5 | `RUN9_CONTRACT.yaml`: hypothesis_algebra_sha PINNED + design_revision 0.6 昇格 | 完了 |
| 6 | probe bridge / failure routing 更新 | probe_manifest: 不要と判定（グラウンディング）/ failure_abort_criteria: rule 7/16 是正・repin（11世代目） |
| 7 | `HARNESS3C_REV06_RECORD.md`（本ファイル） | 本ファイル |

---

## 0. 実装前グラウンディング結果

裁定 §8 の実行順・design spec の指示に従い、着手前に以下を確認した。

### 0.1 rev 0.5 設計文書と HARNESS-3a の 0.4→0.5 昇格同期パターン

`DESIGN_RUN9_REVISION_0.5.md`「契約レベルの design_revision 昇格」節を
確認——design_revision を上げる際は (a) `RUN9_CONTRACT.yaml` トップレベル
`design_revision` 欄、(b) `run9_schema.DESIGN_REVISION` 定数、(c)
`design_revision_doc_sha256` pin の3箇所を**同一改訂内で同時に**更新する
規約であることを確認し、本改訂（0.5→0.6）でも同じ三点同期を実施した
（下記2節参照）。旧 revision 文書は無改変のまま存続させ、系譜表へ
sha256 を追記する規約も踏襲した。

### 0.2 `inputs/identity_metric_space.json` の実名確認

- feature/distance 節: `feature_extractor`（WORLD/pyworld）、
  `extraction_procedure`（harvest/cheaptrick、frame_period 5.0ms、
  voiced_mask=f0>0、sample_rate 正規化規則）、`identity_feature`
  （level-normalized log spectral envelope、`feature(x) = v(x) -
  mean(v(x))・1`）、`distance`（Euclidean、symmetric・deterministic）。
- calibration/decision_rule 節: `calibration.freeze_threshold.formula
  = theta_cal(F) = P95(D_C0(F))`、`calibration.validity_gates`
  （c1_gate/positive_reference_gate/negative_reference_gate）、
  `calibration.decision_rule.formula = d(r) <= theta_cal(F) ⟹
  STABLE_BY_MACHINE_METRIC`。
- feasibility_note: 「本 spec は事前登録であり、birth probe 実測で
  退化が判明した場合は PoR §9 [C] DESIGN FAILURE / UNOBSERVABLE として
  正直に閉じる。結果を見た後の metric 定義変更・閾値の事後調整による
  救済は行わない」——本改訂が pre-run design correction（結果観測前の
  是正）であり事後救済でないことの根拠として引用した
  （`DESIGN_RUN9_REVISION_0.6.md` §0）。
- P0 cell_ref: `evaluation/probe_manifest.json` revision_bridge の
  `c0_replay_takes`/`c1_sham_takes`/`positive_reference`/
  `reference_render`/`evaluated_renders` がいずれも `cell_ref:
  "P0-NEUTRAL-SAKURA-FRAGMENT"` を指すことを確認——本 protocol の
  `birth_identity_separation.cell_ref` も同一値へ固定した
  （`run9_schema._PROBE_EXPECTED_CELL_IDS["P0"]` を正本として loader
  cross-check (3) で再照合する）。
- PJS reference 定義: `confuser_control.pjs_reference_definition`
  （決定論的コーパス集約——`_song.wav` ペアの辞書順列挙 → 正規化 →
  feature 計算 → voiced_mask 除外 → 算術平均集約）を無改変のまま参照
  （`pjs_confuser.pjs_reference_ref` で dotted path 参照）。

### 0.3 `RUN9_CONTRACT.yaml` の pin 状況

- `hypothesis_algebra_sha` 着手前値: `{value: null, status: PENDING,
  reason: "H1-H6 の閾値（δtarget / εk）校正が未実施..."}`。
- `design_revision` 着手前値: `"0.5"`。
- `interventions.c0_replay_takes_per_founder`/
  `interventions.c1_sham_takes_per_founder`: いずれも `{value: 20,
  status: PINNED}`（無改変・rev 0.6 でも同数のまま参照）。

### 0.4 `evaluation/probe_manifest.json` の revision_bridge 正典宣言確認

grep により `hypothesis_algebra_sha` 文字列が probe_manifest.json 内に
**一切出現しない**ことを確認した（`measurement_spec_sha` のみが
`development_generalization_axis_source`/`scope_statement` で literal
PENDING として正典宣言されている）。PR #324 が是正した「PINNED 化が
別正典の literal PENDING 宣言と矛盾する」欠陥パターンには該当しない
——**probe_manifest.json の repin は不要**と判定した（`measurement_
boundary.scope_statement` が「distance/calibration/confuser_control の
式・閾値は本 manifest で重複定義しない」と明記するとおり、render
bookkeeping（何を鳴らすか）と decision semantics（どう判定するか）が
構造的に分離されているため、calibration/decision_rule の supersede は
probe_manifest 側の render 契約に影響しない）。回帰テスト
`test_rev06_probe_manifest_does_not_declare_hypothesis_algebra_sha_
pending` で固定した。

### 0.5 `inputs/failure_abort_criteria.json` の Birth Gate 関連 rule 監査

全20 rule を grep し、P95/theta_cal(F) 依存の記述を持つ2件を特定した:

- **rule 7**（`Birth Identity separation not established`、§22 step 7）:
  checkpoint が「identity_metric_space.json の distance/calibration
  evidence を用いた Codex/User の人間判定」と述べるのみで theta_cal(F)
  を明示していなかったが、machine_promotion_condition が「distance/
  calibration evidence から Birth Identity separation を単一閾値で自動
  判定する設計変更」を昇格条件として明示しており、rev 0.6 protocol
  （d12>0 machine feature 判定）がまさにこの設計変更に相当するため、
  checkpoint/machine_promotion_condition の両方を是正した（詳細は
  §1.2 参照）。
- **rule 16**（`Identity drift beyond non-inferiority`、§22 step 16）:
  checkpoint 内の傍論が「identity_metric_space.json calibration.
  decision_rule の theta_cal(F) 比較（既存・実装済み・実内容検査）」と
  述べており、theta_cal(F) が rev 0.6 で解析的退化・supersede 済みと
  なった現在は stale。本 rule 自体の結論（PROCEDURAL のまま）は変えず、
  傍論のみを是正した（詳細は §1.2 参照）。

rule 6（DUAL_BIRTH_VIABILITY）・rule 14（mandatory metric degeneracy、
H1-H6 δtarget 校正）は theta_cal(F) を参照しておらず対象外と判定した
（rule 14 は本改訂で用途確定した hypothesis_algebra_sha の別軸——H1-H6
の要否自体は rev 0.6 の対象外、design spec の指示どおり）。

---

## 1. 成果物別の実装詳細

### 1.1 `DESIGN_RUN9_REVISION_0.6.md` / `USER_ADJUDICATION_20260827_IDENTITY_REV06.txt`

- 裁定逐語は scratchpad 正本（`run9_user_adjudication_identity_rev06.md`）
  §1-§9 本文部分（【RUN9 User裁定...】から §9 末尾まで、末尾の転記注は
  非含有）を一字一句転記し、Python での文字列完全一致比較で確認した
  （transcription 誤り1件を実際に検出・修正済み——初稿で「C0/C1・
  Identity距離」と middle-dot へ誤記していたのを原文の「C0/C1/Identity
  距離」（スラッシュ）へ訂正した。以後の全一致確認は PASS）。
- `DESIGN_RUN9_REVISION_0.6.md` は rev 0.5 doc と同型式（裁定日/裁定者/
  design_revision/裁定ソース の header、§ごとの「裁定逐語 + 機械表現」
  構成、「design_revision 系譜」表）を踏襲し、§0 に退化の解析的確定
  （C0 母集団の render replay byte 決定論実測 → D_C0(F)=0×20 →
  theta_cal(F)=P95(D_C0(F))=0 の導出）を追加した。
- sha256 実測: 裁定 txt =
  `43c7e71cd3bcb7cf3840c67a18e4a4c35a0259b9e04b1335868c33e925420db1`、
  rev 0.6 doc =
  `40f027c247c380af57b767963af758fde0e4fa7a279f5fa68a8b7e55d10956af`
  （いずれも `RUN9_CONTRACT.yaml` の対応 pin 値と一致することをテストで
  固定）。

### 1.2 `run9_schema.py`

- `DESIGN_REVISION = "0.6"`（旧 "0.5"）+ 更新済みコメント。
- outcome_detail 語彙: `IDENTITY_PROTOCOL_C0_RENDER_MISMATCH_OUTCOME`/
  `IDENTITY_PROTOCOL_C0_FEATURE_MISMATCH_OUTCOME`/
  `IDENTITY_PROTOCOL_C1_MISMATCH_OUTCOME`/
  `IDENTITY_PROTOCOL_BIRTH_ESTABLISHED_DETAIL`/
  `IDENTITY_PROTOCOL_BIRTH_COLLAPSE_DETAIL`/
  `IDENTITY_PROTOCOL_RETENTION_STABLE_DETAIL` の6定数を新設——既存
  `BIRTH_OUTCOMES`/`SEPARATION_OUTCOMES`/`IDENTITY_OUTCOMES` は無改変の
  まま維持し、二層構造で併記した（`test_rev06_outcome_detail_constants_
  do_not_collide_with_existing_frozen_vocab` で機械固定）。
- `validate_identity_decision_protocol(data)`: manifest 単体の構造検証
  （未知/欠落キー拒否、schema 厳密一致、各節の閉じた必須キー集合、
  outcome_detail のリテラル一致、cell_ref/contract_field_ref の凍結値
  一致、immutability.unchanged/execution_order.prerequisites_before_
  birth_gate/invariants.same_attempt_prohibitions の順序込み完全一致）。
- `load_pinned_identity_decision_protocol(contract, *, domain, ...)`:
  `hypothesis_algebra_sha` pin の唯一の正規消費経路。3層防御は
  `_h3c_load_pinned_common()`（disk 正典再読込アンカー・in-process
  contract 改変検出・read-once 実バイト sha256 照合）を再利用し、
  design spec が要求する7 cross-check を実装した:
  1. 裁定 txt 実バイト sha256 照合
  2. `metric_reference.metric_space_sha` と `domain.metric_space_sha`
     （`domains/identity_domain_run9_v1.json` 由来）の一致
  3. `birth_identity_separation.cell_ref` と
     `_PROBE_EXPECTED_CELL_IDS["P0"]`（probe manifest 側の凍結正本）の
     再照合
  4. C0/C1 の `takes_per_founder` と `interventions.c0_replay_takes_
     per_founder`/`interventions.c1_sham_takes_per_founder` PINNED 値の
     一致
  5. `provenance.design_revision_doc.sha256` と `design_revision_doc_
     sha256` PINNED 値の一致
  6. outcome_detail 語彙一致（validator 側で既に強制済みのため loader
     では重複させない旨を docstring に明記）
  7. `supersede_declaration.{preserved,superseded}_sections` の各節名が
     `identity_metric_space.json` に実在することの dotted-path 走査
     （既存 `_resolve_identity_metric_space_ref()` を再利用）

  5 manifest 共通ヘルパー `_h3c_cross_check_adjudication_and_detail_
  record()`（`provenance.detail_record` 前提）は意図的に再利用しなかった
  ——本 protocol は python builder script を経由しない hand-authored
  文書であり、かつ本記録ファイル（`HARNESS3C_REV06_RECORD.md`）は実装
  完了後に書かれるため、protocol 側から前方参照させると執筆順序が
  循環する。

### 1.3 `RUN9_CONTRACT.yaml`

- `design_revision: "0.6"`（旧 `"0.5"`）。
- `design_revision_doc_sha256`: value を rev 0.6 doc の sha256 へ
  repoint（旧 rev 0.5 値は履歴として append-only 保持）。
- `hypothesis_algebra_sha`: `PENDING`（H1-H6 閾値校正欄）→ `PINNED`
  （identity_decision_protocol_v0.6.json の raw sha256、値
  `967e40c2291b7532783b0becd574f16fba63972b5007bbe5c055979ef1de8db3`）
  ——旧 reason は履歴として comment 内に保持。
- 上記3点昇格に伴い、pre-run PENDING 欄は 7 → 6 件（総 PENDING 8 → 7
  件）へ減少（`test_harness3c_rev06_pre_run_pending_count_is_six` 等の
  回帰テストで固定）。

### 1.4 probe bridge / failure routing 更新

- `evaluation/probe_manifest.json`: グラウンディング §0.4 の判定により
  **変更なし**。
- `inputs/failure_abort_criteria.json`: rule 7/rule 16 の checkpoint/
  machine_promotion_condition を是正（§0.5 参照、旧文言は〔履歴〕として
  rule 本文内に保持・削除しない。rule_id/enforcement/verbatim/分類数
  （MACHINE 1件 / PROCEDURAL 19件）はいずれも無改変）。raw byte sha256
  は `297dd46aaa8c520238072f93b9d5e18748dbdd31b4a389a4a8d7e48cd70d8cba`
  （11世代目 repin）へ更新し、`failure_abort_criteria_sha` を repin
  した。

---

## 2. 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2584 passed, 7 warnings in ~41s
```

新設テスト26件（`tests/test_run9_contract.py`、rev06_ prefix + 関連
design_revision 昇格テスト）+ 既存テスト18件の assertion 更新
（design_revision "0.5"→"0.6" 追随、pending count 7→6/8→7、
`REVISION_DOC_PATH` 0.5→0.6 doc への repoint、failure_abort_criteria
lineage 第11世代追加）で、design_revision 昇格の追随漏れがないことを
確認した。

正常系: `validate_identity_decision_protocol()` happy path、
reserialization byte 一致、`load_pinned_identity_decision_protocol()`
happy path（実 contract + 実 domain）。

改ざん拒否系: 未知/欠落 top-level key、誤 schema、C0 takes 型誤り、
birth cell_ref 誤り、outcome_detail 語彙誤り、immutability.unchanged
順序入替、supersede_declaration 非閉集合、裁定 sha 改ざん、
metric_space_sha 改ざん、C0 takes 数 contract 不一致、design_revision_
doc sha 改ざん、supersede 節名 typo（identity_metric_space.json 非実在
path）、in-process contract の disk 正典乖離、の13ケースがいずれも
`Run9ValidationError` で fail-closed 拒否されることを確認した。

---

## 3. design spec からの逸脱・判断事項

1. **`speaker_map_manifest.json` の自己申告 `design_revision` 欄
   （`_SPEAKER_MAP_ADJUDICATED_DESIGN_REVISION = "0.5"`）は本改訂で
   変更しなかった**。同定数直前のコメントは「以後 design_revision を
   変更する際は、契約レベル三点 + 本欄の計四点同期を同一 PR 内で行う
   規約とする」と述べているが、この欄は「speaker map 合成方式が
   adjudicate された design_revision」という**内容固有**の pin であり
   （§4 不変宣言・validator は manifest 自身の自己申告値の凍結として
   docstring がそう定義している）、rev 0.6 は speaker map 合成方式を
   一切変更していない（unrealized mass・非主張3点・禁止4項目のいずれも
   無改変）。教育レッスン manifest 等、他の H3 系 manifest はそもそも
   `design_revision` 自己申告欄を持たない（本改訂で新設した
   `identity_decision_protocol_v0.6.json` も同様に持たない）ことから、
   この欄を機械的に "0.6" へ追随させると「speaker map 合成方式が rev
   0.6 で再 adjudicate された」という事実に反する主張を manifest に
   持たせることになると判断し、変更を見送った。既存 validator
   （`validate_speaker_map_manifest()`）が要求する値は `_SPEAKER_MAP_
   ADJUDICATED_DESIGN_REVISION` 定数（"0.5" のまま）に対する厳密一致
   のみであり、本判断はテストを一切壊さない。この解釈に疑義があれば
   Fable の設計判定を仰ぐ。
2. **`identity_metric_space.json`・`domains/identity_domain_run9_v1.json`
   ・`founders/*.json`・`inputs/speaker_map_manifest.json` は 1 byte も
   変更していない**（`git status --short` で確認済み、immutability
   宣言の直接遵守）。
3. その他、design spec §P の構造・cross-check 7項目・実行順は逐語どおり
   実装し、逸脱はない。

---

## 4. PR #333 Codex bot レビュー第1巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`1373e1b0`）。4件全て採用（P1×2・P2×2）。裁定正本は本 PR の対象外
（既存裁定 §1-§9 の再解釈ではなく、実装欠陥の是正）。

### 4.1 指摘1（P1）: H1-H6 校正 gate の pre-run 閉集合からの脱落

**グラウンディング（着手前の事実確認、逐語引用）**:

- design v0.1 §18.1 Target Skill Gain（923-931行）:
  `LCB_95(Δtarget,i) > δtarget` / 「`δtarget` はpositive/negative
  controlから凍結する。」
- design v0.1 §18.2 Non-Inferiority（933-939行）:
  `LCB_95(Δk,i) >= -εk`（対象軸に Identity を含む）
- `inputs/failure_abort_criteria.json` rule 14 `machine_promotion_
  condition`: 「hypothesis_algebra_sha の schema/validator（§18.1
  δtarget 校正ロジック含む）が実装され、positive/negative control
  からの実測校正が完了した時点で MACHINE へ昇格する。」
- 同 rule 16 `checkpoint`: 「design_revision 0.6（RUN9-L0-HARNESS-3c
  rev 0.6）時点の identity_metric_space.json calibration.decision_rule
  の theta_cal(F) 比較は、rev 0.6 実行について inputs/identity_
  decision_protocol_v0.6.json の calibration/decision_rule 節へ
  supersede 済み」——supersede されたのは **theta_cal(F) 比較のみ**で
  あり、H1-H6 の δtarget/εk 校正要求そのものは supersede 宣言に含まれて
  いない。
- rev 0.6 裁定本文（USER_ADJUDICATION_20260827_IDENTITY_REV06.txt）
  §1-§9 のいずれにも H1-H6 δtarget/εk 校正の要否判断は含まれない（裁定
  は Birth Identity Separation + 学習後 Identity 保持判定の再設計に
  限定——rev 0.6 は H1-H6 校正要求を明示的に supersede していない）。
- RUN9_CONTRACT.yaml 旧 `hypothesis_algebra_sha` reason（本改訂前の
  〔履歴〕注記）: 「rev 0.6 裁定により Identity decision protocol の
  pin 欄へ用途確定し、**H1-H6 閾値校正は本欄の対象から外れた**」——
  用途変更そのものは裁定 §7 の直接実装であり正当だが、H1-H6 校正前提の
  追跡先が失われた。

**判定**: 要求は現存（supersede されていない）。**Fable 設計どおり
実装**: 新規 pre-run PENDING pin `hypothesis_threshold_calibration_sha`
を `RUN9_CONTRACT.yaml` に追加、`run9_schema.CONTRACT_PIN_FIELDS` へ登録
（`CONTRACT_POST_RUN_PIN_FIELDS`/`CONTRACT_OPTIONAL_PIN_FIELDS` のいずれ
にも含めない——`gate_state()` の pre-run 閉集合へ自動包含）。
`hypothesis_algebra_sha` 自体・裁定 §7 の pin 用途確定は無改変。
pre-run PENDING 件数の期待値を 6→7（総 7→8）へ全箇所追随（テスト5箇所
の既存回帰値更新 + 新設 `test_pr333_r1_pre_run_pending_count_is_seven`
+ `tests/test_h3c_learning_recipe_manifests.py::
test_h3c_pre_run_pending_field_count_is_seven` + README.md）。

### 4.2 指摘2（P1）: metric space 実バイトの再照合欠如

**事実確認**: `load_pinned_identity_decision_protocol()` の cross-check
(2)（旧 20465行付近）は `metric_reference.metric_space_sha`（protocol
側の宣言値）と `domain.metric_space_sha`（domain 側の宣言値）という
2つの**宣言済み文字列**同士の比較のみで、どちらも実ファイルバイトを
再ハッシュしない。cross-check (7)（旧 20522行付近）は
`_load_identity_metric_space_document()` で実ファイルを読むが、読んだ
バイトの sha256 を一切照合しない。結果、stale/改ざんされた
`inputs/identity_metric_space.json` は宣言値さえ一致していれば両
cross-check を素通りし得た。実測再現テスト
（`test_pr333_r1_load_pinned_identity_decision_protocol_detects_metric_
space_content_drift`）で、is修正前に本経路が改ざんを検出しないことを
確認した。

**実装**: 新規 `_load_identity_metric_space_document_verified()`
（read-once TOCTOU パターン、`_h3c_load_pinned_common()` と同型）を
新設し、実バイトから `_compute_canonical_pin_sha256()`（`metric_space_
sha` 自体の pin 規約と同一の正規形 sha256）を再計算して
`domain.metric_space_sha` と fail-closed で照合する。
`load_pinned_identity_decision_protocol()` の cross-check (7) をこの
新関数経由へ切替え。`validate_probe_manifest()` 側の既存呼び出し
（`_load_identity_metric_space_document()`、構造検証専用・domain 引数
なし）は変更していない（役割が異なる——本指摘の対象はあくまで
`load_pinned_identity_decision_protocol()` のcross-document 信頼判定）。
`Run9IdentityDomain.is_pinned()`/`content_digest()` が意図的に外部
アーティファクトの内容照合を行わない設計原則（`build_founder()`
docstring 6877-6890行、af0/ritsu/metric_space_sha は「取消意味論を
持たない外部アーティファクトへの形状pin」）とは非対称関係にない——
本修正は「pin 済み宣言値と実ファイルの整合」という TOCTOU 対策であり、
「pin 済み宣言値が外部の真の事実と一致するか」という R9-G1 tooling
（machine-dependent、未実装）の職務には踏み込んでいない。

テスト: happy path・sha 不一致拒否・実際の改ざん検出（内容ドリフト）・
`load_pinned_identity_decision_protocol()` 経由の統合確認、の4件。

### 4.3 指摘3（P2）: invalid/non-finite feature の未登録分岐

**実装**: `inputs/identity_decision_protocol_v0.6.json`
`birth_identity_separation` へ `invalid_or_nonfinite_feature` 分岐
（`condition`/`birth_outcome`（`NOT_ESTABLISHED`）/`outcome_detail`
（新定数）/`action`/`note`）を追加。新定数
`IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL =
"IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_INVALID_OR_NONFINITE_FEATURE"`
を新設（`BIRTH_OUTCOMES`/既存 outcome_detail 定数のいずれとも非衝突）。
`validate_identity_decision_protocol()` の `birth_identity_separation`
必須キー集合へ追加し、`not_established` と同型の検証（condition 非空・
birth_outcome 厳密一致・outcome_detail 厳密一致・action/note 非空）を
実装。d12=0 による feature collapse（既存 `not_established` 分岐）とは
別ラベルで区別——裁定§4「両featureがvalid/finiteであり、d12 > 0の場合
のみBIRTH=ESTABLISHED」の逆条件 + 裁定§9「Birth Gate不成立時は
NOT_ESTABLISHEDとして凍結する」の機械符号化であり、新規則の発明では
ない。裁定逐語の転記部分（USER_ADJUDICATION txt / rev doc 内の逐語
引用節）は無改変。

protocol バイト変更に伴い `hypothesis_algebra_sha` を repin（旧
`967e40c2291b7532783b0becd574f16fba63972b5007bbe5c055979ef1de8db3` →新
`304e72376e30e8e3974485d393c1f56a7256017588bc877c2be15f080291fb77`、旧
値は RUN9_CONTRACT.yaml の【repin 履歴】コメントへ append-only 保持）。
`design_revision_doc_sha256`（`DESIGN_RUN9_REVISION_0.6.md` を無改変の
まま参照）は本指摘では変更不要——protocol 側の
`provenance.design_revision_doc.sha256` は既存値のまま一致する。

### 4.4 指摘4（P2）: protocol 配列比較の dict 偽装

**事実確認**: `validate_identity_decision_protocol()` 内の3箇所
（`immutability.unchanged`/`execution_order.prerequisites_before_
birth_gate`/`invariants.same_attempt_prohibitions`）が旧実装で
`tuple(value) != EXPECTED_TUPLE` のみを検査しており、`value` が期待
文字列をキーとする insertion-ordered dict（値は任意）でも
`tuple(dict)` はそのキー列を返すため偽通過し得た。同モジュール内の
`_validate_identity_protocol_metric_ref_list()`（`supersede_
declaration.*_sections` 用）は既に `isinstance(value, list)` 形状検査
を先行させており、この穴を持たないことを確認——validator 内の
配列比較は上記3箇所のみが同型の欠陥を持っていた。

**実装**: 新規 `_require_ordered_str_list_matching_tuple()` helper を
新設し、`isinstance(value, list)` + 全要素 str の形状検査をタプル比較の
前に必須化。3箇所すべてをこの helper 経由へ置換。既存の順序込み厳密
一致（並び替え拒否）挙動は非回帰（`test_pr333_r1_validate_still_
rejects_reordered_list_after_shape_guard` で確認）。

### 4.5 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2597 passed, 7 warnings in ~40s
```

新設テスト: `tests/test_run9_contract.py` に18件（指摘1のカウント回帰1件
+ 指摘2の4件 + 指摘3の4件 + 指摘4の5件 + 既存カウントテスト5件の値
更新）。`tests/test_h3c_learning_recipe_manifests.py` の既存カウント
テスト1件も 6→7 へ値更新。

### 4.6 変更ファイル

- `run9_schema.py`: 新規 pin 名の登録・`_load_identity_metric_space_
  document_verified()` 新設 + cross-check (7) 差替え・
  `IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL` 定数 +
  `birth_identity_separation` 検証拡張・`_require_ordered_str_list_
  matching_tuple()` 新設 + 3箇所置換。
- `RUN9_CONTRACT.yaml`: `hypothesis_threshold_calibration_sha`
  新設（PENDING）+ `hypothesis_algebra_sha` repin（値・repin履歴コメント
  追加）+ `failure_abort_criteria_sha` 12世代目 repin（§4.8）。
- `inputs/identity_decision_protocol_v0.6.json`:
  `birth_identity_separation.invalid_or_nonfinite_feature` 分岐追加。
- `inputs/failure_abort_criteria.json`: rule 14/16 の checkpoint/
  machine_promotion_condition の stale `hypothesis_algebra_sha` 参照を
  `hypothesis_threshold_calibration_sha` へ差替え（§4.8）。
- `tests/test_run9_contract.py` / `tests/test_h3c_learning_recipe_
  manifests.py`: 上記の回帰テスト・カウント値更新（`failure_abort_
  criteria_sha` repin lineage テストの round12 追加を含む）。
- `README.md`: pre-run PENDING 件数の記述更新（6/7→7/8）+
  「解消済み（PR #333 第1巡指摘1, 2026-08-28）」節新設。

### 4.7 逸脱事項

- `inputs/failure_abort_criteria.json` rule 14/16 の stale な
  `hypothesis_algebra_sha` 参照は当初スコープ外として現状維持していたが、
  Fable 判定により是正済み——詳細は下記 §4.8 を参照。
- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）は 1 byte も変更していない（`git
  status --short` で確認済み）。

### 4.8 追加是正（Fable 判定、2026-08-28）: rule 14/16 の stale 参照是正

**Fable 判定**: §4.7 が報告した逸脱事項（rule 14/16 の
`machine_promotion_condition`/checkpoint が引き続き `hypothesis_algebra_
sha` を参照）は、本巡指摘1（P1「Keep the H1–H6 calibration gate
pending」）と同型の正典矛盾を残さないため是正する。

**対応**: `inputs/failure_abort_criteria.json` rule 14/16 の
checkpoint・machine_promotion_condition を、H1-H6 δtarget/εk 校正前提の
追跡先として `hypothesis_algebra_sha`（rev 0.6 で identity decision
protocol の pin 欄へ用途確定済み）ではなく新設 `hypothesis_threshold_
calibration_sha` を参照する文言へ差し替えた。rev 0.6 裁定 §7「同protocol
のraw SHA256をhypothesis_algebra_shaへPINNEDする」による用途確定の経緯を
一句添え、旧文言は〔履歴（PR #333 第1巡指摘1、2026-08-28）〕として rule
本文内に append-only 保持（削除しない）。`rule_id`/`enforcement`/
`verbatim`・分類数（MACHINE 1件 / PROCEDURAL 19件）はいずれも無改変。

**連鎖更新**: `inputs/failure_abort_criteria.json` の実バイト変更に伴い
`failure_abort_criteria_sha` を12世代目へ repin した（旧
`297dd46aaa8c520238072f93b9d5e18748dbdd31b4a389a4a8d7e48cd70d8cba` → 新
`3de4db27a23498c236b75b3efbb152c0675fce84fe2d6bddfb8bd565850b1251`、旧値
は `RUN9_CONTRACT.yaml` の【repin 履歴】コメントへ append-only 保持）。
`tests/test_run9_contract.py::
test_harness3b_failure_abort_criteria_repinned_lineage_ten_generations`
（12世代の repin lineage を通しで確認する既存の全履歴テスト、テスト名は
レビュー履歴保持のため改名しない）へ round12 の値を追加した。

**検証**:

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2597 passed, 7 warnings in ~41s
```

## 5. PR #333 Codex bot レビュー第2巡対応（2026-08-28、フェーズ1）

### 5.0 第1巡グラウンディングの見落とし（正直な記録）

§0.4 の第1巡グラウンディングは「`hypothesis_algebra_sha` という文字列が
probe_manifest.json に出現しない」ことのみを grep 確認し、「render
bookkeeping と decision semantics は構造的に分離されている」という一般論
から repin 不要と判定した。この判定は**文字列の不在**しか見ておらず、
`revision_bridge.*.identity_metric_space_ref` の**個々の参照先パス**が
`supersede_declaration.superseded_sections`（`calibration.freeze_
threshold`/`calibration.validity_gates`/`calibration.decision_rule`）の
どれかに実際に含まれているかどうかを一つも照合していなかった。第2巡
指摘1で実際に diff を取ったところ、C0/C1/positive/negative の4エントリ
の `identity_metric_space_ref` はいずれも supersede 済みの calibration
節（`calibration.freeze_threshold.d_c0_population`/`calibration.
validity_gates.{c1_gate,positive_reference_gate,negative_reference_
gate}.*`）を指したままであり、「render bookkeeping と decision semantics
は分離されている」という一般論は判定規則の実参照先については成立して
いなかった。以後の同種グラウンディングでは、文字列の不在確認だけでなく
supersede_declaration の対象集合と実参照先の突合せまで行う。

### 5.1 指摘1（P1）: bridge の参照先が supersede 済み calibration 節を
指したまま

**事実確認**: `evaluation/probe_manifest.json` revision_bridge の
`c0_replay_takes`/`c1_sham_takes`/`positive_reference`/
`negative_reference` 4エントリの `identity_metric_space_ref` が、rev 0.6
`supersede_declaration.superseded_sections` に列挙された calibration 節
（`calibration.freeze_threshold.d_c0_population`/`calibration.validity_
gates.c1_gate.d_c1_population`/`calibration.validity_gates.positive_
reference_gate.positive_reference_definition`/`calibration.validity_
gates.negative_reference_gate.negative_reference_definition`）をそのまま
参照していることを確認した。`inputs/measurement_spec_manifest.json`
`identity_axis_metric_paths` の同4エントリも同じ参照先を転記していた。
`reference_render`（`calibration.distance_unit.reference_render_
definition`）は `supersede_declaration` の対象外（`calibration.
distance_unit` は preserved/superseded いずれの列挙にも含まれない、単なる
render 定義）であるため対象から除外した。

**実装**: `identity_metric_space_ref` 自体は無改変のまま履歴参照として
保持し（旧参照を保持する履歴残置様式——probe の生成定義である cell_ref/
contract_field_ref/new_render_required は supersede 対象外のため元々
無改変で有効）、上記4エントリへ新規 `identity_decision_protocol_ref`
（判定規則の現行正本、`inputs/identity_decision_protocol_v0.6.json` の
対応節: `c0_determinism_attestation`/`c1_sham_attestation`/`positive_
reference_audit`/`birth_identity_separation.negative_reference_gate_
note`）+ `superseded_calibration_note`（supersede 済みであることの本文
明記、marker `"supersede"` を validator が強制）を追加した。
`_validate_revision_bridge_entry()`/`_validate_measurement_spec_metric_
path()` を拡張し、新規 `_load_identity_decision_protocol_document()`/
`_resolve_identity_decision_protocol_ref()`/`_REVISION_BRIDGE_EXPECTED_
DECISION_PROTOCOL_REF` でエントリ→期待 path の厳密対応（Fix 8 と同方式）
+ dotted path 実在走査 + probe_manifest.json ⇔ measurement_spec_
manifest.json の二重 pin 一致を fail-closed 強制する。

**repin cascade**: `probe_manifest.json` バイト変更に伴い
`probe_manifest_sha` を repin
（旧 `c6c4b862e775ce99e579ba8e4574453ae86048fa801c9b4e1265743021475534` →
新 `60adeb93b6ca920bdbc590f24ffdb62f68bd12a387e2543361d88954fb1932fe`）。
これに追随し `inputs/dataset_split_manifest.json`
`identity_probe.probe_manifest_sha` 転記値と、`pjs_song_based_probe_non_
adoption_citation`/`c1_sham_takes.description` への行番号引用（新規キー
挿入によるシフト: P4 role 786→787行、c1_sham_takes description
878→880行）を更新し、`dataset_manifest_sha` を repin
（旧 `ba52536c1e36f5d64018a2de7877c288c39ee855a0b463d937ace8032650d448` →
新 `4138639209caabf08465141681756e3b0bc7be4167516ea9bd93b6d276456cf4`）。
`inputs/measurement_spec_manifest.json` も同4エントリへ `identity_
decision_protocol_ref` を追加したためバイトが変わったが、
`measurement_spec_sha` は元々 PENDING（VG-L0 学習ハーネス実装待ちの
既存律速は不変）のため repin は発生しない——本文是正のみ。

### 5.2 指摘2（P2）: post_learning_identity_retention の invalid/
non-finite 分岐欠如

**事実確認**: `post_learning_identity_retention` が `stable`
（`m_other > 0 かつ m_pjs > 0`）/`shifted`（`m_other <= 0 または m_pjs
<= 0`）の2分岐のみで、両条件が前提とする「m_other/m_pjs が有限の実数値」
が成立しない invalid/non-finite feature の事前登録分岐が無いことを確認
した。既存 `IDENTITY_OUTCOMES` に `UNCALIBRATED` が実在することを確認
（`("STABLE_BY_MACHINE_METRIC", "SHIFTED", "UNCALIBRATED")`）。

**実装**: `post_learning_identity_retention.invalid_or_nonfinite_
feature` 節を新設し、`identity_outcome` を既存 `IDENTITY_OUTCOMES` の
`UNCALIBRATED`（tuple へ値追加はしない）、`outcome_detail` を新定数
`IDENTITY_PROTOCOL_RETENTION_INVALID_OR_NONFINITE_DETAIL`
（`IDENTITY_PROTOCOL_POST_LEARNING_INVALID_OR_NONFINITE_FEATURE`、第1巡
指摘3の `IDENTITY_PROTOCOL_BIRTH_NOT_ESTABLISHED_INVALID_OR_NONFINITE_
FEATURE` と同型命名）で凍結した。`validate_identity_decision_protocol()`
の `post_learning_identity_retention` 検証ブロックへ同型の分岐検証を
追加。偽 SHIFTED/偽 STABLE の記録を構造排除する測定失敗系の凍結であり、
裁定§6の両条件の逆条件（feature が invalid/non-finite）+ 裁定§9
fail-closed 原則の機械符号化——新規則の発明ではない。

**repin cascade**: `inputs/identity_decision_protocol_v0.6.json` バイト
変更に伴い `hypothesis_algebra_sha` を repin
（旧 `304e72376e30e8e3974485d393c1f56a7256017588bc877c2be15f080291fb77` →
新 `cde8b003ff88b78693c81058e3a80ec4fbfe546df7e3f8e61812c8d6f61c67c1`）。

### 5.3 指摘3（P2）: pinned revision document バイトの未再照合

**事実確認**: `load_pinned_identity_decision_protocol()` の cross-check
(5)（旧番号）は `provenance.design_revision_doc.sha256`（manifest 側の
**宣言値**）と `RUN9_CONTRACT.yaml` `design_revision_doc_sha256`
PINNED 値（contract 側の**宣言値**）の一致しか見ておらず、どちらの宣言値
も `DESIGN_RUN9_REVISION_0.6.md` の**実バイト**を一度も再ハッシュして
いなかったことを確認した（第1巡指摘2 が是正した `metric_reference.
metric_space_sha` の欠陥——宣言値同士の比較のみで実ファイルを再
ハッシュしない——と同型のパターンが、改訂文書側にも未是正のまま残って
いた）。

**実装**: 新規 cross-check (6) として、`provenance.design_revision_doc.
source_file`（`_resolve_repo_contained_path()` で repo-containment guard
を経由）を read-once で実バイト読込 + sha256 再計算し、cross-check (5)
で contract pin と一致確認済みの `manifest_rev_doc_sha` と厳密一致する
ことを fail-closed で強制する——cross-check (1)（adjudication_basis）と
同型の read-once + 実バイト sha256 照合。テスト用に `design_revision_
doc_path` override 引数を追加した（既存 `adjudication_basis_path` 等と
同型の慣習）。本改訂は `load_pinned_identity_decision_protocol()` の
ロジック追加のみで `identity_decision_protocol_v0.6.json` のバイトは
変えない（§5.2 の repin に含まれる cross-check 番号の付け替え——(11)〜
(13) へシフト——のみ、値は無改変）。

### 5.4 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2604 passed, 7 warnings in ~41s
```

新設テスト: `tests/test_run9_contract.py` に9件（指摘2の5件 + 指摘3の
2件 + カウント/repin 値更新テスト2件〔`test_pin2_dataset_manifest_sha_
is_pinned_and_matches_actual_file`/`test_pin1_r3_measurement_spec_
manifest_file_byte_unchanged_despite_pending_pin` の値更新〕）のうち
新規追加は7件（2597→2604）。指摘1は既存 validator/loader の拡張であり
新規テストは追加していない（既存の `test_rev06_validate_real_manifest_
happy_path`/`validate_probe_manifest`/`validate_measurement_spec_
manifest` 系の既存テストが実データを通すことで正常系を確認、fail-closed
分岐は §5.1 実装の拡張ロジックが既存の「エントリ→期待 path 厳密対応」
（Fix 8 パターン）を再利用しているため既存の型テストで構造的にカバー
される）。

### 5.5 変更ファイル

- `run9_schema.py`: `_IDENTITY_DECISION_PROTOCOL_REF_PREFIX`/
  `_REVISION_BRIDGE_SUPERSEDED_CALIBRATION_ENTRIES`/`_REVISION_BRIDGE_
  EXPECTED_DECISION_PROTOCOL_REF`/`_REVISION_BRIDGE_SUPERSEDE_NOTE_
  MARKER` 新設定数、`_load_identity_decision_protocol_document()`/
  `_resolve_identity_decision_protocol_ref()` 新設、`_validate_revision_
  bridge_entry()`/`_validate_measurement_spec_metric_path()` 拡張、
  `IDENTITY_PROTOCOL_RETENTION_INVALID_OR_NONFINITE_DETAIL` 定数新設、
  `validate_identity_decision_protocol()` の `post_learning_identity_
  retention` 検証拡張、`load_pinned_identity_decision_protocol()` へ
  cross-check (6) 新設 + `design_revision_doc_path` override 引数追加。
- `RUN9_CONTRACT.yaml`: `probe_manifest_sha`/`dataset_manifest_sha`/
  `hypothesis_algebra_sha` repin（値・repin履歴コメント追加）+
  `measurement_spec_sha` 本文是正コメント追加（pin 状態は PENDING の
  まま変更なし）。
- `evaluation/probe_manifest.json`: revision_bridge の
  c0_replay_takes/c1_sham_takes/positive_reference/negative_reference
  4エントリへ `identity_decision_protocol_ref`/`superseded_calibration_
  note` 追加。
- `inputs/measurement_spec_manifest.json`: identity_axis_metric_paths
  の同4エントリへ `identity_decision_protocol_ref` 追加。
- `inputs/dataset_split_manifest.json`: `identity_probe.probe_manifest_
  sha` 転記値更新 + 行番号引用2箇所（786→787、878→880）更新。
- `inputs/identity_decision_protocol_v0.6.json`: `post_learning_
  identity_retention.invalid_or_nonfinite_feature` 分岐追加。
- `tests/test_run9_contract.py`: 上記の回帰テスト・repin 値更新・新規
  fail-closed テスト追加。

### 5.6 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分（`USER_ADJUDICATION_
  20260827_IDENTITY_REV06.txt`）は 1 byte も変更していない（`git status
  --short` で確認済み）。
- 既存 frozen tuple（`BIRTH_OUTCOMES`/`IDENTITY_OUTCOMES`/
  `SEPARATION_OUTCOMES` 等）への値追加は行っていない——新設語彙は
  すべて `IDENTITY_PROTOCOL_*` の outcome_detail 定数側のみ。
- 指摘1の bridge 是正は「参照先の付け替え」を、既存参照を削除して
  上書きするのではなく、新規フィールド追加 + 旧参照の履歴保持という
  形で実装した（裁定の immutability 原則・第1巡の「履歴残置」慣習との
  整合を優先した設計判断——Fable 設計メモの「旧参照を保持（履歴残置
  様式）」指示に沿う）。

## 6. PR #333 Codex bot レビュー第3巡対応（2026-08-28、フェーズ1）

### 6.0 第2巡の見落とし（正直な記録）

第2巡指摘1は「bridge の `identity_metric_space_ref` が supersede 済み
calibration 節を指したまま」という欠陥を、新規 `identity_decision_
protocol_ref`（判定規則の現行正本）+ `superseded_calibration_note`
（本文明記）の追加で是正した。しかしこの是正自体が新たな曖昧さを
生んでいたことを見落としていた: `supersede_declaration.superseded_
sections` は `calibration.freeze_threshold`/`calibration.validity_gates`
を**節ごと**（節配下の全サブフィールドを含めて）supersede すると宣言する
一方、追加した `superseded_calibration_note` は同じ節配下の生成定義
（例: `calibration.freeze_threshold.d_c0_population` — founder-pooling
禁止・自己比較禁止という母集団の作り方の定義）を「参照により有効のまま
履歴保持する」と個別に宣言していた。つまり「節丸ごと supersede」という
宣言と、「節配下の特定サブフィールドは今も有効」という依存が同一 protocol
内で同時成立しており、機械的にどちらが正本か決定不能だった——第2巡時点
ではこの二重宣言の矛盾を検出する仕組み（閉じた集合検査・cross-document
照合）が存在しなかった。

### 6.1 指摘1（P1）: supersede 宣言と生成定義依存の同時成立

**事実確認**: `inputs/identity_decision_protocol_v0.6.json`
`supersede_declaration.superseded_sections` は
`inputs/identity_metric_space.json#calibration.freeze_threshold` /
`#calibration.validity_gates` / `#calibration.decision_rule` の3節を
**節ごと**列挙している。一方 `evaluation/probe_manifest.json`
`revision_bridge` の `c0_replay_takes`/`c1_sham_takes`/
`positive_reference`/`negative_reference` 4エントリの
`superseded_calibration_note`（第2巡で追加）は、同じ節配下の生成定義
（`calibration.freeze_threshold.d_c0_population` /
`calibration.validity_gates.c1_gate.d_c1_population` /
`calibration.validity_gates.positive_reference_gate.positive_reference_
definition` / `calibration.validity_gates.negative_reference_gate.
negative_reference_definition`）を指して「population定義（founder-
pooling・自己比較）自体は参照により有効のまま履歴保持する」「定義自体
（reference/C0双方と別の独立テイク1本）は参照により有効のまま履歴保持
する」と、判定規則としては supersede 済みだが**生成定義としては今も
有効**と明記していた（`run9_schema.py` 873-897/904-910行付近の
`_REVISION_BRIDGE_SUPERSEDED_CALIBRATION_ENTRIES` コメント + 実 JSON
`evaluation/probe_manifest.json` 873-910行付近を確認）。裁定§7 逐語
「旧calibration/decision ruleをrev 0.6実行についてsupersedeする」は
「calibration/decision rule」という語で判定意味論を指しており、生成
手順（母集団の作り方・reference の定義）の supersede までは明言して
いない——bridge note の解釈（生成定義は今も有効）は裁定逐語と整合する
一方、protocol 側の `superseded_sections` の**節丸ごと**列挙という
表現形式がその区別を機械可読な形で表していなかった、というのが本指摘の
核心である。指摘記載の行番号「probe_manifest.json 893-910 行付近」は
実ファイルの `negative_reference`（893-897行）/`positive_reference`
（904-910行）エントリと一致することを確認した（着手前の事実確認）。

**実装**: `identity_decision_protocol_v0.6.json` の
`supersede_declaration` へ `preserved_generation_definitions`（bridge
4エントリが実際に消費する生成定義 dotted path の閉じた列挙、4件）+
`preserved_generation_definitions_note`（機械可読な範囲宣言 — supersede
の対象は当該節の閾値推定・判定意味論のみであり、列挙した生成定義は生成
手順の正本として有効のまま、判定の現行正本は protocol 側の対応節を参照
する旨を明記）を新設した。4件の path は `run9_schema.py` の bridge 側
唯一の正本 `_REVISION_BRIDGE_EXPECTED_METRIC_REF` から、supersede-
calibration 対象4エントリ名（`_REVISION_BRIDGE_SUPERSEDED_CALIBRATION_
ENTRIES`）についてのみ**導出**した新規定数
`_IDENTITY_PROTOCOL_PRESERVED_GENERATION_DEFINITIONS`（single source of
truth — bridge 側の凍結表と手で二重に書き起こさない）を正として、
`validate_identity_decision_protocol()` の閉じた集合検査（`_validate_
identity_protocol_metric_ref_list()` 再利用）+ note のマーカー検査
（`_validate_marker_bearing_str()` 再利用、マーカー `"supersede"`/
`"生成定義"`）で強制する。`load_pinned_identity_decision_protocol()`
cross-check (8)（旧 (13)）の走査ループへ `preserved_generation_
definitions` の各 dotted path を追加し、`identity_metric_space.json` に
実在することも走査する（既存 `preserved_sections`/`superseded_sections`
走査と同型）。

さらに `_validate_revision_bridge_entry()`（`evaluation/probe_manifest.
json` 側）へ cross-document 閉包検査を追加した: 各 superseded-
calibration エントリの `identity_metric_space_ref`（実データの値、
Python 定数ではない）が、実際に読み込んだ `identity_decision_protocol_
v0.6.json` 文書（`_load_identity_decision_protocol_document()` 経由）の
`supersede_declaration.preserved_generation_definitions` に含まれる
ことを fail-closed で強制する。これにより、将来 bridge 側に新たな
superseded-calibration エントリが増えた場合（`_REVISION_BRIDGE_
SUPERSEDED_CALIBRATION_ENTRIES`/`_REVISION_BRIDGE_EXPECTED_METRIC_REF`
の更新）、`_IDENTITY_PROTOCOL_PRESERVED_GENERATION_DEFINITIONS`
（Python 定数側、bridge から自動導出）と protocol JSON 側の宣言の両方が
連動して更新を強制される二重防御になる——Python 定数の更新だけでは
実 JSON ファイルは変わらないため、この cross-document 検査が実際の
乖離を検出する主体である。`evaluation/probe_manifest.json`/`inputs/
measurement_spec_manifest.json` 自体は本巡では無改変（bridge note の
文言は「参照先 path としては supersede 済みの節を指す」という第2巡の
表現のままで新構造と矛盾しないため、最小是正すら不要と判定した）。

**repin cascade**: `inputs/identity_decision_protocol_v0.6.json` バイト
変更に伴い `hypothesis_algebra_sha` を repin
（旧 `cde8b003ff88b78693c81058e3a80ec4fbfe546df7e3f8e61812c8d6f61c67c1` →
新 `7525cd5ef484bfd94a234f25b44a48368d2f1607f334de1b868863c1bd133f4a`）。
`evaluation/probe_manifest.json`/`inputs/measurement_spec_manifest.json`
は無改変のため `probe_manifest_sha`/`measurement_spec_sha` の repin は
発生しない。

### 6.2 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2614 passed, 8 warnings in ~43s
```

新設テスト10件（2604→2614）: `tests/test_run9_contract.py` に7件
（`preserved_generation_definitions` の bridge 導出一致確認・閉じた集合
検査の過剰/不足エントリ拒否・キー欠落拒否・note マーカー欠落/空文字
拒否・loader typo 走査）、`tests/test_run9_probe_manifest.py` に3件
（実データでの4エントリ閉包検査通過確認・`IDENTITY_DECISION_PROTOCOL_
PATH` 差し替えによる列挙外 path 拒否の合成 fixture・列挙キー自体の欠落
拒否）。

### 6.3 変更ファイル

- `inputs/identity_decision_protocol_v0.6.json`:
  `supersede_declaration` へ `preserved_generation_definitions`（4件）+
  `preserved_generation_definitions_note` 新設。
- `run9_schema.py`: `_IDENTITY_PROTOCOL_PRESERVED_GENERATION_
  DEFINITIONS`（bridge 側凍結表から導出）/`_IDENTITY_PROTOCOL_
  PRESERVED_GENERATION_DEFINITIONS_NOTE_MARKERS` 新設定数、
  `validate_identity_decision_protocol()` の `supersede_declaration`
  検証拡張（新2キーの閉じた集合検査 + note マーカー検査）、
  `load_pinned_identity_decision_protocol()` cross-check 走査へ
  `preserved_generation_definitions` 追加、`_validate_revision_bridge_
  entry()` へ cross-document 閉包検査（bridge の `identity_metric_
  space_ref` が protocol 側の `preserved_generation_definitions` に
  含まれることの fail-closed 強制）を追加。
- `RUN9_CONTRACT.yaml`: `hypothesis_algebra_sha` repin（値・repin履歴
  コメント追加）。
- `tests/test_run9_contract.py`: 上記の回帰テスト・repin 値更新・新規
  fail-closed テスト7件追加。
- `tests/test_run9_probe_manifest.py`: 閉包検査の回帰・fail-closed
  テスト3件追加。

### 6.4 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分（`USER_ADJUDICATION_
  20260827_IDENTITY_REV06.txt`）は 1 byte も変更していない（`git status
  --short` で確認済み）。
- `evaluation/probe_manifest.json`/`inputs/measurement_spec_manifest.
  json` は Fable 設計メモが想定した「bridge note の文言が『節ごと
  supersede』前提で書かれていて新構造と矛盾する場合のみ最小是正」の
  条件に該当しないと判定し、無改変とした——既存 `superseded_calibration_
  note` の文言（「識別子（specific dotted path）レベルで supersede
  済み」という表現）は、本改訂後も「判定規則としては supersede 済み」
  という意味で読める限り新構造と矛盾しない。
- `_IDENTITY_PROTOCOL_PRESERVED_GENERATION_DEFINITIONS` を bridge 側の
  凍結表（`_REVISION_BRIDGE_EXPECTED_METRIC_REF` +
  `_REVISION_BRIDGE_SUPERSEDED_CALIBRATION_ENTRIES`）から直接導出する
  設計とした（ハードコードされた別リストとして二重に書き起こさない）。
  これにより Python 定数同士の乖離は構造的に発生しないが、実 JSON
  ファイル（protocol 側の宣言）との乖離は自動では防げないため、
  `_validate_revision_bridge_entry()` の cross-document 閉包検査を
  別途追加し、実データレベルでも二重防御とした——設計メモの「閉包検査」
  指示を、Python 定数間の構造的保証 + 実文書間の実行時検査の2段構えで
  実装した設計判断。
