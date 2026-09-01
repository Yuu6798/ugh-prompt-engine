"""threshold budget と holdout gate（設計正本 §10.2–10.4）。"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from voice_genesis.calibration.observables import q95
from voice_genesis.calibration.vocab import ClaimCeiling, Domain, EvidenceClass


def threshold_margin(e_use: float, u_gt: float, u_num: float) -> float:
    """`M[i] = E_use - U_GT - U_num`（加算、RSS ではない）。`<=0` なら ABSOLUTE は
    NOT_EVALUABLE（E_use を緩めない）。"""
    return e_use - u_gt - u_num


@dataclass(frozen=True)
class EUseEvidenceRow:
    """E_use evidence table の 1 行、必須 13 列（設計正本 §10.2）。"""

    construct_id: str
    unit: str
    domain: str
    intended_use: str
    maximum_claim: str
    e_use_value: float | None
    derivation_rule: str
    evidence_class: EvidenceClass
    source_id_or_url: str
    source_checked_at: str
    source_hash_or_version: str
    applicability_argument: str
    review_status: str

    def __post_init__(self) -> None:
        if self.evidence_class == EvidenceClass.UNJUSTIFIED and self.e_use_value is not None:
            raise ValueError(
                "EUseEvidenceRow: evidence_class=UNJUSTIFIED は数値 e_use_value を"
                " 持てない（設計正本 §10.2: UNJUSTIFIED に数値 placeholder を作らない）"
            )


def auto_ceiling_for_unjustified(a_priori_truth_order_exists: bool) -> ClaimCeiling:
    """E_use 根拠化不能時の自動 ceiling（§10.2）: 独立 truth order が事前に立てば
    DIRECTIONAL、立たなければ DIAGNOSTIC_ONLY。NOT_EVALUABLE へは落とさない。"""
    if a_priori_truth_order_exists:
        return ClaimCeiling.DIRECTIONAL
    return ClaimCeiling.DIAGNOSTIC_ONLY


@dataclass(frozen=True)
class InstanceMargin:
    """ABSOLUTE gate の per-instance 入力。"""

    instance_id: str
    domain: Domain
    eligible: bool
    ae: float
    e: float
    u_gt: float
    u_num: float
    e_use: float


@dataclass(frozen=True)
class InvariancePair:
    """gate4' の invariance axis 上の 1 pair。

    `pair_id` は axis 内で一意な観測識別子（Codex レビュー 2026-09-01 採用）:
    同一観測を複数回カウントして ">= 5 pairs" を水増しできないよう、
    `absolute_gates` は axis 内で `pair_id` が重複したら gate4' を FAIL させる。
    """

    pair_id: str
    axis: str
    ds: float
    e_use_i0: float
    e_use_ia: float


@dataclass(frozen=True)
class AbsoluteGateResult:
    gate1_all_eligible: bool
    gate2_q95: bool
    gate_max: bool
    gate3_bias_budget: bool
    gate4_invariance: bool
    gate5_detection: bool
    g_values: tuple[float, ...]
    passed: bool
    failure_reasons: tuple[str, ...]


def absolute_gates(
    per_instance: Sequence[InstanceMargin],
    *,
    u_rep: float,
    u_proc: float,
    invariance_pairs_by_axis: Mapping[str, Sequence[InvariancePair]],
    declared_invariance_axes: Collection[str],
    fdr0: float,
    fnr1: float,
    min_count_met: bool,
    control_gate: str = "APPLICABLE",
) -> AbsoluteGateResult:
    """§10.3 の ABSOLUTE holdout gate 一式。

    ```
    G[i] = AE[i] + U_GT[i] + U_num[i] + U_rep + U_proc - E_use[i]
    gate 1: 全 PRIMARY instance が eligible
    gate 2': q95_i(G[i]) <= 0
    gate max': max_i(G[i]) <= 0
    gate 3: |BIAS| + max_i(U_GT+U_num) + U_rep + U_proc <= median_i(E_use)
    gate 4': 各 invariance 軸 a で
             q95_pairs( dS[a,pair] + U_rep + U_proc - min(E_use_i0,E_use_ia) ) <= 0
             （各軸 >= 5 pairs 必須。未達なら ABSOLUTE 不可）
    gate 5: FDR0 == 0 かつ FNR1 == 0（最小数条件付き。または control_gate
            NOT_APPLICABLE で通過）
    ```

    境界は `<= 0` が PASS（G が厳密に 0 でも PASS）。AE が median 経由で noise を
    部分的に含むための `+U_rep+U_proc` は保守的二重計上であり意図的（修正しない）。

    `declared_invariance_axes` は C0 で凍結した閉集合（Codex レビュー
    2026-09-01 採用）: gate4' はこの宣言済み軸集合を走査し、
    `invariance_pairs_by_axis` に対応する pair が 1 件もない軸があっても
    黙って消えず、明示的に `<5 pairs` として FAIL する。軸内で `pair_id` が
    重複する観測（同一観測の水増しカウント）も gate4' を FAIL させる。

    `invariance_pairs_by_axis` のバケットキーは呼び出し側が組み立てる辞書
    キーに過ぎず、各 `InvariancePair.axis` フィールドと一致している保証は
    ない（Codex レビュー 2026-09-01 P1: 従来はバケットキーを無条件に信頼して
    おり、`axis` が異なる pair を誤ったバケットへ紛れ込ませても検出できな
    かった）。本関数は各バケット内の全 pair について `p.axis == axis`（走査
    中のバケットキー）を検証し、不一致が 1 件でもあれば当該軸を gate4' FAIL
    とする（理由を個別に列挙する。duplicate pair_id 検査より前に行う —
    axis 不一致がある時点でその pair は当該軸の証拠として無効）。
    """
    primary = [i for i in per_instance if i.domain == Domain.PRIMARY]
    if not primary:
        raise ValueError("absolute_gates: no PRIMARY instance provided")

    reasons: list[str] = []

    gate1 = all(i.eligible for i in primary)
    if not gate1:
        reasons.append("gate1: some PRIMARY instance not eligible")

    eligible_primary = [i for i in primary if i.eligible]
    g_values = tuple(
        i.ae + i.u_gt + i.u_num + u_rep + u_proc - i.e_use for i in eligible_primary
    )

    gate2 = bool(g_values) and q95(g_values) <= 0
    if not gate2:
        reasons.append("gate2': q95_i(G[i]) > 0 (or no eligible instance)")

    gate_max = bool(g_values) and max(g_values) <= 0
    if not gate_max:
        reasons.append("gate_max': max_i(G[i]) > 0 (or no eligible instance)")

    if eligible_primary:
        bias_value = abs(float(np.mean([i.e for i in eligible_primary])))
        max_u_gt_num = max(i.u_gt + i.u_num for i in eligible_primary)
        median_e_use = float(np.median([i.e_use for i in eligible_primary]))
        gate3 = bias_value + max_u_gt_num + u_rep + u_proc <= median_e_use
    else:
        gate3 = False
    if not gate3:
        reasons.append("gate3: |BIAS|+max(U_GT+U_num)+U_rep+U_proc > median(E_use)")

    gate4 = bool(declared_invariance_axes)
    if not declared_invariance_axes:
        reasons.append("gate4': no invariance axis declared")

    unknown_bucket_keys = sorted(set(invariance_pairs_by_axis) - set(declared_invariance_axes))
    if unknown_bucket_keys:
        gate4 = False
        reasons.append(
            "gate4': invariance_pairs_by_axis has bucket key(s) not in "
            f"declared_invariance_axes: {', '.join(unknown_bucket_keys)}"
        )

    for axis in declared_invariance_axes:
        pairs = invariance_pairs_by_axis.get(axis, ())
        mislabeled = sorted({p.pair_id for p in pairs if p.axis != axis})
        if mislabeled:
            gate4 = False
            reasons.append(
                f"gate4': axis {axis} bucket contains pair(s) with mismatched "
                f"InvariancePair.axis: {', '.join(mislabeled)}"
            )
            continue
        pair_ids = [p.pair_id for p in pairs]
        duplicate_ids = sorted({pid for pid in pair_ids if pair_ids.count(pid) > 1})
        if duplicate_ids:
            gate4 = False
            reasons.append(
                f"gate4': axis {axis} has duplicate pair_id(s): {', '.join(duplicate_ids)}"
            )
            continue
        if len(pairs) < 5:
            gate4 = False
            reasons.append(f"gate4': axis {axis} has <5 pairs")
            continue
        margins = [p.ds + u_rep + u_proc - min(p.e_use_i0, p.e_use_ia) for p in pairs]
        if q95(margins) > 0:
            gate4 = False
            reasons.append(f"gate4': axis {axis} q95 margin > 0")

    gate5 = (control_gate == "NOT_APPLICABLE") or (
        min_count_met and fdr0 == 0.0 and fnr1 == 0.0
    )
    if not gate5:
        reasons.append("gate5: FDR0/FNR1 not both zero, or min-count not met")

    passed = gate1 and gate2 and gate_max and gate3 and gate4 and gate5
    return AbsoluteGateResult(
        gate1_all_eligible=gate1,
        gate2_q95=gate2,
        gate_max=gate_max,
        gate3_bias_budget=gate3,
        gate4_invariance=gate4,
        gate5_detection=gate5,
        g_values=g_values,
        passed=passed,
        failure_reasons=tuple(reasons),
    )


@dataclass(frozen=True)
class DirectionalPair:
    """DIRECTIONAL gate の pair 単位入力。

    `delta_truth` は truth 単位（例: injected noise fraction の差）の効果量、
    `delta_output` は output 単位（例: 候補 meter が返す HNR dB の差）の効果量。
    construct が truth と output で同一単位を共有しない場合、この 2 つは
    異なる物理量であり単純合算できない（Codex レビュー 2026-09-01 第 3 巡）。

    `sweep_id` は resolvable-pair 最低数（>= 3）判定の単位（Codex レビュー
    2026-09-01 採用）: 設計正本は「resolvable pair >= 3」を **各 sweep ごと**
    に要求する（sweep 間で集約した合計 3 件では不十分。1 sweep に集中して
    いない限り、複数の異なる測定条件から独立に 3 件を揃えることを求める趣旨）。
    """

    pair_id: str
    delta_truth: float
    delta_output: float
    u_gt_i: float
    u_num_i: float
    u_gt_j: float
    u_num_j: float
    correct_sign: bool
    is_adjacent: bool
    sweep_id: str = "default"


@dataclass(frozen=True)
class DirectionalGateResult:
    resolvable_pairs: tuple[str, ...]
    resolvable_count: int
    three_pair_warning: bool
    sweep_resolvable_counts: Mapping[str, int]
    sweeps_below_minimum: tuple[str, ...]
    sweeps_with_warning: tuple[str, ...]
    all_resolvable_correct_sign: bool
    adjacent_reversal_rate: float
    negative_control_failures: int
    positive_control_failures: int
    tau_b: float | None
    passed: bool
    failure_reasons: tuple[str, ...]


def directional_gates(
    pairs: Sequence[DirectionalPair],
    *,
    u_rep: float,
    u_proc: float,
    expected_sweep_ids: Collection[str],
    negative_control_failures: int,
    positive_control_failures: int,
    units_commensurate: bool,
    tau_b: float | None = None,
    noise_floor_exceeded: bool = True,
) -> DirectionalGateResult:
    """§10.4 の DIRECTIONAL holdout gate。

    v1.0 の合算式 `R_ij = (U_GT_i+U_num_i)+(U_GT_j+U_num_j)+2*(U_rep+U_proc)` は
    truth 単位 (`U_GT`/`U_num`) と output 単位 (`U_rep`/`U_proc`) を単純加算して
    おり、truth と output の construct 単位が異なる候補（例: truth=injected
    noise fraction、output=HNR dB）では無意味になる（Codex レビュー 2026-09-01
    第 3 巡）。そのため resolvability を単位健全な二連言へ分解する:

    ```
    (a) truth 側 resolvability（truth 単位のみ、事前決定可能）:
        Delta_truth(i,j) > (U_GT_i+U_num_i) + (U_GT_j+U_num_j)
    (b) output 側有意性（output 単位。§10.4「各 effect が noise floor 超過」の
        定式化）:
        |Delta_output(i,j)| > 2*(U_rep+U_proc)
    resolvable(i,j) <=> (a) かつ (b)
    ```

    `units_commensurate=True`（truth と output が同一単位で比較可能）の場合は
    v1.0 の合算式を **追加で** 課す（単位可換なケースで v1.0 より判定基準が
    弱くならないようにするため）:

    ```
    (c) Delta_truth(i,j) > (U_GT_i+U_num_i)+(U_GT_j+U_num_j)+2*(U_rep+U_proc)
    resolvable(i,j) <=> (a) かつ (b) かつ (c)   [units_commensurate=True のみ]
    ```

    いずれも厳密不等号。resolvable pair の最低数 (>= 3) は **sweep ごと**に
    課される（Codex レビュー 2026-09-01 採用: sweep 間で集約した合計ではなく、
    宣言された全 sweep がそれぞれ独立に >= 3 件を満たさなければ gate は
    FAIL。「ちょうど 3」の警告 (`three_pair_warning` 相当) も sweep 単位で
    立て、`sweep_resolvable_counts` / `sweeps_below_minimum` /
    `sweeps_with_warning` として結果に含める）。`expected_sweep_ids` は C0 で
    凍結した閉集合として渡す（Codex レビュー 2026-09-01 採用）: 観測 pair から
    sweep 集合を逆算すると、有効な出力が 1 件もない sweep が黙って消えて
    しまうため、宣言済みの全 sweep を走査し、observed pair が 0 件の sweep は
    `sweeps_below_minimum`（延いては gate FAIL、NOT_EVALUABLE 側へ写像）に
    明示的に含める。全 resolvable **adjacent** pair の正符号（§10.4「全
    resolvable adjacent pair の正符号」— non-adjacent な resolvable pair は
    記録されるが符号閾値には数えない）、`adjacent_reversal_rate == 0`、
    negative/positive control 失敗数 == 0、sweep 内で `pair_id` が重複する
    観測がないこと、が必須（noise floor 超過は (b) に統合済みだが、呼び出し側が
    追加の外部判定を持つ場合のための `noise_floor_exceeded` 引数も残す）。
    `tau_b` は記録するのみで PASS 閾値には決して使わない。
    """
    reasons: list[str] = []

    pair_ids_by_sweep: dict[str, list[str]] = {}
    for p in pairs:
        pair_ids_by_sweep.setdefault(p.sweep_id, []).append(p.pair_id)
    duplicate_pair_ids: list[str] = []
    for sweep_id, ids in pair_ids_by_sweep.items():
        dupes = sorted({pid for pid in ids if ids.count(pid) > 1})
        if dupes:
            duplicate_pair_ids.append(f"sweep {sweep_id}: {', '.join(dupes)}")
    if duplicate_pair_ids:
        reasons.append(
            "duplicate pair_id(s) within sweep: " + "; ".join(duplicate_pair_ids)
        )

    resolvable: list[DirectionalPair] = []
    for p in pairs:
        r_truth = (p.u_gt_i + p.u_num_i) + (p.u_gt_j + p.u_num_j)
        truth_resolvable = p.delta_truth > r_truth
        output_significant = abs(p.delta_output) > 2 * (u_rep + u_proc)
        ok = truth_resolvable and output_significant
        if units_commensurate:
            r_combined = r_truth + 2 * (u_rep + u_proc)
            ok = ok and (p.delta_truth > r_combined)
        if ok:
            resolvable.append(p)

    resolvable_count = len(resolvable)

    sweep_ids = sorted(set(expected_sweep_ids))
    sweep_resolvable_counts: dict[str, int] = {s: 0 for s in sweep_ids}
    for p in resolvable:
        if p.sweep_id in sweep_resolvable_counts:
            sweep_resolvable_counts[p.sweep_id] += 1

    sweeps_below_minimum = tuple(s for s in sweep_ids if sweep_resolvable_counts[s] < 3)
    sweeps_with_warning = tuple(s for s in sweep_ids if sweep_resolvable_counts[s] == 3)
    every_sweep_meets_minimum = bool(sweep_ids) and not sweeps_below_minimum
    three_pair_warning = bool(sweeps_with_warning)

    if not sweep_ids:
        reasons.append("no expected sweep declared")
    if sweeps_below_minimum:
        reasons.append(
            "resolvable pair count < 3 in sweep(s): " + ", ".join(sweeps_below_minimum)
        )

    adjacent_resolvable = [p for p in resolvable if p.is_adjacent]
    all_correct = all(p.correct_sign for p in adjacent_resolvable)
    if not all_correct:
        reasons.append("not all resolvable adjacent pairs have correct sign")

    reversals = sum(1 for p in adjacent_resolvable if not p.correct_sign)
    adjacent_reversal_rate = (
        (reversals / len(adjacent_resolvable)) if adjacent_resolvable else 0.0
    )
    if adjacent_reversal_rate != 0.0:
        reasons.append("adjacent_reversal_rate != 0")

    if negative_control_failures != 0:
        reasons.append("negative control failures != 0")
    if positive_control_failures != 0:
        reasons.append("positive control failures != 0")
    if not noise_floor_exceeded:
        reasons.append("effect does not exceed noise floor")

    passed = (
        every_sweep_meets_minimum
        and not duplicate_pair_ids
        and all_correct
        and adjacent_reversal_rate == 0.0
        and negative_control_failures == 0
        and positive_control_failures == 0
        and noise_floor_exceeded
    )
    return DirectionalGateResult(
        resolvable_pairs=tuple(p.pair_id for p in resolvable),
        resolvable_count=resolvable_count,
        three_pair_warning=three_pair_warning,
        sweep_resolvable_counts=sweep_resolvable_counts,
        sweeps_below_minimum=sweeps_below_minimum,
        sweeps_with_warning=sweeps_with_warning,
        all_resolvable_correct_sign=all_correct,
        adjacent_reversal_rate=adjacent_reversal_rate,
        negative_control_failures=negative_control_failures,
        positive_control_failures=positive_control_failures,
        tau_b=tau_b,
        passed=passed,
        failure_reasons=tuple(reasons),
    )


__all__ = [
    "threshold_margin",
    "EUseEvidenceRow",
    "auto_ceiling_for_unjustified",
    "InstanceMargin",
    "InvariancePair",
    "AbsoluteGateResult",
    "absolute_gates",
    "DirectionalPair",
    "DirectionalGateResult",
    "directional_gates",
]
