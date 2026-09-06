---
document_id: VG-METER-CAL-DEBT-DESIGN-v1.2
project: VoiceGenesis
document_class: CANONICAL_DESIGN_REVISION
status: APPROVED_DESIGN_REVISION / NOT_PREREGISTERED / EXECUTION_NOT_AUTHORIZED
revises: VG-METER-CAL-DEBT-DESIGN-v1.1
base_document_path: voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.1.md
base_document_sha256: def109dbc031807103bc630b25622d868a208189ea00665d16d20bd9f6de3a0f
revision_rule: v1.1 は read-only。本書は v1.1 §0/§V6 の改訂規約（in-place 改変禁止・
  新 revision を append-only で作成）に従う新 revision であり、本書に明記した節のみ
  v1.1 を上書きする。明記のない全ての節は v1.1（さらにその上流の v1.0）が引き続き正。
evidence_basis:
  - campaign RUN10-CAL-20260905-410b25f2 (UNSEALED, abort record, c3b 観測 = D106)
  - campaign RUN10-CAL-20260906-a4ed65c1 (c1 部分実行, abort record, D108)
authorship:
  design_compiler: "CLAUDE (Fable session, 2026-09-06)"
  approving_authority: "USER (directive 2026-09-06: 設計 v1.2 を Design Memo から起こし、
    v1.1 §V8 候補課題の対照意味論を是正し、C-1 診断・rehearsal・レビュー運用の固定を
    行う。実行授権ではない)"
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
note_on_execution: 実行は v1.0 §18 の 3 承認 Gate（campaign 実行 / C0 freeze / seal 受容）
  に引き続き従う。本書の承認は設計改訂の承認であり実行授権ではない。
---

# VoiceGenesis RUN10-CAL 設計改訂 v1.2 — 対照意味論の是正・C-1 診断/rehearsal による
疎通・レビュー運用の固定・開発指針の明文化

## 0. 位置づけと改訂範囲

campaign `RUN10-CAL-20260905-410b25f2`（設計 v1.1、UNSEALED で abort、D105/D106）と
`RUN10-CAL-20260906-a4ed65c1`（c1 部分実行で abort、D108）の実測観察、および
v1.1 §V8 が暫定登録した 4 件の v1.2 候補課題を、次の再 freeze の前に設計へ編入する。

本書が v1.1 を上書きするのは以下のみ:

| 節 | 上書き対象（v1.1） | 内容 |
|---|---|---|
| §W1 | §V1 の fail-filter 意味論（v1.0 §8 の分割を継承する node） | negative control の fire/no-fire 判定を `fixtures.controls.detected()` へ一本化し、`SANCTIONED_ABSTENTIONS` 閉語彙で `(SILENCE, F0_UNUSABLE)` を「present かつ non-fired」として明示的に扱う |
| §W2 | §V3.5 の `RowInput` 構築（D105 追補） | `splitter.row_inputs_for_split()` を唯一の正本と再確認し、C-1 診断ステージ・rehearsal 経路・freeze 前提の 3 点を新設 |
| §W3 | §V6「統治文書の切替」+ 承認手続運用 | Gate 1/2 承認時刻 < freeze event の fail-closed 検査、統治文書 pin を v1.2→v1.1→v1.0 の 2 段連鎖へ拡張、レビュー予算の運用固定 |
| §15 | （v1.0、不変） | v1.1 と同様、**§15（RUN11 Hard Claim-Dependency Gate）の文言と凍結は本書でも不変**。本書のいかなる縮小・是正も §15 の凍結解除条件を充足しない — RUN11 は引き続き凍結を維持する |

上記以外の全て（v1.0 の D1–D3 裁定・語彙・C0 manifest・independence tier・456 セル
行列・repeat 構造・selection rule・誤差式・終端 status cascade・provenance schema の
欄構成・費用上限・RUN11 Gate・v1.1 の holdout sweep pinning・c4 実 gate 配線・U_GT/
U_num 凍結・invariance 軸宣言・破棄 ledger 圧縮保全）は v1.1（さらにその上流の v1.0）
のまま不変。

## W0. 開発指針（最上位。User 提示 2026-09-06）

核: **厳格さは理解の深さに比例させる**。分からない段階は安く速く回し、分かった範囲
だけを高コストで固める。**進捗は答えた問いの数で測る**（「完走」「全マージ」
「巡数消化」は進捗ではない）。以下は「反省点 1 つにつきルール 1 つ」の対応表。

| # | 反省点 | ルール | 置き場所 |
|---|---|---|---|
| 1 | armed campaign へ直接 meter を投入し、対照設計/候補由来の fail-closed（FORMANT 43/43 negative false-fire・TILT ceiling NONE）を高コストな本番 campaign 後にしか発見できなかった | **探索ステージ（C-1 診断）を本番の前に必ず置く**。armed campaign に入れる meter は、freeze も封印もない安い診断（目安 30 セル・30 分以内・claim 不可）で「正例に発火し負例に発火しない」を先に確認したものに限る | §W2(a)、`campaign.diagnose` |
| 2 | 修正候補（D82/D87/D88/D90）を「次で直す」と繰り延べたまま再 freeze し、直しても消えない失敗（対照設計由来）を区別せず再実行の準備を進めた | **再実行の前に帰属を義務化**。観測された失敗ごとに「原因 X・修正 Y で消える」の帰属仮説と安い検証（C-1）を済ませないと再 freeze しない。修正を入れても残る失敗があるなら再実行しない | §W2(b)、`IMPLEMENTATION_MAP_v1.md` §8 再 freeze 前提条件 |
| 3 | C4 実 gate の初回検証が本番 456 セル・数時間の campaign でしか行えず、`BLOCKED_LEAKAGE`（D105）のような実装欠陥を armed freeze 後にしか検出できなかった | **freeze 対象は「一度は実経路で動いたコード」に限る**。placeholder / 未実行経路を pin した armed freeze を禁止する | §W2(c)(d)、`c0_validate` の実経路検査（今後の課題として登録） |
| 4 | wrap-up / STATUS.md のサマリーが「completed」「全マージ」といった手続き完了で進捗を語り、答えられていない問い（TILT ceiling・FORMANT fire 定義）が見えにくくなっていた | **進捗の定義を科学的状態で書く**。「答えた問い / 証拠 / 負債の terminal status」を必須行にする。「完走」「全マージ」「巡数消化」は進捗として記載しない | `.claude/skills/wrap-up/SKILL.md`、`.claude/memory/_index.md` |
| 5 | #346 が 26 巡（実害 3 分類に基づく採用の連鎖であって空転ではないが、コストは実測として重い）でマージに至り、レビュー予算の運用が曖昧だった | **レビュー予算を固定する**: 通算 10 巡で機械的打ち切り。「基盤 PR ≤ 問いに答える PR」を STATUS で計上する | `AGENTS.md` §3-4、`CLAUDE.md` bot レビュー節 |
| 6 | §V3.5 で `nuisance_axis` を含む `RowInput` 構築のローカル複製が `render_stage.py` に生じ、`splitter.row_inputs_for_split()` との乖離が C4 入場不能（D105）を招いた | **分岐軸を増やしたら fixture も増やす**。新しい導出軸は、その軸が None でない行を含む fixture テストとセットでのみ入れる。導出ロジックの複製は禁止する | `AGENTS.md` §7 起草チェックリスト item 11、`splitter.row_inputs_for_split()` 呼び出し site の全数テスト |
| 7 | 段階（生成/測定/検証）を区別せず、段階 2（実測による仮説検定）の答えが出る前から段階 3（測定器・生成物の堅牢化）のレビュー指摘を採用し、目的因果が逆転していた（測定器を通すための生成物を作る側に流れた） | **順序の固定と生成・測定の分離**: (1) 仮説どおりの生成物 → (2) 実測で仮説検定 → (3) その後に測定器・生成物の検証、の順序を前後させない。生成過程と測定過程を分離し、測定器を通すための生成物を作らない（目的因果の逆転禁止）。検証器は生成物と測定器が無いうちに突き詰めない。レビュー指摘は「どの段階のどの目的に必要か」を Fable が判定してから採否を決め、段階 1・2 が未回答のうちは段階 3 の堅牢化指摘は境界宣言で次周へ送る（実害 3 分類に該当しても段階が違えば当該 PR では不採用）。RUN10-CAL への当てはめ: 生成物 = 合成 fixture／測定器 = meter 候補／検証器 = `c0_validate`・provenance・ledger・approvals。v1.0–v1.1 は段階 3 に偏った = 逆転の実例 | `AGENTS.md` §3 item 6、`CLAUDE.md` bot レビュー節 |

## W1. 対照意味論の是正（v1.1 §V8 候補課題 1 の裁定）

### W1.1 観察（一次事実）

campaign `410b25f2` の c3b 観測（D106）で、`negative_controls_incomplete` は SILENCE
対照が `F0_UNUSABLE` のとき F0 依存候補の record が 0 件になり無条件発火する一方、
`coverage_incomplete` は同じ行を設計上の除外として扱う非整合が見つかった
（`selection_stage.py:510-514` 付近、実装バグ疑い）。

### W1.2 ruling

- **fire/no-fire 判定を一本化する**: `fixtures.controls.detected(output, predicate=None)`
  を全 fail filter（`campaign/selection_stage.py`・`campaign/holdout_stage.py`）の
  唯一の判定経路とする。既定分岐（`predicate=None`）は v1.1 までの厳格版判定と完全に
  同一挙動——本節は判定の**意味を変えず所在を一本化する**。
- **sanctioned abstention を閉語彙で定義する**: `SANCTIONED_ABSTENTIONS =
  frozenset({(ControlClass.SILENCE, "F0_UNUSABLE")})`。宣言された negative control 行
  のうち own record が皆無の行がこの組に一致すれば、「present かつ non-fired」として
  `negative_controls_incomplete`（completeness 判定から除外）・
  `negative_control_false_fire`（False として算入、他行の real false-fire は隠さない）
  の両方を整合させる。`coverage_incomplete`（BOUNDARY-domain 行、instance 単位）は
  本節の対象外であり非干渉のまま——**record 単位と instance 単位の粒度混在は
  本改訂では変更しない**（境界宣言。次 revision で粒度統一を検討する）。
  `NOISE_ONLY × F0_UNUSABLE` は閉語彙に含めない（noise 実現の確率的な F0 使用可否は
  v1.1 §V1.2 が既に「検出率」として lexicographic 基準に消費しており、abstention 化
  すると F0_CONTROL の C3a 判定と C3b の判定基準が二重に緩む）。
- **閉語彙の拡張は preregistration 経由に限る**: `SANCTIONED_ABSTENTIONS` への追加は
  次 revision の事前登録（本書のように既に閉じた campaign の観察に基づく凍結）を
  経ること。実行中の campaign の都合で緩めることを禁止する（v1.1 §V1.3 の正直性の
  宣言と同型の規律）。**sanctioned abstention は「測定器が正しく棄権した事実の記録」
  に限定し、候補を通すための緩和として語彙を拡張しない**（§W0 ルール 7 と同型の
  目的因果——検証器の都合で測定意味論を緩めることの禁止）。
- **registry `detection_predicate`（任意拡張点）**: `Candidate` に
  `detection_predicate: DetectionPredicate | None = None`（`field`/`min_value` の
  frozen dataclass）を追加する。**既存の全候補は未宣言のまま**とし、挙動は完全に
  不変（`candidate_space_sha()` の payload に含めるが、既存候補が使わない限り sha
  値自体は変わらない）。FORMANT 候補群への `detection_predicate` 宣言（W1.1 が示唆する
  「fire 定義そのものの再検討」）は **本revisionでは行わない** — 宣言は次 revision の
  設計判断であり、拡張点の用意のみを本revisionのスコープとする。

### W1.3 適用範囲

本節は C3b の negative control fail filter にのみ適用する。F0_CONTROL の C3a
（v1.1 §V1 の control class 分割）、claim-critical meter の gate 5（FDR0 = 0）、
両側条件（v1.0 §4.2）はいずれも不変。

## W2. 探索・疎通・freeze 前提

段階割り当て（§W0 ルール 7）: `campaign.diagnose` は段階 2（実測による仮説検定）、
rehearsal は段階 3（測定器・生成物の検証）——段階 2 の答えが出た family のみ
rehearsal を経て本番へ進める。

### (a) C-1 診断ステージ（`campaign.diagnose`）

armed campaign（freeze・封印・ledger 記帳を伴う経路）へ候補を入れる前に、これらを
一切持たない安い診断で「正例（TRUTH_CORE）に発火し負例（SILENCE/NOISE_ONLY/…）に
発火しない」ことを確認する。**freeze・封印・ledger 記帳を持たず、claim 不可**
（`claimable` は常に `False`）。目安 30 セル・30 分以内。F0 依存候補の診断は
`f0_registry_candidates()`（registry の F0_CONTROL 候補、candidate_id 昇順）で
掃引し候補ごとに verdict を出す（`--f0-candidate` で 1 件へ固定可）。verdict 語彙
は `PASS` / `FAIL_POSITIVE` / `FAIL_NEGATIVE` / `NO_CEILING` /
`NOT_EVALUABLE`（`negative_controls_incomplete` 等の理由付き）の 5 分岐。

実測（`--family TILT_GT --max-cells 30`、13 候補）: **2.2 秒**（目安 30 分を大幅に
下回る）。C3b 観測（D106）の再現診断も同一モジュールで実測済み: APERIODICITY_GT の
`M2A-B0-AUTOCORR-PERIODICITY`（PASS、正例 1.0・負例 0.0）と `M2A-HARMONIC-RESIDUAL-*`
（FAIL_NEGATIVE、NOISE_ONLY で有限値発火 = c3b の false-fire を再現）はいずれも
`--repeats>=2` で c3b の実測と整合することを確認済み（`--repeats 1` は 1 probe の
サンプリング分散で見逃す場合がある——診断コード自体の不整合ではない）。

### (b) 帰属義務（再実行の前提条件）

観測された失敗ごとに「原因 X・修正 Y で消える」という帰属仮説を立て、C-1 診断で
安価に検証しなければ再 freeze しない。**提案する修正を入れても残る失敗があるなら
再実行しない。**

今回の適用: FORMANT_GT（負例母集団と候補閾値の対照設計に由来する 43/43 の
negative false-fire）と TILT_GT（clean 候補群の ceiling が registry 宣言で `NONE`
固定）は、W1 の是正を入れても診断上消えないことを確認済み（`campaign.diagnose` の
再診断結果、`scratchpad/v12/wp4b_report.md` §「再診断結果」）。よって
**本 revision のマージ後も、これら 2 家系の帰属修正が済むまで本番 campaign は
回さない**。

### (c) rehearsal 経路

`--rehearsal` を `c0_freeze`/`campaign` の両 CLI に追加する。効果:
`fixtures.matrix.active_matrix()`/`active_candidates()` を縮小プールへ切替え
（`build_rehearsal_matrix()` = 456→58 行、決定論・順序保存・全 control_class/
family/anchor を被覆する部分集合）、`frozen_design.rehearsal` を常に明示記録し
（欠落は REQUIRED_BLOCKING violation）、`campaign_id` に `REHEARSAL-` 接頭辞を
強制し、canonical `campaigns/`/`~/.vg_cal/` 配下への書き込みを path ガードで拒否し、
`derived.debt_discharged` を常に `false` に固定し、`reveal_split_secret()` を
`RehearsalRevealRefusedError` で拒否する——**rehearsal は claim を一切生まない
疎通試験である**ことを構造的に強制する。`rehearsal` フラグは manifest の core
payload に入るため、rehearsal 承認を本番 freeze に流用することはできない。

rehearsal E2E（C0 freeze → c1 → c2 → c3a → c3b → c4 → close の全経路疎通）の実測は
`<WP2b 実測>`（本節はプレースホルダ——WP2b の実装完了後、実測値をここへ転記する）。

### (d) freeze 前提 = 実経路で動いたコードのみ

armed freeze は「一度は実経路（rehearsal を含む）で実行され green だったコード」
にのみ許可する。placeholder・未実行経路を pin した armed freeze を禁止する。
rehearsal green を armed freeze の前提とし、`c0_validate` は各ステージが実 gate
で end-to-end 実行済みであること（rehearsal ledger の kind 列を pin 入力とする）を
検査する。

### (e) Gate 1/2 承認時刻の順序 fail-closed 検査（D108 是正）

`c0_freeze.armed_freeze()`（`_check_gate_approval_ordering()`）は freeze event 時刻
を先に確定し、Gate 1/Gate 2 の `approved_at_utc` がそれより**厳密に前**（かつ
`+60s` 許容内 = `unseal.py` の Gate 3 検査と同じ許容）でなければ
`VALIDATION_BLOCKED`（理由 `gate<N>_approval_not_before_freeze` /
`gate<N>_approval_future_dated` / `gate<N>_approval_unparsable_timestamp`）で
publish しない。`c0_validate`（`_check_gate_approval_ordering()`）は
`approvals.gate{1,2}_sha256` に一致する記録が `approvals/records/` にあれば ledger
の `c0_freeze` event 時刻と突合し、逆転を `gate_approval_ordering_notes` に記録する
（記録が無い場合は非ブロッキング）。**運用規則**: 承認 JSON の `approved_at_utc` は
書き込み直前に `date -u +%Y-%m-%dT%H:%M:%SZ` で実測した値のみとする。推定・丸め・
事前記入は禁止する（D108 は運用ミス——承認 JSON の時刻を実測せず記入——が原因だった）。

## W3. レビュー運用 + abort 記録

### W3.1 レビュー予算の固定

`AGENTS.md` §3-4 の「1 PR あたりの通算上限は 10 ラウンド」を機械的打ち切りとして
明文化する: 第 10 巡で未対応分を境界宣言（再開条件つき）にまとめレビュアーから
unsubscribe し、残りは次周 Design Memo の材料として STATUS.md に登録する。実害
3 分類（実コード被害/将来汚染・致命的バグ）の新しい具体経路が示された指摘でも、
通算 10 巡を超えたら当該 PR では採用せず次周 PR で対応する（打ち切りは分類を
上書きしないが、対応の実行タイミングを次周へ送る）。PR #346 の 26 巡（実害基準に
基づく正当な採用の連鎖であり、当時は通算上限の明文がなかった）を反例として
記録する——今後は同型の巡数は通算 10 巡で機械的に区切る。

### W3.2 「基盤 PR ≤ 問いに答える PR」

campaign 1 周あたりの基盤整備 PR（runner 堅牢化・cap 会計・ledger 保全等）の本数は、
その周で答えた設計上の問いの本数を超えないことを STATUS.md で計上する
（W0 の核「進捗は答えた問いの数で測る」の運用化）。

### W3.3 abort 記録

- **campaign `RUN10-CAL-20260905-410b25f2`**（設計 v1.1）: c3b まで到達し
  UNSEALED としたが、C4 が `BLOCKED_LEAKAGE`（D105。`render_stage.py` の
  `RowInput` 複製が `nuisance_axis` に未追随）で入場できず abort。修正
  `2f24507` は当該 campaign の manifest `schema_paths_sha256` に pin 済みの
  ため継続不可。ledger は gz archive 済み。c3b 観測 = D106（本書 §W1 の動機）。
- **campaign `RUN10-CAL-20260906-a4ed65c1`**: c1 部分実行（8 slice）で abort。
  理由 (a) Gate 2 承認時刻が freeze event より後 = 承認が保護対象の後に記録
  （D108、運用ミス。§W2(e) で fail-closed 検査を新設）、(b) v1.2 設計先行のため
  継続不可。ledger は gz archive 済み。

## W4. v1.2 で「答えた問い / 証拠 / 負債の terminal status」

**答えた問い（4 件）**:

1. **F0 選定は v1.1 §V1 の control class 分割で成立するか** — 成立する（証拠:
   campaign `410b25f2` c3b、`F0-PYIN-FRAME2048-HOP512` が選定される）。
2. **APERIODICITY_GT の `M2A-B0-AUTOCORR-PERIODICITY` は正例発火・負例非発火という
   選定成立を維持するか** — 維持する（証拠: c3b 選定結果 + `campaign.diagnose`
   再現診断、いずれも pos=1.0/neg=0.0）。
3. **FORMANT_GT/TILT_GT の fail-closed（負例母集団の全数 false-fire・ceiling
   NONE 固定）は対照設計/候補由来であって v1.1 の退行ではないか** — 対照設計/
   候補由来であることを `campaign.diagnose` 再診断で確認した（証拠:
   `scratchpad/v12/wp4b_report.md`、`--repeats>=2` で c3b の false-fire を再現）。
   **これは「直った」ではない** — 両家系は本 revision で修正されておらず、
   帰属が確認されただけである（§W2(b)）。
4. **v1.1 のコードで C4 は入場できたか** — 入場不能だった（証拠: D105、
   `campaign_410b25f2` の `BLOCKED_LEAKAGE`）。

**負債の terminal status（v1.2 マージ時点）**: 全 `vocab.MeterId` が
`NOT_EVALUABLE` のまま——**本 revision は本番 campaign を 1 度も完走させておらず、
`debt_discharged` は依然 `false`**。W1 の是正と W2 の疎通機構はいずれも次の再
freeze の前提を整えるものであり、それ自体が負債を返済しない。次に必要な作業は
(1) FORMANT_GT/TILT_GT の帰属修正（未着手）、(2) その修正の C-1 診断での検証、
(3) rehearsal green の実測、(4) 実 gate 承認時刻を実測した再 freeze、の 4 点。

## 裁定

```yaml
status: APPROVED_DESIGN_REVISION / NOT_PREREGISTERED
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
next_actions:
  - W1 の実装（本書起草と同時並行で完了済み。fixtures/controls.py・candidates/registry.py・
    campaign/selection_stage.py・campaign/holdout_stage.py）
  - W2(a)/(c) の実装（`campaign.diagnose`・`--rehearsal`。本書起草と同時並行で完了済み）
  - FORMANT_GT/TILT_GT の帰属修正の設計（未着手・次 Design Memo の材料）
  - rehearsal green の実測 → 実 gate 承認時刻を実測した再 freeze
  - 結果は改竄せず記録する（gate の正直な fail は正当な終端）
```
