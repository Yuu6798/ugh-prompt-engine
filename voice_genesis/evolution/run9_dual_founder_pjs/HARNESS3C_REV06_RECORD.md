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
