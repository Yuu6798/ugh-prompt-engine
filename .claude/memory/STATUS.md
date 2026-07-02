# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-07-02、**Fable 5 初稼働**で制御×意味層の 2 トラックを **3 PR 完走**（設計・レビュー=Fable /
実装=Sonnet の分業、Codex 指摘対応まで即日）。**#125 K3-1**: 直交性行列を DCI/MIG の**効果量
再定式化**として実装（全セル=符号付き Cohen's d・importance floor 0.2/cap 10・MIG=効果量ギャップと
正直命名・エイリアスセンサーは正方コア外の extended 列へ）、決定論 performer 実測で**干渉の 2 分類**
（生成側構造結合 vs センサー側結合）と **dead 行=経験的ヌル分布**（|d|≲2.5 は seed ノイズ）を発見、
玩具 disentanglement 0.375。**#127 K3-2a**: K2(#117) fixture 再利用（**新規生成ゼロ**）で本物 Suno
ミニ行列 — 対角は K2 を正確再現（1.61/0.863）、**bpm→centroid 結合の符号が玩具と反転**（−11.6 vs
+2.33）＝干渉補正は機種**デバイスプロファイル**で持つ知識という PR3 後半の実証的動機、
disentanglement 0.051=**対角 tight でも操作盤は直交でない**を実生成器で初観測（セル単位は R=4・
dead 行なしで未解決＝確度の階層を明示）。**#126 SEM-1**: `SemanticLayer.lyrics_presence` を
control_profile **意味層ノブ第一号**として導入（loose 固定 honesty・fixity 不変・adherence は
skipped 計上で黙って落とさない・昇格ゲート=各ジャンル instrumental alt 込み n≥2×2 セル+K3 干渉
分離を制度化）。次: K3-2b（dead 行同梱+R≥8+baseline key、人手律速）/ SEM-1 昇格セル埋め
（instrumental alt 生成）/ PR2b（依存意思決定待ち）。**分業体制（設計判断=Fable/実装=Sonnet 5/
非設計分析=Opus）は次セッション引き継ぎ**（CLAUDE.md Advisor Strategy 更新は提案中）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| 楽譜マージ PR2b | CLAP 学習センサー（意味層読解器） | P2 | **#122 で PR2 本体(score-adherence)から分離**。CLAP(または MuLan/CLAMP3)を learned 補助センサーとして配線し prompt↔audio/score↔audio の cosine 適合度=「学習版 grip」を算出、ルール版 grip と相互検証。`learned_models_policy.md` の**隔離原則厳守**(`LearnedAudioAnnotations` へ・ルール evidence 非混入)。**着手前に依存方針の意思決定が必要**: ①CLAP は torch+2GB 重みで現 adopt リスト外→policy 更新要 ②本環境で実推論検証不能=spike 配線止まりの恐れ ③「LLM不要・軽量」契約への影響(OSS 学習センサー限定・API LLM は out of scope)。依存=PR1/PR1.5(実コンパイル経路) |
| 楽譜マージ PR3 残 | K3-2b フル Suno 行列 + 機種デバイスプロファイル本体 | P2 | **前半は K3-1(#125)+K3-2a(#127) で closeout 済**（DCI/MIG 効果量再定式化・玩具/実 Suno 実測、controllability_poc.md §5.3-5.4）。残= (a) **K3-2b** フル Suno 行列: §5.4 の設計指示どおり **dead ツマミ行（内部ヌル）同梱・R≥8・baseline key 宣言**（律速=生成バッチ人手）、(b) **デバイスプロファイル本体**: bpm→centroid 結合の符号が玩具と反転（−11.6 vs +2.33）＝干渉補正は機種固有知識、が実証的動機。genre bias（脱トーナル化/mid削り）と統合して機種別補正へ構造化。依存=PR1/PR1.5（済） |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |
| 意味層トラック lyrics 残 | 歌詞=意味層アンカー検証続行 + lyrics_presence 昇格セル埋め | P2 | **(b) control_profile ノブは SEM-1(#126) で実装済み**（`SemanticLayer.lyrics_presence`・loose 固定・昇格ゲート制度化=docs/control_profile.md）。残: (a) 検証デザイン B/C=同一アレンジ×【歌詞なし/原曲無関係歌詞/原曲由来歌詞】で「原曲寄り度」を人間評価×物理乖離(dynamic_range は #124 で棄却済につき別指標を探す)。(c) **mid_ratio 昇格セル埋め**: 各ジャンル instrumental alt 込み n≥2×2 セルで効果>再生成ノイズ + **K3 干渉分離**(genre も mid_ratio を動かす=matched-pair 統制) → 満たせば example の suno プロファイルを loose→tight 更新。**dynamic_range 再検証はしない**(棄却済)・**「ボーカル=主音の錨」は棄却済**。CLAP=PR2b の導入動機と接続。律速=人間生成バッチ+主観評価 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #127 | feat(control): K3-2a — K2 fixture 再利用（新規生成ゼロ・決定論 JSON 変換）で本物 Suno の 2×2 コア+extended 3 列ミニ直交性行列を実測。対角は K2 公表値を正確再現（bpm 1.61/brightness 0.863）＝変換忠実性の検証、**bpm→centroid 結合の符号が玩具と反転**（−11.6 vs +2.33）＝干渉補正は機種デバイスプロファイルで持つ知識（PR3 後半の実証的動機）、overall disentanglement 0.051=玩具の 1/7（**対角 tight ≠ 直交な操作盤**を実生成器で初観測）。honesty: R=4・dead 行なしでセル単位は未解決（確度の階層を §5.4 に明示）。K3-2b 設計指示（dead 行同梱・R≥8・baseline key）を docs 残置 | 2026-07-02 | 制御トラック K3 |
| #126 | feat(compose): SEM-1 — `SemanticLayer.lyrics_presence` を control_profile **意味層ノブ第一号**として導入（`SEMANTIC_CONTROL_FIELDS` ホワイトリスト・fixity 不変・None 省略 serialize で既存スコア byte 互換・compile は present/absent セグメント+instrumental tag+grip tier 参加・adherence は skipped_semantic_fields 計上で黙って落とさない）。**loose 固定 honesty**（mid_ratio noise 超えは Rock のみ=#124）・tight 昇格ゲート=n≥2×2 セル+K3 干渉分離を docs 制度化。Codex P2×1（priority の dotted 表記 no-op）を alias 追加で即日対応 | 2026-07-02 | 意味層トラック |
| #125 | feat(control): K3-1 — 直交性行列を **DCI/MIG の効果量再定式化**として実装（全セル=既存 grip と同一の符号付き Cohen's d・非対角=clean/weak/strong・importance floor 0.2/cap 10・MIG=「効果量ギャップ」と正直命名・エイリアスセンサーは正方コア外 extended 列）。決定論 performer 5×5+1 実測: **干渉の 2 分類**（生成側構造結合 bpm→onset 系 vs センサー側結合 brightness→onset_density）・**dead 行=経験的ヌル分布**（seed ジッターのみで \|d\|≲2.5）・cap 副作用（gap 偽同率）・overall disentanglement 0.375。Fable レビューで -0.0 クランプ問題を捕捉・修正 | 2026-07-02 | 制御トラック K3 |
| #124 | docs: 歌詞アレンジ・デモ n=3 追試（実音源 StartinA を EDM/Rock 再キャスト × 歌詞あり/なし＋歌詞側 alt を実 Suno 計測）＝前 #123 の「歌詞→dynamic_range 低下」を **棄却**（Rock 反転かつ再生成ノイズ未満・EDM も instrumental alt 無しで directional 保留）、**mid_ratio は最有力だが noise 超えは Rock のみ**（昇格=各ジャンル instrumental alt 込み n≥2×2 セル）。付随=BPM grip 確度×精度2軸・調号 grip(生成6中5)/進行 非再現。計測ログ＋audio_sha256 pin・Tier-A サーフェス同期。**「効果>再生成ノイズ」基準を全指標に一様適用**の規律確立。Codex 自動レビュー P2×10 全対応（noise baseline/sha256 provenance/生成器分母5-6 等） | 2026-07-01 | 意味層トラック |
| #123 | docs: 歌詞=意味層アンカー仮説（アレンジ・デモ発見の保全）＝実 Suno＋実音源の「同一 EDM アレンジ × 歌詞あり/なし」2 曲対照から**歌詞は意味層のアンカー**（付与する「メリハリ」は物理 dynamic_range に写らずむしろ逆＝計器の盲点・耳が唯一のセンサー）。honesty: n=1「ボーカル=主音の錨」を n=2 方向反転で棄却・halving 非法則化（n≥3 保留）。中域 mid_ratio はボーカル検出に堅い。付随=genre pop 帯欠落/低 sub EDM 誤判定/実音源 halving/m4a 非対応。n≥3 検証デザイン明記・索引2箇所同期。**※ n=3 追試 #124 で dynamic_range 逆相関を proxy 棄却・mid_ratio を Rock 限定に更新（この行の旧主張は superseded）** | 2026-07-01 | 意味層トラック（新設） |
