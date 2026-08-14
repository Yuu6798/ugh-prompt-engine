# S4 Genesis Report C — gate6 score-informed QC 再設計 + 第三の歌手の再鍛造

- 日付: 2026-08-13
- 対応 memo: `s4_design_memo.md`
- 実装: `singer/gate_checks_v2.py`（新設）、`singer/genesis_v2.py`（新設、
  `genesis_v1.py` を import 流用）
- 前提: S3b 耳判定で複合 0.9 JND ≈ 知覚境界と較正済み（`results_s3b/s3b_ear_record.md`）

## W1: gate6 の score-informed QC 化

原理（memo）: gate6 は「レンダラが Genome の命令に従っているか」の製造検査
でありブラインド分析ではない。検査者は命令値（楽譜F0・commanded vibrato）
を知ってよい。`gate_checks_v2.py` は periodicity を commanded F0 由来のラグ
で、vibrato_depth を commanded F0 ±200cent 窓での棄却（棄却率記録）で
再実装した（`gate_checks.py` は無改変。gate1-5 はそのまま import 流用）。

**効果確認**: S2/S3 で発見した periodicity クラッシュ（formant_scale=0.95・
高breathiness・A4probe で -19.96dB）が、score-informed 計測では +1.02dB
（正常域）に回復した。クラッシュ性の異常値は根絶された（詳細実測は
`safe_box_v2.md` 参照）。全ての gate6-v2 出力に
`provenance="measured (score-informed QC)"` を必ず付す。

## W2: GAIN_FLOOR の適応化 — fail-closed（レンダラ変更なし）

W1適用後、GAIN_FLOOR 従来値のまま formant_scale を再走査したが
[0.92,1.10] 安全域は**開かなかった**（fs=1.0 のみ安定通過、0.99で早くも
不合格）。GAIN_FLOOR の magnitude を 0.10→0.0 まで振っても改善せず
（fs=1.0 自体が floor を下げると悪化する非単調な依存性を確認）。
memo が示す適応 floor（commanded F0 帯域を避けて floor を置く）は
`formant_tv.py` の関数シグネチャ変更を要し、gate1-5 の voice_A/B/C/D
全数再検証という重い改修になるため、**今回は実装せず fail-closed**とした。
`formant_tv.py` は無改変のまま。レンダラを変更していないため「gate1-5
非退行確認」は該当なし（そもそも変わっていない）。詳細根拠は
`safe_box_v2.md` §W2、判断過程は `underspec_log_s4.md` [UNDERSPEC-S4-1]。

## W3: 安全域ボックス再走査 + voice_B 再測

1次元走査 + 多次元スポットチェックの全データは `safe_box_v2.md` 参照。
要点:

- **voice_B の gate6-v2 再測でクロストーク仮説は反証**: score-informed
  計測でも breathiness grip=1.23 で不合格のまま。S1 の未達はブラインド
  F0 推定の劣化由来ではなく、Genome 自体（tilt=-15, fs=1.12, breathiness
  高め）が periodicity 感度を実質的に下げる領域にあるという genuine な
  挙動だったと判明
- **多次元非単調性の再確認**: voice_C（tilt=-17）は1次元的には安全域外
  だが多次元の組み合わせで合格。逆に **voice_D は S3(v1測定)では合格して
  いたが、v2(score-informed)測定では不合格に転落**——旧 voice_D の gate6
  合格はブラインド計測のアーティファクトだった可能性が高いという重要な
  発見（S3/S3b の安全域ボックスの信頼性そのものへの遡及的な疑義）
- 新ボックス `SAFE_BOX_V2` を凍結（表は `safe_box_v2.md` 末尾）。tilt レンジ
  は皮肉にも S3b（[-17,-7]）より狭い [-13,-8] になった——ボックスの
  絶対的な広さより「クラッシュ由来のアーティファクトを含まない」信頼性が
  今回の実質的な成果

## W4: 第三の歌手の再鍛造

`genesis_v2.py`（`genesis_v1.py` の多世代探索機構を import 流用し、安全域
ボックスと最終フル gate 判定のみ差し替え）で G=4・8候補/世代・規約は
S3b と同一の探索を実行。

### 世代ごとの推移

| 世代 | 候補数 | 生存数 | 淘汰内訳 | 最大 distinctiveness | 床(≥2.0)到達個体 |
|---|---|---|---|---|---|
| 1 | 8 | 3 | linkability_fail×5 | 1.981 | なし |
| 2 | 8 | 7 | linkability_fail×1 | **2.459** | id=12 (2.235), **id=15 (2.459)** |

**世代2で床を達成**（S3bはG=4を尽くしても最大1.723止まりだったのに対し、
新ボックス+gate6-v2では世代2で2.459に到達——新ボックスが「歩ける距離」を
実質的に広げたことを裏付ける）。

生存個体上位（世代2）:

| id | 個体名 | op | dist(複合) | vs C | vs D | linkability margin |
|---|---|---|---|---|---|---|
| **15** | g2-15-mut | mutate | **2.459** | 2.459 | 2.950 | +0.0016 |
| 12 | g2-12-mut | mutate | 2.235 | 2.490 | 2.235 | +0.0044 |
| 19 | g2-19-cross | crossover | 1.345 | 1.345 | 1.461 | +0.0026 |
| 18 | g2-18-cross | crossover | 1.137 | 1.137 | 1.662 | +0.0046 |
| 17 | g2-17-cross | crossover | 1.099 | 1.833 | 1.099 | +0.0053 |
| 13 | g2-13-mut | mutate | 1.097 | 1.097 | 2.070 | +0.0062 |
| 14 | g2-14-mut | mutate | 1.033 | 1.033 | 1.468 | +0.0035 |

床(≥2.0)をクリアした2個体（id=12, id=15）を distinctiveness 降順でフル
gate1-5+gate6-v2 に通したところ、**id=15 が即座に合格**（試行1回で決着。
`lineage_genesis3.json` の `full_gate_attempts` 相当ログ参照）。

## 当選者: genesis3（内部名 `g2-15-mut`, candidate_id=15, 世代2）

**識別床（両親からJND複合距離≥2.0）を初めて達成した個体**:

- distinctiveness_composite = **2.459**（vs voice_C=2.459, vs voice_D=2.950
  — 両方とも床 2.0 を明確に上回る）
- linkability: 合格（margin=+0.0016。詳細は `lineage_genesis3.json`）
- 系譜: mutate 由来（世代2の親プールの一員から seed 派生。完全な genome
  パラメータは `lineage_genesis3.json` 参照）

### フル S5 ゲート表（gate1-5 無改変 + gate6-v2 score-informed）

| gate | 結果 |
|---|---|
| gate1 F0追従 | ✓ |
| gate2 plausibility | ✓ |
| gate3 子音実在 | ✓ |
| gate4 決定論 | ✓ |
| gate5 aliasing | ✓ |
| gate6 breathiness grip (v2) | ✓ (3.829) |
| gate6 vibrato grip (v2) | ✓ (6.320) |
| **全通過** | **✓** |
| gate6 provenance | `measured (score-informed QC)` |

### 再現性照合

`genesis_v2.run_multigen_v2()` を独立に2回実行し、当選者（candidate_id=15,
`g2-15-mut`）の Genome 全フィールドが完全一致することを機械照合済み
（`winner_meets_floor=True` も両実行で一致）。

## 総括

- W1（score-informed QC）はクラッシュ性の異常値を根絶したが、W2（GAIN_FLOOR
  適応化）は formant_scale 安全域を単独では開かず fail-closed とした
- W3 の再走査で voice_D の旧 gate6 合格がブラインド計測のアーティファクト
  だった可能性を発見（S3/S3bの安全域ボックスの信頼性への遡及的疑義）。
  voice_B のクロストーク仮説は反証された
- **新安全域ボックス（tilt軸は狭まったが多次元の信頼性が向上）で、
  多世代探索が S3b では G=4尽くしても未達だった識別床（複合JND≥2.0）を
  世代2で達成**。フル gate1-5+gate6-v2・同一seed再現性も確認済みの
  genesis3 を「明確な第三の歌手」候補として `sakura_genesis3.wav` に出力
- 次サイクルへの示唆: (1) S3/S3b で凍結した voice_C/voice_D 自体を gate6-v2
  で正式に再監査すべき（voice_D は既に不合格転落を確認済み）、(2) 適応
  GAIN_FLOOR の本格実装（formant_tv.py 改修 + gate1-5全数再検証）は
  formant_scale を identity 軸として使いたい場合の前提条件として残る、
  (3) genesis3 の耳判定（S3b の 0.9 JND 較正と比較してどう聞こえるか）が
  次の検証ステップ
