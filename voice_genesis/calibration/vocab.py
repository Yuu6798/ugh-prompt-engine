"""RUN10-CAL 閉語彙 (closed vocabularies) の一元定義。

設計正本 (`DESIGN_VG_METER_CAL_DEBT_v1.0.md`) が凍結を要求する全ての列挙値を
ここに集約する。事後の場当たり追加は設計正本の変更（新 revision / correction
record）を経由すべきであり、このモジュール単体の改変で語彙を増やしてはならない。

`DEBT_DISCHARGED` は D1 の裁定により **宣言フィールド化を禁止** されているため、
`debt_discharged()` は純関数 (pure derivation) としてのみ提供する。同様に
`CAMPAIGN_CLOSED` も手続的閉鎖の派生値として関数で提供する。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum


class TerminalStatus(str, Enum):
    """meter の終端 status（設計正本 §11）。同一 campaign 内で再遷移禁止。"""

    INVALID = "INVALID"
    NOT_EVALUABLE = "NOT_EVALUABLE"
    CALIBRATED_ABSOLUTE = "CALIBRATED_ABSOLUTE"
    CALIBRATED_DIRECTIONAL = "CALIBRATED_DIRECTIONAL"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"


class MissingReason(str, Enum):
    """missing の理由コード閉語彙（設計正本 §11。R5 で閉語彙化が確定）。"""

    INPUT_MISSING = "INPUT_MISSING"
    OUTPUT_NOT_EVALUABLE = "OUTPUT_NOT_EVALUABLE"
    OUTPUT_MISSING = "OUTPUT_MISSING"
    PROCEDURE = "PROCEDURE"


class BlockedCode(str, Enum):
    """fail-closed code の閉語彙（設計正本 §3.3）。C0 で列挙済み、事後追加禁止。"""

    BLOCKED_DOMAIN_MANIFEST_INCOMPLETE = "BLOCKED_DOMAIN_MANIFEST_INCOMPLETE"
    BLOCKED_C0_MANIFEST_INCOMPLETE = "BLOCKED_C0_MANIFEST_INCOMPLETE"
    BLOCKED_C0_UNSEEDED_RNG = "BLOCKED_C0_UNSEEDED_RNG"
    #: UNDERSPEC-CAL-D76 ruling (2)（D75 の `BLOCKED_C0_SWEEP_CAPACITY_
    #: INSUFFICIENT` を SUPERSEDE。sweep 定義そのものが誤りだったため名称・
    #: 意味論を改める）: 凍結 matrix (`fixtures.matrix.build_matrix()`) が
    #: 宣言する declared sweep（`fixtures.matrix.declared_sweeps_by_family()`、
    #: def A: truth-core block の nuisance-constant series）のうち 1 件でも
    #: 相異なる truth level 数が 3 未満（`gates.MIN_RESOLVABLE_PAIRS_PER_SWEEP`
    #: 未満の pair しか作れない）なら発行する（`c0_validate.
    #: _check_declared_sweep_truth_levels`）。
    BLOCKED_C0_SWEEP_DECLARATION_INVALID = "BLOCKED_C0_SWEEP_DECLARATION_INVALID"
    BLOCKED_C1_GENERATOR_NONDETERMINISTIC = "BLOCKED_C1_GENERATOR_NONDETERMINISTIC"
    BLOCKED_LEAKAGE = "BLOCKED_LEAKAGE"
    BLOCKED_CANONICAL_MUTATION_REQUIRED = "BLOCKED_CANONICAL_MUTATION_REQUIRED"


class ProcedureGate(str, Enum):
    """手続 Gate（設計正本 §1 D3/R1）。meter status とは別軸。単調（前段非PASSなら
    後段PASS不可）。"""

    PREPARATION_VALID = "PREPARATION_VALID"
    FIXTURE_VALID = "FIXTURE_VALID"
    BASELINE_AUDITED = "BASELINE_AUDITED"
    SELECTION_FROZEN = "SELECTION_FROZEN"
    HOLDOUT_EXECUTED_VALID = "HOLDOUT_EXECUTED_VALID"


PROCEDURE_GATE_ORDER: tuple[ProcedureGate, ...] = (
    ProcedureGate.PREPARATION_VALID,
    ProcedureGate.FIXTURE_VALID,
    ProcedureGate.BASELINE_AUDITED,
    ProcedureGate.SELECTION_FROZEN,
    ProcedureGate.HOLDOUT_EXECUTED_VALID,
)


def procedure_gate_rank(gate: ProcedureGate) -> int:
    """PROCEDURE_GATE_ORDER 上の 0-based 順位。"""
    return PROCEDURE_GATE_ORDER.index(gate)


def procedure_gates_monotonic(passed: Iterable[ProcedureGate]) -> bool:
    """`passed` に含まれる各 gate について、それより手前の全 gate も passed に
    含まれているかを検査する（後段 PASS は前段全 PASS を含意する、の逆はチェックしない
    ＝ passed に列挙されていない後段 gate があっても違反にはしない）。

    ステータス自体の永続化・event 記録はこのモジュールの責務ではない
    （呼び出し側の ledger / provenance が担う）。
    """
    passed_set = set(passed)
    for gate in passed_set:
        rank = procedure_gate_rank(gate)
        for earlier in PROCEDURE_GATE_ORDER[:rank]:
            if earlier not in passed_set:
                return False
    return True


class CampaignStatus(str, Enum):
    """campaign 全体の付加的 status（設計正本 §1 R1, §9）。"""

    CAMPAIGN_CLOSED = "CAMPAIGN_CLOSED"
    SELECTION_FAILED_CLOSED = "SELECTION_FAILED_CLOSED"


class IndependenceTier(str, Enum):
    """4-tier independence（設計正本 §4.1）。"""

    INDEPENDENT_ANALYTIC = "INDEPENDENT_ANALYTIC"
    CROSS_IMPLEMENTATION = "CROSS_IMPLEMENTATION"
    SHARED_MODEL_DIAGNOSTIC = "SHARED_MODEL_DIAGNOSTIC"
    INVALID_CIRCULAR = "INVALID_CIRCULAR"


class ClaimCeiling(str, Enum):
    """独立性 tier / evidence class から導出される主張上限。"""

    ABSOLUTE = "ABSOLUTE"
    DIRECTIONAL = "DIRECTIONAL"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    NONE = "NONE"  # invalid: 校正証拠として無効


# [UNDERSPEC-CAL-01] 設計正本 §4.1 の表は CROSS_IMPLEMENTATION を「ABSOLUTE または
# DIRECTIONAL 候補」と記す（候補ごとに実際の到達 ceiling は gate 判定で決まる）。
# ここでの写像は「そのtierが許す最大 ceiling（gate が全通過した場合に到達しうる上限）」
# であり、個別候補の実際の ceiling は gates.py の判定結果で下方に絞られる。
INDEPENDENCE_TIER_CLAIM_CEILING: Mapping[IndependenceTier, ClaimCeiling] = {
    IndependenceTier.INDEPENDENT_ANALYTIC: ClaimCeiling.ABSOLUTE,
    IndependenceTier.CROSS_IMPLEMENTATION: ClaimCeiling.ABSOLUTE,
    IndependenceTier.SHARED_MODEL_DIAGNOSTIC: ClaimCeiling.DIAGNOSTIC_ONLY,
    IndependenceTier.INVALID_CIRCULAR: ClaimCeiling.NONE,
}


class EvidenceClass(str, Enum):
    """E_use evidence table の evidence_class 閉語彙（設計正本 §10.2）。"""

    NORMATIVE_SPEC = "NORMATIVE_SPEC"
    FIRST_PRINCIPLES_BOUND = "FIRST_PRINCIPLES_BOUND"
    VALIDATED_REFERENCE = "VALIDATED_REFERENCE"
    USER_ACCEPTED_USE_BOUND = "USER_ACCEPTED_USE_BOUND"
    UNJUSTIFIED = "UNJUSTIFIED"


class Domain(str, Enum):
    """fixture 行の派生 domain tag（設計正本 §3.3, §5）。"""

    PRIMARY = "PRIMARY"
    BOUNDARY = "BOUNDARY"


class Split(str, Enum):
    """row→split 表の値（設計正本 §7）。"""

    CALIBRATION = "CALIBRATION"
    SELECTION = "SELECTION"
    HOLDOUT = "HOLDOUT"


class MeterId(str, Enum):
    """校正対象 meter の識別子（設計正本 §1, §8）。"""

    M2_SPECTRAL_TILT = "M2_SPECTRAL_TILT"
    M2_APERIODICITY = "M2_APERIODICITY"
    M3_FORMANTS = "M3_FORMANTS"
    M4_RESONANCE = "M4_RESONANCE"
    M5_TRANSITION = "M5_TRANSITION"
    M6_IDENTITY = "M6_IDENTITY"
    F0_CONTROL = "F0_CONTROL"


CLAIM_CRITICAL_SET: frozenset[MeterId] = frozenset(
    {MeterId.M3_FORMANTS, MeterId.M2_SPECTRAL_TILT, MeterId.M2_APERIODICITY}
)
"""D1: 保守既定の claim-critical subset。C0 後の縮小・追加は禁止。"""


_DISCHARGED_TERMINALS = frozenset(
    {TerminalStatus.CALIBRATED_ABSOLUTE, TerminalStatus.CALIBRATED_DIRECTIONAL}
)


def debt_discharged(terminal: Mapping[MeterId, TerminalStatus]) -> bool:
    """D1 純導出値: CLAIM_CRITICAL_SET の全 meter が CALIBRATED_ABSOLUTE または
    CALIBRATED_DIRECTIONAL に到達しているか。

    宣言フィールドとして永続化してはならない（設計正本 §1 D1 が明示的に禁止）。
    呼び出しの都度、最新の terminal status マッピングから再計算すること。
    """
    return all(
        terminal.get(meter) in _DISCHARGED_TERMINALS for meter in CLAIM_CRITICAL_SET
    )


def campaign_closed(
    terminal: Mapping[MeterId, TerminalStatus],
    expected: Iterable[MeterId] | None = None,
) -> bool:
    """Derive procedural campaign closure from the frozen ``MeterId`` set.

    The required meter population is not a runtime choice: every frozen ``MeterId``
    must have a terminal status. ``expected`` is retained only as a compatibility
    assertion for existing callers; when supplied it must be an exact, duplicate-free
    copy of the frozen set and can never shrink the closure requirement.
    """
    frozen = tuple(MeterId)
    if expected is not None:
        supplied = tuple(expected)
        supplied_set = set(supplied)
        if len(supplied) != len(supplied_set) or supplied_set != set(frozen):
            return False
    return all(meter in terminal for meter in frozen)
