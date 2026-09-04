"""v1.1 §V2.2/§V2.3 — `c0_validate` の holdout sweep pinning 検証（D77 同型）。

対象: `_check_claim_relevant_fields_match()` / `_check_holdout_pin_
feasibility()` / `_check_holdout_sweeps_declaration_match()` /
`_check_holdout_sweeps_realized_membership()`（いずれも `c0_validate.py`
非公開関数だが、既存の D76/D77 系テスト — `test_c0_validate.py` の
`test_declared_sweeps_*` — と同じ規約でモジュール内部関数を直接 import して
検証する）。

新規キー（`claim_relevant_fields`/`holdout_sweeps`）はどちらも
`FIXTURE_SPEC_REQUIRED_KEYS` に加えていない（v1.1 以前の manifest fixture
との後方互換のため）ため、既存の `_complete_manifest()`（`test_c0_validate.py`）
は本テストの対象外——ここでは最小限の手組み manifest を直接使う。
"""

from __future__ import annotations

from voice_genesis.calibration import c0_validate, vocab
from voice_genesis.calibration.fixtures.matrix import (
    build_matrix,
    claim_relevant_fields_by_family,
    declared_sweeps_by_family,
    pin_holdout_sweeps_by_family,
)
from voice_genesis.calibration.vocab import Split

_ROWS = build_matrix()
_DERIVED_CLAIM_RELEVANT = claim_relevant_fields_by_family(_ROWS)
_DECLARED_SWEEPS = declared_sweeps_by_family(_ROWS)
_SECRET = b"\x42" * 32
_PINNED = pin_holdout_sweeps_by_family(_ROWS, _SECRET)


def _manifest_with_fixture_spec(fixture_spec: dict[str, object]) -> dict[str, object]:
    return {"frozen_design": {"fixture_spec": fixture_spec}}


# ---------------------------------------------------------------------------
# _check_claim_relevant_fields_match
# ---------------------------------------------------------------------------


def test_claim_relevant_fields_absent_is_not_a_violation() -> None:
    """新規キーであり REQUIRED ではないため、欠落は無視する（v1.1 以前の
    manifest との後方互換）。"""
    manifest = _manifest_with_fixture_spec({"TRANSITION_GT": {}})
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert violations == ()


def test_claim_relevant_fields_correct_declaration_passes() -> None:
    manifest = _manifest_with_fixture_spec(
        {
            "TRANSITION_GT": {"claim_relevant_fields": list(_DERIVED_CLAIM_RELEVANT["TRANSITION_GT"])},
            "APERIODICITY_GT": {
                "claim_relevant_fields": list(_DERIVED_CLAIM_RELEVANT["APERIODICITY_GT"])
            },
            "TILT_GT": {"claim_relevant_fields": []},
        }
    )
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert violations == ()


def test_claim_relevant_fields_wrong_declaration_blocks() -> None:
    manifest = _manifest_with_fixture_spec(
        {"TRANSITION_GT": {"claim_relevant_fields": ["join_type"]}}  # missing duration_class
    )
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert len(violations) == 1
    assert violations[0].violation == "claim_relevant_field_mismatch"
    assert violations[0].family == "TRANSITION_GT"

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
    assert result.claim_relevant_field_violations == violations


def test_claim_relevant_fields_non_list_value_blocks() -> None:
    manifest = _manifest_with_fixture_spec({"TRANSITION_GT": {"claim_relevant_fields": "oops"}})
    violations = c0_validate._check_claim_relevant_fields_match(manifest)
    assert len(violations) == 1
    assert violations[0].family == "TRANSITION_GT"


# ---------------------------------------------------------------------------
# _check_holdout_pin_feasibility
# ---------------------------------------------------------------------------


def test_holdout_pin_feasibility_empty_for_canonical_matrix() -> None:
    """456 セル canonical matrix は常に feasible（§V2.2 実測値表）。"""
    violations = c0_validate._check_holdout_pin_feasibility({})
    assert violations == ()


# ---------------------------------------------------------------------------
# _check_holdout_sweeps_declaration_match
# ---------------------------------------------------------------------------


def _holdout_sweeps_manifest(pinned: dict[str, dict[str, tuple[str, ...]]]) -> dict[str, object]:
    return {
        "holdout_sweeps": {
            family: {sid: list(rids) for sid, rids in sweeps.items()}
            for family, sweeps in pinned.items()
        }
    }


def test_holdout_sweeps_absent_is_not_a_violation() -> None:
    violations = c0_validate._check_holdout_sweeps_declaration_match({}, None)
    assert violations == ()
    violations_with_secret = c0_validate._check_holdout_sweeps_declaration_match({}, _SECRET)
    assert violations_with_secret == ()


def test_holdout_sweeps_correct_pin_passes_with_and_without_secret() -> None:
    manifest = _holdout_sweeps_manifest(_PINNED)
    # secret 非依存の構造検査（k_hold 数・declared_sweeps との member 一致）。
    assert c0_validate._check_holdout_sweeps_declaration_match(manifest, None) == ()
    # secret 依存の完全再導出照合。
    assert c0_validate._check_holdout_sweeps_declaration_match(manifest, _SECRET) == ()

    result = c0_validate.validate_c0_manifest(manifest, split_secret=_SECRET)
    assert result.holdout_pin_declaration_violations == ()


def test_holdout_sweeps_wrong_secret_detects_mismatch() -> None:
    """宣言は secret A での正しい pin だが、実際の split_secret は別の値
    （B）——完全再導出照合が secret 依存で不一致を検出する。"""
    manifest = _holdout_sweeps_manifest(_PINNED)
    other_secret = b"\x99" * 32
    other_pinned = pin_holdout_sweeps_by_family(_ROWS, other_secret)
    assert other_pinned != _PINNED  # サニティ: 異なる secret は異なる選抜を返す

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, other_secret)
    assert violations, "expected a mismatch when re-derived under a different split_secret"
    assert all(v.violation == "holdout_pin_declaration_mismatch" for v in violations)


def test_holdout_sweeps_wrong_member_row_ids_detected_without_secret() -> None:
    """secret を持たない構造検査でも、declared_sweeps に存在しない sweep_id
    /member 行の宣言は検出できる。"""
    tampered = {
        family: dict(sweeps) for family, sweeps in _PINNED.items()
    }
    tampered["FORMANT_GT"] = dict(tampered["FORMANT_GT"])
    bogus_sweep_id = next(iter(tampered["FORMANT_GT"]))
    tampered["FORMANT_GT"][bogus_sweep_id] = ("not-a-real-row-id",)
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert violations
    assert any(v.family == "FORMANT_GT" and v.sweep_id == bogus_sweep_id for v in violations)


def test_holdout_sweeps_wrong_pin_count_detected_without_secret() -> None:
    """k_hold と異なる pin 数（宣言 sweep 数）の宣言は secret 非依存でも
    検出できる。"""
    tampered = {family: dict(sweeps) for family, sweeps in _PINNED.items()}
    # IDENTITY_CAUSAL_SWEEP の k_hold=4 のうち 1 個を落として 3 個にする。
    ident = tampered["IDENTITY_CAUSAL_SWEEP"]
    dropped_sweep_id = next(iter(ident))
    tampered["IDENTITY_CAUSAL_SWEEP"] = {
        sid: members for sid, members in ident.items() if sid != dropped_sweep_id
    }
    manifest = _holdout_sweeps_manifest(tampered)

    violations = c0_validate._check_holdout_sweeps_declaration_match(manifest, None)
    assert any(
        v.family == "IDENTITY_CAUSAL_SWEEP" and v.expected_count == 4 and v.actual_count == 3
        for v in violations
    )


# ---------------------------------------------------------------------------
# _check_holdout_sweeps_realized_membership
# ---------------------------------------------------------------------------


def test_holdout_membership_absent_realized_split_is_not_a_violation() -> None:
    manifest = _holdout_sweeps_manifest(_PINNED)
    assert c0_validate._check_holdout_sweeps_realized_membership(manifest) == ()


def test_holdout_membership_passes_when_all_pinned_rows_are_holdout() -> None:
    assignment = {}
    for family, sweeps in _PINNED.items():
        for member_row_ids in sweeps.values():
            for rid in member_row_ids:
                assignment[rid] = Split.HOLDOUT.value
    manifest = _holdout_sweeps_manifest(_PINNED)
    manifest["realized_split"] = {"assignment": assignment}

    violations = c0_validate._check_holdout_sweeps_realized_membership(manifest)
    assert violations == ()


def test_holdout_membership_detects_pinned_row_not_in_holdout() -> None:
    """§V2.3 fail-closed: `holdout_sweeps` の member 行が 1 行でも realized
    split 上で HOLDOUT でなければ検出する。"""
    assignment = {}
    for family, sweeps in _PINNED.items():
        for member_row_ids in sweeps.values():
            for rid in member_row_ids:
                assignment[rid] = Split.HOLDOUT.value
    # 1 行だけ改竄して SELECTION にする（割当実装の欠陥を模す）。
    any_family = next(iter(_PINNED))
    any_sweep_members = next(iter(_PINNED[any_family].values()))
    offending_row_id = any_sweep_members[0]
    assignment[offending_row_id] = Split.SELECTION.value

    manifest = _holdout_sweeps_manifest(_PINNED)
    manifest["realized_split"] = {"assignment": assignment}

    violations = c0_validate._check_holdout_sweeps_realized_membership(manifest)
    assert violations
    assert all(v.violation == "holdout_pin_not_in_holdout_split" for v in violations)
    assert any(v.family == any_family for v in violations)

    result = c0_validate.validate_c0_manifest(manifest)
    assert vocab.BlockedCode.BLOCKED_C0_MANIFEST_INCOMPLETE in result.blocked_codes
