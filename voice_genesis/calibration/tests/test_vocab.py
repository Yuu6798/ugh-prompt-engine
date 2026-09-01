from __future__ import annotations

from voice_genesis.calibration import vocab
from voice_genesis.calibration.vocab import (
    CLAIM_CRITICAL_SET,
    BlockedCode,
    CampaignStatus,
    ClaimCeiling,
    Domain,
    EvidenceClass,
    IndependenceTier,
    MeterId,
    MissingReason,
    ProcedureGate,
    Split,
    TerminalStatus,
    campaign_closed,
    debt_discharged,
    procedure_gates_monotonic,
)


def test_terminal_status_membership_count() -> None:
    assert len(TerminalStatus) == 5
    assert {s.value for s in TerminalStatus} == {
        "INVALID",
        "NOT_EVALUABLE",
        "CALIBRATED_ABSOLUTE",
        "CALIBRATED_DIRECTIONAL",
        "DIAGNOSTIC_ONLY",
    }


def test_missing_reason_closed_vocab() -> None:
    assert len(MissingReason) == 4
    assert {m.value for m in MissingReason} == {
        "INPUT_MISSING",
        "OUTPUT_NOT_EVALUABLE",
        "OUTPUT_MISSING",
        "PROCEDURE",
    }


def test_blocked_code_closed_vocab() -> None:
    assert len(BlockedCode) == 6
    assert {b.value for b in BlockedCode} == {
        "BLOCKED_DOMAIN_MANIFEST_INCOMPLETE",
        "BLOCKED_C0_MANIFEST_INCOMPLETE",
        "BLOCKED_C0_UNSEEDED_RNG",
        "BLOCKED_C1_GENERATOR_NONDETERMINISTIC",
        "BLOCKED_LEAKAGE",
        "BLOCKED_CANONICAL_MUTATION_REQUIRED",
    }


def test_procedure_gate_closed_vocab() -> None:
    assert len(ProcedureGate) == 5
    assert list(vocab.PROCEDURE_GATE_ORDER) == [
        ProcedureGate.PREPARATION_VALID,
        ProcedureGate.FIXTURE_VALID,
        ProcedureGate.BASELINE_AUDITED,
        ProcedureGate.SELECTION_FROZEN,
        ProcedureGate.HOLDOUT_EXECUTED_VALID,
    ]


def test_campaign_status_extras() -> None:
    assert {c.value for c in CampaignStatus} == {"CAMPAIGN_CLOSED", "SELECTION_FAILED_CLOSED"}


def test_independence_tier_and_claim_ceiling() -> None:
    assert len(IndependenceTier) == 4
    assert len(ClaimCeiling) == 4
    assert (
        vocab.INDEPENDENCE_TIER_CLAIM_CEILING[IndependenceTier.INDEPENDENT_ANALYTIC]
        == ClaimCeiling.ABSOLUTE
    )
    assert (
        vocab.INDEPENDENCE_TIER_CLAIM_CEILING[IndependenceTier.SHARED_MODEL_DIAGNOSTIC]
        == ClaimCeiling.DIAGNOSTIC_ONLY
    )
    assert (
        vocab.INDEPENDENCE_TIER_CLAIM_CEILING[IndependenceTier.INVALID_CIRCULAR]
        == ClaimCeiling.NONE
    )


def test_evidence_class_closed_vocab() -> None:
    assert len(EvidenceClass) == 5


def test_domain_and_split_enums() -> None:
    assert {d.value for d in Domain} == {"PRIMARY", "BOUNDARY"}
    assert {s.value for s in Split} == {"CALIBRATION", "SELECTION", "HOLDOUT"}


def test_meter_id_and_claim_critical_set() -> None:
    assert len(MeterId) == 7
    assert CLAIM_CRITICAL_SET == frozenset(
        {MeterId.M3_FORMANTS, MeterId.M2_SPECTRAL_TILT, MeterId.M2_APERIODICITY}
    )
    assert MeterId.M6_IDENTITY not in CLAIM_CRITICAL_SET
    assert MeterId.M4_RESONANCE not in CLAIM_CRITICAL_SET


def test_debt_discharged_truth_table_all_calibrated_absolute() -> None:
    terminal = {m: TerminalStatus.CALIBRATED_ABSOLUTE for m in CLAIM_CRITICAL_SET}
    assert debt_discharged(terminal) is True


def test_debt_discharged_truth_table_mixed_absolute_and_directional() -> None:
    terminal = {
        MeterId.M3_FORMANTS: TerminalStatus.CALIBRATED_ABSOLUTE,
        MeterId.M2_SPECTRAL_TILT: TerminalStatus.CALIBRATED_DIRECTIONAL,
        MeterId.M2_APERIODICITY: TerminalStatus.CALIBRATED_ABSOLUTE,
    }
    assert debt_discharged(terminal) is True


def test_debt_discharged_truth_table_any_diagnostic_only_is_false() -> None:
    terminal = {
        MeterId.M3_FORMANTS: TerminalStatus.CALIBRATED_ABSOLUTE,
        MeterId.M2_SPECTRAL_TILT: TerminalStatus.DIAGNOSTIC_ONLY,
        MeterId.M2_APERIODICITY: TerminalStatus.CALIBRATED_ABSOLUTE,
    }
    assert debt_discharged(terminal) is False


def test_debt_discharged_missing_member_is_false() -> None:
    terminal = {
        MeterId.M3_FORMANTS: TerminalStatus.CALIBRATED_ABSOLUTE,
        MeterId.M2_SPECTRAL_TILT: TerminalStatus.CALIBRATED_ABSOLUTE,
        # M2_APERIODICITY missing entirely
    }
    assert debt_discharged(terminal) is False


def test_debt_discharged_is_not_a_stored_field() -> None:
    # D1 は debt_discharged を宣言フィールド化することを禁止する。
    # vocab モジュール名前空間に永続フィールドが存在しないことを間接的に確認する。
    assert callable(debt_discharged)
    assert not hasattr(vocab, "DEBT_DISCHARGED")


def test_campaign_closed_all_terminal() -> None:
    expected = list(MeterId)
    terminal = {m: TerminalStatus.DIAGNOSTIC_ONLY for m in expected}
    assert campaign_closed(terminal, expected) is True


def test_campaign_closed_missing_one_meter() -> None:
    expected = list(MeterId)
    terminal = {m: TerminalStatus.DIAGNOSTIC_ONLY for m in expected[:-1]}
    assert campaign_closed(terminal, expected) is False


def test_gate_monotonicity_full_sequence_ok() -> None:
    assert procedure_gates_monotonic(list(ProcedureGate)) is True


def test_gate_monotonicity_prefix_subset_ok() -> None:
    assert procedure_gates_monotonic(
        [ProcedureGate.PREPARATION_VALID, ProcedureGate.FIXTURE_VALID]
    ) is True


def test_gate_monotonicity_gap_is_violation() -> None:
    # SELECTION_FROZEN が PASS 済みなのに BASELINE_AUDITED が欠落 -> 違反
    assert (
        procedure_gates_monotonic(
            [ProcedureGate.PREPARATION_VALID, ProcedureGate.SELECTION_FROZEN]
        )
        is False
    )


def test_gate_monotonicity_empty_ok() -> None:
    assert procedure_gates_monotonic([]) is True


def test_gate_monotonicity_single_late_gate_without_earlier_is_violation() -> None:
    assert procedure_gates_monotonic([ProcedureGate.HOLDOUT_EXECUTED_VALID]) is False
