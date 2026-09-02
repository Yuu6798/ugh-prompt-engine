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
