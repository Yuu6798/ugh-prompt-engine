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
brightness ルール magnitude 移行が新規音源なしで部分着手可能になった**。続けて **B-3（#104）で
brightness 判定を magnitude `brilliance` へ移行 closeout**：collect-all エンジンに feature 不在を条件化する
`_absent` 演算子を新設し、`spectral_bands` あり→`brilliance`(thr≈0.1537) 一次判定 / 欠落→既存 power
`high_ratio` 経路へ fallback の相互排他を実現（bands-absent 既存回帰テストは無改変で green、二重発火なし、
snapshot 不変、seed confusion 6/5 維持）。post-merge 検証で ruff + 66 tests green を確認。
**2026-06-26 に 3 ジャンル目 rock を追加（#105）**：Suno rock n=5 を抽出し `brilliance` 単独が
3 ジャンルを重なりゼロ 3 バンド分離（orch≤0.0954 / rock 0.139-0.196 / EDM≥0.2119）することを発見、
旧単一閾値 0.1537 が rock クラスタ中央を貫き bass-music/orchestral に裂く欠陥を audit で実証し、
gap 中点 0.117/0.204 の 3way へ是正（audit に rock 期待値・既存 orch6/EDM5 回帰ゼロ・Codex P2 の
境界穴を `_min` 化で対応・full sha256/prompt 本文記録で過去の prefix-only/PENDING を rock で解消）。
さらに **R2-2 BPM halving を調査（#106）**：punk 175→123 は `load_audio` 22050 リサンプル×
start_bpm=120 prior 起因（native 48k は 181 回復）で、閾値低下（ratio punk1.057<BPM 正検出
indie1.098）も sr 上げ（native は synth_05 を半化）も大域回帰と実測確定し、単一ノブ修正を見送って
診断を docs finding #6 に保全。**2026-06-28 に Q1-5 Ph2 を 2 PR で実質クローズ**：#107 で
metamorphic probe の `high_ratio==0.0` 前提を再点検し magnitude `brilliance` も合成器では盲
（grip span≈9e-4・非ゼロ floor で平坦）を回帰ガード化、#108 で low/mid_ratio を seed pair
separability 実測し **`low_ratio` はゲートで健全（全 >0.4・全ペア overlap）・magnitude 低域は
部分分離（全ペア単独判別は brilliance のみ）・general アンカー欠如でゲート境界の magnitude
再導出不能** と確定して `low_ratio` は power 据え置き closeout・`mid_ratio` は production 閾値
未発火で評価不能・繰越とした（production rule 不変）。重要な honesty caveat：magnitude 判別軸と
mid_ratio の所見は実アンカー portals が当該欄を欠くため**全ジャンル Suno のみ（実 grounding
ゼロ）**で、Phase C はゲート境界拡張だけでなく magnitude 軸の generator bias 検証にも必須。
**同日 Session 2 で Genre Calib Phase C を 3 PR 着手＝Suno vs 本物の generator bias を実証**：
ユーザー提供の実音源で repo 初の実 grounding を取得し、#109（実 J-POP 3 本 real anchor 化）→
#110（同ジャンル/キー matched-pair で over-brightening を交絡排除し確定・brilliance candidate
d=3.76・スマイリー EQ）→ #111（本物 orchestral/rock/EDM 各 1 本で cross-genre 検定）。**核心の
発見**：Suno の brilliance over-brightening は**ジャンル横断で一定でない**（orchestral/EDM は
Suno と一致・rock/J-POP は Suno が明るすぎで符号反転）＝単一補正係数は不可。一方 **mid 削り
（mid_ratio 本物>Suno）+ 脱トーナル化（harmonic 本物<純Suno, EDM 最大 −0.43）は全ジャンルで
方向一定**＝補正の手がかり。ボーナス：本物 Star Wars の low_ratio=0.388<0.4 で `low_ratio>0.4`
ゲートを通らず、#108 で健全とした low ゲートは Suno の人工的低域厚に依存と判明。残るは
**各ジャンル本物 ≥3 本に増やして mid/harmonic の補正係数を分布として定式化**（律速=ユーザー
追加 lossless 購入）/ 4 ジャンル目 acoustic / Q1-5 Ph2 の screen_2026-06-16 再採取。標準
コンテキスト: 目的2（再現実証）R0–R5・R1-audio・Q1-5 Ph1/Ph2 は closeout 済、外部律速の残キューは
K2（人間生成バッチ）/ Phase C 補正係数化（本物音源 breadth・licensing）。

## Next-Issue Queue

| ID | Title | Priority | Notes |
|---|---|---|---|
| K2 | Suno 転移検証 | P1 | K1 の tight/loose 判定が Suno/Udio 級で転移するか手動少数バッチ。物理固定・意味差替で「別物」生成を確認済(意味層 grip 有り・耳)だが n=1。律速は人間生成バッチ。controllability_poc.md §5 |
| Q1-5 Ph2 | spectral 帯域 magnitude 再校正(残) | P3 | **大半 closeout 済**: brightness=B-3 / `high_ratio==0.0` 前提見直し=#107 / low/mid_ratio 据え置き finding=#108(low はゲートで健全・mid は未発火で繰越・general アンカー欠如)。残=`screen_2026-06-16.yaml` 再採取 + mid_ratio/magnitude 軸の bias 検証(Phase C と統合・licensing 律速)。**BPM**: #106 で sr/閾値の単一ノブ修正は大域回帰と確定(finding #6)。自動化は multi-prior/sr ensemble+octave tie-break を全 fixture 回帰検証付き Design Memo 化 |
| Genre Calib Phase C | 本物アンカー増強 + bias 補正係数化 | P2 | **着手済(#109/#110/#111)**: 実 J-POP n=3(real)+n=3(suno matched)、orchestral-real/rock-real/edm-real 各 n=1 を登録。発見=brilliance bias はジャンル非一定(単一補正不可)・mid 削り+脱トーナル化は方向一定。残=**各ジャンル本物 ≥3 本に増やし mid/harmonic 補正係数を分布として定式化**(現状 n=1 は方向シグナル)。律速=ユーザー追加 lossless 購入(audio は repo 外・measured+sha256 保全)。`docs/genre_calibration_planning.md` Phase C |
| Genre Calib seed follow-up | EDM 5本の Drive 化 + Suno prompt 転記 | P3 | #103 で measured はインライン保全済。残=`electronic_dance_*` の Drive アップロード→`drive_file_id` 回収 + `prompt:` 本文の PENDING 解消。律速=人間作業 |
| Genre Calib 4th genre | acoustic へ域拡張 | P3 | acoustic(low_ratio<0.4 の general 落ち領域を突く coverage 拡張型)を rock(#105)と同じ協働フロー(物理層明示プロンプト→user 生成→抽出→分離分析→ルール反映)で追加。律速=人間生成バッチ |

## Recently Merged

| PR | Title | Date | Phase |
|---|---|---|---|
| #111 | feat(calibration): cross-genre で Suno 指紋の一定性を検定＝単一補正係数は反証（本物 orch/rock/EDM 各1本 vs 純Suno・brilliance bias 符号反転=非一定・mid削り+脱トーナル化は方向一定・low ゲートは Suno 低域厚バイアス依存・Codex P2×2=audit 配線/純Suno baseline・rule 不変） | 2026-06-28 | Genre Calib Phase C |
| #110 | feat(calibration): 本物×Suno matched-pair で generator bias を genre-controlled 実証（同ジャンル/キー対・brilliance candidate d=3.76・スマイリーEQ=低↑中↓高↑・key3/3 BPM2/3 一致・rule 不変） | 2026-06-28 | Genre Calib Phase C |
| #109 | feat(calibration): 実 J-POP 3 本を real anchor 登録＝Phase C 着手・Suno EDM over-brightening 初観測（repo 初の実 grounding spectral_bands・low ゲートは本物で通用・特徴量ごとの実効 n 明示・rule 不変） | 2026-06-28 | Genre Calib Phase C |
| #108 | docs(genre): low/mid_ratio は power 据え置き＝Q1-5 Ph2 移行は不要/評価不能と実測（`low_ratio` ゲート健全・magnitude 低域は部分分離で全ペア判別は brilliance のみ・mid は production 閾値未発火で繰越・magnitude 軸は Suno-only grounding を caveat 化・Codex 5 ラウンド・rule 不変） | 2026-06-28 | Q1-5 Ph2 |
| #107 | test(probe): magnitude brilliance も合成器では盲を計器/回帰ガード化（`high_ratio==0.0` 前提再点検・power≡0/magnitude 非ゼロ floor で平坦 grip≈9e-4・ノブ energy は mid 帯へ・centroid のみ live sensor・rule 不変） | 2026-06-28 | Q1-5 Ph2 |
