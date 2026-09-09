---
document_id: VG-METER-CAL-DEBT-DESIGN-v1.4
project: VoiceGenesis
document_class: CANONICAL_DESIGN_REVISION
status: APPROVED_DESIGN_REVISION / NOT_PREREGISTERED / EXECUTION_NOT_AUTHORIZED
design_revision: "1.4"
revises: VG-METER-CAL-DEBT-DESIGN-v1.3
base_document_path: voice_genesis/calibration/DESIGN_VG_METER_CAL_DEBT_v1.3.md
base_document_sha256: 68b4d80330582d2b85728670fbff5d03884ace21ff6ae5fd62ddf571cf041c74
revision_rule: v1.3 は read-only。本書は v1.1 §0/§V6 の改訂規約（in-place 改変禁止・
  新 revision を append-only で作成）に従う新 revision であり、本書に明記した節のみ
  v1.3（およびその上流の v1.2/v1.1/v1.0）を上書きする。明記のない全ての節は v1.3 が
  引き続き正。
evidence_basis:
  - "P2 棄権 census (2026-09-09): campaign.diagnose --dump-values による
    TILT_GT (F0 依存 harmonic 12 候補) / APERIODICITY_GT (全 24 候補) の
    control class x missing_reason 全数走査
    (scratchpad/v14/p23/p23_report.txt)"
  - "P3 極性 census (2026-09-09): APERIODICITY_GT 全候補の正例 sweep 行に
    対する Kendall tau 符号照合 (同上)"
authorship:
  design_compiler: "CLAUDE (Fable session, 2026-09-09)"
  approving_authority: "USER 裁定 (2026-09-09): 実装ルート = Claude 完結。
    前提3/前提4は User 責任で承認（P2 census の採用条件は維持）。"
execution_authorized: false
meter_changes_authorized: false
run11_measurement_entry_authorized: false
note_on_execution: 実行は v1.0 §18 の 3 承認 Gate（campaign 実行 / C0 freeze / seal 受容）
  に引き続き従う。本書の承認は設計改訂の承認であり実行授権ではない。
---

# RUN10-CAL 設計 v1.4 — 棄権 2 経路の統一・DIRECTIONAL 極性 preregistration・記録の観測性是正

**本書は確定した統治文書である（PR #354 round 1 finding #4 是正: 旧文は
`DRAFTING`/「起草中」のまま §Y1–§Y4 を「後続の docs コミットで append する」と
予告していたが、§Y1（P1–P3 実測表）/ §Y2（棄権と極性の preregistration まとめ）/
§Y3（記録欠陥の是正まとめ）/ §Y4（答えた問い・答えていない問い・負債の terminal
status）はいずれも既に本書へ append 済みであり、`DESIGN_DOC_CHAIN` 先頭として
参照されている）。§Y0 は Design Memo（scratchpad/design_memo_v14.md）の「前提と
なる設計判断」を verbatim 転記したもの。**

WP-A（コード + テスト）は本書 §Y0 の前提 1–7 を実装対象とし、既に
`voice_genesis/calibration/{fixtures/controls.py, candidates/registry.py,
campaign/selection_stage.py, campaign/holdout_stage.py, observables.py,
c0_freeze.py, c0_validate.py, approvals.py, campaign/diagnose.py, tests/**}` へ
反映済みである（コミット履歴 = ブランチ `claude/run10-cal-v1-4`）。前提 8/9 は
文書化のみで WP-A の実装対象外。

## §Y0. 前提となる設計判断（確定済み。実装で再解釈しない）

Design Memo（RUN10-CAL-v1.4）「前提となる設計判断」節の verbatim 転記。

1. **棄権（abstention）の定義** = 「測定器が測定不能を正しく申告した記録」。**負例側（negative control）でのみ非発火として算入**し、正例・PRIMARY・coverage・margin では従来どおり欠落 = 失敗。棄権を認める組は **freeze 前に preregistration**（registry → `candidate_space`/`selection_rule` の pin に入る）し、C-1 census（P2）で「正例で同じ理由の棄権が 0 件」を実測してから採用する。
2. **棄権の 2 経路を同一の閉語彙で扱う**: (A) F0 prepass skip（ledger `measurement_missing` reason `F0_UNUSABLE`、record 皆無）と (B) `MeterOutput.missing_reason`（`vocab.MissingReason`、record あり）。round 20 契約「非空 group 内の missing/invalid は無条件失敗」は **「宣言されていない理由の missing / ineligible は失敗」** に狭める（宣言済み理由のみ免責）。
3. (A) の語彙は `SANCTIONED_ABSTENTIONS = {(SILENCE, F0_UNUSABLE), (NOISE_ONLY, F0_UNUSABLE)}`。根拠 = 無声対照に F0 は存在しないため F0 依存候補の skip は正しい棄権（2dde4014 実測: NOISE_ONLY 行で F0 が「usable」だった probe #2–4 は HNR −9.7〜−9.8 dB で正しく非発火、#0/#1 は skip。両方とも非発火が正）。PURE_SINE 等には拡張しない。
4. (B) の語彙は **候補ごと**に `Candidate.abstention_reasons: frozenset[MissingReason]`（既定 空）。v1.4 で宣言するのは `M2A-B0-AUTOCORR-PERIODICITY: {OUTPUT_MISSING}` のみ（`hnr_db_approx` が非有限 = 周期成分なし = 棄権）。他候補は P2 census の結果を見て **宣言しない**（census で正例に棄権があれば宣言不可）。
5. **DIRECTIONAL 極性は宣言する**: `Candidate.truth_polarity: Literal[+1, -1]`（construct の変化方向 / truth の変化方向）。`gates._same_nonzero_sign` と `selection_stage.build_candidate_criteria` の tau / reversal は `polarity * delta_output` を使う。既存 `DirectionalPair.correct_sign` は holdout 側で同じ式から再計算する（正本は 1 箇所 = `observables` に `apply_polarity()` を置き両者が呼ぶ。複製禁止）。v1.4 で宣言: `M2A-B0-AUTOCORR-PERIODICITY = -1`（HNR は noise fraction と逆相関）、他の APERIODICITY 候補は construct から機械的に決める（`injected_noise_fraction` 系 = +1、`harmonic_to_noise_ratio` 系 = −1、`world_d4c_aperiodicity` = +1）。宣言と P3 census の tau 符号が矛盾する候補は **宣言せず** `NO_POLARITY` として DIRECTIONAL 不適格（ceiling を DIAGNOSTIC_ONLY に cap）。
6. **記録の観測性**: `MeterHoldoutResult.gate_detail` に gate 5 の `control_detection`（fdr0 / fnr1 / n_neg / n_pos / min_count_met / negative_control_failures / positive_control_failures / negative_control_sanctioned_abstentions）と `margins_summary`（n, |e| q50/q95/max, |BIAS|, U_GT+U_num, U_rep, U_proc, median E_use, G q95/max）、DIRECTIONAL には `pairs_summary`（resolvable_count, correct_count, reversal_count, kendall_tau, polarity）を **常に**書く。
7. **正規化 MAE の分母**: `observables.error_terms` の `re = ae / max(|truth|, zero_guard)` は真値 0 行で発散（2dde4014: TILT 第 1 順位要素 2e8〜7e9、順位が真値 0 行の絶対誤差だけで決まる縮退）。v1.4 は分母の floor を **construct の E_use（absolute mode）** に置換（`re = ae / max(|truth|, E_use)`）。relative mode（F0 の 20 cent）は従来の |truth|（真値 0 なし）。`selection_rule_sha` は変わる（v1.4 preregistration）。
8. **APERIODICITY の微小段** 0→0.01→0.03→0.1 は Δtruth < 2(U_GT+U_num)=0.128 で構造的に解像不能。fixture 水準は凍結行列の一部であり v1.4 では変更しない（文書化のみ。v1.4 doc §Y4「答えていない問い」に登録）。
9. **TILT 精度**（|e| が真値 0/−6 で 0.28、−12/−18/−24 で 14.5〜14.9 の段差）は **測定器を触る前に P1 で生成器/測定器を切り分ける**。P1 の結果が「測定器」なら測定器の再設計は v1.5（段階 1 へ戻る）、「生成器」なら fixture 修正 = 新 revision の行列。いずれも v1.4 では実装しない。

## §Y1. 実測（WP-P、日付 = `date -u` 実測）

WP-P は probe のみで、本番コード（`voice_genesis/` 配下）は一切変更していない
（`campaigns/**`・`~/.vg_cal/` も無変更）。生データは scratchpad
`v14/p1/`・`v14/p23/` に保存済み。実行日時は各 probe スクリプトの
`date -u` 実測（P1: 2026-09-09T07:30:32Z、P2/P3: 2026-09-09 07:28 UTC）。

### §Y1.1 P1 — TILT 段差誤差の生成器 vs 測定器 切り分け

**判定: `VERDICT = METER_OFF`**（`scratchpad/v14/p1/p1_report.txt` §0）。

生成器（`fixtures/generators/tilt.py` + `common.finalize`）は無罪:
全 5 水準 × 2 f0（grid 10 セル）+ holdout 10 行のいずれも
`|rendered_slope − truth| ≤ 0.033` dB/oct（判定閾値 ±0.5 の 1/15 以下）。
測定器は truth F0 を渡す限り正しい（12 候補すべて `|measured − rendered| ≤
0.03` dB/oct）。段差 −14.5 は再現したが、truth slope の関数としてではなく
**campaign が注入する F0 推定値（prepass `F0-PYIN-FRAME2048-HOP512`）の
量子化誤差の関数として**再現した。

**Grid 10 セル**（`sr 48000 / dur 2.0 s / gain −12 dBFS / noise_clean /
context steady-isolated / f0_meter = truth f0`。`harmonics_kept` は独立測定
= 1..8、全セルで floor+20 dB 条件と `k·f0<0.45·sr` 条件を満たす。値は
`p1_table_combined.csv` より verbatim）:

| case | truth | rendered_slope | harmonics_kept | OLS-K4-BH (measured) | e = OLS4−truth | TS-K6-BH (measured) | e = TS6−truth |
|---|---:|---:|---|---:|---:|---:|---:|
| GRID_s0_f130.813 | 0.0 | −0.0251 | 1..8 | −0.0015 | −0.0015 | −0.0029 | −0.0029 |
| GRID_s0_f391.995 | 0.0 | 0.0028 | 1..8 | 0.0001 | 0.0001 | 0.0001 | 0.0001 |
| GRID_s-6_f130.813 | −6.0 | −6.0251 | 1..8 | −6.0013 | −0.0013 | −6.0026 | −0.0026 |
| GRID_s-6_f391.995 | −6.0 | −5.9972 | 1..8 | −5.9999 | 0.0001 | −5.9999 | 0.0001 |
| GRID_s-12_f130.813 | −12.0 | −12.0250 | 1..8 | −12.0013 | −0.0013 | −12.0027 | −0.0027 |
| GRID_s-12_f391.995 | −12.0 | −11.9972 | 1..8 | −11.9999 | 0.0001 | −11.9999 | 0.0001 |
| GRID_s-18_f130.813 | −18.0 | −18.0253 | 1..8 | −18.0013 | −0.0013 | −18.0024 | −0.0024 |
| GRID_s-18_f391.995 | −18.0 | −17.9973 | 1..8 | −17.9999 | 0.0001 | −17.9999 | 0.0001 |
| GRID_s-24_f130.813 | −24.0 | −24.0248 | 1..8 | −24.0009 | −0.0009 | −24.0030 | −0.0030 |
| GRID_s-24_f391.995 | −24.0 | −23.9988 | 1..8 | −24.0000 | 0.0000 | −23.9999 | 0.0001 |

**Holdout 10 行（注入 F0 の再現実験）**: campaign が実際に注入した
realized F0（2 値: `131.4704 Hz` = truth 130.813 の +8.68 cent、8 行 /
`130.7132 Hz` = truth 130.813 の −1.32 cent、2 行）を測定器に渡した結果
（`p1_mechanism.json` より。`harmonics_kept` = 独立測定 1..8。OLS-K4-BH は
`candidates_f0_injected` を、TS-K6-BH は `campaign_measured_TS_K6_BH` /
`candidates_f0_injected` を丸め3桁で転記。`e` は `measured − truth`）:

| row_id | truth | f0_injected | Δf0 | rendered_slope | harmonics_kept | OLS-K4-BH | e(OLS4) | TS-K6-BH | e(TS6) |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| f0c4078c24 | −12.0 | 131.4704 | +8.68c | −12.025 | 1..8 | −51.507 | −39.507 | −55.461 | −43.461 |
| 13755ada0b | −12.0 | 131.4704 | +8.68c | −12.033 | 1..8 | −21.083 | −9.083 | −34.644 | −22.644 |
| dddefcf0a5 | −24.0 | 131.4704 | +8.68c | −23.980 | 1..8 | −30.099 | −6.099 | −38.929 | −14.929 |
| a83a036f14 | −12.0 | 131.4704 | +8.68c | −11.981 | 1..8 | −18.104 | −6.104 | −26.486 | −14.486 |
| ead18ea829 | −12.0 | 131.4704 | +8.68c | −11.981 | 1..8 | −18.104 | −6.104 | −26.486 | −14.486 |
| e4040707df | −18.0 | 131.4704 | +8.68c | −17.980 | 1..8 | −24.103 | −6.103 | −32.485 | −14.485 |
| 9e117940c0 | −12.0 | 131.4704 | +8.68c | −11.981 | 1..8 | −18.104 | −6.104 | −26.481 | −14.481 |
| 2ba19d8bce (\*) | −12.0 | 131.4704 | +8.68c | −11.968 | 1..8 | −17.745 | −5.745 | −24.703 | −12.703 |
| c405c64eb1 | −6.0 | 130.7132 | −1.32c | −5.981 | 1..8 | −6.124 | −0.124 | −6.281 | −0.281 |
| c2b8725cd8 | 0.0 | 130.7132 | −1.32c | 0.019 | 1..8 | −0.124 | −0.124 | −0.281 | −0.281 |

（\*) `2ba19d8bce` のみ `noise_clean=False`（SNR 20 dB）。probe は
`render_root_secret` を持たないため実現ノイズが異なる——campaign 側の
probe 分散 `[-23.502, -25.727]`（`campaign_measured_TS_K6_BH` 5 probe）の
内側であり再現とみなす（`p1_report.txt` §3）。他 9 行は clean = RNG 不使用
のため波形が bit 一致し、`campaign_measured` と `candidates_f0_injected` は
小数第 3 位まで一致する。

**f0 感度掃引の要約**（`p1_f0_sensitivity.csv`。truth −12 の 2 行で
`f0_meter` を ±30 cent 掃引し、`|e| ≤ E_use = 2.0` dB/oct に収まる帯）:

- `a83a036f14`（44.1 kHz / 1.0 s）: K4-BH ±4 cent、他 10 候補 ±2 cent、
  TS-K8-HANN は `[−2, +1]` cent。OLS-K6-BH 参考値: −4c → −14.12、
  −2c → −12.54、0 → −11.99、+2c → −12.54、+4c → −14.18、+8.7c → −23.99。
- `f0c4078c24`（48 kHz / 2.0 s）: K4-BH / TS-K4-BH ±2 cent、他 ±1 cent、
  TS-K8-HANN は `[0, +1]` cent。OLS-K6-BH 参考値: −2c → −14.19、
  0 → −12.00、+2c → −14.16、+4c → −21.75。
- → **TILT 測定器の実効 F0 許容誤差は ±1〜2 cent**。F0 construct 自身の
  許容は relative mode で 20 cent（§Y0 前提 7）、prepass の pYIN は
  10 cent グリッドで量子化する——**「F0 段の合格品質」と「TILT 段が要求
  する F0 品質」が 1 桁ずれている**。

**機構**（`p1_report.txt` §4）: `candidates/impl/tilt_harmonic.py::
harmonic_amplitudes_db` は `target_bin = round(k·f0/bin_hz)` で決まる
固定 bin を**ピーク探索せずに**読む。注入 F0 に相対誤差 δ があると
k 次倍音の周波数ずれは `k·δ·f0` [Hz] で **k に比例して増大**する一方、
窓のメインローブ幅は `sr/L`（L=分析長）で固定である。高次ほど窓の
サイドローブを読み、k 依存の余分な減衰が乗って傾きが過剰に負へ倒れる。
同じ F0 誤差でも frame 長（周波数分解能）が長いほど誤差が大きい
（48 kHz/2.0 s の方が 44.1 kHz/1.0 s より悪化——`f0c4078c24` の e(TS6)
−43.461 vs `a83a036f14` の −14.486）。**E_use（絶対誤差 2.0 dB/oct）を
満たすのは ±1〜2 cent 以内のみ**であり、F0 construct の 20 cent 許容・
pYIN の 10 cent グリッドのいずれよりも 1 桁厳しい。

### §Y1.2 P2 — 棄権 census（`scratchpad/v14/p23/p23_report.txt`）

**TILT_GT**（F0 依存 harmonic 12 候補、`--f0-candidate F0-PYIN-FRAME2048-HOP512`）:

- 正例（TRUTH_CORE 5 水準 × 3 probe = 15 cell/候補 × 12 候補）:
  **180/180 が `measured_detected`**。`F0_UNUSABLE`/`missing_reason`/
  `ineligible` はいずれも 0 件（abstention 総数 = 0/180）。
- SILENCE: 12 候補すべて **3/3 が `F0_UNUSABLE`**（prepass skip）。
- NOISE_ONLY: 12 候補すべて **3/3 が `F0_UNUSABLE`**（本 census の
  probe 分布では 3 本とも skip。2dde4014 実測とは probe 分布が異なるが
  「NOISE_ONLY で発火した cell は 0」という結論は一致——§5.5 注記 (b)）。

**APERIODICITY_GT**（全 24 候補）:

- `M2A-B0-AUTOCORR-PERIODICITY`: 正例 18/18 が `measured_detected`
  （**AUTOCORR positives 18/18**）。負例は SILENCE 3/3・NOISE_ONLY 3/3が
  すべて `missing_reason=OUTPUT_MISSING`（**negatives 6/6 OUTPUT_MISSING**、
  `hnr_db_approx` が非有限 = 周期成分なし）。confound 18/18 も measured。
- HNR_ACF 系 8 候補（情報のみ・v1.4 は宣言しない）: 正例 18/18 measured、
  `OUTPUT_MISSING` 0。ただし **NOISE_ONLY は 3/3 が measured かつ detected**
  （`hnr_db` ≈ −9.18〜−10.72 dB。predicate 無しのため「有限値=発火」）
  → diagnose verdict `FAIL_NEGATIVE`。棄権宣言では救われない。
- HARMONIC_RESIDUAL 系 12 候補（情報のみ）: 正例 18/18 measured、
  `OUTPUT_MISSING` 0。負例は SILENCE 3/3 `F0_UNUSABLE`、NOISE_ONLY は
  2/3 `F0_UNUSABLE` + 1/3 measured&detected（`residual_fraction` ≈
  0.990〜0.998）→ `FAIL_NEGATIVE`。必要な語彙は `OUTPUT_MISSING` ではなく
  `(NOISE_ONLY, F0_UNUSABLE)`（前提 3 側）だが、残り 1 probe の実発火が
  あるため棄権化しても `FAIL_NEGATIVE` は消えない。
- D4C_WORLD 系 3 候補（情報のみ）: 正例 18/18 が
  `ineligible=INELIGIBLE_DEPENDENCY_ABSENT`（pyworld 未導入）。
  `OUTPUT_MISSING` は 0 件だが測定が成立していないため
  `NOT_MEASURABLE(dependency_absent)`。

**判定**:

- **前提 3（`(NOISE_ONLY, F0_UNUSABLE)` の採用条件）= PASS**
  （TILT_GT F0 依存 12 候補、正例で `F0_UNUSABLE` 0/180）。
- **前提 4（`M2A-B0-AUTOCORR-PERIODICITY: {OUTPUT_MISSING}` の宣言条件）
  = PASS**（正例 18/18 で `OUTPUT_MISSING` 0 件）。

### §Y1.3 P3 — 極性 census（APERIODICITY_GT、正例/TRUTH_CORE セル）

| candidate（代表） | construct | declared | tau | n | consistent |
|---|---|---:|---:|---:|---|
| `M2A-B0-AUTOCORR-PERIODICITY` + HNR_ACF 系 8 件（計 9） | `harmonic_to_noise_ratio` | −1 | −0.9487 | 18 | Y |
| `M2A-HARMONIC-RESIDUAL-*` 12 件 | `injected_noise_fraction` | +1 | +0.9487 | 18 | Y |
| `M2A-D4C-BAND-*` 3 件 | `world_d4c_aperiodicity` | +1 | None（measured cell 0） | 0 | NA |

（全 24 候補の内訳は `p23_report.txt` §4 の P3 表が正。tau は 6 truth 水準
`{0.0, 0.01, 0.03, 0.1, 0.3, 0.6}` 上の Kendall tau-b、`M2A-B0-AUTOCORR-
PERIODICITY` の `(truth, hnr_db)` sanity 表は同 report §4 末尾。）

**判定: 矛盾 0、D4C は NA（pyworld 不在）。`NO_POLARITY` に落ちる候補は
本 census の範囲では 0 件**（ただし v1.4 が実際に registry へ宣言したのは
APERIODICITY_GT の 3 algorithm family のみ——他 meter の DIRECTIONAL 候補は
§Y2 のとおり `truth_polarity` 未宣言のまま `NO_POLARITY` で cap される）。

## §Y2. 棄権と極性の preregistration

**棄権語彙（経路 A、`fixtures.controls.SANCTIONED_ABSTENTIONS`、閉語彙）**:

```
SANCTIONED_ABSTENTIONS = {
    (ControlClass.SILENCE,     "F0_UNUSABLE"),
    (ControlClass.NOISE_ONLY,  "F0_UNUSABLE"),
}
```

（v1.2 の 1 組から v1.4 で 2 組へ拡張。根拠 = §Y1.2 前提 3 PASS。追加は
次 revision の preregistration 経由のみ。)

**棄権語彙（経路 B、`Candidate.abstention_reasons`、候補ごと）**:
v1.4 で宣言するのは `M2A-B0-AUTOCORR-PERIODICITY: {OUTPUT_MISSING}` の
**1 候補のみ**（根拠 = §Y1.2 前提 4 PASS）。他の APERIODICITY_GT 候補
（HNR_ACF 8 件・HARMONIC_RESIDUAL 12 件・D4C_WORLD 3 件）は census が
「正例で同一理由の棄権 0 件」を満たさない、または測定不能
（D4C=ineligible）ため **宣言しない**（§Y1.2 参照情報）。

**規則文（`fixtures.controls.abstained(output, candidate)`が正本）**:
`output.missing_reason in candidate.abstention_reasons and not
output.ineligible` のときのみ「正しい棄権」。round 20 契約「非空 group
内の missing/invalid は無条件失敗」は**「宣言されていない理由の missing /
ineligible は失敗」**へ狭める（宣言済み理由のみ免責）。**負例側
（negative control）でのみ非発火として算入**し、正例・PRIMARY・coverage・
margin では従来どおり欠落=失敗のまま——棄権が認められる組は必ず
freeze 前に preregistration（registry → `candidate_space`/`selection_rule`
の pin に入る）し、C-1 census（P2）で「正例で同じ理由の棄権が 0 件」を
実測してから採用する。

**User 裁定（2026-09-09）**: 前提 3（`(NOISE_ONLY, F0_UNUSABLE)` の棄権化）
と前提 4（`M2A-B0-AUTOCORR-PERIODICITY` の `OUTPUT_MISSING` 棄権宣言）は
**User 責任で承認**（「2についてはユーザー責任で承認します」）。採用条件
（P2 census で正例の棄権 0 件）は維持し、実測（§Y1.2）で満たすことを
確認したうえで実装した。

**極性宣言（`Candidate.truth_polarity`、APERIODICITY_GT のみ）**:

| candidate group | construct | truth_polarity | P3 census |
|---|---|---:|---|
| `M2A-B0-AUTOCORR-PERIODICITY`（1 件） | `harmonic_to_noise_ratio` | −1 | tau=−0.9487, consistent |
| `M2A-HNR-ACF-*`（8 件） | `harmonic_to_noise_ratio` | −1 | tau=−0.9487, consistent |
| `M2A-HARMONIC-RESIDUAL-*`（12 件） | `injected_noise_fraction` | +1 | tau=+0.9487, consistent |
| `M2A-D4C-BAND-*`（3 件） | `world_d4c_aperiodicity` | +1 | NA（pyworld 不在。実測未検証、construct 物理からの機械的宣言を維持） |

正本は `observables.apply_polarity(delta_output, polarity)` の 1 箇所
（`selection_stage.build_candidate_criteria` の tau/reversal と
`holdout_stage.build_directional_gate_inputs` の `delta_output`/
`correct_sign` が両方これを呼ぶ。`gates.py` 自体は無改変——極性は入力側で
適用する）。

**`NO_POLARITY` cap とその波及**: `truth_polarity is None` の DIRECTIONAL
候補は `claim_scope_report()` で `DIAGNOSTIC_ONLY` へ cap される
（`cap_reason="NO_POLARITY"`）。v1.4 は APERIODICITY_GT の 3 algorithm
family（計 24 候補）にしか極性を宣言していないため、**M5_TRANSITION
（7 候補）と M6_IDENTITY（2 候補）の DIRECTIONAL 候補は全て
`NO_POLARITY` で `DIAGNOSTIC_ONLY` に cap される**——これは v1.4 の
claim ceiling の実質的な縮小であり、次 revision で極性を宣言するまで
系全体に及ぶ副作用である（§Y4「答えていない問い」に登録）。

## §Y3. 記録欠陥の是正

**`gate_detail` 3 ブロック**（`MeterHoldoutResult.gate_detail`、ABSOLUTE/
DIRECTIONAL とも常に書く。値は `campaign/holdout_stage.py` の
`control_detection_summary()`/`margins_summary()`/`pairs_summary()` が
生成する JSON-serializable な素通し会計であり、いずれも判定は行わない）:

- `control_detection`（ABSOLUTE/DIRECTIONAL 共通形状）: `fdr0`, `fnr1`,
  `n_neg`, `n_pos`, `min_count_met`, `negative_control_failures`,
  `positive_control_failures`, `negative_control_sanctioned_abstentions`。
- `margins_summary`（ABSOLUTE のみ）: `n`, `ae_q50`, `ae_q95`, `ae_max`,
  `abs_bias`（=|BIAS|）, `u_gt_plus_u_num`, `u_rep`, `u_proc`,
  `e_use_median`, `g_q95`, `g_max`（`gates.AbsoluteGateResult.g_values`
  由来。gate2'/gate_max' の判定量そのもの）。
- `pairs_summary`（DIRECTIONAL のみ）: `resolvable_count`（`gates.
  DirectionalGateResult.resolvable_count` を転記）, `correct_count`,
  `reversal_count`（`DirectionalPair.correct_sign`、極性適用済みの単純
  カウント——`gates.directional_gates()` の resolvability 閾値判定の
  再実装ではない）, `kendall_tau`（記録専用、PASS 判定には使わない）,
  `polarity`（`Candidate.truth_polarity`）。

**正規化 MAE の `truth_floor`**: `observables.error_terms(m, truth,
zero_guard, *, truth_floor=None)` の `RE = AE / max(|truth|, truth_floor
if truth_floor is not None else zero_guard)`。`truth_floor` は construct
の **E_use（absolute mode）** を渡す（`selection_stage.
truth_floor_for_candidate()` が正本。relative mode の construct・
E_use 未定義の construct は `None` = 従来の `zero_guard` 挙動、bit-for-bit
不変）。**`selection_rule_sha()` は `voice_genesis/calibration/
selection.py` のみをハッシュする別モジュールであり、本変更が実際に
生きる `campaign/selection_stage.py`（criteria builder）はここに含まれ
ない**（WP-A block 6 deviation 2）。この面は manifest の
`candidates.*_paths_sha256`（`meter_implementation_paths_sha256` 等、
`c0_freeze.py`/`c0_validate.py` が pin する path+hash マップ）が
`campaign/selection_stage.py` を独立に被覆しており、変更検知の欠落は
ない。

**selection 側の deviation（audit-only key）**: memo は holdout 側の
round 20 相当の「非宣言理由は無条件失敗」契約を selection 側にも
適用するよう読めたが、`selection_stage` はそもそも round-20 型の契約を
持たず（`neg_detections` は常に `fixtures.controls.detected()` ベースで
missing/invalid を無条件に非発火とみなす——holdout 固有の契約とは別物）、
文字どおり適用すると 6 件の既存テストが割れた（WP-A block 3
deviation）。代わりに `candidate_fail_filter_report()` へ **audit-only**
の `negative_control_declared_abstentions`（`FAIL_FILTER_NAMES` に非含有、
eligibility に無影響）を追加し、「両 call site が `fixtures.controls.
abstained()` を直接呼ぶ」という文字どおりの要件（call-site AST テストで
固定）だけを満たした——fire/non-fire の判定ロジックは byte-for-byte 不変。

**diagnose schema 0.4 census**: `campaign/diagnose.py::SCHEMA =
"diagnose/0.4"`。`--dump-values` は候補ごとに `census`
（`_census_block()`: `n_cells`/`measured_detected`/
`measured_not_detected`/`f0_unusable_prepass_skip`/`missing_reason`
（理由別件数）/`ineligible` を control class / `"positive"` /
`"confound"` ごとに集計。P2 census が使った語彙をそのまま内製化）と、
DIRECTIONAL 候補の `kendall_tau_sign`（`_kendall_tau_sign()`。P3 census と
同じ Kendall tau-b の符号、`None`/`+1`/`-1`）を追加する。非 `--dump-values`
の report 形状（verdict/fire rates）は不変。

**`design_revision`/統治文書連鎖**: `c0_freeze._DESIGN_REVISION = "1.4"`、
`c0_validate._ALLOWED_DESIGN_REVISIONS`/`_DESIGN_REVISION_ORDER` に
`"1.4"` を追加。`approvals.DESIGN_DOC_CHAIN` は
`(v1.4, v1.3, v1.2, v1.1, v1.0)` の 5 段（先頭が本書）。新設
`c0_validate._check_sanctioned_abstentions_vocabulary()` は
design_revision >= 1.4 かつ非 rehearsal の manifest に対し、実行時
`fixtures.controls.SANCTIONED_ABSTENTIONS` が本書 §Y2 の 2 組と厳密一致
することを要求する（不一致は `VALIDATION_BLOCKED`）。

## §Y4. 答えた問い / 答えていない問い / 負債の terminal status

**答えた問い（3 件）**:

1. **TILT 段差（|e| が 0.28 と 14.5〜14.9 に分かれる段差）の原因は生成器か
   測定器か** — **測定器**（証拠: §Y1.1 P1、`VERDICT=METER_OFF`）。
   `fixtures/generators/tilt.py`/`common.finalize` は無罪。段差の実体は
   `candidates/impl/tilt_harmonic.py::harmonic_amplitudes_db` の固定 bin
   読み（ピーク探索なし）が、campaign 注入 F0 の 10 cent グリッド量子化
   誤差を k 次倍音で k 倍に増幅すること。
2. **前提 3/4 の棄権宣言は「正例で同じ理由の棄権 0 件」の採用条件を
   満たすか** — **満たす**（証拠: §Y1.2 P2 census、両前提とも PASS。
   0 件）。
3. **APERIODICITY_GT 候補の宣言極性は実測 tau の符号と一致するか** —
   **一致する**（証拠: §Y1.3 P3 census。矛盾 0 件。D4C 3 件のみ pyworld
   不在で NA）。

**答えていない問い（明示）**:

- **pYIN が行ごとにどちらの格子点（+8.68 cent / −1.32 cent）を選ぶかの
  要因**は本 probe の範囲外（§Y1.1 末尾。8/10 行が +8.68 cent 側、
  2/10 行が −1.32 cent 側に落ちたが、行のスペクトル特性との対応関係は
  未調査）。
- **M5_TRANSITION（7 候補）/M6_IDENTITY（2 候補）の DIRECTIONAL 極性
  宣言**は v1.4 の範囲外（§Y2「`NO_POLARITY` cap とその波及」。宣言が
  無いため `DIAGNOSTIC_ONLY` に cap されたまま）。
- **APERIODICITY の微小段 0→0.01→0.03→0.1 の解像不能**（§Y0 前提 8。
  `Δtruth < 2(U_GT+U_num) = 0.128`）は本 revision でも解消しない
  （fixture 水準は据え置き）。C-1 診断（unarmed）の点推定は単調に分離
  しているが（§Y1.3 の HNR sanity 表）、これは holdout の不確かさ収支に
  対する claim 能力を意味しない。
- **TILT 測定器の再設計は v1.5**（§Y0 前提 9。段階 1 へ戻る作業。
  User 承認が必要な meter change）。P1 が示した候補案（v1.4 では
  実装しない）: (a) 倍音ごとの局所ピーク探索（±0.25·f0）+ QIFFT、
  (b) 注入 F0 の精緻化（倍音位置からの F0 再推定 / 10 cent グリッドの
  解除）、(c) TILT 候補が要求する F0 品質を候補宣言に持ち上げ、prepass の
  20 cent 許容と整合しない候補を不適格にする。
- **`M2T-B0-CURRENT-HYBRID` の `claim_ceiling=NONE`**（unit 混在）は
  v1.3 に続き本書でも解かない。
- **FORMANT 検出器の新設計**は v1.3 §X2.2 のまま未着手（別 memo）。

**負債の terminal status（v1.4 マージ時点）**: 直近完走した本番 campaign
`RUN10-CAL-20260908-2dde4014`（`CAMPAIGN_CLOSED`、`.claude/memory/
STATUS.md`）の結果のまま**据え置き**——`F0_CONTROL`/`M2_SPECTRAL_TILT`/
`M2_APERIODICITY`/`M4_RESONANCE` は `DIAGNOSTIC_ONLY`、`M3_FORMANTS`/
`M5_TRANSITION`/`M6_IDENTITY` は `NOT_EVALUABLE`、**`debt_discharged =
false`**。**v1.4 は本番 campaign を 1 度も回していない**（WP-P は
unarmed diagnose・ledger なし、WP-A はコード+テストのみ）。§Y1–§Y3 は
測定意味論と記録の是正であり、それ自体は負債を返済しない。**再 freeze の
前提**は (1) v1.5 で TILT 測定器を修正すること（P1 の帰結。段階 1 へ戻る）、
(2) rehearsal green の実測、(3) 実 gate 承認時刻を実測した再 freeze
（F0/TILT/APERIODICITY の 3 家系）——の 3 点。
