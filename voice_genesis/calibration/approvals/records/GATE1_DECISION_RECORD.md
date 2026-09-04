# Gate 1 決定記録 — GATE1_CAMPAIGN_EXECUTION（2026-09-02）

このファイルは Gate 1（campaign 実行承認 + 費用上限 + E_use 境界）の委任経緯と、
それに基づき確定した各値の根拠を記録する。承認ファイル実体（JSON）は
`~/.vg_cal/approvals/gate1_campaign_execution.json`（checkout 外、正本）に置き、
本リポジトリには参照用の同一内容コピーのみを置く
（`approvals/gate1_campaign_execution.2026-09-02.json`。詳細は
[`approvals/README.md`](../README.md) と本ファイル §6 参照）。

## 1. 委任の経緯

2026-09-02、User は Gate 1（`GATE1_CAMPAIGN_EXECUTION`）の判断を設計リード
（Claude Fable 5.1、本セッション `011x6MhCwdZWgxsnkXDVBrdx`）へ委任し、
「推奨値（recommended values）」での承認を依頼した。承認ファイルの
`approver` フィールドはこの委任を明記する:

```
"Yuu6798 (delegated to Claude Fable 5.1 — recommended values, session 011x6MhCwdZWgxsnkXDVBrdx)"
```

以下 §2–§4 が、この委任に基づき設計リードが確定した推奨値とその根拠である。

## 2. Cost caps（`cost_caps`）

| フィールド | 値 | 根拠 |
|---|---|---|
| `compute` | `172800`（秒 = 48 CPU-h） | 設計正本 §14「数〜10 CPU 時間/実装」+ selection ≈ 10^5 calls を根拠に、3–4 倍のマージンを見込んだ |
| `storage` | `4294967296`（bytes = 4 GiB） | 設計正本 §14「renders 概ね 1 GB 以下」に加え、measurements/ledger 分を含めても 4 倍のマージンを見込んだ |
| `budget` | `20`（USD） | ローカル CPU 実行を前提としつつ、クラウド CPU に切り替えた場合の保険として、RUN9/RUN10 の実測 ≈$1.5–2.1 の 10 倍を見込んだ |
| `budget_accounting_mode` | `"local_zero_cost"`（2026-09-02 round 13 finding #3 追補） | 本キャンペーンはローカル計算資源のみで実行し、課金対象の外部リソース（クラウド CPU・API 従量課金等）を一切使わない。そのため各 render/measurement work unit の budget charge は 0 に固定し、`budget`（20 USD）cap は non-binding（各 stage の plan/stop 出力にその旨を明記する）とする。将来クラウド CPU 等の課金資源へ切り替える場合は、本 Gate 1 を re-approve し `budget_accounting_mode="per_unit_fixed"` + 正の `budget_unit_cost` を新たに宣言すること — 過去の `local_zero_cost` 承認を課金資源にそのまま流用しない |

いずれも「保守的な上限」であり、実行時の実測消費とは別軸（`cost_caps.CapCounters`/
`check()` が実測との超過判定を別途行う）。`budget_accounting_mode` は `cost_caps`
宣言自体の一部として凍結 manifest（`frozen_design.cost_caps`）へ埋め込まれ、
`manifest_core_sha` の対象に含まれる（`voice_genesis/calibration/cost_caps.py`
`[UNDERSPEC-CAL-D27]`）。

## 3. `max_claim_scope`

ABSOLUTE を目指してよい construct_id は、claim-critical set
（`vocab.CLAIM_CRITICAL_SET` = `M3_FORMANTS`/`M2_SPECTRAL_TILT`/`M2_APERIODICITY`）
のうち実際に ABSOLUTE 候補が存在する construct と、claim-critical 外だが
上流 control として ABSOLUTE を宣言する `F0_CONTROL` の construct を合わせた
4 件（`GATE_REVIEW_BRIEF_v1.md` §2.2 の claim ceiling 表と一致）:

```json
["formant_frequency", "source_spectral_tilt", "injected_noise_fraction", "fundamental_frequency"]
```

- `formant_frequency`（M3_FORMANTS, CEPSTRAL-POLES/BURG-LPC 系）— claim-critical。
- `source_spectral_tilt`（M2_SPECTRAL_TILT, HARMONIC-OLS/THEILSEN 系。B0-HYBRID
  混在 unit 行は `INVALID_CIRCULAR` 相当のため対象外）— claim-critical。
- `injected_noise_fraction`（M2_APERIODICITY, HARMONIC-RESIDUAL）— claim-critical。
  同じ M2_APERIODICITY 内の `world_d4c_aperiodicity`（D4C、WORLD 合成 fixture 上は
  SHARED_MODEL_DIAGNOSTIC）と `harmonic_to_noise_ratio`（B0/HNR-ACF、DIRECTIONAL
  上限）は ABSOLUTE 候補ではないため対象外。
- `fundamental_frequency`（F0_CONTROL）— claim-critical set 外・上流 control だが
  registry 上 ABSOLUTE 候補（`F0-B0-CURRENT`）が存在するため、タスク指示により
  scope に含めた。

`e_use_bound_accepted` は `true`（§4 の `USER_ACCEPTED_USE_BOUND` 行を受容する）。

## 4. E_use evidence table 決定（`config/e_use_table_v1.json`）

`e_use_table.generate_template()` が生成する 20 行（construct_id/unit/domain の
一意な 3 つ組。`GATE_REVIEW_BRIEF_v1.md` §2.3 の worksheet と同一）に対し、
以下の construct family 単位でユーザー委任の推奨値を適用した。一致しない
construct/unit/domain の組は全て `UNJUSTIFIED`（`e_use_value: null`。自動
ceiling が適用される）のまま残した。

全 `USER_ACCEPTED_USE_BOUND` 行に共通するメタ情報:

- `source_id_or_url`: `"GATE1-DELEGATION-2026-09-02 (user -> design lead, recommended values; PR #343)"`
- `source_checked_at`: `2026-09-02T01:46:11Z`（`date -u` 実測値。本ファイル起草時刻）
- `source_hash_or_version`: 本ファイル（`GATE1_DECISION_RECORD.md`、確定後の
  最終内容）の sha256 — §8 末尾参照（本文中に自己参照のハッシュ値は埋め込ま
  ない: 埋め込んだ瞬間にファイル内容が変わり値自体が無効化する自己言及矛盾を
  避けるため。確定値は `e_use_table_v1.json` の該当列と Gate 1 委任完了報告に
  記載する）
- `review_status`: `"APPROVED_BY_DELEGATION"`

| construct_id | unit | domain（要約） | e_use_mode | e_use_value | derivation_rule | evidence_class |
|---|---|---|---|---|---|---|
| `formant_frequency` | `hz` | CEPSTRAL-POLES（band_lo=300Hz） | `relative` | `0.05` | 宣言 pole 周波数の 5%（per-instance） | `USER_ACCEPTED_USE_BOUND` |
| `formant_frequency` | `hz` | BURG-LPC, fs'=2×4000Hz | `relative` | `0.05` | 同上 | `USER_ACCEPTED_USE_BOUND` |
| `formant_frequency` | `hz` | BURG-LPC, fs'=2×5000Hz | `relative` | `0.05` | 同上 | `USER_ACCEPTED_USE_BOUND` |
| `source_spectral_tilt` | `db_per_oct` | HARMONIC-OLS | `absolute` | `2.0` | TILT_GT grid step 6 dB/oct ÷ 3 = adjacent levels 間に 3 倍マージン | `USER_ACCEPTED_USE_BOUND` |
| `source_spectral_tilt` | `db_per_oct` | HARMONIC-THEILSEN | `absolute` | `2.0` | 同上 | `USER_ACCEPTED_USE_BOUND` |
| `injected_noise_fraction` | `fraction` | HARMONIC-RESIDUAL | `absolute` | `0.05` | mid-range fraction grid step `{0,.01,.03,.10,.30,.60}` の半分。§16-6 の cross-construct 絶対等価禁止に従い本 construct 単独 | `USER_ACCEPTED_USE_BOUND` |
| `world_d4c_aperiodicity` | `fraction` | D4C（SHARED_MODEL_DIAGNOSTIC） | `absolute` | `0.05` | 同上（unit=fraction のため対象。HNR (dB) 行とは異なり fraction 単位のため §16-6 の対象内） | `USER_ACCEPTED_USE_BOUND` |
| `fundamental_frequency` | `hz` | primary F0 帯（baseline） | `relative` | `0.01161` | 20 cents = 2^(20/1200) − 1 ≈ 0.01161（per-instance 相対） | `USER_ACCEPTED_USE_BOUND` |
| `fundamental_frequency` | `hz` | primary F0 帯（pyin, fmin/fmax 固定） | `relative` | `0.01161` | 同上 | `USER_ACCEPTED_USE_BOUND` |

残る 11 行（`formant_centroid`／`source_spectral_tilt` mixed-unit B0-HYBRID／
`harmonic_to_noise_ratio` ×2／`resonance_centroid`／`resonance_center_frequency`／
`join_discontinuity_magnitude` ×3／`identity_component_distance` ×2）は
`UNJUSTIFIED` のまま（`e_use_value: null`）。

### 4.1 `e_use_mode` 拡張（`[UNDERSPEC-CAL-D11]`）

`gates.EUseEvidenceRow`/`e_use_table.py` の `e_use_value` は construct 単位の
**1 スカラー**だが、formant/F0 の推奨値は「宣言 truth 値に対する相対誤差」
（5%・20 cents）としてのみ意味を持つ。一方 `gates.InstanceMargin.e_use`/
`threshold_margin()` は per-instance の **絶対量** E_use を直接消費する
（`M[i] = E_use - U_GT - U_num`）。この不一致を埋めるため、`EUseEvidenceRow`
（`gates.py`）へ 14 列目 `e_use_mode: "absolute" | "relative"`（既定
`"absolute"`、旧 13 列のみの行と後方互換）を追加した。`"relative"` の行は
`e_use_value` を「宣言 truth に対する相対比率」として宣言するのみであり、
実際の per-instance 絶対値展開（`e_use_value × declared_truth`）は
campaign 側（selection/gates 消費前の前処理）の責務とする——本拡張自体は
展開ロジックを持たない、宣言列の追加のみである。設計正本は E_use を
「用途許容誤差」とのみ述べ、相対/絶対のいずれで宣言するかの列までは
規定しないため `[UNDERSPEC-CAL-D11]` として `c0_freeze.py` の README ledger
（`[UNDERSPEC-CAL-D10]` の次番）に登録する。

### 4.2 逸脱事項: 「M5 join time」構成要素は registry に不在

タスク指示は「M5 join time（unit seconds/s）: 0.005 absolute」という
決定を含んでいたが、`candidates/registry.py` の M5_TRANSITION meter には
`join_discontinuity_magnitude`（unit: `rms_amplitude_delta`/
`spectral_flux_l1`/`spectral_flux_l2`。いずれも振幅/スペクトルの変位量で
あり、秒単位の "join time" ではない）の 3 行のみが存在し、`seconds`/`s`
単位の construct は 1 件も存在しない（`GATE_REVIEW_BRIEF_v1.md` §2.2/§2.3
の worksheet も同じ 3 行のみを列挙しており、これは本セッション固有の
見落としではなく registry 自体の実態）。該当する construct が存在しない
ため、この決定は適用できず、M5 の 3 行はタスク指示のデフォルトバケット
「M5 magnitude/discontinuity … UNJUSTIFIED」どおり `UNJUSTIFIED` のまま
残した（結果として、この逸脱は最終テーブルの値には影響しない——
"join time" 決定が適用される行が最初から存在しないため）。

## 5. 運用上の注記: secrets とセッション境界

設計正本 §7 により、split secret / render root secret は**実行環境
（execution environment）内でのみ**存在する（checkout・manifest には
sha256 commitment のみを記録し、平文はコミットしない）。したがって
C0 freeze（`armed_freeze()`）と、その後続の campaign 実行は**同一セッション
内で完結しなければならない**——secret は次回セッションへ引き継がれない
（`VG_CAL_SECRET_DIR` は checkout 外だが、生成した secret 自体をセッション
間で受け渡す手段は設計正本に規定がない）。

### 5.1 新規 UNDERSPEC 提案: campaign close 後の commit-reveal

上記の帰結として、「CAMPAIGN_CLOSED 後に `split_secret` を ledger へ
reveal（commit-reveal）し、検証器（第三者）が split を再計算・再現できる
ようにする」運用が望ましいと考えられるが、設計正本 §7 はこの reveal 手順を
明記していない。`c0_freeze.py` の README ledger に
`[UNDERSPEC-CAL-D09]` として新規登録した（本ファイル §7 参照。C0 レビュー
対象として明記）。本 Phase（D1b + Gate 1 委任）はこの reveal 自体を実装
しない——あくまで問題提起の登録のみ。

## 6. dry-run 出力の記録

### 6.1 Gate 1 承認前（この決定を確定する直前の dry-run。nonce 発行元）

`python -m voice_genesis.calibration.c0_freeze --approval-dir <空ディレクトリ>`
の実測出力（2026-09-02T01:46 UTC 付近。承認ファイルが 1 件も無い状態）:

```
manifest_core_sha: 9f1adff3b2a197e0f430509be0b0668e9b86a3789cc18be71f12af0e47e160c5
campaign_id (if frozen today): RUN10-CAL-20260902-9f1adff3
authorization_nonce: b88b3ed26feb9a0866ab223f979bf4da
blocked_codes: ['BLOCKED_C0_MANIFEST_INCOMPLETE']
gate2.armed: False
```

`missing_required_keys` の内訳（9 + 3 件）: §4 で受容する前の
`USER_ACCEPTED_USE_BOUND` 9 行分の E_use 違反（`gate1_e_use_bound_accepted`
が未承認で `False` のため）+ `frozen_design.cost_caps`/`stop_rules`
（Gate 1 未承認）+ `repo.dirty_tree`（本セッションの作業ツリーが実際に
dirty なため——D1b コード変更を含む正当な dirty state。§8 の再検証時点でも
解消しない、既知の許容差分）。この `authorization_nonce` を Gate 1
承認ファイルへ転記した。

### 6.2 Gate 1 承認後の再検証（§8 参照）

Gate 1 承認ファイル作成後に再実行した dry-run の出力は §8 に記載する
（本ファイル確定後に追記）。

## 7. README ledger への追加

`c0_freeze.py` の `[UNDERSPEC-CAL-D01]`〜`[UNDERSPEC-CAL-D08]` に続けて、
[`README.md`](../../README.md) の UNDERSPEC 台帳へ以下 2 件を追加した:

- `[UNDERSPEC-CAL-D09]`: campaign close 後に `split_secret` を ledger へ
  reveal（commit-reveal）し検証器が split を再計算できるようにする —
  設計正本 §7 は reveal を明記せず、C0 レビュー対象（本ファイル §5.1）。
- `[UNDERSPEC-CAL-D10]`: E_use evidence table の既定 path
  (`voice_genesis/calibration/config/e_use_table_v1.json`)。
- `[UNDERSPEC-CAL-D11]`: `EUseEvidenceRow.e_use_mode`（absolute/relative）
  拡張（本ファイル §4.1）。

## 8. Gate 1 承認後の再検証（dry-run 出力）

Gate 1 承認ファイル（`~/.vg_cal/approvals/gate1_campaign_execution.json`）
作成後、`python -m voice_genesis.calibration.c0_freeze`（既定の
`VG_CAL_APPROVAL_DIR`）を再実行した実測出力:

```
manifest_core_sha: 57fb498ecf874ffb647d81563c539f704640faa16bf563ba79a0b7d8c64e3b22
campaign_id (if frozen today): RUN10-CAL-20260902-57fb498e
authorization_nonce: bc91203d09aa11b517336820342537c8
blocked_codes: ['BLOCKED_C0_MANIFEST_INCOMPLETE']
missing_required_keys: ['repo.dirty_tree (must be exactly false, got True)', 'repo.dirty_tree (inspected checkout is actually dirty)']
gate2.armed: False
gate2.missing_factors: ['cli_flag:--armed', 'env:VG_CAL_C0_FREEZE_AUTHORIZED=1', 'approval_file:approval file not found: /root/.vg_cal/approvals/gate2_c0_freeze.json']
```

`missing_required_keys` は `repo.dirty_tree` の 2 件のみに縮小した（§4 の
E_use 違反 9 件・`frozen_design.cost_caps`/`stop_rules` 2 件が Gate 1 承認に
より解消）。`repo.dirty_tree` は本セッションの作業ツリーが実際に dirty
（D1b コード変更 + このドキュメント自体を含む、コミット前の正当な作業状態）
であることの反映であり、承認欠落とは無関係——`armed_freeze()` は
`--armed`/env/承認ファイルの三要素武装判定（`check_armed`）を別途行う経路
であり、dry-run の `blocked_codes` とは独立した判定軸である。`gate2.*` は
Gate 2 を本 Phase で意図的に発行していないことの反映（想定どおり）。

この dry-run が新規発行した `authorization_nonce`
（`bc91203d09aa11b517336820342537c8`）は Gate 1 承認ファイルの nonce
（§6.1 の `b88b3ed26feb9a0866ab223f979bf4da`）とは**異なる**——`dry_run()` は
呼び出しごとに新規の乱数を発行する設計であり（PR レビュー第 5 巡）、
Gate 1 は §6.1 の dry-run 時点の nonce を保持し続ける。Gate 2 を将来
発行する際は、その時点で改めて dry-run を実行し、その回の
`manifest_core_sha`/`authorization_nonce` を Gate 2 へ転記し、**Gate 1 の
nonce もその値へ更新する**必要がある（`GATE_REVIEW_BRIEF_v1.md` §6 の
運用手順どおり。本 Phase は Gate 1 のみのため未実施）。

`e_use_table_v1.json` の `USER_ACCEPTED_USE_BOUND` 9 行の
`source_hash_or_version` は、本ファイル（この §8 を含む最終内容）の
sha256 で確定する（§4 冒頭参照）。この最終パッチはメタデータ列のみの
変更であり、上記の dry-run 結果・`e_use_table` 検証結果には影響しない
（`e_use_table.validate_e_use_table()` は `source_hash_or_version` の値を
検証対象にしない）。

## 9. メモ編集時の対応

`DESIGN_VG_METER_CAL_DEBT_v1.0.md`/`IMPLEMENTATION_MAP_v1.md` のいずれかが
後日編集されると、Gate 1 承認ファイルの `design_doc_sha256`/`memo_sha256`
束縛は無効化される（`approvals.load_approval()` が hash mismatch で
未承認扱いにする）。その場合は:

```bash
python -m voice_genesis.calibration.approvals refresh --gate gate1 \
  --approval-dir ~/.vg_cal/approvals
```

でハッシュ欄のみを機械的に再スタンプできる（`approvals.refresh_document_hashes()`。
他フィールドは変更しない）。ただし機械的な再スタンプは**承認者本人の再確認を
代替しない**——ハッシュ更新後は改めて委任元（User）へ変更内容を確認し、
必要なら本ファイルの決定内容自体も見直すこと。再スタンプ後は
`voice_genesis/calibration/approvals/records/gate1_campaign_execution.2026-09-02.json`
（本リポジトリ内の参照用コピー）へも同じ内容を再コピーすること
（`approvals/README.md` が明記するとおり、loader はこの参照用コピーを
一切読まない——正本は checkout 外の `VG_CAL_APPROVAL_DIR` のみ）。

### 9.1 第 23 巡（PR #343）再刻印記録

`IMPLEMENTATION_MAP_v1.md` の編集により `memo_sha256` 束縛が上記手順どおり
無効化された（`design_doc_sha256` は不変のため一致を維持）。round 23 finding
（採用）に従い `refresh_document_hashes()` を checkout 外の正本
（`~/.vg_cal/approvals/gate1_campaign_execution.json`）と本リポジトリ内の
参照用コピー（`gate1_campaign_execution.2026-09-02.json`）の両方へ適用し、
`memo_sha256` のみを現在の `IMPLEMENTATION_MAP_v1.md` sha256 へ再スタンプした
（両ファイルは再スタンプ後も内容一致。`design_doc_sha256`/`approver`/
`authorization_nonce`/`cost_caps`/`e_use_bound_accepted`/`max_claim_scope`
はいずれも無変更）。機械的な再スタンプであり、委任元（User）本人による
変更内容の再確認は代替しない（本ファイル §9 冒頭の注記どおり）。

## 10. 再承認（2026-09-03 第 2 回）

1. 契機: #344 第 9 巡（memo hash のみの restamp は再承認ではない）。分類②で採用。
2. 01:46Z 承認以降の memo 変更: D75 撤回（matrix 456 セル復元）、D76（declared
   sweeps = truth-core 因子分解「nuisance 固定・truth 可変」、`frozen_design` へ
   凍結）、D77（manifest `declared_sweeps` 必須・完全一致検証）、D78（`BlockedCode`
   凍結 6 値復元、sweep 宣言不整合は `BLOCKED_C0_MANIFEST_INCOMPLETE` + 詳細
   フィールド）、D79（runner のスライス/再開堅牢化: `--time-budget-seconds`、
   `--discard-partial-groups` + `meter_call_group_discarded` ledger イベント、
   memo §6.5。commit 4169526）。
3. 設計事実の開示: 正本 matrix + holdout の (block, domain) 層化の下では、いずれの
   declared sweep も holdout 側に §10.4 の resolvable pair ≥3 を持てず、
   CALIBRATED_DIRECTIONAL は本 campaign で構造的に到達不能。DIRECTIONAL 天井の
   候補は NOT_EVALUABLE (`DIRECTIONAL_SWEEP_UNRESOLVABLE_ON_HOLDOUT`) で閉じる。
   ABSOLUTE 経路は影響なし。設計 v1.1 候補（sweep-aware 層化）として User へ
   報告済み・本 campaign では変更しない。
4. 承認内容の再確認: cost caps 3 値 / `budget_accounting_mode` / `max_claim_scope`
   / E_use 境界受容は 01:46Z と同一。変更なし。
5. nonce 取扱い: 本節時点の nonce は §6.1 の値のまま。Step 3 の Gate 2 dry-run が
   発行する nonce へ live/record の両方を更新する（§6.2 の手順）。本ファイルは
   E_use table により digest 固定されるため、freeze 後には編集しない。最終 nonce は
   record copy JSON と `c0_manifest.json` が正。
6. ファイル名日付と承認日時の関係（2026-09-03 追記）: 記録コピーのファイル名が
   持つ日付（`gate1_campaign_execution.2026-09-02.json`、
   `gate3_seal_acceptance.2026-09-03.json`）はそれぞれの承認記録の日付であり、
   gate1 側は 01:46Z の元の委任、gate3 側は本節（第 2 回）の再承認を指す——
   実際の承認日時は各 JSON の `approved_at_utc` が正。

### 10.1 D79 追補（2026-09-03）

Codex #345 第 1 巡の ③ 2 件（F0 再開 index 未使用 / PARTIAL_SLICE の parent CPU
が ledger 非記録）を採用し memo §6.5 を追補、続けて第 2 巡の ③ 1 件（c1/c4 再開時に
完了済み render を index で skip）も同じ memo 追補（§6.5.1）へ折り込んだ上で
memo_sha256 を `2c805ae839d624a640ac8ea0d0d372d95d17d99a996fdcf219e50115108115e0` へ
更新した。承認内容（cost caps /
claim scope / E_use 境界）は §10 と同一。

### 10.2 D79 追補 2（2026-09-03）

Codex #345 第 3 巡の ③ 1 件（F5: `render_stage.run_render_stage()` の resume
index skip が completing invocation でも一切検証を行わず、削除・破損した PCM の
上に `fixture_valid`/c4-holdout render→measure 遷移が falsely advance し得た
欠落——遷移直前 1 回のみの skip unit 全数検証 + `stop_event`/
`RenderResumeIndexIntegrityError` fail-closed を追加）と ② 1 件（F6:
`SliceStatus.instances_completed_this_run` が index skip 分まで含めて過大計上して
いた欠落を新規 render 分のみの計上へ修正）を採用し、続けてリハーサル 4
（`freeze_execution_15.txt`/`rehearsal4/slice_table.out`）の ③ 1 件（D:
`measure_stage.run_measure_stage()` が c2/c3a/c3b/c4 の measure サブフェーズで
完了済み instance を毎回フル `MeasurementRecord` へ再構成し続けていた欠落——
新設 `MeterCallIndex.is_complete()` による O(1) skip + completing invocation
時のみの 1 パス再構成へ修正）と ② 1 件（G: `instances_remaining` が「このスライスが
実際に歩いた instance 数」ベースの引き算で、budget が最初の instance 前に尽きると
完了済み分を無視し過大報告していた欠落——render_stage/measure_stage 双方を index
からの直接算出へ修正）を採用した。memo §6.5/§6.5.1/§6.5.2 を追補し、memo_sha256 を
`6f693a213881dfd8c6c5a213c969fc913ee5c6f6063d638eb5ae74c5d5232a0d` へ更新した。
承認内容（cost caps / claim scope / E_use 境界）は §10 と同一。

### 10.3 D79 追補 3（2026-09-03）

Codex #345 第 5 巡の 4 件（slice/resume ファミリーの終端掃討）を採用した。
③ 1 件（S1: `cli._build_f0_by_instance()` の budget 境界検査が F0 再開状態の
`meter_call_index.is_complete()` 参照より先に走っていたため、選択済み F0 が
全 instance で記帳済みでも resume 呼び出しが永久に `PARTIAL_SLICE` を報告し
続け得た欠落——index 参照を budget 検査より先に行う既存規則の未適用箇所を
是正。副次的に露呈した `instances_remaining` の同型過大報告も index からの
直接算出へ修正）。② 1 件（S2: `measure_stage.run_measure_stage()` の
`instances_completed_this_run` が、全 candidate が `is_complete()` fast path
を通っただけの instance も計上していた欠落——既存の `has_pending` 判定を
流用し新規分のみ計上へ修正）。③ 2 件（S3: c4-holdout の render サブフェーズ
完了後、measure サブフェーズが複数 slice に渡ると毎 slice が完了時整合検証
（第 3 巡 F5）を再実行し PCM を再読込・再ハッシュしていた欠落——新設
ノンゲート ledger event `holdout_render_valid`（`state.CampaignPhase`/
`vocab.ProcedureGate` の gate 語彙とは無関係）で render→measure 遷移時の
検証を 1 回だけ記録し、以降の completing invocation は O(1) のマーカー確認
のみで検証本体をスキップする。S4: 上記完了時整合検証（`_validate_skipped_
resume_outcomes`）が `.sha256` sidecar を一度も読まず、測定時検証
（`measure_stage._verify_and_load_rendered_pcm`）と異なる（緩い）チェック
集合になっていた欠落——両関数が共有する新設ヘルパー `render_stage.
_verify_pcm_sidecar()` へ集約し検証項目を同値化）。memo §6.5.3 を追補し、
memo_sha256 を
`d23305cd3feea1fba7904c43524a97621d04177d8756132b93556b894efe4d45` へ更新した。
承認内容（cost caps / claim scope / E_use 境界）は §10 と同一。

### 10.4 D79 追補 4（2026-09-03）

Codex #345 第 6 巡の ③ 1 件（discard された部分 meter_call group の
within-process CPU が、writer プロセスが hard-kill され `cli.py` `main()`
の `finally` に一度も到達しなかった場合、対応する `stage_summary`/
`slice_summary` が記帳されず永久に回収不能だった欠落——`meter_call_group_
discarded` event に新規フィールド `discarded_within_cpu_seconds`（discard
される部分グループの検証済み per-record `within_cpu_seconds` の最大値）を
追加し、`caps.cap_counters_from_ledger()` がこの event でのみ compute へ
1 回だけ加算する。他のどの箇所（`meter_call` 自身の compute 集計含む）にも
対応する減算/加算はなく exactly-once）を採用した。同時に 1 件境界宣言
（`[UNDERSPEC-CAL-D80]`、docs only・コード変更なし: C4 render の最終
holdout 遷移時再検証は不採用——測定値は測定時に入力検証済み・ledger の sha
が正本であり、測定後の render 成果物ストアの完全性は campaign 正当性契約の
外）。memo §6.5（discard event の payload 一覧）/§6.5.4 を追補し、
memo_sha256 を
`cb5ee5bdbe712f6ea6a62de58881de0735d3c066f0c8307ef0ad4264a7cc4c15` へ更新した。
承認内容（cost caps / claim scope / E_use 境界）は §10 と同一。


### 10.5 D79 追補 5（2026-09-03）

Codex #345 第 8 巡の ③ 3 件（invocation_id による明示的 invocation 識別への
置換、discard 時の live counter 課金 + remeasure 前 cap 検査、discard の
budget 検査への先行処理）を採用した——(1) round 7 finding #1 の
`dispatch_epoch`/`last_meter_epoch` ledger 順序ヒューリスティックは、SIGKILL
された writer と、discard フラグ無しで再試行した別 invocation の
`stage_summary` を取り違え得た（false-success）。`cli.py` `main()` が
process ごとに 1 個の `invocation_id`（`uuid.uuid4().hex`）を生成し、cap 会計
対象の全 event（`meter_call`/`render`/`slice_summary`/`stage_summary`/
`meter_call_group_discarded`/`worker_attempts_discarded`・`worker_failed`/
`stop_event`）へ一貫して付与する明示的識別へ置換し、
`caps.cap_counters_from_ledger()` の pairing rule を「discard される
group の WRITER 自身の `invocation_id` と同じ `invocation_id` を持つ
`stage_summary`/`slice_summary` が ledger に存在するか」の同一性判定へ
改めた。(2) `run_measurement_for_instance` が discard 時に live な
`cap_counters` へ課金・cap 検査せずに remeasure してしまい、既に凍結
compute cap を超過した状態のまま処理が進み得た欠落——discard の記帳直後、
`caps.is_invocation_id_summarized()` が非カバーと判定した場合のみ
`discarded_within_cpu_seconds` を live counter へ課金・persist・cap 再検査
し、remeasure 開始前に breach なら `COST_CAP_EXCEEDED` で fail-closed する
よう修正した。(3) `--discard-partial-groups` 指定時、budget を使い切った
状態で partial group が pending 扱いのまま budget 境界検査に先に捕まり
discard へ到達できず、短い budget での再実行が永久に回復しない欠落——
discard（非 dispatch 操作）を budget 検査より先に処理するよう
`run_measure_stage`/`_build_f0_by_instance` を修正した。**ファミリー終端
宣言**: 割込み invocation を跨ぐ cap 会計ファミリー（round 6 finding #3・
round 7 finding #1・round 8 finding #1/#2/#3）は第 8 巡で終端——以降は
新規の具体的な偽成功/偽失敗経路を示す指摘のみ採用する。memo §6.5（discard
event の payload 一覧に `invocation_id` を追記）/§6.5.5 を追補し、
memo_sha256 を
`144aa62beb8e72a3cb2d50fa5fcd28717a7864a5e23ac528500103ca960f7fab` へ更新した。
承認内容（cost caps / claim scope / E_use 境界）は §10 と同一。

### 10.6 D79 追補 6（2026-09-03）

Codex #345 第 12 巡の ③ 1 件（COMPLETE かつ never-discarded な meter_call
group の within CPU 未回収——第 8 巡のファミリー終端宣言後、新規の具体的な
偽成功経路として採用）を採用した。プロセスが group の 6 件目（最後）の
`meter_call` record を追記した直後に kill され、`cli.py` `main()` の
`finally` に一度も到達しなかった場合、discard すべき欠損が無い（6 件とも
揃っている）ため `meter_call_group_discarded` は一切記帳されず、再開時は
この group を「済」として扱い remeasure も discard も一切走らない——round
16 finding #3 の `within_cpu_seconds` 除外（`stage_summary`/`slice_
summary` が回収する前提）はこの writer について永久に成立せず、
`counters.json` の削除/rollback から再構成すると凍結 compute cap を
falsely 下回り得た（false-success）。修正: `caps.cap_counters_from_
ledger()` の pairing rule を discard event 限定から全 `meter_call`
group へ一般化——「writer の `invocation_id` に `stage_summary`/`slice_
summary` が一件も無い group」は discard の有無に関わらず within CPU を
回収対象とする。exactly-once 不変条件は discard 経路（現状維持）と、
forward scan 完了後の deferred pass（discard で pop されなかった key の
みを対象に、writer が非カバーなら最初の record の `within_cpu_seconds`
を 1 回加算）の 2 経路排他で維持する（discard された key は deferred
pass に現れず、deferred pass で課金される key は一度も discard されて
いない）。`reconcile_cap_counters()`（stage 起動時の pre-dispatch breach
check の入力）はこの関数を素通しするため、live counters への反映も
追加コード無しで含意される。memo §6.5.6 を追補し、memo_sha256 を
`bccab5978b12f4f6f36b2c9de25fd2b4d2abf87104a2a3f2861ce860a3473a70` へ
更新した。承認内容（cost caps / claim scope / E_use 境界）は §10 と同一。

### 10.7 D79 追補 7（2026-09-03）

Codex #345 第 13 巡の ③ 1 件（第 12 巡の deferred pass が回復経路で偽の
cost cap 超過を引き起こし得た欠落——新規の具体的な偽成功経路として採用）
を採用した。第 12 巡の deferred pass は COMPLETE/PARTIAL を区別せず、
未 summary な writer を持つ group を無条件に計上していた。
`--discard-partial-groups` による復旧では `cli.py main()` がまず ledger
から `cap_counters` を reconcile する（discard event はまだ存在しない）
ため、PARTIAL な group（hard kill 直後、6 record 未満）であっても未
summary であれば deferred pass がこの時点で within CPU を計上してしまい、
続く `_discard_partial_group()` が `discarded_within_cpu_seconds` を同じ
key 分もう一度課金する——同一 within CPU の二重計上により、
`max(persisted, derived)` の reconcile 規則がその水増しをそのまま実効値
として残し、凍結 compute cap を偽に超過し得た（false
`COST_CAP_EXCEEDED`）。修正: deferred pass を **COMPLETE な group のみ**
（当該 key の期待される全 repeat key が ledger 上に揃っている group）に
限定した。PARTIAL な group は discard されるまでいずれの経路からも
計上されない——discard 前は fail-closed のまま campaign を止め続けることが
安全装置そのものであり、discard event が PARTIAL group を唯一計上できる
経路であり続ける。exactly-once 不変条件（改訂）: 未 summary な writer を
持つ group の within CPU は厳密に 1 回だけ計上される——COMPLETE な group
は deferred pass 経由、PARTIAL な group は discard event 経由。summary
済みの writer は自身の summary でカバーされる（変更なし）。
`caps.cap_counters_from_ledger()`/`measure_stage._discard_partial_group()`
両方の docstring にこの不変条件を明記した。memo §6.5.7 を追補し、
memo_sha256 を
`1dcbb6a5c762f2d6c884d59ff1cc67a722e3b0b5e65d04d59d951472af16da6f` へ
更新した。承認内容（cost caps / claim scope / E_use 境界）は §10 と同一。

### 10.8 D83 正誤表（2026-09-04、campaign close 後）

(a) 誤: §10.7 の「偽成功経路」（第 13 巡の finding をこの語で 2 箇所ラベル）。
正: 「偽失敗経路（偽 `COST_CAP_EXCEEDED`）」——第 13 巡の finding は第 12 巡
deferred pass の within CPU 二重計上により凍結 compute cap を偽に超過させ
`COST_CAP_EXCEEDED` を誤って発生させ得た欠落であり、campaign を誤って
falsely advance させる偽成功経路ではなく、campaign を誤って falsely 停止
させる偽失敗経路である。§10.7 本文（432–459 行）はこの正誤表を付す形で
訂正し、原文そのものは書き換えない。

(b) 指摘元 = PR #345 第 14 巡（`GATE1_DECISION_RECORD.md` ~435 行
「Label the round-13 correction as a false failure」）。採否 = ②
将来汚染（記録の誤記が下流の記録を汚す）。

(c) 適用が campaign close 後になった理由: 本記録は E_use table により
digest 固定され、その E_use table は campaign RUN10-CAL-20260904-862dec28
の凍結入力の一つだった（holdout stage が `E_USE_TABLE_STALE_OR_MUTATED`
を検査する）ため、§10.7 本文への訂正は `campaign_closed`（ledger seq
70627、commit 25be66d）の後にのみ適用可能だった。

(d) campaign dir（`voice_genesis/calibration/campaigns/
RUN10-CAL-20260904-862dec28/`）は正誤表適用前の E_use table の凍結コピーを
別途保持しているため、閉じた campaign の記録はこの restamp の影響を受けない。
本 restamp は今後の campaign にのみ適用される。
