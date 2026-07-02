# STATUS Merged Log（アーカイブ）

STATUS.md "Recently Merged"（最新 5 件保持）から溢れたマージ済み PR を時系列で保持する。

| PR | Title | Date | Phase |
|---|---|---|---|
| #57 | feat(compose): CompositionScore schema + TargetSVP conversion | 2026-06-02 | C1 |
| #55 | feat: 開発フロー移植 + Composition PoC プランニング | 2026-05-26 | FLOW-1 |
| #54 | docs(validation): record 20-file real-audio smoke run | 2026-05 | Q0-5+ |
| #53 | feat(validation): Q4'-8 pseudo-label consensus harness | 2026-05-26 | Q4'-8 |
| #52 | feat(validation): model BPM octave ambiguity | 2026-05 | Q1-3 |
| #51 | real-audio measurement harness | 2026-05 | Q1 |
| #58 | docs: ワークフロー再反転（設計=Claude / 実装=Codex） | 2026-06-02 | FLOW-2 |
| #61 | feat(control): K0 grip measurement harness | 2026-06-03 | K0 |
| #60 | docs: controllability_poc.md 制御トラック K 系列 新設 | 2026-06-02 | K-plan |
| #59 | feat(compose): svprpe compose + ExternalPromptAdapter | 2026-06-02 | C2 |
| #62 | feat(audit): Composition audit control panel (C0+C3) | 2026-06-03 | C0+C3 |
| #63 | feat(discipline): セッション終了プロトコルを semantic-ci-code から移植 | 2026-06-12 | INFRA |
| #64 | feat(compose): C4 Composition E2E デモ — 決定論的シンセ演奏者によるフルループ | 2026-06-12 | C4 |
| #65 | feat(control): K1 grip 代表マップ初版 — 5 ツマミ + 補助センサー | 2026-06-12 | K1 |
| #67 | docs(policy): config 二重コピー同期の規約を Coding Conventions に追加 | 2026-06-12 | POLICY |
| #66 | refactor(sensors): brightness の正規センサーを spectral_centroid へ再設計 | 2026-06-12 | K1-followup |
| #72 | docs(test): Suno 往復/制御性テストケース結果を記録 | 2026-06-13 | T-validate |
| #71 | feat(transcribe): T1 draft Score 採譜 (svprpe transcribe) | 2026-06-13 | T1 |
| #70 | feat(transcribe): T0 per-field 計測ユーティリティ (svprpe measure) | 2026-06-13 | T0 |
| #74 | docs: roadmap_goal1 stale spec 整合 (Q1-2/Q1-4) | 2026-06-14 | goal1-align |
| #73 | docs: roadmap_goal2 起草 (R0–R5) + 索引同期 | 2026-06-14 | goal2 |
| #75 | feat(roundtrip): R0 往復保存性 三値診断 + svprpe roundtrip | 2026-06-15 | R0/T2 |
| #80 | feat(rpe): BPM 半折り（×2 オクターブ）検出 + trust gate 配線 | 2026-06-16 | R2-2a |
| #79 | refactor+fix: TODO センチネル集約 + C-gen send_form 整合 | 2026-06-16 | P3-followup |
| #77 | feat(roundtrip): R1 再実行可能 corpus + manifest 箱 (svprpe roundtrip-corpus) | 2026-06-15 | R1 |
| #76 | feat(compose): R5 fixity 型 + 入場試験制度化 | 2026-06-15 | R5 |
| #81 | feat(tooling): メタモルフィック計器 + R1 corpus スクリーナ（抽出器 BPM halving 発見） | 2026-06-16 | R1/R2 |
| #82 | feat(rpe): BPM octave 検出を近傍探索化（グリッド量子化 halving 捕捉） | 2026-06-17 | R2-2b |
| #83 | feat(rpe): octave-ambiguous 時に reported bpm を回復テンポへ補正 | 2026-06-17 | R2-2c |
| #84 | feat(rpe): BPM subharmonic (3:2) collapse を検出窓へ統合（117.45 アトラクタ） | 2026-06-17 | R2-2d |
| #85 | feat(screen): 高 prior 回復チェックを corpus screener に内蔵（compute_bpm start_bpm） | 2026-06-17 | R2 |
| #86 | feat(screen): 低 prior で BPM doubling(÷2) を抽出器要因と弁別（screener 限定診断、extractor 不触） | 2026-06-18 | R2-2e |
| #87 | docs(R2): R2 closeout — bpm を確率的経路(R3)の信頼再現ノブから明示除外確定（完成定義 §4 / per-field bpm trust / クリティカルパス伝播） | 2026-06-18 | R2 closeout |
| #88 | docs(R4): event roundtrip DD-D 解除条件を文書化（コード進行を最初の事象欄に選定） | 2026-06-19 | R4-1 |
| #89 | feat(R4): CompositionScore にコード進行事象欄を追加し performer grip を実装 | 2026-06-19 | R4-2 |
| #90 | feat(R4): コード進行の往復比較指標と RoundtripField 4値診断 + fixity 事象層対応 | 2026-06-19 | R4-3 |
| #91 | feat(Q1-5): magnitude基準7帯域スペクトル + tempo_stability + HPSS比を追加 | 2026-06-19 | Q1-5 |
| #93 | docs(R2): R2-2f CV-scale 実音源校正 closeout（実音源7本で CV_SCALE=5.0 据置確定） | 2026-06-22 | R2-2f |
| #94 | docs/fix(R1): upload-only 4本をDrive化しdrive_file_id付与 + wafu除外 + fetch_corpus が excluded を unresolved 非計上 | 2026-06-22 | R1 |
| #95 | docs: ドキュメント整合性リファクタ（Architecture ツリー同期 + 鮮度監査ドリフト13件修正） | 2026-06-23 | infra |
| #96 | test: slow マーカーを per-test 化し日常テストループを高速化（6.5→3.4分、slow 31件に厳選） | 2026-06-23 | infra |
| #97 | feat(R1): screen 由来の実音源 calibratable レコードを R1 箱 manifest に取り込み（箱を screener 経路 canonical 化 + Codex P2 で repo-root locator 解決） | 2026-06-24 | R1 |
| #98 | docs(genre-calibration): 意味層ジャンル語彙拡張の planning doc（Tier1/2/3・Suno 校正コーパス方針） | 2026-06-24 | Genre Calib |
| #99 | refactor(semantic): ジャンル/楽器推定の config 化（Phase A・厳密振る舞い保存・条件エンジンに `_gt`/`_lt` 追加・packaged 補完・Codex P2×2 解決） | 2026-06-24 | Genre Calib Phase A |
| #100 | feat(calibration): ジャンル校正ハーネス（genre manifest + 分離度/閾値候補レポート + `insufficient` ゲート + `spectral_bands.*` ドット key 解決） | 2026-06-25 | Genre Calib B-1 |
| #101 | feat(calibration): genre misfire 監査（現行ルールを校正コーパスに適用し混同表を出す計器・verdict なし） | 2026-06-25 | Genre Calib B-1b |
| #102 | feat(semantic): brightness で orchestral/bass-music を分離（B-2・`low_ratio>0.4` を `high_ratio` 0.017 で明暗二分・管弦の bass-music 誤判定を是正・synth 不変） | 2026-06-25 | Genre Calib B-2 |
| #103 | feat(calibration): genre seed manifest を実 Suno 実測 10 本で確定（orchestral n=5 + electronic-dance n=5 を measured インライン保全・2 点 stub→n=12・B-2 split の誤判定 0 を恒久ガード化） | 2026-06-25 | Genre Calib seed |
| #104 | feat(semantic): brightness split を magnitude brilliance へ移行（B-3・`_absent` 演算子新設で bands あり→brilliance 一次/欠落→power high_ratio fallback の相互排他・回帰無改変 green・二重発火なし） | 2026-06-25 | Genre Calib B-3 |
| #105 | feat(genre): brilliance 3-way banding で rock 分離（B-3-rock・旧単一閾値0.1537の rock 裂きを gap 中点0.117/0.204 へ・audit に rock 期待値・回帰ゼロ・Codex P2 境界穴を `_min` 化） | 2026-06-26 | Genre Calib B-3-rock |
| #106 | docs(bpm): R2-2 halving は sr/閾値の単一ノブでは直せないと実証（finding #6・punk ratio1.057<正検出indie1.098 で分離不能・native は synth_05 を半化・コード変更なし） | 2026-06-26 | Q1-5 Ph2 / R2-2 |
| #107 | test(probe): magnitude brilliance も合成器では盲を計器/回帰ガード化（`high_ratio==0.0` 前提再点検・power≡0/magnitude 非ゼロ floor で平坦 grip≈9e-4・ノブ energy は mid 帯へ・centroid のみ live sensor・rule 不変） | 2026-06-28 | Q1-5 Ph2 |
| #108 | docs(genre): low/mid_ratio は power 据え置き＝Q1-5 Ph2 移行は不要/評価不能と実測（`low_ratio` ゲート健全・magnitude 低域は部分分離で全ペア判別は brilliance のみ・mid は production 閾値未発火で繰越・magnitude 軸は Suno-only grounding を caveat 化・Codex 5 ラウンド・rule 不変） | 2026-06-28 | Q1-5 Ph2 |
| #109 | feat(calibration): 実 J-POP 3 本を real anchor 登録＝Phase C 着手・Suno EDM over-brightening 初観測（repo 初の実 grounding spectral_bands・low ゲートは本物で通用・特徴量ごとの実効 n 明示・rule 不変） | 2026-06-28 | Genre Calib Phase C |
| #110 | feat(calibration): 本物×Suno matched-pair で generator bias を genre-controlled 実証（同ジャンル/キー対・brilliance candidate d=3.76・スマイリーEQ=低↑中↓高↑・key3/3 BPM2/3 一致・rule 不変） | 2026-06-28 | Genre Calib Phase C |
| #111 | feat(calibration): cross-genre で Suno 指紋の一定性を検定＝単一補正係数は反証（本物 orch/rock/EDM 各1本 vs 純Suno・brilliance bias 符号反転=非一定・mid削り+脱トーナル化は方向一定・low ゲートは Suno 低域厚バイアス依存・Codex P2×2=audit 配線/純Suno baseline・rule 不変） | 2026-06-28 | Genre Calib Phase C |
| #112 | feat(calibration): orchestral 本物アンカーを n=3 に増強し補正係数を方向確定（Holst・久石譲 追加・mid削り +0.363 支配軸・low ゲートは Suno バイアスで本物に汎化せず audit 3/3 mismatch・Codex P2=純Suno baseline・rule 不変） | 2026-06-29 | Genre Calib Phase C |
| #113 | feat(calibration): rock 本物アンカーを n=3 に増強し方向不変指紋を 2 ジャンル目で確認（AC/DC・Queen 追加・脱トーナル化 -0.189 が rock で最強・sub_bass で edm と分離可能を示唆・rule 不変） | 2026-06-29 | Genre Calib Phase C |
| #114 | feat(calibration): edm 本物アンカーを n=3 に増強＝3 ジャンル実 grounding 完了（横断 Δ で脱トーナル化+mid削りが方向一定を分布確定・brilliance は量がジャンルで割れ単一補正不可・Codex P2×2=brilliance 検定を平均シフト/delta 直接比較へ・rule 不変） | 2026-06-29 | Genre Calib Phase C |
| #115 | feat(genre): rock/edm を本物アンカー対応へルール再設計、orchestral 限界を確定（Phase D・rock 下限 0.117→0.105 で grunge 捕捉・edm を新軸 sub_bass で rock と分離・collect-all で Suno 回帰ゼロ・test_semantic_layer 無改変・orchestral は synth と分離不能で見送り mismatch 維持・Codex P2=カバレッジホール塞ぎ） | 2026-06-29 | Genre Calib Phase D |
| #116 | feat(genre): orchestral を onset_density 第二軸で捕捉（Phase E）＝Phase D 既知限界を closeout（mid-dominant 管弦と thin synth はスペクトル分離不能だが onset で ~14×分離・Star Wars 再添付 sha256 一致で n=3 完成・audit orchestral-real 3/3 match・collect-all で加算のみ・Codex P2=加算挙動を doc 訂正+test pin） | 2026-06-29 | Genre Calib Phase E |
| #117 | feat(control): K2 — 本物 Suno で bpm/brightness の grip 転移を確認（K1 玩具で tight だった 2 ツマミが Suno でも tight: bpm d=1.61 / brightness d=0.86・16 曲 fixture-driven・sha256 provenance・fixture→grip snapshot 固定・bpm 素朴センサーの prior アトラクタ圧縮/Suno は明得意暗苦手/センサー盲は素材依存の3知見） | 2026-06-29 | Controllability K2 |
| #118 | docs: AI 演奏者のための楽譜 マージロードマップ（3 PR 構成）＝既存研究(MIR/CLAP/DCI-MIG/制御性評価/EPR)と蓄積知見(K 系列 grip/roundtrip fixity/genre bias)をマージ。PR1=control_profile スキーマ(fixity 踏襲・K2 初期データ)/PR2=楽譜準拠テスト+CLAP(隔離下)/PR3=K3 直交性(DCI/MIG)+機種プロファイル。索引2箇所同期 | 2026-06-29 | AI-Performer Score Roadmap |
| #119 | docs: 楽譜マージロードマップに PR1.5(control_profile-aware compile)を新設＝壁打ち結論を反映（本命は楽譜=実用物であって測定器でない・コンパイル脚は既存 ExternalPromptAdapter/performer と判明し PR2 の前に PR1.5 昇格・決定論=物理層保証/非決定論=意味層助言で CLAP は OSS 学習センサー限定 LLM は out of scope・多生成器は Suno ルート確立後+backend seam・Codex P2 を 13 件/10 コミット全対応で PR1.5 spec が緻密化・厳密実装契約は Design Memo で確定のスコープ境界注記で発散収束） | 2026-06-29 | AI-Performer Score Roadmap |
| #122 | feat(roundtrip): score-adherence test（PR2）＝control_profile-tight 保証の準拠判定計器（`svprpe score-adherence`: compiled_kept(PR1.5 の drop されない保証)+preserved(roundtrip 4 値診断) をフィールド単位判定・backend selector 共有で path 非依存・**計器であって verdict ではない**=グローバル pass/fail なし。CLAP は torch+2GB 重み・policy adopt 外で PR2b へ分離） | 2026-06-30 | AI-Performer Score Roadmap |
| #121 | feat(compose): control_profile-aware compile（PR1.5）＝コンパイルループを Suno で閉じる（ExternalPromptAdapter を control_profile 駆動のフィールド粒度コンパイルへ刷新・**tight 先頭昇格**(ユーザー確認)・physical.optional 束を 4 フィールド独立文へ分解・backend selector external→suno・priority エイリアス・backend descriptor 隔離。Codex P2×3=casing 退行/time_signature 未描画/backend 誤ラベル全対応） | 2026-06-30 | AI-Performer Score Roadmap |
| #120 | feat(compose): control_profile スキーマ（PR1）＝楽譜が効くチャネルを自己記述（生成器→物理フィールド→grip_class・`fixity` と違い**疎を許容**(K2 の Suno は bpm/brightness のみ)・未知 field fail-fast・ControlGrip(grip_class 必須/grip・sensor・evidence optional)・K2(#117) 初期データ投入・docs/control_profile.md 新規） | 2026-06-30 | AI-Performer Score Roadmap |