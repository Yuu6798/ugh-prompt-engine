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
| Q0-4 | mir_eval 統合 + ground-truth 検証スクリプト | `claude/q0-4-mir-eval-validation` | Claude 代行で実装中（Plan B 継続） |
