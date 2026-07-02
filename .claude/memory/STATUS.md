# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-07-02（2 セッション）、**Fable 5 初稼働で通算 8 PR 完走** — 楽譜マージロードマップの
**PR1〜PR3 構造的作業と PR2b が全て closeout**、残キューは人手・生成律速のみになった。制御トラック:
#125 K3-1（直交性行列=DCI/MIG の効果量再定式化・干渉の 2 分類・dead 行=経験的ヌル分布・玩具
disentanglement 0.375）→ #127 K3-2a（K2 fixture 再利用=生成ゼロで実 Suno ミニ行列・対角 K2 再現・
**bpm→centroid 結合の符号が機種で反転**・disentanglement 0.051=対角 tight でも直交でない）→
#128 デバイスプロファイル（機種の癖を config 化し compile へ 2 経路接続=既定補完+advisory、
**本文不変・自動補正しない計器の規律**）→ #129 K3-1b（`known_dead` 宣言→ヌル天井の自動計算・
散文の確度判断が計器出力へ昇格）。意味層トラック: #126 SEM-1（`lyrics_presence`=control_profile
意味層ノブ第一号・loose 固定 honesty）→ #130 PR2b-1（CLAP 隔離配線・rand_trunc を構造的決定論で
回避・bot 誤指摘を wheel 一次ソースで反証）→ #131 PR2b-2（Codex×Fable 並行分担・実 fixture 採取・
**CLAP vocal contrast が「効果>再生成ノイズ」を両ジャンル初充足** EDM 15×/Rock 12×）→ #132
相互検証①（sha256 突き合わせで**新旧センサーの方向完全一致**・感度は CLAP が桁勝ち 15.1×/10.9×
vs mid_ratio 0.8×/1.3×＝「耳が唯一のセンサー」だった意味層に検収済みの機械センサーが立った）。
**運用**: 分業体制（設計判定=Fable/実装サブ=Sonnet 固定/非設計分析=Opus）に加え、**レビュー対応の
振り分けルール新設**（マシン非依存=Fable 直接対応・codex/* push 可 / マシン依存=Codex・ユーザー）
— #131 で 8 スレッドを並行消化し実証済み。CLAUDE.md への明文化 2 件（Advisor Strategy 改訂+
振り分けルール）は次回 PR 化を提案中。次: 相互検証②（軽 Codex 推論）/ K3-2b・absent alt・
SEM-1 昇格（人手律速）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| CLAP 相互検証 残 | 学習版 grip の適用拡大（②既存コーパス / ③absent alt 対称化） | P2 | **PR2b 本体は closeout 済**（#130 隔離配線 / #131 実 fixture / #132 相互検証①=方向一致+感度 CLAP 桁勝ち、lyrics_semantic_anchor.md §相互検証①）。残: **②** 既存コーパス（K2 の 16 テイク等・音源はローカル/Drive に実在）へ CLAP を適用し brightness 等の「学習版 grip vs ルール版 grip」を拡張 — **律速=軽い Codex ローカル推論のみ**（生成不要・runbook 整備済み・`--amodel HTSAT-base`）。**③** absent 側 alt の完全対称化（Suno 人手生成）— 揃えば SEM-1 昇格ゲートと CLAP の n≥2×2 が同一バッチで埋まる。将来枠: K3 行列への意味センサー列追加・MuLan/CLAMP3 比較 |
| 楽譜マージ PR3 残 | K3-2b フル Suno 直交性行列 | P2 | **K3-1(#125)+K3-1b(#129)+K3-2a(#127)+デバイスプロファイル(#128) で構造的作業は closeout 済**（DCI/MIG 効果量再定式化・有意性計器化・実 Suno ミニ行列・機種の癖の config 化+advisory、controllability_poc.md §5.3-5.4 / control_profile.md）。残= **K3-2b** フル Suno 行列: §5.4 の設計指示どおり **dead ツマミ行（内部ヌル）同梱・R≥8・baseline key 宣言**。取得できれば noise_margin 付きでセル単位の結合（bpm→centroid 符号反転等）が確定し、デバイスプロファイルの cross_couplings を unresolved から昇格できる。律速=生成バッチ人手 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |
| 意味層トラック lyrics 残 | 歌詞=意味層アンカー検証続行 + lyrics_presence 昇格セル埋め | P2 | **(b) control_profile ノブは SEM-1(#126) で実装済み**（`SemanticLayer.lyrics_presence`・loose 固定・昇格ゲート制度化=docs/control_profile.md）。残: (a) 検証デザイン B/C=同一アレンジ×【歌詞なし/原曲無関係歌詞/原曲由来歌詞】で「原曲寄り度」を人間評価×物理乖離(dynamic_range は #124 で棄却済につき別指標を探す)。(c) **mid_ratio 昇格セル埋め**: 各ジャンル instrumental alt 込み n≥2×2 セルで効果>再生成ノイズ + **K3 干渉分離**(genre も mid_ratio を動かす=matched-pair 統制) → 満たせば example の suno プロファイルを loose→tight 更新。**新展開(#131/#132)**: CLAP vocal contrast が「効果>再生成ノイズ」を**両ジャンル充足**(EDM 15.1×/Rock 10.9× vs mid_ratio 0.8×/1.3×)＝昇格判定のセンサーを mid_ratio から CLAP へ切替える選択肢が実データで立った(センサー変更は昇格ゲート定義の改訂=Design Memo 要)。**dynamic_range 再検証はしない**(棄却済)・**「ボーカル=主音の錨」は棄却済**。律速=人間生成バッチ+主観評価 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #132 | docs(learned): 相互検証① — CLAP vocal contrast × mid_ratio を audio_sha256 突き合わせ（6/6 リンク・生成/推論ゼロ）。**条件レベルの方向は両センサー完全一致**=同じ潜在因子（ボーカル有無）を指す、感度は「効果>再生成ノイズ」規約で **CLAP のみ両ジャンル充足**（EDM 15.1×/Rock 10.9× vs mid_ratio 0.8×/1.3×）=意味層は学習センサーの直接読みが桁で有利。honesty: within-condition 順序不一致・n=6・統計量非主張。方向一致は cross-consistency テストで pin（データ変更時に再検証を強制） | 2026-07-02 | 意味層トラック / PR2b |
| #131 | feat(learned): PR2b-2 — CLAP 実 fixture 採取（**Codex×Fable 並行分担の初運用**・Codex PowerShell 実行）。StartinA 6 テイクの実埋め込み+vocal contrast、**「効果>再生成ノイズ」を両ジャンル初充足**（意味層に初の機械センサー）。G4=HF バッジ cc0-1.0 目視・checkpoint_sha256 pin・materialize 手順文書化（Fable 直接 push）・semantic-embed extra へ torch 明示追加（上流 install_requires 不備、Fable 直接 push）。レビュー 8 スレッド全消化 | 2026-07-02 | 意味層トラック / PR2b |
| #130 | feat(learned): PR2b-1 — CLAP を隔離学習センサーとして配線（learned/ 確立パターン複製・`LearnedAudioAnnotations.embedding` を populate する初のアダプタ・similarity は numpy のみ=fixture 上で完全動作・runbook+policy 整合テスト新設）。**rand_trunc 非決定論を「構造による決定論」で回避**（自前デコード→窓長以下チャンク→平均、RNG シード不採用）。bot の exact-size UnboundLocalError 指摘は **PyPI wheel 一次ソースで反証**。amodel 貫通（music checkpoint=HTSAT-base 必須） | 2026-07-02 | 意味層トラック / PR2b |
| #129 | feat(control): K3-1b — 直交性ハーネスに統計的有意性を計器化。fixture の **`known_dead` 宣言**（配線非実在はコード検査で既知=推定でなく宣言）から経験的ヌル天井 max\|d\| を自動計算、各セルに noise_margin / exceeds フラグ。dead 行なし fixture では ceiling=None=全セル unresolved を**計器自身が申告**。機械判定の resolved セルが §5.3 の散文判断と完全一致=昇格の検証。DCI 不変（注釈レイヤー） | 2026-07-02 | 制御トラック K3 |
| #128 | feat(compose): Suno デバイスプロファイル（PR3 後半）— 機種の癖を `config/device_profiles/suno.yaml` に構造化（K2 grip 既定値・knob quirks・K3-2a cross_couplings=全 unresolved 記録・genre calib バイアス、全 evidence 付き）し compile へ 2 経路接続: control_profile 欠落の device 既定補完（score 宣言が常に勝つ）+ advisories（**プロンプト本文・tags 不変=自動補正しない**・CLI text は stderr 分離）。adherence 非対称は未決として docs 明記 | 2026-07-02 | AI-Performer Score Roadmap PR3 |
