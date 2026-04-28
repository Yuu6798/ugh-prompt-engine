# Task Brief Index

Claude が起草し、User が Codex に渡す Task Brief を蓄積する。
フォーマット規約は [`AGENTS.md`](../../AGENTS.md) §1 を参照。

## 運用ポリシー

- Claude が新規 Brief を起草したら本 index に 1 行追加
- User が Codex に渡し PR が作成されたら status を `→ codex/<branch>` に更新
- PR がマージされたら status を `merged: PR #N` に更新
- マージ済の Brief は履歴として保持（削除しない）

## Pending（着手前）

| ID | Title | Phase | Created | Status |
|---|---|---|---|---|

## In Progress / Merged

| ID | Title | Branch | Status |
|---|---|---|---|
| Q0-2 | synth サンプル 5 曲の expected_output 生成 | `claude/q0-2-expected-output` | merged: PR #11（Plan B / Claude 代行） |
| Q0-3 | expected_output の snapshot テスト | `claude/q0-3-snapshot-test` | merged: PR #12（Plan B 継続） |
| Q0-4 | mir_eval 統合 + ground-truth 検証スクリプト | `claude/q0-4-mir-eval-validation` | merged: PR #13（Plan B 継続） |
| Q0-5 | validation.md 初版（5 曲ベースライン） | `claude/q0-5-validation-doc` | merged: PR #14（Plan B 継続） |
| Q1-1 | LUFS / true_peak (pyloudnorm) | `claude/q1-1-loudness-lufs` | merged: PR #15（Plan B 継続、Codex P1 stereo bug fix 込み） |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | `claude/q1-3-bpm-confidence` | merged: PR #16（Codex P2 round 1-3 含む、Plan B 継続） |
| Q1-4 | pro_baseline ジャンル別拡張 (Pro / Loud Pop / Acoustic / EDM) | `claude/q1-4-genre-baseline` | Claude 代行で実装中（Plan B 継続） |
