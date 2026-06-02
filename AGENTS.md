# AGENTS.md — Codex × Claude 連絡プロトコル

このリポジトリは **設計レビューと実装を分業** する。Claude Code（Brief 読解 / 設計メモ
起案 / PR レビュー担当）と Codex（実装 / PR 作成 / 指摘対応担当）が共有する
メッセージ・フォーマット規約を本ファイルで定める。役割分担・運用ポリシーの詳細は
[`CLAUDE.md`](CLAUDE.md) の Workflow 節を参照。

**2026-06-02 改訂**: ワークフローを **Claude Code が設計、Codex が実装** に戻す。
GitHub 移行後の通常開発では、Claude Code が仕様整理・設計レビューを担当し、Codex が
ローカル実装・検証・PR 作成・指摘対応を担当する方針にする。

両エージェントとも作業開始時に本ファイルを読むこと。

---

## メッセージフロー

```
Task Brief
  │
  ▼
Claude Code ─[Design Memo]→ User ─[paste]→ Codex ─[実装 + PR]→ GitHub
                                                                │
                                                                ▼
                                      Codex ←[再レビュー]── Claude Code ←[PR URL]─ User
                                          │
                                          ▼
                              指摘対応コミット ──→ Claude Code 再レビュー → User マージ
```

ループは User がトリガーする。Codex/Claude は各々のフォーマットで出力を出すだけで、
エージェント間で直接通信しない（Claude Code が PR レビューコメントを残す経路は GitHub
内なので User の橋渡し不要）。

---

## 1. Design Memo（Claude Code → Codex）

Claude Code が Task Brief を読んで Codex に渡す設計メモの固定フォーマット。
コピー&ペーストで Codex に渡せる単位にすること（タスク粒度は 0.5–2 日で
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
- ブランチ名: `codex/<topic>`
- PR タイトル: <Conventional Commits 形式>
- 期待する変更ファイル: <列挙>

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文が Completion Summary 規約に準拠（CLAUDE.md の PR 本文必須記述参照）
````

---

## 2. Completion Summary（Codex → Claude Code / User）

Codex が PR を作成する際、PR 本文の冒頭を以下フォーマットで記述する。

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
<Codex 自身が実装後にチェックした観点 3–5 件。
 Claude Code レビューに先んじて defect を捕捉する目的>

## Files Changed
<git diff --stat 相当>

## Deviations from Memo
<Design Memo から逸脱した点。なければ "None">

## Open Questions / Deferred
<Claude Code / User が判断すべき事項、または次フェーズへの持ち越し>

## Review Focus
<Claude Code に重点的に見てほしい観点>
````

---

## 3. PR Review（Claude Code → Codex）

Claude Code は PR 本体の Completion Summary を読み、GitHub PR のレビュー機能で
コメントを残す。レビューコメントの粒度は inline コメント（行指定）優先、
全体総括が必要なら Review Summary を投稿。

レビュー時の必須観点:

1. **Acceptance Criteria 全項目の充足チェック**
2. **回帰テストが「契約全体を破る経路」を網羅しているか**（最も明らかな defect の
   再現だけでなく、同じ defect family の他の入力で再現できるか）
3. **Self-Review で見落とされた defect 探索**
4. **後方互換性 / 既存テストへの影響**
5. **依存追加 / philosophy 抵触の有無**

指摘の重要度 P1（致命）/ P2（重要）/ P3（minor）を明記し、Codex が対応優先順位を
判断できるようにする。

---

## 4. エスカレーション

Codex は以下のいずれかに該当したら **作業を停止し** Completion Summary 形式
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
> Claude Code は Design Memo 発行時に `Allowed Dependencies` を必ず明示し、Codex は
> その範囲内であれば停止せず実装してよい。

---

## 5. ブランチ規約

- Codex が実装するブランチ: `codex/<topic>`（タスクごとに新規）
- Claude Code は基本ブランチを作らない（PR レビュー / Design Memo のみ）。例外的に
  Claude Code が小規模な fix-up を出す場合は `claude/<topic>`
- main への直接 push は CLAUDE.md の例外条項（`.claude/memory/` の運用ログ等）
  に該当する場合のみ

---

## 6. Tiered Attention Budget（注意予算の階層化）

AI エージェントのコンテキストウィンドウは有限資源。セッション開始時に全ドキュメントを
読み込むのではなく、タスクの段階に応じて読む対象を階層化する。

### Tier A — 常時必須（目標: 800 行以内）

セッション開始時に必ず読む。どのタスクでも必要な普遍的コンテキスト。

- `CLAUDE.md`
- `.claude/memory/STATUS.md` — Phase セクション + next-issue queue
- `.claude/memory/_index.md` — 直近 5 件のエントリ
- `AGENTS.md` §1–§5

### Tier B — Brief 起草前に読む（目標: 300 行以内）

新しい Task Brief / Design Memo を起草するタスクでのみ読む。

- `AGENTS.md` §7 Brief 起草チェックリスト
- 該当する `docs/` の planning / roadmap セクション
- `.claude/briefs/_index.md`

### Tier C — 特定タスクのオンデマンド

実装や深掘り調査で必要に応じて読む。

- `docs/` の個別設計ドキュメント
- 直近のセッションログ（`.claude/memory/YYYY-MM-DD.md`）
- `docs/roadmap_goal1.md` の特定フェーズ詳細

### Tier D — デバッグ・考古学

問題調査や過去の設計判断の掘り起こしでのみ読む。

- `.claude/memory/archive/` 配下のアーカイブ済みログ
- 古いセッションログ
- `archive/STATUS_MERGED_LOG.md`

---

## 7. Brief 起草チェックリスト

Design Memo / Task Brief を起草する前に以下を確認する。
semantic-ci-code での 20 ラウンド以上の経験から蒸留した項目。

1. **STATUS.md の next-issue queue を確認** — 既に同等のタスクがないか
2. **roadmap の Phase 位置を特定** — タスクがどのフェーズに属するか明確にする
3. **前提となる設計判断を列挙** — 未決定事項があれば Brief 内で選択肢を提示する
4. **Acceptance Criteria を検証可能な形で書く** — 「〜を改善する」ではなく「〜が X を返す」
5. **Scope IN/OUT を明示** — 変更してよいファイルと変更禁止のファイルを列挙
6. **依存追加の有無を確認** — 新規依存が必要なら Allowed Dependencies に明記
7. **タスク粒度が 0.5–2 日か確認** — 大きすぎる場合はフェーズ分割
8. **レビュー回数の予測** — 0 回が理想。3 回以上かかりそうなら Brief の仕様が不足している

---

## 8. 経験外部化規律（Experience Externalization Discipline）

AI エージェントはセッション間で記憶を持たない。したがって、セッション中に得られた
知見は必ず明示的な成果物（ドキュメント、テスト、チェックリスト）に変換する。

### 原則

- **暗黙知は存在しない** — 言語化されていない知見は次のセッションで消失する
- **レビュー回数は品質指標** — 0 回 = Brief の仕様が十分、10+ 回 = 仕様欠落の兆候
- **パターンの再発は外部化の失敗** — 同じ問題が 2 回起きたら、防止策をドキュメント or テストに変換

### 外部化先の選択基準

| 知見の種類 | 外部化先 |
|---|---|
| 開発プロセス上の教訓 | `CLAUDE.md` or `AGENTS.md` に追記 |
| 特定機能の設計判断 | `docs/<topic>.md` |
| 再現可能な不変条件 | `tests/` に自動テストとして追加 |
| セッション固有の文脈 | `.claude/memory/YYYY-MM-DD.md` |
| 反復的な手順 | チェックリストとして `AGENTS.md` に追加 |

---

## 関連ドキュメント

- [`CLAUDE.md`](CLAUDE.md) — 役割分担・運用ポリシー全般、Workflow 節に概要
- [`.claude/memory/STATUS.md`](.claude/memory/STATUS.md) — プロジェクト状況、Phase、next-issue queue
- [`docs/roadmap_goal1.md`](docs/roadmap_goal1.md) — 目的1（定量観測）の Codex 実装単位
- [`docs/roadmap.md`](docs/roadmap.md) — 段階軸（PoC / Pre-prototype）の俯瞰
