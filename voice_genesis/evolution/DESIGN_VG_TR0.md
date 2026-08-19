# DESIGN VG-TR0 — Trial Layer（実戦試験層）v0

- 起草: User 叩き台 2026-08-19（本文の構成・schema・原則は User 起草を
  ほぼ逐語で採用）+ Fable 統合注記（§S1–S5・既存契約との接続）。
  **User 採用 2026-08-19**
- 対象: VoiceGenesis Evolution Theory v0.3
- 位置づけ: Birth / Development の後、Evaluation / Selection の前に置く
  **観測層**。[`DESIGN_SPR.md`](DESIGN_SPR.md) の全 Routing 行に対する
  **証拠（一次データ）生産層**
- 目的: 固定された SingerRevision に複数の曲・条件を与え、
  「何ができるか」「どこで壊れるか」を**表現型**として観測する
- 非目標: Trial 中の学習・教育・淘汰・Canonical 更新
- 基本原則: **Trial は試験。Evaluation は採点。Selection は処遇決定。
  混ぜない**（= 本リポジトリの確立原則「audit は裁判官でなく計器」の
  生態系版）

## 0. 全体フロー

```text
Birth / E
    ↓
Development / L・T
    ↓
SingerRevision FREEZE
    ↓
┌──────────────────┐
│ Trial Layer / TR │  実際に歌わせる
└──────────────────┘
    ↓
TrialRecord（観測事実のみ）
    ↓
Evaluation / SPR（採点 = Routing 各行）
    ↓
Selection（処遇: Breeder / Elite / Continue / Retrain→L / Teach→T /
           Archive / Retire）
```

Trial 層自身は Singer を変更しない。

## 1. PoR

> 固定された人工歌手を異なる楽曲・歌唱条件へ投入したとき、どの条件で
> 能力を発揮し、どの条件で破綻するかを、学習や評価判断を混ぜず
> 再現可能に観測できるか。

## 2. Trial の基本単位

```text
TrialResult = F( SingerRevision, TrialScore, TrialCondition, ExecutionProfile )
```

Trial 中に固定するもの: Voice Genome / spk_embed・Identity state /
Skill Profile / Performance Adapter / Shared Backbone checkpoint / seed /
renderer version / ExecutionProfile。

変えてよいもの: 曲・音域・テンポ・歌唱要求・Trial 用の明示的環境条件のみ。

> **統合注記 S1（v0.2 語彙の位相差）**: SingerRevision / Skill Profile /
> Performance Adapter は v0.2 語彙であり現行実装には存在しない
> （VISION v0.2 収載ヘッダ注記 2 と同じ位相差）。TR0 PoC における
> SingerRevision の実体 = **(checkpoint sha256, 話者, 制御プロファイル版
> 〔VG-L0 導入後。それまでは既定値 r0〕)** の組と読み替える。

## 3. TrialScore（schema: `trial-score/0.1`）

任意の試験曲を VoiceGenesis が解釈できる正規形へ変換する。

```yaml
schema: trial-score/0.1
trial_score_id: TS-000041
source_class: INTERNAL | PROCEDURAL | RIGHTS_CLEAN
score_sha256: ...
lyrics_sha256: ...
tempo_bpm: 128
notes:
  - {pitch: 64, start_ms: 0, duration_ms: 480, lyric: "あ", phonemes: ["a"]}
phrases:
  - {start_ms: 0, end_ms: 4200}
metadata:
  register_class: mid      # low / mid / high
  tempo_class: medium      # slow / medium / fast
  lyric_density: normal
  dynamic_class: neutral
```

初期 PoC では既存 SCORE_REGISTRY の曲（sakura / umi / d3_sustain /
d3_kana）を TrialScore として再利用してよい。将来は
MIDI / MusicXML + lyrics → TrialScore compiler → `trial-score/0.x` へ拡張。

## 4. TrialCondition（schema: `trial-condition/0.1`）

```yaml
schema: trial-condition/0.1
condition_id: TC-HIGH-FAST-001
pitch_shift_semitones: 0
tempo_scale: 1.0
expression: {intensity: neutral, dynamics: default}
constraints:
  allow_learning: false
  allow_state_mutation: false
  allow_identity_change: false
```

TrialCondition は「歌手を変える命令」ではなく「**試験環境を変える命令**」
に限定する（歌手側の技能状態 = 制御プロファイルは VG-L0 の管轄であり
Trial 中は凍結。両者を混同しない）。

## 5. 初期 Trial Suite（TR0 は全てを測ろうとしない — 6 群から開始）

| # | Trial | 内容 | 目的 |
|---|---|---|---|
| T1 | 基準歌唱 | 通常音域・通常テンポ・通常表現 | そもそも安定して歌えるか |
| T2 | Register Trial | low / mid / high | 音域別破綻・register transition・高低音耐性 |
| T3 | Tempo / Articulation | slow / normal / fast | 高速歌詞・子音潰れ・duration 追従・音素遷移 |
| T4 | Sustain Trial | ロングトーン主体（既存 d3_sustain 譜が転用可能） | F0 stability・vibrato・長音破綻・breath/noise |
| T5 | Dynamics Trial | weak / normal / strong | 弱声・強声・attack・声質崩壊 |
| T6 | Held-out Song Trial | 稽古・教育に未使用の曲 | 技能転移・過学習検出・実曲汎化の初観測（VG-L0 の held-out 判定と同一概念） |

## 6. TrialRecord（schema: `trial-record/0.1`）— 観測事実のみ

Trial 層では「優秀」「不合格」とまだ判断しない。

```yaml
schema: trial-record/0.1
trial_id: TR-000881
singer_revision: VG-000421:r18      # PoC では統合注記 S1 の読み替え
trial_score_id: TS-000041
condition_id: TC-HIGH-001
execution_profile: EP-0003
input_hashes: {genome: ..., skill_profile: ..., backbone: ..., score: ..., renderer: ...}
output: {wav_sha256: ..., duration_ms: 18342}
observations:
  pitch:           {available: true, value: 0.94}
  intelligibility: {available: false, value: null}   # 未校正計器は available: false
  artifact:        {clipping: false, noise_event_count: 1}
  identity:        {automatic: null, human_audit: null}
failure_events:
  - {type: REGISTER_BREAK, start_ms: 12840, end_ms: 13120}
trial_status: COMPLETED
```

**`trial_status = COMPLETED` は `quality = PASS` を意味しない**。
Trial と Evaluation を分離する。

> **統合注記 S2（観測値の正直会計）**: 自動観測の現在地は svp_rpe 抽出
> 系（pitch/F0・ラウドネス等）の一部のみで、intelligibility・identity の
> 自動値は未校正。初期 TrialRecord は該当 observation を
> `available: false` とし、failure_events は耳判定由来で記録する
> （計器が無いのに値を書かない — fail-closed の観測版）。

> **統合注記 S3（failure 語彙の管理）**: `failure_events.type` は管理
> 語彙とする（初期: REGISTER_BREAK / CONSONANT_SMEAR / LONG_TONE_COLLAPSE /
> NOISE_EVENT / IDENTITY_DRIFT_SUSPECT / RENDER_FAIL）。追加は本書の改訂
> PR による（intent graph の status 語彙と同じ運用）。

## 7. Failure Profile（schema: `trial-profile/0.1`）— 点数ではなく表現型マップ

```yaml
schema: trial-profile/0.1
singer_revision: VG-000421:r18
strengths: [MID_REGISTER, SUSTAIN, SLOW_PHRASE]
weaknesses: [HIGH_REGISTER, FAST_ARTICULATION]
failure_modes:
  HIGH_REGISTER: [REGISTER_BREAK]
  FAST_ARTICULATION: [CONSONANT_SMEAR]
```

## 8. Trial と Evaluation の境界

- Trial: 「高音曲で 3 箇所 register break が発生した」（観測）
- Evaluation: 「High-register stability が Quality Floor 未満」（採点）
- Selection: 「繁殖対象外 / 再育成 / 保護 Archive」（処遇）

**Observation → Evaluation → Decision を必ず分離する。**
TrialRecord に elite / reject / breeder 等の Selection verdict を書かない。

## 9. Trial と SPR の接続（Trial = SPR の入力データ生成層）

| SPR 行 | Trial 供給 |
|---|---|
| 行 1（Viability） | 出生直後の Trial は軽量（TRS-BIRTH）。発声可能・critical artifact なし・renderer 成立のみ観測。成熟品質では淘汰しない |
| 行 2（LearningGain） | 学習前後を**同一 TrialSuite** で: `Trial(r1) − Trial(r0)` の差分を Evaluation 層が Developmental として解釈 |
| 行 3（Transferability） | Lesson 適用前後を同一 TrialSuite で測り、複数 Singer の Trial 差分を集めて後段評価 |
| 行 4/5（Floor / Reproduction） | 同一 TrialSuite を成熟個体へ適用し、Absolute Floor と niche 相対選抜の証拠を供給 |
| 行 7（S） | Backbone 更新前後で**同じ固定 Singer 群 × 同じ TrialSuite** を再実行し Population 全体の変化を測る |

## 10. Failure Routing（Trial 層自身は修正しない — 後段が原因分類して回路へ返す）

- 個体固有の技能不足 → **L**
- 既知 Lesson で改善可能 → **T**
- 特定 Genome / lineage に集中 → **E の研究材料**
- 全 Singer 共通で発生 → **S**
- Score / renderer 自体の問題 → **Trial infrastructure**

例: 10 人中 1 人だけ高音崩壊 → 個体問題の可能性。
10 人中 10 人が同じ音素で崩壊 → Backbone / renderer / score 側を疑う。

## 11. Determinism Contract

- Trial は状態を変えない: `before_state_hash == after_state_hash` を必須記録
- 同一 (SingerRevision, TrialScore, TrialCondition, seed, ExecutionProfile)
  なら規定の D2/D3 契約内で再現できること
- 記録: `state_mutation: NONE` / `render_replay: {run_count: 2,
  status: PASS, wav_match: BYTE_IDENTICAL | TOLERANCE_PASS}`
  （実装 = 既存 wav sha256 pin 規律。SPR の Render Reproducibility と同一）
- Trial 中に L / T を実行することは禁止

## 12. Trial Suite の公平性

Singer 間比較に使う場合: same TrialSuite / same score version /
same condition / same seed policy / same renderer / same ExecutionProfile
を保証する。特定 Singer だけ簡単な曲を与えない。TrialSuite 自体も
versioned にする（schema: `trial-suite/0.1`・suite_id + trial id 列）。

## 13. Trial Suite の種類（目的ごとに分ける — 巨大単一 Suite を全用途で使わない）

TRS-BIRTH（出生 viability 用・軽量短尺）/ TRS-LEARNING（L 前後比較）/
TRS-TRANSFER（T 前後・1:N 教育）/ TRS-MATURE（成熟個体の評価）/
TRS-BACKBONE（S 更新前後比較）/ TRS-STRESS（破綻条件探索）

## 14. Trial 層で禁止すること

1. Trial 中の学習
2. Trial 中の Adapter 更新
3. Trial 中の spk_embed 更新
4. Trial 結果による即時淘汰
5. Trial 内での総合スコア生成
6. Singer ごとに試験条件を無記録変更
7. hidden state を次 Trial へ持ち越す
8. Evaluation verdict を TrialRecord に混入
9. Trial 失敗を理由に履歴を削除
10. 同じ trial_id への in-place overwrite

> **統合注記 S4（台帳の置き場所）**: TrialRecord / TrialProfile /
> TrialSuite の台帳は **VG-E0 / VG-L0 の台帳と別系列の新設**
> （`voice_genesis/trial/` 配下・schema は本書の 4 種を実装 PR で定義）。
> **VG-E0 の凍結 schema 3 種は非改変**。個体参照は genome_id または
> 話者名（統合注記 S1 の読み替えに従う）。

## 15. 最小 PoC — VG-TR0

**問い**: 同一 SingerRevision を複数の TrialScore / Condition へ投入し、
状態を変えずに再現可能な Failure Profile を取得できるか。

最小構成: Singer 1〜3 Revision / 曲 = さくら・うみ（+ 可能なら 1 曲追加）/
条件 = normal・high-register・fast・sustain（sustain は既存 d3_sustain 譜
転用）/ 生成 = 既存 gate_synth・renderer / 出力 = WAV + TrialRecord +
TrialProfile + Replay record。

> **統合注記 S5（実装第 1 タスク）**: high-register / fast の条件は
> **score レベルの決定論的変換**（移調 semitones・tempo_scale）の実装を
> 要する（現行 SCORE_REGISTRY は固定譜のみ）。VG-L0 の「gate_synth 入力
> での制御軸表現力の実測」と同一ファミリーの調査として**先に実測**し、
> 表現できない条件は TR0 スコープから外して記録する（fail-closed）。

## 16. Acceptance Criteria

- [ ] `trial-score/0.1` / `trial-condition/0.1` / `trial-record/0.1`
  （+ `trial-suite/0.1` / `trial-profile/0.1`）の定義（新系列台帳・
  VG-E0 非改変 = 統合注記 S4）
- [ ] SingerRevision を read-only で受ける Trial runner
- [ ] 同一 Trial の replay + before/after state hash 一致
- [ ] 複数条件の WAV 生成 + failure event 記録（管理語彙 = 統合注記 S3）
- [ ] TrialProfile 集約
- [ ] TrialRecord に Evaluation / Selection verdict が存在しないこと
- [ ] L/T/E/S への Failure Routing は別処理として実装

## 17. 将来拡張（TR0 成立後にのみ検討）

- **Trial Curriculum**: easy → normal → hard → adversarial の段階化
- **Adaptive Trial**: 弱点発見 Singer への追加試験。ただし探索用 Adaptive
  Trial と公平比較用 Fixed Trial は**別 Suite** にする
- **Environment Evolution**: 世代進行に伴い Trial 自体も高度化。過去
  TrialSuite は削除せず version 付きで比較可能性を残す

## 18. 最終定義

> **VoiceGenesis Trial Layer** — 固定された SingerRevision に対し、
> versioned TrialScore と TrialCondition を与えて実際に歌唱させ、その
> 表現型応答と Failure Profile を再現可能に記録する観測層。Trial は
> Singer を変更せず、評価・淘汰も行わない。生成された観測結果を後段の
> Evaluation / Selection Pressure Routing へ渡し、必要に応じて
> L / T / E / S のどの回路へ改善課題を返すべきかを判断するための
> 一次データを供給する。

最短原則: **産む → 育てる → 試す → 測る → 選ぶ** のうち Trial が担当する
のは「**試す**」だけ。Singer を変えず、採点もせず、実際に歌わせて、
何が起きたかだけを残す。
