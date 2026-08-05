"""tests/test_l0b_scripts.py — L0b loop runner scripts (`examples/l0b_loop/`).

Covers:
- `scripts/pareto_eval.py`'s `evaluate()` (improvement / non-regression tie /
  regression / band-excluded / axis-missing cases, pure unit tests).
- `scripts/run_round.py`'s output-collision guards (negative cases only —
  the positive path is exercised by the slow smoke test below).
- The pinned positive-control `report.json`'s schema conformance under
  `AuthoringDiffReport` (`src/svp_rpe/authoring/report.py`), including the
  boundary-second live wiring (`axes.structure.observed_sections`).
- A single slow end-to-end smoke test that re-runs the positive control
  through `run_round.py` and checks it reproduces the pinned report
  byte-for-byte (determinism regression guard).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from svp_rpe.authoring.report import AuthoringDiffReport

REPO_ROOT = Path(__file__).resolve().parents[1]
LOOP_DIR = REPO_ROOT / "examples" / "l0b_loop"
SCRIPTS_DIR = LOOP_DIR / "scripts"
PARETO_SPEC_PATH = LOOP_DIR / "frozen" / "pareto.yaml"
POSITIVE_CONTROL_REPORT_PATH = LOOP_DIR / "positive_control" / "report.json"
POSITIVE_CONTROL_SCORE_PATH = (
    REPO_ROOT / "examples" / "l0s_spike" / "positive_control" / "score.yaml"
)


def _load_module(name: str, path: Path) -> ModuleType:
    """Loads a standalone script (not a package) as an importable module —
    `examples/` is not on `sys.path` and these scripts are not part of the
    `svp_rpe` package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pareto_eval = _load_module("l0b_pareto_eval", SCRIPTS_DIR / "pareto_eval.py")
run_round = _load_module("l0b_run_round", SCRIPTS_DIR / "run_round.py")


def _axis(*, verdict: str, band: str = "measured", requirement=None, observed=None) -> dict:
    return {"requirement": requirement, "observed": observed, "verdict": verdict, "band": band}


def _report(**axes: dict) -> dict:
    return {
        "schema_version": "authoring-diff-report/1.0",
        "round": 1,
        "symbolic_validation": {"status": "pass"},
        "axes": axes,
        "notes": [],
    }


import yaml  # noqa: E402 (after module-level constants, matches script's own import ordering)

PARETO_SPEC = yaml.safe_load(PARETO_SPEC_PATH.read_text(encoding="utf-8"))


# --- pareto_eval.py unit tests -----------------------------------------------


def test_pareto_axes_match_spec():
    assert set(PARETO_SPEC["axes"]) == {"key", "brightness", "structure"}


def test_evaluate_reports_improvement_on_strict_structure_decrease_no_regression():
    prev = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )
    curr = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    result = pareto_eval.evaluate(prev, curr, PARETO_SPEC)
    assert result["improved"] is True
    assert result["band_excluded"] is False
    assert result["per_axis"]["structure"]["prev_distance"] == 1
    assert result["per_axis"]["structure"]["curr_distance"] == 0
    assert result["per_axis"]["structure"]["strictly_improved"] is True
    assert result["per_axis"]["key"]["strictly_improved"] is False
    assert result["per_axis"]["key"]["regressed"] is False


def test_evaluate_tie_is_not_an_improvement():
    prev = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    curr = json.loads(json.dumps(prev))
    curr["round"] = 2
    result = pareto_eval.evaluate(prev, curr, PARETO_SPEC)
    assert result["improved"] is False
    assert result["band_excluded"] is False
    assert all(not entry["strictly_improved"] for entry in result["per_axis"].values())


def test_evaluate_any_regression_blocks_improvement_even_with_another_strict_gain():
    """D6: cross-axis aggregation is forbidden — a structure gain must not
    offset a key regression."""
    prev = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )
    curr = _report(
        key=_axis(verdict="deviated"),  # regressed: preserved -> deviated
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    result = pareto_eval.evaluate(prev, curr, PARETO_SPEC)
    assert result["per_axis"]["key"]["regressed"] is True
    assert result["per_axis"]["structure"]["strictly_improved"] is True
    assert result["improved"] is False


def test_evaluate_band_not_measured_excludes_round_pair():
    prev = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="mismatch",
            band="not_observed",
            requirement=["intro", "chorus", "outro"],
            observed=[],
        ),
    )
    curr = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    result = pareto_eval.evaluate(prev, curr, PARETO_SPEC)
    assert result["band_excluded"] is True
    assert result["excluded_axes"] == ["structure"]
    assert result["improved"] is False


def test_evaluate_missing_axis_is_band_excluded_not_a_crash():
    """A symbolic_validation=fail report carries no axes at all — a round
    pair against such a report must be band-excluded, not raise."""
    prev = _report()  # axes={} (gate-failure shape)
    curr = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    result = pareto_eval.evaluate(prev, curr, PARETO_SPEC)
    assert result["band_excluded"] is True
    assert set(result["excluded_axes"]) == {"key", "brightness", "structure"}
    assert result["improved"] is False


def test_evaluate_rejects_pareto_spec_with_wrong_axis_set():
    bad_spec = {"axes": {"key": {}, "brightness": {}}}  # missing structure
    with pytest.raises(ValueError):
        pareto_eval.evaluate(_report(), _report(), bad_spec)


def test_levenshtein_matches_expected_distances():
    assert pareto_eval._levenshtein(["a", "b", "c"], ["a", "b", "c"]) == 0
    assert pareto_eval._levenshtein(["a", "b", "c"], ["a", "x", "c"]) == 1
    assert pareto_eval._levenshtein(["a", "b", "c"], ["a", "b"]) == 1
    assert pareto_eval._levenshtein([], []) == 0
    assert pareto_eval._levenshtein([], ["a"]) == 1
    assert pareto_eval._levenshtein(["Intro", "Chorus"], ["intro", "chorus"]) == 0


def test_pareto_eval_cli_writes_deterministic_output(tmp_path: Path):
    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    prev_path.write_text(json.dumps(_report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )))
    curr_path.write_text(json.dumps(_report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )))
    out_path = tmp_path / "result.json"
    exit_code = pareto_eval.main(
        [str(prev_path), str(curr_path), "--pareto", str(PARETO_SPEC_PATH), "-o", str(out_path)]
    )
    assert exit_code == 0
    result = json.loads(out_path.read_text())
    assert result["improved"] is True


# --- run_round.py output-collision guard negative cases ---------------------


def test_reject_workdir_inside_loop_tree():
    with pytest.raises(run_round.ProtectedPathError):
        run_round._reject_workdir_inside_loop_tree(LOOP_DIR / "scratch")


def test_reject_workdir_at_loop_tree_root():
    with pytest.raises(run_round.ProtectedPathError):
        run_round._reject_workdir_inside_loop_tree(LOOP_DIR)


def test_workdir_outside_loop_tree_is_accepted(tmp_path: Path):
    # Must not raise.
    resolved = run_round._reject_workdir_inside_loop_tree(tmp_path / "scratch")
    assert resolved == (tmp_path / "scratch").resolve()


def test_reject_score_copy_self_collision(tmp_path: Path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    score_path = paths["score_copy"]  # deliberately the exact collision path
    with pytest.raises(run_round.ProtectedPathError):
        run_round._reject_score_copy_self_collision(score_path, paths)


def test_reject_output_collision_with_existing_protected_tree_file():
    with pytest.raises(run_round.ProtectedPathError):
        run_round._reject_output_collision(
            LOOP_DIR / "task.md",  # exists, inside the protected tree
            score_path=POSITIVE_CONTROL_SCORE_PATH,
            reserved_paths=[],
        )


def test_reject_output_collision_with_fixed_input_path(tmp_path: Path):
    with pytest.raises(run_round.ProtectedPathError):
        run_round._reject_output_collision(
            run_round.CONTRACT_PATH,
            score_path=POSITIVE_CONTROL_SCORE_PATH,
            reserved_paths=[],
        )


def test_reject_output_collision_with_reserved_workdir_path(tmp_path: Path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    with pytest.raises(run_round.ProtectedPathError):
        run_round._reject_output_collision(
            paths["roundtrip"],
            score_path=POSITIVE_CONTROL_SCORE_PATH,
            reserved_paths=paths.values(),
        )


def test_output_collision_accepts_fresh_path_outside_loop_tree(tmp_path: Path):
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    # Must not raise: a brand-new path outside the protected tree and not a
    # reserved workdir artifact.
    run_round._reject_output_collision(
        tmp_path / "report.json",
        score_path=POSITIVE_CONTROL_SCORE_PATH,
        reserved_paths=paths.values(),
    )


# --- positive-control report.json schema conformance ------------------------


def test_positive_control_report_parses_as_authoring_diff_report():
    data = json.loads(POSITIVE_CONTROL_REPORT_PATH.read_text(encoding="utf-8"))
    report = AuthoringDiffReport.model_validate(data)
    assert report.round == 0
    assert report.symbolic_validation.status == "pass"
    assert set(report.axes) == {"key", "brightness", "structure"}
    assert report.axes["key"].verdict == "preserved"
    assert report.axes["brightness"].verdict == "preserved"
    assert report.axes["structure"].verdict == "exact_match"


def test_positive_control_report_has_live_boundary_seconds():
    data = json.loads(POSITIVE_CONTROL_REPORT_PATH.read_text(encoding="utf-8"))
    report = AuthoringDiffReport.model_validate(data)
    sections = report.axes["structure"].observed_sections
    assert sections is not None
    assert [section.label for section in sections] == ["intro", "chorus", "outro"]
    # Monotonic, non-overlapping, and chained (each section's end is the
    # next section's start) — a real audio-derived boundary sequence, not a
    # placeholder.
    assert sections[0].start_seconds == 0.0
    for earlier, later in zip(sections, sections[1:]):
        assert earlier.end_seconds == later.start_seconds
        assert later.end_seconds > later.start_seconds


def test_positive_control_report_notes_carry_position_match_rate():
    data = json.loads(POSITIVE_CONTROL_REPORT_PATH.read_text(encoding="utf-8"))
    report = AuthoringDiffReport.model_validate(data)
    assert any(note.kind == "position_match_rate" for note in report.notes)


# --- slow end-to-end smoke ---------------------------------------------------


@pytest.mark.slow
def test_positive_control_round_trip_reproduces_pinned_report(tmp_path: Path):
    """Re-renders the positive control through the full L0b pipeline
    (symbolic gate -> roundtrip/score-adherence -> perform -> package ->
    observe -> boundary-second live wiring -> AuthoringDiffReport) and
    checks the result is byte-identical to the pinned
    examples/l0b_loop/positive_control/report.json — a determinism
    regression guard for the whole chain, not just pareto_eval/report.py in
    isolation."""
    workdir = tmp_path / "positive_control"
    output_path = tmp_path / "report.json"
    result = run_round.run_round(POSITIVE_CONTROL_SCORE_PATH, workdir, 0, output_path)
    report = result["report"]
    assert report.symbolic_validation.status == "pass"
    assert report.axes["key"].verdict == "preserved"
    assert report.axes["brightness"].verdict == "preserved"
    assert report.axes["structure"].verdict == "exact_match"

    pinned_bytes = POSITIVE_CONTROL_REPORT_PATH.read_bytes()
    assert output_path.read_bytes() == pinned_bytes
