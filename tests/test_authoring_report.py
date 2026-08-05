"""tests/test_authoring_report.py — AuthoringDiffReport normal form (D-L0a-4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from svp_rpe.authoring.report import (
    AuthoringDiffReport,
    AuthoringNote,
    AxisReport,
    ObservedSection,
    dump_json_bytes,
)
from svp_rpe.authoring.validate import AuthoringErrorItem, SymbolicValidationResult

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUND_REPORTS = sorted((REPO_ROOT / "examples" / "l0s_spike" / "rounds").glob("round*/report.json"))
ROUND_VALIDATIONS = sorted(
    (REPO_ROOT / "examples" / "l0s_spike" / "rounds").glob("round*/validation.json")
)


def test_l0s_spike_has_five_round_reports():
    """Sanity check the fixture set this backward-compat test relies on."""
    assert len(ROUND_REPORTS) == 5


def test_l0s_spike_has_five_round_validations():
    assert len(ROUND_VALIDATIONS) == 5


@pytest.mark.parametrize("path", ROUND_VALIDATIONS, ids=lambda p: p.parent.name)
def test_historical_validation_json_parses_under_symbolic_validation_result(path: Path):
    """L0-s's real validation.json (rounds/round{1..5}/validation.json, all
    `{"status": "pass"}` with no `errors` key) must still parse under the
    status/errors consistency guard added in PR #246 review round 4 A."""

    data = json.loads(path.read_text(encoding="utf-8"))
    result = SymbolicValidationResult.model_validate(data)
    assert result.status == "pass"
    assert result.errors is None


def test_symbolic_validation_result_accepts_consistent_pass():
    result = SymbolicValidationResult(status="pass")
    assert result.errors is None


def test_symbolic_validation_result_accepts_pass_with_errors_omitted_or_none():
    """PR #246 Codex P2 review 5 巡目: `status='pass'` accepts `errors`
    only when the key is absent or explicitly `None` — both must construct
    (this is the regression guard for the fix, not a negative case)."""

    assert SymbolicValidationResult(status="pass").errors is None
    assert SymbolicValidationResult(status="pass", errors=None).errors is None


def test_symbolic_validation_result_accepts_consistent_fail():
    result = SymbolicValidationResult(
        status="fail",
        errors=[AuthoringErrorItem(where="physical.bpm", message="bad", kind="type")],
    )
    assert result.errors is not None and len(result.errors) == 1


def test_symbolic_validation_result_rejects_pass_with_errors():
    """PR #246 Codex P2 review 4 巡目 A: `status='pass'` carrying `errors`
    is a contradictory result and must fail closed."""

    with pytest.raises(ValidationError):
        SymbolicValidationResult(
            status="pass",
            errors=[AuthoringErrorItem(where="physical.bpm", message="bad", kind="type")],
        )


def test_symbolic_validation_result_rejects_pass_with_explicit_empty_errors_list():
    """PR #246 Codex P2 review 5 巡目: closes the 4 巡目 A truthy-check gap
    — `errors: []` (an explicit empty list, not absent/None) is falsy and
    previously slipped past `if self.status == "pass" and self.errors:`.
    Must now be rejected identically to a non-empty `errors` list."""

    with pytest.raises(ValidationError):
        SymbolicValidationResult(status="pass", errors=[])


def test_symbolic_validation_result_rejects_fail_with_no_errors():
    with pytest.raises(ValidationError):
        SymbolicValidationResult(status="fail", errors=None)


def test_symbolic_validation_result_rejects_fail_with_empty_errors():
    """Regression guard (PR #246 review 5 巡目): the fail-side constraint
    (`not self.errors` — None or empty list both rejected) is unchanged by
    the pass-side fix above."""

    with pytest.raises(ValidationError):
        SymbolicValidationResult(status="fail", errors=[])


def test_authoring_diff_report_rejects_contradictory_nested_symbolic_validation():
    """The guard on `SymbolicValidationResult` propagates through nested nested
    construction inside `AuthoringDiffReport` (pydantic validates submodels)."""

    with pytest.raises(ValidationError):
        AuthoringDiffReport.model_validate(
            {
                "round": 1,
                "symbolic_validation": {"status": "fail", "errors": []},
            }
        )


@pytest.mark.parametrize("path", ROUND_REPORTS, ids=lambda p: p.parent.name)
def test_historical_report_parses_under_new_schema(path: Path):
    """L0-s's real report.json (rounds/round{1..5}/report.json) is backward
    compatible with the new AuthoringDiffReport schema — observed_sections/
    notes-item-shape/schema_version are all additive-optional."""

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "schema_version" not in data  # historical reports never wrote this field
    report = AuthoringDiffReport.model_validate(data)
    assert report.round in (1, 2, 3, 4, 5)
    assert report.symbolic_validation.status == "pass"
    assert set(report.axes) == {"key", "brightness", "structure"}
    assert report.notes == []
    # missing schema_version defaults to the current literal (backward compat).
    assert report.schema_version == "authoring-diff-report/1.0"


def test_schema_version_rejects_unknown_version_string():
    """Codex P2 review (PR #246): `schema_version` was a bare `str` — any
    string round-tripped, silently accepting a stale/foreign version tag.
    Now strict-typed to the current literal; the default is unaffected."""

    with pytest.raises(ValidationError):
        AuthoringDiffReport.model_validate(
            {
                "schema_version": "authoring-diff-report/0.9",
                "round": 1,
                "symbolic_validation": {"status": "pass"},
            }
        )


def test_schema_version_accepts_current_literal_explicitly():
    report = AuthoringDiffReport.model_validate(
        {
            "schema_version": "authoring-diff-report/1.0",
            "round": 1,
            "symbolic_validation": {"status": "pass"},
        }
    )
    assert report.schema_version == "authoring-diff-report/1.0"


def test_axis_report_requires_all_fields():
    with pytest.raises(ValidationError):
        AxisReport(requirement="D minor", observed="D minor", verdict="preserved")  # missing band


def test_axis_report_rejects_unknown_band():
    with pytest.raises(ValidationError):
        AxisReport(requirement="D minor", observed="D minor", verdict="preserved", band="guessed")


def test_axis_report_rejects_verdict_outside_frozen_vocabulary():
    """PR #246 Codex P2 review 7 巡目 A: `verdict` accepted any `str`
    previously — now fixed to the frozen `Verdict` vocabulary."""

    with pytest.raises(ValidationError):
        AxisReport(requirement="D minor", observed="D minor", verdict="mostly_ok", band="measured")


def test_authoring_diff_report_rejects_key_axis_with_structure_verdict():
    """PR #246 Codex P2 review 7 巡目 A: `key`/`brightness` axes only accept
    `preserved`/`deviated` — `exact_match`/`mismatch` are `structure`-only
    verdicts and must be rejected even though they're in the frozen
    `Verdict` vocabulary overall."""

    with pytest.raises(ValidationError):
        AuthoringDiffReport(
            round=1,
            symbolic_validation=SymbolicValidationResult(status="pass"),
            axes={
                "key": AxisReport(
                    requirement="D minor", observed="D minor", verdict="exact_match", band="measured"
                )
            },
        )


def test_authoring_diff_report_rejects_structure_axis_with_key_verdict():
    with pytest.raises(ValidationError):
        AuthoringDiffReport(
            round=1,
            symbolic_validation=SymbolicValidationResult(status="pass"),
            axes={
                "structure": AxisReport(
                    requirement=["intro"], observed=["intro"], verdict="preserved", band="measured"
                )
            },
        )


def test_authoring_diff_report_accepts_valid_known_axis_verdicts():
    report = AuthoringDiffReport(
        round=1,
        symbolic_validation=SymbolicValidationResult(status="pass"),
        axes={
            "key": AxisReport(
                requirement="D minor", observed="D minor", verdict="preserved", band="measured"
            ),
            "brightness": AxisReport(
                requirement="dark", observed="dark", verdict="deviated", band="measured"
            ),
            "structure": AxisReport(
                requirement=["intro"], observed=["intro"], verdict="mismatch", band="measured"
            ),
        },
    )
    assert report.axes["key"].verdict == "preserved"


def test_authoring_diff_report_accepts_any_frozen_verdict_for_unknown_axis():
    """Boundary declaration (PR #246 review 7 巡目 A): an axis name outside
    `_AXIS_VERDICTS` (a future L0b axis) accepts any `Verdict` — the report
    schema does not pre-fix the axis set itself."""

    report = AuthoringDiffReport(
        round=1,
        symbolic_validation=SymbolicValidationResult(status="pass"),
        axes={
            "tempo_stability": AxisReport(
                requirement="x", observed="y", verdict="mismatch", band="measured"
            )
        },
    )
    assert report.axes["tempo_stability"].verdict == "mismatch"


def test_axis_report_rejects_preserved_verdict_outside_measured_band():
    """PR #246 Codex P2 review 8 巡目 B: a success verdict (`preserved`/
    `exact_match`) must not smuggle past D5's band-annotation discipline by
    appearing outside `band='measured'`."""

    with pytest.raises(ValidationError):
        AxisReport(requirement="D minor", observed="D minor", verdict="preserved", band="out_of_band")


def test_axis_report_rejects_exact_match_verdict_outside_measured_band():
    with pytest.raises(ValidationError):
        AxisReport(
            requirement=["intro"], observed=["intro"], verdict="exact_match", band="not_observed"
        )


def test_axis_report_accepts_preserved_verdict_with_measured_band():
    axis = AxisReport(requirement="D minor", observed="D minor", verdict="preserved", band="measured")
    assert axis.band == "measured"


def test_axis_report_accepts_deviated_verdict_outside_measured_band():
    """Regression guard: only success-side verdicts require `measured` —
    a failure-side verdict (`deviated`/`mismatch`) outside `measured` is a
    legitimate honest report ("not confirmed, and not claiming success")."""

    axis = AxisReport(requirement="D minor", observed="C major", verdict="deviated", band="out_of_band")
    assert axis.band == "out_of_band"


def test_axis_report_accepts_mismatch_verdict_outside_measured_band():
    axis = AxisReport(requirement=["intro"], observed=[], verdict="mismatch", band="not_observed")
    assert axis.band == "not_observed"


def test_axis_report_accepts_observed_sections_schema():
    axis = AxisReport(
        requirement=["intro", "chorus", "outro"],
        observed=["intro", "chorus", "outro"],
        verdict="exact_match",
        band="measured",
        observed_sections=[
            ObservedSection(label="intro", start_seconds=0.0, end_seconds=7.5),
            ObservedSection(label="chorus", start_seconds=7.5, end_seconds=15.0),
        ],
    )
    assert axis.observed_sections is not None
    assert axis.observed_sections[0].label == "intro"


def test_observed_section_rejects_unknown_key():
    with pytest.raises(ValidationError):
        ObservedSection(label="intro", start_seconds=0.0, end_seconds=1.0, extra="x")


def test_observed_section_rejects_negative_start_seconds():
    """PR #246 Codex P2 review 7 巡目 B."""

    with pytest.raises(ValidationError):
        ObservedSection(label="intro", start_seconds=-1.0, end_seconds=5.0)


def test_observed_section_rejects_negative_end_seconds():
    with pytest.raises(ValidationError):
        ObservedSection(label="intro", start_seconds=0.0, end_seconds=-1.0)


def test_observed_section_rejects_reversed_interval():
    with pytest.raises(ValidationError):
        ObservedSection(label="intro", start_seconds=10.0, end_seconds=5.0)


def test_observed_section_rejects_zero_length_interval():
    with pytest.raises(ValidationError):
        ObservedSection(label="intro", start_seconds=5.0, end_seconds=5.0)


def test_observed_section_accepts_valid_interval():
    section = ObservedSection(label="intro", start_seconds=0.0, end_seconds=7.5)
    assert section.start_seconds == 0.0
    assert section.end_seconds == 7.5


def test_authoring_note_accepts_whitelisted_kind():
    note = AuthoringNote(kind="position_match_rate", value=0.75)
    assert note.kind == "position_match_rate"


def test_authoring_note_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        AuthoringNote(kind="free_text_comment", value="not allowed")


@pytest.mark.parametrize(
    "bad_value",
    ["high", 2.0, -0.1, float("nan"), float("inf"), float("-inf"), True, False],
    ids=["string", "above_one", "below_zero", "nan", "inf", "neg_inf", "true", "false"],
)
def test_authoring_note_rejects_invalid_position_match_rate_values(bad_value):
    """PR #246 Codex P2 review 8 巡目 A: `position_match_rate` must be a
    finite number in [0.0, 1.0] (bool excluded even though bool is an int
    subclass in Python)."""

    with pytest.raises(ValidationError):
        AuthoringNote(kind="position_match_rate", value=bad_value)


@pytest.mark.parametrize("good_value", [0.0, 0.5, 1.0])
def test_authoring_note_accepts_valid_position_match_rate_values(good_value):
    note = AuthoringNote(kind="position_match_rate", value=good_value)
    assert note.value == good_value


def test_authoring_diff_report_is_frozen():
    report = AuthoringDiffReport(
        round=1,
        symbolic_validation=SymbolicValidationResult(status="pass"),
    )
    with pytest.raises(ValidationError):
        report.round = 2  # type: ignore[misc]


def test_authoring_diff_report_rejects_unknown_top_level_key():
    with pytest.raises(ValidationError):
        AuthoringDiffReport.model_validate(
            {
                "round": 1,
                "symbolic_validation": {"status": "pass"},
                "unexpected": True,
            }
        )


def test_dump_json_bytes_is_deterministic():
    report = AuthoringDiffReport(
        round=3,
        symbolic_validation=SymbolicValidationResult(status="pass"),
        axes={
            "key": AxisReport(requirement="D minor", observed="D minor", verdict="preserved", band="measured"),
            "brightness": AxisReport(
                requirement="dark", observed="dark", verdict="preserved", band="measured"
            ),
        },
        notes=[AuthoringNote(kind="position_match_rate", value=0.5)],
    )
    first = dump_json_bytes(report)
    second = dump_json_bytes(report)
    assert first == second
    assert first.endswith(b"\n")
    # round-trip through json.loads to confirm well-formed bytes
    payload = json.loads(first.decode("utf-8"))
    assert payload["round"] == 3
    assert payload["axes"]["key"]["verdict"] == "preserved"


def test_dump_json_bytes_omits_none_fields_pass_case():
    report = AuthoringDiffReport(round=1, symbolic_validation=SymbolicValidationResult(status="pass"))
    payload = json.loads(dump_json_bytes(report).decode("utf-8"))
    assert "errors" not in payload["symbolic_validation"]


def test_dump_json_bytes_includes_errors_fail_case():
    report = AuthoringDiffReport(
        round=1,
        symbolic_validation=SymbolicValidationResult(
            status="fail",
            errors=[AuthoringErrorItem(where="physical.bpm", message="bad", kind="type")],
        ),
    )
    payload = json.loads(dump_json_bytes(report).decode("utf-8"))
    assert payload["symbolic_validation"]["errors"][0]["kind"] == "type"


def test_dump_json_bytes_sorts_keys():
    report = AuthoringDiffReport(round=1, symbolic_validation=SymbolicValidationResult(status="pass"))
    text = dump_json_bytes(report).decode("utf-8")
    # top-level keys sorted alphabetically: axes, notes, round, schema_version, symbolic_validation
    assert text.index('"axes"') < text.index('"round"') < text.index('"symbolic_validation"')
