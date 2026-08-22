# Run 8 クローズアウト（2026-08-22）

権限: **User 裁定 2026-08-22 = (I)**。

```
Gate 1  = UNDETERMINED
Run 8   = CLOSED
reason  = INSUFFICIENT_GATE_ADJUDICATION_EVIDENCE
```

> The preregistered listening set is not aligned with the seven production-eligible
> groups selected by §5-0. Human labels from the current set cannot produce a valid
> Gate 1 verdict.

**聴取作業は実施しない。1.2 の凍結値は変更しない。1.3 と層化設計の再実装には進まない。**

---

## 1. なぜ閉じたか（判定材料の不足。計器の失敗ではない）

Gate 1 の合否は §7-0b の 6 条件で決まる。耳ラベルが付く前に決まる部分だけを
機械検算（`s7_gate1_human_verification_prereg.json` の pre-flight）した結果、
**voicing 3 軸すべてで `evaluable = False`**:

| blocker | 内訳 |
|---|---|
| `calibration_scored = 10 < 12`（§7-0 (4b)） | 7 群制限で ritsu の 6 セル、`run6/pjs` の `degenerate_axis` で 3 セルが落ち **19 → 10** |
| `positive_control_absent`（§7-0b 条件 1） | 陽性対照 `run7/ritsu/P-ANCHOR/sakura-kagiri` が **Gate 対象外の群**にある |

hold-out は 14（≥12 ✓）、Gate 3 の user 対応ペアは 5（✓）。落ちているのは
**校正側と陽性対照だけ**である。

そして §7-0 (4b) は「**不足が起きても耳ラベルの対象セルを事後に足して埋めない**」と
定めている。したがって**この不足は聴取では埋まらない**。40 + 8 クリップの人手を
「判定に使えないと分かっている作業」へ払わない、という判断で閉じる。

## 2. 保存する成果

### 2-1. 1.2 voicing detector — **production-range resolution = ESTABLISHED**（eligible 群）

| | |
|---|---|
| 軸 | `excess_tail_voiced_ms` / `release_after_score_boundary_ms` / `tail_f0_persistence` |
| 分離軸 | エネルギー正規化 log-mel 形状と**ノート核形状**のコサイン距離 |
| 凍結パラメータ | `thr = 0.2` / `win = 100`（再調整禁止） |
| 校正 | 振幅比 0.016 → 0.628（**40 倍**）に対し値がほぼ不変（release は全 rung で 253.49） |
| 本番 | 縮退群 **1–2 / 10**（1.0 は 3–8/10、1.1 は 8/10） |

**Run 8 の核心だった「本番セルを分解できる計器」は、3 系列目で初めて成立した。**
これは Gate 通過とは別の、独立した成果である。

### 2-2. `terminal_mel_persistence` — unavailable under the current observation design

終端窓 mel パワー / ノート核 mel パワー = **相対エネルギー量そのもの**であり、
経路床 0.319 > 本番中央値 0.207 の逆転を相続する。3 mel 候補とも
`production_range_resolution` の分離が**負**（−0.030 〜 −0.037）。

**訂正記録**: 1.0 / 1.1 では「mel 軸だけは本番で縮退していない」と報告していたが、
その非縮退は**振幅のばらつき**を測っていたのであって、経路床と本物の終端を区別する
能力ではなかった。

### 2-3. 研究資産（別の観測研究へそのまま持ち込める）

| 資産 | 所在 | 内容 |
|---|---|---|
| 360 セル全数記録 | `s7_0b_results.json` / `probe_0b_groups/` | rendered 360 / dropped 0 / GPU $0。再レンダで voicing 3 軸は `max\|Δ\| = 0` 再現 |
| SP パディング由来の F0 経路 | `s7_finding_sp_padding_f0.md` | 境界後の voicing を acoustic の release 失敗と直結させない解釈規律 |
| 相対エネルギーの逆転 | `s7_relative_energy_finding.md` | 床 0.319 > 本番中央値 0.207。1 次元では原理的に分離不能 |
| 1.2 計器 | `run8/s7_b1_v12.py` | 形状ベース・振幅不変の voicing detector |
| 標的被覆台帳 + H-TTD closeout | `target_exposure_ledger.json` | 4 話者。`NOT_EVALUABLE_INSUFFICIENT_SUPPORT` を証拠つきで閉じた |
| 再現性と pin 意味論 | `s7_reproducibility_finding.md` | `reference_output 再測定` > `samples_sha256` > `wav_sha256` |
| 復旧手段 | `run8/provision.sh` | 17 資産を sha 照合つき・冪等・fail-closed で再配置 |

## 3. Run 8 が達成したこと / しなかったこと

**達成**:

- Run 8-0 台帳（4 話者・機械集計）と H-TTD の closeout
- B-1 / B-2 の凍結（事前登録の順序規律を 3 系列すべてで維持）
- Run 8-0b の **360 セル全数記帳**（GPU $0）
- **本番分解能を持つ計器の確立**（1.2 voicing 3 軸）
- 3 つの機構的所見（SP-F0 経路 / 相対エネルギー逆転 / 校正セットの非代表性）

**未達**:

- **Gate 1 の合否**（PASS / FAIL のいずれも出していない）
- H0–H5 の裁定
- Run 8-B（有料の学習）への進行 — **BLOCKED のまま**

## 4. 言っていないこと（主張上限）

- 「TRF が存在しない / 観測できない」とは**言っていない**。閉じたのは **Gate 判定の材料**であって、
  計器は 1.2 で成立している
- 「1.2 が失敗した」とも**言っていない**。production-range resolution は eligible 群で成立している
- 「モデルに終端破綻が無い / ある」とも**言っていない**。耳ラベルを取っていないので、
  機械値と知覚の対応そのものが未確認である
- 「ritsu に破綻が無い」とも**言っていない**。ritsu 群が Gate から外れたのは
  検出器と ritsu 終端の相互作用（無声基準が取れない）であって、破綻の有無とは別の話である

## 5. 継続について

**1.3 は作らない。層化設計の再実装にも進まない。**
HNR / vowel drift / mel persistence 等が必要になった場合は、Run 8 の継続版ではなく
**別の観測研究**として再事前登録する（User 裁定 2026-08-22）。

---

## 6. 付記: クローズ後に見つかった所見（判定は動かない）

PR #303 のレビュー対応中に、**1.2 校正音源の親バッチが 1.2 事前登録の pin と違う**
ことが分かった（v2 を宣言し、v3 で測っていた）。差は ONNX 再 export のノイズ
（1e-8 桁）で、1.2 の ε を大きく下回るため **§1–§5 の判定はいずれも動かない**。
事前登録も凍結値も書き換えず、所見として記録した:
[`s7_b1_1_2_provenance_finding.md`](s7_b1_1_2_provenance_finding.md)。
