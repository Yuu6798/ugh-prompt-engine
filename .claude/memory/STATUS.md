# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-07-19、**Arrangement Identity Track を AR0–AR4 全フェーズ実装済みまで 1 セッションで貫通（PR 5 本 #190–#194 全マージ）**。#190 `svprpe verify`（package 内部整合の全数検査計器・#187 round 16 の受け皿）+ AGENTS.md §8「計測スケール変更の消費者全数インベントリ」/ #191 AR4 実観測初取得（queue の「Suno 人手律速」を MusicGen ローカル決定論で解体・12s n=2・fresh-process sha 2/2・AR2-3 は保留継続とし解凍条件を (a) structure センサー + (b) 長尺 form artifact に具体化）/ #192 structure センサー observe 配線（section-map/0.1・(a) 充足・正規化は 3R で「正典 verbatim / 観測は VerseN のみ吸収」の非対称に収束）/ #193 form 実測（30s n=2・挿入/欠落/反復/長さ振れ=実生成の常態を確定・**AR2-3 解凍**）/ #194 **AR2-3 本体**（section-map/0.2 stable ID・section_ref 解決・実測由来 form 語彙 3 語・new-brief 起草で Codex 指摘ゼロ）。Codex 通算 15R・P2 17 件全採用（#191 の 9R は provenance 同一ファミリー）。締めに**実 Suno 5.5 初の AR4 検収 A/B**（scratchpad・非 canonical）: tags セルで正典 form 冒頭完全再現 take・尺 2 倍・進行は 4/4 非再現（調近親圏は維持）。運用: full pytest 1663 件は Bash 10 分上限超 → not-slow+slow 分割が標準。次=MusicGen 無人 n=20 → D-1 閾値 Design Memo（人手ゼロ路線を User と合意）。詳細=2026-07-19.md。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| D-1 閾値 Design Memo | MusicGen 無人バッチ n=20 → 逸脱分布 → changed_within/outside_policy 分類の閾値起草 | P1 | 人手ゼロ。#194 の語彙（section_insertion/omission/repetition）が分類カテゴリの受け皿。実 Suno A/B の A2（契約内逸脱のみ）/B1（順序破壊）が分類の試金石。環境揮発のため新セッションで torch+重み再セットアップ（~20 分）から |
| lyrics / melody センサー配線 | observe の no_sensor 2 domain 解消 | P2 | 人手ゼロ。接続点は observe.py docstring 明記済み（lyrics=eval/lyrics_match+lyrics_adapter / melody=basic_pitch）。structure センサー（#192）と同型の手順 |
| 実 Suno AR4 デモの fixture 化 | scratchpad/suno_ar4_demo の 4 観測を dated case study として commit するか | P3 | **User 未決**。実 Suno 初の AR4 観測データ（planning doc「実 Suno 未観測」ギャップを埋める）。観測 JSON 等は wrap-up 時に SendUserFile で User へ退避済み（scratchpad 揮発対策）。非 canonical 探索の位置づけ維持が条件 |
| verify builds-root 再帰監査 | --builds-root ツリー全体の再帰 verify | P3 | #190 で out of scope と明記した follow-up（docs/cli.md 言及）。完全ローカル |
| Metrics v2 メタモルフィック検証 | レベル不変性（ゲインシフト不変）を probe で掃引恒久化 | P3 | #188 の T-INV 単発を Hypothesis 掃引へ。完全ローカル・決定論 |
| Metrics v2 再ベースライン | Pro n=20 で v2 基準値化 + legacy_valley_depth 一括撤去 | P2 | #188 の凍結措置の解凍タスク。Pro 群 n=20 を v2 で再計測→ジャンル別 crest/valley_db 分布→`config/*_baseline.yaml` の実置換（active_rate_ideal/valley_depth_pro 系の凍結解除）+ semantic rules/domain profile の valley 閾値 v2 スケール移行 + `legacy_valley_depth()` と `valley_depth_legacy` の一括撤去（時限マーカー: rpe/models.py:191 / semantic_ci/observed_adapter.py:22 / baseline yaml 4 本 / docs/metrics.md）。CompositionScore 計器の v2 移行は target 帯再ベースラインと同時。律速=Pro 実音源 n=20 の入手（licensing） |
| DD-D 解除（意味フィールド write-through） | CLAP 読みの semantic.* 自動記入解禁の是非 | P3 | 軸バッテリー本体は #151 で v1.1 closeout（probe チューニング第一巡・7 軸・sweep 手順制度化＝追加軸は config 追記+`--axes-config` sweep+校正ログ再生成のみ）。write-through 解禁は promotion gate G1-G5 + 校正基準文書化が前提（score_centric §5）。valence 軸の本来検証（セクション別感情アーク）は実制作音源律速 |
| K2-seg Suno 転移 | プロンプト欄 grip 地図の Suno 側検証（バッチ 4 = 残 3 ノブ）+ structure4 タグチャネル A/B | P2 | **MusicGen 側はバッチ M2（#173/#174・2026-07-13）で closeout** — 3 ノブとも M1 規律 canonical で再計測済み（active_rate loose 0.414 / valley_depth dead→loose 0.3518 / time_signature dead・device profile 反映済み）。**残は Suno 側のみ**: バッチ 4 は時刻証跡前提（画面録画 or 40 分超バッチ）、**structure4 発注書（タグチャネル A/B・non-canonical 事前登録・instrumental 固定）はコミット済み=発注可能状態**（律速=Suno 人手生成 8 本）。バッチ 1（avoid+core）=#162 / Exclude 追試=#164（confounded・isolated 追試待ち）は従来どおり。副次の種: バッチ 3 novelty d=+0.71 loose の追検討・M1 の区間別非対称（quiet 通る/loud 通らない=デフォルト形状引力と整合） |
| lyrics センサー実音源 E2E | vocals 分離込み実 E2E + committed fixture | P2 | **機械配線は #149 で完走**（`extract --lyrics`/`lyrics-adherence`・schema 1.2・全チェーン MIT 実確認）。実推論はフルミックス経路のみ検証済（検証環境に ffmpeg なし）。残＝歌入り実音源での Demucs 分離込み E2E、real fixture 化、順序診断の実測（反復歌詞の同点限界の実挙動確認）。律速=実音源 |
| v2 ブロック Drive 化 | #159 の 8 音源 drive_file_id 回収 | P3 | lyrics_symmetric_block_2026-07-08.yaml / CLAP v2 manifest は drive_file_id 未取得のまま merged（捏造せず省略・follow-up 明記済み）。#94 方式（SendUserFile→Drive→search 回収・byte/sha 照合）で追記。律速=人間作業 |
| K3-2b フル Suno 行列 | Suno 実測でフル直交性行列 | P2 | **設計指示 (a)(b)(c) と計器は #137 の MusicGen 行列で実証済み**（ノイズ天井・known_dead 行・key センサー化、controllability_poc.md §5.5）。Suno 実測が揃えば bpm→centroid 符号反転=機種依存の裁定と cross_couplings の unresolved 昇格が可能。律速=Suno 人手生成バッチ（5 ノブ×2×R≥8） |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: 自動化は #155 R2-2f で closeout（3:2 帯 (1.35,1.6) 限定の prior-disagreement 検出を extractor 配線）。残=実音源 live E2E（punk 22050+start_bpm=180 の同条件実測、R1-audio 律速）と窓外の未観測比（screener 診断で継続監視） |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |
| 意味層トラック lyrics 残 | 歌詞=意味層アンカー検証続行 + lyrics_presence 昇格セル埋め | P2 | **(b) control_profile ノブは SEM-1(#126) で実装済み**（`SemanticLayer.lyrics_presence`・loose 固定・昇格ゲート制度化=docs/control_profile.md）。残: (a) 検証デザイン B/C=同一アレンジ×【歌詞なし/原曲無関係歌詞/原曲由来歌詞】で「原曲寄り度」を人間評価×物理乖離(dynamic_range は #124 で棄却済につき別指標を探す)。(c) は **#159 で closeout**: mid_ratio 昇格は両ジャンル棄却が確定（absent alt 込み n≥2×2 実測）・センサーは CLAP vocal contrast へ DD-4 改訂済み（tight 昇格は条件 2 formal 充足を経て **#160 で config 反映済み**＝SEM-1 昇格ゲート初完走）。**dynamic_range 再検証はしない**(棄却済)・**「ボーカル=主音の錨」は棄却済**。**新展開(07-05 #149)**: 歌詞転写＋検収は `extract --lyrics`/`lyrics-adherence` として制度化済み＝検証デザイン B/C の「原曲由来歌詞」素材の機械生成・機械検収が CLI で可能に。律速=人間生成バッチ+主観評価 |
| 発注書生成スクリプト再作成 | scratchpad 消失分の再作成（必要時のみ） | P3 | 旧「neutral ラベル化の本体還元」行の残置項目（本体は #156 で closeout）。Suno カバー発注書生成スクリプトは scratchpad 消失につき、次に必要になった時点で再作成 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #194 | feat(arrange): AR2-3 本体 — section-map/0.2（stable id・0.1 と並置=committed 観測 fixture 再現性の根拠付き例外）+ section_ref 解決（0.2 anchor の id へ・dangling fail-fast・0.2 不在は opaque 後方互換）+ form 変形語彙 3 語（実測由来・3 層検証自動追従）。new-brief 起草で Codex 指摘ゼロ。1663 passed | 2026-07-19 | AR2-3（解凍→同日実装） |
| #193 | feat(arrange): AR4 form 実測 — MusicGen 30s×structure センサーで実 form artifact 初観測（挿入・欠落・重複・長さ振れ=常態を確定）+ AR2-3 解凍判断。日付 provenance P2 是正（実クロック 07-19 へ・hash 連鎖内コメントは意図的残置） | 2026-07-19 | AR2-3 解凍条件 (b) |
| #192 | feat(arrange): structure センサー observe 配線 — section-map/0.1 + 位置揃え一致計量。Codex 3R: format_version 宣言ゲート／正規化を非対称（正典 verbatim・観測は抽出器 VerseN 慣行のみ吸収）へ収束 | 2026-07-19 | AR2-3 解凍条件 (a) |
| #191 | feat(arrange): AR4 実観測データ初取得 — MusicGen ローカル決定論 n=2（canonical 事前登録・fresh-process sha 2/2）で observation report を実生成物で充填 + verify 実データ初適用 16/16。Codex 9R 全採用（provenance 同一ファミリー: 全入力 pin 検証接続・per-input hash・出力幾何・衝突ガード・パス衛生） | 2026-07-19 | AR4 実観測（人手律速の解体） |
| #190 | feat(arrange): svprpe verify — PerformancePackage 内部整合の全数検査計器（V1–V4・全数収集・#187 round 16 の受け皿）+ AGENTS.md §8 計測スケール変更の消費者全数インベントリ規律。Codex 2R（manifest anchor 5 欄突合・artifact_base 同一実体検査） | 2026-07-19 | AR 計器 + 規律恒久化 |
