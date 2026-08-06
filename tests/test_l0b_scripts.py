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

# T2 (`examples/l0b_loop/task_t2.md`) — 4-section canonical map + positive control.
SECTION_MAP_T2_PATH = LOOP_DIR / "frozen" / "section_map_t2.json"
POSITIVE_CONTROL_T2_SCORE_PATH = LOOP_DIR / "positive_control_t2" / "score.yaml"
POSITIVE_CONTROL_T2_REPORT_PATH = LOOP_DIR / "positive_control_t2" / "report.json"


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


# --- D1 (PR #247 Codex review round 3, P2): structure tokens must be str, no str() coercion ---


def test_evaluate_rejects_int_token_in_structure_requirement():
    prev = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="mismatch",  # failure side: AuthoringDiffReport allows Any here
            requirement=["intro", 2, "outro"],
            observed=["intro", "chorus", "outro"],
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
    with pytest.raises(ValueError, match="must be a str token"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_rejects_none_token_in_structure_observed():
    prev = _report(
        key=_axis(verdict="preserved"),
        brightness=_axis(verdict="preserved"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", None, "outro"],
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
    with pytest.raises(ValueError, match="must be a str token"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_accepts_all_str_structure_tokens_unchanged():
    """Normal-path regression guard: all-str requirement/observed still
    compute the same distances as before D1 (no behavior change on the
    happy path — only non-str tokens now fail loudly instead of being
    silently str()-coerced)."""
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
    assert result["per_axis"]["structure"]["prev_distance"] == 1
    assert result["per_axis"]["structure"]["curr_distance"] == 0


def test_main_exits_nonzero_on_int_token_in_structure_axis(tmp_path: Path):
    """`main()`-level negative case: a `mismatch`-verdict structure axis
    with a non-str token passes `AuthoringDiffReport` validation (failure
    side keeps the `Any` design — see `src/svp_rpe/authoring/report.py`)
    but must still fail via `evaluate()`'s `_axis_distance`, caught by
    `main()` the same way other `ValueError`s are (stderr + exit 1)."""
    prev = _report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", 2, "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    curr = _report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    out_path = tmp_path / "result.json"
    prev_path.write_text(json.dumps(prev))
    curr_path.write_text(json.dumps(curr))

    exit_code = pareto_eval.main(
        [str(prev_path), str(curr_path), "--pareto", str(PARETO_SPEC_PATH), "-o", str(out_path)]
    )
    assert exit_code != 0
    assert not out_path.exists()


# --- C2 (PR #247 Codex review round 2, P2): pareto_eval.py spec/implementation contract ---


def test_validate_pareto_spec_contract_accepts_canonical_spec():
    # Must not raise, and must return the axes mapping.
    axes = pareto_eval._validate_pareto_spec_contract(PARETO_SPEC)
    assert set(axes) == {"key", "brightness", "structure"}


def test_validate_pareto_spec_contract_rejects_wrong_schema_version():
    bad_spec = {**PARETO_SPEC, "schema_version": "l0b-pareto/2.0"}
    with pytest.raises(ValueError, match="schema_version"):
        pareto_eval._validate_pareto_spec_contract(bad_spec)


def test_validate_pareto_spec_contract_rejects_higher_is_better_order():
    bad_axes = {
        **PARETO_SPEC["axes"],
        "key": {**PARETO_SPEC["axes"]["key"], "order": "higher_is_better"},
    }
    bad_spec = {**PARETO_SPEC, "axes": bad_axes}
    with pytest.raises(ValueError, match="order"):
        pareto_eval._validate_pareto_spec_contract(bad_spec)


def test_validate_pareto_spec_contract_rejects_changed_structure_distance():
    bad_axes = {
        **PARETO_SPEC["axes"],
        "structure": {**PARETO_SPEC["axes"]["structure"], "distance": "hamming"},
    }
    bad_spec = {**PARETO_SPEC, "axes": bad_axes}
    with pytest.raises(ValueError, match="distance"):
        pareto_eval._validate_pareto_spec_contract(bad_spec)


def test_validate_pareto_spec_contract_rejects_missing_prose_rule():
    bad_spec = {**PARETO_SPEC, "tie_rule": ""}
    with pytest.raises(ValueError, match="tie_rule"):
        pareto_eval._validate_pareto_spec_contract(bad_spec)


# --- D2 (PR #247 Codex review round 3, P2): prose rules pinned to frozen/pareto.yaml's exact wording ---


def test_validate_pareto_spec_contract_rejects_reworded_improvement_rule():
    bad_spec = {**PARETO_SPEC, "improvement_rule": PARETO_SPEC["improvement_rule"] + " "}
    with pytest.raises(ValueError, match="improvement_rule"):
        pareto_eval._validate_pareto_spec_contract(bad_spec)


def test_validate_pareto_spec_contract_rejects_reworded_tie_rule():
    bad_spec = {**PARETO_SPEC, "tie_rule": PARETO_SPEC["tie_rule"].replace("NOT", "not")}
    with pytest.raises(ValueError, match="tie_rule"):
        pareto_eval._validate_pareto_spec_contract(bad_spec)


def test_validate_pareto_spec_contract_rejects_reworded_band_rule():
    bad_spec = {**PARETO_SPEC, "band_rule": PARETO_SPEC["band_rule"][:-1]}
    with pytest.raises(ValueError, match="band_rule"):
        pareto_eval._validate_pareto_spec_contract(bad_spec)


def test_evaluate_rejects_via_main_with_nonzero_exit(tmp_path: Path):
    """`main()` catches the spec/implementation contract `ValueError` from
    `evaluate()` the same way it already catches `_load_report()`'s
    validation errors: stderr + exit 1, nothing written to `-o`."""
    good = _report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    bad_pareto_path = tmp_path / "bad_pareto.yaml"
    out_path = tmp_path / "result.json"
    prev_path.write_text(json.dumps(good))
    curr_path.write_text(json.dumps(good))
    bad_spec = {**PARETO_SPEC, "improvement_rule": "   "}
    bad_pareto_path.write_text(yaml.safe_dump(bad_spec))

    exit_code = pareto_eval.main(
        [str(prev_path), str(curr_path), "--pareto", str(bad_pareto_path), "-o", str(out_path)]
    )
    assert exit_code != 0
    assert not out_path.exists()


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
    # key/brightness carry real requirement/observed values: since the B1
    # gate (PR #247 Codex P1) validates inputs as AuthoringDiffReport, a
    # success verdict with requirement=None is now (correctly) rejected —
    # real run_round.py output always populates these.
    prev_path.write_text(json.dumps(_report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )))
    curr_path.write_text(json.dumps(_report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
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


# --- D3 (PR #247 Codex review round 3, P2): `-o` publish goes through atomic_write_bytes ---


def test_pareto_eval_cli_atomic_write_replaces_existing_output_file(tmp_path: Path):
    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    prev_path.write_text(json.dumps(_report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )))
    curr_path.write_text(json.dumps(_report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )))
    out_path = tmp_path / "result.json"
    out_path.write_text("stale placeholder content that must be entirely replaced, not appended")
    exit_code = pareto_eval.main(
        [str(prev_path), str(curr_path), "--pareto", str(PARETO_SPEC_PATH), "-o", str(out_path)]
    )
    assert exit_code == 0
    # A plain `write_bytes` onto a pre-existing, longer file would already
    # fully overwrite it too — the behavior this asserts is specific to
    # publish going through `atomic_write_bytes` (tempfile + os.replace):
    # the result parses as clean JSON with no leftover stale bytes.
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


# --- T2 (`examples/l0b_loop/task_t2.md`) --------------------------------------


def test_section_map_default_is_t1_frozen_map(monkeypatch, tmp_path: Path):
    """`--section-map`'s default must be T1's frozen map — checked at the
    argparse/`main()` dispatch level only (no pipeline run), so T1 callers
    that never pass the new flag keep byte-identical behavior."""
    captured: dict[str, Path] = {}

    def fake_run_round(score_path, workdir, round_number, output_path, *, section_map_path):
        captured["section_map_path"] = section_map_path
        return {
            "report": AuthoringDiffReport(
                round=round_number,
                symbolic_validation=run_round.SymbolicValidationResult(status="pass"),
                axes={},
                notes=[],
            )
        }

    monkeypatch.setattr(run_round, "run_round", fake_run_round)
    exit_code = run_round.main(
        [
            str(POSITIVE_CONTROL_SCORE_PATH),
            "--workdir",
            str(tmp_path / "wd"),
            "-o",
            str(tmp_path / "report.json"),
        ]
    )
    assert exit_code == 0
    assert captured["section_map_path"] == run_round.SECTION_MAP_PATH


def test_section_map_t2_json_content_is_frozen():
    data = json.loads(SECTION_MAP_T2_PATH.read_text(encoding="utf-8"))
    assert data == {
        "schema_version": "section-map/0.1",
        "sections": ["intro", "chorus", "chorus", "outro"],
    }


def test_structure_axis_requirement_is_read_from_selected_section_map():
    """Cheap unit test (no extraction/audio): `sensor.available: False` keeps
    `_structure_axis` from calling `extract_rpe_from_file`, but
    `requirement` is still read from whichever `section_map_path` is passed
    in — this is the T1/T2 dispatch `run_round.py` gained for --section-map."""
    observe_report = {
        "anchors": [
            {
                "anchor_id": "structure",
                "measurements": {"observed_sections": []},
                "adherence_status": "mismatch",
                "sensor": {"available": False},
            }
        ]
    }
    axis_report, position_match_rate = run_round._structure_axis(
        observe_report, Path("unused-take.wav"), section_map_path=run_round.SECTION_MAP_PATH
    )
    assert axis_report.requirement == ["intro", "chorus", "outro"]
    assert axis_report.band == "not_observed"
    assert position_match_rate is None

    axis_report_t2, _ = run_round._structure_axis(
        observe_report, Path("unused-take.wav"), section_map_path=SECTION_MAP_T2_PATH
    )
    assert axis_report_t2.requirement == ["intro", "chorus", "chorus", "outro"]


@pytest.mark.slow
def test_positive_control_t2_round_trip_reproduces_pinned_report(tmp_path: Path):
    """T2 counterpart of test_positive_control_round_trip_reproduces_pinned_report
    above — re-renders the T2 positive control (`build/l0b/t2_probe`'s passing
    hand-authored 4-section candidate, saved to
    `examples/l0b_loop/positive_control_t2/score.yaml`) through the T2 path
    (`--section-map frozen/section_map_t2.json`) and checks the result is
    byte-identical to the pinned `examples/l0b_loop/positive_control_t2/
    report.json` — a determinism regression guard for the T2 chain."""
    workdir = tmp_path / "positive_control_t2"
    output_path = tmp_path / "report.json"
    result = run_round.run_round(
        POSITIVE_CONTROL_T2_SCORE_PATH,
        workdir,
        0,
        output_path,
        section_map_path=SECTION_MAP_T2_PATH,
    )
    report = result["report"]
    assert report.symbolic_validation.status == "pass"
    assert report.axes["key"].verdict == "preserved"
    assert report.axes["brightness"].verdict == "preserved"
    assert report.axes["structure"].verdict == "exact_match"
    assert report.axes["structure"].observed == ["intro", "chorus", "chorus", "outro"]

    pinned_bytes = POSITIVE_CONTROL_T2_REPORT_PATH.read_bytes()
    assert output_path.read_bytes() == pinned_bytes


# --- B1/B2 (Codex review PR #247 #5): pareto_eval.py input validation + -o guard ---


def _schema_valid_report(**axes: dict) -> dict:
    """Like `_report()` above, but every axis carries a well-formed value
    for its verdict (as `AuthoringDiffReport` requires for a *success*
    verdict) — used for the new B1/B2 tests below, which exercise
    `pareto_eval.main()`'s new `AuthoringDiffReport.model_validate` gate and
    therefore need schema-valid fixtures (unlike `_report()`/`_axis()`
    above, whose `requirement=None`/`observed=None` defaults are fine for
    the pre-existing `evaluate()`-only tests but are not valid
    `AuthoringDiffReport` input)."""
    return _report(**axes)


def _valid_key_axis(*, verdict: str = "preserved") -> dict:
    return _axis(verdict=verdict, requirement="D minor", observed="D minor")


def _valid_brightness_axis(*, verdict: str = "preserved") -> dict:
    return _axis(verdict=verdict, requirement="dark", observed="dark")


def _valid_structure_axis(*, verdict: str = "exact_match") -> dict:
    return _axis(
        verdict=verdict,
        requirement=["intro", "chorus", "outro"],
        observed=["intro", "chorus", "outro"],
    )


def test_main_rejects_contradictory_report_with_nonzero_exit(tmp_path: Path):
    """B1 negative case: a report whose `key` axis claims a success verdict
    (`preserved`) outside the `measured` band is internally contradictory
    per `AuthoringDiffReport`'s own `_success_verdict_requires_measured_band`
    validator. `main()`'s new validation gate must reject it before
    `evaluate()` ever runs, with a non-zero exit — not silently compute a
    Pareto result over untrustworthy input."""
    good = _schema_valid_report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    contradictory = json.loads(json.dumps(good))
    contradictory["axes"]["key"]["band"] = "not_observed"  # preserved x not measured

    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    out_path = tmp_path / "result.json"
    prev_path.write_text(json.dumps(contradictory))
    curr_path.write_text(json.dumps(good))

    exit_code = pareto_eval.main(
        [str(prev_path), str(curr_path), "--pareto", str(PARETO_SPEC_PATH), "-o", str(out_path)]
    )
    assert exit_code != 0
    assert not out_path.exists()


def test_main_accepts_schema_valid_reports(tmp_path: Path):
    """Positive counterpart: schema-valid reports still pass the new gate
    and produce the same deterministic output as before (evaluate()'s
    output is untouched by validation — B1's docstring's byte-compat
    claim)."""
    good = _schema_valid_report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )
    improved = _schema_valid_report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    out_path = tmp_path / "result.json"
    prev_path.write_text(json.dumps(good))
    curr_path.write_text(json.dumps(improved))

    exit_code = pareto_eval.main(
        [str(prev_path), str(curr_path), "--pareto", str(PARETO_SPEC_PATH), "-o", str(out_path)]
    )
    assert exit_code == 0
    assert json.loads(out_path.read_text())["improved"] is True


def test_pareto_eval_reject_output_collision_with_prev_report():
    with pytest.raises(pareto_eval.ProtectedPathError):
        pareto_eval._reject_output_collision(
            Path("prev.json"),
            prev_report_path=Path("prev.json"),
            curr_report_path=Path("curr.json"),
            pareto_path=PARETO_SPEC_PATH,
        )


def test_pareto_eval_reject_output_collision_with_pareto_spec():
    with pytest.raises(pareto_eval.ProtectedPathError):
        pareto_eval._reject_output_collision(
            PARETO_SPEC_PATH,
            prev_report_path=Path("prev.json"),
            curr_report_path=Path("curr.json"),
            pareto_path=PARETO_SPEC_PATH,
        )


def test_pareto_eval_output_collision_accepts_fresh_path(tmp_path: Path):
    # Must not raise.
    pareto_eval._reject_output_collision(
        tmp_path / "result.json",
        prev_report_path=Path("prev.json"),
        curr_report_path=Path("curr.json"),
        pareto_path=PARETO_SPEC_PATH,
    )


def test_main_rejects_output_collision_with_curr_report(tmp_path: Path):
    """B2 negative case, at the `main()` CLI level: `-o` aliasing
    `curr_report` is refused before anything is written (and before the
    reports are even parsed/validated — the guard runs first)."""
    good = _schema_valid_report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    prev_path.write_text(json.dumps(good))
    curr_path.write_text(json.dumps(good))

    exit_code = pareto_eval.main(
        [str(prev_path), str(curr_path), "--pareto", str(PARETO_SPEC_PATH), "-o", str(curr_path)]
    )
    assert exit_code != 0
    # The guard must have fired before curr.json was touched/overwritten.
    assert json.loads(curr_path.read_text()) == good


# --- B3 (Codex review PR #247 #5): run_round.py single input-snapshot reads ---


def test_prepare_scores_does_not_reread_score_path(tmp_path: Path):
    """`_prepare_scores` must derive everything from the `score_bytes`
    argument, not re-read `score_path` off disk — passing a `score_path`
    that does not exist proves this (a re-read would raise
    `FileNotFoundError`)."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    score_bytes = POSITIVE_CONTROL_SCORE_PATH.read_bytes()
    nonexistent_score_path = tmp_path / "does-not-exist.yaml"

    eval_score_path, eval_score_sha256 = run_round._prepare_scores(
        nonexistent_score_path, score_bytes, paths
    )
    assert eval_score_path == paths["eval_score"]
    assert eval_score_path.exists()
    assert eval_score_sha256 == run_round._sha256_bytes(eval_score_path.read_bytes())
    # The eval copy carries the injected control_profile on top of the
    # source score's own content.
    eval_data = yaml.safe_load(eval_score_path.read_text(encoding="utf-8"))
    assert "control_profile" in eval_data


def test_structure_axis_does_not_reread_section_map_path_when_bytes_given(tmp_path: Path):
    """`_structure_axis`'s `section_map_bytes` parameter, when given, is
    used instead of reading `section_map_path` — passing a nonexistent
    `section_map_path` alongside real bytes proves the path is not
    re-read."""
    observe_report = {
        "anchors": [
            {
                "anchor_id": "structure",
                "measurements": {"observed_sections": []},
                "adherence_status": "mismatch",
                "sensor": {"available": False},
            }
        ]
    }
    nonexistent_section_map_path = tmp_path / "does-not-exist.json"
    section_map_bytes = run_round.SECTION_MAP_PATH.read_bytes()

    axis_report, _ = run_round._structure_axis(
        observe_report,
        Path("unused-take.wav"),
        section_map_path=nonexistent_section_map_path,
        section_map_bytes=section_map_bytes,
    )
    assert axis_report.requirement == ["intro", "chorus", "outro"]


def test_write_identity_manifest_uses_given_section_map_bytes(tmp_path: Path):
    """`_write_identity_manifest` writes exactly the `section_map_bytes` it
    is given (no independent re-read of a section-map path) into
    `identity/section_map.json`."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    section_map_bytes = run_round.SECTION_MAP_PATH.read_bytes()

    manifest_path, manifest_sha256 = run_round._write_identity_manifest(
        paths,
        score_sha256="deadbeef",
        round_number=0,
        section_map_bytes=section_map_bytes,
    )
    assert manifest_path.exists()
    assert manifest_sha256 == run_round._sha256_bytes(manifest_path.read_bytes())
    assert paths["identity_section_map"].read_bytes() == section_map_bytes


# --- C1 (PR #247 Codex review round 2, P2): run_round.py report/hashes bundle rollback ---
#
# Supersedes the old "hashes written before report" ordering-only test: that
# ordering alone left a provenance-mismatch window when `-o` names an
# existing path *outside* the protected loop tree (the output-collision
# guard only refuses an existing path *inside* the loop tree) — a failure
# partway through the report write could leave an *old* report at `-o`
# while `hashes.json` already names the *new* report's hash. C1 bundles
# both writes with snapshot + rollback so a failed publish attempt leaves
# no trace at all, restoring pre-existing bytes (or absence) on both paths.


def test_run_round_report_bundle_rolls_back_pre_existing_bytes_on_symbolic_fail(
    tmp_path: Path, monkeypatch
):
    """On the symbolic-fail early-return path, injecting a failure into the
    report's (`-o`) atomic write must roll the *whole bundle* back: (a)
    `-o`'s pre-existing bytes are restored (not left as whatever the failed
    attempt produced), and (b) `hashes.json`'s pre-existing bytes are
    restored too — even though the hashes write itself succeeded before the
    report write failed."""
    bad_score_path = tmp_path / "bad_score.yaml"
    bad_score_path.write_text("not: a valid composition score\n", encoding="utf-8")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    output_path = tmp_path / "report.json"

    # Pre-existing bytes at both bundle targets (simulating this round's
    # workdir already carrying a prior report/hashes pair) must survive a
    # failed re-publish attempt byte-for-byte.
    old_hashes_bytes = b'{"round": "old-hashes"}'
    old_report_bytes = b'{"round": "old-report"}'
    (workdir / "hashes.json").write_bytes(old_hashes_bytes)
    output_path.write_bytes(old_report_bytes)

    real_atomic_write_bytes = run_round.atomic_write_bytes
    # Fails only the *first* write attempt to `output_path` (the original
    # publish of the new report) — the rollback's subsequent write of
    # `old_report_bytes` back to `output_path` must succeed, so this proves
    # a genuine restore, not merely "the file was never touched".
    output_writes = {"count": 0}

    def _fail_first_report_write(path: Path, data: bytes) -> None:
        if path == output_path:
            output_writes["count"] += 1
            if output_writes["count"] == 1:
                raise RuntimeError("boom: simulated failure writing the report")
        real_atomic_write_bytes(path, data)

    monkeypatch.setattr(run_round, "atomic_write_bytes", _fail_first_report_write)

    with pytest.raises(RuntimeError, match="boom"):
        run_round.run_round(bad_score_path, workdir, 0, output_path)

    assert (workdir / "hashes.json").read_bytes() == old_hashes_bytes
    assert output_path.read_bytes() == old_report_bytes
    assert output_writes["count"] == 2  # 1 failed original write + 1 successful rollback restore


def test_publish_report_bundle_removes_paths_that_did_not_exist_before(tmp_path: Path):
    """Unit-level counterpart to the fail-gate test above, exercising
    `_publish_report_bundle` directly: when neither bundle path existed
    before the call, a failed report write must roll back to "does not
    exist" (delete), not leave a half-published hashes.json behind."""
    hashes_path = tmp_path / "hashes.json"
    output_path = tmp_path / "report.json"
    real_atomic_write_bytes = run_round.atomic_write_bytes

    def _fail_on_report(path: Path, data: bytes) -> None:
        if path == output_path:
            raise RuntimeError("boom: simulated failure writing the report")
        real_atomic_write_bytes(path, data)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_round, "atomic_write_bytes", _fail_on_report)
        with pytest.raises(RuntimeError, match="boom"):
            run_round._publish_report_bundle(
                output_path=output_path,
                report_bytes=b'{"new": "report"}',
                hashes_path=hashes_path,
                hashes_bytes=b'{"new": "hashes"}',
            )

    assert not hashes_path.exists()
    assert not output_path.exists()


def test_publish_report_bundle_rollback_failure_chains_original_exception(tmp_path: Path):
    """If the rollback itself also fails, the original publish exception
    must not be silently swallowed: `_publish_report_bundle` raises
    `ReportBundleRollbackError` chained (`__cause__`) from the rollback
    failure, and the original exception's message survives in the new
    exception's own message."""
    hashes_path = tmp_path / "hashes.json"
    output_path = tmp_path / "report.json"
    hashes_path.write_bytes(b"old-hashes")
    output_path.write_bytes(b"old-report")

    real_atomic_write_bytes = run_round.atomic_write_bytes

    def _flaky(path: Path, data: bytes) -> None:
        if path == output_path and data == b'{"new": "report"}':
            raise RuntimeError("boom: simulated failure writing the report")
        if path == hashes_path and data == b"old-hashes":
            # Simulated failure during rollback's restore of hashes_path.
            raise OSError("rollback-boom: simulated failure restoring hashes.json")
        real_atomic_write_bytes(path, data)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_round, "atomic_write_bytes", _flaky)
        with pytest.raises(run_round.ReportBundleRollbackError) as exc_info:
            run_round._publish_report_bundle(
                output_path=output_path,
                report_bytes=b'{"new": "report"}',
                hashes_path=hashes_path,
                hashes_bytes=b'{"new": "hashes"}',
            )

    assert "boom: simulated failure writing the report" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, OSError)
