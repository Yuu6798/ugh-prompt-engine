---
document_id: VG-METER-CAL-DEBT-DESIGN-v1.3
project: VoiceGenesis
document_class: CANONICAL_DESIGN_REVISION
status: APPROVED_DESIGN_REVISION / NOT_PREREGISTERED / EXECUTION_NOT_AUTHORIZED
revises: VG-METER-CAL-DEBT-DESIGN-v1.2
base_document_path: voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.2.md
base_document_sha256: cbe45330e70fce9b5a999acbde02fae626e9bf886cb18091c92c00800224b71f
revision_rule: v1.2 は read-only。本書は v1.1 §0/§V6 の改訂規約（in-place 改変禁止・
  新 revision を append-only で作成）に従う新 revision であり、本書に明記した節のみ
  v1.2（およびその上流の v1.1/v1.0）を上書きする。明記のない全ての節は v1.2 が
  引き続き正。
evidence_basis:
  - "段階 2 実測 WP-A (2026-09-07): campaign.diagnose --dump-values による
    FORMANT_GT / TILT_GT / APERIODICITY_GT の分離可能フィールド全数走査
    (--f0-candidate F0-PYIN-FRAME2048-HOP512 --repeats 5 --max-cells 30)"
  - "HNR 列 probe (2026-09-07): candidates/impl/aperiodicity.py hnr_acf_db を
    同一診断セル音声へ適用 (frame 25 ms / hop 10 ms / hann)"
authorship:
  design_compiler: "CLAUDE (Fable session, 2026-09-07)"
  approving_authority: "USER (裁定 2026-09-07 A: FORMANT を claim scope から外し、
    TILT には HNR による detection_predicate を新 preregistration として宣言する。
    実行授権ではない)"
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
note_on_execution: 実行は v1.0 §18 の 3 承認 Gate（campaign 実行 / C0 freeze / seal 受容）
  に引き続き従う。本書の承認は設計改訂の承認であり実行授権ではない。
---

# VoiceGenesis RUN10-CAL 設計改訂 v1.3 — TILT detection_predicate の preregistration
と FORMANT の claim scope 除外

## 0. 位置づけと改訂範囲

v1.2 §W2(b) は「FORMANT_GT/TILT_GT の帰属修正が済むまで本番 campaign を回さない」と
定めた（帰属は確認済み・未修正）。本書はその帰属修正の**段階 2（実測による仮説検定）
の答え**を受けて、2 家系の扱いを確定する。

本書が v1.2 を上書きするのは以下のみ:

| 節 | 上書き対象（v1.2 / v1.0） | 内容 |
|---|---|---|
| §X1 | v1.2 §W1.2 末尾（「FORMANT 候補群への `detection_predicate` 宣言は次 revision」）+ front matter `meter_changes_authorized: false` | TILT harmonic 12 候補に `hnr_acf_db` を配線し `detection_predicate(field="hnr_acf_db", min_value=-5.0)` を宣言する **新 preregistration**。`meter_changes_authorized` の解除は**この 1 変更に限る** |
| §X2 | v1.0 §15 の claim scope（`max_claim_scope` に formant_frequency を含む運用） | **claim 縮小**: `max_claim_scope` = `["source_spectral_tilt", "injected_noise_fraction", "fundamental_frequency"]`。FORMANT を claim 対象から外す |
| §X3 | v1.2 §W4 | v1.3 で「答えた問い / 証拠 / 負債の terminal status」 |
| §X4 | v1.2 §W3 の統治文書 pin（固定 2 段連鎖） | 統治文書連鎖を任意段数のリスト（`approvals.DESIGN_DOC_CHAIN`）へ一般化 |
| §15 | （v1.0、不変） | v1.1/v1.2 と同様、**§15（RUN11 Hard Claim-Dependency Gate）の文言と凍結は本書でも不変**。本書の claim 縮小は §15 の凍結解除条件を充足しない — RUN11 は引き続き凍結を維持する |

上記以外の全て（v1.0 の D1–D3 裁定・語彙・C0 manifest・independence tier・456 セル
行列・repeat 構造・selection rule・誤差式・終端 status cascade・費用上限・RUN11 Gate、
v1.1 の holdout sweep pinning・U_GT/U_num 凍結、v1.2 の §W0 開発指針・§W1 対照意味論・
§W2 C-1 診断/rehearsal/freeze 前提・§W3 レビュー運用）は不変。**特に §W0 ルール 7
（段階の順序固定・生成と測定の分離・測定器を通すための生成物を作らない）は本書の
判断基準そのものとして生きている**。

## X1. TILT detection_predicate の preregistration

### X1.1 段階 2 の実測（WP-A、一次事実）

`campaign.diagnose --dump-values`（v1.2 §W2(a) の C-1 診断。freeze・封印・ledger を
持たず claim 不可）で、TILT_GT / FORMANT_GT / APERIODICITY_GT の**全候補 × 全出力
フィールド**について「正例（TRUTH_CORE）で発火し負例で発火しない単一閾値が存在するか」
を機械的に走査した（`--f0-candidate F0-PYIN-FRAME2048-HOP512 --repeats 5
--max-cells 30`）。分離の定義は `fixtures.controls.detected()` に厳密準拠する
（field 欠落・非有限・`missing_reason`・`ineligible` はすべて非発火）。

実測（TILT_GT、13 候補）:

| 集合 | `tilt_db_per_oct`（現行 primary output） | `hnr_acf_db`（本節が新設する補助値） |
|---|---|---|
| 正例 TRUTH_CORE（25 cell-probe） | 25/25 present。真値 slope 0.0 dB/oct の行が −0.12〜−0.47 に出る | 25/25 present、**[−1.915, +0.747] dB** |
| 負例 NOISE_ONLY（5） | +1.3〜+7.5（1 候補は −1.82 で正例レンジ内） | 5/5 present、**[−9.878, −9.758] dB** |
| 負例 SILENCE（5） | 0/5 present | 0/5 present（非有限 = 非発火） |

`tilt_db_per_oct` 経路: 現行 `DetectionPredicate` は `field` + `min_value` の `>=`
判定のみのため（`fixtures/controls.py`）、`<=` 型でしか分離できない 11 候補は
**predicate 宣言では救済できない**。さらにその 11 件の分離は「真値 slope = 0.0 の
TRUTH_CORE 行が測定上わずかに負側へ出る」ことに依存しており、符号を跨げば即
FAIL_POSITIVE に落ちる razor-thin な成立である。

`hnr_acf_db` 経路: `>= t, t ∈ (−9.758, −1.915]` で正例 100% / 負例 0%。**分離余裕
7.8 dB** は、選定成立実績のある APERIODICITY_GT の HNR_ACF 群（余裕 1.3〜7 dB）と
同等の広さである。

### X1.2 ruling（preregistration）

- **配線**: `candidates/impl/tilt_harmonic.py` の `measure()` が返す `values` に
  `hnr_acf_db` を追加する。値は `candidates/impl/aperiodicity.py` の
  `hnr_acf_db(signal, sr, frame_ms=25.0, hop_ms=10.0, window="hann")`（F0 非依存）
  であり、**WP-A の probe と同一パラメータに凍結する**（frame/hop/window は候補
  パラメータグリッドの軸ではない定数。変更は次 revision の preregistration 経由）。
  非有限値はキー自体を出さない（`detected()` の「field 欠落は非発火」へ写像）。
- **primary output は不変**: `measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY`
  の `HARMONIC_OLS`/`HARMONIC_THEILSEN` → `tilt_db_per_oct` は変えない。誤差式・
  selection・holdout の score 意味論はいずれも不変であり、**変わるのは
  `fixtures.controls.detected()` の fire 判定だけ**である。
- **宣言**: `candidates/registry.py` の TILT harmonic 12 候補
  （`M2T-HARMONIC-OLS-K{4,6,8}-WIN{HANN,BLACKMAN_HARRIS}` 6 件 +
  `M2T-HARMONIC-THEILSEN-…` 6 件）に
  `detection_predicate=DetectionPredicate(field="hnr_acf_db", min_value=-5.0)` を
  宣言する。閾値 −5.0 dB は X1.1 の実測レンジの中間に置いた（正例側マージン
  4.09 dB / 負例側マージン 4.76 dB）。
- **`M2T-B0-CURRENT-HYBRID` は宣言しない**。同候補は `claim_ceiling=NONE`
  （`[UNDERSPEC-CAL-C06]`、unit 混在）であり、C-1 診断は検出問題ではなく
  `NO_CEILING` を返す — predicate では動かない（v1.2 §W2(b)「修正を入れても残る
  失敗」の 1 件として残置する）。
- **`candidate_space_sha` は変わる**。`detection_predicate` は非 `None` のときのみ
  canonical payload へ現れる（v1.2 §W1.2 / #349 第 2 巡）ため、本宣言により
  candidate space の凍結ハッシュが変化する。これは意図した変更であり、次の C0
  freeze は新しい sha で行う。
- **`meter_changes_authorized` の解除範囲**: 本書は front matter で
  `meter_changes_authorized: false` を維持したまま、**上記の配線と宣言 1 件に限って**
  例外的に authorize する（User 裁定 2026-09-07 A）。他のいかなる meter 変更
  （FORMANT 候補への宣言、predicate 型の `max_value` 拡張、`n_poles_found` の
  `values` 昇格、fixture 側の変更）も本書では authorize しない。

### X1.3 既知の限界（宣言）

**HNR predicate は PURE_SINE に対して非発火を保証しない。** TILT_GT の負例集合は
`fixtures/matrix.py` の `_tilt_rows()` が `negative_n=2` で採る SILENCE と NOISE_ONLY
のみであり、PURE_SINE は枠外である。実測では FORMANT_GT の PURE_SINE 行（同一 anchor
f0 = 130.813 Hz、sr 48000）の `hnr_acf_db` が **0.762 dB** であり、TILT 正例の上端
0.747 dB より高い — 純正弦は完全に harmonic なので当然の帰結である。したがって
`NEGATIVE_CONTROL_SEQUENCE` の採用件数が将来 3 に増えれば本 predicate は破れる。

この限界は「測定器を通すための対照変更」ではない — **既に凍結された対照集合に対して、
測定器がどの条件で棄権する（＝発火しない）と主張できるかを宣言したもの**である
（v1.2 §W0 ルール 7 / §W1.2「sanctioned abstention は測定器が正しく棄権した事実の
記録に限定し、候補を通すための緩和として語彙を拡張しない」と同型の規律）。負例集合
そのものは本書で一切変更しない。

副次的な限界（いずれも v1.2 §W2(b) の「修正を入れても残る失敗」として記録する）:

1. 閾値 −5.0 dB は 1 掃引・5 probe の実測に基づく。probe 数を増やした分散の実測は
   していない（余裕 7.8 dB に対し probe 内変動は小さいが、未計測である）。
2. 本 predicate は合成 fixture 上の分離であり、実音声（実際の非周期成分を伴う発声）
   での妥当性は主張しない。

## X2. claim 縮小 — FORMANT を claim scope から外す

### X2.1 段階 2 の実測（WP-A、一次事実）

FORMANT_GT の 43 候補 × 全 `values` フィールドを同じ手続きで走査した結果:

| algorithm_family | 候補数 | 単一閾値で完全分離できる候補 | 実測 |
|---|---|---|---|
| `B0_CURRENT_CEPSTRAL_CENTROID` | 1 | 0 | 正例 `f1_est_hz` [305, 353] の**内側**に SILENCE 300.0・PURE_SINE 343.6 が入る |
| `CEPSTRAL_POLES` | 18 | 6（`>=` 型、`f3_hz >= t ∈ (1020, 1310.67]`。LIFT0.5 群のみ） | LIFT0.7/0.9 群 12 件は全 field で重なる |
| `BURG_LPC` | 24 | 11（すべて `<=` 型 = 現行スキーマでは表現不能） | 分離を担うのは帯域幅の大小ではなく **PURE_SINE が第 2 の極を返さない**こと |

3 つの仮説はいずれも棄却または部分棄却された:

- **H-F1「帯域幅が正例で狭く負例で広い」→ 反証**。正例 `f1_bandwidth_hz` は 24 候補中
  21 件で max 76〜4630 Hz（min は 3e-4〜9e-4 Hz）と 6〜7 桁ばらつき、**PURE_SINE は
  正例と同じ極めて狭い帯域（0.0013〜1.62 Hz）を返す**。完全分離する 3 件の余裕は
  4.1e-4 Hz（razor-thin）。
- **H-F2「CEPSTRAL / B0-CENTROID には分離フィールドが無い」→ 部分反証**。B0-CENTROID
  は支持（分離不能）。CEPSTRAL は lifter 幅 0.5 の 6 件のみ分離するが、閾値
  1020〜1311 Hz は **fixture の pole set レンジ（`axes.FORMANT_POLE_SETS_HZ`）を
  写した値**であり、構成概念（フォルマントが存在するか）ではない。
- **H-T1 の FORMANT 転用 → 反証**。同じ `hnr_acf_db` 列は FORMANT_GT では分離しない
  （正例 [0.586, 0.625] vs PURE_SINE 0.762 vs NOISE −9.9 で両方向とも重なる）。

### X2.2 ruling

**現行 3 推定器（BURG_LPC / CEPSTRAL_POLES / B0_CURRENT_CEPSTRAL_CENTROID）には、
「フォルマントが存在するか」を検出する固有の統計量が無い**。帯域幅は PURE_SINE と
正例で同等であり、成立する閾値は fixture の pole レンジ依存である。すなわちこれらは
**推定器ではあるが検出器ではない**。predicate 宣言で救済できるのは (A) `>=` 型のみで
6/43、predicate 型を `<=` へ拡張しても 17/43 に留まり、残る 26 件は現存する観測量の
組み合わせでは救えない。

したがって v1.0 §15 の手続き（**claim 削除・縮小は新 preregistration + ユーザー承認**）
に従い、本書をもって **FORMANT を claim scope から外す**:

```
max_claim_scope = ["source_spectral_tilt", "injected_noise_fraction", "fundamental_frequency"]
```

（v1.0 §18 の Gate 1 承認 JSON からも `formant_frequency` を除く運用とする。除外は
**承認ファイル側で行う**ものであり、`c0_freeze._check_max_claim_scope()` は
`formant_frequency` を含む承認を拒否しない — 同関数の責務は registry 突合であって
claim 方針の強制ではないため。scope から外れた結果として
`selection_stage.claim_scope_report()` / `capped_ceiling()` が FORMANT 候補の
effective ceiling を capping し、**ABSOLUTE 到達が構造的に不可能**になる。実装上の
cap 先は `DIRECTIONAL`（`capped_ceiling()` は `min(ceiling, DIRECTIONAL)`）であり、
`M3-B0-CURRENT-CENTROID` は元から `DIAGNOSTIC_ONLY` のまま。）

**FORMANT 検出器の新設計は本書の範囲外**とする。段階 1（仮説どおりの生成物）へ戻る
作業であり、別 memo で扱う（候補となる方向: `n_poles_found` の `values` 昇格、
`<=` 対応の predicate 型拡張、フォルマント帯域のエネルギー集中度など新しい観測量の
導入。いずれも v1.2 §W0 ルール 7 に従い段階 1 → 段階 2 の順で回す）。M3_FORMANTS の
負債は `NOT_EVALUABLE` のまま据え置く — **claim を外すことは負債の返済ではない**。

## X3. v1.3 で「答えた問い / 証拠 / 負債の terminal status」

**答えた問い（2 件）**:

1. **FORMANT_GT の負例全数 false-fire は `detection_predicate` の宣言で消えるか**
   — **消えない**（証拠: WP-A 実測。43 候補 × 全 `values` フィールドの全数走査で、
   `>=` 型で分離できるのは 6 件、predicate 型を `<=` へ拡張しても 17 件。残る 26 件は
   正例と負例のレンジが全 field で重なる。救済される 17 件の閾値も fixture の pole
   レンジ依存）。**現行 3 推定器は検出器ではない**というのがこの問いの答えであり、
   claim 縮小（§X2）がその帰結である。
2. **TILT_GT の負例 false-fire は分離可能な観測量を持つか** — **持つ**（証拠: WP-A の
   HNR 列実測。正例 [−1.915, +0.747] dB vs NOISE_ONLY [−9.878, −9.758] dB、SILENCE は
   非発火、**余裕 7.8 dB**）。ただし PURE_SINE には効かない（§X1.3、実測 0.762 dB）。

**答えていない問い（明示）**:

- TILT の `M2T-B0-CURRENT-HYBRID` の `ceiling=NONE`（unit 混在）は本書でも解かない。
- FORMANT の検出器をどう作るか（§X2.2 末尾。別 memo）。
- 本 predicate が実音声で成立するか（合成 fixture 上の分離のみを主張する）。

**負債の terminal status（v1.3 マージ時点）**: 全 `vocab.MeterId` が
`NOT_EVALUABLE` のまま——**本 revision も本番 campaign を 1 度も完走させておらず、
`debt_discharged` は依然 `false`**。§X1 は TILT の帰属修正であり、§X2 は FORMANT の
claim 範囲の縮小であって、いずれもそれ自体が負債を返済しない。次に必要な作業は
(1) rehearsal green の実測、(2) 実 gate 承認時刻を実測した再 freeze（F0 /
TILT / APERIODICITY の 3 家系）、の 2 点（v1.2 §W2(b) の帰属義務は本書で
FORMANT/TILT とも充足された — FORMANT は「修正しても消えない」ことが確定したため
claim から外し、再実行の対象外とする）。

## X4. 統治文書連鎖の一般化

v1.2 §W3 は統治文書 pin を「v1.2 → v1.1 → v1.0 の 2 段連鎖」として固定 2 段の定数
（`BASE_DESIGN_DOC_RELATIVE_PATH` / `BASE_BASE_DESIGN_DOC_RELATIVE_PATH`）で実装して
いた。この形は統治文書が 1 世代進むたびに定数を 1 本増やすコード変更を強いる。

ruling: `approvals.DESIGN_DOC_CHAIN`（先頭が現行の統治正本、以降は承継元を新しい順に
並べた任意段数のリスト）へ一般化する。`_verify_base_document_pin()` は連鎖の全リンク
（要素 `i` の front matter の `base_document_sha256` = 要素 `i+1` の実測 sha256）を
検証し、末尾（v1.0）は基底を持たないため pin 検証の対象外とする。各段の読取は
**1 回だけ**行い、同じバイト列を hash 算出（前段の相手側）と front matter 解析
（次段のピン元）の両方に使う（PR #346 第 16 巡の TOCTOU 閉塞を全段へ適用）。
`c0_validate.scan_calibration_tree_inventory()` は連鎖の全文書を union する。
後方互換の `BASE_*` 別名は置かない。

`c0_freeze._DESIGN_REVISION` は `"1.3"` を発行し、
`c0_validate._ALLOWED_DESIGN_REVISIONS` は `{"1.1", "1.2", "1.3"}` になる。v1.2 固有
検査の適用 floor は `_is_v1_2_or_later()` のまま（版数順で v1.3 も含む）。

## 裁定

```yaml
status: APPROVED_DESIGN_REVISION / NOT_PREREGISTERED
execution_authorized: false
meter_changes_authorized: false   # 例外は §X1.2 の 1 変更に限る（User 裁定 2026-09-07 A）
run11_measurement_entry_authorized: false
max_claim_scope:
  - source_spectral_tilt
  - injected_noise_fraction
  - fundamental_frequency
next_actions:
  - §X1 の実装（本書起草と同時並行で完了済み。candidates/impl/tilt_harmonic.py・
    candidates/registry.py・対応テスト群）
  - §X2 の運用反映（Gate 1 承認 JSON から formant_frequency を除く。次 freeze 時）
  - §X4 の実装（本書起草と同時並行で完了済み。approvals.py・c0_validate.py・c0_freeze.py）
  - C-1 診断で TILT_GT 12 PASS + 1 NO_CEILING、FORMANT_GT 不変、APERIODICITY_GT
    選定候補 PASS を確認（実測済み）
  - rehearsal green の実測 → 実 gate 承認時刻を実測した再 freeze（F0/TILT/APERIODICITY）
  - FORMANT 検出器の新設計は別 memo（段階 1 へ戻る）
  - 結果は改竄せず記録する（gate の正直な fail は正当な終端）
```
