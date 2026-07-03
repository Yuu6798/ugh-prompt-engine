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
| #127 | feat(control): K3-2a — K2 fixture 再利用（新規生成ゼロ・決定論 JSON 変換）で本物 Suno の 2×2 コア+extended 3 列ミニ直交性行列を実測。対角は K2 公表値を正確再現（bpm 1.61/brightness 0.863）＝変換忠実性の検証、**bpm→centroid 結合の符号が玩具と反転**（−11.6 vs +2.33）＝干渉補正は機種デバイスプロファイルで持つ知識（PR3 後半の実証的動機）、overall disentanglement 0.051=玩具の 1/7（**対角 tight ≠ 直交な操作盤**を実生成器で初観測）。honesty: R=4・dead 行なしでセル単位は未解決（確度の階層を §5.4 に明示）。K3-2b 設計指示（dead 行同梱・R≥8・baseline key）を docs 残置 | 2026-07-02 | 制御トラック K3 |
| #126 | feat(compose): SEM-1 — `SemanticLayer.lyrics_presence` を control_profile **意味層ノブ第一号**として導入（`SEMANTIC_CONTROL_FIELDS` ホワイトリスト・fixity 不変・None 省略 serialize で既存スコア byte 互換・compile は present/absent セグメント+instrumental tag+grip tier 参加・adherence は skipped_semantic_fields 計上で黙って落とさない）。**loose 固定 honesty**（mid_ratio noise 超えは Rock のみ=#124）・tight 昇格ゲート=n≥2×2 セル+K3 干渉分離を docs 制度化。Codex P2×1（priority の dotted 表記 no-op）を alias 追加で即日対応 | 2026-07-02 | 意味層トラック |
| #125 | feat(control): K3-1 — 直交性行列を **DCI/MIG の効果量再定式化**として実装（全セル=既存 grip と同一の符号付き Cohen's d・非対角=clean/weak/strong・importance floor 0.2/cap 10・MIG=「効果量ギャップ」と正直命名・エイリアスセンサーは正方コア外 extended 列）。決定論 performer 5×5+1 実測: **干渉の 2 分類**（生成側構造結合 bpm→onset 系 vs センサー側結合 brightness→onset_density）・**dead 行=経験的ヌル分布**（seed ジッターのみで \|d\|≲2.5）・cap 副作用（gap 偽同率）・overall disentanglement 0.375。Fable レビューで -0.0 クランプ問題を捕捉・修正 | 2026-07-02 | 制御トラック K3 |
| #124 | docs: 歌詞アレンジ・デモ n=3 追試（実音源 StartinA を EDM/Rock 再キャスト × 歌詞あり/なし＋歌詞側 alt を実 Suno 計測）＝前 #123 の「歌詞→dynamic_range 低下」を **棄却**（Rock 反転かつ再生成ノイズ未満・EDM も instrumental alt 無しで directional 保留）、**mid_ratio は最有力だが noise 超えは Rock のみ**（昇格=各ジャンル instrumental alt 込み n≥2×2 セル）。付随=BPM grip 確度×精度2軸・調号 grip(生成6中5)/進行 非再現。計測ログ＋audio_sha256 pin・Tier-A サーフェス同期。**「効果>再生成ノイズ」基準を全指標に一様適用**の規律確立。Codex 自動レビュー P2×10 全対応（noise baseline/sha256 provenance/生成器分母5-6 等） | 2026-07-01 | 意味層トラック |
| #123 | docs: 歌詞=意味層アンカー仮説（アレンジ・デモ発見の保全）＝実 Suno＋実音源の「同一 EDM アレンジ × 歌詞あり/なし」2 曲対照から**歌詞は意味層のアンカー**（付与する「メリハリ」は物理 dynamic_range に写らずむしろ逆＝計器の盲点・耳が唯一のセンサー）。honesty: n=1「ボーカル=主音の錨」を n=2 方向反転で棄却・halving 非法則化（n≥3 保留）。中域 mid_ratio はボーカル検出に堅い。付随=genre pop 帯欠落/低 sub EDM 誤判定/実音源 halving/m4a 非対応。n≥3 検証デザイン明記・索引2箇所同期。**※ n=3 追試 #124 で dynamic_range 逆相関を proxy 棄却・mid_ratio を Rock 限定に更新（この行の旧主張は superseded）** | 2026-07-01 | 意味層トラック（新設） |
| #130 | feat(learned): PR2b-1 — CLAP を隔離学習センサーとして配線（learned/ 確立パターン複製・`LearnedAudioAnnotations.embedding` を populate する初のアダプタ・similarity は numpy のみ=fixture 上で完全動作・runbook+policy 整合テスト新設）。**rand_trunc 非決定論を「構造による決定論」で回避**（自前デコード→窓長以下チャンク→平均、RNG シード不採用）。bot の exact-size UnboundLocalError 指摘は **PyPI wheel 一次ソースで反証**。amodel 貫通（music checkpoint=HTSAT-base 必須） | 2026-07-02 | 意味層トラック / PR2b |
| #129 | feat(control): K3-1b — 直交性ハーネスに統計的有意性を計器化。fixture の **`known_dead` 宣言**（配線非実在はコード検査で既知=推定でなく宣言）から経験的ヌル天井 max\|d\| を自動計算、各セルに noise_margin / exceeds フラグ。dead 行なし fixture では ceiling=None=全セル unresolved を**計器自身が申告**。機械判定の resolved セルが §5.3 の散文判断と完全一致=昇格の検証。DCI 不変（注釈レイヤー） | 2026-07-02 | 制御トラック K3 |
| #128 | feat(compose): Suno デバイスプロファイル（PR3 後半）— 機種の癖を `config/device_profiles/suno.yaml` に構造化（K2 grip 既定値・knob quirks・K3-2a cross_couplings=全 unresolved 記録・genre calib バイアス、全 evidence 付き）し compile へ 2 経路接続: control_profile 欠落の device 既定補完（score 宣言が常に勝つ）+ advisories（**プロンプト本文・tags 不変=自動補正しない**・CLI text は stderr 分離）。adherence 非対称は未決として docs 明記 | 2026-07-02 | AI-Performer Score Roadmap PR3 |
| #131 | feat(learned): PR2b-2 — CLAP 実 fixture 採取（**Codex×Fable 並行分担の初運用**・Codex PowerShell 実行）。StartinA 6 テイクの実埋め込み+vocal contrast、**「効果>再生成ノイズ」を両ジャンル初充足**（意味層に初の機械センサー）。G4=HF バッジ cc0-1.0 目視・checkpoint_sha256 pin・materialize 手順文書化（Fable 直接 push）・semantic-embed extra へ torch 明示追加（上流 install_requires 不備、Fable 直接 push）。レビュー 8 スレッド全消化 | 2026-07-02 | 意味層トラック / PR2b |
| #132 | docs(learned): 相互検証① — CLAP vocal contrast × mid_ratio を audio_sha256 突き合わせ（6/6 リンク・生成/推論ゼロ）。**条件レベルの方向は両センサー完全一致**=同じ潜在因子（ボーカル有無）を指す、感度は「効果>再生成ノイズ」規約で **CLAP のみ両ジャンル充足**（EDM 15.1×/Rock 10.9× vs mid_ratio 0.8×/1.3×）=意味層は学習センサーの直接読みが桁で有利。honesty: within-condition 順序不一致・n=6・統計量非主張。方向一致は cross-consistency テストで pin（データ変更時に再検証を強制） | 2026-07-02 | 意味層トラック / PR2b |
| #133 | docs: 棚卸し監査に基づくロードマップ実態同期 + Advisor Strategy 明文化 — roadmap.md P1–P5 Status 列 / **roadmap_goal1 に Q1-5 転記漏れ解消** / roadmap_goal2 の R3 ブロッカー失効（K2=#117 完了）反映・R4 ✅化・#106 追記 / PLANNING ヘッダ 3+1 本実態化 / CLAUDE.md CI 表記 3.11/3.12 / brief 2 本を .claude/briefs/ 正位置化+status 3 件 merged 化 / 未マージブランチから AGENTS.md §7 item 10（locked file/未検出フィールド、PR #71 churn 教訓）をサルベージ+skill ミラー / **Advisor Strategy Fable 主導体制+レビュー振り分けルールを CLAUDE.md へ明文化**。Codex P2×3 対応。棚卸し結論=誇張ゼロ・乖離は全て「完了済みが予定のまま」方向 | 2026-07-03 | リポジトリ棚卸し / 運用 |
| #134 | feat(control): MusicGen ローカル生成 runbook（第二生成器トラック PR A・推論なし CI 安全）— `collect_musicgen_takes.py`（generate=torch 遅延 import・決定論 seed / extract=sha256 pin 照合 fail-fast→K2 同一スキーマ fixture・measure_grip 直結）+ `musicgen` extra（torch 境界・重み非同梱）+ K2 型 plan.yaml（bpm 90/170・brightness dark/bright・R=8）+ docs/musicgen_backend.md（DD-A 契約・**G4 VERIFY PENDING ゲート**・PR A/B/C 分割）。狙い=Suno 人手律速の解体+device profile 複数機種一般性+R3 自動化 | 2026-07-03 | MusicGen トラック PR A |
| #135 | feat(roundtrip): R3 確率的演奏者の往復ハーネス（第二生成器トラック PR C）— `repetition.py`（R3-2: n>1 フィールド別 preserved_rate/diagnosis_counts・R3-3: rejection sampling=`R3_SELECTION_FIELDS(key/brightness)` スコープで補助センサー支配を排除・verdict 語彙ゼロ・n<2 fail-fast・grip_map 既定空=K1 決定論校正の誤流用防止）+ runbook `perform`（slug 安全化・n<2 事前拒否・generator ラベル model_id 導出）+ `svprpe roundtrip-rep`（score_ref=診断スコア固定・manifest 不一致は stderr advisory）。roadmap_goal2 R3=「計器実装済み・実測待ち」。Codex P2×7 全消化 | 2026-07-03 | roadmap_goal2 R3 / MusicGen トラック PR C |
| #136 | feat(musicgen): PR B — 実バッチ 32 本→K2 型 fixture（**brightness tight d=2.25**=Suno 0.86 超・絶対 dark 帯到達 3/8 / **bpm loose 0.21=knob_dead でなく抽出器 halving**、start_bpm=180 で 7/8 が 172.27 回復=R2 発見の第二生成器再現）+ `device_profiles/musicgen.yaml`（第二機種・実測スコープを model_id+revision の 2 軸 pin する scope advisory）+ R3 初実測（rejection sampling 初実証→profile 導入で再生成し「選抜は存在するものからしか拾えない」限界も記録）。G4 verbatim CC-BY-NC-4.0（研究計器限定）。Codex P2×3 | 2026-07-03 | MusicGen PR B |
