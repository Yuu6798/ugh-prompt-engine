# Task Brief: Q1-4 — pro_baseline ジャンル別拡張

## Phase
roadmap_goal1.md Q1-4

## Goal
`pro_baseline.yaml` 単一構成を「Pro / Loud Pop / Acoustic / EDM」の 4 セットに
拡張し、`scorer_rpe` に `--baseline` フラグで切替可能にする。これにより
ジャンル特性の異なる音源を「適切な参照値」で採点でき、Pro 1 セットに
固定された硬直性を解消する。

## Acceptance Criteria
- [ ] `config/loud_pop_baseline.yaml`, `config/acoustic_baseline.yaml`,
  `config/edm_baseline.yaml` を新規追加（パッケージング側 `src/svp_rpe/config/`
  にも同期）
- [ ] `pro_baseline.yaml` は破壊的変更なし（既存 default を維持）
- [ ] `score_rpe(phys, *, baseline="pro")` がパラメータを受け取り、
  `f"{baseline}_baseline"` config を読む
- [ ] 不正な baseline 名 → `FileNotFoundError`（明示的なエラー）
- [ ] CLI の `evaluate` / `run` / `batch` コマンドに `--baseline` flag を追加
- [ ] 同一音源を 4 baseline で score して **少なくとも 2 つの baseline 間で
  RPE score が >0.05 異なる**（ジャンル切替が実効的に効いていることを確認）
- [ ] 既存テストは引き続き pass（default `baseline="pro"` で
  rpe_score / evaluation.json が変わらない）
- [ ] snapshot --check OK（default baseline で hash 不変）

## Scope
- IN:
  - `config/loud_pop_baseline.yaml`, `config/acoustic_baseline.yaml`,
    `config/edm_baseline.yaml`（新規）
  - `src/svp_rpe/config/loud_pop_baseline.yaml` 他（パッケージ側に同期）
  - `src/svp_rpe/eval/scorer_rpe.py`（baseline param 追加）
  - `src/svp_rpe/cli.py`（CLI flag 追加）
  - `src/svp_rpe/batch/runner.py`（必要なら baseline 受け渡し）
  - `tests/test_genre_baseline.py`（新規）
  - `.claude/briefs/Q1-4-genre-baseline.md`
  - `.claude/briefs/_index.md`
- OUT:
  - `src/svp_rpe/rpe/`（extractor は不変）
  - `examples/sample_input/`, `examples/expected_output/`（synth は genre
    label を持たないので default baseline で snapshot 不変）
  - `docs/`（Q1 完了時にまとめて更新）
  - 自動 genre 検出（roadmap Q1-4 リスク表に「ユーザー明示指定」と既述）

## Allowed Dependencies
なし

## Implementation Hints
- baseline 値は **暫定値**。実音源での calibration は将来タスク（Q1-fu）。
  各 yaml の冒頭コメントで「これは starting value、calibration 未済」を
  明記
- 提案する暫定値（音楽プロダクションの一般傾向に基づく）:
  | metric | Pro (既存) | Loud Pop | Acoustic | EDM |
  |---|---|---|---|---|
  | rms_mean | 0.298 | 0.350 | 0.180 | 0.400 |
  | active_rate_ideal | 0.915 | 0.950 | 0.700 | 0.970 |
  | crest_factor_ideal | 5.0 | 3.5 | 7.0 | 3.0 |
  | valley_depth | 0.2165 | 0.150 | 0.350 | 0.100 |
  | thickness | 2.105 | 2.300 | 1.500 | 2.500 |
- `score_rpe` のシグネチャ:
  ```python
  def score_rpe(phys: PhysicalRPE, *, baseline: str = "pro") -> RPEScore:
  ```
- 不正 baseline 名で fallback dict を使うのは「Pro のみ」現状の挙動を
  破壊しないため。それ以外は明示的に `FileNotFoundError` を伝播

## Required Outputs
- ブランチ名: `claude/q1-4-genre-baseline`
- PR タイトル: `feat(eval): genre baselines (loud_pop / acoustic / edm) (Q1-4)`
- 期待する変更ファイル:
  - `config/{loud_pop,acoustic,edm}_baseline.yaml`
  - `src/svp_rpe/config/{loud_pop,acoustic,edm}_baseline.yaml`
  - `src/svp_rpe/eval/scorer_rpe.py`
  - `src/svp_rpe/cli.py`
  - `src/svp_rpe/batch/runner.py`
  - `tests/test_genre_baseline.py`
  - `.claude/briefs/Q1-4-genre-baseline.md`
  - `.claude/briefs/_index.md`

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文に synth_03 を 4 baseline で score した結果の比較表を掲載
- snapshot --check OK
