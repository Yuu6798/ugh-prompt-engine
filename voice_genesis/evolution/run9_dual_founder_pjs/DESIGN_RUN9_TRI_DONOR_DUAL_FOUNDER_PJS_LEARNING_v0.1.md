# DESIGN RUN 9 — Tri-Donor Dual-Founder Common-Teacher Learning
## AF0・Ritsu・User の三点Identityから二体の生態系始祖候補を生成し、PJS由来の同一歌唱教材で個体学習させる実験契約 v0.1

- **策定日（JST）:** 2026-08-22
- **対象リポジトリ:** `Yuu6798/ugh-prompt-engine`
- **設計基準:** `main @ adcf67bc3e70006afb86edad730e1da261209e33`
- **Run ID:** `RUN9`
- **Experiment ID:** `VG-R9-DUAL-FOUNDER-PJS`
- **状態:** Preregistered Design Draft / 実装・本学習未開始
- **主対象:** `R9F-01` / `R9F-02`
- **Identity anchors:** `AF0` / `Ritsu` / `User Donor`
- **Teacher / Curriculum source:** `PJS`
- **前提理論:** VoiceGenesis v0.3 の E/L/T/S 分離、Selection Pressure Routing、Singing Baseline、VG-E0、VG-L0
- **重要:** `RUN9A / RUN9B / RUN9C` の枝番号は作らない。設計変更は `design_revision`、実行履歴は `attempt_id` で管理する。

---

# 0. 裁定要約

RUN 9 は、次の一連の過程を一つの実験として扱う。

```text
Identity donors
  AF0
  Ritsu
  User Donor
       |
       | E: fixed TRI_CROSSOVER
       +-------------------------------+
       |                               |
       v                               v
  R9F-01:r0                       R9F-02:r0
  AF0-dominant                    User-dominant
  neutral skill                   neutral skill
       |                               |
       | same frozen PJS lesson        | same frozen PJS lesson
       | same recipe / same budget     | same recipe / same budget
       v                               v
  R9F-01:r1                       R9F-02:r1
       \                               /
        +---- before/after + cross-founder comparison ----+
```

中心目的は、単に二つの声を作ることではない。

> **異なる三点遺伝構成を持つ二体が、同一のPJS由来歌唱教材と同一育成予算を与えられたとき、各個体のIdentityを保ったまま歌唱技能を獲得し、学習利得の方向または大きさに個体差が現れるかを測る。**

本Runでは次を分離する。

```text
E回路: 二体の異なるVoice Identityを出生させる
L回路: 各個体が同じ教材から技能を獲得する
PJS:   Curriculum provider。Identity donorではない
```

PJSは四体目の親ではない。PJSの `spk_embed`、Identity座標、スペクトル包絡、フォルマント形状を、二体のGenomeまたはIdentity targetへ入れてはならない。

---

# 1. 中心質問

## 1.1 Primary Question

> **AF0・Ritsu・User Donorから固定された異なる遺伝比率で生成した二体の生態系始祖候補は、同じPJS由来Performance Lessonを同じ予算で学習したとき、別個体のまま歌唱技能を獲得できるか。**

## 1.2 Secondary Questions

1. 固定した二つの三点座標は、学習前に構造的・知覚的に異なるIdentityを作るか。
2. 同一Lessonは二体のheld-out歌唱に転移するか。
3. 二体の学習利得ベクトルは、render/replayノイズを越えて異なるか。
4. 学習後も二体間のIdentity距離は維持されるか。
5. PJSの歌い方だけが移り、PJSのIdentityは移らないか。
6. 遺伝構成差と技能吸収差を、少なくとも本Run条件内で追跡可能にできるか。

---

# 2. 因果構造

本RunはEとLを同一タイミングで変更しない。

```text
Phase E:
  donor pins + TRI_CROSSOVER
  -> R9F-01:r0 / R9F-02:r0
  -> Birth Baselineをfreeze

Phase L:
  r0を親Revisionとして固定
  -> PJS Lessonだけを介入
  -> r1を別Revisionとして生成
```

学習の因果比較は個体ごとに行う。

\[
\Delta_i = M(R9F_i:r1)-M(R9F_i:r0)
\]

ここで `M` は単一総合点ではなく、歌唱技能・Identity・品質の軸別ベクトルである。

二体の可塑性差は、

\[
\Delta_{diff}=\Delta_{01}-\Delta_{02}
\]

として記録する。

ただし二体しかいないため、`Δdiff` を任意の遺伝一般則へ外挿しない。

---

# 3. 仮説

## H1 — Dual Founder Birth

固定された二つの三点遺伝構成は、同じPerformance seedと同じBackbone下でも、別の構造的Genome IDと知覚的Identityを作る。

## H2 — Common Teacher Transfer

同一PJS Lessonは、少なくとも一体、理想的には二体に対し、held-out課題で対象歌唱技能の改善を生む。

## H3 — Identity Retention

学習後の各個体は、他方の個体またはPJSよりも、自身の学習前Revisionへ近いIdentityを維持する。

## H4 — Differential Plasticity

同一Lesson・同一予算でも、二体のDevelopmental Vectorには再現ノイズを越える差が現れ得る。

## H5 — No PJS Identity Leakage

PJS由来Performance Lessonの適用後も、PJS Identity方向への有意な接近は起きない。

## H6 — Cross-Song Generalization

学習効果はtraining phraseだけでなく、freeze後に開封するheld-out song / register / phraseでも観測される。

---

# 4. Claim Ceiling

## 4.1 PASS時に言ってよいこと

> **RUN 9で事前登録した三Identity anchor、二つの遺伝座標、PJS Lesson、学習レシピ、Probe条件の範囲において、二体の生態系始祖候補を別Identityとして生成し、同一教師教材による歌唱技能の獲得とIdentity維持を評価した。**

両個体にheld-out gainが成立した場合:

> **同一PJS Lessonは、異なる二つのFounder Genomeに対して個体Identityを維持したまま転移した。**

可塑性差が成立した場合:

> **本Runの二つの固定Genome条件では、同一教材・同一予算に対する学習応答差が再現ノイズを越えて観測された。**

## 4.2 禁止主張

```text
× AF0の全形質が完全遺伝した
× AF-T0のDuration / Energy / AG-alpha輸送が解決した
× 任意の三ドナーで同じ結果になる
× 遺伝子が学習能力を一般に決定する
× PJSの人格・声質・Identityを継承した
× 人間と同等の教育・学習が成立した
× 二体が次世代繁殖に適格であることまで証明した
× RUN 8のTRF問題を解決した
× 一つの総合スコアで優れたFounderを決めた
```

## 4.3 Claim Strength Target

```text
Within-run causal claim: C2 / Moderate
Broad heredity claim:    C0-C1 only
```

C2の対象は「同一個体のbefore/afterで、固定Lesson以外を変えなかった」という局所因果に限る。

---

# 5. 非目的

```text
× 三体目のFounder生成
× 複数教師の比較
× PJSをIdentity anchorへ戻す
× Shared Backboneの更新
× 種レベル教師交代の比較
× RUN 8-Bの代替実験
× AF-P0 / AF-T0の判定変更
× 次世代交配・繁殖率・淘汰
× Cultural Island実装
× オンライン学習
× 学習後のGenome座標変更
× 結果を見た後の座標・Lesson・閾値追加
```

---

# 6. Run番号と依存関係

`RUN9` は正式Runの登録順を表す。`RUN8`の子Runであることは意味しない。

```yaml
run_id: RUN9
baseline_run: null
parent_designs:
  - voice_genesis/evolution/DESIGN_VG_E0.md
  - voice_genesis/evolution/DESIGN_VG_L0.md
  - VoiceGenesis Evolution Theory v0.3
  - VoiceGenesis Singing Baseline v0.1
  - VoiceGenesis Supplement A / Selection Pressure Routing
input_assets:
  - AF-P0 canonical AF0 Body / DonorBank
  - Ritsu canonical donor
  - consented User donor
  - PJS teacher material
not_required:
  - RUN8 verdict
  - AF-T0 PASS
```

RUN8から借用可能なのは、PASS済み・凍結済み・本番適用性が成立したmetricだけである。

`TRF voicing detector 1.0 / 1.1` はproduction applicabilityが未確立のため、RUN9のmandatory Gateへ使用しない。

---

# 7. Frozen Input Roles

## 7.1 AF0

役割:

```text
Identity anchor / donor
```

使用する正典:

- AF-P0 canonical Body
- AF-P0 DonorBank ingestion成果
- AF0 spec / Body / manifestのhash

制限:

- AF0のDuration、Energy、AG-alphaが子へ忠実に輸送されたとは主張しない。
- AF-T0 sidecarをRUN9の前提にしない。
- AF0のhistorical verdictを書き換えない。

## 7.2 Ritsu

役割:

```text
common stabilizing Identity anchor
```

R9F-01とR9F-02の両方で同じ0.30を持たせ、AF0方向とUser方向の差を比較しやすくする。

## 7.3 User Donor

役割:

```text
consented personal Identity anchor
```

本学習開始前に必須:

- consent / rights class
- source manifest
- preprocessing manifest
- raw / transformed hash
- sample inventory
- identity reference split

raw sourceはSource Quarantine外へ複製しない。

## 7.4 PJS

役割:

```text
External Curriculum Provider
Performance teacher only
```

PJSは次へ入れてはならない。

```text
founder identity coordinates
founder parent list
identity latent target
spk_embed target
spectral-envelope inheritance
formant inheritance
founder Genome hash inputs
```

PJSが同一モデル内のSingerRevisionとして技能差分を持たない限り、Edge名は `TRANSFER_SKILL` ではなく、`LEARN_PERFORMANCE` の `teacher_reference=PJS` とする。

---

# 8. Run 9専用Identity Domain

既存VG-E0の凍結三角形は `ritsu / pjs / user` である。PJSを教師専用にしAF0を加えるRUN9では、既存schema・既存台帳をin-place変更しない。

新しいrun-local domainを作る。

```yaml
schema: voicegenesis-identity-domain/1.0
domain_id: run9-af0-ritsu-user/1.0
anchor_order:
  - af0
  - ritsu
  - user
anchor_hashes:
  af0: <PIN_BEFORE_RUN>
  ritsu: <PIN_BEFORE_RUN>
  user: <PIN_BEFORE_RUN>
excluded_teacher_identities:
  - pjs
coordinate_precision: 6
normalization: largest-component-residual
metric_space_sha: <PIN_BEFORE_RUN>
```

必須条件:

```text
coords >= 0
sum(coords) = 1.000000
PJS key absent
anchor hashes complete
same metric/model space
```

---

# 9. TRI_CROSSOVER Operator

## 9.1 定義

RUN9では三つのIdentity anchorから、候補探索を行わず、事前固定した一回の三親交配で各Founderを出生させる。

\[
I_{child}=Normalize(w_A I_{AF0}+w_R I_{Ritsu}+w_U I_{User})
\]

operator:

```yaml
operator_id: TRI_CROSSOVER/1.0
input_count: 3
random_search: false
posthoc_weight_tuning: forbidden
skill_inheritance: false
performance_seed_shared: true
```

獲得技能は相続させない。

```text
K_R9F-01:r0 = K_default
K_R9F-02:r0 = K_default
```

## 9.2 Founder 1 — R9F-01

```yaml
voice_id: R9F-01
ecosystem_role: FOUNDER_CANDIDATE
ecosystem_generation: 0
genetic_generation: 1
identity_domain: run9-af0-ritsu-user/1.0
coords:
  af0:   0.600000
  ritsu: 0.300000
  user:  0.100000
profile_label: AF0_DOMINANT
performance_seed: 909001
parents:
  - AF0
  - RITSU
  - USER_DONOR
skill_state: DEFAULT_NEUTRAL
```

## 9.3 Founder 2 — R9F-02

```yaml
voice_id: R9F-02
ecosystem_role: FOUNDER_CANDIDATE
ecosystem_generation: 0
genetic_generation: 1
identity_domain: run9-af0-ritsu-user/1.0
coords:
  af0:   0.100000
  ritsu: 0.300000
  user:  0.600000
profile_label: USER_DOMINANT
performance_seed: 909001
parents:
  - AF0
  - RITSU
  - USER_DONOR
skill_state: DEFAULT_NEUTRAL
```

## 9.4 設計理由

- Ritsuを両者で0.30に固定する。
- AF0とUserの比率だけを対称的に反転する。
- L1距離を十分に確保し、Birth Identity差を観測しやすくする。
- 同じPerformance seedを使用し、seed差をIdentity差と誤認しない。

数値は最初のFounder render前にfreezeする。試聴後に0.55/0.45等へ調整してはならない。

---

# 10. Birth Protocol

## 10.1 出生

```text
TRI_CROSSOVER
-> immutable Genome
-> Voice ID発行
-> SingerState r0
-> fixed birth probes
-> PCM publication
-> Birth Baseline freeze
```

## 10.2 出生時の選択圧

Selection Pressure Routingに従い、出生時はViabilityだけを見る。

```text
Rights / provenance
Artifact critical failure
最低限の発声成立
Replay
Genome validity
```

出生時に二体を総合品質で競わせない。

## 10.3 Birth Identity Separation

以下を分離して記録する。

### Structural

```text
Genome ID distinct
coords distinct
Voice ID distinct
skill state equal
Backbone equal
performance seed equal
```

### Perceptual

同一のneutral identity probeで、

```text
within-founder replay distance
between-founder distance
anchor/confuser margin
```

を測る。

成立条件:

```text
between-founder distance
>
95th percentile of within-founder replay distance
```

または、校正済みIdentity Gateが同等の分離をPASSすること。

知覚分離が成立しない場合はPJS学習へ進まず、`NOT_ESTABLISHED / BIRTH_IDENTITY_NOT_SEPARATED` で閉じる。座標の事後調整は禁止する。

---

# 11. PJS Performance Lesson

## 11.1 Lesson Record

```yaml
schema: lesson-record/0.3
lesson_id: LS-R9-PJS-001
teacher_reference: PJS
teacher_role: EXTERNAL_CURRICULUM_PROVIDER
edge_application: LEARN_PERFORMANCE
skill_domain: singing-style-bundle/1.0
curriculum_sha: <PIN_BEFORE_LEARNING>
rights_class: <PIN_BEFORE_LEARNING>
identity_channels_excluded: true
training_split_sha: <PIN_BEFORE_LEARNING>
validation_split_sha: <PIN_BEFORE_LEARNING>
holdout_split_sha: <SEALED_BEFORE_LEARNING>
```

## 11.2 Lesson Channels

PJS raw audioから抽出した次のPerformance表現を教材とする。

### Mandatory target channels

```text
relative F0 contour
note / mora duration ratio
phrase-normalized energy envelope
attack timing
phrase-end timing
```

### Advisory / Experimental channels

```text
vibrato rate / depth
breath placement
release persistence
terminal mel persistence
HNR trajectory
vowel drift
```

Run8のTRF detectorが未確立の間、`release persistence` を単独mandatory Gateにしない。

## 11.3 Identity Exclusion

Lesson生成で禁止:

```text
PJS speaker embedding
PJS identity coordinate
PJS formant target
PJS spectral envelope target
PJS raw waveform reconstruction as the sole loss
PJS voice cloning objective
```

Lessonは、scoreに対するPJSのPerformance residualとして表す。

例:

```text
F0_lesson(t)      = F0_PJS(t) - F0_score(t)
Duration_lesson   = Duration_PJS / Duration_score
Energy_lesson(t)  = phrase_normalize(Energy_PJS(t))
End_lesson        = PJS phrase-end control trajectory
```

## 11.4 Freeze Rule

Lesson、split、feature extractor、normalization、metric versionを、Founder学習前にfreezeする。

```text
lesson freeze
-> learning start
-> no lesson modification
```

holdout結果を見てLesson channelを追加してはならない。

---

# 12. Dataset Split

正確な件数は入力inventory確認後にmanifestへ凍結する。割合だけでなくID列を保存する。

```text
TRAIN
VALIDATION
SEALED HOLDOUT
IDENTITY PROBE
NEGATIVE / SHAM CONTROL
```

規則:

1. splitはsong / utterance単位。隣接segmentを別splitへ分けない。
2. 同じlyrics・score fragmentの近似重複を跨がせない。
3. pitch range、phrase length、phoneme classを記録する。
4. holdoutは学習checkpoint freeze後にのみrender/evaluateする。
5. Founder別にsplitを変えない。
6. row orderをhashする。
7. PJS raw audioはSource Quarantine内に留める。

---

# 13. Learning Architecture

## 13.1 Primary Mode

RUN9のprimary learning modeは、Founderごとの `Performance Adapter` を更新する `LEARN_PERFORMANCE` とする。

```text
Shared Backbone            frozen
Founder Genome             frozen
Identity coordinates       frozen
Identity latent/spk_embed  frozen
Performance Adapter        trainable
```

各Founderに独立したAdapterを与える。

```text
R9F-01:r0 + Adapter-01:init
  -> LEARN_PERFORMANCE
  -> R9F-01:r1 + Adapter-01:trained

R9F-02:r0 + Adapter-02:init
  -> LEARN_PERFORMANCE
  -> R9F-02:r1 + Adapter-02:trained
```

二体でAdapter重みを共有しない。

## 13.2 Adapter Entry Gate

本学習開始前に以下が必要。

```text
control-layer ceiling evidence or explicit User waiver
calibrated Identity audit route
learning replay harness
rights-clean PJS curriculum
fixed compute budget
frozen learning recipe
rollback path
```

不足時:

```text
BLOCKED_ADAPTER_ENTRY
```

制御層学習へ自動でscope downgradeしない。制御層版へ変更する場合は、学習開始前のdesign revisionとして記録する。

## 13.3 Learning Recipe

`learning_recipe.yaml` に最低限以下をpinする。

```yaml
optimizer: <PIN>
learning_rate: <PIN>
steps_or_epochs: <PIN>
batch_size: <PIN>
seed: 909002
sampler: <PIN>
feature_normalization_sha: <PIN>
loss_channels:
  - relative_f0
  - duration_ratio
  - normalized_energy
  - attack_timing
  - phrase_end_timing
identity_regularization: <PIN>
checkpoint_interval: <PIN>
early_stopping: disabled_or_same_rule_for_both
hardware_execution_profile: <PIN>
```

各loss channelはcalibration scaleで正規化してから固定重みを与える。評価時の単一TotalScoreとは別物である。

## 13.4 Equal Training Budget

二体で必ず一致させる。

```text
same lesson
same split
same row order
same optimizer
same step budget
same seed policy
same hardware profile
same checkpoint rule
same evaluation probes
```

Founderごとに結果を見て追加epochを与えない。

## 13.5 Immutable Revision

in-place更新禁止。

```text
r0 remains immutable
r1 is a child revision
```

本番推論中のオンライン更新は禁止する。

---

# 14. Controls

## C0 — No-Learning Replay

`r0` を同じ条件で再renderし、renderer / backend / PCM publicationの自然変動を測る。

## C1 — Zero Adapter / Sham Transition

学習stepを実行せず、同一Adapter構造だけを付与する。Adapter導入そのものの副作用を測る。

## C2 — Anchor References

```text
AF0
Ritsu
User Donor
```

を同じIdentity Probeでrenderまたは参照し、Founderの位置とdrift方向を記録する。

## C3 — PJS Confuser

PJSをIdentity confuserとしてのみ評価へ入れる。FounderがPJSへ接近していないことを確認する。

## C4 — Training / Holdout Separation

training performanceとheld-out performanceを分離する。

```text
TRAIN_ONLY_GAIN != GENERALIZED_GAIN
```

---

# 15. Probe Set

## P0 — Neutral Identity Probe

- 同一score
- 同一lyrics
- 中央音域
- 表現指定を最小化
- Identity比較用

## P1 — Pitch / Duration Probe

- 持続音
- 音程遷移
- 短長duration
- register差

## P2 — Energy / Attack Probe

- phrase内の弱→強
- onset class差
- consonant/vowel組合せ

## P3 — Phrase-End Probe

- 短いrelease
- 長いrelease
- voiced / unvoiced ending

TRFが未校正の場合、P3はdiagnostic / advisory。

## P4 — Held-out Song

学習に使っていない曲またはscore断片。

## P5 — Held-out Register / Phrase

学習分布外寄りだが、baseline domain内の音域・フレーズ。

すべての最終WAVは、

```text
float output
-> PCM publication
-> file readback
-> meter
-> actual WAV sha256
```

の順で評価する。メモリ上floatだけで最終Gateを作らない。

---

# 16. Evaluation State

単一総合点を作らない。

```yaml
EvaluationState:
  GateState: {}
  AbsoluteVector: {}
  DevelopmentalVector: {}
  RelativeContext: {}
  AuditRoute: {}
```

## 16.1 GateState

- Rights / Provenance
- File Integrity
- Replay
- Structural Identity
- Identity audit availability
- Lesson identity exclusion
- Equal budget

## 16.2 AbsoluteVector

- Pitch / voicing
- Timing / duration
- Lyrics / phoneme intelligibility
- Acoustic integrity
- Identity margin
- learned singing quality advisory

AbsoluteはQuality Floorとして使用し、二体を一順位へ圧縮しない。

## 16.3 DevelopmentalVector

個体ごとのbefore / after。

```text
pitch_gain
voicing_gain
duration_gain
energy_contour_gain
attack_gain
phrase_end_gain
lyrics_delta
artifact_delta
identity_delta
```

## 16.4 RelativeContext

本Runでは繁殖選抜へ使わない。

記録対象:

```text
R9F-01 vs R9F-02 birth identity distance
R9F-01 vs R9F-02 learned skill distance
PJS lesson response difference
```

## 16.5 Plasticity Observation

```yaml
schema: plasticity-observation/0.3
voice_id: R9F-01
lesson_id: LS-R9-PJS-001
before_revision: R9F-01:r0
after_revision: R9F-01:r1
before_axes: {}
after_axes: {}
gain_vector: {}
identity_audit: PASS
compute_budget_sha: <sha256>
```

R9F-02にも同形式を作る。

---

# 17. Metric States

## Mandatory

```text
execution / provenance
actual WAV integrity and hash
structural identity
acoustic critical failures
pitch / voicing if calibrated
onset / duration if calibrated
energy contour if calibrated
held-out separation
learning replay
```

## Mandatory with Audit Fallback

```text
perceptual identity retention
PJS identity leakage
```

校正済み自動Identity Gateが無い場合は、blind human auditへroutingする。監査無しでPASSにしない。

## Advisory

```text
ASR / CER
SingMOS系
naturalness predictor
vibrato quality
breath quality
```

## Experimental / Disabled until calibrated

```text
TRF voicing detector 1.0 / 1.1
uncalibrated HNR threshold
uncalibrated vowel drift threshold
uncalibrated terminal mel threshold
```

校正不能なmetricは結果欄へ値を保存しても、mandatory判定へ昇格させない。

---

# 18. Developmental Adjudication

## 18.1 Target Skill Gain

各Founderについて、対象軸のheld-out差分を評価する。

\[
LCB_{95}(\Delta_{target,i})>\delta_{target}
\]

`δtarget` はpositive/negative controlから凍結する。

## 18.2 Non-Inferiority

非対象軸:

\[
LCB_{95}(\Delta_{k,i})\geq-\epsilon_k
\]

対象技能が改善しても、Identity、Pitch、Lyrics、Artifact、Replayが許容以上に悪化したRevisionはpromoteしない。

## 18.3 Differential Plasticity

次のどちらかを満たす場合に `DIFFERENTIAL_RESPONSE = ESTABLISHED` とする。

1. 事前登録した少なくとも一軸で、`Δ01 - Δ02` の95% CIがreplay-noise intervalと重ならない。
2. 片方だけがheld-out gainを満たし、もう片方が同一予算で満たさない状態がreplayで再現する。

差が無い場合も実験失敗ではない。

```text
both gain + no reliable difference
-> COMMON_RESPONSE
```

## 18.4 Diversity Retention

\[
D_{retention}=
\frac{d_{identity}(R9F01:r1,R9F02:r1)}
{d_{identity}(R9F01:r0,R9F02:r0)+\epsilon}
\]

`Dretention` は記述値であり、手入力の普遍閾値を置かない。within-founder replay分布とIdentity controlから判定する。

---

# 19. Hard Gate Set

## R9-G0 RUN_CONTRACT_COMPLETE

必須Run Contract欄がすべてpin済み。

## R9-G1 INPUT_FREEZE_AND_RIGHTS

AF0 / Ritsu / User / PJS / Backbone / code / dataset / config / metricのhashと権利来歴が揃う。

## R9-G2 IDENTITY_DOMAIN_VALID

`run9-af0-ritsu-user/1.0` がvalidで、PJSがIdentity空間に存在しない。

## R9-G3 TRI_CROSSOVER_DETERMINISM

同一anchor・weight・operatorから同一Genome ID / SingerStateが生成される。

## R9-G4 DUAL_BIRTH_VIABILITY

二体とも最低発声、artifact、replay、provenanceを満たす。

## R9-G5 BIRTH_IDENTITY_SEPARATION

学習前の二体が知覚的に区別可能。成立しなければ学習へ進まない。

## R9-G6 PJS_LESSON_FREEZE

Lesson / split / extractor / normalization / recipeが学習前にfreeze済み。

## R9-G7 TEACHER_IDENTITY_EXCLUSION

PJS Identity情報がGenome、Identity target、spk_embed target、禁止lossへ混入していない。

## R9-G8 ADAPTER_ENTRY_AND_EQUAL_BUDGET

Adapter Entry条件を満たし、二体の学習予算とrecipeが一致。

## R9-G9 IMMUTABLE_LEARNING_TRANSITIONS

`r0`不変、`r1`は別Revision、transition ledger complete。

## R9-G10 IDENTITY_AUDIT_ROUTE

各Founderの学習後Identityを校正済みmetricまたはblind auditで判定可能。

## R9-G11 HOLDOUT_SEALED

holdoutは学習・candidate selectionに未使用で、checkpoint freeze後に開封。

## R9-G12 REPLAY_AND_PUBLICATION

same-process / cross-processで以下を照合。

```text
Genome bytes
Lesson bytes
recipe/config bytes
Adapter checkpoint SHA
actual output WAV SHA
measurement record
verdict
```

publicationは `staging -> verify -> atomic rename`。

Hard Gateは能力点で相殺しない。

---

# 20. Scientific Outcome States

Hard Gateとは別に、学習結果を次で保存する。

```yaml
transfer_status:
  R9F-01: GAIN | TRAIN_ONLY | NO_GAIN | DRIFTED | UNDETERMINED
  R9F-02: GAIN | TRAIN_ONLY | NO_GAIN | DRIFTED | UNDETERMINED
plasticity_relation:
  DIFFERENTIAL_RESPONSE | COMMON_RESPONSE | UNDETERMINED
identity_status:
  PRESERVED | DRIFTED | PJS_LEAKAGE | UNCALIBRATED
```

## PASS

```text
all Hard Gates PASS
R9F-01 held-out GAIN
R9F-02 held-out GAIN
Identity PRESERVED for both
No PJS leakage
```

`DIFFERENTIAL_RESPONSE` はPASS必須条件ではない。両体が同様に学ぶ結果も有効な科学結果である。

## PASS_WITH_RESIDUAL

```text
all Hard Gates PASS
exactly one Founder has held-out GAIN
both Identity audits safe
```

解釈:

> Lesson transferはIdentity-conditionedである可能性を示すが、二体への一般転移は成立していない。

## NOT_ESTABLISHED

例:

```text
birth identity separation不成立
両体ともheld-out gainなし
train-only gain
valid protocol下でIdentity drift
valid protocol下でPJS leakage
learning collapse
```

有効な否定結果としてarchiveする。

## BLOCKED

例:

```text
rights/provenance不足
User sample不足
metric/audit不能
Adapter Entry未充足
dependency/tool不足
holdoutを作れない
```

## FAILED

例:

```text
PJSをIdentity座標へ混入
r0上書き
結果後にweight/Lesson/threshold変更
holdout leakage
provenance虚偽
replay/determinism契約違反
凍結asset改変
```

---

# 21. Promotion Rules

## Birth Promotion

R9-G0〜G5 PASS後、

```text
R9F-01:r0
R9F-02:r0
```

を `BIRTH_BASELINE_FROZEN` として保存する。

## Learned Revision Promotion

各個体を独立判定する。

```text
GAIN + Identity PASS + Replay PASS
-> CANONICAL_LEARNED_REVISION

NO_GAIN but Identity safe
-> FAILED / INTERESTING ARCHIVE

Identity drift
-> DRIFTED LEARNING ARCHIVE
```

二体を単一TotalScoreで競わせ、片方だけを残す運用は禁止する。

## Ecosystem Founder Promotion

RUN9 PASS時のみ、二体のr1を将来Parent Pool候補へ登録できる。

`PASS_WITH_RESIDUAL` 時は、学習成立した個体だけを成熟候補とし、もう一体のr0/r1は研究Archiveへ残す。User裁定なしに次世代交配へ進まない。

---

# 22. 実行順

```text
0  freeze Run Contract
1  verify repository / dependency pins
2  verify donor and teacher rights / manifests
3  build run9 Identity Domain
4  generate R9F-01:r0 and R9F-02:r0
5  render PCM Birth Probes
6  freeze Birth Baseline
7  adjudicate Birth Identity Separation
8  build and freeze PJS Lesson
9  freeze learning recipe / equal budget
10 execute R9F-01 LEARN_PERFORMANCE
11 execute R9F-02 LEARN_PERFORMANCE
12 freeze both Adapter checkpoints
13 render training/validation diagnostics
14 open sealed holdout
15 render PCM holdout probes
16 identity / leakage / developmental evaluation
17 deterministic replay
18 sparse human audit if routed
19 provenance and atomic publication
20 verdict and STOP
```

次世代交配へ自動進行しない。

---

# 23. Run Contract

本学習開始前に以下をすべて埋める。

```yaml
schema: voicegenesis-run-contract/1.0
run_id: RUN9
experiment_id: VG-R9-DUAL-FOUNDER-PJS
design_revision: 0.1
attempt_id: <PIN>

single_intervention:
  description: >
    固定された二つのFounder Genomeへ、同一PJS Performance Lessonを
    同一recipe・同一budgetで適用する。
  changed_edge: LEARN_PERFORMANCE

baseline_run: null
parent_designs:
  - DESIGN_VG_E0
  - DESIGN_VG_L0
  - VoiceGenesis_v0.3

repository_commit_sha: <PIN>
dataset_manifest_sha: <PIN>
dataset_row_order_sha: <PIN>
config_sha: <PIN>
dependency_pins_sha: <PIN>
execution_profile_sha: <PIN>
seed_policy_sha: <PIN>
expected_speaker_map_sha: <PIN>
backbone_checkpoint_sha: <PIN>
founder_genome_shas:
  R9F-01: <PIN>
  R9F-02: <PIN>
lesson_sha: <PIN>
learning_recipe_sha: <PIN>
probe_manifest_sha: <PIN>
measurement_spec_sha: <PIN>
hypothesis_algebra_sha: <PIN>
human_evaluation_protocol_sha: <PIN>
artifact_manifest_sha: <PIN_AFTER_RUN>
cost_record_sha: <PIN_AFTER_RUN>
failure_abort_criteria_sha: <PIN>
claim_strength_target: C2
```

一項目でも必須pinが欠ける場合、本学習開始禁止。

---

# 24. 推奨ディレクトリ

```text
voice_genesis/evolution/run9_dual_founder_pjs/
├── DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md
├── RUN9_CONTRACT.yaml
├── README.md
├── domains/
│   └── identity_domain_run9_v1.json
├── inputs/
│   ├── donor_manifest.json
│   ├── teacher_manifest.json
│   ├── rights_manifest.json
│   └── dependency_pins.json
├── founders/
│   ├── R9F-01_genome.json
│   ├── R9F-02_genome.json
│   ├── R9F-01_r0_state.json
│   └── R9F-02_r0_state.json
├── lesson/
│   ├── LS-R9-PJS-001.json
│   ├── feature_spec.json
│   └── split_manifest.json
├── learning/
│   ├── learning_recipe.yaml
│   ├── train_founder.py
│   └── replay_learning.py
├── evaluation/
│   ├── measurement_spec.json
│   ├── probe_manifest.json
│   ├── evaluate_development.py
│   ├── evaluate_identity.py
│   └── adjudicate_run9.py
├── tests/
│   └── test_run9_contract.py
└── results/
    └── .gitignore
```

---

# 25. Atomic Results Bundle

```text
results/RUN9/
├── RUN9_RECORD.md
├── run9_results.json
├── run_contract_frozen.yaml
├── input_pins.json
├── identity_domain.json
├── founder_registry.json
├── birth_baseline.json
├── lesson_record.json
├── learning_transitions.json
├── plasticity_observations.json
├── identity_comparison.json
├── developmental_comparison.json
├── holdout_results.json
├── replay_report.json
├── human_audit.json
├── cost_record.json
├── code_closure.json
├── rights_attestation.json
├── artifact_manifest.json
├── SHA256SUMS.txt
├── adapters/
├── probes/
└── freeze/
```

`freeze/` はPASSまたはPASS_WITH_RESIDUAL時のみ作る。

WAVのdigestはraw PCM配列ではなく、公開された実WAVファイルのSHA256を保存する。

---

# 26. run9_results.json 最小Schema

```json
{
  "schema": "voicegenesis-run9/1.0",
  "run_id": "RUN9",
  "experiment_id": "VG-R9-DUAL-FOUNDER-PJS",
  "design_revision": "0.1",
  "identity_domain": "run9-af0-ritsu-user/1.0",
  "teacher": {
    "id": "PJS",
    "role": "EXTERNAL_CURRICULUM_PROVIDER",
    "identity_excluded": true,
    "lesson_id": "LS-R9-PJS-001"
  },
  "founders": {
    "R9F-01": {
      "coords": {"af0": 0.6, "ritsu": 0.3, "user": 0.1},
      "birth_revision": "r0",
      "learned_revision": "r1",
      "transfer_status": "GAIN|TRAIN_ONLY|NO_GAIN|DRIFTED|UNDETERMINED",
      "identity_status": "PRESERVED|DRIFTED|PJS_LEAKAGE|UNCALIBRATED"
    },
    "R9F-02": {
      "coords": {"af0": 0.1, "ritsu": 0.3, "user": 0.6},
      "birth_revision": "r0",
      "learned_revision": "r1",
      "transfer_status": "GAIN|TRAIN_ONLY|NO_GAIN|DRIFTED|UNDETERMINED",
      "identity_status": "PRESERVED|DRIFTED|PJS_LEAKAGE|UNCALIBRATED"
    }
  },
  "plasticity_relation": "DIFFERENTIAL_RESPONSE|COMMON_RESPONSE|UNDETERMINED",
  "hard_gates": {},
  "scientific_outcomes": {},
  "overall": {
    "verdict": "PASS|PASS_WITH_RESIDUAL|NOT_ESTABLISHED|BLOCKED|FAILED"
  }
}
```

---

# 27. 最低テスト

```text
1  Run Contract required fields complete
2  unknown contract fields fail closed
3  base commit / dependency pins fixed
4  AF0 historical artifacts read-only
5  Ritsu source read-only
6  User raw source remains quarantined
7  PJS raw source remains quarantined
8  identity domain anchor order fixed
9  coords non-negative and sum exactly 1.000000
10 PJS coordinate is structurally impossible
11 R9F-01 weights exactly 0.6/0.3/0.1
12 R9F-02 weights exactly 0.1/0.3/0.6
13 shared performance seed is identical
14 TRI_CROSSOVER deterministic Genome ID
15 Founder IDs are distinct
16 default skill state has no inherited PJS lesson
17 r0 is immutable
18 birth probes use same score/seed/ExecutionProfile
19 actual PCM write/readback is measured
20 actual WAV file SHA is recorded
21 birth identity separation uses replay-noise control
22 no post-listening coordinate adjustment API
23 Lesson has no PJS spk_embed / identity coordinate
24 Lesson feature extraction is deterministic
25 split IDs and row order are hashed
26 holdout absent from training and validation
27 holdout sealed before training
28 learning recipe frozen before first update
29 both founders receive identical recipe and budget
30 adapters are independent per Founder
31 Backbone and Identity fields remain byte-identical
32 no online learning path
33 no founder-specific extra epochs
34 no candidate selection using holdout
35 uncalibrated metric cannot be mandatory
36 TRF 1.0/1.1 cannot gate RUN9
37 structural Identity and perceptual Identity are separate
38 PJS leakage check includes confuser comparison
39 target gain and non-inferiority are separate
40 no TotalScore field in evaluation/result schema
41 both PlasticityObservation records are emitted
42 same-process learning replay
43 cross-process learning replay
44 checkpoint SHA equality or declared deterministic contract
45 actual output WAV replay comparison
46 human audit routing capped at 12 pairs
47 blind audit loudness/order normalization
48 failed/drifted revisions are archived, not deleted
49 incomplete Hard Gate set -> BLOCKED
50 post-hoc lesson/threshold change -> FAILED
51 atomic publication rollback
52 freeze only on PASS / PASS_WITH_RESIDUAL
53 no automatic next-generation reproduction
54 no RUN9A/RUN9B/RUN9C IDs
```

---

# 28. Human Audit

自動Identity Gateが未校正または境界例の場合のみ実施する。

上限:

```text
8〜12 A/B pairs
```

質問を限定する。

1. 学習前後で同じ歌手に聞こえるか。
2. R9F-01とR9F-02は別の歌手に聞こえるか。
3. 学習後にPJS本人へ寄ったように聞こえるか。
4. 明らかな破綻・発音不能・不自然なartifactがあるか。

「どちらが好きか」「どちらがプロらしいか」をHard Gateにしない。

---

# 29. Cost Contract

記録必須:

```text
GPU type
GPU hours per Founder
wall-clock time
training steps
failed attempts
storage bytes
human audit pairs
```

二体の予算差:

```text
0 planned
```

OOM・外部障害などで片方だけ再実行する場合、両体を同じattempt条件でやり直すか、比較を`UNDETERMINED`へ落とす。

---

# 30. Stop Rules

以下で即停止する。

```text
1  donor/teacher rights unresolved
2  User donor manifest incomplete
3  anchor metric/model space mismatch
4  PJS identity channel contamination
5  TRI_CROSSOVER non-deterministic
6  one or both Founders fail viability
7  Birth Identity separation not established
8  PJS Lesson cannot be frozen
9  Adapter Entry Gate not satisfied
10 equal budget cannot be guaranteed
11 training NaN / checkpoint corruption
12 r0 or frozen Genome changed
13 holdout leakage
14 mandatory metric degeneracy without audit fallback
15 PJS Identity leakage after all preregistered candidates
16 Identity drift beyond non-inferiority
17 learning replay failure
18 provenance / code closure failure
19 cost cap exceeded
20 candidate class exhausted
```

停止後に同じattempt内で、

```text
new weights
new teacher
new Founder
new metric threshold
new Lesson channel
new optimizer search
```

を追加しない。

修正が必要ならExperiment IDはRUN9のまま、`design_revision`を上げ、旧attemptをappend-only履歴として残す。

---

# 31. 役割分担

## Codex / Implementation Agent

```text
schema and validators
run-local Identity Domain
TRI_CROSSOVER operator
Founder state generation
Lesson extraction
learning harness
adapter freeze
probe render
metric execution
replay
atomic publication
```

## Claude / Contract Auditor

```text
design conformance
PJS Identity leakage audit
single-intervention audit
holdout leakage audit
claim ceiling
metric calibration status
failure classification
minimality audit
```

## User

```text
User donor consent / provenance confirmation
Founder coordinate design approval
PJS teacher role approval
cost cap
human audit when routed
next-generation progression
```

通常の候補調整はAI側で自動反復しない。

---

# 32. 次段

RUN9の後に自動で交配しない。

PASS時に初めて、次の別Run候補を設計できる。

```text
R9F-01:r1
x
R9F-02:r1
-> next-generation inheritance experiment
```

その次段では、

- Genomeを遺伝させるか
- 獲得Skillを相続させないか
- Lessonとして再教育するか

を再び分離する。

---

# 33. Completion Declaration

## PASS

> **VOICEGENESIS RUN 9 COMPLETE — Two distinct ecosystem founder candidates were deterministically generated from AF0, Ritsu, and the consented User donor, then both acquired held-out singing-performance gain from the same frozen PJS-derived lesson under equal learning budgets while retaining their own identities and excluding PJS identity transfer.**

必ず続ける。

```text
AF-P0 historical results remain unchanged.
AF-T0 is not a prerequisite and is not retroactively adjudicated.
RUN8 remains an independent track.
No next-generation reproduction was performed.
Differential plasticity is reported separately from transfer success.
```

## PASS_WITH_RESIDUAL

> **VOICEGENESIS RUN 9 COMPLETE WITH RESIDUAL — The protocol completed with identity safety, but the frozen PJS lesson produced held-out gain in only one of the two founder candidates.**

## NOT_ESTABLISHED

> **VOICEGENESIS RUN 9 NOT ESTABLISHED — The preregistered dual-founder common-teacher condition did not establish transferable held-out learning with retained identity under the tested conditions.**

---

# 34. Primary Source Basis

本設計は次の既存正本・設計を基礎にする。

1. `VoiceGenesis_Evolution_Theory_v0.3_ja.md`
   - E / L / T / S回路分離
   - Genome != Skill != Cultural Lineage
   - 獲得技能の自動相続禁止
   - Birth Quality / Trained Quality / LearningGain分離
   - Structural / Perceptual Identity分離

2. `VoiceGenesis_v0.3_Supplement_A_Evaluation_Selection_Pressure_Routing_ja.md`
   - BirthはViability
   - L回路は自己比較
   - DevelopmentalとRelativeを混同しない
   - 一回路一主選択圧

3. `VoiceGenesis_Singing_Baseline_Design_v0.1_ja`
   - Score / Identity / Quality / Executionの分離
   - control-calibrated threshold
   - uncalibrated metricのHOLD routing
   - cross-song generalization
   - no TotalScore

4. `VoiceGenesis_Debt_Repayment_Plan_v1.md`
   - 新規Run Contract必須欄
   - single intervention
   - dependency pins
   - fixed probe / measurement spec / abort criteria

5. `voice_genesis/evolution/DESIGN_VG_E0.md`
   - barycentric Identity coordinates
   - deterministic Genome ID
   - seed / Identity分離
   - append-only lineage

6. `voice_genesis/evolution/DESIGN_VG_L0.md`
   - Voice Identityと学習書き込み先の分離
   - immutable LearningTransition
   - Performance Adapter Entry条件
   - 教育差分と交配の区別

7. `voice_genesis/foundry/DESIGN_DONOR_EXPANSION.md`
   - Identity donorとteacherの役割分離
   - teacherをIdentity空間へ逆流させない

8. `VoiceGenesis_AF-T0_Trait_Transport_Fidelity_Design_v1.0.md`
   - AF0の歴史的判定不変
   - AF0を利用しても未確立形質を過大主張しない
   - freeze / fresh holdout / determinism / provenance / atomic publication規律

---

# 35. 最終原則

> **三体から二つの異なる声を生み、二体へ同じ先生を与える。**
>
> **出生では声の違いを固定し、学習では本人との比較だけを行い、教師の声そのものは移さない。**
>
> **結果は「どちらが優秀か」ではなく、「同じ教育を受けた別の遺伝個体が、別個体のままどう育ったか」として保存する。**
