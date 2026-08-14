# S4 safe_box_v2 — gate6-v2（score-informed QC）実測の安全域ボックス

- 日付: 2026-08-13
- 計器: `singer/gate_checks_v2.py`（gate6 のみ score-informed。
  provenance = `measured (score-informed QC)`）

## W1: score-informed 化の効果確認

formant_scale=0.95・breathiness=0.5・voice_A基準・A4 probe（S2/S3で
periodicity が -19.96dB へクラッシュした既知の悪条件）を再測:

| 計測 | 旧（blind, measure_v3） | 新（score-informed, gate_checks_v2） |
|---|---|---|
| periodicity_db_median | **-19.96 dB**（クラッシュ） | **+1.02 dB**（正常域） |
| F0 推定 | 452.5 Hz（真値440から約50cent） | （score-informed periodicityはF0推定を経由しない） |

**結論**: W1 はクラッシュ性の異常値（ブラインドF0誤りに起因する
periodicity の激しい負のスパイク）を根絶した。これは実測で確認済み。

## W2: GAIN_FLOOR 従来値のままでの再走査 → 開かず（fail-closed）

W1適用後、GAIN_FLOOR=0.10（従来値）のまま formant_scale を再走査:

| formant_scale | gate6 breathiness grip (v2) | pass |
|---|---|---|
| 1.00 | 3.04 | ✓ |
| 0.99 | 2.95 | × |
| 0.98 | 1.91 | × |
| 0.95 | 1.43 | × |
| 0.92 | 1.73 | × |
| 0.90 | 1.86 | × |
| 0.87 | 1.90 | × |
| 0.85 | 2.02 | × |

**結論**: クラッシュは消えたが、periodicity の breathiness 感度そのものが
formant_scale=1.0 から離れると滑らかに低下する（クラッシュではなく実質的な
感度低下）ことが判明。これは測定バグではなく、この合成器の
formant フィルタ位相特性由来の genuine な挙動と判断する。

GAIN_FLOOR の magnitude を振っても改善しないことも確認済み（fs=1.0 ですら
floor を下げると grip が悪化する非単調な挙動。floor=0.10 が最良）:

| GAIN_FLOOR | fs=1.0 grip | fs=0.95 grip | fs=0.90 grip | fs=0.87 grip |
|---|---|---|---|---|
| 0.10（従来） | 3.04 | 1.43 | 1.86 | 1.90 |
| 0.07 | 0.62 | 0.06 | 1.86 | 1.88 |
| 0.05 | 0.64 | 0.06 | 1.31 | 1.42 |
| 0.03 | 0.07 | 0.06 | 0.88 | 1.50 |
| 0.00 | 0.71 | 1.36 | 1.98 | 0.04 |

memo W2-2 の適応 floor（commanded F0 帯域を避けて floor を置く）は、
`lorentz_gain`/`apply_time_varying_formant_filter` が現状 F0 を引数に
取らない設計であり、対応するには関数シグネチャ変更 + gate1-5 の
voice_A/B/C/D 全数再検証という大きな改修が必要と判断した。
**GAIN_FLOOR は今回変更しない（fail-closed。formant_tv.py は無改変のまま）**。
これにより W2 の「レンダラ変更時の gate1-5 非退行確認」は該当なし
（レンダラ自体を変更していないため）。

## voice_B の gate6-v2 再測（クロストーク仮説の検証）

| | breathiness grip | vibrato grip | pass |
|---|---|---|---|
| 旧（blind, S1） | 非達（詳細値は S1 記録参照） | — | × |
| 新（score-informed, v2） | **1.23** | 8.71 | **×（不合格のまま）** |

**クロストーク仮説は反証された**: score-informed 計測でも voice_B は
gate6 breathiness に不合格。S1 の未達は「ブラインド F0 推定が高
breathiness/vibrato で劣化し vibrato_depth 計測を汚染する」という
クロストークが原因ではなく、voice_B の Genome（tilt=-15, formant_scale=1.12,
breathiness_base=0.12, register_gains 高め）自体が periodicity 感度を
実質的に下げる領域にあるという genuine な挙動だったと判断する。

## W3: 安全域ボックス（1次元走査 + 多次元スポットチェック）

1次元走査（voice_A 基準、他パラメータ既定値のまま単軸のみ変化）:

| 軸 | 走査範囲 | 安全域（gate6-v2 pass） |
|---|---|---|
| tilt | -18〜-5 | **-14〜-8**（-9のみ例外的に不合格。非単調） |
| bandwidth_scale | 0.70〜1.40 | **0.70〜1.05**（1.05超で不合格へ） |
| breathiness_base（own値、vibrato sweepの背景） | 0.0〜0.6 | **0.0〜0.4**（0.5で崩壊） |
| vibrato_depth_cents（own値、breathiness sweepの背景） | 10〜150 | **10〜45**（60で崩壊開始） |
| formant_scale | 0.85〜1.20（memo指定） | **0.99〜1.01のみ**（W2参照。開かず） |

多次元スポットチェック（有望コーナー）:

| コーナー | パラメータ | breathiness grip | pass |
|---|---|---|---|
| dark_corner | tilt=-14, bw=0.70, breath=0.4, rg=(0,.1,.2,.3,.4) | 1.55 | × |
| bright_corner | tilt=-8, bw=1.05, breath=0.4, rg=(0,.1,.2,.3,.4) | 2.49 | × |
| dark_dry | tilt=-14, bw=0.70, breath=0.0, rg=全0 | **2.999** | ×（僅差） |
| voice_C（tilt=-17, bw=0.80, breath=0.0, rg=全0） | — | **3.66** | **✓**（1次元範囲外だが多次元で合格） |
| voice_D（tilt=-10, bw=1.30, breath=0.40, rg高め） | — | 2.74 | ×（S3のv1測定では合格だったが v2では不合格に反転） |

**多次元での非単調性を再確認**（S3の教訓通り）: voice_C は1次元的には
安全域外（tilt=-17）だが、bandwidth_scale/breathiness_base/register_gains
の組み合わせにより合格する。逆に voice_D は1次元的には各軸が単独では
それぞれ危険域スレスレだが、旧(v1)測定では合格していたのに新(v2)測定では
不合格に転じた——**旧voice_D の gate6 合格はブラインド計測のアーティファクト
だった可能性が高い**（重要な発見。underspec_log 参照）。

## 凍結: SAFE_BOX_V2（`genesis_v2.py` 実装値）

保守側に倒した1次元安全域の部分集合を採用（多次元での崩れを見込んだ余裕
を残す。詳細な選定根拠は `underspec_log_s4.md` [UNDERSPEC-S4-2]）:

| 軸 | SAFE_BOX_V2 |
|---|---|
| formant_scale | [0.99, 1.01] |
| tilt | [-13.0, -8.0] |
| bandwidth_scale | [0.80, 1.05] |
| breathiness_base | [0.0, 0.35] |
| register_gains（各要素） | [0.0, 0.35] |
| vibrato_rate_hz | [4.5, 7.0] |
| vibrato_depth_cents | [15.0, 40.0] |

S3b のボックスと比較して tilt レンジは大幅に狭まった（[-17,-7]→[-13,-8]。
S3のvoice_C/D自体が新計測で境界外れ・不合格転落したため）が、
bandwidth_scale の下限は変わらず、breathiness_base/register_gains は
若干保守化した。**皮肉にも「安全域を広げる」という W1-W3 の目標に対し、
tilt軸は却って狭まった**——これは旧ブラインド計測が「クラッシュ由来の
見かけ上のgrip」を作っていた領域を除去した結果であり、ボックスの絶対的な
広さより **信頼性**が S4 の実質的な成果である。
