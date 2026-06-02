# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

Composition Score MVP（PoC 1+2）完成。C1（CompositionScore スキーマ + TargetSVP 変換, PR #57）と C2（`svprpe compose` + ExternalPromptAdapter, PR #59）をマージし、作曲スコア YAML を機械可読モデルへロードし生成器向けプロンプトへ決定論変換できる状態に到達。次は監査トラック（C0: RPEBundle→ObservedRPE → C3: `svprpe audit`）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| C0 | RPEBundle → ObservedRPE アダプタ | P1 | C3 audit の前提（0.5 日）。PhysicalRPE→metrics / SemanticRPE→signals |
| C3 | `svprpe audit` コマンド | P1 | Score + 音源 → ΔE 監査レポート。C0+C1+C2 が前提 |
| C4 | Composition E2E デモ | P2 | Score → prompt →（生成）→ audit の一気通貫例 |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P2 | Claude 代行で着手し中断状態 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #59 | feat(compose): svprpe compose + ExternalPromptAdapter | 2026-06-02 | C2 |
| #58 | docs: ワークフロー再反転（設計=Claude / 実装=Codex） | 2026-06-02 | FLOW-2 |
| #57 | feat(compose): CompositionScore schema + TargetSVP conversion | 2026-06-02 | C1 |
| #55 | feat: 開発フロー移植 + Composition PoC プランニング | 2026-05-26 | FLOW-1 |
| #53 | feat(validation): Q4'-8 pseudo-label consensus harness | 2026-05-26 | Q4'-8 |
