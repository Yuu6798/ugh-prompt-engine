from __future__ import annotations


from voice_genesis.calibration.status import (
    gate_monotonicity_ok,
    missing_to_status,
    terminal_status,
)
from voice_genesis.calibration.vocab import ClaimCeiling, MissingReason, ProcedureGate, TerminalStatus


def test_cascade_branch_1_procedure_breach_is_invalid() -> None:
    status = terminal_status(
        procedure_breach=True,
        evaluable=True,
        ceiling=ClaimCeiling.ABSOLUTE,
        absolute_gates_passed=True,
        directional_gates_passed=True,
    )
    assert status == TerminalStatus.INVALID


def test_cascade_branch_2_not_evaluable() -> None:
    status = terminal_status(
        procedure_breach=False,
        evaluable=False,
        ceiling=ClaimCeiling.ABSOLUTE,
        absolute_gates_passed=True,
        directional_gates_passed=True,
    )
    assert status == TerminalStatus.NOT_EVALUABLE


def test_cascade_branch_3_calibrated_absolute() -> None:
    status = terminal_status(
        procedure_breach=False,
        evaluable=True,
        ceiling=ClaimCeiling.ABSOLUTE,
        absolute_gates_passed=True,
        directional_gates_passed=False,
    )
    assert status == TerminalStatus.CALIBRATED_ABSOLUTE


def test_cascade_branch_4_calibrated_directional() -> None:
    status = terminal_status(
        procedure_breach=False,
        evaluable=True,
        ceiling=ClaimCeiling.ABSOLUTE,
        absolute_gates_passed=False,
        directional_gates_passed=True,
    )
    assert status == TerminalStatus.CALIBRATED_DIRECTIONAL


def test_cascade_branch_4_directional_ceiling_only() -> None:
    status = terminal_status(
        procedure_breach=False,
        evaluable=True,
        ceiling=ClaimCeiling.DIRECTIONAL,
        absolute_gates_passed=True,  # ceiling が ABSOLUTE を許さないので無関係
        directional_gates_passed=True,
    )
    assert status == TerminalStatus.CALIBRATED_DIRECTIONAL


def test_cascade_branch_5_diagnostic_only_residual() -> None:
    status = terminal_status(
        procedure_breach=False,
        evaluable=True,
        ceiling=ClaimCeiling.DIAGNOSTIC_ONLY,
        absolute_gates_passed=True,
        directional_gates_passed=True,
    )
    assert status == TerminalStatus.DIAGNOSTIC_ONLY


def test_cascade_first_match_priority_procedure_breach_over_evaluable() -> None:
    # procedure_breach=True かつ evaluable=False でも INVALID が優先 (first-match)
    status = terminal_status(
        procedure_breach=True,
        evaluable=False,
        ceiling=ClaimCeiling.NONE,
        absolute_gates_passed=False,
        directional_gates_passed=False,
    )
    assert status == TerminalStatus.INVALID


def test_cascade_exhaustive_and_exclusive_over_boolean_grid() -> None:
    # 全 boolean 組み合わせ (procedure_breach x evaluable x ceiling x abs x dir)
    # が必ずちょうど 1 つの終端 status に写像される (網羅性/排他性の構造的検証)。
    ceilings = list(ClaimCeiling)
    for pb in (True, False):
        for ev in (True, False):
            for ceiling in ceilings:
                for ap in (True, False):
                    for dp in (True, False):
                        status = terminal_status(
                            procedure_breach=pb,
                            evaluable=ev,
                            ceiling=ceiling,
                            absolute_gates_passed=ap,
                            directional_gates_passed=dp,
                        )
                        assert isinstance(status, TerminalStatus)


def test_missing_mapping_procedure() -> None:
    assert missing_to_status(MissingReason.PROCEDURE) == (
        TerminalStatus.INVALID,
        MissingReason.PROCEDURE,
    )


def test_missing_mapping_output_not_evaluable() -> None:
    assert missing_to_status(MissingReason.OUTPUT_NOT_EVALUABLE) == (
        TerminalStatus.NOT_EVALUABLE,
        MissingReason.OUTPUT_NOT_EVALUABLE,
    )


def test_missing_mapping_output_missing() -> None:
    assert missing_to_status(MissingReason.OUTPUT_MISSING) == (
        TerminalStatus.DIAGNOSTIC_ONLY,
        MissingReason.OUTPUT_MISSING,
    )


def test_missing_mapping_input_missing() -> None:
    assert missing_to_status(MissingReason.INPUT_MISSING) == (
        TerminalStatus.NOT_EVALUABLE,
        MissingReason.INPUT_MISSING,
    )


def test_missing_mapping_covers_all_reasons() -> None:
    for reason in MissingReason:
        status, echoed = missing_to_status(reason)
        assert echoed == reason
        assert isinstance(status, TerminalStatus)


def test_gate_monotonicity_ok_delegates_to_vocab() -> None:
    assert gate_monotonicity_ok(list(ProcedureGate)) is True
    assert gate_monotonicity_ok([ProcedureGate.HOLDOUT_EXECUTED_VALID]) is False
