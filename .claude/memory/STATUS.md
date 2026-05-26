# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

開発フロー移植完了。Composition PoC（物理層×意味層作曲）の C0–C4 計画を策定し、次セッションから C0/C1 並行着手可能。Q1-3（BPM 信頼度再設計）は中断状態のまま。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| C0 | RPEBundle → ObservedRPE アダプタ | P1 | `docs/composition_poc_planning.md` Phase C0 |
| C1 | Composition Score スキーマ設計 | P1 | `docs/composition_poc_planning.md` Phase C1 |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P2 | Claude 代行で実装中、中断状態 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #55 | feat: 開発フロー移植 + Composition PoC プランニング | 2026-05-26 | FLOW-1 |
| #53 | feat(validation): Q4'-8 pseudo-label consensus harness | 2026-05-26 | Q4'-8 |
| #54 | docs(validation): record 20-file real-audio smoke run | 2026-05 | Q0-5+ |
| #52 | feat(validation): model BPM octave ambiguity | 2026-05 | Q1-3 |
| #51 | real-audio measurement harness | 2026-05 | Q1 |
