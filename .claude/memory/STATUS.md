# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

Composition Score プロダクトブリーフを上位文書として確立。既存 PoC 計画をブリーフ下流として刷新完了。MVP（C1: スキーマ + C2: compose CLI + ExternalPromptAdapter）の着手準備が完了。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| C1 | CompositionScore スキーマ + TargetSVP 変換 | P1 | MVP: ブリーフ §6 正規スキーマの実装 |
| C2 | `svprpe compose` + ExternalPromptAdapter | P1 | MVP: Score → Prompt 変換 + CLI |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P2 | Claude 代行で実装中、中断状態 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #55 | feat: 開発フロー移植 + Composition PoC プランニング | 2026-05-26 | FLOW-1 |
| #53 | feat(validation): Q4'-8 pseudo-label consensus harness | 2026-05-26 | Q4'-8 |
| #54 | docs(validation): record 20-file real-audio smoke run | 2026-05 | Q0-5+ |
| #52 | feat(validation): model BPM octave ambiguity | 2026-05 | Q1-3 |
| #51 | real-audio measurement harness | 2026-05 | Q1 |
