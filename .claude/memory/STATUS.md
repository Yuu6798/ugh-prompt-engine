# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

現行アクティブスレッドは**意味層ジャンル語彙拡張（Genre Calibration）**で、2026-06-29 に
**3 ジャンルの実 grounding（real n=3）→ ルール適用（Phase D）まで一気に前進**した。経緯: 実音源で
意味層が管弦を `bass-music` 誤判定する限界が露呈 → Phase A config 化（#99）→ B-1 校正ハーネス/misfire
監査（#100/#101）→ B-2/B-3 で brightness split を `brilliance`(magnitude) へ（#102/#104）→ rock 追加で
3way banding（#105）→ Phase C で本物アンカー導入（#109/#110/#111, 各 n=1 の方向シグナル）。2026-06-29 に
ユーザーが mora で本物 lossless を順次調達し orchestral/rock/edm を **real n=3 へ増強（#112/#113/#114）**、
本物 vs 純 Suno の Δ で **generator bias の方向不変 2 指紋を分布で確定**：脱トーナル化（harmonic real↓:
orch -0.07/rock -0.19/edm -0.20＝最頑健）+ mid 削り（mid_ratio real↑: +0.36/+0.15/+0.05）。brilliance は
real≤suno だが量がジャンルで割れ**単一補正係数は不可**。**Phase D（#115）で rock/edm 判定を本物対応に
closeout**（rock 下限 brilliance 0.117→0.105 で grunge HSB 捕捉、**edm を新軸 sub_bass で rock と分離**＝
brilliance では重なる Strobe/Levels を sub_bass≥0.052 で識別、collect-all＋緩い intersection match なので
Suno 回帰ゼロ・test_semantic_layer 厳密等価を無改変で維持、分割は exhaustive）。ただし **orchestral は
実装見送り＝既知限界として確定**：本物管弦の中域主役（low<0.4, mid 0.59-0.87）域が thin dark synth と
特徴空間で分離不能（あの夏へ ≈ synth ガード fixture）で mid_ratio rule は Phase A 罠（synth 誤判定）を
再導入するため不採用、audit に mismatch として残し可視化維持。実装も Claude が担当（config+test+docs・
エンジン無改変）、Codex は PR レビュー（P2×4 対応）。標準コンテキスト: 目的2（R0–R5・R1-audio）・
Q1-5 Ph1/Ph2 は closeout 済。**この系統で実質残る本命は orchestral 対応のみ**＝特徴空間拡張（採譜層/
realness/学習器楽推定）が必須の研究寄り別タスク（音源不要）。他残は acoustic 4 ジャンル目・seed Drive 化・
Q1-5 Ph2 screen 再採取（いずれも外部律速 or 軽め）、K2（Suno 転移, P1）は controllability 別ライン。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化 |
| Genre Calib orchestral 対応 | orchestral 判定を本物対応へ（特徴空間拡張） | P2 | **Phase D(#115) で実装不能と確定した本系統の本命残**。本物管弦の中域主役(low<0.4, mid 0.59-0.87)域が thin dark synth と現行特徴量で分離不能(あの夏へ ≈ synth ガード fixture, Phase A 罠の再来)。`mid_ratio` rule は synth 誤判定を再導入するため不採用、audit に mismatch 残置・可視化中。**解決には特徴空間拡張が必須**: (a) 採譜層で旋律/和声から管弦↔synth 分離 / (b) realness・テクスチャセンサー新設 / (c) 学習器楽推定(learned_models_policy 隔離原則下)。どれで攻めるかの設計判断が次 Design Memo の主題。音源不要・研究寄り。`docs/genre_calibration_planning.md` Phase D |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #115 | feat(genre): rock/edm を本物アンカー対応へルール再設計、orchestral 限界を確定（Phase D・rock 下限 0.117→0.105 で grunge 捕捉・edm を新軸 sub_bass で rock と分離・collect-all で Suno 回帰ゼロ・test_semantic_layer 無改変・orchestral は synth と分離不能で見送り mismatch 維持・Codex P2=カバレッジホール塞ぎ） | 2026-06-29 | Genre Calib Phase D |
| #114 | feat(calibration): edm 本物アンカーを n=3 に増強＝3 ジャンル実 grounding 完了（横断 Δ で脱トーナル化+mid削りが方向一定を分布確定・brilliance は量がジャンルで割れ単一補正不可・Codex P2×2=brilliance 検定を平均シフト/delta 直接比較へ・rule 不変） | 2026-06-29 | Genre Calib Phase C |
| #113 | feat(calibration): rock 本物アンカーを n=3 に増強し方向不変指紋を 2 ジャンル目で確認（AC/DC・Queen 追加・脱トーナル化 -0.189 が rock で最強・sub_bass で edm と分離可能を示唆・rule 不変） | 2026-06-29 | Genre Calib Phase C |
| #112 | feat(calibration): orchestral 本物アンカーを n=3 に増強し補正係数を方向確定（Holst・久石譲 追加・mid削り +0.363 支配軸・low ゲートは Suno バイアスで本物に汎化せず audit 3/3 mismatch・Codex P2=純Suno baseline・rule 不変） | 2026-06-29 | Genre Calib Phase C |
| #111 | feat(calibration): cross-genre で Suno 指紋の一定性を検定＝単一補正係数は反証（本物 orch/rock/EDM 各1本 vs 純Suno・brilliance bias 符号反転=非一定・mid削り+脱トーナル化は方向一定・low ゲートは Suno 低域厚バイアス依存・Codex P2×2=audit 配線/純Suno baseline・rule 不変） | 2026-06-28 | Genre Calib Phase C |
