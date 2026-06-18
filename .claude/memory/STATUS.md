# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

目的2（再現実証）R2（BPM 校正）の検出器系列を 2026-06-17 に完走。R1-audio 調査（06-16 Session 2）で
確定した「高速曲の低 BPM = Suno 不忠実でなく抽出器 BPM halving、アトラクタ = tempo prior(~120)×BPM
グリッド選択」を受け、`detect_bpm_octave_ambiguity` を **近傍探索（1.4–2.2×）へ一般化**（#82 R2-2b
固定2×lag→近傍、#84 R2-2d で 3:2 subharmonic「117.45 アトラクタ」を包摂）し、**flag 時に reported
bpm を回復テンポへ補正**（#83 R2-2c、`max(candidates)`、transcribe trust gate は flag で sensor-blind
維持）。さらに手作業診断（高 prior で真テンポ回復）を計器化し、`compute_bpm(start_bpm=)` 追加 +
screener が「崩壊=抽出器 halving」か「生成器不忠実」かを自動弁別（#85）。post-hoc 検出器は部分緩和で、
principled fix（tempo prior 適応化）は高回帰の別タスク・OUT。**÷2 方向（reported-too-fast）は
2026-06-18 に #86 R2-2e で決着** — AC 振幅 / beat-phase 交替 / 単独低 prior の 3 手法いずれも
extractor では分離不能（pad の synth_01 は交替最弱、正検出曲を巻き込む）と実測確定し、stated 真値を
持つ screener 限定の低 prior 診断（`LOW_PRIOR_START_BPM=50` で「抽出器 doubling vs 生成器不忠実」を
弁別）に絞った。負の結果は docs に外部化。R2 残りは **(1) R2 closeout 判断**（完成定義 §4: bpm を
R0/R3 再現対象に含めるか明示除外するか、根拠付き確定 → roadmap_goal2.md/R2-3 へ伝播）と
**(2) `BPM_CONFIDENCE_CV_SCALE` 実校正**（R1-audio calibratable 化＝licensing 待ち）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| R1-audio | 実 Suno 音源の corpus 登録 | P1 | 実 6 テイクを 2026-06-16 入手し `examples/roundtrip/screen_2026-06-16.yaml` に sha256 固定（observation_log、**PR #81 で main マージ済**）。残: **calibratable 化＝音源 repo 同梱は licensing 判断待ち**、R1 箱(#77) manifest への calibratable レコード追加。roadmap_goal2.md R1 |
| R2 closeout | bpm を再現対象に含むか明示除外か確定 | P1 | **#86 で ÷2 が screener 診断に決着した後の締め**。完成定義 §4 は「bpm 校正が inclusion 水準に届かなければ R0/R3 再現対象から明示除外・理由記録」を要求。faster-side は flag+補正の部分緩和・÷2 は extractor 不能（screener 診断のみ）の現状から結論を確定し roadmap_goal2.md §完成定義 §4 / R3 スコープ / R2-3（T0 per-field 校正メモ）へ伝播。Claude docs タスク、人間非依存。roadmap_goal2.md R2 |
| R2 CV校正 | `BPM_CONFIDENCE_CV_SCALE` 実校正 | P2 | confidence の CV scale を実音源で校正する。R1-audio calibratable 化（音源 repo 同梱の licensing 判断、人間タスク）待ち。roadmap_goal2.md R2 |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| R4 | 作品同一性 — 事象レベル欄の往復 (stretch) | P2 | R0 後に並列着手可。旋律/コード進行センサー(learned_models_policy.md の optional extra 隔離)。§2.2 入場試験(R5 で制度化済)を事象欄に適用。roadmap_goal2.md R4 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #86 | feat(screen): 低 prior で BPM doubling(÷2) を抽出器要因と弁別（screener 限定診断、extractor 不触） | 2026-06-18 | R2-2e |
| #85 | feat(screen): 高 prior 回復チェックを corpus screener に内蔵（compute_bpm start_bpm） | 2026-06-17 | R2 |
| #84 | feat(rpe): BPM subharmonic (3:2) collapse を検出窓へ統合（117.45 アトラクタ） | 2026-06-17 | R2-2d |
| #83 | feat(rpe): octave-ambiguous 時に reported bpm を回復テンポへ補正 | 2026-06-17 | R2-2c |
| #82 | feat(rpe): BPM octave 検出を近傍探索化（グリッド量子化 halving 捕捉） | 2026-06-17 | R2-2b |
| #81 | feat(tooling): メタモルフィック計器 + R1 corpus スクリーナ（抽出器 BPM halving 発見） | 2026-06-16 | R1/R2 |
