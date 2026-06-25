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
真因を statistically 確定。dynamics は Suno 生成バイアス（広ダイナミクス再現不可）で overlap。次手は
B-2（brightness ベースの orchestral 判定ルールを `semantic_rules.yaml` へ導出反映）+ seed manifest 確定
+ 本物アンカー増強（Phase C）。標準コンテキスト: 目的2（再現実証）R0–R5・R1-audio・Q1-5 Ph1 は
closeout 済、外部律速の残キューは K2（人間生成バッチ）/ Q1-5 Ph2（校正コーパス licensing）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正 | P2 | 既存 power 3帯域(`low/mid/high_ratio`)の是正 + `semantic_rules.py`/config 閾値(`low_ratio_min:0.4`/`mid_ratio_min:0.45`/`high_ratio>0.3`)を検証済 magnitude `spectral_bands` 基準へ移行 + `screen_2026-06-16.yaml` 再採取 + `test_metamorphic_probe.py` の `high_ratio==0.0` 前提見直し。閾値再導出の校正コーパスが R1-audio 同様 licensing 律速。Phase 1=#91 マージ済 |
| Genre Calib B-2 | brightness ルールを `semantic_rules.yaml` へ導出反映 | P1 | 2026-06-25 の分離線実測で処方箋確定: `bass-music` の `low_ratio>0.4` 判定をやめ／補強し、orchestral を**低 brightness**(`spectral_bands.brilliance<≈0.15` or `spectral_centroid<≈2234` or `high_ratio<≈0.017`)で判定。harmonic / low_ratio は overlap で不可。閾値は candidate（Suno×Suno+本物1点）なので暫定明記。Design Memo 起案が次手。before/after は #101 misfire 監査で対比 |
| Genre Calib seed 確定 | orchestral+EDM 実測を seed manifest 化 | P2 | 2026-06-25 抽出済(orchestral n=5 sha256+drive_id・EDM n=5 sha256のみ/Drive 未up)を `examples/calibration/genre/manifest.yaml` へ取り込み(現状 Portals/UZA 2点 stub)。measured 値は 2026-06-25 dated log に記録済。EDM の Drive 化 + `prompt:` 本文転記が要 |
| Genre Calib Phase C | 本物アンカー増強 + bias 検証 | P2 | 分離線は Suno 生成中心(本物 Portals 1点)。実 orchestral/EDM 録音を各数本 content-address 登録し閾値の generator bias(特に dynamics)を測定・補正。律速=本物音源 licensing。`docs/genre_calibration_planning.md` Phase C |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #101 | feat(calibration): genre misfire 監査（現行ルールを校正コーパスに適用し混同表を出す計器・verdict なし） | 2026-06-25 | Genre Calib B-1b |
| #100 | feat(calibration): ジャンル校正ハーネス（genre manifest + 分離度/閾値候補レポート + `insufficient` ゲート + `spectral_bands.*` ドット key 解決） | 2026-06-25 | Genre Calib B-1 |
| #99 | refactor(semantic): ジャンル/楽器推定の config 化（Phase A・厳密振る舞い保存・条件エンジンに `_gt`/`_lt` 追加・packaged 補完・Codex P2×2 解決） | 2026-06-24 | Genre Calib Phase A |
| #98 | docs(genre-calibration): 意味層ジャンル語彙拡張の planning doc（Tier1/2/3・Suno 校正コーパス方針） | 2026-06-24 | Genre Calib |
| #97 | feat(R1): screen 由来の実音源 calibratable レコードを R1 箱 manifest に取り込み（箱を screener 経路 canonical 化 + Codex P2 で repo-root locator 解決） | 2026-06-24 | R1 |
