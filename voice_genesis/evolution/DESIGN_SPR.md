# DESIGN SPR — Selection Pressure Routing（淘汰圧の配線）v0

- 起草: 2026-08-19（User 叩き台 + Fable 精緻化・User 採用 2026-08-19
  「評価軸は早急に整備する。早期整備が手戻りコストを最小にする」）。
  同日、User 起草の理論正本
  [`../foundry/VISION_evolution_v0.3_supplementA_spr.md`](../foundry/VISION_evolution_v0.3_supplementA_spr.md)
  （v0.3 補論 A）の収載に合わせて**統合再構築**
- 役割分担: **補論 A = 理論正本**（評価値 4 分類・原則 10 か条・強度順序の
  出典）/ **本書 = 実装側の配線契約**（Routing 表・隔離規則・既存実装との
  接続・評価器の現在地）。食い違ったら実装の数値・schema は本書が正、
  思想は補論 A が正
- 関連実装契約: [`DESIGN_VG_E0.md`](DESIGN_VG_E0.md)（Archive/追い出し/保護
  スロット）、[`DESIGN_VG_L0.md`](DESIGN_VG_L0.md)（学習回路・Level 定義）、
  [`../foundry/DESIGN_S4_run5.md`](../foundry/DESIGN_S4_run5.md)（④ = 第 0 世代）
- 位置づけ: **生態系の全ゲートの評価設計の配線正本**。VG-E1 の評価軸凍結・
  ④の審査・VG-L0 の判定は、本書の Routing 表の「行の実装」として行う

## 0. 原理（補論 A の核心 3 点 + 本書の追加 1 点）

1. **一回路一主選択圧**（補論 A §10-1）: 評価軸を増やすのではなく、
   各回路・各タイミングに一つの主選択圧を割り当てる
2. **評価値 4 分類を同格の圧にしない**（補論 A §2）: Gate（制約）/
   Absolute（生存 Floor）/ Relative（成熟個体の主選択圧）/ Developmental
   （発達形質）= **2 つの制約 + 1 つの主選択圧 + 1 つの発達形質**。
   `Score = wG·G + wA·A + wR·R + wD·D` 型の総合点化は禁止
3. **強度順序**（補論 A §5）: `P_birth < P_T ≲ P_L < P_E`。
   S は強度でなく**低頻度・高影響**
4. **回路ごとに淘汰の単位が違う**（本書の追加・多重レベル淘汰の人工版）:

| 回路 | 淘汰されるもの |
|---|---|
| E: Birth / Reproduction | **個体**（genome） |
| L: Training | **Revision**（同一個体内の版・自己比較） |
| T: Teaching | **Lesson**（Δ・教材 — 生徒でなく教材を淘汰する） |
| Archive: Eviction | **個体**（在庫からの追い出し） |
| S: Backbone | **世界**（全個体の共有足場） |

## 1. 評価値 4 分類と保存形（補論 A §1・§9 の実装読み）

- **Gate** `G(v) ∈ {PASS, FAIL}`: Rights/Provenance・Artifact・Replay・
  Determinism・Identity audit・Hidden audit。**能力点に変換しない・
  他スコアで相殺しない**
- **Absolute** `A(v)`: 固定基準に対する軸別性能（pitch/明瞭度/自然さ/
  ノイズ率等）。主用途 = **Quality Floor**（僅差で繁殖率を変えない）
- **Relative** `R(v | P, N)`: 集団・niche 文脈でのみ定義（niche 内順位・
  A/B 勝率・novelty percentile・human preference）。Singer × 比較文脈の関数
- **Developmental** `D(v, C) = A(after) − A(before)`: LearningGain・
  TransferGain・学習速度。「今強い」と「伸びる」を分離する
- 保存形 = `EvaluationState(v) = {GateState, AbsoluteVector,
  RelativeContext, DevelopmentalVector}`（単一 TotalScore を保存しない）。
  **schema 実装との関係**: 現行 `evaluation-record/0.1`（VG-E0 凍結）の
  `axes` は AbsoluteVector 相当のみ。Relative/Developmental の保存は
  VG-E1 実装時に**新 schema または版番改訂**で導入し、VG-E0 の凍結 3 種は
  非改変とする。なお「axes = AbsoluteVector 相当」は本書の**解釈**
  （VG-E0 本文は「軸名→値・語彙は VG-E1 で凍結」としか規定しない。
  `verdict` が Gate 判定を担う構造との併せ読みによる推論であり、
  VG-E1 の軸凍結時に正式化する）

## 2. Routing 表（v0 凍結・種別 = 主圧 / 制約）

「評価器（現在地）」列は正直会計 — 現時点の校正済み評価器は
**User の耳（ブラインド様式）のみ**。自動化は VG-L0 §2 の**制御軸単位の
解禁**規則に従い行ごとに段階導入する。

| # | 圧 / 資格 | 種別 | 回路/時点 | 強度・頻度 | 淘汰単位 | 分類 | 評価器（現在地） | 読んではならない信号（隔離） |
|---|---|---|---|---|---|---|---|---|
| 1 | Viability | 主圧 | E: 個体生成時（④含む） | **弱**・生成毎 | 個体 | Gate + 最低限 Absolute | 機械検査（validator/pin・Rights・Genome/provenance 有効・Replay 可能性）+ 耳の粗判定（致命破綻・最低限の発声成立） | 成熟品質軸・稽古由来の成績（**「出生では殺しすぎない」= S2 型誤配線疑いの再発防止**） |
| 2 | LearningGain | 主圧 | L: 稽古 commit 時 | 中・遷移毎 | Revision | Developmental（+ Gate: Identity/Replay・Absolute 非悪化） | 耳のブラインド順位付け（VG-L0）→ 軸単位で自動化 | niche 相対軸（**自己との競争** — 他個体と比較しない） |
| 3 | Transferability | 主圧 | T: Lesson 選別時 | 中・Lesson 毎 | Lesson (Δ) | **Developmental（各生徒の TransferGain）→ Relative（Lesson 間選抜）**の二段 | **未整備**（1:N の before/after 差分を保存し、その分布を根拠に Lesson 間比較 — 将来。General / Niche-Specific に分けて保存・総合 1 点化しない） | 単一個体の成績のみでの裁定 |
| 4 | Quality Floor | **制約（繁殖資格）** | 成熟判定（Archive 参加前提） | 中〜強・成熟判定時 | 個体 | Absolute（Floor 用途） | 耳。軸の凍結 = VG-E1（LRA 単独禁止・ノイズ/区間レベル軸必須） | 稽古予算の個体差（**均一カリキュラム後のみ評価** — H16 会計） |
| 5 | NicheRelativeAdvantage | 主圧 | E: 繁殖・Archive 挿入 | **最強**・世代毎 | 個体 | Relative（niche 相対） | 耳 + VG-E1 軸（未凍結）。処理 = Floor → Niche assignment → Relative competition → Elite/QD Archive → Parent pool | グローバル 1 位比較（異 niche を単一点で比較しない・総合スコア恒久禁止） |
| 6 | Eviction | **行 5 の実行機構**（独立の主圧ではない — 挿入と追い出しは同一操作の表裏） | Archive 常時 | 行 5 に従属 | 個体 | Relative（niche 相対） | 機構は VG-E0 実装済み（記録付き追い出し・EvictionEvent）。**ただし現行 Archive は呼び出し側供給の scalar `quality` を順序キーに使用**（archive.py `submit(genome, quality, quality_floor)` 実測）— SPR 準拠 comparator（軸別 evidence・niche 相対）の意味論は **VG-E1 で凍結** | **保護スロットは行 5 の圧からの明示的免除区域**（免除の事実と理由を記録） |
| 7 | PopulationLevelImprovement | 主圧 | S: Backbone/評価器更新（現行 = run 単位スクラッチ再学習） | **低頻度・高影響**・run 毎 | 世界 | **Developmental（Population-level Δ）+ Gate/Absolute 制約** | Design Memo 裁定 + 4 ゲート + 節目耳ゲート。parent/child Backbone 間で **ΔCoverage（Quality-Constrained）・Δ故障率・ΔHack 再発率**等を比較し、critical niche survival・Hidden/Human alignment・Replay 安定性は**制約**として扱う（補論 A §3.5 の実装読み） | 「最高個体が強くなったか」（問いは**成立領域が広がったか**）。更新後は全既存評価 revalidation required |

## 3. 隔離規則（圧の漏れの禁止・v0 凍結）

1. **繁殖圧 → 出生への漏れ禁止**: 行 1 は viability のみ。出生時に行 4/5 の
   軸を適用しない（S2「0/10」再解釈 = この誤配線の疑い。④で繰り返さない）
2. **育成圧 → 繁殖判定への漏れ禁止**: 行 4/5 の評価は**同一カリキュラム・
   同一稽古予算の後**の状態でのみ行う（相続格差の防止・VG-L0 H16 会計）
3. **教育圧の単位規則**: 行 3 は Lesson を裁く。1 個体の成績で Lesson を
   採否しない（1:N の転移成績が正）
4. **Developmental の加算禁止**（補論 A §4）: 可塑性を繁殖点へ直接加算
   しない。**Behavior Descriptor** として保存する（high-performance elite と
   high-plasticity lineage を別特性として両方残す）。descriptor の Archive
   合流は VG-E0 グリッド（N=5 凍結）の版番改訂事項 = VG-E1 以降で裁定
5. **時間分離**（補論 A §6）: 同一個体に 4 種の圧を同時にかけない
   （出生→生存 / 学習→Revision 採用 / 教育→Lesson 採用 / 成熟→繁殖選抜 /
   世代間→Backbone 更新）
6. **免除区域**: 保護スロット（VG-E0 §3.2）は行 4–6 の圧を意図的に免除。
   免除の記録必須
7. **meta 圧（横串）**: Hidden 評価器 / Hack DB は特定行に属さず、
   **強いゲート（行 4/5/7）と Level 2 自動化が導入された全行**に横串適用
   （Training 評価器単独 commit の禁止 = VG-L0 §5 禁止線 4）

## 4. 因果帰属（補論 A §7 = 既存規律の再確認）

評価は点数付けでなく**原因特定の計器**。帰属時は一度に 1 種類の Edge のみ
変更し、他レイヤーの hash（Genome/Backbone/score/seed/Probe/
ExecutionProfile）を固定する — 単一介入原則（DESIGN_S3_backfill）・
v0.2 §6.1 A/B contract と同一。`G, L, T, S → A → R`・`D = ΔA`。

## 5. 実装対応（追加工事ゼロ — 計画済み作業への名札付け）

| Routing 行 | 実装先 | 状態 |
|---|---|---|
| 行 1（Viability） | **④ 第 0 世代の審査**（run 5 checkpoint・DESIGN_S4 §4） | **即適用**: ④の耳判定は行 1 の弱圧のみ。**「破綻なし・合成成立」を viability 判定に使用**し、「第三の声か否か」「ノイズ・区間レベルの定性」は**観測・記録のみ**（出生時の reject 条件に使用しない — novelty/品質として効くのは後段の行 4/5）。DESIGN_S4 §4 末尾の耳判定総則（LRA 単独禁止・ノイズ/区間定性の必須併置）は①〜③の品質判定向けであり、④への適用は本書の行 1 配線が優先する（評価設計の正本 = 本書） |
| 行 2（LearningGain） | VG-L0 最小実験（ブラインド順位付け） | 設計済み・run 5 後に実施 |
| 行 3（Transferability） | 1:N 教育（VG-L 将来） | 未整備（校正器ごと将来） |
| 行 4/5（Floor / Reproduction） | **VG-E1 の評価軸凍結** | VG-E1 の作業定義そのもの。行 4（Absolute Floor）と行 5（niche 相対）を区別して宣言する |
| 行 6（Eviction） | VG-E0 Archive 機構 | 機構は実装済み（EvictionEvent 記録付き）。SPR 準拠の niche 比較規則（scalar quality キーの置換）は VG-E1 で凍結 |
| 行 7（S） | S 系列 Design Memo + 4 ゲート | **既に運用中**（run 5 が現物） |

## 6. Acceptance Criteria（SPR v0 出口）

- [ ] ④の審査記録が行 1 の圧のみで書かれている（行 4/5 軸の混入なし —
  審査様式に本書参照を明記）
- [ ] VG-E1 の評価軸凍結が行 4（絶対 Floor）と行 5（niche 相対）を区別して
  宣言している
- [ ] VG-L0 の判定記録が行 2 として記帳されている（niche 相対軸の不使用を
  明記）
- [ ] 表・隔離規則の改訂は本書の版番改訂 PR による（行の具体軸値の凍結は
  各実装契約側。本書は配線のみを凍結する）

## 7. 総合スコア禁止の継承（恒久・再掲）

いかなる行の評価器も総合 1 点スコアを持たない（M3 裁定 → VG-E0 schema
強制 → 補論 A §2 の三重の継承）。行 5 の「最強」は軸別 evidence +
niche 相対比較の強さであって、単一数値の最大化ではない。

> 出生では殺しすぎない。学習では昨日の自分と競わせる。教育では教材を
> 競わせる。成熟後に他個体と強く競わせる。種の更新は生態系全体で判定する。
> （補論 A §11 の最短原則）
