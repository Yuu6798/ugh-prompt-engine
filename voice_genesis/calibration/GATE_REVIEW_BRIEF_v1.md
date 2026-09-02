# RUN10-CAL 承認 Gate レビューブリーフ v1

対象: `DESIGN_VG_METER_CAL_DEBT_v1.0.md` §18 の 3 承認 Gate。
本ブリーフはユーザーの Gate 判断を助けるための読み物であり、正本ではない。
数値の提案は行わない（ユーザー判断が必要な箇所は「ユーザー記入」と明記する）。

参照元: `DESIGN_VG_METER_CAL_DEBT_v1.0.md`（§3, §7, §10.2, §14, §16-2, §18）、
`IMPLEMENTATION_MAP_v1.md`（§2.5〜§2.7, §6）、`README.md`（UNDERSPEC-CAL 台帳）、
`candidates/registry.py`。

---

## 1. 位置づけ

設計正本 §18 は、正本化承認（§17）とは別に、実行直前に個別判断すべき 3 件の
承認 Gate を定めている。正本 §19 の裁定は現在も
`execution_authorized: false` / `meter_changes_authorized: false` /
`run11_measurement_entry_authorized: false` であり、この 3 Gate が承認される
までこの状態は変わらない。

§18 原文（`DESIGN_VG_METER_CAL_DEBT_v1.0.md` 503–512 行）:

> ## 18. 実行時に別途必要なユーザー承認Gate（最終3件。4件目は作らない）
>
> 今回の正本化承認は、以下3件の承認を含まない。各Gateは実行直前に個別判断する。
>
> 1. **campaign 実行承認 + 費用上限 + 許容する最大 claim / E_use 境界**
>    （USER_ACCEPTED_USE_BOUND の受容を含む。価値・費用・主張範囲の判断）
> 2. **C0 freeze の実行承認**（repo への manifest/registry 書込・secret 生成を伴うため。
>    4-1 の承認対象操作）
> 3. **seal 保護水準の受容**（「事故的 leakage と事後改竄の検出」まで。外部鍵管理なしに
>    敵対的実行者は防げないというリスク受容）

各 Gate が解錠するものは次の通り（`IMPLEMENTATION_MAP_v1.md` §6.1「武装プロトコル」
と対応）:

| Gate | 承認ファイル（他エージェントが実装中） | 解錠される操作 |
|---|---|---|
| Gate 1 | `gate1_campaign_execution.json` | campaign 実行（D2 runner の armed 実行）+ cost caps 3 値の確定 + E_use 境界（許容する最大 claim）の受容 |
| Gate 2 | `gate2_c0_freeze.json` | C0 freeze の実行（`campaigns/<id>/` への repo 書込、`split_secret`/`render_root_secret` の生成） |
| Gate 3 | `gate3_seal_acceptance.json` | seal 保護水準（事故的 leakage・事後改竄検出まで）のリスク受容。D2 runner の続行に必要 |

**承認ファイルの配置先**: checkout 内 `voice_genesis/calibration/approvals/` ではなく、
**checkout 外の `VG_CAL_APPROVAL_DIR`（既定 `~/.vg_cal/approvals/`）**。dirty-tree
回避（§3 REQUIRED_BLOCKING の `dirty-tree=false` を満たすため）と HEAD 不変を理由に、
承認ファイル自体は repo に書き込まない設計に変更された。承認ファイルの内容 digest は
C0 manifest / freeze event に記録され、`design_doc_sha256`/`memo_sha256` は実ファイルの
ハッシュと照合される（`IMPLEMENTATION_MAP_v1.md` §6.1 の記述はこの変更を反映して
更新予定。ユーザーは 3 承認ファイルをこの外部ディレクトリに作成する）。

3 Gate はいずれも `--armed` フラグ + 対応する環境変数
（`VG_CAL_C0_FREEZE_AUTHORIZED=1` / `VG_CAL_CAMPAIGN_AUTHORIZED=1`）+ 承認ファイルの
3 要素が揃って初めて有効になる（`IMPLEMENTATION_MAP_v1.md` §6.1）。1 つでも欠ければ
`AUTHORIZATION_REQUIRED` で拒否される。

---

## 2. Gate 1 判断材料 — campaign 実行承認 + 費用上限 + 最大 claim / E_use 境界

### 2.1 費用上限（cost caps）— 規模の算定根拠

`DESIGN_VG_METER_CAL_DEBT_v1.0.md` §14（448–456 行）が示す設計値（R2/R3 で確定）:

| 項目 | 設計値（算定根拠） |
|---|---|
| renders 総数 | 4,560 本 |
| 音声換算時間 | 約 2.5 時間 |
| storage | 概ね 1 GB 以下 |
| baseline+holdout 段 meter calls | 13,680 call / implementation（1 実装あたり数〜10 CPU 時間オーダー。完全並列化可能） |
| selection 段 meter calls | 99 候補 × 自 family selection 行のみで総 call 数 ~10^5 オーダー |

`IMPLEMENTATION_MAP_v1.md` §6.4 が挙げる D2 dry-run の work unit 件数
（instances 2,280 / renders 4,560 / meter calls …）も同じ規模の裏付けとして参照できる。

cap は `c0_validate.COST_CAPS_REQUIRED_KEYS` と一致する厳密に 3 キー
`compute`（単位=秒）/ `storage`（単位=bytes）/ `budget`（単位=通貨単位）の 3 値を
C0 manifest に凍結し、超過時は fail-closed の stop event でキャンペーンを閉じる
（§14「超過で stop event（fail-closed、結果不完全のまま閉鎖）」）（PR #343 第 2 巡
採用）。`IMPLEMENTATION_MAP_v1.md` §2.6 は `COST_CAPS_REQUIRED_KEYS` を
compute/storage/budget の 3 次元に固定済み。

| cap フィールド | 単位 | 上記どの規模数値から見積もるか | 値 |
|---|---|---|---|
| `compute` | 秒 | meter calls 13,680×implementation 数（数〜10 CPU 時間オーダー、完全並列化可能）+ selection 段 ~10^5 call | ユーザー記入 |
| `storage` | bytes | renders 4,560 本・音声換算約 2.5 時間・storage 概ね 1 GB 以下 | ユーザー記入 |
| `budget` | 通貨単位 | 上記 compute を実行環境の課金レートに換算 | ユーザー記入 |

（フィールド名・単位は PR #343 第 2 巡採用: `compute_seconds`/`storage_bytes` 等の
旧称を廃し `compute`/`storage`/`budget` に統一）

cap の値そのものの決定と実行 Go はユーザー判断であり、本書は変数として保持する
（§14 原文）。本ブリーフも具体数値は提案しない。

### 2.2 最大 claim（各 construct が到達しうる ceiling 上限）

`candidates/registry.py` の宣言と `DESIGN_VG_METER_CAL_DEBT_v1.0.md` §16 の裁定に
基づく、meter family 別の claim ceiling 上限（実際の到達は holdout gate 判定で
下方に絞られうる。ここに挙げるのは「目標としうる上限」）。

| meter | construct | unit | registry 上の claim ceiling 上限 | 備考 |
|---|---|---|---|---|
| F0_CONTROL | fundamental_frequency | Hz | ABSOLUTE | claim-critical 外・上流 control |
| M3_FORMANTS | formant_centroid（baseline） | Hz | DIAGNOSTIC_ONLY | B0 診断参照 |
| M3_FORMANTS | formant_frequency（CEPSTRAL-POLES / BURG-LPC、F1/F2/F3 個別） | Hz | **ABSOLUTE** | claim-critical。ABSOLUTE 最大目標は F1/F2/F3 個別 Hz error（centroid を代用しない、§8） |
| M2_SPECTRAL_TILT | source_spectral_tilt（B0-HYBRID） | mixed(db_per_oct\|db) | NONE（INVALID_CIRCULAR 相当） | unit 混在のためそのままでは INVALID（§8, UNDERSPEC-CAL-C06） |
| M2_SPECTRAL_TILT | source_spectral_tilt（HARMONIC-OLS / THEILSEN） | dB/oct | **ABSOLUTE** | claim-critical |
| M2_APERIODICITY | harmonic_to_noise_ratio（B0-AUTOCORR / HNR-ACF） | dB | DIRECTIONAL | |
| M2_APERIODICITY | injected_noise_fraction（HARMONIC-RESIDUAL） | fraction | **ABSOLUTE** | claim-critical。独立 generator 上のみ ABSOLUTE 候補 |
| M2_APERIODICITY | world_d4c_aperiodicity（D4C） | fraction | DIAGNOSTIC_ONLY | WORLD 合成 fixture 上は SHARED_MODEL_DIAGNOSTIC |
| M4_RESONANCE | resonance_centroid / resonance_center_frequency | Hz | **DIAGNOSTIC_ONLY 上限で閉じる** | §16-1「M4 の M3 からの construct 独立性の証明」を対象外裁定。RUN10 では全候補 DIAGNOSTIC_ONLY |
| M5_TRANSITION | join_discontinuity_magnitude（WAVE-DISCONTINUITY / SPECTRAL-FLUX） | rms_amplitude_delta / spectral_flux_l1,l2 | DIRECTIONAL | |
| M6_IDENTITY | identity_component_distance（weighted_L1 / weighted_L2） | normalized_l1 / normalized_l2 | **DIRECTIONAL** | §12「M6 ceiling = CALIBRATED_DIRECTIONAL（物理量 absolute calibration を名乗らない）」 |

**claim-critical set**（`vocab.CLAIM_CRITICAL_SET`、D1 保守既定・C0 後の縮小/追加禁止）:
`M3_FORMANTS` / `M2_SPECTRAL_TILT` / `M2_APERIODICITY` の 3 meter のみ。この 3 meter が
いずれも CALIBRATED_ABSOLUTE または CALIBRATED_DIRECTIONAL に到達して初めて
`debt_discharged()` が真になる。M4/M5/M6/F0 は claim-critical set の外。

### 2.3 E_use evidence table（許容誤差の証拠付け）

`DESIGN_VG_METER_CAL_DEBT_v1.0.md` §10.2（335–349 行）より。C0 で候補結果を見る前に
`E_use[i]`（用途許容誤差）を根拠化して凍結する。`M[i] = E_use - U_GT - U_num <= 0` なら
ABSOLUTE は NOT_EVALUABLE（E_use は結果を見てから緩めない）。

**必須 13 列**:

`construct_id` / `unit` / `domain` / `intended_use` / `maximum_claim` /
`E_use_value` / `derivation_rule` / `evidence_class` / `source_id_or_url` /
`source_checked_at` / `source_hash_or_version` / `applicability_argument` /
`review_status`

**`evidence_class` 語彙**（各 1 行で意味）:

| evidence_class | 意味 |
|---|---|
| `NORMATIVE_SPEC` | 規格・仕様書が定める許容誤差をそのまま採用 |
| `FIRST_PRINCIPLES_BOUND` | 原理から機械導出できる限界（例: meter の宣言分解能そのもの） |
| `VALIDATED_REFERENCE` | 検証済み文献値（例: 知覚 JND）を出典 hash 付きで採用 |
| `USER_ACCEPTED_USE_BOUND` | ユーザーが用途上妥当と判断して受容する上限（ユーザー判断1へ統合。本 Gate 1 の一部） |
| `UNJUSTIFIED` | 根拠化できていない。**数値 placeholder を作らない**。自動 ceiling が適用される |

**UNJUSTIFIED の自動 ceiling 規則**（結果後付け禁止のための fail-safe）:
独立 truth order が事前に立つ construct → `DIRECTIONAL`、立たない construct →
`DIAGNOSTIC_ONLY`。いずれも NOT_EVALUABLE へは落とさない。

D1 実装（`IMPLEMENTATION_MAP_v1.md` §6.3）は、`e_use_table.py` のテンプレート生成が
全 construct 行を `evidence_class: UNJUSTIFIED` かつ `e_use_value: null` で出力すると
規定している——つまり **このテーブルの中身（証拠の記入）はユーザー Gate 1 判断の
本体そのもの**であり、コード側は空の worksheet しか用意しない。

**worksheet の行粒度**（PR #343 第 2 巡採用）: `e_use_table.generate_template()` は
`candidates/registry.py` を読み、そこに登録された全 99 candidate から一意な
**(construct_id, unit, domain) の 3 つ組**を抽出し、その組ごとに 1 行を出力する
（同一 construct・同一 unit でも domain 宣言が異なれば別行——例: M3 の
CEPSTRAL-POLES family と BURG-LPC の fs'=4000Hz / 5000Hz resample family は
domain が異なるため formant_frequency/Hz でも 3 行に分かれる。F0_CONTROL の
baseline と pyin family も domain 宣言が異なるため 2 行に分かれる）。registry 現状
（99 candidate）では一意な 3 つ組は **20 件**であり、下表はその 20 行を全て列挙した
ものである。値セルは全てユーザー記入。候補となる evidence source の例を「参考」欄
に示すが、実際の採否・記入はユーザーが行う。

**construct 別 worksheet**（`candidates/registry.py` 由来の 20 件、一意
(construct_id, unit, domain) 単位。PR #343 第 2 巡採用で registry 全走査から再生成）:

| construct_id | unit | domain | 参考: 想定される evidence_class の候補源 | E_use_value | evidence_class | review_status |
|---|---|---|---|---|---|---|
| fundamental_frequency（F0-B0-CURRENT baseline） | Hz | 宣言済み primary F0 帯 (C3-G4 anchor) + boundary probe | meter 宣言分解能 → `FIRST_PRINCIPLES_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| fundamental_frequency（F0-PYIN-FRAME*-HOP* family ×4） | Hz | 宣言済み primary F0 帯 (C3-G4 anchor) + boundary probe。fmin=80/fmax=600 固定 | meter 宣言分解能 → `FIRST_PRINCIPLES_BOUND` / 音声知覚上のピッチ JND 文献 → `VALIDATED_REFERENCE` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| formant_centroid（M3-B0-CURRENT-CENTROID baseline） | Hz | DIAGNOSTIC_ONLY: centroid は F1/F2/F3 個別 Hz error の代用にならない | 診断用途としての許容幅をユーザーが宣言 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| formant_frequency（M3-CEPSTRAL-POLES family ×18） | Hz | baseline と同族（ケプストラム liftering 系）。band_lo=300Hz 固定 | meter 宣言分解能 → `FIRST_PRINCIPLES_BOUND` / 音声知覚上の formant JND 文献 → `VALIDATED_REFERENCE`（source hash 必須） | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| formant_frequency（M3-BURG-LPC family, fs'=2×4000Hz resample ×12） | Hz | 唯一の独立 family。fs'=2*4000Hz へ決定的 resample 必須 | meter 宣言分解能 → `FIRST_PRINCIPLES_BOUND` / formant JND 文献 → `VALIDATED_REFERENCE`（source hash 必須） | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| formant_frequency（M3-BURG-LPC family, fs'=2×5000Hz resample ×12） | Hz | 唯一の独立 family。fs'=2*5000Hz へ決定的 resample 必須 | meter 宣言分解能 → `FIRST_PRINCIPLES_BOUND` / formant JND 文献 → `VALIDATED_REFERENCE`（source hash 必須） | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| source_spectral_tilt（M2T-B0-CURRENT-HYBRID baseline） | mixed(db_per_oct\|db) | unit 混在のためそのままでは INVALID（設計正本 §8） | NONE（INVALID_CIRCULAR 相当）——ceiling は claim ceiling 表（§2.2）で確定済みのため E_use 記入は本来不要（UNDERSPEC-CAL-C06 参照） | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| source_spectral_tilt（M2T-HARMONIC-OLS family ×6） | dB/oct | 20*log10(A_k) vs log2(k) 線形回帰。H1-H2 フォールバックなし | 回帰の数値分解能 → `FIRST_PRINCIPLES_BOUND` / tilt 知覚閾に関する文献 → `VALIDATED_REFERENCE` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| source_spectral_tilt（M2T-HARMONIC-THEILSEN family ×6） | dB/oct | Theil-Sen（中央値ベース）勾配。H1-H2 フォールバックなし | 回帰の数値分解能 → `FIRST_PRINCIPLES_BOUND` / tilt 知覚閾に関する文献 → `VALIDATED_REFERENCE` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| harmonic_to_noise_ratio（M2A-B0-AUTOCORR-PERIODICITY baseline） | dB | harmonic/noise 帯域エネルギー比（FFT ベース） | 用途上許容する dB 幅をユーザーが直接宣言 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| harmonic_to_noise_ratio（M2A-HNR-ACF family ×8） | dB | 正規化自己相関ピーク → HNR。独立実装は directional/monotonicity 上限 | 用途上許容する dB 幅をユーザーが直接宣言 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| injected_noise_fraction（M2A-HARMONIC-RESIDUAL family ×12） | fraction | comb-remove 後の残差/全パワー比。独立 generator 上のみ ABSOLUTE 候補 | 注入量そのものの量子化限界 → `FIRST_PRINCIPLES_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| world_d4c_aperiodicity（M2A-D4C family ×3） | fraction | WORLD 合成 fixture 上は SHARED_MODEL_DIAGNOSTIC。F0 入力は選択済み F0_CONTROL 固定 | 診断用途としての許容幅をユーザーが宣言 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| resonance_centroid（M4-B0-CURRENT-CENTROID baseline） | Hz | 全 M4 候補は RUN10 で DIAGNOSTIC_ONLY 上限に閉じる（設計正本 §16） | 診断用途としての許容幅をユーザーが宣言 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| resonance_center_frequency（M4-LOCAL-PROMINENCE family ×4） | Hz | 全 M4 候補は RUN10 で DIAGNOSTIC_ONLY 上限に閉じる（設計正本 §16。M3 との construct 独立性は未証明） | 診断用途としての許容幅をユーザーが宣言 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| join_discontinuity_magnitude（M5-WAVE-DISCONTINUITY family ×3） | rms_amplitude_delta | 短窓 RMS の frame-to-frame jump | 検出感度の用途要件 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| join_discontinuity_magnitude（M5-SPECTRAL-FLUX family, L1 ノルム ×2） | spectral_flux_l1 | frame-to-frame 振幅スペクトル差分のノルム | 検出感度の用途要件 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| join_discontinuity_magnitude（M5-SPECTRAL-FLUX family, L2 ノルム ×2） | spectral_flux_l2 | frame-to-frame 振幅スペクトル差分のノルム | 検出感度の用途要件 → `USER_ACCEPTED_USE_BOUND` | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| identity_component_distance（M6-WEIGHTED-L1） | normalized_l1 | CLAIM_CRITICAL_SET 全 member が CALIBRATED_ABSOLUTE のときのみ計算（DIRECTIONAL 上限 §12） | E_use[j] は各 component 側 E_use の正規化に従属するため、上記 construct 側の記入が前提 | ユーザー記入 | ユーザー記入 | ユーザー記入 |
| identity_component_distance（M6-WEIGHTED-L2） | normalized_l2 | CLAIM_CRITICAL_SET 全 member が CALIBRATED_ABSOLUTE のときのみ計算（DIRECTIONAL 上限 §12） | E_use[j] は各 component 側 E_use の正規化に従属するため、上記 construct 側の記入が前提 | ユーザー記入 | ユーザー記入 | ユーザー記入 |

---

## 3. Gate 2 判断材料 — C0 freeze の実行承認

C0 freeze は repo への `campaigns/<id>/` 書込と secret 生成を伴う操作であり
（`IMPLEMENTATION_MAP_v1.md` §18-2 対応操作）、以下がそのままユーザーレビュー対象になる。

### 3.1 UNDERSPEC-CAL 台帳（全件、`README.md` より逐語抽出）

数値・グリッド・エンコーディングを設計正本が確定していない箇所について、実装が
「§5.2/§8 の件数と厳密一致する最も単純な選択」を採って記録したもの。**C0 freeze
承認時のユーザーレビュー対象であり、コードが正本を上書きするものではない**
（`IMPLEMENTATION_MAP_v1.md` §3）。

| tag | module | 内容 |
|---|---|---|
| `UNDERSPEC-CAL-01` | `vocab.py` | `IndependenceTier.CROSS_IMPLEMENTATION` の claim ceiling 写像を `ABSOLUTE`（tier が許す上限。実際の到達は gate 判定で下方に絞られる）とした |
| `UNDERSPEC-CAL-02` | `streams.py` | HKDF `info` のフィールド連結を、区切り文字連結ではなく衝突耐性のある長さ接頭辞（4-byte big-endian）方式で実装した |
| `UNDERSPEC-CAL-03` | `splitter.py` | stratum 内 largest-remainder の closed-form 導出（`n mod 4` で場合分け）と、SEL/HOLD 端数 tie（構造的に常に同値）を stratum 内 HMAC 順位最大行の末尾ニブル偶奇で決める規則 |
| `UNDERSPEC-CAL-04` | `splitter.py` | family 合計の厳密一致は pairwise swap だけでは原理的に不可能なため、`reason="family_total"` の片道移動を導入し `reason="coverage"` の真の 2 行交換と区別した |
| `UNDERSPEC-CAL-05` | `tolerance.py` | floor 導出式を「PCM 量子化半ステップ・float eps bound・meter 宣言分解能の `max()`」として機械導出した |
| `UNDERSPEC-CAL-06` | `splitter.py` | coverage repair の donor 選択に、victim と対称な `_safe_to_remove` 安全性検査を適用した（同一行が違反間を往復し repair が収束しない振動を防ぐ） |
| `UNDERSPEC-CAL-07` | `provenance.py` | ledger `entry_sha` の digest 対象に `payload`+`prev_sha` に加えて `seq` も含めた（chain 上の位置を署名に取り込む保守的選択） |
| `UNDERSPEC-CAL-08` | `m6_identity.py` | M6 component の識別子型を `vocab.MeterId` に固定した（他 meter の校正 status と直接突合できる一貫性を優先） |
| `UNDERSPEC-CAL-B01` | `fixtures/axes.py` | negative control の `TOO_SHORT` (0.02s) / `INVALID_SR` (8000Hz) 具体数値。boundary probe（0.10s/16000Hz）より外側の値とし、boundary 探査点と negative 探査点を区別した |
| `UNDERSPEC-CAL-B02` | `fixtures/axes.py` | targeted interaction の "low-SR/high-SR" を boundary SR 極値 (16000/96000) と定義した。primary SR 極値 (24000/48000) のみで表現すると、anchor F0 が既に primary 低域にある family（FORMANT_GT 等）で interaction 行が truth core anchor 行と完全一致する退化（row_id 衝突）が生じるため |
| `UNDERSPEC-CAL-B03` | `fixtures/axes.py` | FORMANT_GT の confound/boundary/negative 行（truth core 以外）の `generator_impl` を `cascade` に固定した（implementation は truth core でのみ因子として直積される） |
| `UNDERSPEC-CAL-B04` | `fixtures/axes.py` | RESONANCE_GT / TRANSITION_GT（truth core に F0 を因子として持たない family）の励起/context 用 F0 を primary domain 中央値 C4 (261.626Hz) に固定した |
| `UNDERSPEC-CAL-B05` | `fixtures/axes.py` | TRANSITION_GT の 3 severity（設計正本が「C0 表で固定」として据え置く discontinuity magnitude）を low=0.15/medium=0.35/high=0.65（無次元、join_type ごとに解釈）とした |
| `UNDERSPEC-CAL-B06` | `fixtures/axes.py` | TRANSITION_GT の 2 duration class（join 遷移窓長。primary "duration" 軸のクリップ全長とは別概念）を short=5ms/long=50ms とした |
| `UNDERSPEC-CAL-B07` | `fixtures/axes.py` | IDENTITY_CAUSAL_SWEEP の 4 founder（distinct F0/formant-set/tilt parameter bundle）の具体値。primary F0 4 水準 + FORMANT_GT pole set の一部を再利用し内部一貫性のある bundle を凍結した |
| `UNDERSPEC-CAL-B08` | `fixtures/axes.py` | IDENTITY_CAUSAL_SWEEP の 3 trait の generator-unit→物理量換算則（F0: 1 unit=5 cents、FORMANT_SHIFT: 1 unit=pole周波数 2% scale、TILT_SLOPE: 1 unit=1 dB/oct）を凍結した |
| `UNDERSPEC-CAL-B09` | `fixtures/axes.py` | F0_CONTROL の第 2 confound anchor（設計正本 §2.7 の anchor 一覧は単一 anchor C4@48k のみ明記するが、confound 件数検算「F0 24=11+6+7」は FORMANT_GT/IDENTITY と同型の 2-anchor 構造を要求する）を G4@48k（F0 のみ変更）とした |
| `UNDERSPEC-CAL-B10` | `fixtures/axes.py` | single-anchor family（TILT/APERIODICITY/RESONANCE/TRANSITION）の positive control 用第 2 anchor（§2.7 control 共有契約は「2 anchor truth rows per family」を要求するが、これら 4 family の anchor 一覧は単一 anchor のみ明記）を、truth core grid 上で A1 と最も対照的な点（primary sweep 軸の反対端）とした |
| `UNDERSPEC-CAL-B11` | `fixtures/generators/tilt.py` | TILT_GT の dB/oct slope 定義を `A_k[dB]=slope*log2(k)`（高調波次数 k の log2、`k=1` を 0dB 基準）の 1 定義に凍結した（回帰ルーチンは使わず、meter 側の tilt 推定と非共有） |
| `UNDERSPEC-CAL-B12` | `fixtures/generators/transition.py` | `duration_class`（short=5ms/long=50ms, `UNDERSPEC-CAL-B06`）を 4 join type（amplitude-step/phase-jump/spectral-envelope-switch/crossfade）全てで「join time を中心とした raised-cosine 遷移窓の物理的な長さ」として具現化した |
| `UNDERSPEC-CAL-C01` | `candidates/impl/b0_wrappers.py` | 5 つの B0 candidate と harness 関数の配線対応（`F0-B0-CURRENT`→`estimate_f0_hps`、`M3-B0-CURRENT-CENTROID`/`M4-B0-CURRENT-CENTROID`→ともに `formant_centroid_and_f1`、`M2T-B0-CURRENT-HYBRID`→`source_tilt_v2`、`M2A-B0-AUTOCORR-PERIODICITY`→`hnr_db_approx`）を設計正本の候補名一致から導いた |
| `UNDERSPEC-CAL-C02` | `candidates/impl/formant_cepstral.py` | M3 formant 系のピーク missing 閾値を「帯域内ピーク 0 個で OUTPUT_MISSING」とした（M2T の「K 本未満は missing」に相当する明記が M3 にはないため、最も単純な閾値を採用） |
| `UNDERSPEC-CAL-C03` | `candidates/impl/formant_burg.py` | Burg LPC 実装詳細（リサンプラ=`scipy.signal.resample_poly`、preemphasis=1 次ハイパス時定数、窓関数=Hamming、極選択=単位円内・虚部正・周波数昇順）を機械的に選んだ |
| `UNDERSPEC-CAL-C04` | `candidates/impl/resonance_prominence.py` | 包絡平滑化を移動平均（box filter、帯域幅 Hz→ビン数換算）とした |
| `UNDERSPEC-CAL-C05` | `candidates/registry.py` | `complexity_rank` を「本モジュール内の宣言順の 0-based 連番」とした（実計算コストの実測値ではない） |
| `UNDERSPEC-CAL-C06` | `candidates/registry.py` | `M2T-B0-CURRENT-HYBRID`（「そのままでは INVALID」）を vocab 4-tier のうち意味が最も近い `INVALID_CIRCULAR`/`ClaimCeiling.NONE` へ割り当てた |
| `UNDERSPEC-CAL-C07` | `c0_validate.py` | RECORDED_OR_ABSENT（§3.2）キーが manifest に全く存在しない場合を REQUIRED_BLOCKING と同様の missing 扱いとした。`WEAK_ENV_LOCK` 降格 annotation は §3.2 の 5 項目全てに一律適用する |
| `UNDERSPEC-CAL-C08` | `c0_validate.py` | RNG 台帳 entry のフィールド名を `{"stream_name": str, "seeded": bool}` に固定した |
| `UNDERSPEC-CAL-C09` | `candidates/registry.py`, `candidates/impl/aperiodicity.py` | M2A-HARMONIC-RESIDUAL の残差帯域グリッド「0–Nyquist」を実装トークン `broadband`（D4C 側の帯域トークンと統一）へ写像した |
| `UNDERSPEC-CAL-C10` | `c0_validate.py` | path+hash 系マップの各エントリを `path(非空文字列) -> sha256(64 桁小文字 16 進)` 形状として検証した |
| `UNDERSPEC-CAL-C11` | `c0_validate.py` | `frozen_design.meter_specs` が全 meter family（vocab.MeterId 全件）をカバーすることを要求し、欠落 meter を個別キーとして列挙する規則にした |
| `UNDERSPEC-CAL-C12` | `c0_validate.py` | `independence_ledger` の各エントリ値を `vocab.IndependenceTier` の閉語彙メンバーであることまで検証。ledger のキー集合は registry の凍結 99 candidate_id 全集合と完全一致・各 entry の tier は registry 宣言 tier と一致することを要求する |
| `UNDERSPEC-CAL-C13` | `c0_validate.py` | `rng_ledger` エントリの形状を `{"stream_name": str(非空), "seeded": bool}` に加え、`seeded=true` の場合は非空 `public_seed_id` を必須とした |
| `UNDERSPEC-CAL-C14` | `c0_validate.py` | path+hash 系マップが要求すべき inventory を、版管理されコミットされた閉じた inventory ファイル `c0_path_inventory.json` として機械定義した |
| `UNDERSPEC-CAL-C15` | `fixtures/generators/resonance.py` | declared `noise_snr_db` の nuisance noise を prominence 較正の floor 測定より前に解析的に折り込む式を採用した。同様に `context` も較正パスへ折り込んだ |
| `UNDERSPEC-CAL-C16` | `streams.py`, `c0_validate.py` | C0 の `rng_ledger` 記録粒度を「family ごとの generator render stream 1 個 ∪ `"split/hmac"` ∪ `"split/tiebreak"`」の 9 stream closed set に固定した |
| `UNDERSPEC-CAL-C17` | `c0_validate.py` | `frozen_design` の各セクションが持つべき完全なネスト鍵集合を module-level frozen 定数として定義した（`METER_SPEC_REQUIRED_KEYS`/`FIXTURE_SPEC_REQUIRED_KEYS`/`SPLIT_SPEC_REQUIRED_KEYS`/`SELECTION_SPEC_REQUIRED_KEYS`/`PROVENANCE_SPEC_REQUIRED_KEYS`/`COST_CAPS_REQUIRED_KEYS`）。`stop_rules` は非空チェックのみ |
| `UNDERSPEC-CAL-C18` | `c0_validate.py` | `frozen_design` ネスト鍵と `stop_rules` に BOUNDED shape validation を追加（`*_hash`/`*_sha256` → 64桁hex sha256、`confound_axes`等 → 非空 list、`parameter_grid` → 非空 mapping、`generator_version`/`schema_version` → 非空白 str）。値の意味論的相互検証は armed producer 実装時の別 PR の責務とした |

台帳の脚注（`README.md`）: 上記に加えて Codex レビュー各巡で採用され
`IMPLEMENTATION_MAP_v1.md` に凍結された仕様（`canonical.py` の `vgcal-canon/1` 版管理・
`provenance.py` の単一 writer 境界・`gates.py` の DIRECTIONAL resolvability 分解・
`splitter.py` の重複 row_id 拒否・`selection.py` の ceiling 階級間裁定・
`observables.py` の `u_rep` singleton 除外・`m6_identity.py` の CLAIM_CRITICAL_SET 全
member ABSOLUTE 必須化）は、正本の一部として実装済みのため UNDERSPEC 台帳には
数えていない。ただし次節 3.2 の 3 件はこれとは別に「正本への correction 候補」
として扱われている（未反映の正本改訂案）。

### 3.2 正本への correction 候補（`IMPLEMENTATION_MAP_v1.md` §2.5、3 件）

正本は read-only のため本実装では改訂を行わず、以下の解釈で実装し
**C0 freeze 承認時のユーザーレビュー対象**とした 3 件。

| 候補 | 一行理由 |
|---|---|
| **U_rep singleton 除外**（§6/§10.1） | repeat 数 ≥2 の process group のみを `u_rep` の母集団とする。n=1 の range は 0 でなく未定義であり、singleton を含めると range が構造ゼロとして q95 を不当に希釈するため（除外は U_rep を大きくする fail 側の保守的読み） |
| **R_ij の単位分解**（§10.4） | v1.0 の合算式は truth 単位（U_GT/U_num）と output 単位（U_rep/U_proc）を無条件加算しており、truth と output の construct 単位が異なる候補（例: M2A 系）では無意味になる。truth 側 resolvability と output 側有意性の二連言に分解し、単位可換な construct には v1.0 式も追加で課す（保守性を弱めない） |
| **M6 all-member rule**（§12） | 「CALIBRATED_ABSOLUTE component のみで構成」を部分集合の再構成と読まず、凍結済み CLAIM_CRITICAL_SET の全 member が CALIBRATED_ABSOLUTE のときのみ M6 distance を計算し、それ以外は NOT_EVALUABLE とする（D1・§8・§15 と整合する唯一の読み） |

### 3.3 §2.6/§2.7 の凍結選択（レビューチェックリスト）

`IMPLEMENTATION_MAP_v1.md` §2.6/§2.7 は「実装者裁量に残さず本 memo で凍結する」
項目であり、コード内の暗黙変更を禁止している。C0 freeze 承認前に確認すべき
チェックリストとして列挙する（詳細は `IMPLEMENTATION_MAP_v1.md` 83–188 行を参照）。

- [ ] selection の ceiling 階級間裁定規則（ABSOLUTE pool 優先 → DIRECTIONAL pool →
      `SELECTION_FAILED_CLOSED`。DIAGNOSTIC_ONLY ceiling 候補は holdout claim の
      selection 対象にならない）
- [ ] §8 未確定パラメタグリッド 7 件の具体水準（M2A-HNR-ACF 8 / M2A-HARMONIC-RESIDUAL
      12 / M2A-D4C 3 / M4-LOCAL-PROMINENCE 4 / M5-WAVE-DISCONTINUITY 3 /
      M5-SPECTRAL-FLUX 4 / F0-PYIN 4）
- [ ] fixture matrix 456 行の truth core 因子分解（family 別件数: F0 12 / FORMANT 60 /
      TILT 30 / APERIODICITY 36+bandwise 24 / RESONANCE 24 / TRANSITION 24 /
      IDENTITY 60）
- [ ] anchor 水準（SR 48000 / gain −12 dBFS / duration 1.00s / noise clean /
      context steady-isolated、family 別 anchor 値）
- [ ] confound block の決定的レシピ（正準 nuisance 系列 11 行 + targeted interaction +
      第2 anchor 系列、family 別「先頭 N 件」規則）
- [ ] boundary/negative block の決定的レシピ（正準 boundary 系列 9 行 + negative
      control 系列 6 行、family 別「先頭 (N−3)+3」規則）
- [ ] targeted interactions の per-family 実列挙（F0/FORMANT/IDENTITY=全6件、
      TILT/RESONANCE=k=1、TRANSITION=k=4、APERIODICITY=k=0）
- [ ] control 共有契約（N_neg/N_pos は instance 数で数える。negative control は
      leakage 除外集合に含む・positive control（truth core 行）は含まない。
      truth-core 行の split 当たり被覆下限 2）

### 3.4 dry-run 手順（Gate 2 承認前に実施可能な事前検証）

```bash
python -m voice_genesis.calibration.c0_freeze
```

`IMPLEMENTATION_MAP_v1.md` §6.3 の設計により、dry-run は manifest を生成・検証して
報告するのみ（書込なし・secret 生成なし）。武装プロトコル 3 要素
（`--armed` + `VG_CAL_C0_FREEZE_AUTHORIZED=1` + 承認ファイル）が揃っていない状態では
`AUTHORIZATION_REQUIRED` で拒否されるが、dry-run 自体は cost caps / stop rules が
未記入（Gate 1 未承認）の段階でも実行でき、その場合は cost caps / stop rules の
欠落のみをブロック理由として報告することが期待される（`e_use_table.py` /
`cost_caps.py` が Gate 1 承認前は空 worksheet ・未確定のため）。

---

## 4. Gate 3 判断材料 — seal 保護水準の受容

### 4.1 正直な保護水準宣言（§7 原文、254–255 行）

> **保護水準の正直な宣言**: 本 seal は「事故的 leakage と事後改竄の検出」水準であり、
> 外部鍵管理なしに台帳外の敵対的実行者は防げない（ユーザー判断3）。

### 4.2 外部鍵管理を対象外とした裁定（§16-2 原文、483–484 行）

> **外部鍵管理による敵対的秘匿の実現** — 2エージェント+共有 Drive 構成では原理的に
> 不成立。保護水準を「事故的 leakage・事後改竄の検出」と正直に宣言（ユーザー判断3）。

### 4.3 seal が検出するもの / 防げないもの

| 区分 | 内容 |
|---|---|
| **検出する** | holdout 行の unseal 前の事故的な render/meter 呼び出し（append-only measurement ledger に row_id 付きで全 render・全 meter call を記帳し、unseal 前初出を `BLOCKED_LEAKAGE` として検知） |
| **検出する** | unseal 条件（`baseline_audit_sha + candidate_space_sha + selection_rule_sha + selected_candidate_sha + selection_freeze_event_sha` の存在と相互参照一致）を介した、unseal 後の候補・parameter・threshold・domain の事後改竄（hash chain 経由で検出） |
| **防げない** | ledger の外で動く敵対的実行者（台帳に記帳せず直接ファイル操作する運用主体）を止める仕組みはない |
| **防げない** | 外部鍵管理相当の秘匿は 2 エージェント+共有 Drive 構成では原理的に不成立。`split_secret`/`render_root_secret` は C0 freeze スクリプトが実行環境内で生成し、Drive には SHA-256 commitment のみを置く（plaintext を Drive に置く案は§7で明示的に棄却されている）が、これは「秘匿」ではなく「事後検出」のための commitment である |

Gate 3 の受容対象は、この宣言された水準（事故検出・改竄検出まで）を「十分」と
みなしてリスクを引き受けるかどうかの判断である。

---

## 5. 処理順と各 Gate 後に起こること

1. **Gate 1 承認**（`gate1_campaign_execution.json` を `VG_CAL_APPROVAL_DIR`
   （既定 `~/.vg_cal/approvals/`、checkout 外）に記入・配置）
   → producer（`cost_caps.py` / `e_use_table.py`）が cost caps 3 値と E_use
   境界を確定できるようになる。まだ C0 freeze・secret 生成・repo 書込は起きない。
2. **Gate 2 承認**（`gate2_c0_freeze.json` を同じく `VG_CAL_APPROVAL_DIR` に
   記入・配置）
   → `--armed` + `VG_CAL_C0_FREEZE_AUTHORIZED=1` + 承認ファイルの 3 要素が揃うと
   `c0_freeze.py` の armed 実行が可能になる。この承認は producer が dry-run 時点で
   報告する `manifest_core_sha`（`approvals` セクションと secret-commitment を除いた
   manifest 本体の sha。承認ファイル自体は campaign_id を含まない）を束縛対象とする
   （`IMPLEMENTATION_MAP_v1.md` §6.1/§6.2。PR #343 第 2 巡採用）: secret 生成 →
   commitment 記入 → splitter 実行 → 実現 split 表 → freeze event を ledger 先頭に
   記帳 → `campaigns/<id>/` へ書込（`c0_manifest.json` / `realized_split.json` /
   `ledger.jsonl` / `events/*.json`）。この `<id>`（campaign_id）は
   `RUN10-CAL-<YYYYMMDD>-<manifest_core_sha[:8]>` として事後導出される。secret は
   `VG_CAL_SECRET_DIR`（既定 `~/.vg_cal/secrets/<campaign_id>/`、mode 0600）に生成
   され repo には commitment のみが残る。承認ファイル自体も checkout 外のため、この
   段階で repo の dirty-tree 状態を汚さない。**git commit はしない**（ユーザー操作）。
3. **Gate 3 承認**（`gate3_seal_acceptance.json` を `VG_CAL_APPROVAL_DIR` に
   記入・配置）
   → runner（D2）が続行してよい状態になる。Gate 3 は freeze **後**に成立するため
   C0 manifest には含まれない: D2 runner が sealed-stage 作業に入る前に、この承認
   ファイルの sha256 を伴う `GATE3_ACCEPTED` ledger event を記帳することで束縛する
   （`IMPLEMENTATION_MAP_v1.md` §6.2。PR #343 第 2 巡採用）。`--armed` +
   `VG_CAL_CAMPAIGN_AUTHORIZED=1` + 承認ファイルが揃うと、`c1-fixtures` →
   `c2-baseline` → `c3-selection` → `unseal` → `c4-holdout` → `close` の手続
   Gate を ledger 駆動で進められる。**`c1-fixtures` は calibration 行・
   selection 行・negative control 行のみを render する**（holdout 行は
   render しない）。holdout 行の render は `unseal` 後の `c4-holdout` で
   初めて行われる——これは §7 の leakage 契約（holdout 行の unseal 前初出を
   `BLOCKED_LEAKAGE` とする規則）と整合させるための設計であり、C1 が
   「全 fixture の render」を意味するわけではない点に注意（Gate 3 の
   seal 受容判断に直接関わる: unseal 前に holdout 音声そのものが
   生成されないため、事故的 leakage の物理的な発生源が構造的に
   縮小されている）。

---

## 6. 承認ファイル記入例（D1 完了後に追記）

（プレースホルダー。`approvals.py` / `approvals/README.md` / `c0_freeze.py` /
`e_use_table.py` / `cost_caps.py` の実装完了後、実際の承認ファイルスキーマ
（`{approver, approved_at_utc, campaign_id, design_doc_sha256, memo_sha256}` の
具体的な記入例と、E_use table / cost caps の実ファイル配置パス）をここに追記する。）
