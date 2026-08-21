# S4B RECORD — Perceptual Coexpression Confirmation

- schema: `voicegenesis-genome-s4b/1.0`
- s4_results_sha256: `af7ddbde5c154eacf8f5da2aa360d8d9eaed39d007cb73ba2b9f1db2f340ea05`
- 音源: S4 canonical WAV の byte copy（再生成・補正・normalize なし）

## 位置づけ

複合発現時に F0 / Duration の差が **別 pair でも**耳へ残るかの確認。
**S4 本体の結果（NOT_ESTABLISHED）は変更しない。** 判定閾値は S4 §15.1 の写しであり、新しい裁定ではない。

## 結果

**NOT_CONFIRMED** — ABX 3/4 正解

| 問 | gene | context | 提示 | 正解 | 回答 | |
|---|---|---|---|---|---|---|
| Q001 | duration | medial_ri | F vs FD | A | B | MISS |
| Q002 | f0 | terminal_i | FD vs D | B | B | OK |
| Q003 | duration | terminal_i | FD vs F | A | A | OK |
| Q004 | f0 | medial_ri | FD vs D | B | B | OK |

- gene 別正解: duration 1 / f0 2
- context 別正解: medial_ri 1 / terminal_i 2

## 事前選択（耳では選ばない）

選択規則: `s4b-salience-v1: S3 SUPPORTED & S4 COMBINABLE & identity_margin>0 のうち intervention amount 最大（同値は pair_key 昇順）`

| gene | rank | context | pair | intervention | identity margin |
|---|---|---|---|---|---|
| f0 | max_salience | medial_ri | `medial_ri\|1st_color#357\|pjs002#54` | 354.003 cent | +9.315 dB |
| f0 | max_salience_other_context | terminal_i | `terminal_i\|1st_color#218\|pjs001#73` | 351.069 cent | +8.218 dB |
| duration | max_salience | terminal_i | `terminal_i\|1st_color#218\|pjs001#73` | 53.904 ms | +8.218 dB |
| duration | max_salience_other_context | medial_ri | `medial_ri\|1st_color#357\|pjs002#54` | 43.298 ms | +9.315 dB |

## Notes

- 各セル 1 問・偶然一致 1/2。統計的検定ではなく確認である。
- WAV / private key / answers は commit しない。
- **境界宣言**: s4_results.json は per-file の wav_sha256 を記録していないため、S4b が記録するのは実際に読んだ bytes の self-pin であり、S4 側 pin との cross-check ではない。S4 の記録を後付けで書き換えるのは「S4 本体の結果は変更しない」に反するため行わない（次回 S4 走行で wav_sha256 を §21 出力へ載せるのが本来の是正）。
