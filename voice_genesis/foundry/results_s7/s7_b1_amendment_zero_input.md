# B-1 amendment — `silence_zero` → `zero_input_false_positive`（2026-08-21）

権限: **User 裁定 2026-08-21 / STEP 6 = (A) 採用**。
本 amendment は**再測定より前に単独 commit / pin** する。以降の再測定は、この
amendment を pin した状態の事前登録 3 点に対してのみ有効とする。

対象ファイル（本 commit で改訂した事前登録）:

- `s7_b1_selection_rule.json` — hard requirement の id と定義
- `s7_b1_calibration_set.json` — 役割の担当刺激（合成側 / 実レンダ側）

**`trf_measurement_spec_real_render_rc2.json`（`1.0-rc2` / `blocked`）は書き換えない。**
歴史的成果物として保存する。

---

## 1. なぜ旧 `silence_zero` が不適切だったか

要求の意味は「**測定器が、入力に何も無いのに有声（あるいは終端エネルギー）を
検出しないこと**」である。許容が ms 軸で 0.0 なのはその意味に対応している。

ところが実レンダ側の操作化は `rr_silence` = **SP のみの実レンダ**だった。
これは「無入力」ではなく「経路が生成した非ゼロ波形」であり、要求が意図した
detector false positive とは**別の量**を測っていた。つまり旧要件は、実レンダ側で
**要求の意味を実装していなかった**。

## 2. `rr_silence` の実測事実（`1.0-rc2` の測定より）

| 量 | 実測 |
|---|---|
| 核 RMS | 0.00152574255 |
| 終端窓 RMS | 0.00045705103 |
| 終端窓の主周波数 | **256.667 Hz** |
| 命令音高 | MIDI 60（C4 = 261.626 Hz） |
| 軸残差（候補 A / pyin） | `excess_tail_voiced_ms` 130–235 ms |
| 軸残差（候補 B / RMS ゲート） | 同 10–30 ms |

SP しか命令していないのに**命令音高の調波**が出る。経路:

```
SP → duration → pitch predictor（SP フレームにも非ゼロ F0）
   → acoustic → NSF source（F0 で励振）→ vocoder → 非ゼロ波形
```

## 3. zero buffer と SP-only render の意味の違い

| | 入力 | 測っているもの |
|---|---|---|
| **zero buffer** | サンプル値が厳密に 0.0 | **測定器**が無から有声を作るか（false positive） |
| **SP-only render** | DiffSinger 経路の出力（非ゼロ） | **経路**が SP 区間にどれだけ音を残すか |

両者を同じ要件で扱うと、測定器の欠陥と経路の性質が混ざる。分離する。

## 4. 新 hard requirement の式

```
zero_input_false_positive(axis, candidate) = |value(axis, candidate, zero_buffer)|
    zero_buffer := samples[i] == 0.0 (bit-exact) for all i,
                   同じ校正音源の刺激と同一の sample rate / 長さ / 境界定義
合格条件: ms 軸  <= 0.0        （旧 silence_zero と同一）
          ratio 軸 <= 0.01     （同上）
```

合成側の担当刺激 `silence` は**元から厳密ゼロ**なので、合成側は名称のみの改称で
値は変わらない。実レンダ側の担当刺激を `rr_silence` → `rr_zero_buffer` に差し替える。

## 5. `rr_silence` の降格

`rr_silence` は**削除しない**。`real_render_set.diagnostics` の
`pipeline_silence_residual` として保存し、**pass / fail 判定には使わない**。
記録必須項目（User 裁定）: waveform RMS / dominant F0 / median F0 / commanded pitch /
SP frame count / tail frame count / 全候補の TRF 軸実測値 /
acoustic・pitch predictor・vocoder の provenance（sha256）。

## 6. 変更していないもの

- 候補空間（`s7_b1_candidate_space.json`）— **無改訂**
- window / hop / mel 設定・候補 id
- `ranking_among_survivors`（キー名 `silence_residual` を含め **byte 単位で不変**。
  改称に合わせてキー名を変えると「ranking key の変更」になるため、意図して据え置く）
- 他 5 要件（`reproducibility` / `gain_invariance` / `monotone_response` /
  `cross_process_reproducibility` / `numerical_stability`）とすべての許容値
- 校正条件 11 + 派生 2 の定義（音高・拍・延長 ms・r:i 配分）

## 7. 本番 360 セルを見ていないこと

本 amendment の起草に使った入力は、**校正専用 real-render セットの実測値のみ**である。
本番 360 セル・run5/6/7 の good/bad ラベル・P-ANCHOR 結果は 1 件も参照していない
（B-1 ハーネスはそれらへの入力口を構造的に持たない）。

## 8. 変更理由の固定（重要）

変更理由は **「旧刺激が要求の意味を実装していなかった」** に固定する。
**「候補 A が通りそうだから」ではない。** 再測定は全候補を最初から回し、
既存 6 要件（`zero_input_false_positive` を含む）をすべて評価し、
**結果を見る前に winner を決めない**（順位付けは従来どおり機械規則のみ）。

## 9. 昇格条件（User 裁定）

再測定で 4 軸すべてが次を満たしたときに限り `spec_version = 1.0` /
`freeze_status = frozen` へ昇格し、その時だけ PR-2 を解錠する:

- 6 つの hard requirement を PASS
- winner が機械規則で一意に決まる
- 同一プロセス再現・独立プロセス再現
- analysis stack pin 一致 / prereg SHA 一致
- 360-cell contamination = 0

1 つでも落ちたら昇格させず、再び User 裁定へ戻す。
