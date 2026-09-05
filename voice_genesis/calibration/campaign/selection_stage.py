"""C3a (F0 selection) / C3b (他 family selection) stage
（IMPLEMENTATION_MAP_v1.md §6.4, 設計正本 §9, memo §2.6 ceiling 階級間裁定）。

`selection.select_across_ceilings()` を family ごとに独立に呼び、
`SELECTION_FROZEN` event を記帳する。unseal（§7）が要求する 5 sha 相互参照の
うち 4 つ（`baseline_audit_sha`/`candidate_space_sha`/`selection_rule_sha`/
`selected_candidate_sha`）は `provenance.py` の `_UNSEAL_PREREQUISITE_KINDS`
契約に厳密に従う: これらのフィールド値は参照先イベントの **`artifact_sha`
ではなく `entry_sha`（ledger chain 上のハッシュ）** でなければならない
（`provenance._references_prior_prerequisites` が `prior_entries_by_sha` を
`entry_sha` でインデックスするため）。

`[UNDERSPEC-CAL-D20]` `provenance.py` の unseal 相互参照契約は単一の
`selected_candidate` prerequisite（`candidate_id: str` 1 件）を要求する形状
だが、本キャンペーンは meter family ごとに独立した selection を持つ（§9）。
そのため C3b は **全 non-F0 family の選択結果を 1 つの `selected_candidate`
prerequisite event へ集約**する: `candidate_id` は family→selected_id を
`family:id` 形式で連結した監査用文字列、`artifact_sha` は全 family の
raw/rounded vector を含む canonical summary の sha。個々の family の詳細
（`SelectionOutcome`）は `selection_frozen` event の payload
（`selected_by_family`/`raw_vectors_by_family`/`rounded_vectors_by_family`）
にも別途フルで記録するため、集約による情報損失はない。

F0 selection（C3a）は unseal の 5-sha 相互参照チェーンには参加しない
（`kind="f0_selection_frozen"`。§8「F0 選択は下流候補実行前に完了する
一方向依存」を `state.CampaignPhase.F0_SELECTION_FROZEN` の手続順序で表現
する — `cli.py`/`state.py` が C3b 開始前に F0_SELECTION_FROZEN 到達を要求
する）。

**max_claim_scope capping**（finding #11, 第 11 巡採用）: 凍結 manifest の
`frozen_design.max_claim_scope`（Gate 1 が ABSOLUTE 主張を許す construct id
の集合）に含まれない construct の候補は、selection 前に claim ceiling を
`min(元 ceiling, DIRECTIONAL)` へ capping してから `select_across_ceilings`
へ渡す（scope 外候補は ABSOLUTE pool に入れない）。同じ capped ceiling は
holdout の terminal status 導出（`cli._run_c4`）にも使う。manifest に
`max_claim_scope` が無ければ「scope 未凍結」として `ClaimScopeError` で
fail-closed する（`max_claim_scope_from_manifest`）。capping の事実は
`capped`/`construct`/`original_ceiling`/`capped_ceiling` として
候補ごとに記録し、`run_c3a_f0_selection`/`run_c3b_selection` の payload
（`claim_scope_by_candidate`）へ残す。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import kendalltau

import voice_genesis.calibration.selection as selection_module
from voice_genesis.calibration.campaign import measure_stage
from voice_genesis.calibration.campaign.state import FrozenCampaign
from voice_genesis.calibration.candidates import adapter
from voice_genesis.calibration.candidates.registry import ALL_CANDIDATES, Candidate
from voice_genesis.calibration.canonical import manifest_sha
from voice_genesis.calibration.fixtures.matrix import FixtureRow
from voice_genesis.calibration.observables import bias, error_terms, q95, two_stage_median
from voice_genesis.calibration.selection import CandidateCriteria, SelectionOutcome, select_across_ceilings
from voice_genesis.calibration.vocab import ClaimCeiling


def candidate_space_sha(candidates: Sequence[Candidate] | None = None) -> str:
    """`registry.ALL_CANDIDATES`（または明示指定した候補集合）の canonical
    snapshot sha。C0 で凍結済みの 99 候補宣言を C3 時点で再確認・記録する。"""
    pool = candidates if candidates is not None else ALL_CANDIDATES
    payload = {
        c.candidate_id: {
            "meter": c.meter.value,
            "construct": c.construct,
            "unit": c.unit,
            "algorithm_family": c.algorithm_family,
            "parameters": dict(c.parameters),
            "domain": c.domain,
            "missing_rule": c.missing_rule,
            "independence_tier": c.independence_tier.value,
            "claim_ceiling": c.claim_ceiling.value,
            "complexity_rank": c.complexity_rank,
            "implementation_ref": c.implementation_ref,
        }
        for c in pool
    }
    return manifest_sha(payload)


def selection_rule_sha() -> str:
    """`selection.py` ソース bytes の sha256（deliverable 要求: "sha of
    selection.py source"）。"""
    path = Path(selection_module.__file__)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vectors_jsonable(
    vectors: Mapping[str, tuple[float | int | str, ...]],
) -> dict[str, list[float | int | str]]:
    return {k: list(v) for k, v in sorted(vectors.items())}


@dataclass(frozen=True)
class F0SelectionResult:
    outcome: SelectionOutcome
    f0_selection_frozen_entry_sha: str


def run_c3a_f0_selection(
    campaign: FrozenCampaign,
    criteria: Sequence[CandidateCriteria],
    *,
    fail_filter_reports: Mapping[str, Mapping[str, object]] | None = None,
    claim_scope_reports: Mapping[str, Mapping[str, object]] | None = None,
    invocation_id: str | None = None,
) -> F0SelectionResult:
    """C3a: F0_CONTROL candidates の selection。`f0_selection_frozen` event を
    記帳する（unseal の 5-sha チェーンには参加しない — 上記モジュール
    docstring 参照）。`fail_filter_reports`（finding #8, 第 9 巡採用:
    `candidate_id -> {filter_name: bool}`、`candidate_fail_filter_report()`
    の戻り値をそのまま渡せる）を与えれば `fail_filters_by_candidate` として
    payload に記録する（省略時は空 dict — 呼び出し側が fail filter を未適用
    のまま `criteria.eligible` のみで判定した場合の後方互換）。
    `claim_scope_reports`（finding #11, 第 11 巡採用: `candidate_id ->
    claim_scope_report() の report`）を与えれば `claim_scope_by_candidate`
    として payload に記録する。"""
    outcome = select_across_ceilings(criteria)
    payload = {
        "kind": "f0_selection_frozen",
        "candidate_space_sha": candidate_space_sha(),
        "selection_rule_sha": selection_rule_sha(),
        "family": "F0_CONTROL",
        "selected_candidate_id": outcome.selected_candidate_id,
        "outcome": outcome.outcome,
        "raw_vectors": _vectors_jsonable(outcome.raw_vectors),
        "rounded_vectors": _vectors_jsonable(outcome.rounded_vectors),
        "ineligible_candidates": [list(t) for t in outcome.ineligible_candidates],
        "fail_filters_by_candidate": {
            cid: dict(report) for cid, report in sorted((fail_filter_reports or {}).items())
        },
        "claim_scope_by_candidate": {
            cid: dict(report) for cid, report in sorted((claim_scope_reports or {}).items())
        },
        "invocation_id": invocation_id,
    }
    entry = campaign.ledger.append(payload)
    return F0SelectionResult(outcome=outcome, f0_selection_frozen_entry_sha=entry.entry_sha)


@dataclass(frozen=True)
class SelectionFreezeResult:
    outcomes_by_family: Mapping[str, SelectionOutcome]
    selection_frozen_entry_sha: str
    baseline_audit_prerequisite_sha: str
    candidate_space_prerequisite_sha: str
    selection_rule_prerequisite_sha: str
    selected_candidate_prerequisite_sha: str


def run_c3b_selection(
    campaign: FrozenCampaign,
    criteria_by_family: Mapping[str, Sequence[CandidateCriteria]],
    *,
    baseline_audit_entry_sha: str,
    fail_filter_reports_by_family: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None,
    claim_scope_reports_by_family: Mapping[str, Mapping[str, Mapping[str, object]]] | None = None,
    invocation_id: str | None = None,
) -> SelectionFreezeResult:
    """C3b: F0_CONTROL を除く各 family の selection を独立に
    `select_across_ceilings()` で行い、4 前提 event（`candidate_space`/
    `selection_rule`/`selected_candidate`、`baseline_audit` は呼び出し側が
    C2 で既に記帳した event の entry_sha を渡す）+ 1 つの `selection_frozen`
    event を記帳する。`baseline_audit_entry_sha` は `baseline_stage` が
    `campaign.ledger.append({"kind": "baseline_audit", ...})` した際の
    `LedgerEntry.entry_sha`（`artifact_sha` ではない — モジュール docstring
    参照）。`fail_filter_reports_by_family`（finding #8, 第 9 巡採用:
    `family -> candidate_id -> {filter_name: bool}`）を与えれば
    `fail_filters_by_family` として payload に記録する（省略時は空 dict）。
    `claim_scope_reports_by_family`（finding #11, 第 11 巡採用:
    `family -> candidate_id -> claim_scope_report() の report`）を与えれば
    `claim_scope_by_family` として payload に記録する。
    """
    if "F0_CONTROL" in criteria_by_family:
        raise ValueError("run_c3b_selection: F0_CONTROL selection uses run_c3a_f0_selection")

    outcomes: dict[str, SelectionOutcome] = {}
    for family in sorted(criteria_by_family):
        outcomes[family] = select_across_ceilings(criteria_by_family[family])

    cs_entry = campaign.ledger.append(
        {
            "kind": "candidate_space",
            "artifact_sha": candidate_space_sha(),
            "invocation_id": invocation_id,
        }
    )
    sr_entry = campaign.ledger.append(
        {
            "kind": "selection_rule",
            "artifact_sha": selection_rule_sha(),
            "invocation_id": invocation_id,
        }
    )

    selected_by_family = {
        family: outcome.selected_candidate_id for family, outcome in sorted(outcomes.items())
    }
    aggregate_summary = {
        "selected_by_family": selected_by_family,
        "raw_vectors_by_family": {
            family: _vectors_jsonable(outcome.raw_vectors)
            for family, outcome in sorted(outcomes.items())
        },
        "rounded_vectors_by_family": {
            family: _vectors_jsonable(outcome.rounded_vectors)
            for family, outcome in sorted(outcomes.items())
        },
    }
    aggregate_sha = manifest_sha(aggregate_summary)
    candidate_id_join = (
        "|".join(f"{family}:{cid}" for family, cid in sorted(selected_by_family.items()) if cid)
        or "NONE"
    )
    sc_entry = campaign.ledger.append(
        {
            "kind": "selected_candidate",
            "artifact_sha": aggregate_sha,
            "candidate_id": candidate_id_join,
            "invocation_id": invocation_id,
        }
    )

    selection_frozen_payload = {
        "kind": "selection_frozen",
        "baseline_audit_sha": baseline_audit_entry_sha,
        "candidate_space_sha": cs_entry.entry_sha,
        "selection_rule_sha": sr_entry.entry_sha,
        "selected_candidate_sha": sc_entry.entry_sha,
        "selected_by_family": selected_by_family,
        "outcomes_by_family": {
            family: outcome.outcome for family, outcome in sorted(outcomes.items())
        },
        "fail_filters_by_family": {
            family: {cid: dict(report) for cid, report in sorted(by_candidate.items())}
            for family, by_candidate in sorted((fail_filter_reports_by_family or {}).items())
        },
        "claim_scope_by_family": {
            family: {cid: dict(report) for cid, report in sorted(by_candidate.items())}
            for family, by_candidate in sorted((claim_scope_reports_by_family or {}).items())
        },
        **aggregate_summary,
        "invocation_id": invocation_id,
    }
    sf_entry = campaign.ledger.append(selection_frozen_payload)

    return SelectionFreezeResult(
        outcomes_by_family=outcomes,
        selection_frozen_entry_sha=sf_entry.entry_sha,
        baseline_audit_prerequisite_sha=baseline_audit_entry_sha,
        candidate_space_prerequisite_sha=cs_entry.entry_sha,
        selection_rule_prerequisite_sha=sr_entry.entry_sha,
        selected_candidate_prerequisite_sha=sc_entry.entry_sha,
    )


# ---------------------------------------------------------------------------
# CandidateCriteria construction from real measurement records
# [UNDERSPEC-CAL-D13] (truth field) / [UNDERSPEC-CAL-D16] (criteria
# aggregation rule)
# ---------------------------------------------------------------------------

#: family の known-truth field（`c0_freeze._KNOWN_TRUTH_FIELD` と同じ対応
#: だが、他 agent が並行編集中の `c0_freeze.py` には依存せず本モジュールで
#: 独立に宣言する）。FORMANT_GT のみ `pole_freqs_hz`（tuple）の先頭要素
#: （F1）をスカラー truth として使う。
_TRUTH_FIELD_BY_FAMILY: Mapping[str, str] = {
    "F0_CONTROL": "f0_hz",
    "TILT_GT": "slope_db_per_oct",
    "APERIODICITY_GT": "injected_noise_fraction",
    "RESONANCE_GT": "center_hz",
    "TRANSITION_GT": "discontinuity_magnitude",
    "IDENTITY_CAUSAL_SWEEP": "delta",
}


def truth_value_for_row(row: FixtureRow) -> float | None:
    """`[UNDERSPEC-CAL-D13]` family の主要 truth スカラーを返す（FORMANT_GT
    は先頭 pole=F1 を代表値とする）。値が未定義なら `None`。"""
    if row.family == "FORMANT_GT":
        return float(row.pole_freqs_hz[0]) if row.pole_freqs_hz else None
    field = _TRUTH_FIELD_BY_FAMILY.get(row.family)
    if field is None:
        return None
    value = getattr(row, field, None)
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# 共通 fail filter による eligibility 判定（finding #8, 第 9 巡採用）
# ---------------------------------------------------------------------------

#: fail filter 名の閉集合（`candidates.adapter` の 5 種、設計正本 §8、+
#: `candidate_fail_filter_report()` 自身が定義する `positive_rows_absent`/
#: `negative_controls_incomplete`/`coverage_incomplete` の計 8 種。round 17
#: finding #1 採用で `negative_controls_incomplete` を追加、round 28 ADOPT (2)
#: (`[UNDERSPEC-CAL-D64]`) で `coverage_incomplete` を追加、round 30
#: self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`) で `coverage_incomplete` の
#: 母集団・判定を拡張（filter 名自体は増えない）。
#: `candidate_fail_filter_report()` が返す dict のキーと 1:1 対応する。
FAIL_FILTER_NAMES: tuple[str, ...] = (
    "schema_violation",
    "unexplained_nonfinite",
    "within_fresh_process_mismatch",
    "negative_control_false_fire",
    "positive_control_non_fire",
    "positive_rows_absent",
    "negative_controls_incomplete",
    "coverage_incomplete",
)


def _detected(output: adapter.MeterOutput) -> bool:
    """`negative_control_false_fire`/`positive_control_non_fire` が要求する
    「detections: Iterable[bool]」を、連続値 meter の `MeterOutput` から
    導出する運用上の定義（`[UNDERSPEC-CAL-D24]`）: `missing_reason`/
    `ineligible` のいずれでも説明されない、通常の finite 出力を返したことを
    以て「検出した」とみなす。negative control（sweep truth を運ばない
    fixture）上でこれが True なら偽検出、positive control（family anchor の
    truth core 行）上でこれが False なら不発火——同じ boolean 信号を 2 つの
    instance 母集団（negative/positive control 行）へ適用するだけで、
    F0/tilt/aperiodicity 等どの family の連続値 meter にも一様に使える
    （detector 型 meter 専用の閾値判定を新規発明しない）。"""
    return output.missing_reason is None and not output.ineligible


def candidate_fail_filter_report(
    candidate: Candidate,
    records: Sequence[measure_stage.MeasurementRecord],
    *,
    negative_control_row_ids: frozenset[str] = frozenset(),
    positive_control_row_ids: frozenset[str] = frozenset(),
    expected_coverage_instances: frozenset[tuple[str, int]] = frozenset(),
    within_fresh_tol: float = 0.0,
    noise_only_control_row_ids: frozenset[str] = frozenset(),
) -> dict[str, bool | float | int | None]:
    """finding #8: `candidates.adapter` の共通 5 fail filter（schema 違反 /
    無説明非有限 / within-process と fresh-process の不一致 / negative
    control 偽検出 / positive control 不発火）を `candidate` の全 record へ
    適用し、`{filter_name: 発火したか}` を返す（`FAIL_FILTER_NAMES` の 7 キー
    すべてを必ず持つ）。`eligible_after_fail_filters()` と組み合わせて使う。

    `negative_control_row_ids`/`positive_control_row_ids` が空（対象 family
    に control 行が無い、または当該 instance 集合に含まれない）場合、その
    2 filter は発火しない（判定材料が無い = fail-closed 側ではなく
    「判定対象外」として扱う — 通常の候補評価で control 行が母集団に
    含まれないのは正常系であり、これを ineligible の根拠にしない）。

    round 13 finding #1: 上記「判定材料が無い」の免除は
    `positive_control_row_ids` **引数自体が空**（呼び出し側が「この family
    に positive 母集団は無い」と判断した場合）にのみ適用する。引数が
    **非空**（呼び出し側は positive 母集団を宣言した）にもかかわらず
    `records` 側に該当行の record が 1 件も無い場合は、`pos_detections` は
    やはり空になるが、これは「判定対象外」ではなく「positive 証拠が
    観測から消えた」ことを意味する（例: designated anchor が SELECTION
    split に home split を持たない、または record 収集が不完全）。これを
    黙って non-failure 扱いすると fail-open になるため、`positive_rows_absent`
    filter として別途発火させ ineligible にする（`[UNDERSPEC-CAL-D25]`）。

    round 17 finding #1 採用: 負 control 側にも同型の欠陥があった —
    `negative_control_row_ids` **引数自体が非空**（呼び出し側は family の
    negative control 母集団を宣言した）にもかかわらず、宣言された行の一部
    または全部に `records` 側の該当 record が無い場合（例: home split が
    CALIBRATION/HOLDOUT で当該 instance が測定対象集合から漏れていた —
    round 17 finding #1 が `workunits.c3a_f0_selection_instances`/
    `c3b_family_selection_instances` 側で修正済み）、旧実装は
    `neg_detections` が単に空または部分集合になるだけで `negative_control_false_fire`
    が発火せず「偽検出なし」と誤判定していた（fail-open）。`positive_rows_absent`
    を鏡写しし、宣言された negative control 行のうち 1 件でも record を
    欠けば `negative_controls_incomplete` を発火させ ineligible にする
    （`[UNDERSPEC-CAL-D37]`）。

    round 28 ADOPT (2) 採用（`[UNDERSPEC-CAL-D64]`, design 引用: §10.1 「control
    出力の missing/invalid は分子に算入（分母から除外しない。eligibility は
    C0 入力側条件のみで判定）」+ §11 「score 計算可能だが PRIMARY 一部 output
    missing で gate 不通過 → DIAGNOSTIC_ONLY/OUTPUT_MISSING」）: `positive_rows_
    absent`/`negative_controls_incomplete` は control 母集団（row_id 単位）
    のみを検査し、F0 unusable による instance 単位の record 欠落（同じ row_id
    の一部 probe_index にのみ record が無い——`measure_stage.
    F0_DEPENDENT_ALGORITHM_FAMILIES` candidate が `f0_unusable_instances` 上で
    一切呼ばれない結果、`records` から丸ごと消える）を検出できなかった
    （row_id が 1 件でも record を持てば「seen」に数えられるため）。
    `build_candidate_criteria()` は `own_records` からのみ分母/誤差ベクトルを
    構築するため、この instance 単位の欠落は「N_pos/coverage の分母から
    除外」ではなく「見えないまま候補が縮小母集団で勝つ」経路になっていた。
    `expected_coverage_instances`（当初 `fixtures.controls.
    positive_detection_instances()` が返す TRUTH_CORE 限定の instance 集合
    だった。`positive_control_row_ids` の instance 版）が **非空**（呼び出し
    側がこの family の期待 instance 集合を宣言した）にもかかわらず、
    `own_records` がそのうち 1 件でも instance を欠いていれば
    `coverage_incomplete` を発火させ ineligible にする（`positive_rows_
    absent`/`negative_controls_incomplete` と同じ「宣言されたが観測から
    消えた」fail-closed 規約——設計正本はこの箇所に selection 段階固有の
    missing-rate 閾値を定義していないため、1 件でも欠ければ ineligible の
    既定規則を適用する）。

    round 30 self-review ADOPT (1) 採用（`[UNDERSPEC-CAL-D68]`, design 引用:
    §10.1 「control 出力の missing/invalid は分子に算入（分母から除外
    しない）」+ §11 「score 計算可能だが PRIMARY 一部 output missing で
    gate 不通過 → DIAGNOSTIC_ONLY/OUTPUT_MISSING」）: D64 の
    `coverage_incomplete` は 2 点で不十分だった。(a) 母集団が TRUTH_CORE
    限定で、hard CONFOUND 行（114 行）は対象外——CONFOUND 行の全 within/fresh
    call で一貫して `OUTPUT_MISSING` を返す candidate は coverage 判定を
    素通りし、縮小 instance 母集団の上で `primary_normalized_mae`/`signed_
    bias`/`primary_q95_ae` が計算され、`missing_failure_rate`（lexicographic
    順位の末尾側、`selection.py` の `_ranking_key`）による比較より **先に**
    正直に測定した candidate へ勝ち得た——D64 が閉じたはずの「縮小母集団で
    勝つ」fail-open を、D64 が対象にしなかった CONFOUND 母集団の上で再現する
    経路。(b) `seen_instances` が **record-presence-only**（値の有無を見ない）
    だったため、record 自体は存在するが `missing_reason` 付きで values が
    空の instance（F0-unusable による丸ごと欠落とは別に、candidate 自身が
    正しく `OUTPUT_MISSING` を返すケースを含む）を「観測済み」と誤カウント
    していた。本 ADOPT は両方を是正する: `expected_coverage_instances` の
    母集団を `fixtures.controls.non_boundary_selection_instances()`
    （`domain == Domain.PRIMARY` = TRUTH_CORE ∪ CONFOUND、BOUNDARY-domain 行
    は D2「domain 外は自動外挿せず NOT_EVALUABLE」により除外）へ拡張し、
    判定を値の有無（`measure_stage.primary_output_value()` が有限値を返す
    record が 1 件でもあるか）へ切り替える。BOUNDARY-domain 行の missing は
    design-sanctioned であり本 filter の対象外のまま（`missing_failure_rate`
    にのみ反映される）。negative control 行は `non_boundary_selection_
    instances()` 自体が domain=BOUNDARY として除外するため無関係
    （`[UNDERSPEC-CAL-D67]` の「negative control の一貫した非検出は不一致
    ではない」規約とは独立に、そもそも本 filter の母集団に入らない）。

    round 2 #344 ADOPT（`[UNDERSPEC-CAL-D71]`）: 上記 (b) の値の有無への
    切り替えは frozen contract（§9 ~L300-305、missing/failure rate は
    selection の lexicographic ranking criterion であり hard eligibility
    gate ではない）と矛盾する過剰締め付けだった——1 件でも explained
    `OUTPUT_MISSING` を返した候補が ineligible になり、family 内の全候補が
    各 1 件ずつ explained miss を持つだけで `SELECTION_FAILED_CLOSED` に
    落ちかねなかった。判定は record-presence（1 件でも own record があれば
    その instance は covered。値の有無は見ない）へ差し戻し、(a) の母集団
    拡張のみを維持する。詳細は下の実装コメントを参照。

    v1.1 §V1（F0_CONTROL の C3a に限る negative control fail filter 分割）:
    `noise_only_control_row_ids`（既定は空 frozenset——C3b や他 family は
    一切渡さず、従来どおり `negative_control_row_ids` 単一集合が any-fire
    判定と completeness 判定の両方を兼ねる）が非空のとき、呼び出し側は
    `negative_control_row_ids` を「決定論的縮退 class（SILENCE/TOO_SHORT/
    INVALID_SR 等、噪音 seed に依存しない全 class）」のみへ絞り込んで渡す
    ことを期待する。`negative_control_false_fire`（any-fire ゼロ許容）は
    引き続き `negative_control_row_ids` のみを母集団とし、NOISE_ONLY 行は
    この any-fire 判定から除外される。`negative_controls_incomplete`
    （record 有無の completeness 判定）は安全側に倒し、
    `negative_control_row_ids | noise_only_control_row_ids` の**和集合**を
    母集団とする（NOISE_ONLY 行の record 欠落を無言で見逃さない）。
    NOISE_ONLY 行の検出率は `noise_only_false_detection_rate`
    （`noise_only_instances_detected / noise_only_instances_total`。
    母集団が空なら `None`）として追加キーに記録し、v1.0 §8 が宣言済みの
    lexicographic 基準「voiced false detection rate」の実体として
    呼び出し側（`campaign/cli.py` の F0 選択経路）が `CandidateCriteria`
    へ配線する。この 3 キー（`noise_only_false_detection_rate`/
    `noise_only_instances_detected`/`noise_only_instances_total`）は
    `FAIL_FILTER_NAMES` に含まれないため `eligible_after_fail_filters()`
    の判定には一切影響しない（純粋な監査・配線用の追加情報）。"""
    own_records = [r for r in records if r.candidate_id == candidate.candidate_id]

    required_field = measure_stage.PRIMARY_OUTPUT_FIELD_BY_ALGORITHM_FAMILY.get(
        candidate.algorithm_family
    )
    required_fields = {required_field} if required_field else set()

    schema_violated = any(
        adapter.schema_violation(r.output, required_fields) for r in own_records
    )
    unexplained = any(adapter.unexplained_nonfinite(r.output) for r in own_records)

    mismatch = False
    if required_field is not None:
        by_instance: dict[tuple[str, int], dict[str, list[Mapping[str, float]]]] = {}
        for r in own_records:
            slot = by_instance.setdefault((r.row_id, r.probe_index), {"within": [], "fresh": []})
            if r.repeat_kind in slot:
                slot[r.repeat_kind].append(r.output.values)
        for slot in by_instance.values():
            if adapter.within_fresh_process_mismatch(
                slot["within"], slot["fresh"], field_name=required_field, tol=within_fresh_tol
            ):
                mismatch = True
                break

    neg_detections = [_detected(r.output) for r in own_records if r.row_id in negative_control_row_ids]
    pos_detections = [_detected(r.output) for r in own_records if r.row_id in positive_control_row_ids]
    neg_fire = adapter.negative_control_false_fire(neg_detections) if neg_detections else False
    pos_non_fire = adapter.positive_control_non_fire(pos_detections) if pos_detections else False
    # round 13 finding #1: a *declared* (non-empty) positive population that
    # yields zero matching records is a failure, not "not applicable" — see
    # the docstring section above (`[UNDERSPEC-CAL-D25]`).
    positive_rows_absent = bool(positive_control_row_ids) and not pos_detections
    # round 17 finding #1 (mirrors round 13 finding #1 on the negative side,
    # `[UNDERSPEC-CAL-D37]`): a *declared* (non-empty) negative-control
    # population must have a record for *every* declared row, not merely a
    # non-empty intersection — a partial population (some declared rows
    # missing) silently under-counts `neg_detections` and can hide a false
    # fire that only occurs on the missing row.
    # v1.1 §V1: completeness is checked over the *union* of the zero-tolerance
    # `negative_control_row_ids` and the (default-empty) `noise_only_control_
    # row_ids` — a caller that splits F0_CONTROL's C3a population by control
    # class must not lose the "every declared negative-control row has a
    # record" safety net for the NOISE_ONLY subset just because it is exempt
    # from the any-fire `negative_control_false_fire` filter below.
    declared_negative_row_ids = negative_control_row_ids | noise_only_control_row_ids
    seen_negative_row_ids = {r.row_id for r in own_records if r.row_id in declared_negative_row_ids}
    negative_controls_incomplete = bool(declared_negative_row_ids) and not (
        declared_negative_row_ids <= seen_negative_row_ids
    )
    # v1.1 §V1: NOISE_ONLY detections are excluded from the any-fire
    # `negative_control_false_fire` filter above and instead consumed as a
    # rate (v1.0 §8's declared-but-previously-dead "voiced false detection"
    # lexicographic ranking criterion). The rate is per *instance*
    # (`(row_id, probe_index)`, §10.1's instance definition), not per raw
    # record — each instance carries up to 6 `meter_call` records (3
    # within-process + 3 fresh-process, §6), and `within_fresh_process_
    # mismatch` (above) already separately audits within/fresh disagreement.
    # An instance counts as a false detection here if *any* of its own
    # records detected voicing (fail-closed: one spurious detection among
    # an instance's repeats still means the instance produced a voiced false
    # detection). `noise_only_control_row_ids` is empty for every caller
    # except F0_CONTROL's C3a, so this is a no-op elsewhere.
    noise_only_detections_by_instance: dict[tuple[str, int], list[bool]] = {}
    for r in own_records:
        if r.row_id in noise_only_control_row_ids:
            noise_only_detections_by_instance.setdefault(
                (r.row_id, r.probe_index), []
            ).append(_detected(r.output))
    noise_only_instances_total = len(noise_only_detections_by_instance)
    noise_only_instances_detected = sum(
        1 for dets in noise_only_detections_by_instance.values() if any(dets)
    )
    noise_only_false_detection_rate = (
        noise_only_instances_detected / noise_only_instances_total
        if noise_only_instances_total
        else None
    )
    # round 30 self-review ADOPT (1) (`[UNDERSPEC-CAL-D68]`, supersedes round
    # 28 ADOPT (2) `[UNDERSPEC-CAL-D64]`): instance-granular coverage check
    # against the expected non-BOUNDARY (TRUTH_CORE + CONFOUND) instance set
    # — see the docstring paragraph above.
    #
    # round 2 #344 ADOPT (`[UNDERSPEC-CAL-D71]`, amends D68/D70): the
    # value-aware check D68 introduced here (only a finite
    # `primary_output_value()` counted as "covered") over-tightened the
    # filter past the frozen contract. `records` passed into this function
    # contains only `meter_call` ledger records (see `measure_stage.
    # _completed_meter_call_records`/the `meter_call` append site) — a
    # skipped cell (F0_UNUSABLE/F0_SELECTION_FAILED etc., recorded instead as
    # a `measurement_missing` event, `[UNDERSPEC-CAL-D64]`/`[UNDERSPEC-CAL-
    # D65]`) never produces a `MeasurementRecord` at all, so it was already
    # absent from `own_records` and already correctly detected by a plain
    # record-presence check. What the value-aware check additionally
    # penalized was a candidate that *did* call the meter and got back a
    # legitimately recorded, explained `OUTPUT_MISSING` (a present
    # `meter_call` record with `missing_reason` set) on even one PRIMARY
    # instance — DESIGN_VG_METER_CAL_DEBT_v1.0.md §9 (~L300-305) lists
    # "missing/failure rate" as a lexicographic *ranking* criterion for both
    # the ABSOLUTE and DIRECTIONAL families (after the error/bias/sensitivity
    # criteria, before complexity rank), not a hard selection-eligibility
    # gate; a candidate whose only fault is one explained miss must remain
    # ranked, not made ineligible — and if every candidate in a family has
    # exactly one such expected miss, the value-aware form would falsely
    # drive `select_across_ceilings()` to `SELECTION_FAILED_CLOSED` for the
    # whole family. Ruling: `coverage_incomplete` reverts to record-presence
    # — any own record for the instance counts as covered, regardless of its
    # value — which detects ABSENT calls only (no `meter_call` record at
    # all, or a `measurement_missing` skip in its place); an explained-miss
    # `meter_call` record is a measured outcome that instead feeds
    # `build_candidate_criteria()`'s `missing_failure_rate` (and the
    # existing `positive_control_non_fire`/`negative_control_false_fire`
    # filters), exactly as before D68's value-aware change. D68's population
    # expansion (`expected_coverage_instances` = TRUTH_CORE + CONFOUND via
    # `non_boundary_selection_instances()`), the D67 within/fresh
    # missing-status consistency filter, and the BOUNDARY-domain exemption
    # from this filter are all unaffected and kept as-is.
    covered_instances = {(r.row_id, r.probe_index) for r in own_records}
    coverage_incomplete = bool(expected_coverage_instances) and not (
        expected_coverage_instances <= covered_instances
    )

    return {
        "schema_violation": schema_violated,
        "unexplained_nonfinite": unexplained,
        "within_fresh_process_mismatch": mismatch,
        "negative_control_false_fire": neg_fire,
        "positive_control_non_fire": pos_non_fire,
        "positive_rows_absent": positive_rows_absent,
        "negative_controls_incomplete": negative_controls_incomplete,
        "coverage_incomplete": coverage_incomplete,
        # v1.1 §V1: audit-only keys, not in `FAIL_FILTER_NAMES` — never
        # consulted by `eligible_after_fail_filters()`. Non-empty only when
        # the caller (F0_CONTROL's C3a) passes `noise_only_control_row_ids`.
        "noise_only_false_detection_rate": noise_only_false_detection_rate,
        "noise_only_instances_detected": noise_only_instances_detected,
        "noise_only_instances_total": noise_only_instances_total,
    }


def eligible_after_fail_filters(report: Mapping[str, object]) -> bool:
    """`candidate_fail_filter_report()` の戻り値から eligibility を導出する:
    7 filter のいずれか 1 つでも発火（True）していれば ineligible。"""
    return not any(report.get(name, False) for name in FAIL_FILTER_NAMES)


# ---------------------------------------------------------------------------
# max_claim_scope capping（finding #11, 第 11 巡採用）
# ---------------------------------------------------------------------------


class ClaimScopeError(RuntimeError):
    """finding #11: 凍結 manifest に `frozen_design.max_claim_scope`
    （Gate 1 が ABSOLUTE を許す construct id の集合）が無い場合の fail-closed
    error（"scope 未凍結" — 値が無いまま selection/holdout を進めない）。"""


#: `ClaimCeiling` の強さ順位（小さいほど強い主張）。`capped_ceiling()` の
#: `min(元 ceiling, DIRECTIONAL)` 判定に使う。
_CEILING_RANK: Mapping[ClaimCeiling, int] = {
    ClaimCeiling.ABSOLUTE: 0,
    ClaimCeiling.DIRECTIONAL: 1,
    ClaimCeiling.DIAGNOSTIC_ONLY: 2,
    ClaimCeiling.NONE: 3,
}


def max_claim_scope_from_manifest(manifest: Mapping[str, object]) -> frozenset[str]:
    """`manifest["frozen_design"]["max_claim_scope"]`（`list[str]`）を読む。
    節・キーが無い、または `list[str]` でなければ `ClaimScopeError`
    （scope 未凍結のまま selection/holdout を進めない — fail-closed）。"""
    frozen_design = manifest.get("frozen_design")
    if not isinstance(frozen_design, Mapping):
        raise ClaimScopeError("manifest is missing a frozen_design section")
    scope = frozen_design.get("max_claim_scope")
    if not isinstance(scope, list) or not all(isinstance(s, str) for s in scope):
        raise ClaimScopeError(
            "manifest.frozen_design.max_claim_scope is missing or not a list[str] "
            "(claim scope not frozen — refusing to select/holdout without it)"
        )
    return frozenset(scope)


def capped_ceiling(
    construct: str, ceiling: ClaimCeiling, max_claim_scope: frozenset[str]
) -> tuple[ClaimCeiling, bool]:
    """finding #11: `construct` が `max_claim_scope` に含まれなければ
    `ceiling` を `min(ceiling, DIRECTIONAL)`（`DIAGNOSTIC_ONLY`/`NONE` は
    既に DIRECTIONAL より弱いためそのまま）へ capping する。
    `(capped_ceiling, capped_かどうか)` を返す。`construct` が scope 内なら
    常に `(ceiling, False)`。"""
    if construct in max_claim_scope:
        return ceiling, False
    if _CEILING_RANK[ceiling] < _CEILING_RANK[ClaimCeiling.DIRECTIONAL]:
        return ClaimCeiling.DIRECTIONAL, True
    return ceiling, False


def claim_scope_report(
    candidate: Candidate, max_claim_scope: frozenset[str]
) -> tuple[ClaimCeiling, dict[str, object]]:
    """`candidate.construct`/`candidate.claim_ceiling` に `capped_ceiling()`
    を適用し、`(capped, report)` を返す。`report` は SELECTION_FROZEN /
    HOLDOUT_EXECUTED_VALID payload の `claim_scope_by_candidate` に候補ごと
    そのまま積める形（`construct`/`original_ceiling`/`capped_ceiling`/
    `capped`）。"""
    capped, was_capped = capped_ceiling(candidate.construct, candidate.claim_ceiling, max_claim_scope)
    return capped, {
        "construct": candidate.construct,
        "original_ceiling": candidate.claim_ceiling.value,
        "capped_ceiling": capped.value,
        "capped": was_capped,
    }


def build_candidate_criteria(
    candidate: Candidate,
    records: Sequence[measure_stage.MeasurementRecord],
    truth_by_instance: Mapping[tuple[str, int], float],
    *,
    zero_guard: float = 1e-9,
) -> CandidateCriteria:
    """`[UNDERSPEC-CAL-D16]` 実測 record 列（within+fresh 6 call/instance）
    から `CandidateCriteria` を構築する集計規則:

    - instance ごとの測定値 `m[i]` は §10.1 の凍結二段 median
      `m[i] = median_p( median_r( x_hat[i,p,r] ) )`（`observables.two_stage_median`。
      `MeasurementRecord.process_id` で process group 化 — within-process 3
      repeat は単一 group、fresh-process 3 repeat は各 1 repeat の独立
      group。process 間で repeat 数が不均等でも repeat 数の多い process が
      支配しないための規則であり、`holdout_stage.build_instance_margins`
      と同一の入力形・同一関数を使う。round 19 finding #1（採用）:
      旧実装は単純平均（`np.mean`）を使っており、外れ値 repeat が中央値
      では吸収されるはずのケースでも平均値へ直接混入していた
      （`[UNDERSPEC-CAL-D43]`）。
    - `e[i] = m[i] - truth[i]`（raw signed error）、`AE[i] = |e[i]|`、
      `RE[i] = AE[i]/max(|truth[i]|, zero_guard)`（`observables.error_terms`、
      §10.1: `e[i] = m[i] - x[i]`、`AE[i] = |e[i]|`、
      `RE[i] = AE[i]/max(|x[i]|, d[i])`）。
    - ABSOLUTE 系列: **normalized MAE は `RE[i]` の平均**（primary-domain の
      相対誤差指標。§9 の "primary-domain normalized MAE" 呼称に対応）、
      **BIAS は raw `e[i]` の平均**（§10.1: `BIAS = mean_i(e[i])` — `e[i]`
      は正規化前の raw signed error と定義されている）、**q95(AE) は raw
      `AE[i]` の 95th percentile**（§10.1 の `AE[i] = |e[i]|` をそのまま
      q95 する。normalized ではない）。round 19 finding #2（採用）:
      旧実装は `RE[i]` を `errors` として BIAS/q95(AE) にも使い回しており、
      instance 間で truth の桁が大きく異なる場合に正規化誤差ベースの
      ranking と raw 誤差ベースの ranking が逆転し得た（`[UNDERSPEC-CAL-D44]`。
      設計正本 §10.1 は `BIAS`/`q95` の入力が `e[i]`/`AE[i]`（raw）である
      ことを明示するが、§9 の "normalized MAE" と地続きに読めてしまう
      曖昧さがあったため本 UNDERSPEC で裁定を記録する）。
      DIRECTIONAL 系列は `scipy.stats.kendalltau(truth, measured)` と
      truth 順ソート上の隣接反転率を使う（`measured` は上記 `m[i]`
      そのもの — ABSOLUTE/DIRECTIONAL で同じ二段 median 観測値を使う）。
    - `nuisance_sensitivity_max` は本 D2 infra では `0.0` 固定とする
      （confound axis ペアリングの実測配線 — §5.1 targeted interaction 行と
      anchor 行の対応付け — は `holdout_stage.RawInstanceObservation`/
      `nuisance_ds` という building block を既に提供しており、config 化は
      後続 PR の対象）。
    - `missing_failure_rate` は missing/ineligible/欠損 truth の instance
      割合。1 件も有効な instance が無ければ `eligible=False` を返す。
    """
    own_records = [r for r in records if r.candidate_id == candidate.candidate_id]
    per_instance_process_values: dict[tuple[str, int], dict[str, list[float]]] = {}
    missing_count = 0
    total_count = 0
    for r in own_records:
        total_count += 1
        value = measure_stage.primary_output_value(candidate, r.output)
        if value is None or not math.isfinite(value):
            missing_count += 1
            continue
        slot = per_instance_process_values.setdefault((r.row_id, r.probe_index), {})
        slot.setdefault(r.process_id, []).append(value)

    missing_failure_rate = (missing_count / total_count) if total_count else 1.0

    truths: list[float] = []
    measured: list[float] = []
    raw_errors: list[float] = []
    relative_errors: list[float] = []
    for key in sorted(per_instance_process_values):
        truth = truth_by_instance.get(key)
        if truth is None:
            continue
        per_process = per_instance_process_values[key]
        if not per_process:
            continue
        # round 19 finding #1 (`[UNDERSPEC-CAL-D43]`): frozen §10.1 two-stage
        # median, grouped by MeasurementRecord.process_id (matches
        # holdout_stage.build_instance_margins's input shape exactly).
        m = two_stage_median(per_process)
        et = error_terms(m, truth, zero_guard)
        truths.append(truth)
        measured.append(m)
        raw_errors.append(et.e)
        relative_errors.append(et.re)

    if not raw_errors:
        return CandidateCriteria(
            candidate_id=candidate.candidate_id,
            eligible=False,
            complexity_rank=candidate.complexity_rank,
            missing_failure_rate=missing_failure_rate,
            ceiling=candidate.claim_ceiling,
        )

    # round 19 finding #2 (`[UNDERSPEC-CAL-D44]`): normalized MAE stays
    # relative-error based (§9 "normalized MAE"); BIAS/q95(AE) use raw
    # signed/absolute error per §10.1's `e[i]`/`AE[i]` definitions.
    normalized_mae = float(np.mean(np.abs(relative_errors)))
    signed_bias = bias(raw_errors)
    primary_q95_ae = q95([abs(e) for e in raw_errors])

    kendall_tau = 0.0
    if len(truths) >= 2 and len(set(truths)) >= 2 and len(set(measured)) >= 2:
        tau, _p_value = kendalltau(truths, measured)
        if tau is not None and math.isfinite(float(tau)):
            kendall_tau = float(tau)

    order = sorted(range(len(truths)), key=lambda i: truths[i])
    reversal_pairs = max(len(order) - 1, 1)
    reversals = 0
    for a, b in zip(order, order[1:]):
        delta_truth = truths[b] - truths[a]
        delta_measured = measured[b] - measured[a]
        if delta_truth != 0 and (delta_measured > 0) != (delta_truth > 0):
            reversals += 1
    adjacent_reversal_rate = reversals / reversal_pairs

    return CandidateCriteria(
        candidate_id=candidate.candidate_id,
        eligible=True,
        complexity_rank=candidate.complexity_rank,
        nuisance_sensitivity_max=0.0,
        missing_failure_rate=missing_failure_rate,
        ceiling=candidate.claim_ceiling,
        primary_normalized_mae=normalized_mae,
        signed_bias=signed_bias,
        primary_q95_ae=primary_q95_ae,
        kendall_tau=kendall_tau,
        adjacent_reversal_rate=adjacent_reversal_rate,
    )


__all__ = [
    "candidate_space_sha",
    "selection_rule_sha",
    "F0SelectionResult",
    "run_c3a_f0_selection",
    "SelectionFreezeResult",
    "run_c3b_selection",
    "truth_value_for_row",
    "FAIL_FILTER_NAMES",
    "candidate_fail_filter_report",
    "eligible_after_fail_filters",
    "ClaimScopeError",
    "max_claim_scope_from_manifest",
    "capped_ceiling",
    "claim_scope_report",
    "build_candidate_criteria",
]
