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
PR #333 Codex bot レビュー対応（本記録 §4 以降、巡ごとに節を追加する
運用）を経て、`ruff check .` clean・`pytest` は run9 サブツリー全件
pass を維持している。**

**本節はテスト件数・巡数のリテラル値を主張しない（PR #333 第8巡指摘2、
P2、採用、本記録 §11.2 で構造変更）。最新の検証結果は、本記録内で
最後に追加された『PR #333 Codex bot レビュー第N巡対応』節（ファイル
末尾に最も近い巡セクション）の「検証結果」小節（`pytest ... passed`
の実行ログ）を正とする**——巡が進むたびに新設テストが追加され続ける
ため、本節にリテラルの件数・巡数を書くと次巡の追加のたびに再び stale
化する。実際に第7巡指摘1（本記録 §10.1）は当時の最新値「2681件」へ
本節を更新したが、その更新元となった第7巡自身が新設した5件のテスト
（2681→2686）が本節へ反映されないまま残り、最新化した直後にまた
§10.3 の実測値と矛盾するという再帰問題が発生していた（本節の更新が
常にその巡自身の変更より1手遅れる構造的な欠陥）。本改訂はテスト件数
を本節から完全に排除しポインタへ置き換えることで、この再帰問題を
構造的に終端する——以後、巡が追加されても本節自体の更新は不要になる。

| # | 成果物 | 状態 |
|---|---|---|
| 1 | `DESIGN_RUN9_REVISION_0.6.md` | 新規作成・完了 |
| 2 | `USER_ADJUDICATION_20260827_IDENTITY_REV06.txt` | 新規作成・裁定逐語 byte 一致確認済み |
| 3 | `inputs/identity_decision_protocol_v0.6.json` | 新規作成・§P 構造どおり（巡ごとの対応で追補・repin 継続、最新状態は本記録内で最後に追加された巡セクションを正とする） |
| 4 | `run9_schema.py`: validate/load + outcome_detail 定数 | 新規実装・テスト green（巡ごとの対応で追補） |
| 5 | `RUN9_CONTRACT.yaml`: hypothesis_algebra_sha PINNED + design_revision 0.6 昇格 | 完了（`hypothesis_algebra_sha` は複数回 repin、最新値は本記録内で最後に追加された巡セクションの repin cascade 小節を正とする——ポインタ化、第8巡指摘2） |
| 6 | probe bridge / failure routing 更新 | probe_manifest: 実装前グラウンディング時点では不要と判定したが第2巡指摘1（§5.1）で bridge 参照是正に伴い repin 済み / failure_abort_criteria: rule 7/16 是正・repin（11世代目、以後未変更） |
| 7 | `HARNESS3C_REV06_RECORD.md`（本ファイル） | 本ファイル |

**初版時点（design_revision 0.6 第2 PR フェーズ1、レビュー対応着手前）
の記述**（履歴保持、上記が現行正）: 「`pytest` は run9 サブツリー
2584 件全 pass（新設 26 件を含む）。実装前グラウンディングで
probe_manifest.json の repin は不要と判定（該当する正典矛盾なし）」。

**第7巡是正時点（本記録 §10.1、レビュー対応着手後の中間状態）の記述**
（履歴保持、上記ポインタ形式が現行正）: 「`pytest` は run9 サブツリー
2681 件全 pass」——この数値は第7巡自身が追加した5件のテストを含めて
おらず、記述直後から stale だった（実際の第7巡終了時点の値は §10.3 の
2686 件）。この矛盾こそが本改訂（第8巡指摘2）でポインタ形式へ構造変更
した直接の理由である。

実装前グラウンディング時点（第0巡）では probe_manifest.json の repin
は不要と判定していたが、**第2巡指摘1（§5.1）の bridge 参照是正により
probe_manifest.json はその後 repin 済み**（`probe_manifest_sha`
旧→新、詳細は §5.1）。failure_abort_criteria.json は実装前グラウン
ディングの時点で Birth Gate 関連 2 rule の stale 文言を是正し repin
した（この repin は第1巡以降の対応と独立、以後未変更）。

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

> **第0巡時点の判定であることの注記**（第7巡指摘1対応で追記）:
> 上記「repin は不要」は本節が扱う `hypothesis_algebra_sha`
> 非出現の観点に限った着手前グラウンディングの判定であり、その後
> 第2巡指摘1（§5.1）で発見された別軸の欠陥（revision_bridge の
> `identity_metric_space_ref` が supersede 済み節を指したまま）への
> 対応で `probe_manifest.json` は repin 済みである。最終状態は §5.1
> および総合判定を正とする。

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

## 7. PR #333 Codex bot レビュー第4巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`d8dfd704`）。2件全て採用（P1×2）。裁定正本は本 PR の対象外（既存裁定
§1-§9 の再解釈ではなく、実装欠陥の是正）。両件とも Fable が事前に採否・
設計を確定し、着手前の事実確認で指摘内容が実物と一致することを確認して
から実装した。

### 7.1 指摘1（P1）: 「Require every birth check before declaring
ESTABLISHED」

**事実確認**: `inputs/identity_decision_protocol_v0.6.json` の
`birth_identity_separation` の `established` 分岐（d12>0 →
ESTABLISHED）と `pjs_confuser` の `on_zero` 分岐（PJS confuser 距離=0 →
NOT_ESTABLISHED）が、それぞれ独立した節として定義されており、両者の
合成条件・優先順を定義する節が存在しないことを確認した（`validate_
identity_decision_protocol()` の実装・実データともに、この2節を横断して
束ねる検証は第3巡時点まで存在しなかった）。

**実装（Fable 設計）**: 新規節 `birth_gate_aggregate_rule` を発行し、
Birth Gate 全体の ESTABLISHED 判定を単一の連言として凍結した:

- **必要十分条件**: 「両 founder の feature が valid/finite」∧
  「d12 > 0」∧「両 founder の PJS confuser 距離 > 0」の連言
  （`necessary_and_sufficient_condition_for_established`）。裁定§4/§5は
  独立の門ではなく Birth Gate の連言構成要素であり、`verbatim_basis`
  （裁定§5 逐語「distance=0の場合はPJS confuserとのfeature collapseとして
  BIRTH NOT_ESTABLISHEDとする。」）を `pjs_confuser.verbatim` と単一の
  正本として共有することで、新規則の発明ではなく既存裁定の機械符号化
  であることを構造的に強制した（`verbatim_basis != pjs_confuser.
  verbatim` は validator が fail-closed で拒否する）。
- **不成立時の outcome_detail 記録**: `outcome_detail_priority` に
  決定論的優先順（`order`: `invalid_or_nonfinite_feature` →
  `d12_zero_collapse` → `pjs_confuser_zero_distance`、順序込み固定
  タプル）を凍結し、`detail_by_key` で各優先枝が指す outcome_detail
  ラベルを既存/新設の凍結定数へ紐付けた——`invalid_or_nonfinite_feature`
  は既存 `IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL`、
  `d12_zero_collapse` は既存 `IDENTITY_PROTOCOL_BIRTH_COLLAPSE_DETAIL`
  を再利用し、`pjs_confuser_zero_distance` のみ新規定数
  `IDENTITY_PROTOCOL_BIRTH_PJS_CONFUSER_COLLAPSE_DETAIL` を追加した
  （`pjs_confuser.on_zero` 自体は無改変——本定数は `birth_gate_aggregate_
  rule` 側だけが保持し、既存節を書き換えない設計とした）。`order_note`
  に、複数条件が同時該当し得ること・優先順の先頭1件を主 detail とし
  該当した全条件キーを `outcome_detail_all_applicable_keys` へ機械可読
  リストで併記する旨（情報を落とさない）を明記した。
- **§9 接続**: `gate_failure_action_ref` を `invariants.birth_gate_
  failure_action` への自己参照 dotted path として凍結し、Birth Gate
  不成立時の凍結（§9）へ接続した。
- **既存分岐との相互参照**: `conjunct_refs`（順序込み4項目固定タプル
  `birth_identity_separation.established` /
  `birth_identity_separation.invalid_or_nonfinite_feature` /
  `pjs_confuser.on_positive` / `pjs_confuser.on_zero`）で既存4分岐を
  参照するのみ——`birth_identity_separation`/`pjs_confuser` 両節は
  1 byte も変更していない（`test_pr333_r4_aggregate_rule_pjs_confuser_
  section_byte_unchanged` で回帰確認）。

### 7.2 指摘2（P1）: 「Route a failed positive-reference audit to a
stop」

**事実確認**: `positive_reference_audit` 節が要求（WAV byte 一致 +
distance=0）のみを規定し、不一致時の outcome/stop action を持たない
ことを確認した。同時に `c0_determinism_attestation` には `on_mismatch`
（render byte 不一致 → `DETERMINISM_CONTRACT_BROKEN` / feature 計算
不一致 → `IMPLEMENTATION_FAILURE`）、`c1_sham_attestation` には
`on_nonzero`（→ `C1_SHAM_EFFECT_DETECTED`）という停止語彙割当てが既に
存在しており、`positive_reference_audit` だけがこの割当てを持たない
未登録の穴であることを確認した。

**実装（Fable 設計）**: `positive_reference_audit` へ `on_mismatch` を
新設し、C0 と同型の決定論的停止割当てを凍結した——WAV バイト不一致は
`wav_byte_mismatch`（値は既存定数 `IDENTITY_PROTOCOL_C0_RENDER_MISMATCH_
OUTCOME` = `DETERMINISM_CONTRACT_BROKEN` をそのまま再利用）、バイト
一致だが distance≠0 または feature 計算不一致は
`distance_nonzero_or_feature_mismatch_with_matching_wav`（値は既存定数
`IDENTITY_PROTOCOL_C0_FEATURE_MISMATCH_OUTCOME` = `IMPLEMENTATION_
FAILURE` をそのまま再利用）とし、新語彙は一切発明していない
（`test_pr333_r4_positive_reference_audit_on_mismatch_reuses_c0_
vocabulary` で C0 側の実データ値との一致を直接確認）。`gate_effect` へ
「いずれの outcome も Birth Gate 非 PASS・学習非進行・閾値救済禁止
（裁定§9）。C0側 `c0_determinism_attestation.on_mismatch` と同一の
割当て規則である」旨を明記し、裁定§3『positive referenceは追加のexact
replay監査として維持する』+ §9 fail-closed の機械符号化であることを
文書化した。`c0_determinism_attestation`/`c1_sham_attestation` 両節は
1 byte も変更していない（`test_pr333_r4_c0_c1_sections_byte_unchanged_
by_positive_reference_fix` で回帰確認）。

### 7.3 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2638 passed, 7 warnings in ~44s
```

新設テスト24件（2614→2638）: `tests/test_run9_contract.py` へ
`pr333_r4_*` prefix で追加——`birth_gate_aggregate_rule` の happy path・
既存語彙再利用確認・verbatim_basis 単一正本確認・欠落キー拒否・
verbatim_basis 改ざん拒否・established/not_established の outcome_detail
改ざん拒否・`conjunct_refs` 並び替え/過剰エントリ/dict 偽装拒否・
`outcome_detail_priority.order`/`detail_by_key` 改ざん/未登録キー拒否・
`gate_failure_action_ref` 改ざん拒否・3ラベルの非衝突確認・
`pjs_confuser` 節無改変確認・`positive_reference_audit.on_mismatch`
の C0 語彙再利用確認・欠落キー拒否・両フィールド値改ざん拒否・サブキー
欠落拒否・空文字拒否・`c0_determinism_attestation`/`c1_sham_attestation`
節無改変確認・`load_pinned_identity_decision_protocol()` happy path
（新設2フィールドの to-level 到達確認）・改ざん経由 loader fail-closed
確認。

### 7.4 変更ファイル

- `inputs/identity_decision_protocol_v0.6.json`: 新規節
  `birth_gate_aggregate_rule` 追加（指摘1）、`positive_reference_audit`
  へ `on_mismatch` 追加（指摘2）。`birth_identity_separation`/
  `pjs_confuser`/`c0_determinism_attestation`/`c1_sham_attestation` の
  既存4節は無改変。
- `run9_schema.py`: 新規定数 `IDENTITY_PROTOCOL_BIRTH_PJS_CONFUSER_
  COLLAPSE_DETAIL`・`_IDENTITY_PROTOCOL_BIRTH_GATE_CONJUNCT_REFS`・
  `_IDENTITY_PROTOCOL_BIRTH_GATE_PRIORITY_ORDER`・`_IDENTITY_PROTOCOL_
  BIRTH_GATE_DETAIL_BY_KEY`・`_IDENTITY_PROTOCOL_BIRTH_GATE_FAILURE_
  ACTION_REF`、`_IDENTITY_DECISION_PROTOCOL_TOP_LEVEL_KEYS` へ
  `birth_gate_aggregate_rule` 追加、`validate_identity_decision_
  protocol()` へ `birth_gate_aggregate_rule` の構造検証ブロック（既存
  `pjs_confuser` 検証結果の `pjs` 変数を再利用した `verbatim_basis`
  cross-check 含む）+ `positive_reference_audit.on_mismatch` の検証
  拡張。
- `RUN9_CONTRACT.yaml`: `hypothesis_algebra_sha` repin（値・repin履歴
  コメント追加）。
- `tests/test_run9_contract.py`: `hypothesis_algebra_sha` repin 値の
  回帰更新、新規 fail-closed/確認テスト24件追加。

### 7.5 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分（`USER_ADJUDICATION_
  20260827_IDENTITY_REV06.txt`）は 1 byte も変更していない
  （`git status --short` で確認済み）。
- 指摘1の実装にあたり `pjs_confuser.on_zero` へ `outcome_detail` を
  追加する案も検討したが、既存節への追記は「文言変更は矛盾解消に必要な
  最小限のみ」という Fable 設計方針に照らして不要（`birth_gate_
  aggregate_rule` 側だけで新規定数を保持すれば矛盾なく合成条件を表現
  できる）と判断し、`pjs_confuser` 節は 1 byte も変更しない設計とした
  （既存4分岐すべて無改変）。
- `outcome_detail_priority` の「複数該当時に全項目を機械可読リストで
  併記する」という指示は、本 PR が Birth Gate 実測を一切含まない事前
  登録フェーズ（裁定§8 実行順どおり）であるため、実行時の
  `outcome_detail_all_applicable_keys` 生成ロジック自体はまだ実装
  対象外——本節はその生成規約（優先順・参照ラベル）を pre-run で凍結
  するところまでを範囲とした（他の全節と同じく「事前登録一式」の一部）。
- `birth_gate_aggregate_rule`/`positive_reference_audit.on_mismatch`
  いずれも既存 `BIRTH_OUTCOMES`/`FAILURE_CLASSES` frozen tuple への
  値追加は行っていない（既存語彙の再利用 + `IDENTITY_PROTOCOL_*` detail
  層への新設のみ、という二層構造の設計方針を維持）。

## 8. PR #333 Codex bot レビュー第5巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`68bb1872`）。3件全て採用（P1×3）。裁定正本は本 PR の対象外（既存裁定
§1-§9 の再解釈ではなく、実装欠陥の是正）。指摘1・3 は第4巡実装
（本記録 §7）が導入した欠陥の是正であり、その経緯を本節で正直に記録
する。3件とも Fable が事前に採否・設計を確定し、着手前の事実確認で
指摘内容が実物と一致することを確認してから実装した。

### 8.1 指摘1（P1）: 「Conjoin predicates rather than mutually exclusive
branches」

**事実確認**: 第4巡（本記録 §7.1）で追加した `birth_gate_aggregate_rule.
conjunct_refs` が `birth_identity_separation.established` と
`invalid_or_nonfinite_feature`、`pjs_confuser.on_positive` と `on_zero`
という**排他ペア両方**を含んでおり、「conjunct_refs 全項が成立」という
連言条件が要求する4項目は数学的に同時成立不可能（`established` と
`invalid_or_nonfinite_feature` は互いに否定の関係、`on_positive` と
`on_zero` も同様）であることを確認した。文字通りの消費者（protocol
JSON の宣言だけを読んで判定を実装するコード）は、この連言が常に偽と
なるため決して BIRTH=ESTABLISHED に到達できず、毎回 NOT_ESTABLISHED へ
凍結される——**第4巡実装自体の欠陥**であり、第4巡の Completion Summary
（本記録 §7.1）が「新規則の発明ではない」と記述した設計意図（Birth Gate
全体の ESTABLISHED 連言条件を凍結する）を実装が正しく符号化できていな
かった。

**実装（Fable 設計）**: `conjunct_refs` を成功述語のみ2項目
（`birth_identity_separation.established` / `pjs_confuser.on_positive`）
へ限定し直した。失敗分岐（`birth_identity_separation.invalid_or_
nonfinite_feature` / `birth_identity_separation.not_established` /
`pjs_confuser.on_zero`）は新設 `not_established.outcome_detail_priority.
failure_refs`（3項目、`order` と同順）へ分離し、conjunct_refs から完全
に除外した——`conjunct_refs ∩ failure_refs = ∅` を直接検証するテスト
（`test_pr333_r5_conjunct_refs_and_failure_refs_disjoint_no_exclusive_
pair`）で回帰確認する。`birth_identity_separation`/`pjs_confuser` 既存
節は1 byte も変更していない（第4巡と同型の無改変方針を維持、`test_
pr333_r5_aggregate_rule_existing_branches_byte_unchanged` で確認）。

`note` フィールドへ、第4巡実装が排他ペア欠陥を含んでいたこと・本巡が
それを発見・是正したことを明記した（是正の透明性——CLAUDE.md 運用細則
「事後監査」の精神に沿い、成果物自体に経緯を残す）。

### 8.2 指摘2（P1）: 「Route C1 byte-only mismatches to a stop」

**事実確認**: `c1_sham_attestation` が `on_nonzero`（D_C1(F)≠0 全体、
`C1_SHAM_EFFECT_DETECTED` へ停止）のみを持ち、`c0_determinism_
attestation`/`positive_reference_audit` が明示的に区別している
「WAV バイトは不一致だが identity feature は一致（byte-level のみの
決定論破り）」という具体的経路に対して、C1 側だけ専用の分岐が存在しな
いことを確認した。

**実装（Fable 設計）**: `c1_sham_attestation` へ `on_wav_byte_mismatch`
を新設した——「C1 render の WAV bytes が C0/reference と不一致（identity
feature が一致し D_C1(F)=0 であっても）→ `DETERMINISM_CONTRACT_BROKEN`
（既存定数 `IDENTITY_PROTOCOL_C0_RENDER_MISMATCH_OUTCOME` の再利用、
新語彙の発明はしない）。Birth Gate 非 PASS・学習非進行」。
`cross_reference` フィールドで `c0_determinism_attestation.on_mismatch.
render_byte_mismatch`・`positive_reference_audit.on_mismatch.wav_byte_
mismatch` と同一の byte-mismatch routing 規則であることを明記した
（`test_pr333_r5_c1_on_wav_byte_mismatch_reuses_c0_vocabulary` で実データ
一致確認）。既存 `on_nonzero`（`C1_SHAM_EFFECT_DETECTED`）は無改変
（`test_pr333_r5_c1_on_nonzero_byte_unchanged`）。

### 8.3 指摘3（P1）: 「Include exact-replay audits in the aggregate
gate」

**事実確認**: `birth_gate_aggregate_rule.necessary_and_sufficient_
condition_for_established` が validity/d12/PJS confuser のみを規定し、
C0/C1/positive reference の exact-replay 監査（`on_mismatch`/
`on_nonzero`/`on_wav_byte_mismatch`）を一切含んでいないことを確認した。
このため、監査失敗（例: `DETERMINISM_CONTRACT_BROKEN`）と
BIRTH=ESTABLISHED が同時成立し得る——第4巡実装が導入した
`birth_gate_aggregate_rule` は Birth Gate の一部（identity_
establishment 層）のみを規定しており、Birth Gate 全体の PASS 判定
（裁定§8『rev 0.6のBirth GateがPASSした場合のみ、learning recipe
freezeおよび学習実行へ進む。』）を機械符号化していなかった。

**実装（Fable 設計 — 二層分離）**: Birth Gate 判定を2層に再構成した:

- **identity_establishment**（既存 `birth_gate_aggregate_rule`、無改変
  のまま維持——ただし指摘1 の是正を含む）: BIRTH ラベル
  （ESTABLISHED / NOT_ESTABLISHED）を決める層。`necessary_and_
  sufficient_condition_for_established` の「必要十分」はこの層限定
  であることを新設 `identity_establishment_scope_note` で明記した。
- **birth_gate_overall_pass**（新設節）: 「Birth Gate 全体の PASS ⇔
  identity_establishment = ESTABLISHED ∧ C0/C1/positive reference の
  各 attestation・audit（`audit_stop_refs` 列挙の4節: `c0_determinism_
  attestation.on_mismatch` / `c1_sham_attestation.on_nonzero` /
  `c1_sham_attestation.on_wav_byte_mismatch` / `positive_reference_
  audit.on_mismatch`）がいずれの停止条件にも該当しない」。`verbatim_
  basis` は `execution_order.gate_sequencing`（裁定§8 逐語、既存フィー
  ルドと単一正本を共有——二重に書き起こさない）と byte-identical
  であることを validator が cross-check する。`pass_gates_learning` で
  「PASS のみが learning recipe freeze / 学習実行へ進む条件」を、
  `audit_failure_does_not_invalidate_established` で「監査失敗時は
  該当停止語彙で停止し ESTABLISHED 判定そのものは無効化しない（identity
  判定と実装健全性判定の会計分離）——ただし gate は非 PASS」を明記した。

### 8.4 敵対的自己検査（Task 指示必須）

protocol JSON だけを入力に「文字通りの消費者」を演じるシミュレーション
（`_literal_consumer_birth_gate()`、`tests/test_run9_contract.py` へ
テストコードとして実装・収載）を実装後・報告前に実行し、以下4系を機械
検証した——いずれも矛盾なし:

- **(a) 全成功ケース**: `feature_valid=True, d12_positive=True,
  pjs_distance_positive=True`、監査失敗なし → `birth_outcome ==
  "ESTABLISHED"` ∧ `overall_pass is True`
  （`test_pr333_r5_adversarial_literal_consumer_all_success_
  establishes_and_passes`）。
- **(b) PJS 距離 0**: 他は全て成功、`pjs_distance_positive=False` →
  `birth_outcome == "NOT_ESTABLISHED"` ∧ `overall_pass is False`
  （`test_pr333_r5_adversarial_literal_consumer_pjs_zero_distance_not_
  established`）。
- **(c) C1 バイトのみ不一致**: identity_establishment 側は全て成功、
  `c1_wav_byte_mismatch=True` → `birth_outcome == "ESTABLISHED"`
  （ESTABLISHED 判定は維持）∧ `overall_pass is False`（監査停止で
  非PASS）∧ 実データの `c1_sham_attestation.on_wav_byte_mismatch.
  outcome == "DETERMINISM_CONTRACT_BROKEN"`
  （`test_pr333_r5_adversarial_literal_consumer_c1_byte_only_mismatch_
  broken_but_established`）。
- **(d) 排他ペア非存在**: `conjunct_refs` の全参照（成功述語2項目）が
  全成功ワールドで同時に True を返すこと、かつ `conjunct_refs ∩
  failure_refs = ∅` であることを直接検証
  （`test_pr333_r5_adversarial_literal_consumer_no_exclusive_pair_all_
  conjuncts_satisfiable` / `test_pr333_r5_conjunct_refs_and_failure_
  refs_disjoint_no_exclusive_pair`）。

矛盾は検出されなかった（実装のやり直しは発生していない）。

### 8.5 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2674 passed, 7 warnings in ~42s
```

新設テスト36件（2638→2674）: `tests/test_run9_contract.py` へ
`pr333_r5_*` prefix で追加——`conjunct_refs` 是正の happy path・成功
述語2項目のみへの限定確認・排他ペア非存在確認・`failure_refs` 新設の
frozen tuple 一致/欠落キー/並び替え/過剰エントリ/dict 偽装拒否・旧
排他ペア4項目の復元回帰拒否・`identity_establishment_scope_note` 欠落
/空文字拒否・既存節無改変確認・`c1_sham_attestation.on_wav_byte_
mismatch` の C0/positive 語彙再利用確認・欠落キー/誤値/サブキー欠落
/空文字拒否・既存 `on_nonzero` 無改変確認・`birth_gate_overall_pass`
の `identity_establishment_ref` 一致確認・`verbatim_basis` 単一正本
確認・`audit_stop_refs` frozen tuple 一致/欠落/誤値/並び替え/過剰
エントリ/dict 偽装拒否・欠落サブキー/空文字拒否・`load_pinned_
identity_decision_protocol()` happy path（新設3フィールドの到達確認）
・改ざん経由 loader fail-closed 確認・敵対的自己検査4系（§8.4）。

### 8.6 変更ファイル

- `inputs/identity_decision_protocol_v0.6.json`:
  `birth_gate_aggregate_rule.conjunct_refs` を4項目（排他ペア2組）から
  成功述語2項目へ是正（指摘1）、`not_established.outcome_detail_
  priority.failure_refs` 新設（指摘1、失敗分岐参照の分離先）、
  `identity_establishment_scope_note` 新設（指摘3、必要十分の層限定
  明記）、`c1_sham_attestation.on_wav_byte_mismatch` 新設（指摘2）、
  新規節 `birth_gate_overall_pass` 追加（指摘3）。既存
  `birth_identity_separation`/`pjs_confuser`/`c0_determinism_
  attestation`/`c1_sham_attestation.on_nonzero`/`positive_reference_
  audit` の既存分岐は無改変。
- `run9_schema.py`: 新規定数 `_IDENTITY_PROTOCOL_BIRTH_GATE_FAILURE_
  REFS`・`_IDENTITY_PROTOCOL_OVERALL_PASS_IDENTITY_ESTABLISHMENT_REF`・
  `_IDENTITY_PROTOCOL_OVERALL_PASS_AUDIT_STOP_REFS`、
  `_IDENTITY_PROTOCOL_BIRTH_GATE_CONJUNCT_REFS` を4項目→2項目へ改訂、
  `_IDENTITY_DECISION_PROTOCOL_TOP_LEVEL_KEYS` へ `birth_gate_overall_
  pass` 追加、`validate_identity_decision_protocol()` へ
  `c1_sham_attestation.on_wav_byte_mismatch`・`birth_gate_aggregate_
  rule.identity_establishment_scope_note`・`outcome_detail_priority.
  failure_refs`・`birth_gate_overall_pass` 全体の構造検証ブロックを
  追加。
- `RUN9_CONTRACT.yaml`: `hypothesis_algebra_sha` repin（値・repin履歴
  コメント・reason 追記）。
- `tests/test_run9_contract.py`: `hypothesis_algebra_sha` repin 値の
  回帰更新、新規 fail-closed/確認/敵対的自己検査テスト36件追加。

### 8.7 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分（`USER_ADJUDICATION_
  20260827_IDENTITY_REV06.txt`）は1 byte も変更していない（`git status
  --short` で確認済み）。
- 指摘1・3 はいずれも第4巡実装（本記録 §7）が導入した欠陥の是正であり、
  裁定 §1-§9 そのものの再解釈は行っていない——本節 §8.1/§8.3 に経緯を
  正直に記録した（第4巡の設計意図は正しかったが実装が意図を正しく
  符号化できていなかった、という区別）。
- `birth_gate_overall_pass` の `audit_stop_refs` は C0/C1/positive
  reference の4節のみを列挙し、`birth_identity_separation`/
  `pjs_confuser`（= identity_establishment 層の構成要素）は含めていな
  い——両者は identity_establishment 層の判定材料であり、overall PASS
  層が追加で要求する exact-replay 監査とは会計上別カテゴリのため
  （指摘3 の設計方針: 二層は独立の懸念事項を分離する）。
- 本 PR は依然として事前登録フェーズ（裁定§8 実行順どおり、Birth Gate
  の実行自体は含まない）——`birth_gate_overall_pass`/`c1_sham_
  attestation.on_wav_byte_mismatch`/`outcome_detail_priority.failure_
  refs` いずれも判定規約の凍結までを範囲とし、実行時の実測ロジック
  自体は本 PR の対象外。
- `birth_gate_aggregate_rule`/`birth_gate_overall_pass` いずれも既存
  `BIRTH_OUTCOMES`/`FAILURE_CLASSES` frozen tuple への値追加は行って
  いない（既存語彙の再利用のみ、という二層構造の設計方針を維持）。

## 9. PR #333 Codex bot レビュー第6巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`5d9abded`）。1件（P1）採用。裁定正本は本 PR の対象外（既存裁定 §1-§9 の
再解釈ではなく、第5巡（本記録 §8）と同型の未登録分岐の是正）。

### 9.1 指摘1（P1）: 「Reject C1 feature-only mismatches at zero
distance」

**事実確認**: `c1_sham_attestation` が `on_nonzero`（D_C1(F)≠0 全体、
`C1_SHAM_EFFECT_DETECTED` へ停止）と `on_wav_byte_mismatch`（第5巡、
WAV bytes 不一致→`DETERMINISM_CONTRACT_BROKEN`）の2分岐のみを持ち、
「WAV bytes は C0/reference と一致し数値距離 D_C1(F)=0 だが、serialized
identity feature bytes/hash が C0/reference と不一致（dtype/signed-zero/
シリアライズ差等）」という決定論破りの具体的経路のいずれにも発火する
分岐が存在しないことを確認した。`c0_determinism_attestation.on_mismatch`
には対称分岐 `feature_computation_mismatch_with_matching_render` →
`IMPLEMENTATION_FAILURE`（render 一致だが feature 計算不一致）が既存
であるのに対し、C1 側だけがこの経路に未分岐だった——第5巡指摘2
（本記録 §8.2、byte-level 未分岐の是正）と同型だが独立の穴（byte-level
と feature-level は別軸であり、片方の是正がもう片方を自動的に埋めない）。
**指摘内容は事実と一致** → Fable 設計どおり実装。

**実装（Fable 設計）**: `c1_sham_attestation` へ `on_feature_mismatch`
を新設した——「C1 render の WAV bytes が C0/reference と一致し数値距離
D_C1(F)=0 であっても、serialized identity feature bytes/hash が
C0/reference と不一致 → `IMPLEMENTATION_FAILURE`（既存定数
`IDENTITY_PROTOCOL_C0_FEATURE_MISMATCH_OUTCOME` の再利用、新語彙の発明
はしない）。Birth Gate 非 PASS・学習非進行（裁定§9）」。`cross_reference`
フィールドで `c0_determinism_attestation.on_mismatch.feature_
computation_mismatch_with_matching_render` と同一の feature-mismatch
routing 規則であることを明記した（`test_pr333_r6_c1_on_feature_mismatch_
reuses_c0_vocabulary` で実データ一致確認）。既存 `on_nonzero`
（`C1_SHAM_EFFECT_DETECTED`）/`on_wav_byte_mismatch`
（`DETERMINISM_CONTRACT_BROKEN`）は無改変（`test_pr333_r6_c1_on_nonzero_
and_on_wav_byte_mismatch_unchanged`）。`birth_gate_overall_pass.audit_
stop_refs` へ `c1_sham_attestation.on_feature_mismatch` を追加した
（4→5項目、`test_pr333_r5_overall_pass_audit_stop_refs_matches_frozen_
tuple` を5項目へ更新して回帰確認）。

### 9.2 敵対的自己検査（Task 指示必須）

`_literal_consumer_birth_gate()`（§8.4 で実装済みのシミュレーション
関数）へ `c1_feature_mismatch` 引数を追加し、以下1系を新規に機械検証
した——矛盾なし:

- **(e) C1 feature のみ不一致**: WAV bytes は一致・identity_
  establishment 側は全て成功、`c1_feature_mismatch=True` →
  `birth_outcome == "ESTABLISHED"`（ESTABLISHED 判定は維持）∧
  `overall_pass is False`（監査停止で非PASS）∧ 実データの
  `c1_sham_attestation.on_feature_mismatch.outcome ==
  "IMPLEMENTATION_FAILURE"` ∧ 同値が
  `c0_determinism_attestation.on_mismatch.feature_computation_mismatch_
  with_matching_render` と一致（C0 側対称分岐との語彙一致を直接確認）
  （`test_pr333_r6_adversarial_literal_consumer_c1_feature_only_
  mismatch_established_but_not_pass`）。

既存 (a)-(d) の4系（§8.4）も回帰実行し矛盾なし。

### 9.3 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2681 passed, 7 warnings in ~45s
```

新設テスト7件（2674→2681）: `tests/test_run9_contract.py` へ
`pr333_r6_*` prefix で追加——`on_feature_mismatch` の C0 語彙再利用確認・
欠落キー/誤値/サブキー欠落/空文字拒否・既存 `on_nonzero`/`on_wav_byte_
mismatch` 無改変確認・敵対的自己検査1系（§9.2）。

### 9.4 変更ファイル

- `inputs/identity_decision_protocol_v0.6.json`:
  `c1_sham_attestation.on_feature_mismatch` 新設（指摘1）、
  `birth_gate_overall_pass.audit_stop_refs` へ同分岐を追加（4→5項目）、
  同節 `definition` の「4節」表記を「5節」へ更新。既存
  `on_nonzero`/`on_wav_byte_mismatch`/`c0_determinism_attestation`/
  `positive_reference_audit`/`birth_identity_separation`/`pjs_confuser`
  の各既存分岐は無改変。
- `run9_schema.py`: `_IDENTITY_PROTOCOL_OVERALL_PASS_AUDIT_STOP_REFS`
  を4項目→5項目へ改訂（`c1_sham_attestation.on_feature_mismatch` 追加）。
  `validate_identity_decision_protocol()` へ `c1_sham_attestation.on_
  feature_mismatch` の構造検証（`condition`/`outcome`/`gate_effect`/
  `cross_reference`/`note`）を追加。専用の新定数は追加していない
  （既存 `IDENTITY_PROTOCOL_C0_FEATURE_MISMATCH_OUTCOME` の再配線のみ）。
- `RUN9_CONTRACT.yaml`: `hypothesis_algebra_sha` repin（値・repin履歴
  コメント・reason 追記）。
- `tests/test_run9_contract.py`: `hypothesis_algebra_sha` repin 値の
  回帰更新、`_literal_consumer_birth_gate()` へ `c1_feature_mismatch`
  引数追加、`audit_stop_refs` frozen tuple 一致テストを5項目へ更新、
  新規 fail-closed/確認/敵対的自己検査テスト7件追加。

### 9.5 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分（`USER_ADJUDICATION_
  20260827_IDENTITY_REV06.txt`）は1 byte も変更していない（`git status
  --short` で確認済み）。
- 本指摘は第5巡指摘2（本記録 §8.2）と同型だが独立の穴の是正であり、
  裁定 §1-§9 そのものの再解釈は行っていない——byte-level（WAV bytes）と
  feature-level（serialized identity feature bytes/hash）は別軸の
  決定論破りであり、片方の是正がもう片方を自動的に埋めないことを本節
  で明記した。

---

## 10. PR #333 Codex bot レビュー第7巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`3a61a62e`）。2件（P2 × 2）着手前に事実確認、両件とも事実と一致した
ため採用対応。裁定正本は本 PR の対象外（既存裁定 §1-§9 の再解釈ではなく、
既存節の自己矛盾是正・欠落した cross-check の追加）。

### 10.1 指摘1（P2）: 「Refresh the canonical summary after review
fixes」

**事実確認**: 本記録冒頭の「総合判定」が design_revision 0.6 実装完了
直後（第2 PR フェーズ1、レビュー対応着手前）の状態のまま——「2584 件
全 pass」「probe_manifest.json の repin は不要と判定」——で固定されて
おり、その後の第2巡指摘1（§5.1、bridge 参照是正に伴う probe_manifest.
json repin）・第6巡 §9.3（最終 2681 件 pass）と自己矛盾していることを
確認した。**指摘内容は事実と一致** → Fable 設計どおり実装。

**実装**: 「総合判定」節を最終状態（テスト件数・probe_manifest repin
実績・巡数）へ更新した。旧記述は削除せず、「初版時点の記述」注記付きで
節末に履歴保持した（本記録 §総合判定）。§0.4（実装前グラウンディングの
probe_manifest 判定）にも、その判定が `hypothesis_algebra_sha`
非出現の観点に限った着手前時点のものであり、最終状態は §5.1 が正である
ことを示す注記ブロックを追加した。record 全体を走査し、他に総合判定と
矛盾する見出し・サマリー文言が無いことを確認した（各巡セクション §4-§9
はいずれもその巡の時点の状態を記述する時系列ログとして構成されており、
「総合判定」以外に現在形の総括主張を行う箇所は存在しなかった）。

**cascade 確認**: `provenance.detail_record` 経由で本 record を参照する
pin は、5manifest 共通の `_h3c_cross_check_adjudication_and_detail_
record()` の消費対象（`HARNESS3C_AXIS_FEASIBILITY_RECORD.md` 系列）
であり、`HARNESS3C_REV06_RECORD.md` 自身は対象外であることを
`run9_schema.py` のコメント（識別 decision protocol 節冒頭、
「HARNESS3C_REV06_RECORD.md は実装完了後に書かれる記録であり、protocol
側から前方参照すると執筆順序が循環するため `_h3c_cross_check_
adjudication_and_detail_record()` を再利用しない」）で確認した。repo
全体を `HARNESS3C_REV06_RECORD` で grep し、`run9_schema.py`（上記コメント
のみ）と `README.md`（正本ポインタの散文リンクのみ、pin ではない）以外に
本ファイルを参照する箇所がないことも確認した——**本 record のバイト
変更に伴う repin cascade は発生しない**（着手前の見込みどおり）。

### 10.2 指摘2（P2）: 「Validate the declared metric source path」

**事実確認**: `validate_identity_decision_protocol()` の
`metric_reference.source_file` 検証が非空文字列チェックのみ
（`_require_non_empty_str()`）であり、`load_pinned_identity_decision_
protocol()` が実際に読む `identity_metric_space.json` は常に固定定数
`IDENTITY_METRIC_SPACE_PATH` 経由（`_load_identity_metric_space_
document_verified(domain.metric_space_sha)`、cross-check (8)）で、
`metric_reference.source_file` の宣言値自体はどの読み込み経路にも
使われていないことを確認した——宣言 path が誤記・改ざんされても、
`metric_reference.metric_space_sha` さえ `domain.metric_space_sha` と
一致していれば検証を通過してしまう構造的な乖離だった。**指摘内容は
事実と一致** → Fable 設計どおり実装。

**実装（Fable 設計）**: `IDENTITY_METRIC_SPACE_PATH` から repo-relative
表記（既存 `adjudication_basis.source_file`/`provenance.design_
revision_doc.source_file` と同じ posix 形式）を導出する凍結定数
`_IDENTITY_PROTOCOL_METRIC_REFERENCE_EXPECTED_SOURCE_FILE` を新設し、
(a) `validate_identity_decision_protocol()` で `metric_reference.
source_file` の厳密一致を要求（`_require_non_empty_str()` を置換）、
(b) `load_pinned_identity_decision_protocol()` の cross-check (2) を
拡張し、同一の凍結正本を再導出して二層防御で再照合する
（`birth_identity_separation.cell_ref` の validator + loader
cross-check (3) 二層防御と同型）。protocol 実データの現宣言値
（`"voice_genesis/evolution/run9_dual_founder_pjs/inputs/identity_
metric_space.json"`）は是正時点で期待 path と一致しており、**protocol
側の repin は不要**（`test_pr333_r7_metric_reference_source_file_
matches_frozen_expected_constant` で回帰確認）。

**同型点検（他 provenance 欄の source_file）**: `adjudication_basis.
source_file` と `provenance.design_revision_doc.source_file` は、
loader が declared source_file を `_resolve_repo_contained_path()` で
解決し実バイトを read-once で再ハッシュしてから宣言 sha256（さらに
adjudication は自欄、design_revision_doc は contract PINNED 値と）と
突合する構造（cross-check (1)/(6)）——宣言 path そのものが実ハッシュ対象
の決定に使われるため、typo/改ざんは実ファイル不在 or sha256 不一致で
必ず検出される。`metric_reference.source_file` だけが「実ハッシュ対象は
別の固定定数経由、宣言値は非空文字列チェックのみ」という非対称な構造
だった——他の provenance 欄は同型の穴を持たない（**既に閉じている**）
ことを repo 内 `"source_file"` 全出現（3件、protocol JSON 内）を
grep して確認した。

### 10.3 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2686 passed, 7 warnings in ~45s
```

新設テスト5件（2681→2686）: `tests/test_run9_contract.py` へ
`pr333_r7_*` prefix で追加——凍結定数と実データの一致確認・typo 拒否・
実在する無関係ファイルへの差し替え拒否・`load_pinned_identity_decision_
protocol()` 経由の end-to-end 改ざん拒否・loader cross-check の凍結正本
再利用確認。

### 10.4 変更ファイル

- `HARNESS3C_REV06_RECORD.md`: 「総合判定」節を最終状態へ更新（初版時点
  の記述は履歴保持）、§0.4 へ第0巡時点判定であることの注記ブロック追加
  （指摘1）、本節（§10）追加。
- `run9_schema.py`: `_IDENTITY_PROTOCOL_METRIC_REFERENCE_EXPECTED_
  SOURCE_FILE` 定数新設（`_IDENTITY_DECISION_PROTOCOL_REPO_ROOT` 直後）、
  `validate_identity_decision_protocol()` の `metric_reference.
  source_file` 検証を非空文字列チェックから厳密一致チェックへ置換、
  `load_pinned_identity_decision_protocol()` cross-check (2) へ
  `source_file` 再照合を追加（docstring も同期更新）（指摘2）。
- `tests/test_run9_contract.py`: 新規テスト5件追加（指摘2）。
- `inputs/identity_decision_protocol_v0.6.json` / `RUN9_CONTRACT.yaml`:
  **無改変**（宣言値が既に期待 path と一致していたため repin 不要）。

### 10.5 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分（`USER_ADJUDICATION_
  20260827_IDENTITY_REV06.txt`）は1 byte も変更していない。
- `inputs/identity_decision_protocol_v0.6.json` は無改変のため
  `hypothesis_algebra_sha` の repin は発生していない（本巡の変更は
  `run9_schema.py`（検証ロジック）と `HARNESS3C_REV06_RECORD.md`
  （記録の是正）のみ）。
- 本節はコミット・push・GitHub への投稿前の Fable 検分待ちフェーズ1
  実装であり、返信文面は別ファイル（`pr333_round7_replies.md`）に
  起草済み。
- 既存 `BIRTH_OUTCOMES`/`FAILURE_CLASSES` frozen tuple への値追加は
  行っていない（既存語彙の再利用のみ）。`_IDENTITY_PROTOCOL_OVERALL_
  PASS_AUDIT_STOP_REFS` は監査停止参照の列挙という性質上、新設分岐の
  追加に伴い伸長する運用（第5巡で新設・本巡で4→5項目へ伸長）——
  `BIRTH_OUTCOMES`/`FAILURE_CLASSES` のような判定語彙 enum とは異なる
  カテゴリである。
- 本 PR は依然として事前登録フェーズ（裁定§8 実行順どおり、Birth Gate
  の実行自体は含まない）——`on_feature_mismatch` も判定規約の凍結までを
  範囲とし、実行時の実測ロジック自体は本 PR の対象外。

## 11. PR #333 Codex bot レビュー第8巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`567e36b0`）。3件（P2 × 3）着手前に事実確認、3件とも事実と一致した
ため採用対応。裁定正本は本 PR の対象外（既存裁定 §1-§9 の再解釈ではなく、
既存節の未登録分岐・自己矛盾の是正）。加えて、ファミリー全数掃討
（同型の重複可能な述語対・分岐の無い値域）を実施し終端宣言した（§11.4）。

### 11.1 指摘1（P2）: 「Resolve overlapping C1 mismatch outcomes」

**事実確認**: `c1_sham_attestation` が「WAV bytes 不一致かつ D_C1(F)≠0」
の場合、`on_nonzero`（D_C1(F)≠0 全体、条件に byte 一致/不一致の限定
なし）と `on_wav_byte_mismatch`（`condition` 文言が「D_C1(F)=0 であって
も」と明記——D_C1(F)≠0 の場合にも該当する設計）の双方に同時該当し、
優先順・全該当会計が未定義であることを確認した（`on_feature_mismatch`
は `condition` が D_C1(F)=0 を前提とするため `on_nonzero` とは元々排他
——第6巡指摘1〔本記録 §9.1〕で新設した際の設計どおりで、本指摘の対象
外）。**指摘内容は事実と一致** → Fable 設計どおり実装。

**実装（Fable 設計）**: `c1_sham_attestation` へ `outcome_priority`
（`order`/`detail_by_key`/`order_note`）を新設し、決定論的優先順を
凍結した——birth 側 `outcome_detail_priority`（第4巡指摘1・第5巡指摘1・
第8巡指摘3で拡張）と同型パターン: (1) `on_wav_byte_mismatch` — byte
決定論の破りは render 自体が異なるという最上流の物理的原因であり最優先、
(2) `on_feature_mismatch` — 次に feature シリアライズ決定論の破り、
(3) `on_nonzero` — 上記いずれにも該当しない残余としての効果検出。
`detail_by_key` の各値は `c1_sham_attestation` 側で既に検証済みの実際
の `outcome` 値と単一の正本を共有する（二重に書き起こさない設計—— `
c1_sham_attestation.outcome_priority.detail_by_key.{key}` が
`c1_sham_attestation.{key}.outcome` と byte-identical であることを
validator が fail-closed で強制）。主 outcome は `order` 先頭の該当
キーとし、該当した全キーは `outcome_all_applicable_keys` へ機械可読
リストで併記する旨を `order_note` に明記した（情報を落とさない、本 PR
は事前登録フェーズのためその実行時生成ロジック自体は対象外——生成規約
の凍結までを範囲とする）。既存 `on_nonzero`/`on_wav_byte_mismatch`/
`on_feature_mismatch` は無改変
（`test_pr333_r8_c1_on_nonzero_on_wav_byte_mismatch_on_feature_mismatch_
unchanged` で確認）。

**C0 / positive_reference_audit の同型点検**（Task 指示）: `c0_
determinism_attestation.on_mismatch` の `render_byte_mismatch`/`feature_
computation_mismatch_with_matching_render` は、後者の `condition` が
「render 一致」を明示的に前提とするため前者（render 不一致が前提）とは
構造的に排他——重複しない。`positive_reference_audit.on_mismatch` の
`wav_byte_mismatch`/`distance_nonzero_or_feature_mismatch_with_matching_
wav` も同様に、後者が「matching wav」を明示的に前提とするため排他——
重複しない。C1 だけが `on_nonzero` の条件を「D_C1(F)≠0」という byte
レベルを問わない広い条件のまま定義しており、後から追加された
`on_wav_byte_mismatch`（第5巡）・`on_feature_mismatch`（第6巡）の
condition 文言がそれぞれ独立に byte 一致/不一致を明示したことで、
`on_nonzero` との重複が構造的に生じていた——C0/positive_reference_audit
は初回実装時から2分岐が相互に排他な条件文言で設計されていたのに対し、
C1 は3分岐が別々の巡（第4巡原設計→第5巡→第6巡）で段階的に追加された
ため、この非対称性が生じたと判定した。C0/positive_reference_audit へ
の追加是正は不要（詳細は §11.4 のファミリー掃討結果）。

### 11.2 指摘2（P2）: 「Update the summary to the final test count」

**事実確認**: 第7巡是正（本記録 §10.1）後の「総合判定」節が「2681 件
全 pass」の記述のまま固定されており、§10.3 の実測値「2686 件」と
再び矛盾していることを確認した——第7巡指摘1（同じく「総合判定の
陳腐化」を扱った指摘）の是正自体が、その巡が新設した5件のテストを
本節へ反映し損ねたことで即座に再陳腐化するという再帰問題を露呈させて
いた。**指摘内容は事実と一致** → Fable 設計どおり実装（**構造的終端**）。

**実装（Fable 設計）**: 「総合判定」節から**リテラルのテスト件数・
巡数の現在形主張を排除**し、「最新の検証結果は本記録内で最後に追加
された『PR #333 Codex bot レビュー第N巡対応』節（ファイル末尾に最も
近い巡セクション）の「検証結果」小節を正とする」というポインタ形式へ
書き換えた（本記録冒頭「総合判定」節参照）。特定の節番号（例: 「§10」）
を直接名指す形式は避けた——節番号自体も巡が増えるたびに変わるため、
「ファイル末尾に最も近い巡セクション」という位置関係の記述にすることで、
今後何巡追加されても本節の見出し・文面が stale 化しない構造とした。
併せて成果物テーブルの5行目（`hypothesis_algebra_sha` の最新値ポインタ、
旧「§9 参照」という特定節番号の直接参照）も同型のポインタ形式へ是正
した——同じ再帰問題を抱えていたため（本節の対象を指摘2の文言どおり
「総合判定」節に限定しつつ、同一節内の同型欠陥として併せて解消した）。
第7巡是正時点の「2681件」という中間状態の数値は、それが stale だった
経緯の説明とともに履歴注記として残置した（初版時点の「2584件」注記と
並置）。この構造変更の理由（第7巡の是正自体が数値を陳腐化させた再帰
問題の終端）を「総合判定」節本文に明記した。

**cascade 確認**: 本節の変更は `HARNESS3C_REV06_RECORD.md` のプローズ
のみであり、`run9_schema.py`/JSON manifest/`RUN9_CONTRACT.yaml` のいず
れにも影響しない——第7巡指摘1（§10.1）が確認したとおり、本ファイルを
参照する pin/cross-check は存在しない（`run9_schema.py` のコメント・
`README.md` の散文リンクのみ）。**本節の変更に伴う repin cascade は
発生しない**。

### 11.3 指摘3（P2）: 「Handle non-finite PJS birth distances」

**事実確認**: `pjs_confuser` が `on_positive`（距離>0）/`on_zero`
（距離=0）の2分岐のみを持ち、distance が invalid または non-finite の
場合はどちらの条件にも該当しない未登録分岐であることを確認した
（等号・不等号比較は non-finite 値に対して両方 false となり得るため、
文字通りの消費者は無条件に沈黙し得た——`birth_identity_separation.
invalid_or_nonfinite_feature`〔第1巡指摘3〕・`post_learning_identity_
retention.invalid_or_nonfinite_feature`〔第2巡指摘2〕と同型の穴）。
集約 gate（`birth_gate_aggregate_rule`）側にも対応する失敗分岐参照が
存在しないことを確認した。**指摘内容は事実と一致** → Fable 設計どおり
実装。

**実装（Fable 設計）**: `pjs_confuser` へ `invalid_or_nonfinite_
distance` 分岐（`condition`/`birth_outcome`〔`NOT_ESTABLISHED`〕/
`outcome_detail`〔新定数〕/`action`/`note`、`birth_identity_separation.
invalid_or_nonfinite_feature` と同型キー構成）を追加した。新定数
`IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_DETAIL = "IDENTITY_PROTOCOL_
BIRTH_NOT_ESTABLISHED_PJS_CONFUSER_INVALID_OR_NONFINITE_DISTANCE"` を
新設（既存 `IDENTITY_PROTOCOL_BIRTH_INVALID_FEATURE_DETAIL`/`IDENTITY_
PROTOCOL_BIRTH_PJS_CONFUSER_COLLAPSE_DETAIL` と同型命名・非衝突）。

`birth_gate_aggregate_rule.not_established.outcome_detail_priority` の
`order`/`failure_refs`/`detail_by_key` を3項目→4項目へ拡張し、新設
`invalid_or_nonfinite_pjs_distance` を優先順に組み込んだ。**位置の
設計判断**: validity 系（(1) `invalid_or_nonfinite_feature`）の直後・
collapse 系（(3) `d12_zero_collapse`/(4) `pjs_confuser_zero_distance`）
の前という順位2へ配置した——(1)(2) はいずれも「測定・実装レベルで値
そのものが評価不能（符号比較が定義できない）」という同種の失敗である
のに対し、(3)(4) は「値は有効だが collapse（差が消失）」という別種の
失敗であり値が評価可能であることを前提とする点で validity 系より下流
の懸念事項であるため。さらに (1) を (2) より先に置いた理由は、founder
feature が invalid であれば通常はそれに依存する PJS distance の計算も
invalid になり得るため、より上流の原因である feature validity を優先
する（依存関係に沿った優先順）。`failure_refs` へ `pjs_confuser.
invalid_or_nonfinite_distance` を同じ位置（2番目）で追加した。既存
`on_positive`/`on_zero`/`birth_identity_separation.*` の各分岐は無改変
（`test_pr333_r8_c1_on_nonzero_on_wav_byte_mismatch_on_feature_mismatch_
unchanged` と対の `on_zero`/`on_positive` 無改変確認は既存 byte-
unchanged テスト2件の更新〔§11.6〕で維持）。

### 11.4 ファミリー全数掃討（Task 指示必須・終端宣言）

protocol 全体を機械的に走査し、次の2ファミリーの残余を点検した。

#### (a) 重複可能な述語対の総点検

各節の分岐条件を列挙し、同時成立し得る組に優先順が定義されているかを
全節について確認した。

| 節 | 分岐 | 重複可能性 | 優先順の要否 | 状態 |
|---|---|---|---|---|
| `c0_determinism_attestation.on_mismatch` | `render_byte_mismatch` / `feature_computation_mismatch_with_matching_render` | 無（後者が「render 一致」を前提とし構造的に排他） | 不要 | 既に閉じている |
| `c1_sham_attestation` | `on_nonzero` / `on_wav_byte_mismatch` / `on_feature_mismatch` | 有（`on_nonzero`×`on_wav_byte_mismatch`。`on_feature_mismatch` は D_C1(F)=0 前提のため `on_nonzero` と排他） | 要 | **第8巡指摘1で是正済み**（`outcome_priority` 新設） |
| `positive_reference_audit.on_mismatch` | `wav_byte_mismatch` / `distance_nonzero_or_feature_mismatch_with_matching_wav` | 無（後者が「matching wav」を前提とし構造的に排他） | 不要 | 既に閉じている |
| `birth_identity_separation` | `established` / `not_established` / `invalid_or_nonfinite_feature` | 無（3値が d12/feature validity の値域を排他分割） | 不要 | 既に閉じている |
| `pjs_confuser` | `on_positive` / `on_zero` / `invalid_or_nonfinite_distance` | 無（3値が distance の値域を排他分割——ただし従来は3値目が未登録） | 不要（値域網羅は (b) の対象） | **第8巡指摘3で分岐追加**（優先順ではなく値域欠落——(b) 参照） |
| `post_learning_identity_retention` | `stable` / `shifted` / `invalid_or_nonfinite_feature` | 無（3値が m_other/m_pjs の値域を排他分割） | 不要 | 既に閉じている（第2巡指摘2で分岐追加済み） |
| `birth_gate_aggregate_rule` | `established` / `not_established`（内部4優先枝） | `established`/`not_established` 自体は論理否定で排他。`not_established` 内部の4優先枝は互いに重複し得る（例: feature invalid ⇒ PJS distance も invalid になり得る） | 要（内部4優先枝間） | 既に閉じている（第4/5巡で `established`/`not_established` の優先順機構を確立、**第8巡指摘3で内部優先枝を3→4項目へ拡張**） |
| `birth_gate_overall_pass` | PASS ⇔ established ∧ ¬(いずれの audit_stop_refs にも該当) | audit_stop_refs 内の複数項目は同時該当し得るが、overall_pass は boolean gate であり単一 outcome の選定を要求しない（各 attestation 節が個別に outcome を報告する） | 不要（boolean AND/OR に優先順は無意味） | 既に閉じている |

**残余ゼロを終端宣言する**: C1 の1件（`on_nonzero`×`on_wav_byte_
mismatch`）が本巡で唯一の未対応事例であり、指摘1で是正済み。他の全節
は構造的排他（`condition` 文言が相互に排他な前提を明示）または既存の
優先順機構（`birth_gate_aggregate_rule` 内部4優先枝、第4/5/8巡で確立）
のいずれかで閉じている。**同型の重複述語対の残余はゼロ**。

#### (b) 分岐の無い値域の総点検

各判定入力について、「正 / ゼロ / 負 / non-finite / invalid」の5値域
被覆表を作成した。

| 判定入力 | 定義域 | 正 | ゼロ | 負 | non-finite/invalid |
|---|---|---|---|---|---|
| d12（`birth_identity_separation`） | Euclidean distance（≥0 保証） | `established` | `not_established` | N/A（距離は非負、構造的に空） | `invalid_or_nonfinite_feature`（既存・第1巡指摘3） |
| pjs_confuser distance | Euclidean distance（≥0 保証） | `on_positive` | `on_zero` | N/A（距離は非負、構造的に空） | `invalid_or_nonfinite_distance`（**第8巡指摘3で新設・本改訂で被覆完了**） |
| D_C0(F)（C0 render replay 距離） | 等価性判定（exact-match test） | N/A（「一致/不一致」の二値判定であり符号域を持たない） | 一致（無違反、pass） | N/A | `on_mismatch` に包摂——mismatch 判定は「reference と exact に一致しない」ことの検出であり、NaN 等の non-finite 出力も自動的に「不一致」に分類される（等価性判定は non-finite 値に対して安全側に倒れる。値域の観点で追加分岐は不要） |
| D_C1(F)（C1 sham 距離） | 等価性判定（exact-match test） | N/A | 一致（無違反、pass） | N/A | `on_nonzero`/`on_wav_byte_mismatch`/`on_feature_mismatch` に包摂——D_C0(F) と同型の理由で等価性判定は non-finite を安全側で「不一致」に分類する |
| positive reference 距離 | 等価性判定（exact-match test） | N/A | 一致（無違反、pass） | N/A | `on_mismatch` に包摂——同型の理由 |
| m_other = d_other − d_self | 実数（差、符号は任意） | `stable` の一部条件 | `shifted`（`<=0` に包摂） | `shifted`（`<=0` に包摂） | `invalid_or_nonfinite_feature`（既存・第2巡指摘2） |
| m_pjs = d_pjs − d_self | 実数（差、符号は任意） | `stable` の一部条件 | `shifted`（`<=0` に包摂） | `shifted`（`<=0` に包摂） | `invalid_or_nonfinite_feature`（既存・第2巡指摘2、m_other と共有） |

**設計上の一般則（本掃討で言語化）**: distance/等価性を判定する入力
（d12・pjs_confuser distance・D_C0/D_C1/positive reference の各種
distance）のうち、**符号比較（`>0`/`=0`）で判定するもの**（d12・
pjs_confuser distance）は non-finite 値に対して両方の比較が false と
なり得るため明示的な invalid/non-finite 分岐が必須である一方、**等価性
判定（exact-match test）で判定するもの**（D_C0/D_C1/positive reference
の各 exact-replay attestation）は non-finite 出力も自動的に「reference
と一致しない」＝ mismatch に分類されるため追加分岐が不要——この非対称
性が、pjs_confuser distance（符号比較）にだけ本巡まで穴が残っていた
構造的理由である。m_other/m_pjs は差分（符号比較の一種）だが、既存の
`invalid_or_nonfinite_feature` 分岐（第2巡指摘2）で既に被覆済み。

「負」の値域はすべての純粋な distance（d12・pjs_confuser・D_C0/D_C1/
positive reference の各距離）について構造的に空である（Euclidean
distance は定義上非負）——N/A であり残余ではない。m_other/m_pjs のみ
差分のため負の値域が意味を持ち、これは `shifted`（`<=0`）へ既に包摂
済みである。

**残余ゼロを終端宣言する**: pjs_confuser distance の1件（本巡で被覆
完了）が唯一の未被覆値域であり、他のすべての判定入力は既存分岐で
被覆済みか、値域が構造的に空（N/A）であるかのいずれかである。**同型の
無分岐値域の残余はゼロ**。

### 11.5 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2707 passed, 7 warnings in ~43s
```

新設テスト21件（2686→2707）: `tests/test_run9_contract.py` へ
`pr333_r8_*` prefix で追加——`c1_sham_attestation.outcome_priority` の
`order` frozen tuple 一致・`detail_by_key` 実データ一致確認・欠落
トップレベルキー/サブキー拒否・`order` 並び替え/dict 偽装拒否・
`detail_by_key` 値改ざん/未登録キー拒否・`order_note` 空文字拒否・
既存 `on_nonzero`/`on_wav_byte_mismatch`/`on_feature_mismatch` 無改変
確認（指摘1、10件）、`pjs_confuser.invalid_or_nonfinite_distance` の
happy path・非衝突確認・欠落キー/サブキー拒否・`birth_outcome`/
`outcome_detail` 改ざん拒否・空文字拒否（指摘3、6件）、
`birth_gate_aggregate_rule` 優先順4項目拡張の確認・3項目への回帰拒否
（指摘3、2件）、`load_pinned_identity_decision_protocol()` happy path
（新設フィールドの到達確認）・改ざん経由 loader fail-closed 確認
（指摘1/3共通、2件）、`hypothesis_algebra_sha` repin 値の回帰更新
（既存テスト1件の値更新）、`pjs_confuser`/`birth_identity_separation`
byte-unchanged 確認2件の対象範囲更新（トップレベル key set の伸長を
許容するよう docstring・assertion を是正）。

### 11.6 変更ファイル

- `inputs/identity_decision_protocol_v0.6.json`: `c1_sham_attestation`
  へ `outcome_priority`（`order`/`detail_by_key`/`order_note`）新設
  （指摘1）、`pjs_confuser` へ `invalid_or_nonfinite_distance` 分岐
  新設（指摘3）、`birth_gate_aggregate_rule.not_established.outcome_
  detail_priority` の `order`/`failure_refs`/`detail_by_key` を3項目→
  4項目へ拡張（指摘3）。既存 `on_nonzero`/`on_wav_byte_mismatch`/
  `on_feature_mismatch`/`on_positive`/`on_zero`/`birth_identity_
  separation.*` の各既存分岐は無改変。
- `run9_schema.py`: 新規定数 `IDENTITY_PROTOCOL_PJS_INVALID_DISTANCE_
  DETAIL`・`_IDENTITY_PROTOCOL_C1_OUTCOME_PRIORITY_ORDER`、
  `_IDENTITY_PROTOCOL_BIRTH_GATE_FAILURE_REFS`/`_IDENTITY_PROTOCOL_
  BIRTH_GATE_PRIORITY_ORDER`/`_IDENTITY_PROTOCOL_BIRTH_GATE_DETAIL_BY_
  KEY` を3項目→4項目へ改訂、`validate_identity_decision_protocol()` へ
  `c1_sham_attestation.outcome_priority`・`pjs_confuser.invalid_or_
  nonfinite_distance` の構造検証ブロックを追加（`c1_sham_attestation`/
  `pjs_confuser` それぞれの `required_keys` へ新キー追加を含む）。
- `RUN9_CONTRACT.yaml`: `hypothesis_algebra_sha` repin（値・repin履歴
  コメント追加）。
- `HARNESS3C_REV06_RECORD.md`: 「総合判定」節をポインタ形式へ構造変更
  （指摘2）、本節（§11）追加。
- `tests/test_run9_contract.py`: `hypothesis_algebra_sha` repin 値の
  回帰更新、`pjs_confuser`/`birth_identity_separation` byte-unchanged
  確認2件の対象範囲更新、`failure_refs` 4項目化の回帰更新、新規
  fail-closed/確認テスト21件追加。

### 11.7 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分（`USER_ADJUDICATION_
  20260827_IDENTITY_REV06.txt`）は1 byte も変更していない。
- 既存 `BIRTH_OUTCOMES`/`IDENTITY_OUTCOMES`/`FAILURE_CLASSES` frozen
  tuple への値追加は行っていない（既存語彙の再利用のみ）。
  `_IDENTITY_PROTOCOL_BIRTH_GATE_FAILURE_REFS`/`_IDENTITY_PROTOCOL_
  BIRTH_GATE_PRIORITY_ORDER`/`_IDENTITY_PROTOCOL_BIRTH_GATE_DETAIL_BY_
  KEY`/`_IDENTITY_PROTOCOL_OVERALL_PASS_AUDIT_STOP_REFS` は監査停止・
  優先順参照の列挙という性質上、新設分岐の追加に伴い伸長する運用
  （第5/6巡で新設・伸長、本巡も3→4項目へ伸長）——`BIRTH_OUTCOMES`/
  `IDENTITY_OUTCOMES`/`FAILURE_CLASSES` のような判定語彙 enum とは
  異なるカテゴリであり、これらへの値追加ではない。
- `c1_sham_attestation.outcome_priority`/`birth_gate_aggregate_rule`
  優先順拡張 いずれも判定規約の凍結までを範囲とし、`outcome_all_
  applicable_keys`/`outcome_detail_all_applicable_keys` 自体の実行時
  生成ロジックは本 PR の対象外（裁定§8 実行順どおり、Birth Gate の
  実行自体は依然として事前登録フェーズの対象外）。
- ファミリー全数掃討（§11.4）の結果、(a) 重複可能な述語対・(b) 分岐の
  無い値域のいずれも本巡の3件の是正で残余ゼロとなったことを確認した
  ——追加の是正対象は発見されなかった。

## 12. PR #333 Codex bot レビュー第9巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`fb22c2a2`）。1件（P1）着手前に事実確認、指摘内容は事実と一致したため
採用対応。裁定正本は本 PR の対象外（既存裁定 §1-§9 の再解釈ではなく、
第2巡が見落とした宣言文レベルの stale 表明の是正）。

### 12.1 指摘（P1）: 「Reconcile the identity-axis canonical source」

**事実確認（着手前、逐語引用）**:

- `evaluation/probe_manifest.json` `measurement_boundary.identity_axis_
  source`（是正前）: 「inputs/identity_metric_space.json が正本
  （domains/identity_domain_run9_v1.json の metric_space_sha として
  pin済み）。distance/calibration/confuser_controlの式・閾値は 本
  manifestで重複定義しない。」
- 同 `scope_statement`（是正前、抜粋）: 「identity軸は
  inputs/identity_metric_space.json（metric_space_sha としてpin済み）が
  正本」
- `inputs/measurement_spec_manifest.json` `scope_note`（是正前、抜粋、
  134行付近）: 「identity 軸の距離・校正・閾値の正本は
  inputs/identity_metric_space.json（metric_space_sha として既
  PINNED）であり、本 manifest では重複定義しない」
- `README.md`（是正前、601-602行付近）: 「identity 軸は
  `inputs/identity_metric_space.json` 正本」
- `README.md`（是正前、875-881行付近）: 「式・閾値そのものは
  `inputs/identity_metric_space.json` を正本のまま重複定義しない」

いずれも現在形で「calibration・閾値・判定規則の正本は
identity_metric_space.json」と宣言しており、rev 0.6 裁定 §7「旧
calibration/decision ruleをrev 0.6実行についてsupersedeする」（新規
`inputs/identity_decision_protocol_v0.6.json` への切替え、
`hypothesis_algebra_sha` として PINNED 済み）への言及を一切欠いていた
ことを確認した。第2巡指摘1（本記録 §5）は `evaluation/probe_manifest.json`
revision_bridge の**エントリ単位**（c0_replay_takes/c1_sham_takes/
positive_reference/negative_reference の `identity_metric_space_ref`）の
参照付け替えのみを行い、上記の**宣言文レベル**（measurement_boundary
自身の identity_axis_source/scope_statement、および同型の
measurement_spec_manifest.json scope_note・README プローズ2箇所）は
見落としたまま残っていた。**指摘内容は事実と一致** → Fable 設計どおり
実装。

### 12.2 実装（Fable 設計）

対象5箇所すべてを、feature/distance 定義（および calibration 配下の
`preserved_generation_definitions`——d_c0_population/d_c1_population/
positive_reference_definition/negative_reference_definition、生成手順の
定義のみ）は `inputs/identity_metric_space.json` が正本のまま（無改変・
immutability 維持）／calibration・閾値・判定規則
（freeze_threshold/validity_gates/decision_rule の各節）は rev 0.6 実行
について `inputs/identity_decision_protocol_v0.6.json` が正本
（supersede、裁定 §7、`hypothesis_algebra_sha` として PINNED）、という
二元宣言へ更新した。旧文言はいずれも各フィールド内へ〔旧文言〕形式で
履歴残置し（probe_manifest.json/measurement_spec_manifest.json は
フィールド末尾の〔旧文言（PR #333第9巡是正前...）〕括弧、README.md/
run9_schema.py は同型の〔履歴...〕括弧）、削除しない。

- `evaluation/probe_manifest.json`: `measurement_boundary.identity_axis_
  source`/`scope_statement` を二元宣言へ更新。
- `inputs/measurement_spec_manifest.json`: `scope_note` の該当箇所を
  同型に更新。
- `README.md`: probe_manifest.json 7成果物の記述（旧601-602行付近）と
  measurement_spec_manifest.json extractor カタログの記述（旧875-881行
  付近）の2箇所を更新。README はプローズであり pin 対象外であることを
  確認済み（`README.md:NNN` 形式の行番号引用は
  `inputs/measurement_spec_manifest.json` の2箇所（19行目
  `scope_source`・134行目 `scope_note`）のみで、いずれも本改訂で編集した
  2ブロックより手前（587-590行）を指しており非影響——リポジトリ全体を
  grep して確認した）。
- `run9_schema.py`: probe manifest のモジュール冒頭コメント（旧
  3585-3591行付近）と `validate_probe_manifest()` docstring（旧
  6733-6745行付近）を同型のパラフレーズとして同時是正（宣言文の要約が
  別の場所で stale 化する再発を防ぐ）。加えて
  `_MEASUREMENT_BOUNDARY_IDENTITY_AXIS_MARKERS` へ
  `"identity_decision_protocol_v0.6.json"`/`"supersede"` の2マーカーを
  追加し、`identity_axis_source` が今後も二元宣言を両方言及することを
  `validate_probe_manifest()` で fail-closed 強制するようにした
  （既存の `inputs/identity_metric_space.json`/`metric_space_sha`
  マーカーは無改変のまま維持）。

**フィールド閉集合の扱い**: `measurement_boundary` は
`_MEASUREMENT_BOUNDARY_KEYS`（`scope_statement`/`identity_axis_source`/
`development_generalization_axis_source` の3キーのみ）で厳密閉集合
検証されており、新規キー追加は validator 改訂を要する。本改訂は
新規キーを追加せず既存2フィールドの文言拡張のみで完結させた（validator
側は新マーカー2件の追加のみ）。

### 12.3 ファミリー全数掃討（Task 指示必須・終端宣言）

「calibration/閾値/判定の正」と現在形で主張する箇所をリポジトリ全体
（run9 ディレクトリ + README + docs 内の run9 言及）から `identity_
metric_space`/`正本` の組で grep し、全数を点検した。

| # | 箇所 | 状態（是正前） | 対応 |
|---|---|---|---|
| 1 | `evaluation/probe_manifest.json` `measurement_boundary.identity_axis_source` | 現在形で正本宣言・supersede 未言及 | **本巡で是正**（§12.2） |
| 2 | 同 `scope_statement` | 同上 | **本巡で是正**（§12.2） |
| 3 | `inputs/measurement_spec_manifest.json` `scope_note`（134行） | 同上 | **本巡で是正**（§12.2） |
| 4 | `README.md`（旧601-602行、probe_manifest 7成果物の記述） | 同上 | **本巡で是正**（§12.2） |
| 5 | `README.md`（旧875-881行、measurement_spec extractor カタログの記述） | 同上 | **本巡で是正**（§12.2） |
| 6 | `run9_schema.py` probe manifest モジュールコメント（旧3585-3591行） | 同型のパラフレーズ・supersede 未言及 | **本巡で是正**（§12.2、Task 範囲外だが同型欠陥として併せて解消） |
| 7 | `run9_schema.py` `validate_probe_manifest()` docstring（旧6733-6745行） | 同上 | **本巡で是正**（§12.2、同上） |
| 8 | `run9_schema.py` `_validate_revision_bridge_entry()` エラーメッセージ（`_IDENTITY_METRIC_SPACE_REF_PREFIX` 検証、6564行付近） | 「正本は identity_metric_space.json への参照のみ」 | **対象外と判定**——これは `identity_metric_space_ref` フィールド自身の構造検証（値が同ファイルへの参照でなければならない、という規約）であり、calibration・閾値・判定規則の現行正本を主張するものではない。エントリの生成定義参照（`identity_metric_space_ref`）自体は rev 0.6 でも supersede 対象外のまま有効——第2巡是正が既に `identity_decision_protocol_ref`/`superseded_calibration_note` を併記済み |
| 9 | `evaluation/probe_manifest.json` revision_bridge 4エントリの `superseded_calibration_note` | rev 0.6 供 supersede を明記済み | 対象外（第2巡で既に是正済み、本巡は無改変） |
| 10 | README.md 1298行付近（rev 0.6 の解消済み節、過去形で supersede の経緯を記述） | 過去形の履歴記述（正しく supersede に言及） | 対象外（現在形の stale 宣言ではない） |

**残余ゼロを終端宣言する**: 上記10箇所を機械的に走査した結果、指摘が
挙げた3箇所（#1-3）に加え、第2巡グラウンディングが見落とした同型の
宣言文2箇所（README.md #4-5）と、宣言文のパラフレーズに過ぎない
run9_schema.py 内コメント2箇所（#6-7）を発見し、いずれも本巡で是正した。
それ以外（#8-10）は、フィールド自体の構造規約や生成定義参照
（supersede 対象外）、または既に是正済み・過去形で正しく記述された
箇所であり、対象外と判定した。**同型の宣言文レベル欠陥の残余はゼロ**。

### 12.4 repin cascade

`evaluation/probe_manifest.json` のバイト変更に伴い `probe_manifest_sha`
を repin（旧
`60adeb93b6ca920bdbc590f24ffdb62f68bd12a387e2543361d88954fb1932fe` → 新
`c121243b9679ceae88322a43d1c804c2c5eddb4d25413c09e6a7d737033ea095`）。
`identity_probe.probe_manifest_sha` 転記値・`pjs_song_based_probe_non_
adoption_citation`/`c1_sham_takes.description` への行番号引用（787/880
行）はいずれも既存キーの**文字列拡張のみ**（新規キー挿入を伴わない）
ため行番号は不変——転記値のみ更新し、`inputs/dataset_split_manifest.json`
の実バイトに伴い `dataset_manifest_sha` を repin（旧
`4138639209caabf08465141681756e3b0bc7be4167516ea9bd93b6d276456cf4` → 新
`43de511f2711fc9d559e8d21461a5b00c3a99ddc03b83455d577039e7952ddd6`）。

`inputs/measurement_spec_manifest.json` の `scope_note` 変更に伴いバイトは
変わったが、`measurement_spec_sha` は元々 PENDING（VG-L0 学習ハーネス
実装待ちの既存律速は不変）のため repin は発生しない——本文是正のみ
（`test_pin1_r3_measurement_spec_manifest_file_byte_unchanged_despite_
pending_pin` の期待値を新実バイト sha256 へ更新して追随）。

`README.md` はプローズであり pin 対象外（§12.2 で確認済み）。
`inputs/identity_decision_protocol_v0.6.json`・`inputs/identity_metric_
space.json`・`domains/identity_domain_run9_v1.json` は1 byte も
変更していない（`git status --short` で確認済み）ため `hypothesis_
algebra_sha` は無改変のまま。

### 12.5 既存テストの追随（回帰値更新）

- `tests/test_run9_contract.py::test_pin2_dataset_manifest_sha_is_pinned_
  and_matches_actual_file`: `dataset_manifest_sha` 期待値を新値へ更新。
- `tests/test_run9_contract.py::test_pin1_r3_measurement_spec_manifest_
  file_byte_unchanged_despite_pending_pin`: 期待 sha256 を新値へ更新
  （テスト名の「unchanged」は「pin 状態が PENDING のまま」を指すのみで
  ファイルバイト自体は本巡含め計3回改訂されていることをテスト docstring
  へ明記）。
- `tests/test_run9_contract.py::test_rev06_probe_manifest_does_not_
  declare_hypothesis_algebra_sha_pending`: 「`hypothesis_algebra_sha`
  という文字列が一切出現しない」という絶対不在の検査から、「出現する
  場合は PENDING と矛盾併記していないこと」という正確な検査へ改訂
  （本巡の是正で `identity_axis_source` が正当に `hypothesis_algebra_
  sha` を PINNED 宣言の一部として参照するようになったため、絶対不在
  検査のままでは正しい是正を偽陽性で拒否してしまう——検査意図
  〔PR #324 型の PENDING 偽装矛盾の検出〕をそのまま保ちつつ、判定条件を
  「不在」から「PENDING との矛盾併記の不在」へ精密化した）。

### 12.6 新設テスト

- `tests/test_run9_probe_manifest.py::test_negative_pr333_r9_identity_
  axis_source_missing_rev06_supersede_marker`: `identity_axis_source` を
  旧文言（rev 0.6 supersede 未言及）へ差し戻すと fail-closed 拒否される
  ことの回帰。
- `tests/test_run9_probe_manifest.py::test_negative_pr333_r9_identity_
  axis_source_missing_supersede_word`: `identity_decision_protocol_v0.6.
  json` への言及があっても `supersede` の語を欠く文言は依然拒否される
  ことの確認（2マーカーが独立必須であることの確認）。
- `tests/test_run9_contract.py::test_pr333_r9_canonical_source_
  declarations_reference_rev06_supersede`: 実ファイル
  （probe_manifest.json/measurement_spec_manifest.json/README.md）が
  いずれも二元宣言（feature/distance 側=identity_metric_space.json・
  calibration/閾値/判定規則側=identity_decision_protocol_v0.6.json+
  supersede）を実際に含んでいることの直接照合。

### 12.7 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2710 passed, 7 warnings in ~43s
```

新設テスト3件（2707→2710）。既存テスト3件の回帰値更新（§12.5）。

### 12.8 変更ファイル

- `evaluation/probe_manifest.json`: `measurement_boundary.identity_axis_
  source`/`scope_statement` を二元宣言へ更新（旧文言は履歴残置）。
- `inputs/measurement_spec_manifest.json`: `scope_note` を同型に更新
  （旧文言は履歴残置）。
- `inputs/dataset_split_manifest.json`: `identity_probe.probe_manifest_
  sha` 転記値更新（行番号引用は不変）。
- `RUN9_CONTRACT.yaml`: `probe_manifest_sha`/`dataset_manifest_sha`
  repin（値・repin履歴コメント追加）+ `measurement_spec_sha` 本文是正
  コメント追加（pin 状態は PENDING のまま変更なし）。
- `README.md`: probe_manifest.json 7成果物の記述・measurement_spec_
  manifest.json extractor カタログの記述の2箇所を二元宣言へ更新（旧
  文言は各箇所の〔履歴〕括弧へ保持）。
- `run9_schema.py`: probe manifest モジュールコメント・
  `validate_probe_manifest()` docstring を同型に更新、
  `_MEASUREMENT_BOUNDARY_IDENTITY_AXIS_MARKERS` へ2マーカー追加。
- `tests/test_run9_probe_manifest.py`: 新設 fail-closed テスト2件。
- `tests/test_run9_contract.py`: 新設確認テスト1件 + 既存テスト3件の
  回帰値・検査条件更新（§12.5）。

### 12.9 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分
  （`USER_ADJUDICATION_20260827_IDENTITY_REV06.txt`）は1 byte も
  変更していない。`inputs/identity_decision_protocol_v0.6.json` も本巡は
  無改変（`hypothesis_algebra_sha` 自体は repin なし）。
- `run9_schema.py` のモジュールコメント・docstring 2箇所（§12.3 #6-7）
  の是正は Task 指示（3箇所の宣言文）の範囲外だが、同一の stale
  パラフレーズであり、ファミリー全数掃討（Task 指示必須）の一部として
  併せて是正した——第2巡（§5.0）が「文字列の不在確認だけでなく実参照先
  の突合せまで行う」という教訓を残したのと同型に、本巡は「指摘が挙げた
  宣言文そのものだけでなく、その宣言文を要約するコメント・docstring も
  併せて点検する」という教訓を残す。
- README.md の2箇所是正により行数が増加したが、リポジトリ全体を grep
  して README.md への行番号引用が `inputs/measurement_spec_manifest.json`
  の2箇所（19行目・134行目、いずれも本改訂で編集した2ブロックより手前の
  587-590行を指す）のみであることを確認済みであり、他箇所への行番号
  シフトの影響はない。

## 13. PR #333 Codex bot レビュー第10巡対応（2026-08-28、フェーズ1）

対象 PR: #333（branch `claude/run9-implementation-start-p7xqqu`、head
`f983b2da`）。1件（P2）着手前に事実確認、指摘内容は事実と一致したため
採用対応。裁定正本は本 PR の対象外（第9巡が確立した二元宣言の内容自体
ではなく、その回帰ガードの適用範囲の是正）。**本巡で bot レビュー対応の
採否上限10巡に到達した**（§13.4 参照）。

### 13.1 指摘（P2）: 「Guard all canonical-source declarations」

**事実確認（着手前）**: `run9_schema.py` を読み、第9巡（本記録 §12）が
新設した rev 0.6 supersede マーカー検査の適用範囲を突合した。

- `_validate_measurement_boundary()`（旧4880-4895行付近）: `identity_axis_
  source` は `_MEASUREMENT_BOUNDARY_IDENTITY_AXIS_MARKERS`（`inputs/
  identity_metric_space.json`/`metric_space_sha`/`identity_decision_
  protocol_v0.6.json`/`supersede` の4マーカー）で検査されていたが、
  `scope_statement`（同関数、旧6708-6711行付近）は `_MEASUREMENT_
  BOUNDARY_SCOPE_MARKERS`（「何を鳴らすか」/「どう測るかは対象外」の
  汎用文言2件）のみで、rev 0.6 マーカーは一切要求していなかった。
- `validate_measurement_spec_manifest()` の `scope_note`（旧8816行）は
  `_require_non_empty_str()` のみ——非空検査のみで、マーカー検査
  そのものが存在しなかった。

**指摘内容は事実と一致**（identity_axis_source 以外の2箇所は将来の
repin で「identity_metric_space.json が calibration・閾値の正」へ
回帰しても検査を素通りする）→ Fable 設計どおり実装。

実データ側（`evaluation/probe_manifest.json` の `scope_statement` /
`inputs/measurement_spec_manifest.json` の `scope_note`）は第9巡是正
（§12.2）で既に4マーカーを含む二元宣言文へ更新済みであることを
`python3 -c "..."` で事前確認した——いずれも新検査を無改変のまま通過
することを実装前に確認済み（見込みどおり、manifest 本文の改訂・repin は
発生しなかった）。

### 13.2 実装（Fable 設計）

第9巡が identity_axis_source 専用に定義した4マーカー tuple
（`_MEASUREMENT_BOUNDARY_IDENTITY_AXIS_MARKERS`）を、宣言箇所限定の
識別子名から用途横断の `_REV06_SUPERSEDE_DECLARATION_MARKERS` へ改名し
（値は無改変）、3箇所すべてが同一マーカー集合・同一
`_validate_marker_bearing_str()` 経路を通るようガード方式を統一した
（Task 指示「共通ヘルパー化が自然ならそうする」——低レベル共通ヘルパー
`_validate_marker_bearing_str()` は第2巡時点で既存のため、新規ヘルパー
関数は追加せず、マーカー定義の一元化 + 呼び出し側3箇所の統一で足りると
判定）。

- `_validate_measurement_boundary()`: `scope_statement` の検査を
  `_MEASUREMENT_BOUNDARY_SCOPE_MARKERS + _REV06_SUPERSEDE_DECLARATION_
  MARKERS`（汎用文言2件 + rev 0.6 マーカー4件の結合）へ拡張。
  `identity_axis_source` は同じ `_REV06_SUPERSEDE_DECLARATION_MARKERS`
  を参照するよう改名のみ（検査内容は無改変）。
- `validate_measurement_spec_manifest()`: `scope_note` の検査を
  `_require_non_empty_str()` から `_validate_marker_bearing_str(...,
  markers=_REV06_SUPERSEDE_DECLARATION_MARKERS)` へ置き換え（非空検査は
  `_validate_marker_bearing_str()` が内部で `_require_non_empty_str()`
  を呼ぶため後退しない）。

manifest 本文（`evaluation/probe_manifest.json`/`inputs/measurement_
spec_manifest.json`）は無改変——事前確認（§13.1）どおり現データが新検査
を通過したため、repin は発生していない。

### 13.3 新設テスト

- `tests/test_run9_probe_manifest.py::test_negative_pr333_r10_scope_
  statement_missing_rev06_supersede_marker`: `scope_statement` を
  rev 0.6 supersede 未言及の旧文言相当へ差し戻すと fail-closed 拒否
  されることの回帰（identity_axis_source 側の第9巡回帰テストと同型）。
- 同 `test_negative_pr333_r10_scope_statement_missing_supersede_word`:
  `identity_decision_protocol_v0.6.json` への言及があっても `supersede`
  の語を欠く `scope_statement` は依然拒否されることの確認（2マーカーが
  独立必須であることの確認）。
- `tests/test_run9_contract.py::test_negative_pr333_r10_scope_note_
  missing_rev06_supersede_marker`: `measurement_spec_manifest.json`
  `scope_note` を旧文言相当へ差し戻すと `validate_measurement_spec_
  manifest()` が fail-closed 拒否することの回帰（第9巡時点では検査
  自体が存在せず非空文字列であれば何でも通過していた欠陥の直接回帰）。
- 同 `test_negative_pr333_r10_scope_note_missing_supersede_word`: 同上、
  `supersede` の語のみを欠く場合の確認。

### 13.4 検証結果

```
$ ruff check .
All checks passed!

$ python3 -m pytest voice_genesis/evolution/run9_dual_founder_pjs/tests -q --tb=short
2714 passed, 7 warnings in ~47s
```

新設テスト4件（2710→2714）。既存テストの回帰値更新は不要（manifest
本文が無改変のため sha・回帰値のいずれも変わらない）。

### 13.5 変更ファイル

- `run9_schema.py`: `_MEASUREMENT_BOUNDARY_IDENTITY_AXIS_MARKERS` を
  `_REV06_SUPERSEDE_DECLARATION_MARKERS` へ改名（値は無改変）、
  `_validate_measurement_boundary()` の `scope_statement` 検査へ rev 0.6
  マーカーを追加、`validate_measurement_spec_manifest()` の `scope_note`
  検査を非空検査からマーカー検査へ置き換え。
- `tests/test_run9_probe_manifest.py`: 新設 fail-closed テスト2件
  （`scope_statement`）。
- `tests/test_run9_contract.py`: 新設 fail-closed テスト2件
  （`scope_note`）。

`evaluation/probe_manifest.json`・`inputs/measurement_spec_manifest.json`
はいずれも無改変（§13.2 で確認）。`RUN9_CONTRACT.yaml`・pin 値の repin
なし。

### 13.6 逸脱事項

- immutability 対象（`identity_metric_space.json`/`identity_domain`/
  `Genome`/speaker map manifest）・裁定逐語転記部分
  （`USER_ADJUDICATION_20260827_IDENTITY_REV06.txt`）は1 byte も
  変更していない。`inputs/identity_decision_protocol_v0.6.json` も本巡は
  無改変。
- 本巡は検査ロジックのみの改訂であり、ファミリー全数掃討（§12.3 が
  10箇所を既に確認済み）を再実行する対象ではない——本巡の指摘は §12.3
  #1-3（既是正のデータ文言）ではなく、そのデータ文言を守る**検査コード
  側**の適用漏れ2箇所（scope_statement/scope_note）についてのものであり、
  §12.3 の掃討結果自体（データ文言レベル）に変更はない。

### 13.7 採否上限10巡到達 — 以後の運用切替え

`AGENTS.md` §3-4 の bot レビュー対応上限（同一 PR 10ラウンド）に本巡で
到達した。CLAUDE.md「bot レビュー対応の運用」節が定める運用に従い、
以後 PR #333 への Codex bot レビュー追加ラウンドは:

- **採用するのは新しい具体的経路を示す指摘のみ**（3分類 — 実コード被害
  / 将来汚染 / 致命的バグ——のいずれかに該当し、かつ本記録が既に
  掃討・対応済みのファミリーとは異なる具体的な新規欠陥経路を指す指摘）。
- それ以外（既対応ファミリーの言い換え、逓減領域の指摘、3分類に該当
  しない指摘）は**境界宣言のみを記録し、実装は行わず User へ引き継ぐ**。
- 「打ち切りは3分類を上書きしない」——新しい具体経路を示す指摘であれば
  巡数上限を超えていても採用判定自体は行う（上限は「無条件で全採用し
  続ける運用」を終了させるものであり、3分類判定を無効化するものでは
  ない）。
