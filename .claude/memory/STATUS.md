# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-07-03（日跨ぎセッション）、**棚卸し監査→修正→MusicGen 第二生成器トラック着手で 3 PR 完走**。
棚卸し（Sonnet×4 並列）で「完了済みが予定のまま」乖離を一掃（#133: R3 ブロッカー失効反映・
Q1-5 転記・Status ヘッダ実態化・brief 正位置化・ロスト知見サルベージ + **Advisor Strategy
Fable 主導体制/レビュー振り分けルールの CLAUDE.md 明文化を解消**）。壁打ちで構造ギャップ=
「生成の人手律速」+「単一機種校正」を特定し **MusicGen（transformers 経路）トラック新設**:
#134 PR A（runbook generate/extract・`musicgen` extra=torch 境界・K2 型 plan・
docs/musicgen_backend.md・**G4 VERIFY PENDING ゲート**=HF 目視まで PR B 非着手）→
#135 PR C（R3 計器: `roundtrip/repetition.py` の n>1 フィールド別一致率 R3-2 +
rejection sampling R3-3=`R3_SELECTION_FIELDS(key/brightness)` スコープ・bpm は R2 closeout
除外・verdict 語彙ゼロ・n<2 fail-fast / `perform` サブコマンド R3-1 / `svprpe roundtrip-rep`。
grip_map 既定は空マッピング=K1 決定論校正の誤流用防止。Codex P2×7 全消化）。
**R3 = 計器実装済み・実測待ち** — 実測は PR B（実バッチ→K2 型 fixture→
`device_profiles/musicgen.yaml`→R3 初実測）が **HF 許可反映後の新セッションの機械作業のみ**。
プロセス教訓: 検証チェーンは `set -o pipefail` 必須（exit code 隠蔽で red push 1 回）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| MusicGen PR B | 実バッチ→K2 型 fixture + device profile + R3 初実測 | P1 | **器と計器は #134/#135 で完成済み・人手生成不要が本トラックの狙い**。手順=docs/musicgen_backend.md: ①ネットワーク許可に `*.hf.co` 追加確認（huggingface.co/cdn-lfs は設定済・反映は新セッション必須） ② G4 目視（HF badge → §5 に verbatim 記録、期待 CC-BY-NC-4.0=研究計器限定） ③ `pip install -e ".[musicgen]"` → runbook generate/extract → measure_grip → `config/device_profiles/musicgen.yaml`（packaged 同期・unprofiled 前提テストの backend 名差し替えに注意） ④ `perform`+`roundtrip-rep` で R3 初実測。その先は K3-2b 相当のフル行列自動生成も射程 |
| CLAP 相互検証 残 | 学習版 grip の適用拡大（②既存コーパス / ③absent alt 対称化） | P2 | **PR2b 本体は closeout 済**（#130 隔離配線 / #131 実 fixture / #132 相互検証①=方向一致+感度 CLAP 桁勝ち、lyrics_semantic_anchor.md §相互検証①）。残: **②** 既存コーパス（K2 の 16 テイク等・音源はローカル/Drive に実在）へ CLAP を適用し brightness 等の「学習版 grip vs ルール版 grip」を拡張 — **律速=軽い Codex ローカル推論のみ**（生成不要・runbook 整備済み・`--amodel HTSAT-base`）。**③** absent 側 alt の完全対称化（Suno 人手生成）— 揃えば SEM-1 昇格ゲートと CLAP の n≥2×2 が同一バッチで埋まる。将来枠: K3 行列への意味センサー列追加・MuLan/CLAMP3 比較 |
| 楽譜マージ PR3 残 | K3-2b フル Suno 直交性行列 | P2 | **K3-1(#125)+K3-1b(#129)+K3-2a(#127)+デバイスプロファイル(#128) で構造的作業は closeout 済**（DCI/MIG 効果量再定式化・有意性計器化・実 Suno ミニ行列・機種の癖の config 化+advisory、controllability_poc.md §5.3-5.4 / control_profile.md）。残= **K3-2b** フル Suno 行列: §5.4 の設計指示どおり **dead ツマミ行（内部ヌル）同梱・R≥8・baseline key 宣言**。取得できれば noise_margin 付きでセル単位の結合（bpm→centroid 符号反転等）が確定し、デバイスプロファイルの cross_couplings を unresolved から昇格できる。律速=生成バッチ人手 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |
| 意味層トラック lyrics 残 | 歌詞=意味層アンカー検証続行 + lyrics_presence 昇格セル埋め | P2 | **(b) control_profile ノブは SEM-1(#126) で実装済み**（`SemanticLayer.lyrics_presence`・loose 固定・昇格ゲート制度化=docs/control_profile.md）。残: (a) 検証デザイン B/C=同一アレンジ×【歌詞なし/原曲無関係歌詞/原曲由来歌詞】で「原曲寄り度」を人間評価×物理乖離(dynamic_range は #124 で棄却済につき別指標を探す)。(c) **mid_ratio 昇格セル埋め**: 各ジャンル instrumental alt 込み n≥2×2 セルで効果>再生成ノイズ + **K3 干渉分離**(genre も mid_ratio を動かす=matched-pair 統制) → 満たせば example の suno プロファイルを loose→tight 更新。**新展開(#131/#132)**: CLAP vocal contrast が「効果>再生成ノイズ」を**両ジャンル充足**(EDM 15.1×/Rock 10.9× vs mid_ratio 0.8×/1.3×)＝昇格判定のセンサーを mid_ratio から CLAP へ切替える選択肢が実データで立った(センサー変更は昇格ゲート定義の改訂=Design Memo 要)。**dynamic_range 再検証はしない**(棄却済)・**「ボーカル=主音の錨」は棄却済**。律速=人間生成バッチ+主観評価 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #135 | feat(roundtrip): R3 確率的演奏者の往復ハーネス（第二生成器トラック PR C）— `repetition.py`（R3-2: n>1 フィールド別 preserved_rate/diagnosis_counts・R3-3: rejection sampling=`R3_SELECTION_FIELDS(key/brightness)` スコープで補助センサー支配を排除・verdict 語彙ゼロ・n<2 fail-fast・grip_map 既定空=K1 決定論校正の誤流用防止）+ runbook `perform`（slug 安全化・n<2 事前拒否・generator ラベル model_id 導出）+ `svprpe roundtrip-rep`（score_ref=診断スコア固定・manifest 不一致は stderr advisory）。roadmap_goal2 R3=「計器実装済み・実測待ち」。Codex P2×7 全消化 | 2026-07-03 | roadmap_goal2 R3 / MusicGen トラック PR C |
| #134 | feat(control): MusicGen ローカル生成 runbook（第二生成器トラック PR A・推論なし CI 安全）— `collect_musicgen_takes.py`（generate=torch 遅延 import・決定論 seed / extract=sha256 pin 照合 fail-fast→K2 同一スキーマ fixture・measure_grip 直結）+ `musicgen` extra（torch 境界・重み非同梱）+ K2 型 plan.yaml（bpm 90/170・brightness dark/bright・R=8）+ docs/musicgen_backend.md（DD-A 契約・**G4 VERIFY PENDING ゲート**・PR A/B/C 分割）。狙い=Suno 人手律速の解体+device profile 複数機種一般性+R3 自動化 | 2026-07-03 | MusicGen トラック PR A |
| #133 | docs: 棚卸し監査に基づくロードマップ実態同期 + Advisor Strategy 明文化 — roadmap.md P1–P5 Status 列 / **roadmap_goal1 に Q1-5 転記漏れ解消** / roadmap_goal2 の R3 ブロッカー失効（K2=#117 完了）反映・R4 ✅化・#106 追記 / PLANNING ヘッダ 3+1 本実態化 / CLAUDE.md CI 表記 3.11/3.12 / brief 2 本を .claude/briefs/ 正位置化+status 3 件 merged 化 / 未マージブランチから AGENTS.md §7 item 10（locked file/未検出フィールド、PR #71 churn 教訓）をサルベージ+skill ミラー / **Advisor Strategy Fable 主導体制+レビュー振り分けルールを CLAUDE.md へ明文化**。Codex P2×3 対応。棚卸し結論=誇張ゼロ・乖離は全て「完了済みが予定のまま」方向 | 2026-07-03 | リポジトリ棚卸し / 運用 |
| #132 | docs(learned): 相互検証① — CLAP vocal contrast × mid_ratio を audio_sha256 突き合わせ（6/6 リンク・生成/推論ゼロ）。**条件レベルの方向は両センサー完全一致**=同じ潜在因子（ボーカル有無）を指す、感度は「効果>再生成ノイズ」規約で **CLAP のみ両ジャンル充足**（EDM 15.1×/Rock 10.9× vs mid_ratio 0.8×/1.3×）=意味層は学習センサーの直接読みが桁で有利。honesty: within-condition 順序不一致・n=6・統計量非主張。方向一致は cross-consistency テストで pin（データ変更時に再検証を強制） | 2026-07-02 | 意味層トラック / PR2b |
| #131 | feat(learned): PR2b-2 — CLAP 実 fixture 採取（**Codex×Fable 並行分担の初運用**・Codex PowerShell 実行）。StartinA 6 テイクの実埋め込み+vocal contrast、**「効果>再生成ノイズ」を両ジャンル初充足**（意味層に初の機械センサー）。G4=HF バッジ cc0-1.0 目視・checkpoint_sha256 pin・materialize 手順文書化（Fable 直接 push）・semantic-embed extra へ torch 明示追加（上流 install_requires 不備、Fable 直接 push）。レビュー 8 スレッド全消化 | 2026-07-02 | 意味層トラック / PR2b |
