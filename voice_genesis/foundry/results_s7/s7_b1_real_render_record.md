# B-1 実レンダ校正の実測記録（STEP 6 / 2026-08-21）

権限: User 裁定 2026-08-21 §1（裁定 1 = **(b)**「合成校正だけでは TRF measurement
spec を 1.0 へ昇格させない。本番 360 セルとは完全分離した校正専用実レンダセットを
**生成前に**事前登録し、それで再校正する」）。

事前登録: `s7_b1_calibration_set.json` の `real_render_set`
（sha256 `c001f6e6…`。commit `d4ee992` = **レンダ実行より前**）。

結論（2 段階。§5b で更新）:

1. **第 1 回（amendment 前）= `1.0-rc2` / `blocked`。4 軸すべて `unavailable`**
   （事前登録 `acceptance.on_failure` どおり昇格させず、`trf_measurement_spec_real_render_rc2.json`
   として保存。書き換えていない）
2. **第 2 回（User 裁定 (A) の amendment を単独 pin した後の全候補再測定）
   = `1.0` / `frozen`。4 軸すべて凍結**（§5b）

§1–§4 は第 1 回の記録、§5 は裁定に上げた論点、§5b が最終結果である。

---

## 1. 何を回したか

| 項目 | 実測値 |
|---|---|
| レンダ条件 | 11（事前登録どおり） + 派生 2（線形ゲイン、再レンダなし） |
| 経路 | `s1_gate/gate_synth.py::run_pipeline`（run7 と同一） |
| acoustic | run7 40K ckpt から export した `s6_run7_acoustic.onnx`（reflow 多話者） |
| 話者 | ritsu（spk_id 0）固定 |
| 容器 pin 照合 | ckpt `518df090…` / canon zip `5c7b8c32…` / vocoder 容器 `e22f8400…` **3/3 一致** |
| 測定スタック | `ANALYSIS_STACK_PIN`（numba 0.66.0 / librosa 0.11.0 / pyloudnorm 0.2.0）fail-closed 照合済み |
| 生成物 | `s7_b1_real_render_manifest.json`（波形 sha256 13 件・境界・命令フレーム） |

命令の実現方法（事前登録が要求する 2 条件は譜面では命令できない）:

- `rr_dur_perturb_*`（終端 /ri/ の r:i 配分 0.20/0.35/0.50）— 配分は `dsdur/dur.onnx`
  の**予測**であって譜面の命令ではない。
- `rr_long_tail_*`（終端の有声を 0/40/80/160 ms 伸ばす）— ノート尺を伸ばすと
  境界自体が動いてしまい、「境界固定・はみ出しだけ増える」梯子にならない。

そこで `run_pipeline` に **既定 off** の `final_phone_dur_override` フックを 1 つ足した。
**off のときの経路は run5/6/7 と 1 命令も変わらない**ことを、既存成果物との sha256
一致で確認済み（`gate_sakura_n4.wav` = `6e955a7d9a55f31a0eda0e20c18e2f8e6eddf5c35be6b6c3103487dd846a3392`、
フック追加の前後で完全一致）。

境界は**命令から**算出した（波形から推定していない）:
`note_onset_s = 0.09288`（HEAD_FRAMES=8）/ `commanded_note_end_s = 1.34676`（108 フレーム）/
`score_boundary_s = 1.34288`（1.5 拍 @72 BPM）/ 終端窓 300 ms。梯子 4 条件は
**同じ譜面・同じ境界**で、終端フレームの延長量（0/3/7/14 フレーム）だけが違う。

全条件を共通長（1.6929 s）へゼロ詰めした。詰めないと終端窓に入るフレーム数が条件ごとに
変わり、「有声が伸びた」と「音源が長い」が分離できない（合成刺激側の固定長 1.6 s と
同じ役割）。ゼロ詰めは有声を足さない。

## 2. 実レンダ校正は候補を落とした（合成では 1 つも落ちなかった）

合成刺激では 6 要件が候補を 1 つも落とさず（12/12・3/3 通過）、選別が働かなかった。
実レンダでは要件ごとに**異なる候補が落ちる**:

| 要件 | 実レンダでの挙動 |
|---|---|
| `reproducibility` / `cross_process` | 全候補 0.0（合格） |
| `numerical_stability` | 全候補 finite（合格） |
| `monotone_response` | 梯子は単調（span 120–170 ms）。ただし **win300 の候補は 300 ms 窓で飽和**して最終段の増分が 0 になり不合格 |
| `gain_invariance` | **候補 B（RMS+自己相関ゲート）が全滅**（誤差 15–30 ms / 比 0.05–0.10 > 許容）。候補 A（pyin）は誤差 0.0 |
| `silence_zero` | **全候補が不合格**（下記 §3） |

つまり実レンダ校正は「合成では見えなかった候補の欠陥」を実際に検出した。
候補 B の絶対 RMS 閾値（1e-3）は、ピーク 0.6 の合成刺激では 0.5 倍しても閾値を割らないが、
ピーク 0.02–0.03 の実レンダでは 0.5 倍が閾値をまたぐ — **線形ゲイン不変ではない**。

## 3. 停止要因: `rr_silence` は無音ではない（モデル側の実測所見）

`silence_zero` の許容は **ms 軸で 0.0**（定義 `|value(silence)|`）。事前登録の
`rr_silence` は「SP のみ（ノート無し）」の**実レンダ**である。実測:

| 条件 | 核 RMS | 終端窓 RMS | 終端窓のピーク周波数 |
|---|---|---|---|
| `rr_silence` | 0.00153 | 0.00046 | **256.7 Hz** |
| `rr_clean_i` | 0.01245 | 0.00246 | 250.0 Hz |
| `rr_long_tail_160` | 0.01266 | 0.00908 | 250.0 Hz |

C4 = 261.6 Hz。**SP しか命令していないのに、命令音高の基本周波数を持つ有声信号が出る**。
理由は経路上の構造にある: nsf_hifigan は **source-filter 型 vocoder** で、pitch 予測器が
SP フレームにも f0 を出し、その f0 が調波音源を励振する。したがって
「SP のみ」は**この経路では定義上ゼロにならない**。

結果、`excess_tail_voiced_ms` の silence 残差は候補 A で 130–235 ms、候補 B で 10–30 ms。
許容 0.0 に対して**どの候補も通れない**。

この所見自体が TRF にとって重要である: 終端 SP パディング（`TAIL_FRAMES = 8`）にも
f0 が乗るため、**譜面境界の後の有声は acoustic の release 挙動と無関係にも発生し得る**。

## 4. やらなかったこと（規律）

- 要件・許容・順位付けキー・候補空間を**変更していない**（事前登録の禁止事項）。
- `rr_silence` の定義を結果を見てから差し替えて**いない**（生成後の条件変更は禁止）。
- 本番 360 セルを 1 セルも通していない。ラベル / P-ANCHOR 結果も入力していない。
- spec を 1.0 へ昇格させていない。

## 5. User 裁定に上げた 1 点（裁定済み: (A) 採用）

事前登録 `acceptance` は「昇格しない」までを規定しているが、**次の一手**は規定していない。
`rr_silence` の不合格は候補の欠陥ではなく**刺激の操作化のミスマッチ**（許容 0.0 は
デジタル無音を前提に書かれた）であり、これを直すには**再実行の前に amendment を
commit/pin** する必要がある。選択肢:

- **(A) `silence_zero` の刺激をデジタル無音に戻す** — 役割 `silence_zero` を
  「サンプル値ゼロの緩衝」に定義し直し（合成側と同じ意味論）、`rr_silence`（SP のみの
  実レンダ）は**要件ではなく所見**として記録に残す。→ 候補 A が
  `gain_invariance`・`silence_zero` を通り、win100/win200 が `monotone_response` も通る
  ため、**4 軸とも凍結できる見込み**（要再測）。
- **(B) `silence_zero` の許容を実レンダ用に定める**（例: 校正セット内で最も静かな
  条件に対する相対量）。→ 事後に閾値を作る形になるため、規律上は (A) より弱い。
- **(C) 昇格を諦める** — TRF spec を凍結せず、PR-2 を恒久 BLOCKED とする。

いずれも「実レンダ校正で 4 軸を確定する」という裁定 1 の目的自体は変えない。
**(A) を推す**（要件の意味 =「無音に対して 0 を返すか」を保ったまま、
経路の物理（SP でも調波音源が鳴る）と要件の混同だけを解く）。

→ **User 裁定 2026-08-21: (A) 採用**（名称は `zero_input_false_positive` へ明確化、
`rr_silence` は `pipeline_silence_residual` として保存、(B)(C) は不採用）。
実施内容は `s7_b1_amendment_zero_input.md`、結果は §5b。

## 5b. 再測定の結果（amendment 後・2026-08-21）

User 裁定は (A) を採用した。amendment（`s7_b1_amendment_zero_input.md`）を**単独 commit /
pin**（`07d90fe`）してから、**全候補を最初から**再測定した。

| 軸 | 生存 | 勝者 | ε | 決着したキー |
|---|---|---|---|---|
| `excess_tail_voiced_ms` | 4/12 | `A_pyin_voiced_flag\|win100\|hop10` | 10 ms | #3 → #5 |
| `release_after_score_boundary_ms` | 4/12 | 同上 | 10 ms | #3 → #5 |
| `tail_f0_persistence` | 6/12 | 同上 | 0.0333 | #3 → #5 |
| `terminal_mel_persistence` | 3/3 | `M2_2048_512_80` | 0.0387 | #3 |

**4 軸すべてが 6 要件を PASS。`spec_version = 1.0` / `freeze_status = frozen` へ昇格した。**

落ちた候補と理由（実レンダ校正が実際に選別した）:

- 候補族 B（RMS + 自己相関ゲート）= **全滅 / `gain_invariance`**。絶対 RMS 閾値が
  線形ゲインに不変でない（誤差 15–30 ms / 比 0.05–0.10）
- 候補 A の `win300` = **`monotone_response`**。300 ms 窓が 300 ms の終端窓を飽和させ、
  梯子の最終段の増分が 0 になる（ms 軸 2 本）

`zero_input_false_positive` は**全候補が 0.0 で通過**した。すなわちこの要件は今回
**1 つも候補を落としていない**。これは要件が働かなかったのではなく、
「測定器が無から有声を作らない」という**床の検査**として設計どおりの結果である
（旧 `silence_zero` が全滅させていたのは、刺激が無入力でなかったからである = §3）。

決着キーの正直な内訳: ms 2 軸と比 1 軸は、まずキー #3（`monotone_min_step` 降順）で
`hop10` 族が `hop5` 族に勝ち、最後の `win100` vs `win200` は**キー #5（`window_ms` 昇順）**
で割れた。つまり最終差は応答余裕ではなく**測定設定の選好**（狭い窓を先に採る）である。
凍結済みの順位付け規則どおりだが、「物理的信用度で決まった」とは言わない。
`terminal_mel_persistence` はキー #3（応答余裕）で決着した。
**どの軸も最終手段の `candidate_id` までは落ちていない。**

昇格条件（User 裁定 7 項目）の照合:

| 条件 | 実測 |
|---|---|
| 6 要件 PASS（4 軸とも） | ✅ |
| winner が機械規則で一意 | ✅（`rank_order` 先頭 = 選定候補・重複なし） |
| same-process reproducible | ✅ 誤差 0.0 |
| cross-process reproducible | ✅ 誤差 0.0（独立プロセス実測） |
| analysis stack pin 一致 | ✅ numba 0.66.0 / librosa 0.11.0 / numpy 2.4.6 |
| prereg SHA 一致 | ✅ 3 点 + manifest。manifest がレンダ時に見た事前登録 sha も現在と一致 |
| 360-cell contamination | ✅ 0（ラベル入力も 0） |

§12-0-D（PR-2 開始 Gate）9 項目も再照合し、**9/9 充足**した。

## 6. 生成物

| ファイル | 内容 |
|---|---|
| `s7_b1_real_render_manifest.json` | 11 レンダ + 2 派生の sha256・境界・命令フレーム・容器 pin |
| `trf_measurement_spec_real_render_rc2.json` | 実レンダ校正の結果 spec（`1.0-rc2` / `blocked`、全候補の実測値つき） |
| `trf_measurement_spec.json` | **`1.0` / `frozen`**（実レンダ校正・amendment 後の再測定） |
| `trf_measurement_spec_synthetic_rc1.json` | 合成校正の spec（`1.0-rc1`。歴史的成果物として保存） |
| `s7_b1_amendment_zero_input.md` | `zero_input_false_positive` への改訂（再測定前に単独 pin） |
| `s7_b1_pipeline_silence_diagnostic.json` | 降格した `rr_silence` の観測記録（pass/fail に使わない） |

波形（14 本・計 ~4 MB）は `/home/user/s7work/out/b1_real_render_v2/` に置いた
（amendment 後の再レンダ。v1 は `/home/user/s7work/out/b1_real_render/`）。
sha256 は manifest に全件記録してある。**決定論の照合は `samples_sha256`（標本列）で行う**:
float WAV の容器 sha は libsndfile が PEAK チャンクへ書き出し時刻を入れるため再現しない
（v1 と v2 で標本列は完全一致・容器 sha のみ相違、を実測）。
