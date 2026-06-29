# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-06-29 に 2 本命を完走し、アクティブスレッドは **Genre Calibration（orchestral closeout）→
controllability（制御トラック K 系列）** へ重心が移った。(1) **Genre Calib Phase E（#116）** で長らく
「特徴空間拡張が必須・実装見送り」としていた **orchestral 対応を closeout**：mid-dominant 本物管弦は
thin dark synth とスペクトル(low/mid/harmonic/centroid)分離不能だが、既存の決定論フィールド
**onset_density** で ~14× 分離（repo synth 0.133–0.167 実測 vs 管弦 2.34–3.93）。rule=`low<0.4 ∧
mid≥0.5 ∧ onset_density≥1.0 → cinematic/orchestral`（collect-all で加算のみ・既存 match 無改変）。
Star Wars FLAC 再添付（sha256 一致）で onset 3.8667 実測し本物管弦 **n=3 完成・audit orchestral-real
3/3 match**。Codex P2（collect-all で他ジャンルと二重ラベル化）は「加算は設計仕様」と doc 訂正＋加算
挙動 test pin で対応（fallback化は Holst から正タグを消すため不採用）。(2) **controllability K2（#117）**
で K1（玩具＝決定論シンセ演奏者）が tight とした bpm/brightness が **本物 Suno でも tight 転移**を 16 曲
（2ツマミ×2水準×4反復）で確認（bpm d=1.61 / brightness d=0.86）。fixture-driven（音源 repo 外・
sha256 provenance・fixture→grip 決定論を snapshot 固定）。副産物の計器知見3点: bpm 素朴センサーは
prior アトラクタ(~125)で grip を圧縮するが tight 維持（真テンポ d≈6.4）・Suno は bright 得意/dark 苦手の
非対称・legacy 帯域比センサーの dead は素材依存（実音源で生き返る）。実装も Claude 単独（Codex 不在の
例外 fix-up 枠）。標準コンテキスト: 目的2（R0–R5・R1-audio）・Q1-5 Ph1/Ph2 は closeout 済。
Genre Calib の本命 orchestral は closeout 済で残は acoustic 4th genre・seed Drive 化（人手律速）、
controllability の次は **K3（直交性）** または K2 の他ツマミ拡張。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| K3 | 直交性行列（レイヤー独立性） | P2 | controllability の次手。ツマミ i が観測 j を動かさないか(bpm が centroid を動かす等)を N×N で測る。K2(#117)で対角 grip(bpm/brightness tight 転移)が立証されたので着手可能。または K2 を他ツマミ(key 等)へ拡張。controllability_poc.md §5 K3 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #117 | feat(control): K2 — 本物 Suno で bpm/brightness の grip 転移を確認（K1 玩具で tight だった 2 ツマミが Suno でも tight: bpm d=1.61 / brightness d=0.86・16 曲 fixture-driven・sha256 provenance・fixture→grip snapshot 固定・bpm 素朴センサーの prior アトラクタ圧縮/Suno は明得意暗苦手/センサー盲は素材依存の3知見） | 2026-06-29 | Controllability K2 |
| #116 | feat(genre): orchestral を onset_density 第二軸で捕捉（Phase E）＝Phase D 既知限界を closeout（mid-dominant 管弦と thin synth はスペクトル分離不能だが onset で ~14×分離・Star Wars 再添付 sha256 一致で n=3 完成・audit orchestral-real 3/3 match・collect-all で加算のみ・Codex P2=加算挙動を doc 訂正+test pin） | 2026-06-29 | Genre Calib Phase E |
| #115 | feat(genre): rock/edm を本物アンカー対応へルール再設計、orchestral 限界を確定（Phase D・rock 下限 0.117→0.105 で grunge 捕捉・edm を新軸 sub_bass で rock と分離・collect-all で Suno 回帰ゼロ・test_semantic_layer 無改変・orchestral は synth と分離不能で見送り mismatch 維持・Codex P2=カバレッジホール塞ぎ） | 2026-06-29 | Genre Calib Phase D |
| #114 | feat(calibration): edm 本物アンカーを n=3 に増強＝3 ジャンル実 grounding 完了（横断 Δ で脱トーナル化+mid削りが方向一定を分布確定・brilliance は量がジャンルで割れ単一補正不可・Codex P2×2=brilliance 検定を平均シフト/delta 直接比較へ・rule 不変） | 2026-06-29 | Genre Calib Phase C |
| #113 | feat(calibration): rock 本物アンカーを n=3 に増強し方向不変指紋を 2 ジャンル目で確認（AC/DC・Queen 追加・脱トーナル化 -0.189 が rock で最強・sub_bass で edm と分離可能を示唆・rule 不変） | 2026-06-29 | Genre Calib Phase C |
