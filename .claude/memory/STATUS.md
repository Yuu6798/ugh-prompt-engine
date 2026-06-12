# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

制御トラック K0（grip 測定ハーネス, PR #61）と Composition audit C0+C3（RPEBundle→ObservedRPE アダプタ + `svprpe audit` 制御盤コマンド, PR #62）が稼働。audit は「合否を出す裁判官」ではなく「ツマミの効きを返す計器（制御盤）」として確立し、verdict/pass-fail/loss を一切出さず `_assert_no_outcome_keys` 回帰テストで固定（ci-check の合否ゲートとは別物として並存）。定性ターゲットと数値観測の突き合わせは `semantic_rules.yaml` の閾値をゲージ目盛り（帯境界）に読み替えて解決し、audit=1 サンプルの針位置 / K0 grip=針の動きやすさ、で同一センサー層を共有。次は C4（Score→prompt→生成→audit の E2E デモ）または K1（grip 代表マップを ~5 ツマミに拡張）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| C4 | Composition E2E デモ | P1 | Score → prompt →（生成）→ audit の一気通貫例。audit が #62 で稼働したので次の本命 |
| K1 | grip 代表マップ初版（~5 ツマミ） | P1 | bpm/key/brightness/active_rate/valley に拡張し tight/loose/dead スペクトルを張る。controllability_poc.md §5 |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P2 | Claude 代行で着手し中断状態 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #63 | feat(discipline): セッション終了プロトコルを semantic-ci-code から移植 | 2026-06-12 | INFRA |
| #62 | feat(audit): Composition audit control panel (C0+C3) | 2026-06-03 | C0+C3 |
| #61 | feat(control): K0 grip measurement harness | 2026-06-03 | K0 |
| #60 | docs: controllability_poc.md 制御トラック K 系列 新設 | 2026-06-02 | K-plan |
| #59 | feat(compose): svprpe compose + ExternalPromptAdapter | 2026-06-02 | C2 |
