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
  design_compiler: "CLAUDE (Fable session, 2026-09-04)"
  approving_authority: "USER (directive 2026-09-04: 設計 v1.1 を Design Memo から起こし再 freeze → 再 campaign を実行、D82/D87/D88/D90 を事前に修正)"
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
| §V2 | §7（split 層別規則）+ §10.4 の holdout sweep 意味論 + **§12/§13 の claim 適用範囲**（M6 および DIRECTIONAL 終端 status の claim を holdout 評価済み範囲へ縮小、claim text / prohibited interpretations 欄の必須記載を追加） | holdout sweep pinning + claim 被覆の正直化 |
| §V3 | （v1.0 の変更ではなく実装欠落 D17 の閉塞） | c4 の実 gate 組み立て配線を campaign 成立要件に昇格 |
| §V4 | §13 運用（artifact layout の運用細則） | 破棄 campaign ledger の圧縮保全規約 |
| §V5 | （設計外・コード是正の記録） | D82/D87/D88/D90 の correction record |

上記以外の全て（D1–D3 裁定・語彙・C0 manifest・independence tier・456 セル行列・
repeat 構造・selection rule・誤差式・終端 status cascade・provenance schema の欄構成・
費用上限・RUN11 Gate・§16 対象外事項・§18 承認 Gate）は v1.0 のまま不変。
§15（RUN11 Hard Claim-Dependency Gate）の**文言と凍結は不変**であり、§V2 の claim
縮小はそれを緩めない（Codex レビュー第 6 巡 P1 採用で明確化、2026-09-04）: §15 は
「claim 削除・縮小は新 preregistration + ユーザー承認」を明文で要求するため、
**縮小後の claim を RUN11 で用いるには、その縮小 claim 自体の新 preregistration と
User 承認が別途必要**である。本 campaign の終端 status だけで §15 条件 (3) を充足
したとは扱わず、RUN11 は引き続き凍結を維持する。本書はその preregistration を
授権しない。

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

### V2.2 ruling — holdout sweep pinning（2 段割当）

**改訂履歴**: 本節の初版は TRUTH_CORE∧PRIMARY 行を 3 split すべてへ sweep クラスタ
単位で配分する案だったが、Codex レビュー第 1 巡（P1、2026-09-04）が
APERIODICITY_GT での厳密実行不能を指摘し（60 truth 行 = 6 行量子 × 10 クラスタ、
family 厳密枠 36/18/18 と「各 split ≥ 1 BOUNDARY 行」coverage の下で
k1 ≤ 5, k2 ≤ 2, k3 ≤ 2 → Σ ≤ 9 < 10）、検算の結果採用。欠陥の実体は holdout の
sweep 薄まりのみであるため、クラスタ化を **HOLDOUT だけ**に縮小した 2 段割当へ
差し替えた。

**段 1 — holdout sweep pinning（層内選抜）**: family ごとに、declared sweep
（`fixtures.matrix.declared_sweeps_by_family()` が正本、member 行数 r・sweep 数 S）
から `k_hold` 個を **HOLDOUT 専属 sweep** として pin し、その member 全行を HOLDOUT
へ割当てる。選抜は一括の HMAC 順位ではなく **sweep stratum 内**で行う（Codex
レビュー第 2 巡 P1 × 2 採用、2026-09-04 — 一括順位は (a) FORMANT_GT で pin 3 個が
全て同一 generator_impl になり得て「各 split に各 generator_impl ≥ 1」coverage が
secret 依存で充足不能になる条件付き実行不能、(b) IDENTITY_CAUSAL_SWEEP で pin 3 個
が trait を欠き得て、無被覆 trait の M6 成分に holdout 証拠ゼロのまま directional
主張が立つ経路を作る）:

- **sweep stratum key**（C0 で凍結）: 当該 family の held-fixed field のうち、
  (a) coverage 軸に該当するもの（`generator_impl`）と (b) claim 構成次元
  （IDENTITY_CAUSAL_SWEEP の `trait` と `founder_id` — founder 次元の追加は Codex
  レビュー第 3 巡 P1 採用、2026-09-04: trait のみの層化では pin 3 個が単一 founder に
  集中し得て、無被覆 founder に holdout 証拠ゼロのまま M6 directional 主張が立つ）。
  該当 field が無い family は単一 stratum。456 セル canonical matrix では
  FORMANT_GT = generator_impl 2 strata（各 6 sweep）、IDENTITY_CAUSAL_SWEEP =
  founder_id × trait の 12 cell（各 1 sweep）、他 5 family = 単一 stratum。
- **被覆要件**: pin された sweep 集合は、各 stratum-key field の**全ての値**を最低
  1 回含むこと。これを満たすため `k_hold` は
  `min( max( floor(0.25*S + 0.5), 1, max_field_cardinality ), floor((N_hold-1)/r) )`
  とし（max_field_cardinality = stratum-key field の値数の最大。IDENTITY は
  founder 4 値 → k_hold = 4）、被覆要件が cap `floor((N_hold-1)/r)` 内で充足不能な
  family 構成は C0 validation で fail-closed（456 セルでは FORMANT k=3 ≥ 2、
  IDENTITY k=4 = cap 4 で発生しない）。
- **選抜規則**: 単一 field の family（FORMANT_GT）は field 値ごとの largest-remainder
  配分（全値 ≥ 1、同点は値の字句順）+ stratum 内 `HMAC-SHA256(split_secret,
  sweep_id の canonical 表現)` 昇順。複数 field の family（IDENTITY_CAUSAL_SWEEP）は
  決定論的被覆選抜: founder を `HMAC-SHA256(split_secret, founder_id)` 昇順に並べ、
  trait を `HMAC-SHA256(split_secret, trait)` 昇順に並べ、i 番目（i = 0..k_hold-1）の
  founder に trait `i mod 3` の sweep を割当てる（4 pin で全 founder × 全 trait を
  被覆。各 (founder, trait) cell は sweep 1 個なので一意に定まる）。
- **claim-relevant 次元の一般規則**（Codex レビュー第 5 巡 P1 採用、2026-09-04 —
  IDENTITY 特例の一般化）: 各 family について、declared sweep を区別する held-fixed
  field のうち **construct の適用範囲を分割するもの**（= claim-relevant field。例:
  TRANSITION_GT の `join_type`/`duration_class`、APERIODICITY_GT の bandwise band、
  IDENTITY_CAUSAL_SWEEP の `founder_id`/`trait`。nuisance 設定のみが変動する family
  は該当なし、§16-1 で DIAGNOSTIC_ONLY 固定の RESONANCE_GT は claim を持たない）を
  C0 で宣言・凍結し、c0_validate が matrix 実体からの機械導出値と照合する。pin 選抜
  は claim-relevant field の値多様性を最大化する決定論的ラウンドロビン（値組を
  HMAC 昇順に巡回し、各値組グループ内は HMAC 昇順）で行う（456 セルでの帰結:
  TRANSITION_GT は k=2 で join_type 4 値中 2 値のみ評価。APERIODICITY_GT も k=2 で
  band の一部のみ評価。FORMANT_GT / IDENTITY は stratum-key field の全値被覆が成立）。
- **claim の評価済み文脈への一律縮小**（Codex レビュー第 6 巡 P1 採用で一般化、
  2026-09-04 — claim-relevant 次元に限らず、nuisance 設定のみが sweep 間で変動する
  family（例: TILT_GT の f0_hz × sr_hz 文脈 6 sweep 中 k=2）でも、評価されなかった
  文脈への無宣言外挿は承継 §10.4 の「全 declared sweep」要求より弱い主張を黙って
  通すことになる）: **全 family の DIRECTIONAL 終端 status について**、claim text
  には holdout 評価済み sweep（その held-fixed 文脈の値組）を機械可読で列挙し、
  prohibited interpretations に「非評価 sweep 文脈への directional 外挿」の禁止を
  必ず含める。claim-relevant field の全値被覆が成立する family（FORMANT / IDENTITY
  の周辺被覆）でも、列挙義務は同様に課される（被覆成立は claim の広さではなく
  選抜の質の保証にすぎない）。

```
k_hold(family) = min( max( floor(0.25 * S + 0.5),      # 25% 目標の half-up 丸め
                           1,                          # 最低 1
                           max_field_cardinality ),    # 被覆要件（下記）: stratum-key
                                                       # field の値数の最大
                      floor((N_hold - 1) / r) )        # 非 sweep 行 ≥ 1 の枠を保証
```

（被覆要件が cap 項 `floor((N_hold - 1) / r)` を超える場合は C0 fail-closed。
IDENTITY_CAUSAL_SWEEP は S=12, N_hold=24, r=5, max_field_cardinality=founder 4 値
→ k_hold = min(max(3, 1, 4), 4) = 4。）

**縮退規則**（2026-09-04 追補 — CI 実測: pin 機構を縮小合成 matrix に適用すると
secret の引きに依存して C0 freeze が確率的に失敗する欠陥が露呈した。正典 456 セル
では顕在化しないが、割当は任意の有効 matrix に対して決定論的に全域で定義されて
いなければならない）:

- `cap < 1`（holdout が sweep 1 本 + 非 sweep 行 1 行を収容できない family）は
  **pin 免除**: k_hold = 0、当該 family は段 2 の行単位割当のみ（v1.0 挙動）。
  DIRECTIONAL holdout 評価は「宣言 sweep なし → 防御的 fail-closed」の既存意味論
  （D74）に従い正直に評価不能へ倒れる。
- 段 2（既存 coverage 制約の修復）が pin 選抜の結果として修復不能になった場合は、
  **k_hold を 1 ずつ決定論的に縮退**して段 1 を再選抜し、段 2 を再試行する。縮退の
  下限は claim 被覆 family（max_field_cardinality > 1）では max_field_cardinality
  （これ未満へは縮退せず C0 fail-closed — R2/R4 巡で採用した被覆保証を静かに
  弱めない）、それ以外の family では 0（= pin 免除）。実現された k は manifest の
  `holdout_sweeps` の宣言数として自動的に記録され、c0_validate の再導出照合は
  同一の縮退規則を再実行して一致を検査する。
- 正典 456 セル matrix では全 family が満額 k_hold で feasible（§V2.2 の表の
  とおり）であり、本縮退規則は挙動を変えない。

（N_hold = §5.2 の family holdout 目標行数。式の完全形は下記「被覆要件」の
max_field_cardinality 項を含む。456 セル canonical matrix での値:
F0_CONTROL k=1 / FORMANT_GT k=3 / TILT_GT k=2 / APERIODICITY_GT k=2 /
RESONANCE_GT k=2 / TRANSITION_GT k=2 / IDENTITY_CAUSAL_SWEEP k=4（founder 被覆、
第 3 巡改訂）。各 family とも `N_hold - k*r >= 1` で boundary/negative 行の
holdout 枠が残る — IDENTITY は 24 - 20 = 4。）

**段 2 — 残余の行単位割当（v1.0 §7 の既存機構）**: pin されなかった全行
（残りの truth-core 行・confound・boundary・negative control）は従来どおり行単位の
層別 largest-remainder + coverage 制約 + 決定的最小 swap で割当てる。ただし:

- TRUTH_CORE stratum の HOLDOUT 枠は段 1 の pin 行で**全量を構成**する（pin 外の
  truth-core 行の holdout 割当は 0。部分 sweep が holdout に混入しない）。
- family 合計 50/25/25 の厳密一致（v1.0 §7）は不変 — 段 2 の largest-remainder が
  pin 済み行数を既割当として控除した目標で走る。
- swap 修復は pin 行を不動とする（pin 行を動かす修復は禁止、修復不能は fail-closed）。
- 既存 coverage 制約（truth_level / generator_impl / boundary_class、TRUTH_CORE
  最低 2 行）は不変。HOLDOUT の TRUTH_CORE 最低 2 行は pin sweep（r ≥ 3）で自動充足。

realized split map の正本形式（C0 manifest の row→split 表 + 検証器の機械照合）は
不変。pin された sweep_id 一覧は manifest の**トップレベル非 core キー**
`holdout_sweeps` として凍結する（**実装時訂正、2026-09-04**: 本節の初版は
`frozen_design.fixture_spec.<FAMILY>.holdout_sweeps`（core 節 = `manifest_core_sha`
対象）と規定したが、pin 選抜は `split_secret` に依存し、secret は Gate 2 の
`manifest_core_sha` 束縛検証を通した armed freeze 後にのみ生成されるため、core 節
への格納は「承認対象の core に承認後にしか存在しない値を含める」ハッシュ循環で
構造的に不能。`realized_split`/`realized_split_sha` と同格の非 core キーとし、
`manifest_sha`（full manifest、`c0_freeze` event に記録）には含める。整合性は
(i) armed freeze と staging 読み戻しの 2 箇所での secret 依存完全再導出照合、
(ii) secret 非依存の構造検査（宣言 sweep の declared_sweeps 実在・member 一致・
k_hold 一致）を validate が常時実行、(iii) §V2.3 の realized membership 検査、の
3 層で担保する。secret 非依存の `claim_relevant_fields` は当初どおり
`frozen_design.fixture_spec.<FAMILY>` = core 節に置く）。

### V2.3 ruling — holdout 上の DIRECTIONAL gate の評価対象 sweep

§10.4 の「resolvable pair は各 sweep で >= 3」の holdout 評価は、**段 1 で pin した
HOLDOUT 専属 sweep**（= manifest の `holdout_sweeps`）を対象として行う。C4 の
DIRECTIONAL 容量検査の expected sweep 集合も同じ定義に従う（従来の「全 declared
sweep」は holdout 非常駐 sweep を含み評価不能を強制するため）。realized split 上で
`holdout_sweeps` の member に HOLDOUT 非所属行が 1 行でもあれば、割当実装の欠陥として
fail-closed する。

selection 側（C3b）の行構成と selection rule（v1.0 §9）の式・順序は不変（前 campaign
で selection は sweep 薄化の下でも成立しており、欠陥は holdout 側にのみ実測された）。

### V2.4 帰結の宣言

- 456 セル・2,280 instance・render/meter call 会計・family 合計 50/25/25 は不変
  （変わるのは holdout 内の truth-core 行の選ばれ方だけ）。
- holdout の pin sweep は各 family とも truth level 3–6 水準を全保持し、
  `C(r_truth, 2) >= 3` の構造条件を満たす。resolvable 性は依然
  `Delta_truth > R_ij` の実測に従う — 本改訂は「構造的に評価不能」を除去するだけで、
  評価結果を保証しない。
- positive control instance の最小数（§10.1 の N_pos >= 10）は、HOLDOUT が pin sweep
  の r × 5 probe（最小 F0_CONTROL の 4 × 5 = 20）、SELECTION は従来どおり行単位割当
  + TRUTH_CORE 最低 2 行 coverage で充足。
- 層内選抜により、HOLDOUT は FORMANT_GT の両 generator_impl の truth 行と、
  IDENTITY_CAUSAL_SWEEP の全 3 trait・全 4 founder の sweep を必ず 1 個以上持つ
  （M6 の claim 構成次元のいずれかに holdout 証拠が存在しない状態での directional
  主張は構造的に発生しない）。
- **IDENTITY の交互作用被覆の正直な限定**（Codex レビュー第 4 巡 P1 採用、
  2026-09-04）: 12 の founder × trait cell（各 5 行）を 24 行の holdout に全て
  収めることは §5.2 の枠組み上不可能であり、pin される 4 cell は founder・trait を
  **周辺被覆**するが**交互作用は被覆しない**。よって IDENTITY_CAUSAL_SWEEP /
  M6 の holdout directional 証拠の適用範囲は **pin された (founder, trait) cell に
  限定**して主張する: 終端 status の claim text は評価済み cell を機械可読で列挙し、
  prohibited interpretations に「非評価 (founder, trait) 組合せへの directional
  外挿」を必ず含める（v1.0 §13 の claim text / prohibited interpretations 欄を使用。
  §12 の distinctness 前提「事前 causal sweep の全 resolvable pair での directional
  gate 成立」における「causal sweep」は、本改訂下では holdout 評価済み cell の
  sweep を指し、claim もその範囲に縮小される）。この限定は v1.0 実現状態
  （holdout 常駐 sweep が構造的に 0 個 = directional 証拠ゼロ）に対する厳密な改善
  であり、24 行 holdout という凍結枠の帰結として正直に記録する。被覆拡大
  （§5.2 の holdout 枠拡張）は次期改訂の設計候補であり本 campaign では行わない。

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
- 欠落の終端写像は **v1.0 §11 の一意写像に完全に従う**（Codex レビュー第 3 巡 P1
  採用で明確化、2026-09-04 — 本 ruling は §11 を一切変更しない）: C0 **入力側**の
  critical 欠落（E_use evidence 行の欠落等）→ `NOT_EVALUABLE / INPUT_MISSING`。
  測定 **output** の全欠損・最小数割れで score/gate 計算不能 →
  `NOT_EVALUABLE / OUTPUT_NOT_EVALUABLE`。score 計算可能だが PRIMARY 一部 output
  missing で gate 不通過 → `DIAGNOSTIC_ONLY / OUTPUT_MISSING`（D66–D77 家系で実装済み
  の被覆分類のまま）。いずれの場合も placeholder による無言の DIAGNOSTIC_ONLY へ
  戻さない。gate が正直に fail した場合の `DIAGNOSTIC_ONLY` / `NOT_EVALUABLE` は
  正当な終端であり、通過のための閾値調整は §10.2（結果後付けの禁止）により引き続き
  禁止。

### V3.3 追補 — U_GT / U_num の C0 凍結（実装時発見、2026-09-05）

§V3.2 の配線実装（commit c8dbd5b）で、現行 `c0_freeze` が v1.0 §10.2 の
`U_GT[i]`（generator truth の保守上限）と `U_num[i]`（PCM 量子化・浮動小数・宣言分解能
からの機械導出）を manifest に一切凍結していないことが判明した（`862dec28` の
`c0_manifest.json` 実測）。実 gate はこれを `NOT_EVALUABLE / INPUT_MISSING` として
正直に扱うため、**凍結しない限り全 ABSOLUTE gate が入力欠落で終端し debt は構造的に
返済不能**である。よって次 campaign の C0 freeze までに以下を凍結対象へ追加する
（`frozen_design.fixture_spec.<FAMILY>.u_gt_bound` / `.u_num_bound`。値と導出式文字列
を併記し `manifest_core_sha` に含める）:

- **U_num[construct]** = `tolerance.derive_floor(pcm_term, float_term,
  meter_declared_resolution)`（既存の機械導出。3 項の max）。`pcm_term` は 16-bit PCM
  量子化（−96 dBFS 相当の加法雑音、宣言 gain に対して相対化）の当該 construct 単位への
  伝播量を family ごとの閉形式で与える（式は manifest に文字列で記録）。`float_term` =
  float64 eps × |truth|。`meter_declared_resolution` は選抜候補の parameter JSON が
  分解能を宣言していればその値、無ければ 0（`TOLERANCE_FLOOR_LIMITED` と同型の付記）。
  U_num は gate に加算的に入るため過大は fail 側（保守方向）であり許容、過小は禁止。
- **U_GT[family]**（generator truth の保守上限、fixture 行単位）:
  - F0_CONTROL / FORMANT_GT / TILT_GT: 真値は float64 の解析的合成で実現されるため
    `U_GT = 0`（残差は `float_term` が吸収する旨を導出式に明記）。
  - TRANSITION_GT: `join_time_s` は sample 境界に量子化されるため `U_GT = 0.5 / sr_hz`
    [s]。`discontinuity_magnitude` は解析的指定で `0`。
  - APERIODICITY_GT: 有限長雑音の実現パワーは宣言 fraction の周りで χ² 分布に従って
    揺らぐため、`U_GT = fraction × 3 × sqrt(2 / N)`（N = duration_s × sr_hz、3σ 相対
    偏差の保守上限）。
  - RESONANCE_GT: §16-1 で DIAGNOSTIC_ONLY 固定のため gate 入力にならない（記録は
    `ABSENT:diagnostic_only`）。IDENTITY_CAUSAL_SWEEP: 物理 GT なし（§4.2）=
    `ABSENT:no_physical_ground_truth`。
- c0_validate は非 ABSENT family について両キーの存在・有限非負・導出式文字列の存在を
  検査し、欠落は `BLOCKED_C0_MANIFEST_INCOMPLETE` 経由で fail-closed（閉語彙は不変）。

### V3.4 追補 — M6 の測定経路は v1.2 へ繰延（境界宣言、2026-09-05）

§12 の `m6_distance()` は IDENTITY_CAUSAL_SWEEP の founder pair に対する
CLAIM_CRITICAL_SET 3 meter の実測 component vector と null pair 母集団を要求するが、
現行 campaign は IDENTITY 行を claim-critical meter で測定しておらず（family→meter
写像に IDENTITY が無い）、「どの行を A/B とするか」「null pair をどう構成するか」の
実験設計も v1.0/v1.1 で操作的に規定されていない。M6 は CLAIM_CRITICAL_SET 外
（`DEBT_DISCHARGED` に無関係）で RUN11 の独立 conjunct であるため、本 campaign では
precondition 判定のみ実評価とし、結果は正直な `NOT_EVALUABLE / INPUT_MISSING`
（`gate_detail` に境界宣言と pin 済み cell 列挙）で閉じる。cross-family 測定ユニットと
pair 設計の規定は **v1.2 の設計課題**として登録し、本 campaign の close はこれを待たない。

### V3.5 追補 — gate 4' invariance 軸の宣言と pair 構成（Codex レビュー第 12 巡 P1 採用、2026-09-05）

実 gate 配線後の監査で、現行 C0 が全 family に同一の 6 軸（f0_hz / sr_hz / gain /
duration / noise / context）を invariance 軸として一律宣言する一方、正典 456 セル
行列には f0_hz・sr_hz を名指す単一軸 CONFOUND 行が無く、targeted interaction 行は
単一軸 tag を持たないため、gate 4' の pair が当該軸で常に 0 件となり、**全 ABSOLUTE
候補が「軸あたり 5 pair 未満」で構造的に偽失敗する**ことが判明した。v1.0 §10.1 の
「truth 自体が変わる軸は invariance 対象に混ぜない」「invariance 軸ごとに >= 5 pairs」
を、行列の実体に接地して次のとおり運用化する:

- **宣言軸の機械導出**: family の invariance 軸は、当該 family の CONFOUND block に
  **単一軸の主効果行（`nuisance_tag` が 1 軸を名指す行）が存在する軸**のみを C0 で
  宣言する（family の truth 軸は除外。例: F0_CONTROL の f0_hz は truth）。宣言は
  `fixtures.matrix` からの機械導出値とし、c0_validate が manifest 宣言との完全一致を
  照合する（D77 の declared_sweeps と同型）。targeted interaction 行（2 軸同時摂動）は
  単一軸 invariance の pair に用いず、failure boundary 診断（§10.1）の材料に留める。
- **pair の単位は instance**: invariance pair は `(anchor instance, 同 probe_index の
  単一軸変異 instance)` で数える（probe repeat は独立 seed の別実現であり、§6 の
  「probe repeat = 分散推定の単位」と整合）。1 holdout 行 = PROBE_REPEATS(5) pair。
- **coverage 制約の追加**: (family, 宣言 invariance 軸) ごとに、各 split が単一軸
  CONFOUND 行を最低 1 行持つことを段 2 の coverage 制約に加える（`_COVERAGE_AXES`
  へ nuisance 軸キーを追加。既存の swap 修復で充足、不能は fail-closed）。これにより
  holdout で各宣言軸が ≥ 5 pair（1 行 × 5 probe）を構造的に持ち得る。
- **anchor の共有測定**（Codex レビュー第 15 巡 P1 採用、2026-09-05）: pair の
  anchor 側（family の指定 positive anchor 行）が HOLDOUT 以外の split に home すると
  pair が全滅する。指定 anchor 行は negative control と同じく **split 非依存の共有
  control**（v1.0 §2.7 の control 共有契約の同型）として扱い、C4 で選抜候補により
  HOLDOUT 変異行と同一 probe_index で測定して pair を構成する（split 割当・leakage
  規則は不変 — anchor 行は C1 で全 split 分 render 済みであり、HOLDOUT 行の unseal 前
  露出は生じない）。
- **§10.1 の意味論は不変**: 未達軸が 1 つでもあれば ABSOLUTE 不可（実測で pair が
  欠落した場合は従来どおり正直に fail）。本追補は「宣言と行列の不整合による構造的
  偽失敗」だけを除去する。

### V3.6 追補 — control 出力欠落の分子算入（同 P1 採用、2026-09-05）

v1.0 §10.1「control 出力の missing/invalid は分子に算入（分母から除外しない）」を
gate 5 の実装契約として再確認する: negative control instance の repeat に
missing/invalid があれば当該 instance は **偽検出（失敗）側**に数え、positive control
の missing/invalid は **不発火（失敗）側**に数える。欠落を「非検出 = 成功」に写像する
実装は禁止（偽 CALIBRATED 経路）。

## V4. 破棄 campaign ledger の圧縮保全（運用規約）

- 破棄（abort）裁定済み campaign の `ledger.jsonl` は、非圧縮バイト列の SHA-256 を
  sidecar `ledger.jsonl.sha256` に記録した上で gzip（`ledger.jsonl.gz`）へ置換して
  保全する。原本同一性は `zcat ledger.jsonl.gz | sha256sum` と sidecar の照合で機械
  検証できる（chain 検証は伸長後に従来どおり可能）。
- **置換は原子的に行う**（Codex レビュー第 5 巡 P1 採用、2026-09-04 — abort record
  は当該 campaign の唯一の正本であり、部分置換はそれを破損する）: (1) 同一
  ディレクトリ内の staging 名で `ledger.jsonl.gz` と sidecar を書き、(2) staging の
  gz を実際に伸長して sidecar の sha256 および原本バイト列と一致すること・伸長結果の
  ledger chain が verify を通ることを確認し、(3) fsync 後に rename で公開してから
  (4) 原本を削除する。中断からの回復規則: 公開済み gz + sidecar が検証を通る場合
  のみ残存原本を除去してよく、それ以外は原本を正とし staging/部分成果物を破棄する。
  この手順は故障注入（各段階間での中断を模す）テストで検証する。
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
- **D88**: replay 検証器に Gate 3 順序 parity を追加 — `c0_freeze` event 時刻 <
  gate3 `approved_at_utc` ≤ `holdout_unseal` event 時刻 **+ 許容 clock skew**
  （producer 側 `unseal_campaign()` が受容する `_CLOCK_SKEW_TOLERANCE_SECONDS` =
  60s と同一の共有定数。Codex レビュー第 3 巡 P2 採用: producer は +60s までの
  未来日付 Gate 3 を受容して直後に local clock で `holdout_unseal` を記帳するため、
  厳密不等式は正常経路の ledger を偽拒否する）。fail-closed。空行のみの検証済み
  ledger への正当 append を拒否する偽失敗（genesis watermark の縮退比較）を修正。
- **D90**: `f0_injection_rejected` の resume 跨ぎ重複記帳を、既記帳 instance との
  差集合記帳（空なら無記帳）で排除。

## V6. 統治文書の切替

本 revision の発効後、Gate 承認（v1.0 §18）が pin する設計文書は本書
（`DESIGN_VG_METER_CAL_DEBT_v1.1.md`）とする（`approvals.py` の
`DESIGN_DOC_RELATIVE_PATH` を本書へ更新）。v1.0 は read-only の基底文書として保存し、
本書に明記のない全ての節の正であり続ける。

**基底文書の実行時 pin（Codex レビュー第 1 巡 P2 採用、2026-09-04）**: 本書だけを
pin すると、承継元 v1.0 が承認後・freeze 後に改変されても `check_armed()` が
無効化されない穴が残る。よって承認ローダは、承認ファイルの `design_doc_sha256`
（= 本書の sha）照合に加えて、**本書 front-matter の `base_document_sha256` と
checkout 上の `DESIGN_VG_METER_CAL_DEBT_v1.0.md` の実測 sha256 の一致**を実行時に
検証し、不一致は未承認扱い（fail-closed）とする（信頼の連鎖: 承認 → v1.1 バイト列
→ v1.0 バイト列）。あわせて v1.0 / v1.1 の両ファイルを C0 の path inventory
（`c0_path_inventory.json`）の検査対象に含める。

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
