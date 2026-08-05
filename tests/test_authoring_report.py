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


def test_l0s_spike_has_five_round_reports():
    """Sanity check the fixture set this backward-compat test relies on."""
    assert len(ROUND_REPORTS) == 5


@pytest.mark.parametrize("path", ROUND_REPORTS, ids=lambda p: p.parent.name)
def test_historical_report_parses_under_new_schema(path: Path):
    """L0-s's real report.json (rounds/round{1..5}/report.json) is backward
    compatible with the new AuthoringDiffReport schema — observed_sections/
    notes-item-shape/schema_version are all additive-optional."""

    data = json.loads(path.read_text(encoding="utf-8"))
    report = AuthoringDiffReport.model_validate(data)
    assert report.round in (1, 2, 3, 4, 5)
    assert report.symbolic_validation.status == "pass"
    assert set(report.axes) == {"key", "brightness", "structure"}
    assert report.notes == []


def test_axis_report_requires_all_fields():
    with pytest.raises(ValidationError):
        AxisReport(requirement="D minor", observed="D minor", verdict="preserved")  # missing band


def test_axis_report_rejects_unknown_band():
    with pytest.raises(ValidationError):
        AxisReport(requirement="D minor", observed="D minor", verdict="preserved", band="guessed")


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


def test_authoring_note_accepts_whitelisted_kind():
    note = AuthoringNote(kind="position_match_rate", value=0.75)
    assert note.kind == "position_match_rate"


def test_authoring_note_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        AuthoringNote(kind="free_text_comment", value="not allowed")


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
