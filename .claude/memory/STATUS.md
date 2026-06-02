# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

Composition Score MVP（PoC 1+2）完成済み。本セッションで方向性を読み替え、意味層・物理層パラメータを「評価する値」ではなく「制御する値（効くツマミ）」と再定義し、制御トラック（K 系列）を新設（`docs/controllability_poc.md`, PR #60）。grip = A/B コントラスト効果量で「ツマミが出力に効くか」を測る PoC を計画。観測トラック（目的1, Q 系列）は「未完成→未検証」と位置づけ並走可と判断。次は K0 最小方法実証（MusicGen で bpm/brightness の grip 測定ハーネス、Design Memo は doc §7 同梱）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| K0 | grip 測定ハーネス最小実証 | P1 | 制御トラック起点。MusicGen で bpm/brightness 2 ツマミの grip を測る。Design Memo は controllability_poc.md §7 にペースト可形で同梱 |
| C0 | RPEBundle → ObservedRPE アダプタ | P1 | C3 audit の前提（0.5 日）。PhysicalRPE→metrics / SemanticRPE→signals |
| C3 | `svprpe audit` コマンド | P1 | Score + 音源 → ΔE 監査レポート。C0+C1+C2 が前提 |
| C4 | Composition E2E デモ | P2 | Score → prompt →（生成）→ audit の一気通貫例 |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P2 | Claude 代行で着手し中断状態 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #60 | docs: controllability_poc.md 制御トラック K 系列 新設 | 2026-06-02 | K-plan |
| #59 | feat(compose): svprpe compose + ExternalPromptAdapter | 2026-06-02 | C2 |
| #58 | docs: ワークフロー再反転（設計=Claude / 実装=Codex） | 2026-06-02 | FLOW-2 |
| #57 | feat(compose): CompositionScore schema + TargetSVP conversion | 2026-06-02 | C1 |
| #55 | feat: 開発フロー移植 + Composition PoC プランニング | 2026-05-26 | FLOW-1 |
