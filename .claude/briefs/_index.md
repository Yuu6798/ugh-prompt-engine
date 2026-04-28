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
| Q0-3 | expected_output の snapshot テスト | roadmap_goal1.md Q0-3 | 2026-04-28 | pending（Q0-2 マージ後着手） |

## In Progress / Merged

| ID | Title | Branch | Status |
|---|---|---|---|
| Q0-2 | synth サンプル 5 曲の expected_output 生成 | `claude/q0-2-expected-output` | Claude 代行で実装中（User 不在のため Plan B 適用） |
