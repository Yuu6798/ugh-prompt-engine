# Task Brief: Q0-4 — mir_eval 統合 + ground-truth 検証スクリプト

## Phase
roadmap_goal1.md Q0-4

## Goal
`examples/sample_input/ground_truth.yaml` の真値と svp-rpe パイプライン出力を
`mir_eval` で対真値比較するスクリプトを整備する。これによって BPM / key /
構造境界の推定精度が定量的に測定でき、Q0-5（validation.md）の表データを
生成するソースになる。

## Acceptance Criteria
- [ ] `pyproject.toml` の `dev` 依存に `mir_eval>=0.7` を追加
- [ ] `scripts/validate_against_truth.py` が存在し、無引数実行で 5 曲分の
  対真値比較を表として stdout に出力
- [ ] `--json` フラグで structured JSON を出力（Q0-5 が消費）
- [ ] 比較対象: **BPM / key / section boundaries**（roadmap の "onset" は
  ground_truth に onset 真値が無いため section boundaries に置換 — 設計判断
  として PR 本文に明記）
- [ ] BPM: 真値との absolute diff + `mir_eval.tempo.detection` の p_score
- [ ] Key: `<note> <mode>` 文字列を `mir_eval.key.evaluate` に渡し
  Weighted Score を取得
- [ ] Sections: `mir_eval.segment.detection` で window=0.5s および 3.0s の
  両方で P/R/F を計算
- [ ] スクリプトが `--check` フラグを持ち、最低基準（BPM 誤差 < 5 BPM,
  key Weighted Score >= 0.5, segment F@3s >= 0.5）を満たさない曲があれば
  exit 1。デフォルトモード（`--check` なし）は exit 0 のまま結果を表示
- [ ] 既存 127 件のテストが引き続き pass
- [ ] スクリプト自身を呼ぶ pytest を 1 件追加（`--json` モードを
  parse 可能か smoke test）

## Scope
- IN:
  - `scripts/validate_against_truth.py`（新規）
  - `pyproject.toml`（dev extra に mir_eval 追加のみ）
  - `tests/test_validation_script.py`（smoke test）
  - `.claude/briefs/_index.md`（Q0-4 を In Progress に）
- OUT:
  - `src/svp_rpe/`（実装変更が必要になったら escalation）
  - `examples/expected_output/`（Q0-2 成果物に手を入れない）
  - `examples/sample_input/`（PR #9 の成果物に手を入れない）
  - `docs/`（Q0-5 で扱う）

## Allowed Dependencies
- `mir_eval>=0.7`（roadmap_goal1.md の "Q0-4: mir_eval" として事前許可済）

## Implementation Hints
- `scripts/regenerate_expected.py` の `load_song_ids()` と
  `extract_rpe_from_file` を直接再利用（subprocess 不使用）
- ground_truth.yaml には `bpm`, `key`, `mode`, `sections` (start_sec/end_sec
  ペア), `section_boundaries_sec` が記録済
- mir_eval.key.evaluate は "C major" / "F# minor" 形式を受理（lowercase 可）
- mir_eval.segment.detection は intervals shape (N, 2) の np.array を要求
  → `phys.structure[i].start_sec / end_sec` から構築
- BPM 比較は `mir_eval.tempo.detection(np.array([gt_bpm, 0.0]), 1.0,
  np.array([est_bpm, 0.0]))` で OK（候補 1 つの簡略形）
- 出力フォーマットは `rich.table` か markdown 風の自前テーブル。Q0-5 で
  validation.md に貼り付けやすい形（markdown table）が望ましい

## Required Outputs
- ブランチ名: `claude/q0-4-mir-eval-validation`（Plan B / Claude 代行）
- PR タイトル: `feat(scripts): add ground-truth validation against mir_eval (Q0-4)`
- 期待する変更ファイル:
  - `scripts/validate_against_truth.py`
  - `pyproject.toml`
  - `tests/test_validation_script.py`
  - `.claude/briefs/_index.md`
- 必須テスト: validation script の `--json` smoke test

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文に validation 結果のサンプル出力（5 曲分の BPM / key /
  segment F@3s スコア表）を貼付
- 5 曲のうち少なくとも 3 曲で `--check` の最低基準を満たすことを実測
  （満たさない曲は次フェーズで baseline 調整 / extractor 改善対象に）
