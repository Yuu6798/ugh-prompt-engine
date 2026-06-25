# STATUS.md — ugh-prompt-engine プロジェクト状況

## Phase

現行アクティブスレッドは**意味層ジャンル語彙拡張（Genre Calibration）**。実音源体験で意味層が管弦を
`bass-music` 誤判定する限界が露呈し、planning（#98）→ Phase A config 化（#99, `cultural_context`/
`instrumentation` を `semantic_rules.yaml` 宣言ルール化・厳密 `_gt`/`_lt`・packaged 補完）で着手済。
2026-06-25 に Phase B-1 を実装まで前進：B-1 校正ハーネス（#100, `src/svp_rpe/calibration/` に genre
ラベル付き manifest + ジャンル別 feature 分布 + ペア分離度/閾値候補レポート + `n<3` を `insufficient`
とする honesty ゲート + `_feature_value` の `spectral_bands.*` ドット key 解決）と misfire 監査（#101,
現行ルールを校正コーパスに適用し `genre_label×予測` 混同表を出す計器、verdict なし）をどちらも
Design Memo→Codex 実装→マージで完走。**初の分離線を実データで実証**：Suno 生成 orchestral n=5（+本物
Portals）と electronic-dance n=5 を `extract`→`genre-calibrate` し、**brightness 軸で綺麗に分離**
（brilliance d=6.99 / spectral_centroid 閾値≈2234 / presence / high_ratio が non-overlap）、一方
**harmonic_ratio と low_ratio は overlap**＝(1) harmonic 単独不可が本物 Portals=0.81 を入れた瞬間に
EDM 帯へ食い込み再現、(2) `low_ratio` は両ジャンルとも 0.4 超で区別不能＝現行 `bass-music` 誤判定の
真因を statistically 確定。dynamics は Suno 生成バイアス（広ダイナミクス再現不可）で overlap。**B-2（#102）で
コア誤診を closeout**：`low_ratio>0.4`（低音厚）を `high_ratio` 0.017 で二分し暗→`cinematic/orchestral`・
明→`bass-music` とする相補ルールを config に反映、orchestral→cinematic/orchestral・EDM→bass-music・
synth→general を実測検証（discriminator は最強 brilliance でなく後方互換＝常在欄の `high_ratio` を採用し
magnitude 移行は Q1-5 Ph2 へ委譲、synth 誤検出は `low_ratio` ガードで回避し snapshot 不変を AC 化）。
**2026-06-25 に seed manifest を確定（#103）**：6-25 dated log に保全していた実 Suno 実測 orchestral n=5 +
electronic-dance n=5（measured + sha256 prefix + orchestral の drive_id）を `examples/calibration/genre/manifest.yaml`
へ取り込み、2 点 stub → orchestral n=6（+Portals）/ electronic-dance n=5 / electronic stub n=1 に拡張。
分離線（brilliance d=6.99 / centroid≈2234 / high_ratio 0.017）が dated log と一致することを検証し、
B-2 brightness split が実 seed で orchestral→cinematic/orchestral 6/6・EDM→bass-music 5/5・誤判定 0 を
恒久ガード化（honesty: full sha256/Suno prompt 本文は未保全のため prefix/PENDING に分離、EDM の Drive 化は
follow-up）。**重要な副産物：seed が実 magnitude `spectral_bands` を repo 内に保有したことで Q1-5 Ph2 の
brightness ルール magnitude 移行が新規音源なしで部分着手可能になった**（次アクション B-3）。
残るは解像度向上系（B-3 brightness magnitude 移行 / Phase C 本物アンカー増強 / 3ジャンル目以降）。標準コンテキスト:
目的2（再現実証）R0–R5・R1-audio・Q1-5 Ph1 は closeout 済、外部律速の残キューは K2（人間生成バッチ）/
Q1-5 Ph2 の最終校正コーパス breadth（licensing）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| Genre Calib B-3 | brightness ルールを magnitude `brilliance` へ移行 | P1 | B-2 の暫定 power `high_ratio>0.017` を最強判別子 `spectral_bands.brilliance`(d=6.99 / thr≈0.1537)ベースへ置換。#103 seed が実 magnitude 帯域を repo 内に保有したため新規音源なしで着手可。要設計: `spectral_bands` Optional の後方互換(欠落時は既存 power 経路へ fallback)・回帰は方向性のみ固定・misfire audit で before/after 対比。Design Memo 起案済(本セッション) |
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P2 | B-3 で brightness 系は magnitude 移行。残=他 power 帯域(`low/mid_ratio`)是正 + `screen_2026-06-16.yaml` 再採取 + `test_metamorphic_probe.py` の `high_ratio==0.0` 前提見直し + 校正コーパス breadth(licensing 律速)。Phase 1=#91 マージ済 |
| Genre Calib Phase C | 本物アンカー増強 + bias 検証 | P2 | 分離線は Suno 生成中心(本物 Portals 1点)。実 orchestral/EDM 録音を各数本 content-address 登録し閾値の generator bias(特に dynamics)を測定・補正。律速=本物音源 licensing。`docs/genre_calibration_planning.md` Phase C |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 3rd genre | rock / acoustic へ域拡張 | P3 | planning §6 次アクション#3。Suno 生成バッチ仕様(ジャンル/曲数/プロンプト→ラベル)確定 + manifest 拡張。律速=人間生成バッチ |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #103 | feat(calibration): genre seed manifest を実 Suno 実測 10 本で確定（orchestral n=5 + electronic-dance n=5 を measured インライン保全・2 点 stub→n=12・B-2 split の誤判定 0 を恒久ガード化） | 2026-06-25 | Genre Calib seed |
| #102 | feat(semantic): brightness で orchestral/bass-music を分離（B-2・`low_ratio>0.4` を `high_ratio` 0.017 で明暗二分・管弦の bass-music 誤判定を是正・synth 不変） | 2026-06-25 | Genre Calib B-2 |
| #101 | feat(calibration): genre misfire 監査（現行ルールを校正コーパスに適用し混同表を出す計器・verdict なし） | 2026-06-25 | Genre Calib B-1b |
| #100 | feat(calibration): ジャンル校正ハーネス（genre manifest + 分離度/閾値候補レポート + `insufficient` ゲート + `spectral_bands.*` ドット key 解決） | 2026-06-25 | Genre Calib B-1 |
| #99 | refactor(semantic): ジャンル/楽器推定の config 化（Phase A・厳密振る舞い保存・条件エンジンに `_gt`/`_lt` 追加・packaged 補完・Codex P2×2 解決） | 2026-06-24 | Genre Calib Phase A |
| #98 | docs(genre-calibration): 意味層ジャンル語彙拡張の planning doc（Tier1/2/3・Suno 校正コーパス方針） | 2026-06-24 | Genre Calib |
