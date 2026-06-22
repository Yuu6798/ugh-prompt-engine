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
content-address で licensing 回避）。R1-audio も loader は #92 マージ済で、残は upload-only
4 本（astral + abc×3）の Drive 化 + `wafu_jungle_174` バイト入手のみ。並行して Q1-5（spectral 計器拡張）を closeout：外部 audio-analyzer
との実測差分比較で重複コアの同等性を実証しつつ `spectral_profile` の power/magnitude 不整合を発見し、magnitude
基準 7帯域 `spectral_bands` + `tempo_stability_std` + harmonic/percussive 比を additive 追加（#91、マージ後 A/B/C で
外部 magnitude 7帯域との一致を実測確認）。次の actionable は Phase 2（既存 power 3帯域の是正と semantic_rules/config
閾値の magnitude 再校正、校正コーパスの licensing が律速）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| R1-audio | 実 Suno 音源の corpus 登録 | P1 | sha256 固定済（#81）+ **Drive-backed content-address loader `scripts/fetch_corpus.py` マージ済（#92）**。licensing は Drive 非同梱で回避済。残: durable resolver(`drive_file_id`) は 3/7（shiden/yaoyorozu/so_what）、**upload-only 4 本（astral + abc×3）の Drive アップロード + `drive_file_id` 付与**、`wafu_jungle_174` バイト入手で 8/8、R1 箱(#77) manifest への calibratable レコード追加。roadmap_goal2.md R1 |
| ~~R2 CV校正~~ ✅ | `BPM_CONFIDENCE_CV_SCALE` 実校正 | — | **closeout（2026-06-22, #93）** — #92 loader で実音源 7 本を materialize し `CV_SCALE=5.0` 据え置き確定（preserved 3 本で Q1-3 契約 >0.7 を実音源実証、CV∈[0.020,0.040]）。結論は Drive 解決可能 3 本で成立。残る reproducible corpus（upload-only 4 本の Drive 化）は上の R1-audio 行へ集約。roadmap_goal2.md R2-2f |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正 | P2 | 既存 power 3帯域(`low/mid/high_ratio`)の是正 + `semantic_rules.py`/config 閾値(`low_ratio_min:0.4`/`mid_ratio_min:0.45`/`high_ratio>0.3`)を検証済 magnitude `spectral_bands` 基準へ移行 + `screen_2026-06-16.yaml` 再採取 + `test_metamorphic_probe.py` の `high_ratio==0.0` 前提見直し。閾値再導出の校正コーパスが R1-audio 同様 licensing 律速。Phase 1=#91 マージ済 |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #91 | feat(Q1-5): magnitude基準7帯域スペクトル + tempo_stability + HPSS比を追加 | 2026-06-19 | Q1-5 |
| #90 | feat(R4): コード進行の往復比較指標と RoundtripField 4値診断 + fixity 事象層対応 | 2026-06-19 | R4-3 |
| #89 | feat(R4): CompositionScore にコード進行事象欄を追加し performer grip を実装 | 2026-06-19 | R4-2 |
| #88 | docs(R4): event roundtrip DD-D 解除条件を文書化（コード進行を最初の事象欄に選定） | 2026-06-19 | R4-1 |
| #87 | docs(R2): R2 closeout — bpm を確率的経路(R3)の信頼再現ノブから明示除外確定（完成定義 §4 / per-field bpm trust / クリティカルパス伝播） | 2026-06-18 | R2 closeout |
