# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-07-03（Session 2）、**7 PR マージの最長セッション**。前半 = MusicGen 実測 3 部作（#136 PR B=G4 verbatim CC-BY-NC-4.0+K2 実測 fixture+`device_profiles/musicgen.yaml`+R3 初実測 / #137 K3-2b フル直交性行列=**ノイズ天井 |d|=0.848 初稼働**・異種チャネル間結合の天井超えゼロ+R3 n=20=key 保存率 0.15 確定・「選択=制御には per-field 保存率に見合う n が必要」を定量化 / #138 CLAP②=学習版 bpm d=2.60 重なりゼロ=**halving 交絡の第三証拠**・センサーは階層でなく相補）。後半 = 負債監査（Sonnet×3 並列・高 6 中 8 低 5 件）→ 解消 3 部作（#139 安全網 / #140 整理=**日常テストループ 134→60 秒** / #141 README 楽譜中心化+Status 矛盾 4 本解消）。機械作業のみで進むタスクは全て刈り取り、残キューは人手律速群 + Design Memo 3 件。プロセス教訓: worktree 並列エージェントはメインツリー誤編集リスク（1 回未遂・復元済み）、ブリーフの実測値は誤りうる=実装側の実測優先を明示する、検証待ちは pgrep で実在確認して直轄切替。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| CLAP 残③ | absent 側 alt の完全対称化（Suno 人手生成） | P2 | **② は #138 で closeout**（MusicGen K2 バッチへ拡張・bpm 学習版 d=2.60=halving 第三証拠・brightness は物理センサー優位=センサーは相補、musicgen_backend.md §7.5）。残 ③ のみ: 揃えば SEM-1 昇格ゲートと CLAP の n≥2×2 が同一バッチで埋まる。律速=Suno 人手生成。将来枠: K3 行列への意味センサー列追加 |
| K3-2b フル Suno 行列 | Suno 実測でフル直交性行列 | P2 | **設計指示 (a)(b)(c) と計器は #137 の MusicGen 行列で実証済み**（ノイズ天井・known_dead 行・key センサー化、controllability_poc.md §5.5）。Suno 実測が揃えば bpm→centroid 符号反転=機種依存の裁定と cross_couplings の unresolved 昇格が可能。律速=Suno 人手生成バッチ（5 ノブ×2×R≥8） |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化(K2 #117 でも bpm 素朴センサーの prior アトラクタが grip を圧縮することを再確認) |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |
| 意味層トラック lyrics 残 | 歌詞=意味層アンカー検証続行 + lyrics_presence 昇格セル埋め | P2 | **(b) control_profile ノブは SEM-1(#126) で実装済み**（`SemanticLayer.lyrics_presence`・loose 固定・昇格ゲート制度化=docs/control_profile.md）。残: (a) 検証デザイン B/C=同一アレンジ×【歌詞なし/原曲無関係歌詞/原曲由来歌詞】で「原曲寄り度」を人間評価×物理乖離(dynamic_range は #124 で棄却済につき別指標を探す)。(c) **mid_ratio 昇格セル埋め**: 各ジャンル instrumental alt 込み n≥2×2 セルで効果>再生成ノイズ + **K3 干渉分離**(genre も mid_ratio を動かす=matched-pair 統制) → 満たせば example の suno プロファイルを loose→tight 更新。**新展開(#131/#132)**: CLAP vocal contrast が「効果>再生成ノイズ」を**両ジャンル充足**(EDM 15.1×/Rock 10.9× vs mid_ratio 0.8×/1.3×)＝昇格判定のセンサーを mid_ratio から CLAP へ切替える選択肢が実データで立った(センサー変更は昇格ゲート定義の改訂=Design Memo 要)。**dynamic_range 再検証はしない**(棄却済)・**「ボーカル=主音の錨」は棄却済**。律速=人間生成バッチ+主観評価 |
| 負債繰越（Design Memo 要） | key 一致判定 3 実装の統一 / CLAP 埋め込みサイドカー化 / CI nightly extra ジョブ | P3 | 2026-07-03 負債監査の意図的スコープ外 3 件。key 統一は compare/audit/measure_grip 間の挙動変更を伴うため Design Memo 必須。サイドカー化は CLAP fixture 肥大対策（326KB/32 テイク・キュー続行で倍増確定）。nightly extra は adapter 環境依存バグ（#139 で×3 修正済み）の恒久検出、Actions 予算はユーザー判断 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #141 | docs: 負債監査② 大同期 — README を「Composition Score を軸に AI 生成器の制御可能性を実測する研究計器」へ書き直し（192 行・Score track Quick Start・Project Structure 実装完全一致）/ Status ヘッダ矛盾 4 本解消（learned_models_policy・score_centric_planning・ai_performer_score_roadmap タイトル 3→4 本・controllability_poc）/ architecture.md 二部構成化（計測三層+楽譜トラック）/ CLAUDE.md ツリー calibration/+device_profiles/ 追加（378 行）/ score-adherence を決定論経路チェックと明示（Codex P2） | 2026-07-03 | 負債解消 3/3 |
| #140 | chore: 負債監査③ 整理 — 死にスタブ 4 削除（svp/templates.py・batch/report.py・svp_templates.yaml×2）/ slow マーカー実測是正（--durations 全数実測で 11 付与 1 削除、**日常ループ 134→60 秒=55% 短縮**。ブリーフ想定誤り 1 件を実測で棄却し slow 維持）/ examples/control/k0/README.md=手作り fixture の出所明記 / CI pip cache / discipline 完了マーカーに「済」追加（Title 列粒度=Notes 欄の正当な部分完了説明を誤検出しない） | 2026-07-03 | 負債解消 2/3 |
| #139 | fix: 負債監査① 安全網 — サイレント事故 6 系統: section_features の except→0.0 に logging 可視化+回帰テスト / adapter version テスト×3（basic_pitch/panns/beat_this）を extra 導入環境でも密閉化（#138 CLAP 修正の横展開） / test_source_separator の subprocess グローバル汚染を _run 間接参照化（単体実行の確定 TypeError 解消） / .gitignore expected_output 罠除去 / config 同期テスト全 YAML 動的列挙化 / build_k3_fixture の mir_eval fail-fast 横展開 | 2026-07-03 | 負債解消 1/3 |
| #138 | feat(learned): CLAP 相互検証② — 学習版 grip を MusicGen K2 バッチ 32 テイクへ拡張（#131 同一 checkpoint・sha256 pin 一致）。**bpm: 学習版 d=2.60 tight・分布重なりゼロ**（素朴センサー 0.21 loose）=halving 交絡の第三の独立証拠。brightness: 学習版 1.50 vs ルール版 centroid 2.25=病理のない欄では物理センサー優位。**センサーは階層でなく相補**（学習版=ルール版の病理帯域を埋める補助計器）。fake CLAP テストの環境依存も密閉化 | 2026-07-03 | CLAP② / 意味層 |
| #137 | feat(k3/r3): K3-2b MusicGen フル直交性行列 + R3 n=20 — 設計指示 (a)dead 2 行 (b)R=8 (c)key センサー化を 80 クリップ自動生成で充足。**ノイズ天井計器（K3-1b）初稼働**: 天井 |d|=0.848・解像 3 セルのみ・異種チャネル間結合の天井超えゼロ=K3-2a の strong 群を over-reading から救済。key 対角 0.14 dead=R3 保存率 0.15 と独立整合。R3 n=20 で選抜完全回復+運用条件定量化。**DD-A seed pin は同一環境で sha256 完全再現**。Codex P2×2（mir_eval fail-fast / docs カウント矛盾） | 2026-07-03 | K3-2b / R3 |
