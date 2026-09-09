---
document_id: VG-METER-CAL-DEBT-DESIGN-v1.4
project: VoiceGenesis
document_class: CANONICAL_DESIGN_REVISION
status: DRAFTING
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

# RUN10-CAL 設計 v1.4（起草中）

**本書は起草中の骨子である。§Y0 は Design Memo（scratchpad/design_memo_v14.md）の
「前提となる設計判断」を verbatim 転記したもの。§Y1（P1–P3 実測表）/ §Y2（棄権と極性の
preregistration まとめ）/ §Y3（記録欠陥の是正まとめ）/ §Y4（答えた問い・答えていない
問い・負債の terminal status）は後続の docs コミットで append する（append-only。
本節を含め既に書いた内容の改変はしない）。**

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

（§Y1–§Y4 は後続の docs コミットで append する。）
