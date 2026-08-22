# Run 8 mechanistic finding — SP パディングにも F0 が乗る（2026-08-21）

権限: User 裁定 2026-08-21 / STEP 6「この発見は **B-1 測定器選定とは別の Run 8
mechanistic finding** として残す」。

**この所見は測定器の合否とは無関係である**（B-1 の hard requirement からは
`rr_silence` を外し、`pipeline_silence_residual` という diagnostic へ降格した）。
ここに残すのは、**TRF の因果解釈**に効くからである。

## 1. 実測（`s7_b1_pipeline_silence_diagnostic.json`）

SP のみを命令したレンダ（`rr_silence`。ノートは 1 個、音素は `SP` だけ）:

| 量 | 実測 |
|---|---|
| レンダ区間 RMS | 0.00146 |
| 核 RMS / 終端窓 RMS | 0.00146 / 0.00046 |
| ピーク | 0.00546 |
| 終端窓の主周波数 | **256.67 Hz** |
| 核の主周波数 | 259.99 Hz |
| 有声フレームの median F0 | **257.13 Hz** |
| 有声フレーム率（pyin） | **0.859** |
| 命令音高 | MIDI 60 = **261.63 Hz** |
| SP フレーム数（命令から） | 124（head 8 + note 108 + tail 8） |

つまり **SP しか命令していない区間の 86% が有声と判定され、その F0 は命令音高に
一致する**。

## 2. 経路

```
commanded score boundary
  ↓
TAIL_FRAMES = 8（gate_synth の終端 SP パディング）
  ↓
SP トークン
  ↓
pitch predictor が SP フレームにも非ゼロ F0 を出す
  ↓
NSF（source-filter）vocoder の調波音源がその F0 で励振される
  ↓
post-boundary voicing
```

`nsf_hifigan` は source-filter 型なので、mel が無音寄りでも **f0 > 0 なら音源が鳴る**。
この経路は acoustic モデルの release 挙動を一切経由しない。

## 3. 帰結（解釈規律）

**「score boundary 後の voicing」= 「acoustic model が release に失敗した」と直結させない。**

TRF の 4 軸はいずれも譜面境界より後の窓を見るので、この経路の寄与を
**交絡候補**として明示的に記帳する:

- 交絡名: `predicted-F0-on-SP contribution`
- 影響を受け得る軸: `excess_tail_voiced_ms` / `release_after_score_boundary_ms` /
  `tail_f0_persistence`（`terminal_mel_persistence` は mel 比なので相対的に受けにくいが、
  無関係とは言えない）
- 下限の目安（本レンダ条件・話者 ritsu・run7 40K ckpt）: 候補
  `A_pyin_voiced_flag|win100|hop10` で `excess_tail_voiced_ms = 130 ms` /
  `release_after_score_boundary_ms = 127 ms` / `tail_f0_persistence = 0.433`
  が **SP のみのレンダでも出る**

この値は「その水準までは release 失敗と呼べない」という**閾値ではない**
（1 条件・1 話者・1 checkpoint の観測でしかない）。PR-2 以降で本番セルの
TRF 値を読むときに、この寄与を**分離できているかを必ず問う**という規律のために残す。

## 4. 分離のために将来やり得ること（今回は実施しない）

- 終端 SP 区間の予測 F0 を直接読み出して記帳する（pitch 予測器の出力を保存する）
- `TAIL_FRAMES` を変えた対照レンダ（0 / 8 / 16）で寄与が動くかを見る
- vocoder を non-source-filter 系に替えた対照（経路依存性の切り分け）

いずれも本 PR の範囲外。**この文書は所見の記帳であって、対策の設計ではない。**
