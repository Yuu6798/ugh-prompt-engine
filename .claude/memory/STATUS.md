# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

目的2（再現実証）R2 のブロックだった R1-audio を 2026-06-16 Session 2 で初入手（実 Suno
6 テイク、prompt 付き）。実音源調査が決定的知見を生んだ: 高速曲（>~160）の低 BPM 検出
（89/117.5）は **Suno 不忠実でなく抽出器 BPM halving**（`start_bpm=180` で全件真テンポ 172.3 を
回復＝真テンポは音源に在る）。「89.1/117.45 アトラクタ」の正体は既定 tempo prior(~120)×BPM
グリッド選択で、R2-2a（#80 の ×2 検出）は固定 2× lag のためグリッド量子化された 1.93× 実パルスを
外す具体バグを持つ。修正（近傍探索一般化）は R2-2b Design Memo として起案済（`docs/briefs/`、
Codex 実装待ち）。計測ツール 2 本（`metamorphic_probe`/`screen_corpus`）と R1 screen データ
（`examples/roundtrip/screen_2026-06-16.yaml`）は `claude/testing-design-insights-l5l0rz`
ブランチ（未マージ・PR 化判断が User 待ち）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| R1-audio | 実 Suno 音源の corpus 登録 | P1 | 実 6 テイクを 2026-06-16 入手し `examples/roundtrip/screen_2026-06-16.yaml` に sha256 固定（observation_log）。残: **calibratable 化＝音源 repo 同梱は licensing 判断待ち**、R1 箱(#77) manifest への calibratable レコード追加。screen は claude/testing-design-insights ブランチ（未マージ）。roadmap_goal2.md R1 |
| R2-2b | BPM octave 検出の近傍探索一般化 | P1 | `detect_bpm_octave_ambiguity` を固定 2× lag→1.8–2.2× 近傍探索へ。グリッド量子化 halving（176→89.1, 真 172.3=1.93×）を flag。Option A（flag のみ・値不変・低リスク）で Design Memo 済（`docs/briefs/R2-2b_bpm_octave_neighborhood.md`）。**Codex 実装待ち**。roadmap_goal2.md R2 |
| R2 残部 | アトラクタ校正 + ÷2 方向 + 値補正判断 | P1 | 実音源調査(Session 2)で **崩壊=抽出器 halving**（真テンポは音源に在り `start_bpm=180` で回復）・アトラクタ=prior×グリッド と判明。残: Option B（reported bpm 値補正 R2-2c）/ ÷2 方向 / `BPM_CONFIDENCE_CV_SCALE` 実校正 / `screen_corpus` に高 prior 回復チェック内蔵。結論を完成定義 §4・R3 へ伝播。roadmap_goal2.md R2 |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| R4 | 作品同一性 — 事象レベル欄の往復 (stretch) | P2 | R0 後に並列着手可。旋律/コード進行センサー(learned_models_policy.md の optional extra 隔離)。§2.2 入場試験(R5 で制度化済)を事象欄に適用。roadmap_goal2.md R4 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #80 | feat(rpe): BPM 半折り（×2 オクターブ）検出 + trust gate 配線 | 2026-06-16 | R2-2a |
| #79 | refactor+fix: TODO センチネル集約 + C-gen send_form 整合 | 2026-06-16 | P3-followup |
| #77 | feat(roundtrip): R1 再実行可能 corpus + manifest 箱 (svprpe roundtrip-corpus) | 2026-06-15 | R1 |
| #76 | feat(compose): R5 fixity 型 + 入場試験制度化 | 2026-06-15 | R5 |
| #75 | feat(roundtrip): R0 往復保存性 三値診断 + svprpe roundtrip | 2026-06-15 | R0/T2 |
