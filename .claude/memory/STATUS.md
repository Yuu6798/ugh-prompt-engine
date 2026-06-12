# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

Composition E2E（C4, PR #64）と grip 代表マップ初版（K1, PR #65）が決定論パスで完走し、PoC 5 の三層往復（Score の構造意図 → 物理演奏 → 意味層の再観測）を決定論的シンセ演奏者で実証。K1 で「dead には 2 種類ある」（ツマミ死 = 生成側が読まないフィールド / センサー盲 = 観測帯の不一致）を発見し、後者の実例だった brightness は正規センサーを `spectral_centroid` へ再設計（dark ≤ 1200 / bright ≥ 2500、PR #66）して全消費者（semantic ラベル / GRV アンカー / audit 針 / semantic_ci compare / domain profile / grip 地図）を単一情報源に統一済み。config の repo/packaged 二重コピーは同期テストで恒久ガード。次は K2（Suno/Udio 転移検証 — 手動生成バッチが必要、centroid センサーと grip 地図の前提は整備済み）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチで確認。manifest 様式は §8 未決。controllability_poc.md §5 |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P2 | Claude 代行で着手し中断状態。K1 で観測した「高 BPM 指定の検出が低めに出る癖」と合流しうる |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #66 | refactor(sensors): brightness の正規センサーを spectral_centroid へ再設計 | 2026-06-12 | K1-followup |
| #65 | feat(control): K1 grip 代表マップ初版 — 5 ツマミ + 補助センサー | 2026-06-12 | K1 |
| #64 | feat(compose): C4 Composition E2E デモ — 決定論的シンセ演奏者によるフルループ | 2026-06-12 | C4 |
| #63 | feat(discipline): セッション終了プロトコルを semantic-ci-code から移植 | 2026-06-12 | INFRA |
| #62 | feat(audit): Composition audit control panel (C0+C3) | 2026-06-03 | C0+C3 |
