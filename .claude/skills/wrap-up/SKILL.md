---
name: wrap-up
description: Persist a session-end reflection into .claude/memory and run the memory-hygiene sweep for the ugh-prompt-engine repo. Use when the user signals the session is ending — e.g. 「今日はここまで」「今日は終わり」「セッション終了」「また明日」「お疲れ様」「done for today」「that's all」 — or runs /wrap-up manually.
---

# wrap-up — session memory persistence + hygiene sweep

This skill is the **source of truth** for the end-of-session procedure. The
full 8-step list, archive-TTL policy, summary layout, and anti-pattern list
used to live inline in `CLAUDE.md` § Session Memory; they were moved here so
the always-loaded policy doc stays lean and the procedure is only loaded on
demand. `CLAUDE.md` keeps a short pointer to this file. If the two ever
diverge, **this skill wins** — fix `CLAUDE.md`'s pointer, do not re-inline
the procedure there.

Run it confirmation-free when a trigger phrase fires (that is the documented
contract), but still surface what you changed at the end.

## Why this is a skill, not prose

The procedure has a hard ordering and a hard gate that散文では構造的に
保証されない:

- **step 4 (Next-Issue Queue sweep) must run before step 5 (Recently Merged
  compaction)** — a single pass that moves completed entries into Recently
  Merged *then* re-evaluates the 5-cap.
- **step 8 (`python -m pytest tests/discipline/`) must run before any direct
  push** — the `.claude/memory/` main-push exception is post-hoc-only, so a
  discipline violation turns main red directly instead of being blocked by
  PR CI.

Walk the steps in order. Do not skip the gates.

## Procedure

### 1. Save the reflection
Write the session reflection to `.claude/memory/YYYY-MM-DD.md` (today =
the `currentDate` from context). If the file already exists for today,
append a new `## Session N` section instead of overwriting.

Use the conventional section layout (see the Summary layout appendix below):
**Context / Design Decisions / Artifacts / Progress / Handoff**. Progress is a
required line — answered questions / evidence / debt terminal status, never
"completed" / "all merged" / "rounds exhausted" (RUN10-CAL v1.2 §W0.4).

**Fable 直接実行監査**（CLAUDE.md § Advisor Strategy 運用細則の事後監査）:
Fable 稼働セッションでは、Artifacts か Handoff に「Fable が直接実行した
手を動かす作業」（マイクロ操作例外＝1–2 ツールコールを超えた実装・実行・
検証・調査）を 1–2 行で明記する。委譲すべきだった項目はそのまま次
セッションへの改善引き継ぎになる。該当なしならその旨 1 語で足りる。

### 2. Append the index entry
Add **one 1–2 line bullet** to `.claude/memory/_index.md` using the existing
list form: `- YYYY-MM-DD: <1行成果>。[詳細](YYYY-MM-DD.md)` (同日複数は
`(Session N)` を日付に付記). Keep each entry ≤ 500 chars — this is enforced
by `tests/discipline/test_index_md_entry_compactness.py`. Do NOT essay-ify
the entry; full narrative lives in the dated file.

### 3. Archive dated logs older than 30 days
Move any `YYYY-MM-DD.md` older than 30 days into
`.claude/memory/archive/YYYY-MM/`, preserving the original text verbatim
(zero information loss). Rewrite its `_index.md` bullet to a 1-line summary +
archive path. Update `.claude/memory/archive/INDEX.md`.

### 4. Sweep `STATUS.md` Next-Issue Queue  ⚠️ before step 5
In `.claude/memory/STATUS.md` § `## Next-Issue Queue`, remove any item that
has been **completed/merged**, converting it into a new row under
`## Recently Merged`. Enforced by
`tests/discipline/test_status_md_next_queue_no_completed.py`.

### 5. Compact `STATUS.md` Recently Merged
Keep only the most recent **5** rows inline under `## Recently Merged`.
Move the overflow (oldest first) to the end of
`.claude/memory/archive/STATUS_MERGED_LOG.md`, verbatim.

### 6. Check `STATUS.md ## Phase` is a single paragraph
`## Phase` must be exactly **one** canonical paragraph. If you added a new
paragraph, delete the old one — do not leave both. Enforced by
`tests/discipline/test_status_md_phase_single_paragraph.py`.

### 7. Externalize lessons / propose policy updates
If any spec/ambiguity took **5+ rounds** of review or 壁打ち this session,
confirm its resolution is encoded in docs/tests per `AGENTS.md §8`
(経験外部化規律). If a `CLAUDE.md` / `AGENTS.md` update is warranted,
propose it to the user — do not silently edit policy docs.

### 8. Verify discipline tests, then push  ⚠️ gate
Run:

```bash
python -m pytest tests/discipline/ -q
```

Use `python -m pytest`, not bare `pytest`, so the invocation is pinned to
the active environment's pytest. (This repo's dev extras do not include
`pytest-cov`, so do NOT pass `--no-cov` — it would be rejected as an
unrecognized argument.)

All tests in `tests/discipline/` MUST pass before pushing. A failure means
drift remains from steps 4–6 (or `CLAUDE.md` grew past its 400-line cap, see
`test_claude_md_line_cap.py`) — fix the offending file and re-run; do NOT
push red. Only `.claude/memory/` changes may go direct to main (the memory
exception); everything else still needs a feature branch + PR.

## Closeout
After pushing, give the user a short summary: which memory files changed,
any archive moves, the discipline-test result, and any 5+ round item you
externalized or are proposing to encode.

---

## Appendix A — Archive policy (compaction TTL)

`.claude/memory/` artifacts are archived on these TTLs (verbatim, zero info
loss). Archive moves are allowed direct-to-main under the memory exception.

| Artifact | TTL | 移送先 | 移送後の本体 source |
|---|---|---|---|
| dated session log `YYYY-MM-DD.md` | 30 日 | `archive/YYYY-MM/YYYY-MM-DD.md` | 原文保存 (情報損失ゼロ) |
| `_index.md` の対応 entry | 同上 | inline → 1 行 summary + archive path 追記 | 詳細は archive file 経由で参照可 |
| `STATUS.md ## Recently Merged` row | 直近 5 を超えた時点 | `archive/STATUS_MERGED_LOG.md` 末尾 | 原文保存 |
| `STATUS.md Next-Issue Queue` の完了 item | merge と同時 | `## Recently Merged` の新 row に変換 | 完了宣言として保存 |
| `STATUS.md ## Phase` paragraph | 上書き時 | (保存しない、 1 paragraph 厳守) | 旧 phase の history は dated session log / `_index.md` に分散保存 |

Archive infrastructure: `.claude/memory/archive/` directory + `archive/INDEX.md`.

## Appendix B — Summary layout (慣例フォーマット)

Compose the dated reflection with these sections:

```markdown
## Session Summary — YYYY-MM-DD [Session N]

### Context
<1–2 段落: セッションで扱ったトピック>

### Design Decisions
<なぜその選択をしたか>

### Artifacts
<マージした PR / 追加したファイル>

### Progress (answered questions / evidence / debt terminal status)
<答えた問い / その証拠 / 負債の terminal status を 1 行ずつ。
「完走」「全マージ」「巡数消化」は進捗として記載しない
（RUN10-CAL v1.2 §W0.4、2026-09-06 制定）>

### Handoff
<次のセッションへの引き継ぎ事項>
```

## Appendix C — Anti-patterns (`AGENTS.md §8` の対応原則参照)

- `_index.md` entry を essay 化させる (entry ≤ 500 chars は
  `test_index_md_entry_compactness.py` で enforce)。
- 完了済み item を `Next-Issue Queue` に残置 (次セッションで誤った優先順位
  判断を招く。 PR merge 直後の即時 sweep が必須)。
- `## Phase` に新 paragraph を追加するが旧 paragraph を残置 (現在の状況が
  不明確になる drift)。
- archive 移送を「後で」と先送り (30 日経過 dated entry は wrap-up 時に必ず移送)。
- discipline test の pre-push verification を skip して memory を直 main push
  する (step 8 違反): post-hoc 検出のみのため main red を直接引き起こす。
- **`CLAUDE.md` を 400 行超に肥大させる** (always-loaded policy doc の固定費 +
  指示遵守劣化。 reference detail は `docs/` / skill に逃がしポインタ化する。
  `test_claude_md_line_cap.py` で enforce)。
- **進捗を「完走」「全マージ」「巡数消化」で記載する** (答えた問い / 証拠 / 負債の
  terminal status で書く。RUN10-CAL v1.2 §W0.4、2026-09-06 制定)。
