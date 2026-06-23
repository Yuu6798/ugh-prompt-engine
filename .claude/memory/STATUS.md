# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

目的2（再現実証）の R4「作品同一性 — 事象レベル欄の往復」を 2026-06-19 に closeout。コード進行を
最初の事象欄に選び、R4-1（#88, DD-D 解除条件 docs）→ R4-2（#89, `CompositionScore.events.chord_progression`
構造化欄 + performer grip）→ R4-3（#90, 系列一致率 `chord_sequence_match_rate` を既存 roundtrip
ハーネスに統合 + draft `chord_events`→`events` 復元 + fixity 事象層対応）で、authored コード進行が
score→perform→extract→draft→系列一致率→RoundtripField 4値診断の往復を生き残ることを実測根拠付きで
正規スキーマに入れた。設計判断: degree を **pre-transpose tonic 基準**で算出し `style.transpose` を
take 摂動として温存（FAITHFUL_TAKE で絶対 root 厳密復元・FIRST_TAKE で進行ごと移調）、閾値
`CHORD_MATCH_THRESHOLD=0.75` は比較器側に置き Pydantic model に非埋込、chord 欄は source events
非空時のみ report に追加して既存物理 7 欄の形状を不変に保つ。honesty 規律として grip fixture に
chord record を捏造せず knob_dead は既存 grip_map 経路を温存（knob 生存の根拠は R4-2 grip テスト）、
移調不変形レポートと chord grip 実測 fixture は follow-up に defer。R4 で人間非依存タスクは出切り、
残キュー（R1-audio / K2 Suno 転移）は人間タスク律速。**R2 `BPM_CONFIDENCE_CV_SCALE` 校正は
#93 で closeout**（#92 loader で実音源 7 本を materialize し `CV_SCALE=5.0` 据え置き確定・
content-address で licensing 回避）。R1-audio も loader は #92 マージ済で、**2026-06-22 に upload-only 4 本（astral + abc×3）を Drive 化し
manifest に drive_file_id 付与（#94、Drive `create_file` は MB 級 base64 転送不能のため SendUserFile
返送→ユーザー Drive→search で id 回収・byte-size 照合の橋渡し、screen 対象 7 本すべて保有）**。
`wafu_jungle_174`（8 本目）はバイト未取得で `excluded: true` 除外し `fetch_corpus.py` を excluded＝
unresolved 非計上へ修正、R1 Drive corpus は screen 対象 7/7 で完結。並行して Q1-5（spectral 計器拡張）を closeout：外部 audio-analyzer
との実測差分比較で重複コアの同等性を実証しつつ `spectral_profile` の power/magnitude 不整合を発見し、magnitude
基準 7帯域 `spectral_bands` + `tempo_stability_std` + harmonic/percussive 比を additive 追加（#91、マージ後 A/B/C で
外部 magnitude 7帯域との一致を実測確認）。次の actionable は Phase 2（既存 power 3帯域の是正と semantic_rules/config
閾値の magnitude 再校正、校正コーパスの licensing が律速）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| R1-audio | R1 箱 calibratable レコード追加 | P2 | Drive corpus 部分は #94 で screen 対象 7/7 解決済（drive_file_id 付与・`excluded` loader 対応）。残作業は **R1 箱(#77) manifest（`takes` コンテナ）へ calibratable レコードを追加**し、`fetch_corpus.py`→`screen_corpus.py` 経路で実音源を回せる形にすること。`wafu_jungle_174` はバイト未取得で除外中（将来入手時に `excluded` 解除 + drive_file_id 付与で 8/8 復帰可）。roadmap_goal2.md R1 |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正 | P2 | 既存 power 3帯域(`low/mid/high_ratio`)の是正 + `semantic_rules.py`/config 閾値(`low_ratio_min:0.4`/`mid_ratio_min:0.45`/`high_ratio>0.3`)を検証済 magnitude `spectral_bands` 基準へ移行 + `screen_2026-06-16.yaml` 再採取 + `test_metamorphic_probe.py` の `high_ratio==0.0` 前提見直し。閾値再導出の校正コーパスが R1-audio 同様 licensing 律速。Phase 1=#91 マージ済 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #96 | test: slow マーカーを per-test 化し日常テストループを高速化（6.5→3.4分、slow 31件に厳選） | 2026-06-23 | infra |
| #95 | docs: ドキュメント整合性リファクタ（Architecture ツリー同期 + 鮮度監査ドリフト13件修正） | 2026-06-23 | infra |
| #94 | docs/fix(R1): upload-only 4本をDrive化しdrive_file_id付与 + wafu除外 + fetch_corpus が excluded を unresolved 非計上 | 2026-06-22 | R1 |
| #93 | docs(R2): R2-2f CV-scale 実音源校正 closeout（実音源7本で CV_SCALE=5.0 据置確定） | 2026-06-22 | R2-2f |
| #91 | feat(Q1-5): magnitude基準7帯域スペクトル + tempo_stability + HPSS比を追加 | 2026-06-19 | Q1-5 |
