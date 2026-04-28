# Task Brief: Q1-1 — LUFS / true_peak 統合 (pyloudnorm)

## Phase
roadmap_goal1.md Q1-1

## Goal
ITU-R BS.1770 標準準拠の loudness 測定 (LUFS) と true peak (dBFS) を
PhysicalRPE に追加し、業界標準のラウドネス指標を svp-rpe で観測可能にする。

## Acceptance Criteria
- [ ] `pyproject.toml` の runtime 依存に `pyloudnorm>=0.1` を追加
- [ ] `PhysicalRPE` に `loudness_lufs_integrated: Optional[float]` と
  `true_peak_dbfs: Optional[float]` を追加（Optional で後方互換）
- [ ] `physical_features.py` に `compute_loudness(y, sr) -> tuple[Optional[float], Optional[float]]`
  を実装し、extractor から呼び出す
- [ ] **ITU-R BS.1770 リファレンス検証**: 1 kHz / -20 dBFS / 30s mono の
  正弦波を生成して LUFS 測定 → **-23.045 ± 0.5 LU 一致**（pytest）
- [ ] True peak は 4x oversampling 後の最大 abs（ITU-R BS.1770-4 簡易実装）
- [ ] 短い音声（< 0.4 秒、pyloudnorm の最小ブロック長未満）では fallback
  として None を返す（fail-fast でなく fallback chain）
- [ ] `examples/expected_output/` を再生成し snapshot test が引き続き green
- [ ] 既存テスト 129 件は引き続き pass（schema は backward-compatible
  Optional 追加のみ）

## Scope
- IN:
  - `pyproject.toml`（pyloudnorm 追加）
  - `src/svp_rpe/rpe/models.py`（PhysicalRPE フィールド追加）
  - `src/svp_rpe/rpe/physical_features.py`（compute_loudness 追加）
  - `src/svp_rpe/rpe/extractor.py`（compute_loudness 呼び出し）
  - `tests/test_loudness.py`（ITU 参照検証）
  - `examples/expected_output/`（snapshot 再生成）
  - `.claude/briefs/Q1-1-loudness-lufs.md`（本 Brief）
  - `.claude/briefs/_index.md`
- OUT:
  - `src/svp_rpe/eval/`（scorer は LUFS をまだ使わない、別タスク）
  - `scripts/validate_against_truth.py`（LUFS の真値が ground_truth に無い、
    別タスクで対処）
  - `docs/`（Q1 完了時にまとめて更新）

## Allowed Dependencies
- `pyloudnorm>=0.1`（roadmap_goal1.md Q1-1 で事前許可済）

## Implementation Hints
- `pyln.Meter(sr).integrated_loudness(y)` で LUFS（mono / stereo 両対応）
- True peak: `scipy.signal.resample_poly(y, 4, 1)` で 4x oversample → `20*log10(max(abs))`
- pyloudnorm の最小ブロック長は 0.4 秒。短い場合 ValueError → catch で None
- データが完全 silence (LUFS = -inf) のとき → None として扱う
- mono / stereo 両方を love可能にするため、`y` の shape を意識
  （mono: (N,) / stereo: (N, 2) — pyloudnorm はどちらも受理）

## Required Outputs
- ブランチ名: `claude/q1-1-loudness-lufs`
- PR タイトル: `feat(rpe): integrate ITU-R BS.1770 loudness (LUFS + true peak) (Q1-1)`
- 期待する変更ファイル:
  - `pyproject.toml`
  - `src/svp_rpe/rpe/models.py`
  - `src/svp_rpe/rpe/physical_features.py`
  - `src/svp_rpe/rpe/extractor.py`
  - `tests/test_loudness.py`
  - `examples/expected_output/**/*` (snapshot 再生成)
  - `.claude/briefs/_index.md`

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文に 5 曲の LUFS / true_peak 実測値を表として掲載
