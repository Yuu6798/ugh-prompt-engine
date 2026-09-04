---
document_id: VG-METER-CAL-DEBT-DESIGN-v1.1
project: VoiceGenesis
document_class: CANONICAL_DESIGN_REVISION
status: APPROVED_DESIGN_REVISION / NOT_PREREGISTERED / EXECUTION_NOT_AUTHORIZED
revises: VG-METER-CAL-DEBT-DESIGN-v1.0
base_document_path: voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.0.md
base_document_sha256: 48e15e485301e5e686dfa96870d190ad08d2bc0a57e39d1971f13c9cf8426508
revision_rule: v1.0 は read-only。本書は v1.0 §0 の改訂規約（in-place 改変禁止・新 revision を append-only で作成）に従う新 revision であり、本書に明記した節のみ v1.0 を上書きする。明記のない全ての節は v1.0 が引き続き正。
evidence_basis:
  - campaign RUN10-CAL-20260904-862dec28 (CAMPAIGN_CLOSED, ledger seq 70627)
  - discarded campaign RUN10-CAL-20260903-591cadcd (abort record)
  - discarded campaign RUN10-CAL-20260903-9bcbbf86 (abort record)
authorship:
  design_compiler: CLAUDE (Fable session, 2026-09-04)
  approving_authority: USER (directive 2026-09-04: 設計 v1.1 を Design Memo から起こし再 freeze → 再 campaign を実行、D82/D87/D88/D90 を事前に修正)
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
note_on_execution: 実行は v1.0 §18 の 3 承認 Gate（campaign 実行 / C0 freeze / seal 受容）に引き続き従う。本書の承認は設計改訂の承認であり実行授権ではない。
---

# VoiceGenesis RUN10-CAL 設計改訂 v1.1 — split 頑健化・sweep-cluster 割当・c4 gate 配線・運用是正

## 0. 位置づけと改訂範囲

campaign `RUN10-CAL-20260904-862dec28`（2026-09-04 close、`debt_discharged = false`）の
実測観察と、freeze 後に境界宣言で繰り延べた修正候補（`UNDERSPEC-CAL-D82/D87/D88/D90`、
正本 = `README.md` 逸脱台帳）を、次 campaign の開始前に設計へ編入する。

本書が v1.0 を上書きするのは以下のみ:

| 節 | 上書き対象（v1.0） | 内容 |
|---|---|---|
| §V1 | §8（F0_CONTROL の selection / 共通 fail filter） | F0 C3a の negative control fail filter を control class で分割 |
| §V2 | §7（split 層別規則）+ §10.4 の holdout sweep 意味論 | TRUTH_CORE∧PRIMARY 行の sweep-cluster 割当 |
| §V3 | （v1.0 の変更ではなく実装欠落 D17 の閉塞） | c4 の実 gate 組み立て配線を campaign 成立要件に昇格 |
| §V4 | §13 運用（artifact layout の運用細則） | 破棄 campaign ledger の圧縮保全規約 |
| §V5 | （設計外・コード是正の記録） | D82/D87/D88/D90 の correction record |

上記以外の全て（D1–D3 裁定・語彙・C0 manifest・independence tier・456 セル行列・
repeat 構造・selection rule・誤差式・終端 status・M6・provenance schema・費用上限・
RUN11 Gate・§16 対象外事項・§18 承認 Gate）は v1.0 のまま不変。

## V1. F0_CONTROL C3a の negative control fail filter 分割（v1.0 §8 の部分改訂）

### V1.1 観察（一次事実、改訂の動機）

- C3a の negative control 評価集合は split 非依存（F0_CONTROL の negative control
  全 3 行 SILENCE / NOISE_ONLY / TOO_SHORT × 5 probe を、home split に依らず全件評価
  — `campaign/workunits.py` round 17 finding #1）。にもかかわらず選定結果は campaign 間で
  反転した: `591cadcd` では `F0-PYIN-FRAME2048-HOP256` のみ `negative_control_false_fire
  = False` で SELECTED、`862dec28` では全 5 候補 True で `SELECTION_FAILED_CLOSED`
  （両 ledger の `f0_selection_frozen` event 原文）。
- 反転の機序: render 音声は HKDF seed（campaign secret・split 割当を含む info）由来で
  campaign ごとに異なる。NOISE_ONLY の噪音実現に対する pyin 系候補の偽発火は確率事象で
  あり、any-fire ゼロ許容の binary filter を 5 probe の標本にかけると、選定可否が
  噪音実現の抽選で決まる。
- v1.0 §8 の内部矛盾: F0 selection criteria は「voiced false detection」を**順位基準**
  として宣言する一方、共通 fail filter「negative control 偽検出」は any-fire で候補を
  ineligible にする。前者は eligible 候補間で常に 0 となり、宣言された順位基準が
  構造的に無意味になっていた。

### V1.2 ruling

F0_CONTROL の C3a に限り、negative control を control class で 2 分する:

1. **決定論的縮退 control（SILENCE / TOO_SHORT / INVALID_SR、および噪音 seed に依存
   しない全 class）**: any-fire ゼロ許容の fail filter を維持する。これらへの発火は
   voicing 判定の破綻を意味し、確率事象ではない（split/seed 頑健）。
2. **噪音実現を持つ control（NOISE_ONLY）**: binary fail filter から除外し、検出率
   （instance 単位の検出数 / 全 instance 数）を v1.0 §8 が宣言済みの lexicographic
   基準「voiced false detection」として消費する（順位: cents error → octave-error
   rate → voiced false detection rate → process reproducibility。丸めは §9 の
   rate 系 0.001 刻み）。

適用範囲は **F0_CONTROL の C3a のみ**。他 family（C3b）の negative control fail filter、
claim-critical meter の gate 5（FDR0 = 0）、両側条件（§4.2）はいずれも不変。
probe repeat 数・456 セル・2,280 instance の会計も不変。HKDF stream 分離（§7）も不変。

### V1.3 正直性の宣言

本改訂は閉じた campaign の観察に動機づけられた設計変更であり、新 campaign のデータを
見る前に本書で凍結する（事前登録相当）。「候補を通すための緩和」ではなく v1.0 内部
矛盾の解消であることを、上記 V1.1 の一次事実とともに記録する。NOISE_ONLY 検出率が
高い候補が cents error で選ばれ得ることは設計上許容する（F0_CONTROL は claim-critical
外の上流 control であり、率は provenance に残る）。

## V2. sweep-cluster 割当（v1.0 §7 層別規則の部分改訂 + §10.4 holdout 意味論の確定）

### V2.1 観察（一次事実）

- D76（README 台帳）が確定したとおり、行単位の (block, domain) 層別 25% holdout では
  各 declared sweep の holdout 残存行が 1–2 行に薄まり、DIRECTIONAL ceiling 候補は
  構造的に `DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT` に倒れる（`862dec28` の
  M2_APERIODICITY、85/85 instances・10 sweep 中 9 が最小 3 ペア未満 = D77）。
- 行単位のまま stratum 因子に sweep を追加しても解決しない: 「全 declared sweep が
  holdout で truth level ≥ 3」を行単位で要求すると、truth-core 行の 50–100% が
  holdout に必要になり（例: FORMANT_GT 12 sweep × 3 行 = 36 行 > holdout 全 24 行）、
  25% split と算術的に矛盾する。

### V2.2 ruling — 割当単位の変更

TRUTH_CORE ∧ PRIMARY の行（= declared sweep の member。`fixtures.matrix.
declared_sweeps_by_family()` が正本）は、**行単位ではなく declared sweep 単位の
クラスタで split へ割当てる**:

- family ごとに sweep クラスタを `HMAC-SHA256(split_secret, sweep_id の canonical
  表現)` 昇順に並べ、largest-remainder で 50/25/25 へ配分（端数は v1.0 §7 の既存規約
  どおり HMAC 順位の偶奇で selection/holdout へ交互配分）。
- sweep 非所属行（confound / boundary / negative control / TRUTH_CORE∧BOUNDARY）は
  従来どおり行単位（v1.0 §7 不変）。
- 新 coverage 制約: **各 split は family ごとに declared sweep を最低 1 個持つ**。
  違反は sweep クラスタ単位の決定的最小 swap で修復し manifest に記録、修復不能は
  fail-closed（v1.0 §7 の swap 規約の cluster 版）。
- family 合計 50/25/25 の厳密一致は sweep 非所属行の再配分のみで補正する（クラスタを
  分断する片道移動は禁止）。
- 既存 coverage 制約（truth_level / generator_impl / boundary_class、TRUTH_CORE
  最低 2 行）は行数ベースのまま維持（各 sweep ≥ 3 行のため自動充足）。
- realized split map の正本形式（C0 manifest の row→split 表 + 検証器の機械照合）は
  不変。照合対象のアルゴリズムが本節の cluster 版になる。

### V2.3 ruling — holdout 上の DIRECTIONAL gate の評価対象 sweep

§10.4 の「resolvable pair は各 sweep で >= 3」の holdout 評価は、**member 全行が
HOLDOUT に属する declared sweep**（= V2.2 により必ず sweep 全体が単一 split に完結
する）を対象として行う。C4 の DIRECTIONAL 容量検査の expected sweep 集合も同じ定義
に従う（従来の「全 declared sweep」は V2.2 の下では holdout 非常駐 sweep を含み
評価不能を強制するため）。member が split を跨ぐ declared sweep は V2.2 の割当では
発生し得ず、検出した場合は割当実装の欠陥として fail-closed する。

selection 側（C3b の DIRECTIONAL 順位基準）も selection 常駐 sweep を full truth
level で持つことになるが、selection rule（v1.0 §9）の式・順序は不変。

### V2.4 帰結の宣言

- 456 セル・2,280 instance・render/meter call 会計は不変（割当の単位だけが変わる）。
- F0_CONTROL は 3 sweep のため calibration/selection/holdout に各 1 sweep（truth-core
  4 行ずつ）となる。positive control instance は split あたり 4 行 × 5 probe = 20
  ≥ 10（§10.1 最小数）で充足。
- holdout 常駐 sweep の resolvable 性は依然 `Delta_truth > R_ij` の実測に従う。本改訂は
  「構造的に評価不能」を除去するだけで、評価結果を保証しない。

## V3. c4 実 gate 組み立ての配線（D17 の閉塞。campaign 成立要件への昇格）

### V3.1 観察（一次事実）

`campaign/cli.py` の C4 は、被覆完全時に `[UNDERSPEC-CAL-D17]`（実 gate 組み立ては
D2 infra scope 外）の placeholder として全 meter を `DIAGNOSTIC_ONLY` で閉じる。実 gate
（`holdout_stage.evaluate_absolute_meter` / `evaluate_directional_meter`、テストでは
実配線で検証済み）は campaign 実行経路から一度も呼ばれない。したがって **現行実装の
まま何度 campaign を再実行しても `CALIBRATED_ABSOLUTE` / `CALIBRATED_DIRECTIONAL` には
構造的に到達せず、`DEBT_DISCHARGED` は恒久に false** である。

### V3.2 ruling

次 campaign の C0 freeze までに、C4 実行経路へ実 gate 組み立てを配線することを
campaign 成立要件とする:

- ABSOLUTE effective ceiling の meter → `evaluate_absolute_meter`（E_use table・
  manifest 凍結の U_GT/U_num・holdout 実測からの U_rep/U_proc・宣言 invariance 軸・
  gate 5 の control instance を v1.0 §10.3 どおり配線）。
- DIRECTIONAL effective ceiling の meter → `evaluate_directional_meter`
  （V2.3 の holdout 常駐 declared sweep 単位、v1.0 §10.4 どおり）。
- M6 は CLAIM_CRITICAL_SET 全 member が CALIBRATED_ABSOLUTE の場合のみ §12 の実評価を
  配線。precondition 不成立は NOT_EVALUABLE（不変）。
- M4 は §16-1 どおり全候補 DIAGNOSTIC_ONLY 固定（不変）。F0_CONTROL は gate 評価外
  （上流 control、不変）。
- gate 入力の欠落は fail-closed（`NOT_EVALUABLE / INPUT_MISSING`）とし、placeholder に
  よる無言の DIAGNOSTIC_ONLY へ戻さない。gate が正直に fail した場合の
  `DIAGNOSTIC_ONLY` / `NOT_EVALUABLE` は正当な終端であり、通過のための閾値調整は
  §10.2（結果後付けの禁止）により引き続き禁止。

## V4. 破棄 campaign ledger の圧縮保全（運用規約）

- 破棄（abort）裁定済み campaign の `ledger.jsonl` は、非圧縮バイト列の SHA-256 を
  sidecar `ledger.jsonl.sha256` に記録した上で gzip（`ledger.jsonl.gz`）へ置換して
  保全する。原本同一性は `zcat ledger.jsonl.gz | sha256sum` と sidecar の照合で機械
  検証できる（chain 検証は伸長後に従来どおり可能）。
- 既存の破棄 campaign `RUN10-CAL-20260903-9bcbbf86` / `RUN10-CAL-20260903-591cadcd`
  に本規約を遡及適用する。
- **閉鎖（CAMPAIGN_CLOSED）campaign の凍結ディレクトリは不変のまま**（本規約の対象外。
  `862dec28` は 1 バイトも変更しない）。
- git-lfs は導入しない（実行環境に未導入・proxy 制約。50 MB 超の非圧縮 ledger を
  新規 commit しないことを運用で担保する）。

## V5. correction record — D82 / D87 / D88 / D90（コード是正の設計追認）

以下は v1.0 の設計意味論を変えないコード是正であり、freeze 規律（freeze 後の校正
コード変更 = 新 campaign）により前 campaign 中は境界宣言で繰り延べたもの。次 campaign
の C0 freeze までに適用する。正本の欠陥記述は `README.md` 逸脱台帳の各 entry。

- **D82**: `_build_f0_by_instance()` の重複 repeat key fail-closed 経路に説明用
  `stop_event`（append → raise、閉語彙 `BlockedCode` へは追加しない）。
- **D87**: cap 会計の順序統一 — pre-dispatch CPU（campaign ロード・全 chain 検証）の
  課金、c1/c2/unseal の遷移前 cap 再検査、summary 直前の最終 delta 計上。ledger
  summary append → counters cache 保存の権威順序は不変。
- **D88**: replay 検証器に Gate 3 順序 parity（`c0_freeze` event 時刻 < gate3
  `approved_at_utc` ≤ `holdout_unseal` event 時刻、fail-closed）を追加。空行のみの
  検証済み ledger への正当 append を拒否する偽失敗（genesis watermark の縮退比較）を
  修正。
- **D90**: `f0_injection_rejected` の resume 跨ぎ重複記帳を、既記帳 instance との
  差集合記帳（空なら無記帳）で排除。

## V6. 統治文書の切替

本 revision の発効後、Gate 承認（v1.0 §18）が pin する設計文書は本書
（`DESIGN_VG_METER_CAL_DEBT_v1.1.md`）とする（`approvals.py` の
`DESIGN_DOC_RELATIVE_PATH` を本書へ更新）。v1.0 は read-only の基底文書として保存し、
本書に明記のない全ての節の正であり続ける。

## V7. 裁定

```yaml
status: APPROVED_DESIGN_REVISION / NOT_PREREGISTERED
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
next_actions:
  - V5 の是正と V1–V3 の実装を C0 freeze 前に適用
  - 実行へ進む場合のみ v1.0 §18 の 3 承認 Gate を順に処理（Gate 3 は armed freeze 後）
  - 結果は改竄せず記録する（gate の正直な fail は正当な終端）
```
