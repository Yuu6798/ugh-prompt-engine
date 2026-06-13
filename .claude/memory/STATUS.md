# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

採譜トラック（T 系列）の往路が開通。T0 `svprpe measure`（音源 1 本から物理 7 欄を
センサー/校正メモ付きで計測, PR #70）と T1 `svprpe transcribe`（loader-valid な draft
CompositionScore YAML, 計測欄は埋め・意味層は TODO センチネルで人間欄を明示, PR #71）を
設計→実装→レビュー→マージで完走。さらに実 Suno 生成 4 曲（Celtic 原曲/生成 + J-Pop
rock/EBM）で双方向性・制御性を即興検証し `docs/roundtrip_case_studies.md`（PR #72）に保存。
判明事項: 計器の有効帯域は西洋・長調/短調・4/4・明るい帯域（J-Pop で E major/4/4/bright 命中、
Celtic の旋法/6/8 は盲目）、物理固定・意味層を rock→EBM 差替で「全く別物」生成＝意味層 grip
有り（耳判定のみ・計器は盲目で T3 動機）、双方向性は実送出ノブ key/brightness のみ検証済み
（bpm は raw 89.1 アトラクタと交絡し判定不能で Q1-3 入力）。次は T2（往復保存性の in-repo 実証）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| T2 | 往復保存性の最小実証 | P1 | 既存Score→C4決定論演奏→T1採譜→元と比較。全工程決定論で in-repo 完結。実曲検証では送出ノブ key/brightness のみ検証済みなので検証欄を限定して引き継ぐ。score_centric_planning.md §3 |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチで確認。物理固定・意味差替で「別物」生成を確認済み(意味層 grip 有り・耳)だが n=1。manifest 様式は §8 未決。controllability_poc.md §5 |
| Q1-3 | BPM 信頼度の再設計 (CV-based) | P2 | 実曲入力: Suno 生成 3 曲が raw 89.1 アトラクタ + J-rock 175→89 半折り。校正には音源+真値+manifest の保存が前段で要る(roundtrip_case_studies.md §4) |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #72 | docs(test): Suno 往復/制御性テストケース結果を記録 | 2026-06-13 | T-validate |
| #71 | feat(transcribe): T1 draft Score 採譜 (svprpe transcribe) | 2026-06-13 | T1 |
| #70 | feat(transcribe): T0 per-field 計測ユーティリティ (svprpe measure) | 2026-06-13 | T0 |
| #67 | docs(policy): config 二重コピー同期の規約を Coding Conventions に追加 | 2026-06-12 | POLICY |
| #66 | refactor(sensors): brightness の正規センサーを spectral_centroid へ再設計 | 2026-06-12 | K1-followup |
