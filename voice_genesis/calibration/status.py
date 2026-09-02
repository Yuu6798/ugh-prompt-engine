"""終端 status の first-match cascade（設計正本 §11）。"""

from __future__ import annotations

from collections.abc import Sequence

from voice_genesis.calibration.vocab import (
    ClaimCeiling,
    MissingReason,
    ProcedureGate,
    TerminalStatus,
    procedure_gates_monotonic,
)


def terminal_status(
    *,
    procedure_breach: bool,
    evaluable: bool,
    ceiling: ClaimCeiling,
    absolute_gates_passed: bool,
    directional_gates_passed: bool,
) -> TerminalStatus:
    """§11 の 5 段 first-match cascade。

    ```
    1. procedure breach 検出                  -> INVALID
    2. 評価可能性条件の不成立                  -> NOT_EVALUABLE
    3. ceiling が ABSOLUTE を許し gate 全通過   -> CALIBRATED_ABSOLUTE
    4. ceiling が DIRECTIONAL を許し gate 全通過 -> CALIBRATED_DIRECTIONAL
    5. else                                   -> DIAGNOSTIC_ONLY
    ```

    5 が残余のため網羅性は構造的に保証、排他性は first-match による。

    同一 campaign 内での再遷移禁止（one-shot）は ledger レベルの関心事であり、
    この純関数自体は状態を持たない（呼び出し側が「既に終端 status が確定済みの
    meter へ再度呼ばない」ことを保証する）。
    """
    if procedure_breach:
        return TerminalStatus.INVALID
    if not evaluable:
        return TerminalStatus.NOT_EVALUABLE
    if ceiling == ClaimCeiling.ABSOLUTE and absolute_gates_passed:
        return TerminalStatus.CALIBRATED_ABSOLUTE
    if (
        ceiling in (ClaimCeiling.ABSOLUTE, ClaimCeiling.DIRECTIONAL)
        and directional_gates_passed
    ):
        return TerminalStatus.CALIBRATED_DIRECTIONAL
    return TerminalStatus.DIAGNOSTIC_ONLY


_MISSING_MAPPING: dict[MissingReason, tuple[TerminalStatus, MissingReason]] = {
    MissingReason.PROCEDURE: (TerminalStatus.INVALID, MissingReason.PROCEDURE),
    MissingReason.OUTPUT_NOT_EVALUABLE: (
        TerminalStatus.NOT_EVALUABLE,
        MissingReason.OUTPUT_NOT_EVALUABLE,
    ),
    MissingReason.OUTPUT_MISSING: (
        TerminalStatus.DIAGNOSTIC_ONLY,
        MissingReason.OUTPUT_MISSING,
    ),
    MissingReason.INPUT_MISSING: (
        TerminalStatus.NOT_EVALUABLE,
        MissingReason.INPUT_MISSING,
    ),
}


def missing_to_status(kind: MissingReason) -> tuple[TerminalStatus, MissingReason]:
    """missing の一意写像（§11）:

    - procedure breach                                    -> INVALID / PROCEDURE
    - critical output 全欠損 or 最小数割れで score/gate 計算不能
      -> NOT_EVALUABLE / OUTPUT_NOT_EVALUABLE
    - score 計算可能だが PRIMARY 一部 output missing で gate 不通過
      -> DIAGNOSTIC_ONLY / OUTPUT_MISSING
    - C0 入力側 critical missing                            -> NOT_EVALUABLE / INPUT_MISSING
    """
    return _MISSING_MAPPING[kind]


def gate_monotonicity_ok(passed: Sequence[ProcedureGate]) -> bool:
    """手続 Gate の単調性検査（§1 R1）: 後段 PASS は前段全 PASS を含意するか。"""
    return procedure_gates_monotonic(passed)
