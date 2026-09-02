---
document_id: VG-METER-CAL-DEBT-DESIGN-v1.0
project: VoiceGenesis
document_class: CANONICAL_DESIGN
status: CANONICAL_APPROVED_DESIGN / NOT_PREREGISTERED / EXECUTION_NOT_AUTHORIZED
canonicalized_utc: 2026-09-01T03:30:48.745Z
session_id: VG-RUN10-METER-CAL-COLLAB-20260831-JST
source_document_id: VG-METER-CAL-DEBT-DESIGN-v0.2-proposed
source_drive_file_id: 1IX-QYU4UoZkc4RQzkZ5svI5XqKLncXtg
supersedes: VG-METER-CAL-DEBT-DESIGN-v0.2-proposed
preservation_rule:
  - v0.1_discussion_draft_read_only
  - v0.2_proposed_read_only
  - run9_run10_canonical_assets_read_only
authorship:
  design_compiler: CLAUDE
  collaborating_reviewer: GPT
  approving_authority: USER
  canonicalization_agent: GPT
approval_scope:
  - design_canonicalization_only
  - no_execution_authority
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
---

# VoiceGenesis RUN10後 測定器校正・修正 技術負債返済 設計正本 v1.0

## 0. 正本としての位置づけ

本書は、`VoiceGenesis_RUN10_Meter_Calibration_Debt_Repayment_Design_v0.2_proposed.md`を
ユーザーの明示指示により、VoiceGenesis RUN10後のmeter校正・修正に関する
**唯一の承認済み設計正本**へ昇格したものである。

正本化に先立ち、GPTはR0〜R5の全12通信文書とv0.2_proposedをreadback照合した。
誤転記・脱落・意味変質は0件であり、R5の明確化2点と修正1点
（§11の理由コード閉語彙、`control_gate`事前宣言列、§12のsum-of-norms）を
明示的にACKした。技術的未決・未写像PoRは残っていない。

本書の正本化は**設計内容の承認**であり、preregistration完了または実行許可ではない。
実験開始、meter修正、repo書込、manifest/registry作成、secret生成、Pod起動、課金、
RUN11 measurement entryのいずれも授権しない。これらは§18の独立した承認Gateに従う。

v0.1、v0.2_proposed、RUN9/RUN10の既存正本資産はread-onlyで保存する。
本書の技術内容を変更する場合はin-place改変せず、新revisionまたはcorrection recordを
append-onlyで作成する。

## 1. D1〜D3 の裁定（R0 で確定、R1/R5 で精緻化）

### D1 — 「全family終端裁定 + claim-critical subset校正」（v0.1 案A/B/C をいずれも不採用）

- M2 spectral tilt / M2 aperiodicity / M3 / M4 / M5 は全て、sealed holdout 後に
  相互排他的な終端 status（§11）へ到達させる。INVALID / NOT_EVALUABLE /
  DIAGNOSTIC_ONLY への裁定も正当な終端である。**全meterの校正成功は要求しない。**
- M2 は tilt と aperiodicity を**別meterとして**終端裁定する。family 合成 status は作らない。
- `CLAIM_CRITICAL_SET = {M3_formants, M2_spectral_tilt, M2_aperiodicity}` を
  保守既定として C0 で凍結する。C0 後の縮小・追加は禁止（M4/M5 は M6 に含める仕様を
  C0 で明示した場合のみ追加可）。
- M6 は集合に入れない（物理 GT を持たず C5 構成概念検証を通るため）。ただし
  RUN11 gate の**独立 conjunct**（gate-critical）として保持する。
- 「返済完了」という語は廃止し、二層で記録する（R1）:
  - `CAMPAIGN_CLOSED` = 全 meter が終端 status に到達（手続的閉鎖）
  - `DEBT_DISCHARGED` = **純導出値**（宣言フィールド化を禁止）:
    `DEBT_DISCHARGED := ∀m ∈ CLAIM_CRITICAL_SET, terminal(m) ∈ {CALIBRATED_ABSOLUTE, CALIBRATED_DIRECTIONAL}`

### D2 — bounded target-domain + boundary probes + fail-closed

- primary domain は「人間歌唱全域」ではなく、凍結済み target probe manifest
  （RUN9 probe 定義、RUN10 record、renderer environment pins）が使う軸の和集合。
- 軸は2種類: (i) **証拠決定軸**は C0 で一次資料から列挙し、列挙不能なら manifest 全体を
  `BLOCKED_DOMAIN_MANIFEST_INCOMPLETE`（軸単位の部分進行は不採用）。
  (ii) **設計選択軸**（boundary 位置等)は「primary domain 両端から当該軸の sweep 刻み
  1段分外側」と相対定義し、推測値を置かない。
- domain 外は自動外挿せず `NOT_EVALUABLE`。
- 各 fixture 行は導出フィールド `domain: PRIMARY | BOUNDARY` を持ち
  （いずれかの軸が boundary level なら BOUNDARY）、PASS 集計はこの tag で分離する。
  primary と boundary の PASS 率を平均しない。boundary 失敗は domain 縮退点を定める。

### D3 — Hard Measurement Gate（Split Gate 棄却）

v0.1 の仮推奨 B（Split Gate）は**棄却**。RUN11 凍結
（CLAUDE-20260831T063500Z-RUN11-DESIGN-R2-025）と整合する Hard Gate に一本化し、
最終文言は §15 の Hard Claim-Dependency Gate（R5 で置換確定）とする。
本キャンペーンは RUN11 凍結を解除する授権を持たない。
Transition Determinism だけの compiler PoC を将来行う場合も、別のユーザー承認と
別契約で扱う。

### 手続 Gate と meter status の分離（R1）

C0〜C4 は手続 Gate: `PREPARATION_VALID / FIXTURE_VALID / BASELINE_AUDITED /
SELECTION_FROZEN / HOLDOUT_EXECUTED_VALID`。meter の終端 status とは別軸であり、
holdout が有効実行されて meter が INVALID でも C4 手続は PASS し得る。
Gate は単調（前段非PASSなら後段PASS不可）とし、各 PASS に event 記録 + 対象 hash を付す。

## 2. 語彙の分離

- 信頼クラス（CALIBRATED_ABSOLUTE 等）は **meter 校正の語彙**。
- audit v0.4 §15 の互換 enum（DIRECT_COMPATIBLE 等）は **cross-system mapping の語彙**。
- 両者は別軸であり自動変換しない。`UNCALIBRATED` は両語彙に現れるため、
  meter 側は `UNCALIBRATED (meter class)` と表記して参照する。

## 3. C0 freeze manifest

欠落時の扱いで二層に分ける（R1。全欠落一律 BLOCK は弱い値で埋める誘因を作るため）。

### 3.1 REQUIRED_BLOCKING（欠落 → BLOCKED_C0_MANIFEST_INCOMPLETE）

- repo URL / full commit SHA（branch 名は正本にしない。C0 freeze event で HEAD を
  再実測して記録）/ dirty-tree=false
- `measurement_directory_status`（実測: 統合 measurement registry は不在 = ABSENT。
  legacy 候補 path `voice_genesis/harness/measure_v3.py` を別記）
- 候補 meter・generator・schema・test の全 path + SHA-256
- Python exact version / numpy・scipy・librosa・soundfile exact versions
- sample format・dtype・channel policy・resampling implementation/parameters
- frozen design 全項目（CLAIM_CRITICAL_SET、meter 別 construct/unit/domain/
  algorithm family/有限 parameter grid/baseline/fallback/missing・failure rule、
  fixture family・generator version/hash・known-truth field・confound 軸・
  boundary probes・negative controls、split・seed・seal、selection rule・tie rule・
  candidate exhaustion rule・holdout FAIL 後の固定 outcome、provenance schema・
  artifact layout・cost cap・stop rules）
- independence ledger 全項目（§4）

### 3.2 RECORDED_OR_ABSENT（値または `ABSENT:<理由>` を必須記録。BLOCK せず claim ceiling を制限）

- container/image digest（ABSENT → process reproducibility 主張を `WEAK_ENV_LOCK` へ降格）
- BLAS/FFT backend / OS・kernel・CPU arch / wheel hashes
- WORLD build flags（wheel 使用時は wheel version + hash で代替）

### 3.3 特則・追加規則

- **pyworld 特則**: D4C/WORLD を使う候補に限り exact version + wheel hash を
  REQUIRED_BLOCKING。欠落時は当該候補のみ ineligible（campaign 全体は BLOCK しない）。
- **generator determinism**: 同一 seed・fresh process で2回生成し byte 一致
  （または宣言済み数値許容差内）。違反 → `BLOCKED_C1_GENERATOR_NONDETERMINISTIC`。
- **manifest 自己 hash**: 正規化 serialization 上の `manifest_sha` + freeze event 時刻。
  追記は append-only + 新 campaign 扱い。
- **RNG 台帳**: 全乱数 stream（generator / split / tie-break）と seed を列挙。
  未 seed 乱数の検出 → `BLOCKED_C0_UNSEEDED_RNG`。
- **fail-closed code の閉語彙**: `BLOCKED_DOMAIN_MANIFEST_INCOMPLETE /
  BLOCKED_C0_MANIFEST_INCOMPLETE / BLOCKED_C0_UNSEEDED_RNG /
  BLOCKED_C1_GENERATOR_NONDETERMINISTIC / BLOCKED_LEAKAGE /
  BLOCKED_CANONICAL_MUTATION_REQUIRED` を C0 で列挙し、事後の場当たり追加を禁止。
- F0 帯域整合検査: `fmin <= 0.8 * min(PRIMARY truth F0)` かつ
  `fmax >= 1.25 * max(PRIMARY truth F0)`。不成立の fixture 行は grid を変えず
  BOUNDARY へ再タグ。再タグは split 生成・seal より前の C0 validation で一度だけ確定。

## 4. Independence tier と fixture family

### 4.1 4-tier（fixture × meter ごとに機械可読で固定）

| tier | 条件 | claim ceiling |
|---|---|---|
| INDEPENDENT_ANALYTIC | GT は解析的指定、meter と source/library/estimated intermediate 非共有 | ABSOLUTE 候補 |
| CROSS_IMPLEMENTATION | 同一理論 construct・独立実装・非共有 code/dependency・正負 control あり | ABSOLUTE または DIRECTIONAL 候補（共有仮定を明記） |
| SHARED_MODEL_DIAGNOSTIC | generator と meter が同一 model family / 主要 dependency を共有 | DIAGNOSTIC_ONLY 上限 |
| INVALID_CIRCULAR | GT label を同一 estimator またはその出力から生成 | 校正証拠として無効 |

### 4.2 family 別（NEGATIVE_CONTROL は第8 family ではなく全 family に直交する control class）

| family | generator / known truth | 独立性・ceiling |
|---|---|---|
| F0_CONTROL | 解析的 sinusoid・harmonic pulse train、宣言 F0 | meter と非共有なら INDEPENDENT_ANALYTIC |
| FORMANT_GT | pole frequency/bandwidth を直接指定した source-filter 合成。**resonator code path を共有しない2実装**（filter cascade 系と閉形式加算合成系）。GT は pole で宣言。高 F0 の pole≠spectral peak 領域は試験対象。ceiling は宣言済み F0×ceiling×window×sample-rate domain 内の pole recovery に限定 | 条件付き ABSOLUTE 候補 |
| TILT_GT | 宣言 harmonic 範囲上の dB/oct slope 1定義に凍結。label はいかなる候補 meter と共有される回帰 routine からも生成しない | ABSOLUTE 候補 |
| APERIODICITY_GT | `injected_noise_fraction` が GT。D4C 出力との absolute equality 禁止。WORLD 合成→D4C は SHARED_MODEL_DIAGNOSTIC | 独立実装は directional/monotonicity |
| RESONANCE_GT | pole/Q 指定、M3 と異なる出力 construct を宣言 | overlap 未解消のため RUN10 では DIAGNOSTIC_ONLY（§16） |
| TRANSITION_GT | exact join time・step/crossfade・discontinuity energy を生成時記録 | detector 非共有なら ABSOLUTE/DIRECTIONAL 候補 |
| IDENTITY_CAUSAL_SWEEP | content/F0/duration/SNR 固定の one-factor causal sweep | 物理 scalar GT なし。construct validation のみ。CALIBRATED_ABSOLUTE 禁止 |

- negative control（silence / noise-only / 単一正弦 / 帯域外 pole / too-short /
  unvoiced / 不正 sample rate 等）は family ごとに C0 で凍結。
- **両側条件**: negative control の「偽検出しない」だけでは全域 NOT_EVALUABLE を返す
  meter が自明 PASS するため、対になる positive fixture での検出器発火を同時要求する。

## 5. Fixture matrix（456 logical cells。R2 で確定）

logical cell = truth 条件 × confound 条件の一意な行。repeat は cell 数に含めない。

### 5.1 共通軸

Primary: F0 `[130.813(C3), 195.998(G3), 261.626(C4), 391.995(G4)] Hz` /
sample rate `[24000, 44100, 48000] Hz` / gain `[-24, -12, -6] dBFS` /
duration `[0.25, 0.50, 1.00, 2.00] s` / noise `[clean, 40, 20, 10] dB SNR` /
context `[steady-isolated, 20ms-cosine-ramp, 100ms-voiced-prefix/suffix, transition-adjacent]`

Boundary: F0 `[97.999(G2), 523.251(C5)]` / SR `[16000, 96000]` / gain `[-36, -1] dBFS` /
duration `[0.10, 4.00] s` / noise `0 dB SNR` / invalid・negative 各種。

nuisance block = anchor truth 条件への一因子主効果行 + 6 targeted interactions
(`high-F0×low-SR`, `high-F0×short-duration`, `high-F0×low-SNR`, `low-F0×high-SR`,
`low-gain×noise`, `transition×short-duration`)。全行を C0 で明示列挙する
（"covering array" とだけ書くことを禁止）。

### 5.2 family 別内訳

| family | truth/core | confound | boundary/negative | total | cal/sel/holdout |
|---|---:|---:|---:|---:|---|
| F0_CONTROL | 12 | 24 | 12 | 48 | 24/12/12 |
| FORMANT_GT | 60 | 24 | 12 | 96 | 48/24/24 |
| TILT_GT | 30 | 12 | 6 | 48 | 24/12/12 |
| APERIODICITY_GT | 36+24 bandwise | 6 | 6 | 72 | 36/18/18 |
| RESONANCE_GT | 24 | 12 | 12 | 48 | 24/12/12 |
| TRANSITION_GT | 24 | 12 | 12 | 48 | 24/12/12 |
| IDENTITY_CAUSAL_SWEEP | 60 | 24 | 12 | 96 | 48/24/24 |
| **total** | | | | **456** | **228/114/114** |

truth 値: FORMANT_GT pole sets `[(300,2200,3000),(500,1900,2600),(800,1200,2500),(500,900,2400),(350,800,2200)] Hz`・bandwidth anchor `(80,100,120) Hz`。
TILT_GT `[-24,-18,-12,-6,0] dB/oct`。APERIODICITY_GT fraction `[0,0.01,0.03,0.10,0.30,0.60]`・
bandwise `[broadband, 0–3kHz, 3–6kHz, 6kHz–Nyquist]`。RESONANCE_GT center
`[500,1000,2000,3500] Hz`・bandwidth `[50,150,300] Hz`・prominence `[6,12] dB`。
TRANSITION_GT join types = amplitude step / phase jump / spectral-envelope switch /
crossfade × 3 severities（C0 表で固定）× 2 duration classes。
IDENTITY_CAUSAL_SWEEP = 4 synthetic founders × 3 claim-critical traits ×
delta `[-2,-1,0,+1,+2]` generator units。
（表の列は構成の記述であって集計単位ではない。集計は domain tag による。）

## 6. Repeat 構造と tolerance 導出

- **probe repeat = 5**: 独立 phase/noise seed の canonical instance。
- **generator repeat = 2 fresh processes / same seed**: byte-identical PCM を要求
  （float 中間から PCM 量子化する場合は最終 PCM で一致）。
- **meter within-process repeat = 3 calls** / instance（同一 process・同一 bytes）。
- **meter fresh-process repeat = 3 processes** / instance。
- 件数: canonical instances 456×5 = 2,280 / renders 計 4,560 /
  meter calls 2,280×6 = 13,680 per implementation / split 別 instance 1,140/570/570。
- 同一 artifact 再読の within-process repeat と独立 seed の probe repeat を混同しない。
  分散推定は probe repeat、ソフトウェア再現性は within/fresh-process で別々に報告。
- **tolerance 導出（RUN11 知見の適用）**: per-cell n=5 は分散推定に不足のため、
  (family × condition class) で pooled した dispersion を使い、cell 内5反復は
  `UNSTABLE_CELL` flag 専用。`tolerance = max(k · pooled_SD, floor)`。floor は
  PCM 量子化・float 誤差・meter 宣言分解能から C0 で機械導出（値と導出式を manifest に
  記載）。dispersion ゼロを「無限に厳しい合格基準」にも「高精度の証拠」にも変換しない。
  全層ゼロ時は `TOLERANCE_FLOOR_LIMITED` を status に付記。

## 7. Split / seed / seal

- canonical row JSON を RFC 8785 相当で正規化し `row_id = SHA256(row_json)`。
- family ごとに `HMAC-SHA256(split_secret, row_id)` 昇順で 50/25/25 へ割当。
- **層別規則**: stratum 因子を C0 で明示列挙 → stratum 内 HMAC 順 →
  largest-remainder で 50/25/25 → 端数は HMAC 順位の偶奇で selection/holdout へ交互配分
  → 制約検査（family 合計厳密 / 各 truth level・generator 実装・boundary class の
  split 内最低1行）→ 違反は HMAC 順位最小の行対の決定的最小 swap で修復し manifest に記録。
  **正本は C0 manifest に列挙した実現済み row→split 表**（検証器がアルゴリズム出力と機械照合）。
- RNG stream は HKDF-SHA256 で分離
  (`info = campaign_id || family || split || row_id || probe_index || purpose`)。
- `split_secret` / `render_root_secret` は C0 freeze スクリプトが実行環境内で生成。
  **Drive には SHA-256 commitment のみ**を置く（plaintext を Drive に置く案は棄却）。
- leakage の強制力は秘匿ではなく **append-only measurement ledger** に接地:
  全 render・全 meter call を row_id 付きで記帳し、holdout 行の unseal 前初出は
  `BLOCKED_LEAKAGE`。unseal 条件は
  `baseline_audit_sha + candidate_space_sha + selection_rule_sha + selected_candidate_sha + selection_freeze_event_sha`
  の存在と相互参照一致。unseal 後の候補・parameter・threshold・domain 変更は禁止。
- **保護水準の正直な宣言**: 本 seal は「事故的 leakage と事後改竄の検出」水準であり、
  外部鍵管理なしに台帳外の敵対的実行者は防げない（ユーザー判断3）。

## 8. Candidate measurement space（99候補。C0 で凍結、事後追加・連続 optimizer・手調整禁止）

共通規則: 各候補は candidate_id / construct / unit / algorithm family /
implementation path・hash / dependency hash / parameter JSON / domain /
missing・failure rule / independence tier / claim ceiling / complexity rank を持つ。
`B0_CURRENT` を必ず含める（construct 不一致でも除外せず DIAGNOSTIC_ONLY / INVALID を
正当な結果として許す）。共通 fail filter: schema 違反 / 非有限値の無説明返却 /
within・fresh-process 不一致 / negative control 偽検出 / 対 positive control 不発火。

- **F0_CONTROL（5候補・claim-critical 外・上流 control）**: F0-B0-CURRENT +
  F0-PYIN（frame 2048/4096 × hop 256/512、fmin 80、fmax 600）。selection は
  cents error / octave-error rate / voiced false detection / process reproducibility。
  F0 選択は下流候補（D4C 等）実行**前**に完了する一方向依存を C0 phase 順に明記。
  fixture truth F0 の production 流用禁止。
- **M3 formants（43候補）**: M3-B0-CURRENT-CENTROID（baseline、DIAGNOSTIC_ONLY 候補）/
  M3-CEPSTRAL-POLES 18（**baseline と同族**と明記。lifter_ratio {0.5,0.7,0.9} ×
  min_lifter_samples {4,8} × band_hi {3500,4000,4500}）/ M3-BURG-LPC 24
  （**唯一の独立 family**。order {12,16,20} × window {25,40}ms × preemph {0,50}Hz ×
  max_formant {4000,5000}Hz。分析前に `fs' = 2*max_formant_hz` への決定的 resample を
  必須とし、resampler・係数・端処理を parameter JSON へ）。ABSOLUTE 最大目標は
  F1/F2/F3 個別 Hz error（centroid を代用にしない）。
- **M2 spectral tilt（13候補）**: M2T-B0-CURRENT-HYBRID（unit 混在のためそのままでは
  INVALID）/ M2T-HARMONIC-OLS 6 / M2T-HARMONIC-THEILSEN 6（K {4,6,8} ×
  window {hann, blackman_harris}）。K 本未満は縮退せず missing。H1–H2 は別 construct
  として selection 競争から除外。harmonic amplitude 取得方式は1案に凍結し
  parameter JSON に含める。
- **M2 aperiodicity（24候補）**: M2A-B0-AUTOCORR-PERIODICITY /
  M2A-HNR-ACF 8 / M2A-HARMONIC-RESIDUAL 12（独立 generator 上のみ ABSOLUTE 候補）/
  M2A-D4C 3（bandwise directional/diagnostic 上限。WORLD 合成 fixture 上は
  SHARED_MODEL_DIAGNOSTIC。F0 入力は選択済み F0_CONTROL 固定）。
- **M4 resonance（5候補）**: M4-B0-CURRENT-CENTROID + M4-LOCAL-PROMINENCE 4。
  **RUN10 では全候補 DIAGNOSTIC_ONLY 上限で閉じる**（§16）。
- **M5 transition/join（7候補)**: M5-WAVE-DISCONTINUITY 3 + M5-SPECTRAL-FLUX 4。
  join time error > 凍結窓 → fail filter / steady false detection → negative control
  fail filter / 残る ranking は投入 discontinuity magnitude の truth order への
  DIRECTIONAL 族規則。3軸を単一 TotalScore に合成しない。
- **M6 Identity Spec v2（2構成）**: 独立物理 meter として扱わない。C4 後の eligible
  component のみ・E_use[j] normalization・等重み {weighted_L1, weighted_L2}・
  重み学習禁止・CLAIM_CRITICAL_SET の1件でも missing/ineligible なら NOT_EVALUABLE。
  両構成が construct-valid gate を通過した場合の選択は DIRECTIONAL 族規則。

## 9. Selection rule

selection split のみから、family ごとに lexicographic 比較（重み付き単一 score 禁止）。

- ABSOLUTE 族: primary-domain normalized MAE → |signed bias| → primary q95 AE →
  nuisance sensitivity max → missing/failure rate → complexity rank → candidate_id 字句順。
- DIRECTIONAL 族: `1 - Kendall_tau`（事前 truth order）→ adjacent reversal rate →
  nuisance sensitivity max → missing/failure rate → complexity rank → candidate_id 字句順。
- **丸め後比較**: error 系 = 有効数字3桁、rate/sensitivity 系 = 0.001 刻み、
  complexity = 整数。丸めは事前定義の同値帯であり、全順序を保存し非推移性を回避。
- 全 criterion vector（丸め前後）を `SELECTION_FROZEN` event に記録してから holdout unseal。
- 全候補 fail → `SELECTION_FAILED_CLOSED`（候補選択なし・unseal なし・meter status は
  fail-closed、上限 NOT_EVALUABLE）。holdout FAIL 後の次点差替え・grid 拡張は新 campaign。
- 実行範囲: selection は各候補 × 自 family selection 行のみ。holdout は選択1候補 + B0 のみ
  （B0 は診断参照であり claim に使わない）。

## 10. 誤差式・threshold 導出（R4/R5 で確定）

### 10.1 観測量

- `m[i] = median_p( median_r( x_hat[i,p,r] ) )`（二段 median。process 間 repeat 不均等時の
  支配を防ぐ）
- `e[i] = m[i] - x[i]`、`AE[i] = |e[i]|`、`RE[i] = AE[i]/max(|x[i]|, d[i])`
  （zero guard d[i] は C0 導出。0近傍 signed construct では RE を PASS に使わず診断のみ）
- `BIAS = mean_i(e[i])`、MAE、`q95(method=linear 固定)`
- `U_rep = q95_{i,p}(max_r - min_r)/2`、`U_proc = q95_i(|median_r x[i,1,r] - median_r x[i,2,r]|)/2`
  （多 process は全 pair 差の q95/2 へ一般化）
- nuisance: `dS[a,pair] = |(m[ia]-x[ia]) - (m[i0]-x[i0])|`。truth 自体が変わる軸は
  invariance 対象に混ぜない。
- detection: `FDR0` / `FNR1`。**control 出力の missing/invalid は分子に算入**
  （分母から除外しない。eligibility は C0 入力側条件のみで判定）。
  **最小数**: binary detection gate を持つ construct は `N_neg >= 10` かつ `N_pos >= 10`。
  非該当 construct は C0 で `control_gate: NOT_APPLICABLE` を事前宣言（結果後切替禁止）。
- invariance 軸ごとに **>= 5 pairs**。未達軸が1つでもあれば ABSOLUTE 不可。
- failure boundary: 各事前順序軸で `[last passing level, first failing level]`。補間なし、
  missing/nonfinite は first failing。

### 10.2 threshold budget（結果後付けの禁止）

C0 で候補結果を見る前に凍結: `E_use[i]`（用途許容誤差。原理・仕様・用途から独立に根拠化）、
`U_GT[i]`（generator truth の保守上限）、`U_num[i]`（PCM 量子化・浮動小数・宣言分解能から
機械導出）。`M[i] = E_use - U_GT - U_num <= 0` なら ABSOLUTE は NOT_EVALUABLE
（E_use を緩めない）。不確かさは RSS でなく加算。

**E_use evidence table（全 construct/unit/domain 必須13列）**:
construct_id / unit / domain / intended_use / maximum_claim / E_use_value /
derivation_rule / evidence_class / source_id_or_url / source_checked_at /
source_hash_or_version / applicability_argument / review_status。
`evidence_class ∈ {NORMATIVE_SPEC, FIRST_PRINCIPLES_BOUND, VALIDATED_REFERENCE, USER_ACCEPTED_USE_BOUND, UNJUSTIFIED}`。
UNJUSTIFIED に数値 placeholder を作らない。E_use 根拠化不能時の自動 ceiling:
独立 truth order が事前に立つ → DIRECTIONAL / 立たない → DIAGNOSTIC_ONLY
（NOT_EVALUABLE へは落とさない）。USER_ACCEPTED_USE_BOUND はユーザー判断1へ統合。

### 10.3 ABSOLUTE holdout gate（per-instance margin 方式）

```
G[i] = AE[i] + U_GT[i] + U_num[i] + U_rep + U_proc - E_use[i]
gate 1: 全 PRIMARY instance が eligible（critical missing/undefined なし）
gate 2': q95_i(G[i]) <= 0
gate max': max_i(G[i]) <= 0
gate 3: |BIAS| + max_i(U_GT+U_num) + U_rep + U_proc <= median_i(E_use)
gate 4': 各宣言 invariance 軸 a で
        q95_pairs( dS[a,pair] + U_rep + U_proc - min(E_use[i0],E_use[ia]) ) <= 0
gate 5: FDR0 = 0 かつ FNR1 = 0（最小数条件付き）
```

AE が median 経由で noise を部分的に含むための +U_rep+U_proc は
**保守的二重計上（意図的・fail側）**であり修正しない。

### 10.4 DIRECTIONAL holdout gate

```
R_ij = (U_GT_i + U_num_i) + (U_GT_j + U_num_j) + 2*(U_rep + U_proc)
resolvable(i,j) <=> Delta_truth(i,j) > R_ij
```

（036 の係数2×全項は実効4×の過剰保守だったため上式に確定。）
resolvable pair は各 sweep で >= 3（3 ちょうどの directional 主張は provenance でフラグ）。
全 resolvable adjacent pair の正符号 / adjacent_reversal_rate = 0 /
negative・positive control 各 0 / 各 effect が noise floor 超過を必須。
tau_b は記録・selection に使うが、PASS 閾値を production AUC 等から設定しない。

## 11. 終端 status（first-match cascade。同一 campaign 内で再遷移禁止）

```
1. procedure breach 検出           → INVALID
2. 評価可能性条件の不成立           → NOT_EVALUABLE
3. ceiling が ABSOLUTE を許し gate 全通過 → CALIBRATED_ABSOLUTE
4. ceiling が DIRECTIONAL を許し gate 全通過 → CALIBRATED_DIRECTIONAL
5. else                            → DIAGNOSTIC_ONLY
```

5 が残余のため網羅性は構造的に保証、排他性は first-match による。

**missing の一意写像（理由コードは閉語彙 `{INPUT_MISSING, OUTPUT_NOT_EVALUABLE, OUTPUT_MISSING, PROCEDURE}` として C0 凍結、事後追加禁止）**:

- procedure breach → `INVALID / PROCEDURE`
- critical output 全欠損 or 最小数割れで score/gate 計算不能 → `NOT_EVALUABLE / OUTPUT_NOT_EVALUABLE`
- score 計算可能だが PRIMARY 一部 output missing で gate 不通過 → `DIAGNOSTIC_ONLY / OUTPUT_MISSING`
- C0 入力側 critical missing → `NOT_EVALUABLE / INPUT_MISSING`

selection 全 fail → campaign は SELECTION_FAILED_CLOSED、meter は NOT_EVALUABLE。
holdout performance fail → DIAGNOSTIC_ONLY。手順違反のみ INVALID。

## 12. M6 Identity Spec v2

- CLAIM_CRITICAL_SET は **CALIBRATED_ABSOLUTE component のみ**で構成。directional
  component は本体 distance に入れない。空集合（ABSOLUTE component 0件）→ M6 は
  NOT_EVALUABLE（部分構成で distance を出力しない）。
- normalization は各 E_use[j]。等重み L1/L2 のみ、重み学習禁止。
- **pairwise endpoint uncertainty（sum-of-norms。R5 で確定）**:

```
u_X[j] = (U_GT_X[j] + U_num_X[j] + U_rep_X[j] + U_proc_X[j]) / E_use[j]   (X = A, B)
U_obs_pair(A,B) = ||u_A||_p + ||u_B||_p      （p は distance と同じ norm）
U_null_pair[k] = 同式を null pair k の両端点に適用
T_null = q95_k( D_null[k] + U_null_pair[k] )
distinct(A,B) <=> D_obs(A,B) - U_obs_pair(A,B) > T_null
```

（||u_A+u_B||_p は L2 で保守上限を下回りうるため norm-of-sum は棄却。
両端点同一 process 由来の U_rep/U_proc 二重計上は意図的・保守的。）

- distinctness は上式 + 事前 causal sweep の全 resolvable pair での directional gate
  成立時のみ主張。
- 出力は component vector / distance / 寄与 / status のみ。単一 TotalScore・品質・
  人間知覚上の同一性・法的/生体認証 identity を禁止。
- M6 ceiling = CALIBRATED_DIRECTIONAL（物理量 absolute calibration を名乗らない）。
- C5 causal sweep は発話内容・長さ・SNR を fixture 間で完全一致させる。generic speaker
  embedding は内容・話速の漏洩交絡が既知のためデフォルト不採用（採用するなら
  交絡統制設計ごと C0 で凍結）。

## 13. Provenance schema（必須 field。型/required・nullable/enum/unit/hash algorithm は付属表で定義）

campaign_id / campaign_parent_id / event_id / event_time_utc / actor /
authorization flags、source document IDs+hashes / repo URL / code SHA / dirty-state /
dependency lock hash / runtime image hash、candidate_id / algorithm family /
implementation hash / parameter JSON+hash / claim ceiling / complexity rank、
fixture family・row・instance IDs / generator spec+code hash / render hash / truth /
U_GT / U_num / domain tag、seed derivation scheme+public identifier（secret は記録しない）/
realized split map+hash / seal commitment / unseal event、raw repeat/process outputs /
missing・invalid reason（閉語彙）/ control eligibility / pair membership、
E_use value+evidence row / raw+rounded criterion vector / selection rank /
SELECTION_FROZEN event、all gate inputs・outputs / N_neg・N_pos / invariance pair count /
resolvable pair count / 3-pair warning、final meter status / reason code / claim text /
prohibited interpretations、meter calls・storage・runtime counters / cap values /
stop event・reason、全 artifact・schema・test・fixture hash + append-only ledger link。
追加: `control_gate` 宣言列 / `UNSTABLE_CELL` flag / `TOLERANCE_FLOOR_LIMITED` 付記 /
split swap 記録。旧正本・RUN9/RUN10 資産の in-place 変更禁止。

## 14. 費用上限（枠と算定根拠。値はユーザー判断1）

- 規模（R2/R3 で確定した設計値）: renders 4,560 本（音声換算 ~2.5 時間・storage <= 1 GB
  程度）/ baseline+holdout 段の meter calls 13,680 per implementation
  （1実装あたり数〜10 CPU 時間オーダー・完全並列化可能）/ selection 段は 99 候補 ×
  自 family selection 行のみで総 meter call ~10^5 オーダー。
- cap は compute / storage / 課金の3値を C0 manifest に凍結し、超過で stop event
  （fail-closed、结果不完全のまま閉鎖）。**cap の値の決定と実行 Go はユーザー判断**であり、
  本書は変数として保持する。

## 15. RUN11 Hard Claim-Dependency Gate（確定文言）

```
RUN11 remains frozen.
RUN11_MEASUREMENT_ENTRY_AUTHORIZED = false until:
  (1) the user separately authorizes the RUN10-CAL campaign and C0 freeze,
  (2) the campaign completes from the frozen manifest without INVALID procedure status,
  (3) every preregistered RUN11 claim-critical meter has a final status adequate
      for that exact claim,
  (4) the user separately authorizes Run11 measurement entry after reviewing outcomes.
```

- absolute trait magnitude claim → 対応 component が CALIBRATED_ABSOLUTE 必須。
- direction/order-only claim → CALIBRATED_DIRECTIONAL 以上 + claim の事前限定。
- DIAGNOSTIC_ONLY / NOT_EVALUABLE / INVALID は confirmatory evidence に使用禁止。
- M6 を使う identity distinctness claim → nonempty critical set + 全 critical component
  CALIBRATED_ABSOLUTE + M6 final status CALIBRATED_DIRECTIONAL。
- 一部 meter 通過での RUN11 自動再開なし。claim 削除・縮小は新 preregistration +
  ユーザー承認。
- 本 Gate は現在の RUN11 凍結を解除しない。

## 16. 今回の提案対象外と裁定した事項（曖昧 TBD の排除。理由付き）

1. **M4 の M3 からの construct 独立性の証明** — 独立 construct の証明設計自体が
   別 campaign 規模。RUN10 では全 M4 候補を DIAGNOSTIC_ONLY 上限で閉じる。
2. **外部鍵管理による敵対的秘匿の実現** — 2エージェント+共有 Drive 構成では原理的に
   不成立。保護水準を「事故的 leakage・事後改竄の検出」と正直に宣言（ユーザー判断3）。
3. **知覚的同一性・人間聴取軸** — RUN11 凍結領域。本キャンペーンの非主張。
4. **baseline の construct 不一致の修理** — INVALID / DIAGNOSTIC_ONLY として記録するのみ。
5. **grid 外の連続最適化・手調整** — 後知恵経路の温床のため設計から排除。
6. **M2A construct 間の絶対等価性検証**（injected fraction vs autocorr vs HNR vs D4C）—
   別 construct と宣言し相互 absolute equality を仮定しない。
7. **RUN10 記録の遡及再判定** — 本キャンペーンは prospective であり、RUN10 の
   PARTIAL_COMPATIBILITY_MAP / Phase B SKIP を変更しない。

## 17. 正本化監査と合意状態

- R0〜R5の全12通信文書をreadbackし、13 PoRの採否、非主張、対象外、
  保守outcome、式、Gate、status、M6境界の写像を照合した。
- v0.2_proposedとの不一致は0件。未写像項目も0件。
- R5の明確化2点・修正1点
  （理由コード閉語彙 / `control_gate`宣言列 / M6 sum-of-norms）をGPTが明示的にACKした。
- 技術的未決、未合意、追加PoRはない。
- 本正本はユーザーによる設計正本化の承認を記録する。署名文字列を本人性・授権の証明には使わない。

## 18. 実行時に別途必要なユーザー承認Gate（最終3件。4件目は作らない）

今回の正本化承認は、以下3件の承認を含まない。各Gateは実行直前に個別判断する。

1. **campaign 実行承認 + 費用上限 + 許容する最大 claim / E_use 境界**
   （USER_ACCEPTED_USE_BOUND の受容を含む。価値・費用・主張範囲の判断）
2. **C0 freeze の実行承認**（repo への manifest/registry 書込・secret 生成を伴うため。
   4-1 の承認対象操作）
3. **seal 保護水準の受容**（「事故的 leakage と事後改竄の検出」まで。外部鍵管理なしに
   敵対的実行者は防げないというリスク受容）

## 19. 正本裁定

```yaml
status: CANONICAL_APPROVED_DESIGN / NOT_PREREGISTERED
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
design_gate: CANONICALIZED
next_actions:
  - 実行へ進む場合のみ §18 の3承認Gateを順に処理
  - 承認後に C0 manifest と execution contract をfreeze
  - RUN11は別承認まで凍結を維持
```

本設計正本の核心は、v0.1から一貫して次である:

> 正解付き世界で測定器自身を試験し、使える範囲・誤差・失敗条件を先に固定してから、
> VoiceGenesis の個体や遺伝を測る。


Canonical design compiled by Claude, readback-verified by GPT, approved by User.

