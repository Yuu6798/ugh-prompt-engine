# S3 Genesis Report — Genesis Graph v0（VG-015）

- 日付: 2026-08-13
- 対応 memo: `s3_genesis_design_memo.md`
- 実装: `singer/genesis_v0.py`
- 前提: S2 は人間判定で成立（`results_s2/s2_gate_record.md`）

## U1. gate6 較正域の拡張（診断）

### 診断: formant_scale ±0.01 で gate6 breathiness grip が崩れる機序

`gate_checks.gate6_grip_quick_check` の breathiness sweep（probe: C3/E4/A4/
C5/C6）を voice_A 基準・formant_scale=0.95 で probe 別に生データ確認したところ、
**A4 probe（440Hz）が breathiness=0.5 で periodicity_db_median=-19.96dB へ
クラッシュ**し、F0 推定も 440→452.5Hz（約50cent）へ僅かにずれることを発見。
GAIN_FLOOR（`formant_tv.py`、S1 でオクターブ誤り対策として導入）を一時的に
0 にすると同じ条件で periodicity が 1.81dB に回復する一方、F0 推定が
824.7Hz（≈2×440Hz、オクターブ誤り）に悪化する。GAIN_FLOOR を 0.03〜0.10 で
振っても滑らかな中間点は見つからず（0.07 以下でオクターブ誤り再発、0.08 以上で
periodicity クラッシュ）、**二値的なトレードオフ**であることを実測で確認した。

**切り分け結果**: (b) GAIN_FLOOR との相互作用が主因、(a) 計器（periodicity
推定器のノイズ耐性の閾値効果）が増幅要因、(c) 純粋な実物理ではない
（formant シフト自体は実在の現象だが、崩壊の急峻さは GAIN_FLOOR 由来）。

### 対処判断: fail-closed

GAIN_FLOOR は S1 で voice_A/B の gate1（F0追従）非退行のために導入された
既存の凍結対象に近い定数であり、無理に緩めると gate1（frozen, voice_A/B の
実測基準）を壊すリスクが高い。滑らかな中間解が実測で見つからなかったため、
**formant_scale ∈ [0.92, 1.10] の成功条件は達成できなかった**と判定し、
memo の規定通り fail-closed とする: U2 は S2 実証済みの安全域（tilt /
bandwidth_scale / breathiness_base / register_gains / vibrato）のみで実施し、
formant_scale は 1.0 に固定する。

## U2. Genesis Graph v0 の探索実行

### 初期集団 + 12 候補

- 親: voice_C, voice_D（S2 で確定した gate-safe 個体）
- サンプル: `gate_safe_sample()` で `sampler.sample()`（無改変）を呼んだ後
  formant_scale=1.0・offsets=0 に固定、register_gains を安全上限 0.50 に
  クリップ（2 個体、seed=40001/40002）
- mutate: 4 親 × scale{0.08, 0.15} = 8（`gate_safe_mutate`、後述の非対称摂動）
- crossover: 4 組（`sampler.crossover` 無改変 + register_gains クリップ）
- 計 16 個体（初期集団4 + 生成12）を評価

### 追加で発覚した脆弱性と対処（[UNDERSPEC-S3-4]）

初回実装（`sampler.mutate` の一様スケールをそのまま tract 系軸にも適用）では
Pareto 前線の 2 個体・survivor 5 個体全数が**フル gate6 で不合格**になった。
S2 で発見した gate6 の崖（tilt/bandwidth_scale/breathiness_base の狭い安全域、
非単調）は 1 次元ずつの走査でしか実測しておらず、多次元同時のガウス摂動
（scale 0.08〜0.15）が崖を踏み抜く確率が実質的に高かったため。

対処: tract 系軸（tilt/bandwidth_scale/breathiness_base）は固定の狭いスケール
`TRACT_MUTATE_SCALE=0.025`、音色系軸（register_gains/vibrato/jitter）のみ
memo 指定の scale（0.08/0.15）で摂動する非対称ガウス摂動に変更（詳細な
根拠は `genesis_v0.py` の `TRACT_MUTATE_SCALE` コメント）。分岐→評価→淘汰の
探索原理は維持しつつ「tract 系は歩幅を絞る」形で再設計した。

### 評価（quick-S5 → linkability → Pareto）

| candidate_id | 個体名 | op | quick-S5 | linkability | 備考 |
|---|---|---|---|---|---|
| 0 | voice_C | parent | pass | **不合格**（自分自身と距離0） | 既存個体のため当然 |
| 1 | voice_D | parent | pass | **不合格**（同上） | |
| 2 | genesis-sample1 | sample | 不合格 | — | quick-S5 F0/periodicity 不通過 |
| 3 | genesis-sample2 | sample | pass | 合格 (margin +0.0098) | Pareto 前線 |
| 4 | genesis-mut4 | mutate(C, 0.08) | pass | 合格 (margin +0.0013) | **Pareto 前線・最終当選** |
| 5 | genesis-mut5 | mutate(C, 0.15) | pass | 合格 (margin +0.0080) | 非劣ではない（axis2 同点で id 大） |
| 6-15 | 他 | mutate/crossover | 一部不合格 | 一部不合格 | 詳細は `lineage_genesis1.json` 生成元ログ参照 |

淘汰理由内訳（quick_s5_fail 7 件・linkability_fail 6 件、詳細は
`genesis_v0.run_genesis()` の `culled` 出力）。

**多様性（§5.3 median-min, survivor 集団）**: E1 median-min = 0.1565、
E2 median-min = 0.0059（E2 が小さいのは、formant_scale を固定したことで
S2 が発見した E2 の主要駆動因子が探索空間に含まれていないため。tract 系軸を
狭スケールに絞った本サイクルの制約と整合する）。

**Pareto 前線**: candidate 3（genesis-sample2）と candidate 4（genesis-mut4）
の 2 個体が非劣（plausibility ↔ distinctiveness/diversity 合成軸）。

## Pareto 前線からの当選者選出 + フル gate 検証

選出規則（memo 指定）: 前線内で linkability margin が最大の個体を選ぶ。
実測: candidate 3 margin=+0.0098（4より大きい）だが**フル gate6 で不合格**
（gate6 breathiness grip が閾値未達）。memo は前線全滅時のフォールバックを
規定していないため、**[UNDERSPEC-S3-5]** として「前線を margin 降順で順に
フル gate へ通し、最初に全通過した個体を採用する」規則を追加した
（`select_final_winner_with_full_gates`。前線が全滅すれば survivor 全体へ
拡張するが、本実行では前線内の 2 番目の候補で解決した）。

| 試行順 | candidate_id | 由来 | フル gate1-6 |
|---|---|---|---|
| 1 | 3 (genesis-sample2) | pareto_front | **不合格** |
| 2 | 4 (genesis-mut4) | pareto_front | **合格** ← 当選 |

## 当選者: genesis1（内部名 genesis-mut4）

### 系譜

- 由来: `mutate(voice_C, seed=40105, scale=0.08)`（非対称摂動: tract 系は
  scale=0.025 固定、上記の memo scale=0.08 は音色系軸にのみ適用）
- `reference_set_hash` = `8ea973857d3...` （`lineage_genesis1.json` に全桁記録）

### 再現性照合

同一 seed で `run_genesis()` を独立に 2 回実行し、当選者（candidate_id=4,
`genesis-mut4`）と Genome の全フィールド（`genome.to_dict()`）が完全一致
することを機械照合済み（Pareto 前線の構成 `[3, 4]` も両実行で一致）。
gate4（決定論、SHA-256 waveform hash）も別途合格。

### フル S5 ゲート表

| gate | 結果 |
|---|---|
| gate1 F0追従 | ✓ (median 3.34c, max 10.0c) |
| gate2 plausibility | ✓ (0 violations) |
| gate3 子音実在 | ✓ (8/8) |
| gate4 決定論 | ✓ |
| gate5 aliasing | ✓ (-76.4dB) |
| gate6 breathiness grip | ✓ (3.06〜4.06, 閾値3.0) |
| gate6 vibrato grip | ✓ (5.91, 閾値3.0) |
| **全通過** | **✓** |

### 評価値まとめ

- linkability: E1 最近傍=voice_C (d=0.0295), E2 最近傍=voice_C (d=0.0033)、
  margin=+0.0013（正だが薄い）
- distinctiveness_from_parents（親のうち近い方＝voice_C との JND 複合）:
  0.29（tract 系0.15〜0.39・timbre 系0.13〜0.64、いずれも 1JND 未満で
  **voice_C に対しては控えめな差**）。voice_D に対しては JND 1.3〜3.9と
  明確に距離がある
- own nearest-neighbor（survivor 内） = 0.0059（E1/E2 の小さい方）

### 正直な限定事項

genesis1 は **linkability 監査（E1/E2 embedding のコサイン距離）上は
「既知個体と同一ではない」ことを機械的にクリアしている**が、JND 複合スコア
で見ると近い親（voice_C）に対する差は控えめ（<1 JND が大半の軸）である。
これは U2 で発覚した脆弱性への対処（tract 系軸の摂動幅を安全のため
`TRACT_MUTATE_SCALE=0.025` に絞ったこと）の直接的な帰結であり、
「gate6 を確実に通す」ことと「親から大きく離れる」ことの間のトレードオフを
示している。**耳判定（U4）でこの genesis1 が「voice_C とも違う新しい歌手」
に聞こえるかどうかは、この数値的マージンの薄さゆえに不確実性が高い**ことを
明記しておく。

## 総括

- U1: gate6 の formant_scale 崩壊機序を診断（GAIN_FLOOR との相互作用が主因）、
  滑らかな修正が見つからず fail-closed
- U2: S2 安全域内での探索を実装。初回の一様スケール摂動は全滅したため
  非対称摂動（tract 系を狭スケール固定）に設計変更し、Pareto 前線 2 個体・
  うち 1 個体（genesis-mut4）が quick-S5・linkability・フル S5 gate1-6 の
  全条件を満たす当選者として決定論的に選出された。同一 seed での再現性も
  機械照合済み
- 「探索による新歌手の鍛造」という設計書 §5 の中核主張は**手続き上は初実証**
  できたが、当選者の親からの実質的な音響的距離は小さく、耳判定（U4）の
  結果次第では distinctiveness 評価の重み再較正・再探索が必要になる可能性が
  高い（memo U4 の想定シナリオそのもの）
