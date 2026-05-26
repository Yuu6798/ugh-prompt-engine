# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

Goal 1（定量観測完成）の Q0–Q1 を完了し、Q1-3（BPM 信頼度再設計）が実装途中で中断。
Q4' 系（Learned Output Validation）は PR #33–#35 でマージ済み。
音楽ドメインの野心的目標「物理層×意味層レイヤー作曲」に向けた開発フロー整備を開始。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P1 | Claude 代行で実装中、中断状態 |
| FLOW-1 | 開発フロー移植（semantic-ci-code → prompt-engine） | P1 | 本セッションで対応中 |
| MUSIC-COMP-1 | 物理層×意味層作曲スタイル — 宣言フォーマット設計 | P2 | 新規構想、FLOW-1 の後に着手 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #54 | docs(validation): record 20-file real-audio smoke run | 2026-05 | Q0-5+ |
| #52 | feat(validation): model BPM octave ambiguity | 2026-05 | Q1-3 |
| #51 | real-audio measurement harness | 2026-05 | Q1 |
| #35 | fix: 二重デコード解消 | 2026-05-03 | Q4'-6 |
| #34 | refactor: skipped 集計分離・L-1〜L-4 | 2026-05-03 | Q4'-6 |
