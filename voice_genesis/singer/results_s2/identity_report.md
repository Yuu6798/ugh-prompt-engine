# S2 Identity Report — Genome 知覚的分離度の強化

- 日付: 2026-08-13
- 対応 memo: `s2_identity_design_memo.md`
- 実装: `singer/identity_metrics.py`（T1 計測。E1/E2 は `proto1/reference_set.py`
  の embedding 定義を無改変 import）、`singer/render_song.py`（`voice_c()` /
  `voice_d()` 追加）
- **instrument-validity caveat**: E1（measure_v3 6 特徴集約）/ E2（log-mel 64帯域
  集約）は本プロジェクト従来通りのスタンドイン計器であり、実話者識別器としての
  検証は行っていない。本レポートの「分離」は「この 2 系統の集約ベクトルにおける
  between>within」の意味に限定される。

## T1. まず測る（A/B 実測 = 耳の所見の計器化）

### T1-1: between/within 分離判定（A/B）

| | E1 between | E1 within(max) | E1 分離 | E2 between | E2 within(max) | E2 分離 |
|---|---|---|---|---|---|---|
| phrase0 | 0.4976 | 0.2275 | ○ (margin +0.270) | 0.0661 | 0.1077 | × (margin -0.042) |
| phrase1 | 0.0251 | 0.2275 | × (margin -0.202) | 0.0694 | 0.1026 | × (margin -0.038) |

**判定: 不成立**（4 軸中 1 軸のみ成立）。耳の所見（「別の歌手に聞こえない」）と
定量的に整合する。within は voice_A/voice_B のうち大きい方を採用（§4 参照）。

### T1-2: JND 会計表（A/B、各フレーズ先頭 3 ノート母音核の中央値）

| 特徴 | JND |
|---|---|
| mean_f0 | 0.340 |
| **formant_centroid** | **6.247** |
| **source_tilt** | **3.934** |
| periodicity | 0.905 |
| rms | 1.644 |
| vibrato_depth | 1.123 |

**memo の仮説（「A/B は breathiness・vibrato 中心の差で tract 系が小さい」）は
反証された**: tract 系（formant_centroid / source_tilt）の JND はむしろ最大級で、
breathiness/vibrato 系（vibrato_depth=1.123）より大きい。writeup 系の写像自体は
弱くない — 問題は集約 embedding 側にあった（§2 参照）。

## T2. 原因調査 + 写像・Genome の強化

### T2-1: 計測器バグの発見（E1 rms 次元の縮退）

`reference_set.py` の gallery（R0.1・8 個体）で E1 の rms 次元の標準偏差が
極端に小さい（実測 std≈0.072、他次元は 0.5〜760）。R0.1 が各ノート出力を
一定ピーク値へ正規化する設計のため、gallery 内で rms がほぼ変動しないことが
原因と推定。結果、z-score が他次元の 1〜2 桁大きくなり、コサイン距離が
実質「rms 次元の符号一致」だけで決まってしまっていた。

対応（`reference_set.py` は無改変。`identity_metrics.py` 側でのみ頑健化）:
1. z-score を ±5 にクリップ（`Z_SCORE_CLIP`）
2. クリップ後も rms 次元の符号がノイズで反転し within/between を歪めることを
   実測で確認（例: voice_C の phrase0/phrase1 で rms z-score が +5.00/-5.00 と
   符号反転、同一声内なのに巨大な within 距離を生む）→ E1 のコサイン距離
   計算から rms 次元を除外（`E1_DEGENERATE_DIMS`）。JND 表は per-feature 個別
   集計のため rms も従来通り記載を継続（除外は集約 embedding のみ）

### T2-1（続）: identity 担持次元の写像ゲイン確認 — 発見した構造的緊張

memo の想定（「vowel 目標が formant_scale/tilt の identity 変換を希釈して
いないか」）を実測で調べたところ、**希釈ではなく別の構造的制約**を発見した:

`gate_checks.py` の gate6（grip 非退行クイックチェック、S1 で凍結・voice_A
近傍で較正）は、genome の `tilt`/`formant_scale` が voice_A の既定値から
逸脱すると急激に不通過になる:

| 偏差方向 | 安全域（実測、voice_A 基準の genome_base で判定） |
|---|---|
| `source.tilt`（負方向、dark） | -17 まで安全（-18 で gate6 breathiness grip 1.25 に急落） |
| `source.tilt`（正方向、bright） | -7〜-10 が安全（-6 は grip 2.91 で不通過、非単調） |
| `resonance.formant_scale` | **±0.01 で崩壊**（0.99=grip 3.00 通過、0.98=grip 1.88 不通過）。
  実質 1.0 固定でないと gate6 が保たない |
| `resonance.bandwidth_scale`（凍結表に無いフィールド） | 0.80〜1.30（tilt 依存）
  まで安全。formant_scale よりずっと寛容 |
| `noise.breathiness_base`（自身の値。gate6 sweep では上書きされない
  「背景値」として他軸のクイックチェックに混入する） | 0.40 が安全上限
  （0.42 で gate1 F0 追従が median 14→崩壊、max_cents_err 22.9→1218.2 の
  急峻な崖。背景ノイズ過大で F0 推定器が破綻） |

**結論**: `formant_scale` は memo が想定した主要な tract identity 軸として
機能しない（この engine の現行 gate6 較正下では実質固定値扱い）。identity
差の担い手を `tilt` + `bandwidth_scale`（凍結表に無いフィールド、物理的に
妥当な声道フォルマント帯域幅の差として解釈可能）+ `breathiness_base` の
gate-safe な範囲内での最大対比に置き換えた。

### T2-2: voice_C / voice_D の最終設計

上記の安全域を総当たりで走査し、T3 の 3 条件（分離・JND・S5 gate 1-6）を
同時に満たす点を実測で確定した（詳細な調整過程・却下した候補は
`underspec_log_s2.md` 参照）。

| パラメータ | voice_C（大きく暗い） | voice_D（小さく明るい） | memo 原案 |
|---|---|---|---|
| source.tilt | -17.0 | -10.0 | C:-13 / D:-6 |
| resonance.formant_scale | 1.0（固定・理由は上記） | 1.0（固定） | C:0.87 / D:1.15 |
| resonance.bandwidth_scale | 0.80 | 1.30 | (指定なし・新規採用) |
| noise.breathiness_base | 0.0 | 0.40 | C:低 / D:0.25 |
| noise.register_gains | (0,0,0,0,0) | (0,0.10,0.20,0.30,0.40) | — |
| microprosody.vibrato_rate/depth | 5.0Hz / 30c | 6.3Hz / 45c | C:5.0/30 / D:6.3/55 |

いずれも `out_of_physio_range=False`（凍結表フィールドは境界内、
非凍結フィールドの `bandwidth_scale` も物理的に妥当な範囲）。

## T3. 受け入れ条件（実測、全て満たす）

### T3-1: 機械 identity 分離（voice_C / voice_D）

| | E1 between | E1 within(max) | E1 分離 | E2 between | E2 within(max) | E2 分離 |
|---|---|---|---|---|---|---|
| phrase0 | 0.3645 | 0.1671 | ○ (margin +0.197) | 0.1489 | 0.1030 | ○ (margin +0.046) |
| phrase1 | 0.2411 | 0.1671 | ○ (margin +0.074) | 0.1089 | 0.1030 | ○ (margin **+0.006**) |

**判定: 成立（4/4）**。E2 phrase1 の margin は薄い（+0.0059）が正。within は
voice_C/voice_D のうち大きい方（voice_D）を採用（§4 の定義を継続適用）。

### T3-2: JND 会計（tract 系差 ≥3 JND）

| 特徴 | JND（voice_C vs voice_D） |
|---|---|
| mean_f0 | 0.469 |
| formant_centroid | 2.973（僅かに 3 未達） |
| **source_tilt** | **4.319** ✓（≥3、閾値 4.5dB/oct 相当を超過） |
| periodicity | 2.651 |
| rms | 2.359 |
| vibrato_depth | 1.759 |

**判定: 成立**（source_tilt 軸で 3 JND 以上を達成。formant_centroid・source_tilt
のいずれか一方で良く、source_tilt が要件を満たす）。

### T3-3: S5 機械ゲート（gate1-6、両声とも全通過）

| gate | voice_C | voice_D |
|---|---|---|
| gate1 F0追従 | ✓ (median 11.7c, max 19.2c) | ✓ (median 7.7c, max 22.9c) |
| gate2 plausibility | ✓ | ✓ |
| gate3 子音実在 | ✓ (8/8) | ✓ (8/8) |
| gate4 決定論 | ✓ | ✓ |
| gate5 aliasing | ✓ (-85.3dB) | ✓ (-76.9dB) |
| gate6 breathiness grip | ✓ (3.756) | ✓ (3.068) |
| gate6 vibrato grip | ✓ (6.067) | ✓ (5.242) |
| **全通過** | **✓** | **✓** |

T3 の 3 条件すべて成立を確認したため、`sakura_voiceC.wav` / `sakura_voiceD.wav`
を耳判定素材として出力した。

## 総括

- 耳の所見（A/B が別歌手に聞こえない）は E1/E2 計測でも再現・定量化された
  （4 軸中 1 軸のみ分離）
- memo の仮説（tract 差が小さいのが原因）は反証。真因は (1) E1 rms 次元の
  計測器バグ、(2) gate6 が `formant_scale` をほぼ固定値に縛る構造的制約、
  の 2 つ
- voice_C/voice_D は `formant_scale` を使わず `tilt` + `bandwidth_scale`
  （非凍結フィールド）+ `breathiness_base` で tract/声質対比を作ることで
  T3 の 3 条件（分離・JND・S5 gate 全通過）を同時達成
- 次サイクルへの引き継ぎ: `formant_scale` を identity 軸として復権させたい
  場合は gate6（grip クイックチェック）の較正域拡張、または `formant_scale`
  非依存の grip 検査への再設計が前提条件になる（`underspec_log_s2.md` 参照）
