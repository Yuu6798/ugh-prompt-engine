# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

2026-07-20、**WI 系列（Work Identity 判定トラック）始動 — 壁打ち→ロードマップ→WI0 完了まで 1 セッション貫通（PR 5 本 #195–#199 全マージ）**。#195 AGENTS.md §8 規律 2 件恒久化（記録日付の date -u 実測確認 / provenance 再現レシピは全入力 pin 検証接続後に emit）→ #196 実 Suno AR4 検収デモを dated fixture 化（非 canonical 維持・pin 検証規律の適用第一号・verify 38/38）→ #197 `docs/work_identity_roadmap.md` 新設（WI0–WI4: 宣言的契約×弁別判定×人間校正×正直会計。queue の D-1/センサー配線を WI1/WI0 へ編入）→ #198 WI0-a=observe へ lyrics/melody センサー配線（synthetic・schema 0.1 無変更・4 anchor domain 全計器化）→ #199 WI0-b=実推論初実測: **melody pitch_lcs 0.6<0.8（事前登録閾値）で WI2 v0 除外**（原因=分離層欠如でセンサー品質でない・再入条件=旋律分離後 ≥0.8）+ lyrics ハルシネーション境界記録（ゲート=no_speech_prob・#149 追認）。1682 passed。セッションはエラー停止し wrap-up は継承セッションが PR 記録から代行（会話ログ喪失・非 PR 成果物は欠落の可能性・ブランチは User 復元済み）。次=**WI1**（MusicGen 無人 n=20 → 逸脱分布 → D-1 閾値 Design Memo・torch 再セットアップから）。詳細=2026-07-20.md。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| WI1: D-1 閾値 Design Memo | MusicGen 無人バッチ n=20 → 逸脱分布 → changed_within/outside_policy 分類の閾値起草 | P1 | 人手ゼロ。#197 で WI 系列 WI1 へ編入。#194 の語彙（section_insertion/omission/repetition）が分類カテゴリの受け皿。実 Suno A/B の A2（契約内逸脱のみ）/B1（順序破壊）が分類の試金石。**WI2 v0 の軸から melody は除外**（#199 事前登録判定・再入条件=旋律分離層導入後 ≥0.8）。環境揮発のため新セッションで torch+重み再セットアップ（~20 分）から |
| lyrics 転写の精度実測 | 歌入り+歌詞 pin 音源での転写精度実測 | P3 | #199 で defer（素材律速）。配線・境界挙動（instrumental ハルシネーション・no_speech_prob ゲート）は WI0-b で実測済み |
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
| #199 | feat(arrange): WI0-b — 実推論の転写精度初実測を fixture 化。melody pitch_lcs 0.6<0.8（事前登録閾値・事後変更なし）で **WI2 v0 除外**（原因=v0 比較設計の分離層欠如でセンサー品質でない・再入条件=旋律分離後 ≥0.8）+ lyrics は instrumental 入力の Whisper ハルシネーション境界記録（ゲート=no_speech_prob・#149 追認・精度実測は素材律速 defer）。1682 passed | 2026-07-20 | WI0-b |
| #198 | feat(arrange): WI0-a — observe へ lyrics/melody センサー配線（synthetic 検証・schema 0.1 無変更・4 anchor domain 全計器化・依存欠如は degrade 契約・melody v0 は音高系列のみ=BPM 交絡回避・検算照合 4 件全一致）。1650 passed | 2026-07-20 | WI0-a |
| #197 | docs(roadmap): WI 系列（Work Identity 判定トラック）ロードマップ新設 — WI0–WI4・思想 4 点セット（宣言的契約×弁別判定×人間校正×正直会計）・Goodhart 抑止/ラベル先行付与禁止の事前埋め込み・queue 2 項目を WI0/WI1 へ編入 | 2026-07-20 | WI 系列新設 |
| #196 | feat(arrange): 実 Suno AR4 検収デモを dated fixture 化（observed/suno/・非 canonical 探索の位置づけ維持・emit 前 pin 検証=#195 規律の適用第一号・verify 38/38・音源 4 本は sha256 pin のみで非コミット） | 2026-07-20 | AR4 実 Suno 初観測 |
| #195 | docs(agents): §8 に記録日付の date -u 実測確認 + provenance 再現レシピの全入力 pin 検証後 emit の 2 規律を恒久化（#193 日付誤記 / #191 9R 同根の教訓・414 行） | 2026-07-20 | §8 規律恒久化 |
