# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

目的2（再現実証）のクリティカルパス前半が開通し、R2 の音源非依存スライスまで前進。
R0 往復ループ+三値診断（#75）/ R5 fixity 型+入場試験（#76）/ R1 再実行可能 corpus 箱
（#77）に続き、R2-2a「BPM 半折り（×2 オクターブ）検出」を完走（#80: `detect_bpm_octave_ambiguity`
= onset 自己相関の subdivision/primary 比を overlap 正規化、threshold 1.15、×2 方向のみ。
`bpm_octave_ambiguous` を transcribe trust gate に配線し曖昧 BPM を sensor-blind 化）。
2026-06-15 積み残しの P3 fix-up（センチネル集約 + C-gen send_form 整合, #79）も解消。
重要な実証: 推定器は明瞭に速い合成信号を halving せず、Suno 175→89 の真病理は合成再現不能
→ **R2-1（アトラクタ再現確認）/ CV-scale 実校正 / ÷2 方向 / R2-2a 検出器の実効力検証は
すべて R1-audio（人間が Suno 問題ケース音源を保存付き確保）待ち**が確定。R2 はこれ以上
Claude 単独では進められない。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| R1-audio | BPM 問題ケース音源の確保（人間トラック） | P1 | 89.1 アトラクタ / 175→89 半折りを示すテイクを保存付き新規生成（CC0 コミット or ハッシュ一致アーティファクト）。R1 箱(#77)の manifest に calibratable レコードとして追加。**単一クリティカルパス**: R2-1 / CV-scale 校正 / ÷2 方向 / R2-2a 実効力検証がすべてこれに依存。合成代替は調査で否定済（2026-06-16, 再試行しない）。roadmap_goal2.md R1 |
| R2 残部 | アトラクタ校正 + ÷2 方向 + 検出器実効力検証 | P1 | ×2 半折り検出は #80 で実装済（音源非依存）。残: R2-1 アトラクタ再現確認 / `BPM_CONFIDENCE_CV_SCALE` 実校正 / ÷2（reported too fast）方向 / R2-2a 検出器が実 Suno halving を flag するかの検証。**すべて R1-audio 待ち**。結論(bpm を再現対象に含む/明示除外)を完成定義 §4・R3 へ伝播。roadmap_goal2.md R2 |
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
