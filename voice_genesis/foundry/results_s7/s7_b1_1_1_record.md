# TRF measurement spec / 1.1 — 校正記録（2026-08-21）

権限: **User 裁定 2026-08-21（Run8 User Adjudication / NEXT ACTION = Option C）**。
1.0 は差し替えず、新系列として 1.1 を作る。

事前登録（**計算を 1 回も回す前に**単独 commit `fbedb51` で pin）:

- [`s7_b1_candidate_space_1_1.json`](s7_b1_candidate_space_1_1.json)
- [`s7_b1_selection_rule_1_1.json`](s7_b1_selection_rule_1_1.json)
- [`s7_b1_calibration_set_1_1.json`](s7_b1_calibration_set_1_1.json)

結果: **4 軸とも 7 要件を通過 → `spec_version = 1.1` / `freeze_status = frozen`**
（[`trf_measurement_spec_1_1.json`](trf_measurement_spec_1_1.json)）。

---

## 1. 1.0 から変えたもの / 変えていないもの

| | |
|---|---|
| **変えた** | voicing 候補（periodicity 証拠 **AND** energy 証拠）/ hard requirement を 6 → 7（`production_range_resolution` 新設） |
| **変えていない** | 4 軸の計算式（1.0 の実装をそのまま呼ぶ）/ 順位付けキーと丸め（`rank_key` を共有）/ ε の導出式 / 校正音源（1.0 の real-render セットを sha pin で継承・新規生成なし）/ mel 候補 3 種 |
| **触っていない** | `trf_measurement_spec.json`（1.0）は 1 バイトも変更していない（sha `e1ed1a89…` 不変を機械確認） |

energy gate は **「frame RMS / 同一 render の voiced-core RMS」** の比。分子分母が同じ
線形 gain で伸縮するので **gain 不変**（旧候補 B の固定 absolute RMS threshold は
復活させていない）。core 窓が取れないレンダでは fail-closed で `voiced = False`。

## 2. 新要件 `production_range_resolution` が実際に選別した

| 軸 | 生存 | 落とした要件 |
|---|---|---|
| `excess_tail_voiced_ms` | 27 / 96 | **`production_range_resolution` が 69 件**（他の 6 要件で落ちた候補は 0） |
| `release_after_score_boundary_ms` | 27 / 96 | 同上 |
| `tail_f0_persistence` | 27 / 96 | 同上 |
| `terminal_mel_persistence` | 3 / 3 | なし（1.0 と同じ） |

**新要件だけが候補を落とした**。つまり 1.1 の選別は「1.0 では見えなかった性質」に
対してのみ働いており、既存 6 要件の意味は変わっていない。

## 3. 選定候補と実測

**勝者（voicing 3 軸すべて）**: `A_pyin_prob_relenergy|thr0.5|tau0.5|win100|hop10`
= pyin voiced_probability ≥ 0.5 **AND** frame RMS / voiced-core RMS ≥ 0.5、窓 100 ms・hop 10 ms。
**mel 軸**: `M2_2048_512_80`（1.0 と同一）。

| 軸 | 経路床（`rr_silence`） | 終端 tail（`rr_long_tail_160`） | 分離 | 1.0 の床 |
|---|---|---|---|---|
| `excess_tail_voiced_ms` | **50.0 ms**（許容 ≤ 75） | 180.0 ms | 130.0（要 ≥ 80） | 130 ms |
| `release_after_score_boundary_ms` | 47.1 ms | 177.1 ms | 130.0（要 ≥ 80） | 127 ms |
| `tail_f0_persistence` | **0.167**（許容 ≤ 0.25） | 0.600 | 0.433（要 ≥ 0.05） | 0.433 |
| `terminal_mel_persistence` | 0.086（床上限は免除） | 0.484 | 0.398（要 ≥ 0.05） | — |

ε は **1.0 と同一**（10 ms / 10 ms / 0.0333 / 0.0387）。**縮めていない。**

## 4. 正直に書いておくこと — 勝者は最終手段のタイブレークで決まった

voicing 3 軸の上位は

```
A_pyin_prob_relenergy|thr0.5|tau0.5|win100|hop10
A_pyin_prob_relenergy|thr0.8|tau0.5|win100|hop10
B_autocorr_relenergy |thr0.5|tau0.5|win100|hop10
```

が gain 誤差 0・zero 残差 0・`monotone_min_step`・`hop_ms`・`window_ms` まで**完全同値**で、
**キー #6 = `candidate_id`（辞書順）**で決着した。事前登録の順位付け規則どおりであり
機械規則で一意ではあるが、**物理的な優劣で選ばれたのではない**。

なお同率上位はいずれも **τ = 0.5（energy gate の最強水準）・win100・hop10** で一致して
おり、**分かれているのは periodicity 側の族と閾値だけ**である。校正セット上ではこの 3 者を
弁別できなかった、というのが正確な言い方になる。

1.0 のときは mel 軸がキー #3（応答余裕）、voicing 軸がキー #5（窓幅）で決着していたので、
**1.1 は 1.0 より弱いタイブレークで勝者が決まっている**。

## 5. 主張上限

- 「1.1 が本番セルで分解能を持つ」とは**言っていない**。1.1 が通ったのは**校正**であり、
  1.0 で起きたのは「校正は通ったが本番で飽和した」ことである。本番適用性は
  **新規事前登録の confirmation set** で初めて評価する（裁定 CONFIRMATION）
- 「§5-0 の ringing 基準が再取得できる」とも**まだ言っていない**。既存 360 セルでの
  再測定は診断であって Gate 評価ではない（[`s7_0b_remeasure_1_1.json`](s7_0b_remeasure_1_1.json)）
- 「候補 A が物理的に最良」とも**言っていない**（§4）
