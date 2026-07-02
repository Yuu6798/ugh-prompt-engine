---
name: new-brief
description: Draft a Design Memo (Claude→Codex handoff) for the ugh-prompt-engine repo, running the AGENTS.md §7 brief-drafting checklist as a pre-flight gate before emitting the AGENTS.md §1 Design Memo format. Use when the user asks to write/draft a new Design Memo, Task Brief, or implementation handoff, or to change an existing one.
---

# new-brief — Design Memo drafter with §7 pre-flight gate

Drafts a Design Memo in the `AGENTS.md §1` format, but only after running the
`AGENTS.md §7` brief-drafting checklist (semantic-ci-code での 20+ review
round から蒸留した項目). This skill is the **executor**; the policy sources
of truth are `AGENTS.md` §1 / §7 / §8. If they diverge from this file, they
win — fix this skill rather than acting on a stale copy.

Goal of the gate: front-load the checks that historically caused multi-round
review churn, so the memo lands in fewer rounds (review-round count is the
leading quality indicator — `AGENTS.md §8`).

## 0. Pre-flight reading

Before drafting, read:

- `AGENTS.md §1` (Design Memo format) + `§4` (escalation rules) + `§5`
  (branch rules) + `§7` (起草チェックリスト) + `§8` (経験外部化規律).
- `.claude/memory/STATUS.md` § Phase + § Next-Issue Queue for current
  priority, and `.claude/memory/_index.md` (直近 5 entries) + the 直近 3
  dated `YYYY-MM-DD.md` session logs. Skipping the memory log re-introduces
  the "過去 session の決定を知らずに再発明する" anti-pattern.
- The relevant roadmap/planning doc for the phase at hand —
  `docs/roadmap_goal1.md` (Q 系列), `docs/composition_poc_planning.md`
  (C 系列), `docs/controllability_poc.md` (K 系列), `docs/roadmap.md` (俯瞰).

If a required doc is stale or missing, surface that in the draft rather than
inventing context (documented recurring failure mode).

## 1. Pre-flight checklist (AGENTS.md §7 — run before writing spec)

1. **STATUS.md の Next-Issue Queue を確認** — 既に同等のタスクがないか。
2. **roadmap の Phase 位置を特定** — タスクがどのフェーズ (Q / C / K) に
   属するか明確にする。
3. **前提となる設計判断を列挙** — 未決定事項があれば memo 内で選択肢を提示
   する (必要なら AskUserQuestion で先に user に確定させる)。
4. **Acceptance Criteria を検証可能な形で書く** — 「〜を改善する」ではなく
   「〜が X を返す」「`pytest tests/test_x.py` が pass する」。
5. **Scope IN/OUT を明示** — 変更してよいファイルと変更禁止のファイルを列挙。
6. **依存追加の有無を確認** — 新規依存が必要なら Allowed Dependencies に
   明記 (記載なし = escalation 対象)。
7. **タスク粒度が 0.5–2 日か確認** — 大きすぎる場合はフェーズ分割。
8. **レビュー回数の予測** — 0 回が理想。3 回以上かかりそうなら memo の仕様
   が不足している。
9. **locked file と未検出フィールドを初手で縛る** — Scope OUT の「変更禁止
   ファイル」（特に共有スキーマ `compose/models.py` 等）は edge case 対応でも
   破ってよくないと明記する。あわせて計測値が未検出/低信頼になりうるフィールドの
   扱い（素直に欠落 / sentinel / schema は触らない）を memo 段階で確定する。
   未確定だと実装者が locked schema を広げて吸収し、自動レビュアーの連鎖 P2
   （PR #71 で 10+ ラウンド churn）を誘発する。詳細は `AGENTS.md §7` item 10。

### 1a. Schema grounding  ⚠️ highest-yield

Every module path / CLI command / config key / model field you name in the
memo MUST be verified to exist in the implementation by grep, not from
memory. Canonical surfaces to grep:

- `src/svp_rpe/` (module paths — the CLAUDE.md Architecture tree can lag)
- `src/svp_rpe/cli.py` (CLI subcommands + option names)
- `config/*.yaml` (config keys, thresholds, template names)
- `src/svp_rpe/rpe/models.py` / `svp/models.py` / `eval/models.py`
  (Pydantic field names — compile-pass / runtime-fail の定番混同源)

## 2. Emit the memo (AGENTS.md §1 format)

Use the Design Memo template verbatim from `AGENTS.md §1`:
`Phase / Goal / Acceptance Criteria / Implementation Approach / Risks /
Test Strategy / Scope / Allowed Dependencies / Required Outputs / Done When`.

Make every Acceptance Criterion **verifiable** (a command, a test, or a
grep-able assertion). Target task size ≈ 0.5–2 days. Branch name is
`codex/<topic>`; Done When requires `ruff check .` + `pytest -q --tb=short`
green and a Completion Summary PR body (`AGENTS.md §2`).

**Output the entire memo inside a single fenced code block** so the user can
copy-paste it verbatim into Codex (use an outer ```` ```` ```` fence when the
memo itself contains ``` code fences). The memo body is the deliverable; keep
prose outside the block to a minimum.

## 3. Closeout

Hand the memo to the user (it is paste-ready for Codex). Note any §1a grep
that surfaced a schema mismatch, any unresolved design decision the user must
settle, and any 5+ round dispute that should be externalized into docs/tests
per `AGENTS.md §8`.
