# AGENTS.md — Claude × Codex 連絡プロトコル

このリポジトリは **設計と実装を分業** する。Claude Code（設計担当）と Codex
（実装担当）が共有するメッセージ・フォーマット規約を本ファイルで定める。
役割分担・運用ポリシーの詳細は [`CLAUDE.md`](CLAUDE.md) の Workflow 節を参照。

両エージェントとも作業開始時に本ファイルを読むこと。

---

## メッセージフロー

```
Claude ─[Task Brief]→ User ─[paste]→ Codex
                                       │
                                       ▼
Claude ←[Completion Summary]─ User ←[PR URL]─ Codex
```

ループは User がトリガーする。Claude/Codex は各々のフォーマットで出力を出すだけで、
エージェント間で直接通信しない。

---

## 1. Task Brief（Claude → Codex）

Claude が新規タスクを発行するときの固定フォーマット。コピー&ペーストで Codex に
渡せる単位にすること（タスク粒度は 0.5–2 日で完結する範囲）。

````markdown
# Task Brief: <ID> — <短いタイトル>

## Phase
<roadmap_goal1.md の Q-ID または該当する設計参照>

## Goal
<1–2 文で「何を達成すれば完了か」>

## Acceptance Criteria
- [ ] 検証可能な条件 1
- [ ] 検証可能な条件 2

## Scope
- IN: <変更してよいファイル / モジュール>
- OUT: <変更してはならないもの>

## Implementation Hints (任意)
<推奨実装方針、参考リンク、既存パターン参照>

## Required Outputs
- ブランチ名: `codex/<topic>`
- PR タイトル: <Conventional Commits 形式>
- 期待する変更ファイル: <列挙>
- 必須テスト: <追加すべきテスト観点>

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文が Completion Summary 規約に準拠
````

---

## 2. Completion Summary（Codex → Claude）

Codex が PR を作成する際、PR 本文の冒頭を以下フォーマットで記述する。

````markdown
# Completion Summary: <Task ID>

## Phase
<Task Brief の Phase ID をそのまま転記>

## What Changed
- <高レベルの変更点 3–5 行>

## Acceptance Criteria Status
- [x] 条件 1 — <根拠 / 該当コミット SHA>
- [x] 条件 2 — <根拠>
- [ ] 条件 3 — <未達成の場合は理由>

## Tests
- 追加: <テスト名 / 件数>
- 実行結果: <pass / fail / skip 件数>

## Files Changed
<git diff --stat 相当>

## Deviations from Brief
<Brief から逸脱した点。なければ "None">

## Open Questions / Deferred
<次に Claude が判断すべき事項、または次フェーズへの持ち越し>

## Next Handoff
<Claude にレビューを依頼したい観点>
````

---

## 3. エスカレーション

Codex は以下のいずれかに該当したら **作業を停止し** Completion Summary 形式で中断状態を
報告すること（PR を draft で開く / 何もせず User に戻すどちらも可）:

1. Acceptance Criteria が技術的に達成不可能と判明した
2. Brief に書かれていない設計判断が必要になった
3. 既存テストが新規変更で壊れる（後方互換破壊の疑い）
4. 依存ライブラリの追加が必要（`pyproject.toml` の dependencies 変更を伴う）
5. 哲学原則（決定論 / LLM 不使用 / API キー不要）への抵触の可能性

---

## 4. ブランチ規約

- Claude が docs / 設計を更新するブランチ: `claude/<topic>` または既定の
  `claude/japanese-greeting-BF7XW`
- Codex が実装するブランチ: `codex/<topic>`（タスクごとに新規）
- main への直接 push は CLAUDE.md の例外条項に該当する場合のみ

---

## 関連ドキュメント

- [`CLAUDE.md`](CLAUDE.md) — 役割分担・運用ポリシー全般、Workflow 節に概要
- [`docs/roadmap_goal1.md`](docs/roadmap_goal1.md) — 目的1（定量観測）の Codex 実装単位
- [`docs/roadmap.md`](docs/roadmap.md) — 段階軸（PoC / Pre-prototype）の俯瞰
