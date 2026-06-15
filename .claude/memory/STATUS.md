# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

目的2（再現実証）のクリティカルパス前半が開通。R0 往復保存性ループ（既存 Score→C4
決定論演奏→T1 採譜→三値診断 保存/ツマミ死/センサー盲, `svprpe roundtrip`, PR #75）、
R5 入場試験の制度化（optional `CompositionScore.fixity` 型 + `field_fixity` + AGENTS
Schema Admission 手順, PR #76）、R1 再実行可能 corpus の箱（manifest スキーマ/ローダ/
バッチ + `svprpe roundtrip-corpus` + 既存 4 Suno ケースの観測ログ化 + in-repo
calibratable synth レコード, PR #77）を設計→実装→レビュー→マージで完走。R0 レビュー指摘
（bpm 比較器 ±5 許容差 / grip 地図の package 同梱 + 等価性回帰テスト）は main へ反映済み。
判明: send_form=numeric_knob 限定比較で盲目センサーの一致を制御証拠にしない設計を確立、
calibratable synth で bpm 170→172 の保存を決定論実証。次は R2（bpm 89.1 アトラクタ校正 /
Q1-3 連動）で、前段の R1 箱は完成済み・人間トラックで BPM 問題ケース音源を保存付き確保
すれば calibratable レコードとして渡せる。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| R1-audio | BPM 問題ケース音源の確保（人間トラック） | P1 | 89.1 アトラクタ / 175→89 半折りを示すテイクを保存付き新規生成（CC0 コミット or ハッシュ一致アーティファクト）。R1 箱(#77)の manifest に calibratable レコードとして追加すれば R2 へ渡る。保存できた範囲が R2 校正対象スコープを規定。roadmap_goal2.md R1 |
| R2 / Q1-3 | BPM 89.1 アトラクタ校正 + 半折り検出 | P1 | R1 箱完成済み・前段は R1-audio のみ。CV 信頼度は pin 済(`BPM_CONFIDENCE_CV_SCALE`/`test_bpm_confidence.py`)。残は半折り(×2/÷2)検出 + アトラクタ校正。結論(bpm を再現対象に含む/明示除外)を完成定義 §4・R3 へ伝播。roadmap_goal2.md R2 |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| R4 | 作品同一性 — 事象レベル欄の往復 (stretch) | P2 | R0 後に並列着手可。旋律/コード進行センサー(learned_models_policy.md の optional extra 隔離)。§2.2 入場試験(R5 で制度化済)を事象欄に適用。roadmap_goal2.md R4 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #77 | feat(roundtrip): R1 再実行可能 corpus + manifest 箱 (svprpe roundtrip-corpus) | 2026-06-15 | R1 |
| #76 | feat(compose): R5 fixity 型 + 入場試験制度化 | 2026-06-15 | R5 |
| #75 | feat(roundtrip): R0 往復保存性 三値診断 + svprpe roundtrip | 2026-06-15 | R0/T2 |
| #74 | docs: roadmap_goal1 stale spec 整合 (Q1-2/Q1-4) | 2026-06-14 | goal1-align |
| #73 | docs: roadmap_goal2 起草 (R0–R5) + 索引同期 | 2026-06-14 | goal2 |
