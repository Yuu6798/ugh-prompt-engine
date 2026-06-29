# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-06-29 (Session 3) に方向性壁打ち→ロードマップ反映 PR を完走し、アクティブスレッドは
**AI 演奏者のための楽譜マージロードマップ（#118）の実装着手** へ移った。壁打ちで **「楽譜（実用物）
が本命で測定器ではない」** を確認し、コンパイル脚は既存（`compose/prompt_renderer.py` の
`ExternalPromptAdapter`＝楽譜→外部生成器プロンプト、`perform/performer.py` の決定論 performer＝
楽譜→音声）と判明 → ロードマップに **PR1.5（control_profile-aware compile）を PR2 の前に新設**
（PR #119 docs merged）。確定した改訂方針: ①本命は楽譜であって測定器でない ②決定論=物理層保証/
非決定論=意味層助言（CLAP 等 API キー不要・ローカル OSS 学習センサー限定・API ベース LLM は
`CLAUDE.md` の LLM-free 契約で out of scope、採用には契約見直し escalation）③多生成器は Suno
ルート確立後だが control_profile の生成器キー構造は今から保ち PR1.5 で backend seam を名前で引く。
#119 は Codex P2 を **13 件・10 コミット**で全対応（内部整合・実装可能性の precondition を詰めた
結果 PR1.5 spec が緻密化＝フィールド粒度 drop accounting・疎 profile 許容・backend selector・
priority エイリアス・実フィールド名・依存関係）、発散の構造因＝実装契約のロードマップ前倒しを
「厳密契約は PR1.5 Design Memo で確定」のスコープ境界注記で収束。標準コンテキスト: Genre Calib
本命 orchestral / controllability K2 / 目的2(R0–R5・R1-audio) / Q1-5 Ph1/Ph2 は closeout 済。
**次の本命は PR1（control_profile スキーマ・依存ゼロ・K2 実測が初期データ）着手 → Design Memo
展開**、続いて PR1.5（コンパイルループを閉じる）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| 楽譜マージ PR1 | control_profile スキーマ（楽譜が効くチャネルを知る）| P1 | **現アクティブプランの起点**。`docs/ai_performer_score_roadmap.md`（#118 起草・#119 で PR1.5 新設し PR 4 本構成へ）の第1歩。`CompositionScore` に optional `control_profile`（生成器→フィールド→grip_class）を追加。**fixity からは未知キー fail-fast のみ流用し網羅必須は引き継がない＝疎な profile を許容**（#119 で確定。K2 由来の suno は bpm/brightness のみ）。K2(#117) 実測を初期データに。依存ゼロ・即着手可。着手時に Design Memo 展開 |
| 楽譜マージ PR1.5 | control_profile-aware compile（コンパイルループを閉じる）| P1 | **#119 で新設・PR2 の前**。既存 `ExternalPromptAdapter` に control_profile を配線し楽譜→演奏ループを Suno で閉じる＝実用物の核。前提移行（フィールド粒度 segment/drop accounting・backend selector external→suno・priority エイリアス）を伴う＝wiring-only でない。厳密な実装契約は着手時 Design Memo で確定。依存=PR1 |
| 楽譜マージ PR2/PR3 | 楽譜準拠テスト+CLAP(意味層読解器) / K3 直交性(DCI/MIG)+機種プロファイル | P2 | PR2=roundtrip を楽譜準拠テスト化(PR1.5 の実コンパイル経路を検証)+CLAP(OSS 学習センサー・learned_models_policy 隔離下・LLM は out of scope)、PR3=K3 直交性を DCI/MIG で定式化+generator デバイスプロファイル。**両者とも PR1.5 を必須依存**。`docs/ai_performer_score_roadmap.md` |
| K3 | 直交性行列（レイヤー独立性） | P2 | **↑楽譜マージ PR3 に統合**。ツマミ i が観測 j を動かさないか(bpm が centroid を動かす等)を N×N で測り DCI/MIG で定式化。K2(#117)で対角 grip(bpm/brightness tight 転移)が立証済。または K2 を他ツマミ(key/stereo_width/valley_depth)へ拡張。controllability_poc.md §5 K3 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #119 | docs: 楽譜マージロードマップに PR1.5(control_profile-aware compile)を新設＝壁打ち結論を反映（本命は楽譜=実用物であって測定器でない・コンパイル脚は既存 ExternalPromptAdapter/performer と判明し PR2 の前に PR1.5 昇格・決定論=物理層保証/非決定論=意味層助言で CLAP は OSS 学習センサー限定 LLM は out of scope・多生成器は Suno ルート確立後+backend seam・Codex P2 を 13 件/10 コミット全対応で PR1.5 spec が緻密化・厳密実装契約は Design Memo で確定のスコープ境界注記で発散収束） | 2026-06-29 | AI-Performer Score Roadmap |
| #118 | docs: AI 演奏者のための楽譜 マージロードマップ（3 PR 構成）＝既存研究(MIR/CLAP/DCI-MIG/制御性評価/EPR)と蓄積知見(K 系列 grip/roundtrip fixity/genre bias)をマージ。PR1=control_profile スキーマ(fixity 踏襲・K2 初期データ)/PR2=楽譜準拠テスト+CLAP(隔離下)/PR3=K3 直交性(DCI/MIG)+機種プロファイル。索引2箇所同期 | 2026-06-29 | AI-Performer Score Roadmap |
| #117 | feat(control): K2 — 本物 Suno で bpm/brightness の grip 転移を確認（K1 玩具で tight だった 2 ツマミが Suno でも tight: bpm d=1.61 / brightness d=0.86・16 曲 fixture-driven・sha256 provenance・fixture→grip snapshot 固定・bpm 素朴センサーの prior アトラクタ圧縮/Suno は明得意暗苦手/センサー盲は素材依存の3知見） | 2026-06-29 | Controllability K2 |
| #116 | feat(genre): orchestral を onset_density 第二軸で捕捉（Phase E）＝Phase D 既知限界を closeout（mid-dominant 管弦と thin synth はスペクトル分離不能だが onset で ~14×分離・Star Wars 再添付 sha256 一致で n=3 完成・audit orchestral-real 3/3 match・collect-all で加算のみ・Codex P2=加算挙動を doc 訂正+test pin） | 2026-06-29 | Genre Calib Phase E |
| #115 | feat(genre): rock/edm を本物アンカー対応へルール再設計、orchestral 限界を確定（Phase D・rock 下限 0.117→0.105 で grunge 捕捉・edm を新軸 sub_bass で rock と分離・collect-all で Suno 回帰ゼロ・test_semantic_layer 無改変・orchestral は synth と分離不能で見送り mismatch 維持・Codex P2=カバレッジホール塞ぎ） | 2026-06-29 | Genre Calib Phase D |
