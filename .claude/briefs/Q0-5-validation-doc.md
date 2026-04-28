# Task Brief: Q0-5 — validation.md 初版

## Phase
roadmap_goal1.md Q0-5

## Goal
Q0-4 の `validate_against_truth.py --json` 出力を docs に固定化する。
5 曲分の BPM 誤差・key 一致率・section 境界 F-measure を表として記録し、
Q0 完了基準（roadmap_goal1.md）の達成状況を明示する。Q1 以降の
改善効果を比較するベースラインになる。

## Acceptance Criteria
- [ ] `docs/validation.md` を更新し、Q0-5 専用セクションを追加
  （既存の PoC 警告・Interpretation Rules は保持）
- [ ] Per-song 表（5 曲 × BPM/key/segment）が記録されている
- [ ] 集計（BPM 平均誤差、key 一致率、segment F@3s 平均）が記録されている
- [ ] Q0 完了基準のチェック表が含まれる（達成 / 未達成を明示）
- [ ] 既知の問題が明記される（synth_01 BPM octave error など）
- [ ] 再生成手順（`scripts/validate_against_truth.py --json`）が記載
- [ ] CLAUDE.md docs 索引に `docs/validation.md` を追加（README は既記載）
- [ ] 既存テスト 129 件は引き続き pass（docs 変更のみ）

## Scope
- IN:
  - `docs/validation.md`（既存ファイルを更新）
  - `CLAUDE.md`（docs 索引に 1 行追加）
  - `.claude/briefs/Q0-5-validation-doc.md`（本 Brief）
  - `.claude/briefs/_index.md`（Q0-5 を In Progress に）
- OUT:
  - `src/svp_rpe/`
  - `scripts/`（Q0-4 で完成済の `validate_against_truth.py` に手を入れない）
  - `tests/`
  - `examples/`

## Allowed Dependencies
なし（docs のみ）

## Implementation Hints
- 既存 `docs/validation.md` は Q0 以前の PoC 全体警告 doc。完全に置換する
  のではなく、Q0-5 セクションを冒頭に追加して既存内容は後段に残す
- `validate_against_truth.py --json` をローカル実行して JSON 値を転記
- 表は markdown table。GitHub レンダリングと CommonMark 互換であること
- Q0 完了基準は `roadmap_goal1.md` の「Q0 完了基準: BPM 誤差 < 5 BPM,
  key 一致率 > 60%, snapshot CI green」を参照

## Required Outputs
- ブランチ名: `claude/q0-5-validation-doc`
- PR タイトル: `docs(validation): Q0-5 baseline measurements (5-song validation)`
- 期待する変更ファイル:
  - `docs/validation.md`
  - `CLAUDE.md`
  - `.claude/briefs/Q0-5-validation-doc.md`
  - `.claude/briefs/_index.md`

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文が docs PR として適切（Verification 節は「docs のみ」で省略可）
