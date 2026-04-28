# Task Brief: Q1-3 — BPM 信頼度の再設計

## Phase
roadmap_goal1.md Q1-3

## Goal
`compute_bpm` の confidence 計算を「120 BPM からの距離」というナンセンスな
ヒューリスティックから、**実際のリズム規則性に基づく** rank/consistency
ベースの値に置き換える。これにより `bpm_confidence` が下流で意味のある
重み付けに使える状態になる。

## Acceptance Criteria
- [ ] `physical_features.compute_bpm` が新しい confidence 公式を使う
- [ ] **真値 ±5 BPM 以内で BPM 推定された曲は confidence > 0.7**
  （synth_02 / synth_03 / synth_04 / synth_05 の 4 曲全てで満たす）
- [ ] 旧式 `1 - abs(bpm-120)/120` は完全撤廃
- [ ] `tests/test_bpm_confidence.py` に 4 件の AC テスト + 公式の単体テスト
- [ ] 既存テスト 137 件は引き続き pass
- [ ] `examples/expected_output/` を再生成し snapshot test green

## Scope
- IN:
  - `src/svp_rpe/rpe/physical_features.py`（`compute_bpm` redesign）
  - `tests/test_bpm_confidence.py`（新規）
  - `examples/expected_output/`（snapshot 再生成）
  - `.claude/briefs/Q1-3-bpm-confidence.md`（本 Brief）
  - `.claude/briefs/_index.md`
- OUT:
  - `src/svp_rpe/eval/`（scorer は bpm_confidence をまだ重み付けに使わない）
  - `scripts/validate_against_truth.py`（confidence 検証は別タスク）

## Allowed Dependencies
なし

## Implementation Hints
- 設計判断として **beat interval CV (Coefficient of Variation)** を採用:
  - `tempo, beats = librosa.beat.beat_track(y, sr)`
  - `beat_times = librosa.frames_to_time(beats, sr=sr)`
  - `intervals = np.diff(beat_times)`
  - `cv = std(intervals) / mean(intervals)`
  - `confidence = clamp(1.0 - 5.0 * cv, 0.0, 1.0)`
  - 直感: regular beats → low CV → high confidence
- 5 倍係数の根拠: 4 曲（synth_02-05）で実測 CV ∈ [0.024, 0.035] →
  confidence ∈ [0.83, 0.88]、AC 0.7 を余裕でクリア
- **既知の限界**: octave error（synth_01: 60 BPM 真値 → 123 BPM 推定）でも
  beat interval は規則的なので confidence は高めに出る。AC は「正しいとき
  >0.7」のみ規定で「間違いのとき <0.7」は規定外。下記の Notes に明記
- 探索したが採用しなかった代替案:
  - **Top-bin / total**: 最頻 tempo bin の比率 → ≤0.55 で AC 未達
  - **Tempogram peak strength**: chosen BPM での autocorrelation 強度 →
    0.58-0.71 で AC 未達
  - **Top1 / (Top1+Top2)**: histogram dominance → 0.52-0.74 で synth_03 が未達
- edge case:
  - beats が 1 個以下 → confidence = 0.0 (interval 計算不可)
  - mean interval ≤ 0 → confidence = 0.0

## Required Outputs
- ブランチ名: `claude/q1-3-bpm-confidence`
- PR タイトル: `feat(rpe): rank-based BPM confidence (CV of beat intervals) (Q1-3)`
- 期待する変更ファイル:
  - `src/svp_rpe/rpe/physical_features.py`
  - `tests/test_bpm_confidence.py`
  - `examples/expected_output/`（rpe.json で confidence 値が変わる）
  - `.claude/briefs/Q1-3-bpm-confidence.md`
  - `.claude/briefs/_index.md`

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文に 5 曲の旧 confidence vs 新 confidence の対照表を掲載
