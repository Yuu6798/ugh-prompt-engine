# DESIGN RUN9 — Revision 0.6

- **裁定日:** 2026-08-27
- **裁定者:** User
- **design_revision:** 0.5 → 0.6
- **裁定ソース:** [`USER_ADJUDICATION_20260827_IDENTITY_REV06.txt`](./USER_ADJUDICATION_20260827_IDENTITY_REV06.txt)
  （「RUN9 User裁定 — Identity Calibration Degeneracy / design_revision
  0.6」、逐語・一字一句改変禁止。受領経路は口頭/チャット裁定 — session
  scratchpad `scratchpad/run9_user_adjudication_identity_rev06.md` へ
  Fable が記録したものを repo 内収載した。同ファイルの実バイト sha256
  は `RUN9_CONTRACT.yaml` へ情報記録として収載する——`design_doc_sha256`
  規約と同じファイル実バイト規約で、裁定文書自体は「値の転記元の証跡」
  であり RUN9 の実行前提条件そのものではないため
  `USER_ADJUDICATION_20260826_EXECUTION_PROFILE.txt` の裁定 txt sha256
  と同じ扱い）。

## 0. 退化の解析的確定（本改訂の契機）

本改訂は、C0/C1・Identity 距離・学習結果・holdout を観測した後の救済では
ない。既存の render replay byte 決定論実測（RUN9-L0-HARNESS-3a
[`HARNESS3A_SPEAKER_MAP_RECORD.md`](./HARNESS3A_SPEAKER_MAP_RECORD.md) の
6点 pre-pin 検証中「生成 embedding の byte 決定論」「render replay 決定論」
2点 PASS 実測、および speaker map manifest の runtime 合成が乱数・摂動・
試聴後調整を構造的に排除する決定論的単純加重和であること）により、C0
（NO_LEARNING_REPLAY 枝の同条件再 render）はいずれも同一 raw WAV bytes・
同一 identity feature bytes/hash を生成する——すなわち founder F の C0
母集団 D_C0(F) は全 20 標本が恒等ゼロ距離となる。旧 `identity_metric_
space.json` calibration.freeze_threshold の式 `theta_cal(F) =
P95(D_C0(F))` は、全ゼロ標本に対しては解析的に `theta_cal(F) = 0` へ
退化する（この事実は Birth Probe を実際に render しなくても、render
replay が byte-deterministic であるという既に確立済みの事実から導出
できる——実測を待たずに退化が確定している）。

`theta_cal(F) = 0` の下では、`calibration.decision_rule`
（`d(r) <= theta_cal(F)` ⟹ STABLE_BY_MACHINE_METRIC、closed-below 境界）は
「render byte が寸分でも異なれば無条件 SHIFTED」という判定へ縮退し、
`c1_gate`（`P50(D_C1(F)) <= theta_cal(F)`）も同様に「C1 sham render が
C0/reference と完全 byte 一致しない限り無条件 INVALID」へ縮退する。
これは校正手続きとしての機能を失っており（PoR §9 [C] DESIGN FAILURE /
UNOBSERVABLE の一種に相当する構造的退化）、結果を見た後の閾値の事後
調整で救済することは feasibility_note の事前登録規律・holdout 開封後の
救済禁止規律（DESIGN_RUN9_REVISION_0.3.md 改訂E）に反する。本改訂は、
この退化を Birth Probe 実行**前**に解析的に確定させたことを根拠とする
pre-run design correction であり、User 裁定前文が明示的にこの位置づけを
宣言する（下記「1. 裁定本文」参照）。

## 1. 選択肢A採用 — Identity decision protocol 全体の再事前登録

裁定逐語:

> 選択肢Aを採用する。
>
> ただし、Birth Identity Separationだけでなく、
> 学習後Identity保持判定を含むIdentity decision protocol全体を
> design_revision 0.6として再事前登録する。

Birth Identity Separation（旧 §4 相当）だけでなく、学習後 Identity 保持
判定（旧 §6 相当）を含む Identity decision protocol 全体を、新規
`inputs/identity_decision_protocol_v0.6.json`（`run9-identity-decision-
protocol/0.6`）として再事前登録する。既存 `identity_metric_space.json`
の feature_extractor / extraction_procedure / identity_feature /
distance / confuser_control（PJS reference 定義を含む）の各節は**無改変**
のまま参照により有効とし、`calibration` 節（freeze_threshold /
validity_gates / decision_rule）のみを rev 0.6 の実行について supersede
する（詳細は「7. Metric文書とGenomeの不変性」節を参照）。

## 2. C0 — runtime 決定論の実証、閾値推定には不使用

裁定逐語:

> C0はFounderごとに20 takesを実行する。
>
> 全takeについてreference renderと
> raw WAV SHA256およびidentity feature bytes/hashの完全一致を要求する。
>
> D_C0(F)=0×20を期待値とし、
> replay-noise閾値の推定には使用しない。
>
> C0はruntime決定論の実証として保存する。
> 不一致時は閾値を作らず、
> IMPLEMENTATION_FAILUREまたは
> DETERMINISM_CONTRACT_BROKENとして停止する。

C0（`RUN9_CONTRACT.yaml interventions.c0_replay_takes_per_founder`、
PINNED、founder 1体あたり n=20、無改変）は、旧 rev（〜0.5）の校正母集団
としての役割を終える。rev 0.6 では「runtime 決定論の実証」として保存
される exact-replay attestation のみを目的とし、D_C0(F) の分布から
閾値を推定する用途には使用しない（0節が確定した退化の直接的帰結）。
20 take 全てで reference render との raw WAV SHA256・identity feature
bytes/hash の完全一致（D_C0(F)=0×20）を期待値とし、不一致が生じた場合は
render byte 自体が不一致なら `DETERMINISM_CONTRACT_BROKEN`、render
一致だが feature 計算が不一致なら `IMPLEMENTATION_FAILURE` として停止
する（新規閾値を作らない）。詳細な構造は
`inputs/identity_decision_protocol_v0.6.json` `c0_determinism_
attestation` 節を参照。

## 3. C1 — ControlProfile機構通過の副作用ゼロ確認

裁定逐語:

> C1 ZERO_CONTROLPROFILE_SHAMもFounderごとに20 takes実行する。
>
> C1のWAV bytesおよびidentity featureは
> C0/referenceと完全一致することを要求する。
>
> D_C1(F)=0×20を期待し、
> ControlProfile機構通過の副作用ゼロを確認する。
>
> 非ゼロの場合はC1_SHAM_EFFECT_DETECTEDとして停止する。

C1（`interventions.c1_sham_takes_per_founder`、PINNED、founder 1体あたり
n=20、無改変）も C0 と同型の exact-replay attestation として扱う。C0 と
同様 D_C1(F)=0×20 を期待し、非ゼロが検出された場合は
`C1_SHAM_EFFECT_DETECTED` として停止する（C1 gate は rev 0.6 では
theta_cal(F) 比較を行わない — 校正有効性ゲートとしての役割は 2 節同様
終了する）。詳細は `c1_sham_attestation` 節を参照。

## 4. Positive reference — 追加の exact replay 監査

裁定逐語:

> positive referenceは追加のexact replay監査として維持する。
> referenceとのWAV byte一致およびdistance=0を要求する。
> 独立した閾値校正標本としては扱わない。

positive reference（P0 cell 専用追加テイク、`identity_metric_space.json
calibration.validity_gates.positive_reference_gate.positive_reference_
definition` が定める既存の生成手続きは無改変のまま再利用）は、C0/C1 と
同じ exact-replay 監査の1形態として維持する——WAV byte 一致 + distance=0
を要求するのみで、独立した閾値校正標本としては扱わない。詳細は
`positive_reference_audit` 節を参照。

## 5. Birth Identity Separation — machine feature 判定への切替え

裁定逐語:

> 凍結済みP0 Neutral Identity Probeで、
>
> d12 = distance(R9F-01:r0, R9F-02:r0)
>
> を計算する。
>
> 両featureがvalid/finiteであり、d12 > 0の場合のみ、
> BIRTH = ESTABLISHED_BY_MACHINE_FEATURE
> とする。
>
> d12 = 0の場合は
> NOT_ESTABLISHED /
> PROJECTED_RUNTIME_IDENTITIES_COLLAPSED_IN_MACHINE_FEATURE_SPACE
> として凍結し、学習へ進まない。
>
> negative reference gateは同じd12を対称参照する。
>独立証拠として二重計上しない。

theta_cal(F) 依存の校正ゲートに代わり、Birth Identity Separation は
凍結済み P0 Neutral Identity Probe（`P0-NEUTRAL-SAKURA-FRAGMENT`、
無改変）上での二体間距離 `d12 = distance(R9F-01:r0, R9F-02:r0)` を直接
評価する machine feature 判定へ切り替える。両 founder の feature が
valid/finite かつ `d12 > 0` の場合のみ `BIRTH = ESTABLISHED_BY_MACHINE_
FEATURE`（既存 `BIRTH_OUTCOMES` の `"ESTABLISHED"` を精緻化する
outcome_detail、二層構造は「語彙の扱い」節を参照）とする。`d12 = 0` の
場合は `NOT_ESTABLISHED` として凍結し、`PROJECTED_RUNTIME_IDENTITIES_
COLLAPSED_IN_MACHINE_FEATURE_SPACE` を凍結理由として付記し、学習へは
進まない。negative reference gate（旧 `identity_metric_space.json
calibration.validity_gates.negative_reference_gate`）は同じ `d12` を
対称参照するのみであり、独立証拠として二重計上しない。詳細は
`birth_identity_separation` 節を参照。

## 6. PJS Confuser — 距離記録のみ、事後閾値の新設禁止

裁定逐語:

> 出生時に各Founderと凍結済みPJS referenceとの距離を記録する。
> distance=0の場合はPJS confuserとのfeature collapseとして
> BIRTH NOT_ESTABLISHEDとする。
>
> 正値の場合は距離・marginを記録するが、
> 事後的な最小距離閾値を新設しない。

`identity_metric_space.json confuser_control.pjs_reference_definition`
が定める決定論的 PJS コーパス集約（無改変）を用い、出生時に各 founder と
pjs_reference との距離を記録する。distance=0（feature collapse）の場合は
BIRTH NOT_ESTABLISHED とする。正値の場合は距離・margin を記録するのみで、
事後的な最小距離閾値は新設しない（confuser_control.evaluation の
「総合スコア化・閾値によるPASS/FAIL化はしない」規律を Birth 時点の
confuser 記録にも一貫して適用する）。詳細は `pjs_confuser` 節を参照。

## 7. 学習後 Identity 保持 — マージン方式への切替え

裁定逐語:

> 各学習後render rについて、
>
> d_self  = distance(r, own r0)
> d_other = distance(r, other Founder r0)
> d_pjs   = distance(r, PJS reference)
>
> m_other = d_other - d_self
> m_pjs   = d_pjs - d_self
>
> を計算する。
>
> m_other > 0 かつ m_pjs > 0 の場合のみ
> STABLE_BY_MACHINE_METRIC /
> RELATIVE_SELF_NEAREST
> とする。
>
> いずれかが0以下ならSHIFTEDとする。
> 同率をSTABLEへ丸めない。
>
> d_pjs(r_learned)-d_pjs(r0)も
> PJS方向への接近evidenceとして別記録する。

theta_cal(F) との単一距離比較（旧 `calibration.decision_rule`）に代わり、
学習後の各 render r について自己/他者/PJS の相対近さをマージンで評価する
方式へ切り替える: `d_self = distance(r, own r0)`、`d_other = distance(r,
other Founder r0)`、`d_pjs = distance(r, PJS reference)` を計算し、
`m_other = d_other - d_self`、`m_pjs = d_pjs - d_self` を導出する。
`m_other > 0` かつ `m_pjs > 0` の場合のみ `STABLE_BY_MACHINE_METRIC`
（既存 `IDENTITY_OUTCOMES` の値、無改変）+ `RELATIVE_SELF_NEAREST`
（outcome_detail、二層構造は次節参照）とする。いずれかが 0 以下なら
`SHIFTED`（既存 `IDENTITY_OUTCOMES` の値）とし、同率（マージン=0）を
STABLE 側へ丸めない（closed-above ではなく strictly-greater-than-zero、
両マージンとも）。`d_pjs(r_learned) - d_pjs(r0)` も PJS 方向への接近
evidence として、STABLE/SHIFTED 判定とは別に記録する（confuser_control
evidence-only 規律と同型 — 単独で判定を左右しない）。詳細は
`post_learning_identity_retention` 節を参照。

## 語彙の扱い（二層構造）

既存 frozen tuple（`run9_schema.BIRTH_OUTCOMES` / `SEPARATION_OUTCOMES`
/ `IDENTITY_OUTCOMES`）は本改訂で**無改変**のまま維持する。裁定が導入
する新ラベル（`ESTABLISHED_BY_MACHINE_FEATURE`・`PROJECTED_RUNTIME_
IDENTITIES_COLLAPSED_IN_MACHINE_FEATURE_SPACE`・`RELATIVE_SELF_NEAREST`・
`IMPLEMENTATION_FAILURE`/`DETERMINISM_CONTRACT_BROKEN`（C0 の停止語彙、
既存 `FAILURE_CLASSES` の `IMPLEMENTATION_FAILURE` は再利用・
`DETERMINISM_CONTRACT_BROKEN` は新規）・`C1_SHAM_EFFECT_DETECTED`）は、
`inputs/identity_decision_protocol_v0.6.json` が用いる protocol 側の
`outcome_detail` 語彙として新設し、既存語彙に**併記**する二層構造で
表現する（既存の `BIRTH_OUTCOMES`/`IDENTITY_OUTCOMES` を消費する既存
validator・harness の enum 契約は変更しない）。`run9_schema.py` の
`IDENTITY_PROTOCOL_*` 定数群がこの detail 層を凍結する。

## 8. Metric文書とGenomeの不変性

裁定逐語:

> 既存identity_metric_space.json、
> domains/identity_domain_run9_v1.json、
> 発行済みFounder Genome、coords、genome_id、
> speaker map、TRI_CROSSOVER/1.0は変更しない。
>
> identity_metric_space.jsonを直接編集して
> metric_space_shaとGenome IDを動かしてはならない。
>
> 新規identity_decision_protocol_v0.6.jsonを発行し、
> 既存metricのfeature/distance定義を参照した上で、
> 旧calibration/decision ruleをrev 0.6実行についてsupersedeする。
>
> 同protocolのraw SHA256をhypothesis_algebra_shaへPINNEDする。

`inputs/identity_metric_space.json`・`domains/identity_domain_run9_v1.json`
・発行済み Founder Genome（`founders/R9F-0{1,2}_genome.json`）・
`coords`・`genome_id`・speaker map（`inputs/speaker_map_manifest.json`）・
`TRI_CROSSOVER/1.0` はいずれも本改訂で 1 byte も変更しない
（`metric_space_sha` と `genome_id` を動かさないという直接の禁止も
含む）。新規 `inputs/identity_decision_protocol_v0.6.json` は、
`identity_metric_space.json` の feature/distance 定義（feature_extractor
/extraction_procedure/identity_feature/distance/confuser_control の
各節）を無改変のまま参照により有効とした上で、`calibration` 節
（freeze_threshold/validity_gates/decision_rule）のみを rev 0.6 実行に
ついて supersede する。同 protocol の raw byte sha256 を、契約の
`hypothesis_algebra_sha` 欄（旧 reason「H1-H6 の閾値校正が未実施」から、
本改訂により「rev 0.6 Identity decision protocol の pin 欄」へ用途確定
——旧 reason は履歴として append-only 保持）へ PINNED 化するのは本改訂の
直接の成果物である。

## 9. 実行順

裁定逐語:

> DESIGN_RUN9_REVISION_0.6.md、
> User裁定文書、
> identity_decision_protocol_v0.6.json、
> validator/loader、
> hypothesis_algebra_sha、
> 関連probe bridge・failure routingの更新が完了するまで、
> Birth Gate、learning_recipe_shaの最終build/PIN、
> LEARN_PERFORMANCEを開始しない。
>
> rev 0.6のBirth GateがPASSした場合のみ、
> learning recipe freezeおよび学習実行へ進む。

**本 PR（第2 PR、Phase 1）は上記6点の事前登録一式の実装までを範囲とし、
Birth Gate の実行自体は含まない**——裁定が「...更新が完了するまで、
Birth Gate...を開始しない」と明示的に時系列を分けているとおりの区切り
である。rev 0.6 の Birth Gate が PASS した場合のみ、learning recipe の
最終 freeze および LEARN_PERFORMANCE 実行へ進む。

## 10. 不変規律

裁定逐語:

> Birth Gate不成立時はNOT_ESTABLISHEDとして凍結する。
>
> 同attempt内で、
> Founder座標変更、
> speaker-map重み変更、
> Identity metric feature変更、
> 任意epsilon追加、
> 方式Bへの自動昇格を行わない。
>
> 方式Bが必要な場合は別design_revisionまたは別Runとする。

Birth Gate 不成立時は `NOT_ESTABLISHED` として凍結する。同一 attempt 内
で、Founder 座標変更・speaker-map 重み変更・Identity metric feature
変更・任意 epsilon 追加・方式Bへの自動昇格のいずれも行わない——これは
`inputs/speaker_map_manifest.json` `synthesis_formula.prohibited` の
「試聴後の重み調整」禁止・DESIGN_RUN9_REVISION_0.5.md「6. Birth Identity
Separation Gate は pin 後に別途実行」節の禁止規律と同じ規律を、rev 0.6
の Identity decision protocol 全体（Birth Gate + 学習後 Identity 保持
判定）へ拡張したものである。方式Bが必要な場合は別 design_revision または
別 Run とする（rev 0.5「7. 方式B・方式Cの扱い」節と同型の扱い）。

---

## design_revision 系譜（byte-pin sha256 記録）

| revision | 文書 | sha256（実バイト） |
|---|---|---|
| v0.1（正本、無改変） | `DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md` | `b1f6901c0ba8bcfcbd61170aa672c95e96a37d082fce5e3f12f245bc4faaae1e` |
| 0.2（無改変・存続） | `DESIGN_RUN9_REVISION_0.2.md` | `406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb` |
| 0.3（無改変・存続） | `DESIGN_RUN9_REVISION_0.3.md` | `b4f05cfbccb484a16a39b736086e989e1c953f295bda66970d491e4db5b94b04` |
| PoR 裁定ソース（無改変・byte-pin） | `POR_CONCEPT_ADJUDICATION_20260824.txt` | `56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007` |
| 派生設計変更メモ（無改変・byte-pin） | `DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt` | `a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091` |
| 0.4（無改変・存続） | `DESIGN_RUN9_REVISION_0.4.md` | `7bfefcf61886062511c30df92c25e597b7a4a7745037514ed4655a623e38df07` |
| AF0 runtime mapping 裁定ソース（無改変・byte-pin） | `USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt` | `07d932da7d60e0e5abf3011040228d47e0b027514a5d0b6d2c165e71d6c65426` |
| 0.5（無改変・存続） | `DESIGN_RUN9_REVISION_0.5.md` | `095ce77147e897473e8d87b474159c2ff4fdeb6684356cc03649f99a603cb2a9` |
| Identity Calibration Degeneracy 裁定ソース（無改変・byte-pin） | `USER_ADJUDICATION_20260827_IDENTITY_REV06.txt` | `43c7e71cd3bcb7cf3840c67a18e4a4c35a0259b9e04b1335868c33e925420db1` |
| 0.6（本文書、`design_revision_doc_sha256` が PINNED で保持する契約レベルの現行文書） | `DESIGN_RUN9_REVISION_0.6.md` | `RUN9_CONTRACT.yaml` の `design_revision_doc_sha256` が PINNED で保持する（本文書は本文書自身の sha256 を内部に書けないため実測は contract 側を正とする） |
