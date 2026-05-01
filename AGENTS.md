# AGENTS.md — Codex × Claude 連絡プロトコル

このリポジトリは **設計レビューと実装を分業** する。Codex（Brief 読解 / 設計メモ
起案 / PR レビュー担当）と Claude Code（実装 / PR 作成 / 指摘対応担当）が共有する
メッセージ・フォーマット規約を本ファイルで定める。役割分担・運用ポリシーの詳細は
[`CLAUDE.md`](CLAUDE.md) の Workflow 節を参照。

**2026-05-02 改訂**: Claude が設計、Codex が実装の旧フローから役割を反転した。
GitHub 移行タスク (PR #20–#23) で Claude が直接実装＋セルフレビュー＋指摘対応を
行った方が往復回数が減ることが確認できたため。

両エージェントとも作業開始時に本ファイルを読むこと。

---

## メッセージフロー

```
Task Brief
  │
  ▼
Codex ─[Design Memo]→ User ─[paste]→ Claude ─[実装 + PR]→ GitHub
                                                                │
                                                                ▼
                                       Claude ←[再レビュー]── Codex ←[PR URL]─ User
                                          │
                                          ▼
                                  指摘対応コミット ──→ Codex 再レビュー → User マージ
```

ループは User がトリガーする。Codex/Claude は各々のフォーマットで出力を出すだけで、
エージェント間で直接通信しない（Codex が PR レビューコメントを残す経路は GitHub
内なので User の橋渡し不要）。

---

## 1. Design Memo（Codex → Claude）

Codex が Task Brief を読んで Claude に渡す設計メモの固定フォーマット。
コピー&ペーストで Claude に渡せる単位にすること（タスク粒度は 0.5–2 日で
完結する範囲）。

````markdown
# Design Memo: <ID> — <短いタイトル>

## Phase
<roadmap_goal1.md の Q-ID または該当する設計参照>

## Goal
<1–2 文で「何を達成すれば完了か」>

## Acceptance Criteria
- [ ] 検証可能な条件 1
- [ ] 検証可能な条件 2

## Implementation Approach
<推奨実装方針、データフロー、既存パターン参照、API 設計>

## Risks
<実装で詰まりやすいポイント、後方互換破壊の可能性、性能リスク等>

## Test Strategy
- 単体テスト観点: <網羅すべきブランチ / エッジケース>
- 回帰テスト観点: <pin すべき契約 / 過去 defect の再発防止>
- 既存テストへの影響: <スナップショット更新の要否等>

## Scope
- IN: <変更してよいファイル / モジュール>
- OUT: <変更してはならないもの>

## Allowed Dependencies (任意)
<本タスクで pyproject.toml への追加を許可する依存。例: `mir_eval>=0.7`>
<記載がない場合、新規依存追加は escalation 対象>

## Required Outputs
- ブランチ名: `claude/<topic>`
- PR タイトル: <Conventional Commits 形式>
- 期待する変更ファイル: <列挙>

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文が Completion Summary 規約に準拠（CLAUDE.md の PR 本文必須記述参照）
````

---

## 2. Completion Summary（Claude → Codex / User）

Claude が PR を作成する際、PR 本文の冒頭を以下フォーマットで記述する。

````markdown
# Completion Summary: <Task ID>

## Phase
<Design Memo の Phase ID をそのまま転記>

## What Changed
- <高レベルの変更点 3–5 行>

## Acceptance Criteria Status
- [x] 条件 1 — <根拠 / 該当コミット SHA>
- [x] 条件 2 — <根拠>
- [ ] 条件 3 — <未達成の場合は理由>

## Tests
- 追加: <テスト名 / 件数>
- 実行結果: <pass / fail / skip 件数>

## Self-Review
<Claude 自身が実装後にチェックした観点 3–5 件。
 Codex レビューに先んじて defect を捕捉する目的>

## Files Changed
<git diff --stat 相当>

## Deviations from Memo
<Design Memo から逸脱した点。なければ "None">

## Open Questions / Deferred
<Codex / User が判断すべき事項、または次フェーズへの持ち越し>

## Review Focus
<Codex に重点的に見てほしい観点>
````

---

## 3. PR Review（Codex → Claude）

Codex は PR 本体の Completion Summary を読み、GitHub PR のレビュー機能で
コメントを残す。レビューコメントの粒度は inline コメント（行指定）優先、
全体総括が必要なら Review Summary を投稿。

レビュー時の必須観点:

1. **Acceptance Criteria 全項目の充足チェック**
2. **回帰テストが「契約全体を破る経路」を網羅しているか**（最も明らかな defect の
   再現だけでなく、同じ defect family の他の入力で再現できるか）
3. **Self-Review で見落とされた defect 探索**
4. **後方互換性 / 既存テストへの影響**
5. **依存追加 / philosophy 抵触の有無**

指摘の重要度 P1（致命）/ P2（重要）/ P3（minor）を明記し、Claude が対応優先順位を
判断できるようにする。

---

## 4. エスカレーション

Claude は以下のいずれかに該当したら **作業を停止し** Completion Summary 形式
（または draft PR の本文）で中断状態を報告すること:

1. Acceptance Criteria が技術的に達成不可能と判明した
2. Design Memo に書かれていない設計判断が必要になった
3. 既存テストが新規変更で壊れる（後方互換破壊の疑い）
4. **Design Memo の `Allowed Dependencies` に明示されていない**依存ライブラリの追加が
   必要になる（`pyproject.toml` の dependencies 変更を伴う場合。Memo で許可された
   依存の追加は escalation 対象外）
5. 哲学原則（決定論 / LLM 不使用 / API キー不要）への抵触の可能性

> **依存追加の運用補足**: roadmap_goal1.md の各フェーズ（Q0-4: `mir_eval`、
> Q1-1: `pyloudnorm`、Q2-1: `madmom`、Q3-1: `Demucs` 等）は新規依存を要する。
> Codex は Design Memo 発行時に `Allowed Dependencies` を必ず明示し、Claude は
> その範囲内であれば停止せず実装してよい。

---

## 5. ブランチ規約

- Claude が実装するブランチ: `claude/<topic>`（タスクごとに新規）
- Codex は基本ブランチを作らない（PR レビュー / Design Memo のみ）。例外的に
  Codex が小規模な fix-up を出す場合は `codex/<topic>`
- main への直接 push は CLAUDE.md の例外条項（`.claude/memory/` の運用ログ等）
  に該当する場合のみ

---

## 関連ドキュメント

- [`CLAUDE.md`](CLAUDE.md) — 役割分担・運用ポリシー全般、Workflow 節に概要
- [`docs/roadmap_goal1.md`](docs/roadmap_goal1.md) — 目的1（定量観測）の Codex 実装単位
- [`docs/roadmap.md`](docs/roadmap.md) — 段階軸（PoC / Pre-prototype）の俯瞰
