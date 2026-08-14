# S3b Genesis Report — 多世代 Genesis Graph 追補（identity 床付き）

- 日付: 2026-08-13
- 仕様: コーディネーター指示メッセージそのもの（新規メモなし。逸脱・補充は
  `underspec_log_s3b.md`）
- 実装: `singer/genesis_v1.py`（`genesis_v0.py` を import 流用）
- 背景: S3 当選者 genesis1 は親 voice_C から複合 JND <1（0.29）で耳判定素材
  として不十分な可能性が高いと自己申告済み（`results_s3/genesis_report.md`）。
  原因診断: `TRACT_MUTATE_SCALE=0.025` の 1 世代探索では安全域内を遠くまで
  歩けない → 本サイクルで **G=4 世代の貪欲坂登り**に拡張して再挑戦した。

## 実行設定

- 世代数 G=4、世代内候補数=8（[UNDERSPEC-S3B-1]: 12 ではなく 8 に事前調整。
  多世代 × 再現性照合の 2 回実行で時間超過するリスクを避けるための判断。
  実測では 1 回のフル実行が約 2m47s、2 回実行（再現性照合込み）で
  約 5m05s と、15 分規模の枠に対して余裕を持って収まった）
- 安全域ボックス: tilt [-17,-7] / bandwidth_scale [0.80,1.30] /
  breathiness_base [0,0.40] / register_gains 各要素 [0,0.50] /
  vibrato_rate [4.5,7.0] / vibrato_depth [20,60]。formant_scale=1.0 固定
  （U1 fail-closed 継続）
- 摂動歩幅は S3 と同一規約: tract 系（tilt/bandwidth_scale/breathiness_base）
  固定 scale=0.025、音色系（register_gains/vibrato/jitter）scale∈{0.08,0.15}
- 識別床: JND 複合距離（S3 と同一定義、mean_f0 を除く 5 特徴の中央値の平均、
  voice_C・voice_D のうち近い方を採用）**両方に対し ≥2.0**
- 親選択: 生存者を distinctiveness（複合距離）降順・candidate_id 昇順で
  ソートし上位 min(4, 生存数) を次世代の親に採用（貪欲坂登り）

## 世代ごとの距離推移

| 世代 | 候補数 | 生存数 | 淘汰内訳 | 生存中の最大 distinctiveness | 床(≥2.0)到達 |
|---|---|---|---|---|---|
| 1 | 8 | 1 | linkability_fail×5, quick_s5_fail×2 | 0.642 | なし |
| 2 | 8 | 5 | linkability_fail×3 | 1.103 | なし |
| 3 | 8 | 7 | linkability_fail×1 | **1.723** | なし |
| 4 | 8 | 8 | なし | 1.628 | なし |

各世代トップ3個体（distinctiveness降順）:

| 世代 | id | 個体名 | op | dist(複合) | dist_vs_C | dist_vs_D |
|---|---|---|---|---|---|---|
| 1 | 4 | g1-4-mut | mutate | 0.642 | 0.642 | 2.102 |
| 2 | 18 | g2-18-mut2 | mutate | 1.103 | 1.103 | 2.085 |
| 2 | 19 | g2-19-mut2 | mutate | 0.717 | 0.717 | 1.839 |
| 2 | 16 | g2-16-mut2 | mutate | 0.665 | 0.665 | 2.225 |
| 3 | 26 | g3-26-cross | crossover | **1.723** | 1.723 | 2.092 |
| 3 | 20 | g3-20-mut | mutate | 1.156 | 1.156 | 2.119 |
| 3 | 21 | g3-21-mut | mutate | 0.951 | 0.951 | 1.861 |
| 4 | 29 | g4-29-mut | mutate | 1.628 | 1.628 | 1.821 |
| 4 | 32 | g4-32-cross | crossover | 1.449 | 1.449 | 2.187 |
| 4 | 34 | g4-34-cross | crossover | 1.225 | 1.225 | 1.891 |

**観測された非対称性**: 全世代を通じて `dist_vs_D` は 2.0 前後〜2.4 に容易に
到達する一方、`dist_vs_C`（複合距離のボトルネック）は世代 3 の 1.723 が
最高値で、床の 2.0 に届かなかった。原因分析: voice_C は安全域ボックスの
tilt/bandwidth_scale/breathiness_base いずれも **境界値ちょうど**
（tilt=-17=下限, bw=0.80=下限, breathiness=0.0=下限）に位置するため、
ボックス内でこれらの軸方向に「C からさらに離れる」余地は voice_D 側
（tilt=-10, bw=1.30=上限, breathiness=0.40=上限）へ向かう一方向しかない。
D は境界からやや内側（tilt）にいるため、D から離れる方向にはまだ余地が
残っており、非対称な到達しやすさが生じたと考えられる。

## 識別床の判定: **未達（fail-closed）**

G=4 世代・候補32個（初期集団4含め計36個体）を尽くしても、
「voice_C・voice_D 両方に対し JND 複合距離 ≥2.0」を満たす生存個体は
出現しなかった（全世代を通じた最大値は世代3の 1.723、床まで 0.277 不足）。
仕様どおり fail-closed とし、最良個体の数値を記録した上で終了する。

### 全世代を通じた最良個体（distinctiveness 最大）

- candidate_id=26（世代3, `g3-26-cross`, crossover 由来）
- distinctiveness_composite = **1.723**（vs voice_C）、vs voice_D = 2.092
- **フル S5 gate1-6 は不合格**（gate6 崩壊。詳細は下記フル gate 表参照）
  → 床未達に加えゲートも通らないため、この個体は当選者にも
  「参考個体」にもなれない

## 当選者選出（fail-closed フォールバック）

床を満たす個体がないため、仕様の規定通り「最良個体とその数値を記録して
終了」する。ただし deliverable の `sakura_genesis2.wav` は「床未達なら
最良個体で生成し『参考』明記」との指示に従い、**distinctiveness 降順に
フル S5 gate1-6 を順に試し、最初に全通過した個体**を参考個体として採用した
（`genesis_v1._try_full_gates_by_distinctiveness`）。

### フル gate 試行順（distinctiveness 降順）

| 試行順 | candidate_id | 個体名 | dist(複合) | フル gate1-6 |
|---|---|---|---|---|
| 1 | 26 | g3-26-cross | 1.723 | 不合格 |
| 2 | 29 | g4-29-mut | 1.628 | 不合格 |
| 3 | 32 | g4-32-cross | 1.449 | 不合格 |
| 4 | 34 | g4-34-cross | 1.225 | 不合格 |
| 5 | 20 | g3-20-mut | 1.156 | 不合格 |
| 6 | 28 | g4-28-mut | 1.117 | 不合格 |
| 7 | 18 | g2-18-mut2 | 1.103 | 不合格 |
| 8 | 35 | g4-35-cross | 1.060 | 不合格 |
| 9 | 21 | g3-21-mut | 0.951 | 不合格 |
| 10 | 31 | g4-31-mut | **0.895** | **合格** ← 参考個体（genesis2） |

distinctiveness 上位 9 個体が軒並みフル gate 不合格という結果は、S3 の
知見（gate6 の安全域は多次元同時摂動に対して非常に脆い）を追加で裏付けた
（詳細は `underspec_log_s3b.md` [UNDERSPEC-S3B-2]）。

## 参考個体: genesis2（内部名 `g4-31-mut`, candidate_id=31, 世代4）

**注意: この個体は識別床（複合距離≥2.0）を満たしていない（参考データ）。**

- 系譜: mutate(親, seed, scale=0.15 or 0.08 — 世代4の親プール由来。
  詳細な親チェーンは `lineage_genesis2.json` の genome 全パラメータ参照)
- distinctiveness_composite = **0.895**（vs voice_C=0.895, vs voice_D=1.855）
  — S3 の genesis1（0.29）からは改善したが床の 2.0 には遠い
- linkability: 合格（margin 詳細は `lineage_genesis2.json`）

### フル S5 ゲート表（genesis2）

| gate | 結果 |
|---|---|
| gate1 F0追従 | ✓ |
| gate2 plausibility | ✓ |
| gate3 子音実在 | ✓ |
| gate4 決定論 | ✓ |
| gate5 aliasing | ✓ |
| gate6 grip | ✓ |
| **全通過** | **✓** |

### 再現性照合

`run_multigen()` を独立に 2 回実行し、参考個体（candidate_id=31,
`g4-31-mut`）の Genome 全フィールドが完全一致することを機械照合済み
（全世代の生存者集合・最良個体 candidate_id=26 の distinctiveness=1.723
も両実行で一致）。

## 総括

- 多世代化（G=4・貪欲坂登り）により最良 distinctiveness は S3 の 1 世代
  探索（genesis1: 0.29）から **1.723 まで改善**したが、識別床 2.0 には
  未達。原因は S2/S3 実測に基づく安全域ボックスが voice_C の実座標を
  境界に貼り付けた形で定義されているため、C から離れられる物理的な
  「歩ける距離」自体がボックス内で頭打ちになっていること
- フル S5 gate（特に gate6）は distinctiveness が高い個体ほど不合格に
  なりやすい傾向が明確に観測された（上位9個体が全滅）。「識別床を満たす
  こと」と「gate6 を通ること」はこの安全域ボックスの中でも依然として
  トレードオフの関係にある
- 次サイクルへの示唆: 識別床を達成するには (a) 安全域ボックス自体を
  voice_C の座標を内部に含む形に再定義する（境界貼り付き問題の解消）、
  (b) tract 系の gate6 安全域をさらに特性化してボックスを広げる、
  (c) 複合距離の定義を「tract 系のみ」から「音色系を含めた重み付け」へ
  見直す、のいずれかが前提条件になる
