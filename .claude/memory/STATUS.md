# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

目的2（再現実証）R2（BPM 校正）の検出器系列を 2026-06-17 に完走。R1-audio 調査（06-16 Session 2）で
確定した「高速曲の低 BPM = Suno 不忠実でなく抽出器 BPM halving、アトラクタ = tempo prior(~120)×BPM
グリッド選択」を受け、`detect_bpm_octave_ambiguity` を **近傍探索（1.4–2.2×）へ一般化**（#82 R2-2b
固定2×lag→近傍、#84 R2-2d で 3:2 subharmonic「117.45 アトラクタ」を包摂）し、**flag 時に reported
bpm を回復テンポへ補正**（#83 R2-2c、`max(candidates)`、transcribe trust gate は flag で sensor-blind
維持）。さらに手作業診断（高 prior で真テンポ回復）を計器化し、`compute_bpm(start_bpm=)` 追加 +
screener が「崩壊=抽出器 halving」か「生成器不忠実」かを自動弁別（#85）。post-hoc 検出器は部分緩和で、
principled fix（tempo prior 適応化）は高回帰の別タスク・OUT。R2 残りは ÷2 方向（beat-phase 要）と
`BPM_CONFIDENCE_CV_SCALE` 実校正（R1-audio calibratable 化＝licensing 待ち）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| R1-audio | 実 Suno 音源の corpus 登録 | P1 | 実 6 テイクを 2026-06-16 入手し `examples/roundtrip/screen_2026-06-16.yaml` に sha256 固定（observation_log、**PR #81 で main マージ済**）。残: **calibratable 化＝音源 repo 同梱は licensing 判断待ち**、R1 箱(#77) manifest への calibratable レコード追加。roadmap_goal2.md R1 |
| R2 ÷2方向 | reported-too-fast の過検出 | P1 | 検出器系列（近傍探索/補正/subharmonic/高prior回復）後の残課題。synth_01 真60→117 型の reported-too-fast は AC 振幅で分離不能・**beat-phase 解析要 → Design Memo 先行**の難タスク。roadmap_goal2.md R2 |
| R2 CV校正 | `BPM_CONFIDENCE_CV_SCALE` 実校正 | P2 | confidence の CV scale を実音源で校正する。R1-audio calibratable 化（音源 repo 同梱の licensing 判断、人間タスク）待ち。roadmap_goal2.md R2 |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| R4 | 作品同一性 — 事象レベル欄の往復 (stretch) | P2 | R0 後に並列着手可。旋律/コード進行センサー(learned_models_policy.md の optional extra 隔離)。§2.2 入場試験(R5 で制度化済)を事象欄に適用。roadmap_goal2.md R4 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #85 | feat(screen): 高 prior 回復チェックを corpus screener に内蔵（compute_bpm start_bpm） | 2026-06-17 | R2 |
| #84 | feat(rpe): BPM subharmonic (3:2) collapse を検出窓へ統合（117.45 アトラクタ） | 2026-06-17 | R2-2d |
| #83 | feat(rpe): octave-ambiguous 時に reported bpm を回復テンポへ補正 | 2026-06-17 | R2-2c |
| #82 | feat(rpe): BPM octave 検出を近傍探索化（グリッド量子化 halving 捕捉） | 2026-06-17 | R2-2b |
| #81 | feat(tooling): メタモルフィック計器 + R1 corpus スクリーナ（抽出器 BPM halving 発見） | 2026-06-16 | R1/R2 |
