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
import os
import sys
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
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
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
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
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
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )
    curr = _report(
        # regressed: preserved -> deviated. requirement matches prev's (F1
        # requires the pair to share a requirement); observed genuinely
        # differs (non-str-safe, non-contradictory: F2/the consistency gate
        # both accept a genuinely-deviated str pair).
        key=_axis(verdict="deviated", requirement="D minor", observed="C major"),
        brightness=_valid_brightness_axis(),
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
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="mismatch",
            band="not_observed",
            requirement=["intro", "chorus", "outro"],
            observed=[],
        ),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
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
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
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


# --- Codex review round 6, P1: pair-internal requirement identity gate (F1) --


def test_evaluate_rejects_differing_structure_requirement_between_pair():
    """F1 negative case: prev/curr judged against two different structure
    requirements (e.g. a `--section-map` swap between rounds) — the pair's
    distance is not comparable, regardless of what the two raw distances
    happen to be, so this must raise before any distance is computed."""
    prev = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="exact_match",
            # T2's 4-section canonical map — differs from prev's T1 3-section map.
            requirement=["intro", "chorus", "chorus", "outro"],
            observed=["intro", "chorus", "chorus", "outro"],
        ),
    )
    with pytest.raises(ValueError, match="requirement differs"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_rejects_differing_key_requirement_between_pair():
    """F1 negative case on a binary axis: prev/curr's `key` requirement
    (a string, not a list) also has to agree for the pair to be
    comparable."""
    prev = _report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    curr = _report(
        key=_axis(verdict="preserved", requirement="C minor", observed="C minor"),
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    with pytest.raises(ValueError, match="requirement differs"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


# --- Codex review round 5, P1: deviated-verdict/value consistency gate -----


def test_evaluate_rejects_contradictory_deviated_key_axis():
    """A `key` axis claiming `verdict='deviated'` while `requirement` and
    `observed` are actually the same string is an internally-contradictory
    report — `evaluate()` must refuse to trust the verdict over the values."""
    prev = _report(
        key=_axis(verdict="deviated", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
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
    with pytest.raises(ValueError, match="contradictory report"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_rejects_contradictory_deviated_key_axis_enharmonic():
    """Same contradiction, but `requirement`/`observed` are only equal under
    enharmonic equivalence (`C# minor` == `Db minor`), not plain string
    equality — the gate must still catch it (it uses
    `keys_enharmonically_equal`, the same comparator `AuthoringDiffReport`'s
    own `preserved` validator trusts)."""
    prev = _report(
        key=_axis(verdict="deviated", requirement="C# minor", observed="Db minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
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
    with pytest.raises(ValueError, match="contradictory report"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_rejects_contradictory_deviated_brightness_axis():
    """Same contradiction on the `brightness` axis (plain string equality,
    no enharmonic concept applies)."""
    prev = _report(
        key=_axis(verdict="preserved", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="deviated", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
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
    with pytest.raises(ValueError, match="contradictory report"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_accepts_genuinely_deviated_key_axis_at_distance_one():
    """Positive counterpart: a `deviated` `key` axis whose `requirement`/
    `observed` are genuinely different passes the consistency gate unchanged
    and still contributes distance 1 (no behavior change on the honest-
    failure happy path)."""
    prev = _report(
        key=_axis(verdict="deviated", requirement="D minor", observed="C major"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
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
    result = pareto_eval.evaluate(prev, curr, PARETO_SPEC)
    assert result["per_axis"]["key"]["prev_distance"] == 1
    assert result["per_axis"]["key"]["curr_distance"] == 0
    assert result["per_axis"]["key"]["strictly_improved"] is True
    assert result["improved"] is True


# --- Codex review round 6, P1: measured-binary-axis str gate (F2) ----------


def test_evaluate_rejects_null_requirement_observed_on_deviated_measured_axis():
    """F2 negative case (Codex's own example): a `band == 'measured'`,
    `verdict == 'deviated'` `key` axis with `requirement=None`/
    `observed=None` (`_axis()`'s own test-helper default) used to slip
    straight past `_reject_contradictory_deviated_verdict`'s old "both str"
    early return, unexamined — `_require_str_measured_binary_axis_values`
    now rejects it outright, before the consistency gate even runs."""
    prev = _report(
        key=_axis(verdict="deviated"),  # band="measured" default, requirement/observed=None
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    with pytest.raises(ValueError, match="must be a str"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_rejects_numeric_value_on_measured_binary_axis():
    """F2 negative case: a `band == 'measured'` `brightness` axis with a
    numeric (non-str) `observed` value must be rejected regardless of
    `verdict` — this one is a success verdict (`preserved`), which the old
    gate never even looked at."""
    prev = _report(
        key=_valid_key_axis(),
        brightness=_axis(verdict="preserved", requirement="dark", observed=1),
        structure=_valid_structure_axis(),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_valid_structure_axis(),
    )
    with pytest.raises(ValueError, match="must be a str"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_main_exits_nonzero_on_contradictory_deviated_verdict(tmp_path: Path):
    """`main()`-level negative case: the same contradiction, caught via the
    CLI's existing `ValueError` handling (stderr + exit 1, nothing written)."""
    prev = _report(
        key=_axis(verdict="deviated", requirement="D minor", observed="D minor"),
        brightness=_axis(verdict="preserved", requirement="dark", observed="dark"),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
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


def test_evaluate_rejects_pareto_spec_with_wrong_axis_set():
    bad_spec = {"axes": {"key": {}, "brightness": {}}}  # missing structure
    with pytest.raises(ValueError):
        pareto_eval.evaluate(_report(), _report(), bad_spec)


# --- D1 (PR #247 Codex review round 3, P2): structure tokens must be str, no str() coercion ---


def test_evaluate_rejects_int_token_in_structure_requirement():
    # curr's structure requirement deliberately matches prev's (F1's
    # pair-internal requirement-identity gate would otherwise fire first,
    # before the str-token check this test targets ever runs) — only
    # `observed` differs between the two sides.
    prev = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="mismatch",  # failure side: AuthoringDiffReport allows Any here
            requirement=["intro", 2, "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="exact_match",
            requirement=["intro", 2, "outro"],
            observed=["intro", "chorus", "outro"],
        ),
    )
    with pytest.raises(ValueError, match="must be a str token"):
        pareto_eval.evaluate(prev, curr, PARETO_SPEC)


def test_evaluate_rejects_none_token_in_structure_observed():
    prev = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", None, "outro"],
        ),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
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
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
        structure=_axis(
            verdict="mismatch",
            requirement=["intro", "chorus", "outro"],
            observed=["intro", "verse", "chorus", "outro"],
        ),
    )
    curr = _report(
        key=_valid_key_axis(),
        brightness=_valid_brightness_axis(),
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
    `main()` the same way other `ValueError`s are (stderr + exit 1). curr's
    structure `requirement` deliberately matches prev's malformed one (F1's
    pair-internal requirement-identity gate would otherwise fire first)."""
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
            requirement=["intro", 2, "outro"],
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


# --- Codex review round 6, P2: axes[*].distance_definition pinned (F3) -----


def test_validate_pareto_spec_contract_rejects_reworded_distance_definition():
    bad_axes = {
        **PARETO_SPEC["axes"],
        "structure": {
            **PARETO_SPEC["axes"]["structure"],
            "distance_definition": PARETO_SPEC["axes"]["structure"]["distance_definition"] + " ",
        },
    }
    bad_spec = {**PARETO_SPEC, "axes": bad_axes}
    with pytest.raises(ValueError, match="distance_definition"):
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


# --- Codex review round 5, P1: reserved-path symlink-escape guard ----------


def test_reject_escaping_reserved_paths_rejects_symlinked_score_copy(tmp_path: Path):
    """`score.yaml`'s reserved slot is a symlink pointing at a real file
    outside --workdir — writing through it would truncate/overwrite that
    outside file. Must be refused before anything is written."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    outside_target = tmp_path / "outside_evidence.yaml"
    outside_target.write_text("precious pinned evidence\n")
    paths = run_round._reserved_workdir_paths(workdir)
    paths["score_copy"].symlink_to(outside_target)

    with pytest.raises(run_round.ProtectedPathError, match="symlink"):
        run_round._reject_escaping_reserved_paths(workdir, paths)
    # The outside file must be untouched by the (refused) check itself.
    assert outside_target.read_text() == "precious pinned evidence\n"


def test_reject_escaping_reserved_paths_rejects_symlinked_identity_dir(tmp_path: Path):
    """`identity/` (a reserved *directory*, not just a file) is a symlink
    pointing outside --workdir — must be refused the same way a symlinked
    file entry is."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    paths["identity_dir"].symlink_to(outside_dir, target_is_directory=True)

    with pytest.raises(run_round.ProtectedPathError, match="symlink"):
        run_round._reject_escaping_reserved_paths(workdir, paths)


def test_reject_escaping_reserved_paths_accepts_ordinary_reuse(tmp_path: Path):
    """Ordinary reused --workdir: real leftover files/dirs from a prior
    round, no symlinks anywhere — must not raise."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    paths["score_copy"].write_text("schema_version: composition-score/0.1\n")
    paths["identity_dir"].mkdir()
    paths["identity_section_map"].write_text('{"sections": []}\n')

    # Must not raise.
    run_round._reject_escaping_reserved_paths(workdir, paths)


def test_reject_escaping_reserved_paths_accepts_fresh_workdir(tmp_path: Path):
    """First-run shape: --workdir exists but none of the reserved paths do
    yet — nothing to escape through, must not raise."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)

    # Must not raise.
    run_round._reject_escaping_reserved_paths(workdir, paths)


# --- Codex review round 6, P1: reserved-path writes are atomic (hardlink-safe, F4) --


def test_atomic_write_bytes_hardlink_does_not_corrupt_shared_inode(tmp_path: Path):
    """F4 positive case: a reserved workdir write target (e.g. `score.yaml`)
    that is a *hard* link to some other pin-recorded file (sharing its inode
    — indistinguishable from an ordinary file to `Path.is_symlink()`, so the
    symlink-escape guard above cannot catch this case) must not have that
    other file's content corrupted when this run writes fresh bytes through
    the reserved name. `run_round.py`'s reserved-path writes all go through
    `atomic_write_bytes` (tempfile + `os.replace`), which unlinks the
    reserved name from whatever inode it previously shared and rebinds it to
    a brand-new inode — the old inode, and whatever other path still points
    at it, is left untouched."""
    original = tmp_path / "original_evidence.yaml"
    original.write_text("precious pinned evidence\n", encoding="utf-8")
    reserved = tmp_path / "score.yaml"
    os.link(original, reserved)
    # Sanity: the two paths really do share one inode before the write.
    assert reserved.read_text() == original.read_text()

    run_round.atomic_write_bytes(reserved, b"freshly staged round content\n")

    assert reserved.read_bytes() == b"freshly staged round content\n"
    assert original.read_text() == "precious pinned evidence\n"


# --- Codex review round 9, P1: subprocess-output staged atomic publish (H1) --


def test_run_round_validation_staged_publish_hardlink_safe(tmp_path: Path):
    """H1 positive case (module docstring's "H1" section): reserved
    `validation.json` is a *hard* link to some other pin-recorded file when
    a reused `--workdir` carries it over from a prior round — reached
    cheaply via the symbolic-fail early-return path (an invalid
    `score.yaml` never reaches the audio pipeline, same fixture
    `test_run_round_report_bundle_rolls_back_pre_existing_bytes_on_symbolic_fail`
    above uses). `svprpe validate`'s own `-o` write now lands on a staging
    path under `<workdir>/subproc_staging/`, and only the staged bytes —
    read back after the subprocess exits — are republished to the reserved
    `validation.json` name via `atomic_write_bytes`, so the shared inode's
    other owner must survive completely untouched, the same guarantee F4's
    `test_atomic_write_bytes_hardlink_does_not_corrupt_shared_inode` already
    gives this module's own direct writes."""
    bad_score_path = tmp_path / "bad_score.yaml"
    bad_score_path.write_text("not: a valid composition score\n", encoding="utf-8")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    output_path = tmp_path / "report.json"

    original = tmp_path / "original_evidence.json"
    original.write_text('{"pinned": "evidence"}', encoding="utf-8")
    reserved_validation = workdir / "validation.json"
    os.link(original, reserved_validation)
    # Sanity: the two paths really do share one inode before the run.
    assert reserved_validation.read_text() == original.read_text()

    result = run_round.run_round(bad_score_path, workdir, 0, output_path)

    assert result["report"].symbolic_validation.status == "fail"
    # The reserved name now holds this run's freshly staged validation
    # result...
    assert reserved_validation.read_text() != '{"pinned": "evidence"}'
    # ...and the other owner of the formerly shared inode is untouched.
    assert original.read_text() == '{"pinned": "evidence"}'


# --- Codex review round 17, P1: subprocess bound to this interpreter (J1) --


def test_svprpe_cmd_binds_to_current_interpreter_not_bare_path_lookup():
    """`_svprpe_cmd` must build `[sys.executable, "-m", "svp_rpe.cli", ...]`
    — never a bare `"svprpe"` console-script name resolved via `PATH`, which
    could pick up a stale script installed for a different
    interpreter/environment (module docstring's "J1" section)."""
    cmd = run_round._svprpe_cmd(["validate", "score.yaml", "--contract", "contract.yaml"])
    assert cmd == [sys.executable, "-m", "svp_rpe.cli", "validate", "score.yaml", "--contract", "contract.yaml"]
    assert "svprpe" not in cmd


def test_run_cli_and_run_validate_cli_invoke_svprpe_cmd(monkeypatch, tmp_path: Path):
    """`_run_cli`/`_run_validate_cli` must themselves route through
    `_svprpe_cmd` rather than constructing their own `["svprpe", ...]`
    command list — captures the exact `args` passed to `subprocess.run` and
    asserts its first element is `sys.executable`, not the bare string
    `"svprpe"`."""
    captured: list[list[str]] = []

    class _FakeCompletedProcess:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = ""
            self.stderr = ""

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _FakeCompletedProcess()

    monkeypatch.setattr(run_round.subprocess, "run", _fake_run)

    run_round._run_cli(["package", "score.yaml"])
    staging_path = tmp_path / "staged_validation.json"
    staging_path.write_text('{"status": "pass"}', encoding="utf-8")
    dest_path = tmp_path / "validation.json"
    run_round._run_validate_cli(Path("score.yaml"), staging_path, dest_path, Path("contract.yaml"))

    assert len(captured) == 2
    for cmd in captured:
        assert cmd[0] == sys.executable
        assert cmd[1] == "-m"
        assert cmd[2] == "svp_rpe.cli"
        assert "svprpe" not in cmd


# --- Codex review round 19, P1: import engine checkout containment (K1) ----


def test_reject_engine_outside_checkout_accepts_current_checkout():
    """Positive case: this test environment's `svp_rpe` is an editable
    install of *this* checkout (`svp_rpe.__file__` resolves under
    `<repo_root>/src/svp_rpe/...`), so the real, unpatched containment check
    must not raise."""
    # Must not raise.
    run_round._reject_engine_outside_checkout()
    # Sanity: the real environment's svp_rpe really is this checkout's own
    # src/ tree, not some other install — otherwise this test would be
    # vacuously passing.
    assert Path(run_round.svp_rpe.__file__).resolve().is_relative_to(
        run_round._SRC_ROOT.resolve()
    )


def test_reject_engine_outside_checkout_rejects_out_of_tree_engine_file(tmp_path: Path):
    """Negative case: a `svp_rpe.__file__` resolving outside this checkout's
    `src/` (e.g. a different checkout's editable install, a stale wheel, or
    a site-packages copy) must be refused. `engine_file` is the check
    function's own parameterization (module docstring's "K1" section) —
    substitutes for monkeypatching the real `svp_rpe.__file__` module
    attribute, which would leak into every other test sharing the same
    imported module object."""
    fake_engine_dir = tmp_path / "other_checkout" / "src" / "svp_rpe"
    fake_engine_dir.mkdir(parents=True)
    fake_engine_file = fake_engine_dir / "__init__.py"
    fake_engine_file.write_text("", encoding="utf-8")

    with pytest.raises(
        run_round.EngineCheckoutContainmentError,
        match="imported engine is not this checkout's pinned source",
    ):
        run_round._reject_engine_outside_checkout(str(fake_engine_file))


def test_reject_engine_outside_checkout_is_a_protected_path_error(tmp_path: Path):
    """`EngineCheckoutContainmentError` must subclass `ProtectedPathError` —
    `main()`'s existing `except ProtectedPathError` handling must catch it
    unchanged, same as every other preflight guard's error."""
    assert issubclass(run_round.EngineCheckoutContainmentError, run_round.ProtectedPathError)


def test_run_round_rejects_engine_outside_checkout_before_any_write(
    monkeypatch, tmp_path: Path
):
    """K1 runs first in `run_round()`'s preflight chain, before
    `workdir.mkdir()` or any other write — monkeypatches
    `_reject_engine_outside_checkout` to simulate an out-of-tree engine and
    asserts the rejection happens before `--workdir` is ever created."""

    def _fake_reject() -> None:
        raise run_round.EngineCheckoutContainmentError("simulated engine containment failure")

    monkeypatch.setattr(run_round, "_reject_engine_outside_checkout", _fake_reject)

    workdir = tmp_path / "wd"
    output_path = tmp_path / "report.json"
    score_path = tmp_path / "score.yaml"
    score_path.write_text("schema_version: composition-score/0.1\n", encoding="utf-8")

    with pytest.raises(run_round.EngineCheckoutContainmentError):
        run_round.run_round(score_path, workdir, 0, output_path)

    assert not workdir.exists()
    assert not output_path.exists()


def test_reserved_workdir_paths_includes_subproc_staging_paths(tmp_path: Path):
    """The H1 staging paths every subprocess CLI output is redirected to
    before being republished to its real reserved path (module docstring's
    "H1" section) are ordinary entries in `_reserved_workdir_paths`, so the
    existing output-collision guard and reserved-path symlink-escape guard
    protect them automatically — the same pattern
    `test_reserved_workdir_paths_includes_judge_inputs_copies` below already
    pins for the G2 copies."""
    workdir = tmp_path / "wd"
    paths = run_round._reserved_workdir_paths(workdir)
    staging_dir = workdir / "subproc_staging"
    assert paths["subproc_staging_dir"] == staging_dir
    assert paths["subproc_staging_validation"] == staging_dir / "validation.json"
    assert paths["subproc_staging_roundtrip"] == staging_dir / "roundtrip.json"
    assert paths["subproc_staging_adherence"] == staging_dir / "adherence.json"
    assert paths["subproc_staging_observe_report"] == staging_dir / "observe_report.json"
    assert paths["subproc_staging_package_dir"] == staging_dir / "package"


# --- Codex review round 6, P1: preflight judge-input drift check (F5) ------


def test_fixed_input_sha256_matches_actual_files():
    """F5 drift-of-drift meta-test: `_FIXED_INPUT_SHA256`'s pinned digests
    must match the actual current bytes of every file it names — this would
    fail if either the pin table or the underlying frozen/config file
    drifted from the other without updating the table (the same drift
    `_reject_judge_input_drift` itself exists to catch at run time,
    mirrored here as a static check)."""
    for label, (path, expected_sha256) in run_round._FIXED_INPUT_SHA256.items():
        assert run_round._sha256_file(path) == expected_sha256, label


def test_fixed_input_sha256_key_set_is_fixed():
    assert set(run_round._FIXED_INPUT_SHA256) == {
        "section_map",
        "section_map_t2",
        "eval_control_profile",
        "arrangement",
        "suno_capability_profile",
        "authoring_contract_l0",
    }


def test_reject_judge_input_drift_accepts_pinned_files_for_t1_and_t2():
    # Must not raise, for either --section-map selection.
    run_round._reject_judge_input_drift(run_round.SECTION_MAP_PATH)
    run_round._reject_judge_input_drift(run_round.SECTION_MAP_T2_PATH)


def test_reject_judge_input_drift_rejects_modified_fixed_input(tmp_path: Path, monkeypatch):
    """F5 negative case: a fixed input whose on-disk bytes no longer match
    its pin must be refused with `JudgeInputDriftError` — exercised via a
    monkeypatched `_FIXED_INPUT_SHA256` entry pointed at a tampered tmp copy
    (cheaper than mutating a real frozen/config file in place)."""
    tampered = tmp_path / "eval_control_profile.yaml"
    tampered.write_text("tampered: true\n", encoding="utf-8")
    patched = dict(run_round._FIXED_INPUT_SHA256)
    patched["eval_control_profile"] = (
        tampered,
        run_round._FIXED_INPUT_SHA256["eval_control_profile"][1],  # stale pin, on purpose
    )
    monkeypatch.setattr(run_round, "_FIXED_INPUT_SHA256", patched)

    with pytest.raises(run_round.JudgeInputDriftError, match="eval_control_profile"):
        run_round._reject_judge_input_drift(run_round.SECTION_MAP_PATH)


# --- Codex review round 7, P1: G1 unpinned --section-map rejection ---------


def test_reject_unpinned_section_map_accepts_pinned_paths():
    # Must not raise, for either pinned path — positive counterpart to the
    # negative case below. Full-pipeline positive coverage (a real run
    # reaching completion with each pinned map) is already provided by
    # `test_positive_control_round_trip_reproduces_pinned_report` (T1,
    # default `--section-map`) and
    # `test_positive_control_t2_round_trip_reproduces_pinned_report` (T2,
    # `--section-map frozen/section_map_t2.json`) below.
    run_round._reject_unpinned_section_map(run_round.SECTION_MAP_PATH)
    run_round._reject_unpinned_section_map(run_round.SECTION_MAP_T2_PATH)


def test_reject_unpinned_section_map_rejects_byte_identical_copy_at_unpinned_path(
    tmp_path: Path,
):
    """G1 negative case: a `--section-map` that is a byte-identical *copy*
    of the pinned T1 map, but sitting at a different (unpinned) path, must
    still be refused — pin membership is a path-identity check, not a
    content check (F5's hash table only ever pins the two canonical paths;
    without G1, this exact case used to fall out of
    `_selected_fixed_inputs`'s selection and skip drift protection
    entirely, per the module docstring's "G1" section)."""
    unpinned_copy = tmp_path / "section_map.json"
    unpinned_copy.write_bytes(run_round.SECTION_MAP_PATH.read_bytes())

    with pytest.raises(
        run_round.JudgeInputDriftError, match="not one of the pinned section maps"
    ):
        run_round._reject_unpinned_section_map(unpinned_copy)


def test_run_round_rejects_unpinned_section_map_before_any_write(tmp_path: Path):
    """G1 negative case at the `run_round()` entry point: an unpinned
    `--section-map` is refused before `workdir.mkdir()` or any other write
    — cheap to assert because the guard fires immediately, before the
    (expensive) audio/subprocess pipeline ever starts, so this test never
    pays for it."""
    unpinned_copy = tmp_path / "section_map_copy.json"
    unpinned_copy.write_bytes(run_round.SECTION_MAP_PATH.read_bytes())
    workdir = tmp_path / "wd"
    output_path = tmp_path / "report.json"

    with pytest.raises(run_round.JudgeInputDriftError):
        run_round.run_round(
            POSITIVE_CONTROL_SCORE_PATH,
            workdir,
            0,
            output_path,
            section_map_path=unpinned_copy,
        )
    assert not workdir.exists()
    assert not output_path.exists()


# --- Codex review round 7, P1: G2 judge-input snapshot-feed ----------------


def test_reject_judge_input_drift_returns_verified_bytes():
    """`_reject_judge_input_drift` now returns the exact bytes it read and
    hashed for each fixed input (G2), not just `None` — this is the
    snapshot `run_round()` threads to every downstream consumer instead of
    a fresh re-read."""
    verified = run_round._reject_judge_input_drift(run_round.SECTION_MAP_PATH)
    assert verified["authoring_contract_l0"] == run_round.CONTRACT_PATH.read_bytes()
    assert verified["arrangement"] == run_round.ARRANGEMENT_PATH.read_bytes()
    assert (
        verified["suno_capability_profile"] == run_round.CAPABILITY_PROFILE_PATH.read_bytes()
    )
    assert (
        verified["eval_control_profile"] == run_round.EVAL_CONTROL_PROFILE_PATH.read_bytes()
    )
    assert verified["section_map"] == run_round.SECTION_MAP_PATH.read_bytes()
    assert "section_map_t2" not in verified


def test_verified_judge_input_bytes_survive_source_mutation_after_preflight(
    tmp_path: Path, monkeypatch
):
    """G2 snapshot-feed unit check (cheap — no full pipeline run): once
    `_reject_judge_input_drift` has read+verified a fixed input's bytes,
    mutating the *source* path afterward must not affect a workdir
    `judge_inputs/` copy staged from that already-verified snapshot — this
    exercises the same `atomic_write_bytes(paths["judge_contract"], ...)`
    staging step `run_round()` itself performs, without paying for the full
    subprocess pipeline. Before G2, the consumer of this input (the
    `svprpe validate --contract` subprocess) re-read the source path itself,
    arbitrarily long after preflight verified it — a verify-then-reread
    TOCTOU window this test proves is now closed."""
    tampered_source = tmp_path / "authoring_contract_l0.yaml"
    original_bytes = b"schema_version: contract/0\nkind: original\n"
    tampered_source.write_bytes(original_bytes)
    patched = dict(run_round._FIXED_INPUT_SHA256)
    patched["authoring_contract_l0"] = (
        tampered_source,
        run_round._sha256_bytes(original_bytes),
    )
    monkeypatch.setattr(run_round, "_FIXED_INPUT_SHA256", patched)

    verified = run_round._reject_judge_input_drift(run_round.SECTION_MAP_PATH)
    assert verified["authoring_contract_l0"] == original_bytes

    # Mirrors run_round()'s own staging write: the reserved judge_inputs/
    # copy is written from the verified bytes, not from a fresh read of
    # tampered_source.
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    run_round.atomic_write_bytes(paths["judge_contract"], verified["authoring_contract_l0"])

    # Mutate the source *after* verification+staging — a TOCTOU re-read
    # would now observe this; the already-staged copy must not.
    tampered_source.write_bytes(b"schema_version: contract/0\nkind: TAMPERED\n")

    assert paths["judge_contract"].read_bytes() == original_bytes
    assert paths["judge_contract"].read_bytes() != tampered_source.read_bytes()


def test_prepare_scores_does_not_reread_eval_control_profile_path(
    tmp_path: Path, monkeypatch
):
    """G2: `_prepare_scores` must derive `control_profile` from the
    `eval_control_profile_bytes` argument, not re-read
    `EVAL_CONTROL_PROFILE_PATH` off disk — monkeypatching that module
    global to a nonexistent path proves it (a re-read would raise
    `FileNotFoundError`)."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    score_bytes = POSITIVE_CONTROL_SCORE_PATH.read_bytes()
    eval_control_profile_bytes = run_round.EVAL_CONTROL_PROFILE_PATH.read_bytes()
    monkeypatch.setattr(
        run_round, "EVAL_CONTROL_PROFILE_PATH", tmp_path / "does-not-exist.yaml"
    )

    eval_score_path, _ = run_round._prepare_scores(
        POSITIVE_CONTROL_SCORE_PATH, score_bytes, paths, eval_control_profile_bytes
    )
    eval_data = yaml.safe_load(eval_score_path.read_text(encoding="utf-8"))
    assert "control_profile" in eval_data


def test_reserved_workdir_paths_includes_judge_inputs_copies(tmp_path: Path):
    """The three new G2 staging copies are ordinary entries in
    `_reserved_workdir_paths`, so the existing output-collision guard and
    reserved-path symlink-escape guard protect them automatically (module
    docstring's "G2" section) — this pins their presence and location."""
    workdir = tmp_path / "wd"
    paths = run_round._reserved_workdir_paths(workdir)
    assert paths["judge_inputs_dir"] == workdir / "judge_inputs"
    assert paths["judge_contract"] == workdir / "judge_inputs" / "authoring_contract_l0.yaml"
    assert paths["judge_arrangement"] == workdir / "judge_inputs" / "arrangement.yaml"
    assert (
        paths["judge_capability_profile"]
        == workdir / "judge_inputs" / "capability_profile.yaml"
    )


# --- PR #247 Codex review round 23, P1: L1 pre-publish judge-input copy ---
# --- re-verification -------------------------------------------------------


def _staged_verified_inputs(paths: dict) -> dict:
    """Stages `judge_contract`/`judge_arrangement`/`judge_capability_profile`
    from the real pinned fixed inputs' current bytes (mirrors `run_round()`'s
    own G2 staging step) and returns a `verified_inputs`-shaped dict the
    tests below can hand to `_reject_judge_input_copy_drift` directly,
    without paying for a full `_reject_judge_input_drift()` preflight call."""
    verified_inputs = {
        "authoring_contract_l0": run_round.CONTRACT_PATH.read_bytes(),
        "arrangement": run_round.ARRANGEMENT_PATH.read_bytes(),
        "suno_capability_profile": run_round.CAPABILITY_PROFILE_PATH.read_bytes(),
    }
    run_round.atomic_write_bytes(paths["judge_contract"], verified_inputs["authoring_contract_l0"])
    run_round.atomic_write_bytes(paths["judge_arrangement"], verified_inputs["arrangement"])
    run_round.atomic_write_bytes(
        paths["judge_capability_profile"], verified_inputs["suno_capability_profile"]
    )
    return verified_inputs


def test_judge_input_copy_drift_error_is_a_judge_input_drift_error():
    """`JudgeInputCopyDriftError` must subclass `JudgeInputDriftError` (in
    turn a `ProtectedPathError`) — module docstring's "L1" section — so
    `main()`'s existing `except ProtectedPathError` handling catches it
    unchanged, same as every other preflight/pre-publish guard's error."""
    assert issubclass(run_round.JudgeInputCopyDriftError, run_round.JudgeInputDriftError)
    assert issubclass(run_round.JudgeInputCopyDriftError, run_round.ProtectedPathError)


def test_reject_judge_input_copy_drift_accepts_untampered_copies(tmp_path: Path):
    """Positive case, cheap helper-level check: copies staged straight from
    `verified_inputs` and never touched afterward must not raise."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    verified_inputs = _staged_verified_inputs(paths)

    run_round._reject_judge_input_copy_drift(paths, verified_inputs)  # must not raise


def test_reject_judge_input_copy_drift_rejects_mutated_copy(tmp_path: Path):
    """L1 negative case: a `judge_inputs/*` copy rewritten after staging (the
    "another process mutates the copy mid-run" scenario the module docstring's
    "L1" section describes) must be refused with `JudgeInputCopyDriftError`
    naming the drifted copy's label, before any publish happens."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    verified_inputs = _staged_verified_inputs(paths)

    # Simulate a second process rewriting the staged arrangement copy after
    # G2 staged it (but before this run's report bundle is published).
    paths["judge_arrangement"].write_bytes(b"schema_version: arrangement/0.1\nkind: tampered\n")

    with pytest.raises(run_round.JudgeInputCopyDriftError, match="arrangement"):
        run_round._reject_judge_input_copy_drift(paths, verified_inputs)


def test_reject_judge_input_copy_drift_rejects_missing_copy(tmp_path: Path):
    """L1 negative case: a `judge_inputs/*` copy *deleted* mid-run (not just
    rewritten) must also be refused, not silently skipped."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    verified_inputs = _staged_verified_inputs(paths)

    paths["judge_capability_profile"].unlink()

    with pytest.raises(run_round.JudgeInputCopyDriftError, match="suno_capability_profile"):
        run_round._reject_judge_input_copy_drift(paths, verified_inputs)


def test_run_round_rejects_judge_input_copy_drift_before_publish_on_symbolic_fail_path(
    tmp_path: Path, monkeypatch
):
    """L1 wiring proof, exercised through the real `run_round()` symbolic-fail
    branch (cheap — an invalid `score.yaml` never reaches the audio pipeline,
    same fixture the H1 hard-link test above uses): a `judge_inputs/*` copy
    mutated *mid-run* — here, immediately after `svprpe validate` has already
    consumed it, simulating a second process rewriting it before this run's
    report bundle is published — must make `run_round()` raise
    `JudgeInputCopyDriftError` and must leave no report/hashes published (the
    pre-publish check runs and fires before `_publish_report_bundle` is ever
    called, module docstring's "L1" section). This is a real end-to-end
    `run_round()` call, not a monkeypatched stand-in for the guard itself —
    only the tampering trigger (writing extra bytes right after the real
    `_run_validate_cli` call that would otherwise run unmodified) is
    injected, via a thin wrapper around the real function rather than a fake
    replacement, so this run's `symbolic_validation` result is still
    genuinely produced by the real subprocess.

    G2 stages all three `judge_inputs/*` copies unconditionally before the
    symbolic gate is even reached, so — contrary to a "the symbolic-fail
    branch returns before judge_inputs is written, and is therefore exempt"
    assumption — this branch is not exempt from the drift window L1 closes;
    this test's fixture reaches the guard by construction (module docstring's
    "L1" section, "Applied at *both* publish sites unconditionally" clause)."""
    bad_score_path = tmp_path / "bad_score.yaml"
    bad_score_path.write_text("not: a valid composition score\n", encoding="utf-8")
    workdir = tmp_path / "wd"
    output_path = tmp_path / "report.json"

    real_run_validate_cli = run_round._run_validate_cli

    def _run_validate_cli_then_tamper(score_copy_path, staging_path, dest_path, contract_path):
        real_run_validate_cli(score_copy_path, staging_path, dest_path, contract_path)
        # Simulated mid-run tamper of the staged judge_inputs/ copy, after
        # this run's own `svprpe validate` subprocess already consumed it.
        contract_path.write_bytes(contract_path.read_bytes() + b"\ntampered: true\n")

    monkeypatch.setattr(run_round, "_run_validate_cli", _run_validate_cli_then_tamper)

    with pytest.raises(run_round.JudgeInputCopyDriftError, match="authoring_contract_l0"):
        run_round.run_round(bad_score_path, workdir, 0, output_path)

    assert not output_path.exists()
    assert not (workdir / "hashes.json").exists()


# --- PR #247 Codex review round 25, P1: M1 pre-publish recorded-artifact ---
# --- re-verification (family terminus) --------------------------------------


def _staged_recorded_artifacts(paths: dict) -> dict[str, str]:
    """Writes deterministic stub bytes to every workdir path
    `_RECORDED_ARTIFACT_PATH_KEYS` names (mirrors `_staged_verified_inputs`'s
    role for the L1 tests above) and returns the `hashes`-shaped dict that
    matches those bytes — the tests below can hand this straight to
    `_reject_recorded_artifact_drift` without running the real pipeline."""
    paths["package_dir"].mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for hash_key, path_key in run_round._RECORDED_ARTIFACT_PATH_KEYS.items():
        artifact_path = paths[path_key]
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        content = f"{hash_key}-stub-bytes\n".encode("utf-8")
        artifact_path.write_bytes(content)
        hashes[hash_key] = run_round._sha256_bytes(content)
    return hashes


def test_recorded_artifact_drift_error_is_a_judge_input_drift_error():
    """`RecordedArtifactDriftError` must subclass `JudgeInputDriftError` (in
    turn a `ProtectedPathError`) — module docstring's "M1" section — so
    `main()`'s existing `except ProtectedPathError` handling catches it
    unchanged, same as every other preflight/pre-publish guard's error."""
    assert issubclass(run_round.RecordedArtifactDriftError, run_round.JudgeInputDriftError)
    assert issubclass(run_round.RecordedArtifactDriftError, run_round.ProtectedPathError)


def test_recorded_artifact_path_keys_cover_every_hashes_key_run_round_sets(tmp_path: Path):
    """Regression guard for the key -> path table's exhaustiveness: every key
    `run_round()` is documented to set into `hashes` (module docstring's "M1"
    section) must be covered by exactly one of `_RECORDED_ARTIFACT_PATH_KEYS`
    (has a real workdir artifact to re-verify) or
    `_RECORDED_ARTIFACT_EXCLUDED_KEYS` (deliberately exempt, each for its own
    documented reason) — and the two tables must not overlap. A future
    artifact key added to `hashes` without updating one of these tables is
    also caught at *runtime* by `_reject_recorded_artifact_drift`'s
    fail-closed unknown-key branch (see
    `test_reject_recorded_artifact_drift_rejects_unknown_key` below); this
    test instead pins the *current*, complete key set statically, so a silent
    divergence between run_round()'s actual hashes keys and these tables
    shows up here first, without needing a full pipeline run to trigger it."""
    known_hashes_keys = {
        "score",
        "validation",
        "report",
        "eval_score",
        "roundtrip",
        "adherence",
        "take_wav",
        "manifest",
        "package",
        "package_compilation_report",
        "observe_report",
    }
    path_keys = set(run_round._RECORDED_ARTIFACT_PATH_KEYS)
    excluded_keys = run_round._RECORDED_ARTIFACT_EXCLUDED_KEYS
    assert path_keys.isdisjoint(excluded_keys)
    assert path_keys | excluded_keys == known_hashes_keys

    # Every path-table value must resolve to a real `_reserved_workdir_paths`
    # entry, so `_reject_recorded_artifact_drift`'s `paths[path_key]` lookup
    # can never itself KeyError on a typo'd target.
    reserved_keys = set(run_round._reserved_workdir_paths(tmp_path / "wd"))
    path_key_targets = set(run_round._RECORDED_ARTIFACT_PATH_KEYS.values())
    assert path_key_targets <= reserved_keys


def test_reject_recorded_artifact_drift_accepts_untampered_artifacts(tmp_path: Path):
    """Positive case, cheap helper-level check: artifacts staged straight
    from the bytes `hashes` was computed from, and never touched afterward,
    must not raise."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    hashes = _staged_recorded_artifacts(paths)

    run_round._reject_recorded_artifact_drift(paths, hashes)  # must not raise


def test_reject_recorded_artifact_drift_skips_excluded_keys(tmp_path: Path):
    """`score`/`report` must be skipped even though this test never wrote a
    workdir artifact under either key (`score` has no independently
    generated workdir artifact of its own — `score_copy` is a passthrough of
    the same snapshot bytes; `report` isn't written to disk until after this
    guard passes) — their presence in `hashes` must not be treated as
    something to re-verify, and their absence from `paths`/disk under those
    keys must not raise either."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    hashes = _staged_recorded_artifacts(paths)
    hashes["score"] = "deadbeef"
    hashes["report"] = "deadbeef"

    run_round._reject_recorded_artifact_drift(paths, hashes)  # must not raise


def test_reject_recorded_artifact_drift_rejects_mutated_take_wav(tmp_path: Path):
    """M1 negative case (the take.wav scenario the task brief calls out
    directly): `take.wav` rewritten after `hashes["take_wav"]` was recorded
    from it (the "another process mutates a consumed artifact mid-run"
    scenario the module docstring's "M1" section describes) must be refused
    with `RecordedArtifactDriftError` naming the drifted key, before any
    publish happens."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    hashes = _staged_recorded_artifacts(paths)

    # Simulate a second process rewriting take.wav after this run's own
    # subprocess chain already consumed it (svprpe observe, then
    # _structure_axis's own extract_rpe_from_file call) and hashed it.
    paths["take_wav"].write_bytes(b"RIFF-tampered-not-the-recorded-audio")

    with pytest.raises(run_round.RecordedArtifactDriftError, match="take_wav"):
        run_round._reject_recorded_artifact_drift(paths, hashes)


def test_reject_recorded_artifact_drift_rejects_missing_artifact(tmp_path: Path):
    """M1 negative case: a recorded artifact *deleted* mid-run (not just
    rewritten) must also be refused, not silently skipped."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    hashes = _staged_recorded_artifacts(paths)

    paths["observe_report"].unlink()

    with pytest.raises(run_round.RecordedArtifactDriftError, match="observe_report"):
        run_round._reject_recorded_artifact_drift(paths, hashes)


def test_reject_recorded_artifact_drift_rejects_unknown_key(tmp_path: Path):
    """Fail-closed coverage guard: a `hashes` key present in neither
    `_RECORDED_ARTIFACT_PATH_KEYS` nor `_RECORDED_ARTIFACT_EXCLUDED_KEYS`
    must itself be refused, naming the unrecognized key — a future artifact
    added to `hashes` without a matching table entry must not silently skip
    re-verification (module docstring's "M1" section)."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    hashes = _staged_recorded_artifacts(paths)
    hashes["mystery_future_artifact"] = "deadbeef"

    with pytest.raises(run_round.RecordedArtifactDriftError, match="mystery_future_artifact"):
        run_round._reject_recorded_artifact_drift(paths, hashes)


def test_run_round_rejects_recorded_artifact_drift_before_publish_on_symbolic_fail_path(
    tmp_path: Path, monkeypatch
):
    """M1 wiring proof, exercised through the real `run_round()` symbolic-fail
    branch (cheap — an invalid `score.yaml` never reaches the audio pipeline,
    same fixture the L1 wiring test above uses): `validation.json` mutated
    *mid-run* — here, immediately after L1's own guard has already run,
    simulating a second process rewriting it in the narrow window between L1
    and M1 — must make `run_round()` raise `RecordedArtifactDriftError` and
    must leave no report/hashes published (the pre-publish check runs and
    fires before `_publish_report_bundle` is ever called, module docstring's
    "M1" section)."""
    bad_score_path = tmp_path / "bad_score.yaml"
    bad_score_path.write_text("not: a valid composition score\n", encoding="utf-8")
    workdir = tmp_path / "wd"
    output_path = tmp_path / "report.json"

    real_reject_judge_input_copy_drift = run_round._reject_judge_input_copy_drift

    def _reject_judge_input_copy_drift_then_tamper(paths, verified_inputs):
        real_reject_judge_input_copy_drift(paths, verified_inputs)
        # Simulated mid-run tamper of validation.json, after
        # hashes["validation"] was already recorded from it and after L1's
        # own guard ran clean, but before M1's guard gets a chance to catch
        # it.
        paths["validation"].write_bytes(
            paths["validation"].read_bytes() + b"\ntampered mid-run\n"
        )

    monkeypatch.setattr(
        run_round, "_reject_judge_input_copy_drift", _reject_judge_input_copy_drift_then_tamper
    )

    with pytest.raises(run_round.RecordedArtifactDriftError, match="validation"):
        run_round.run_round(bad_score_path, workdir, 0, output_path)

    assert not output_path.exists()
    assert not (workdir / "hashes.json").exists()


@pytest.mark.slow
def test_run_round_rejects_recorded_artifact_drift_before_publish_on_success_path(
    tmp_path: Path, monkeypatch
):
    """M1 E2E proof through the real full-pipeline success branch, using the
    positive control fixture (same fixture the slow smoke test at the bottom
    of this file re-renders): `take.wav` rewritten *after* every consumer
    that reads it (`svprpe observe`, then `_structure_axis`'s own
    `extract_rpe_from_file` call — module docstring (c)) has already
    finished with it, but before this run's report bundle is published, must
    make `run_round()` raise `RecordedArtifactDriftError` naming `take_wav`,
    and must leave no report/hashes published. Wraps the real
    `_structure_axis` (the last consumer of `take_wav_path` in the pipeline)
    rather than faking it, so `structure_axis`'s own result is still
    genuinely produced by the real extraction — only the tampering trigger
    (writing extra bytes right after that call returns) is injected, mirrors
    the L1 slow-path wiring style above."""
    workdir = tmp_path / "wd"
    output_path = tmp_path / "report.json"

    real_structure_axis = run_round._structure_axis

    def _structure_axis_then_tamper(observe_report, take_wav_path, *, section_map_path, section_map_bytes=None):
        result = real_structure_axis(
            observe_report,
            take_wav_path,
            section_map_path=section_map_path,
            section_map_bytes=section_map_bytes,
        )
        # Simulated mid-run tamper of take.wav, after every real consumer in
        # this pipeline has already finished reading it.
        take_wav_path.write_bytes(b"RIFF-tampered-after-every-consumer-finished")
        return result

    monkeypatch.setattr(run_round, "_structure_axis", _structure_axis_then_tamper)

    with pytest.raises(run_round.RecordedArtifactDriftError, match="take_wav"):
        run_round.run_round(POSITIVE_CONTROL_SCORE_PATH, workdir, 0, output_path)

    assert not output_path.exists()
    assert not (workdir / "hashes.json").exists()


def test_reject_escaping_reserved_paths_rejects_symlinked_judge_contract(tmp_path: Path):
    """The reserved-path symlink-escape guard covers the new G2 copies the
    same way it covers every other reserved name — a symlinked
    `judge_inputs/authoring_contract_l0.yaml` pointing outside `--workdir`
    must be refused before anything is written."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    outside_target = tmp_path / "outside_contract.yaml"
    outside_target.write_text("precious pinned contract\n")
    paths = run_round._reserved_workdir_paths(workdir)
    paths["judge_inputs_dir"].mkdir()
    paths["judge_contract"].symlink_to(outside_target)

    with pytest.raises(run_round.ProtectedPathError, match="symlink"):
        run_round._reject_escaping_reserved_paths(workdir, paths)
    assert outside_target.read_text() == "precious pinned contract\n"


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


# --- Codex review round 11, P1: reserved-directory containment (I1) --------


def test_reserved_directory_paths_includes_all_directory_entries(tmp_path: Path):
    """`_reserved_directory_paths` selects exactly the directory-type
    entries `_RESERVED_DIRECTORY_KEYS` names — the containment roots the
    score/`-o` guards below check against."""
    workdir = tmp_path / "wd"
    paths = run_round._reserved_workdir_paths(workdir)
    directories = dict(run_round._reserved_directory_paths(paths))
    assert set(directories) == {
        "identity_dir",
        "package_dir",
        "judge_inputs_dir",
        "subproc_staging_dir",
        "subproc_staging_package_dir",
    }
    assert directories["subproc_staging_dir"] == paths["subproc_staging_dir"]
    assert directories["subproc_staging_package_dir"] == paths["subproc_staging_package_dir"]


def test_reject_score_copy_self_collision_rejects_score_under_subproc_staging(
    tmp_path: Path,
):
    """I1 unit case: a score.yaml living *inside* a reserved directory
    (subproc_staging/, which run_round() clears via `shutil.rmtree` before
    ever reading score.yaml) must be refused by containment, not just by
    exact equality with a single reserved path."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)
    staging_dir = paths["subproc_staging_dir"]
    staging_dir.mkdir(parents=True)
    score_path = staging_dir / "candidate.yaml"
    score_path.write_text("schema_version: composition-score/0.1\n")

    with pytest.raises(run_round.ProtectedPathError, match="subproc_staging_dir"):
        run_round._reject_score_copy_self_collision(score_path, paths)


def test_reject_output_collision_rejects_output_under_subproc_staging(tmp_path: Path):
    """I1 unit case: an -o target living inside subproc_staging/ must be
    refused by containment even though it doesn't equal any single reserved
    path exactly (e.g. an arbitrary filename, not one of the known staged
    CLI output names)."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)

    with pytest.raises(run_round.ProtectedPathError, match="subproc_staging_dir"):
        run_round._reject_output_collision(
            paths["subproc_staging_dir"] / "report.json",
            score_path=POSITIVE_CONTROL_SCORE_PATH,
            reserved_paths=paths.values(),
            reserved_directory_paths=run_round._reserved_directory_paths(paths),
        )


def test_reject_output_collision_accepts_fresh_path_under_workdir_non_reserved_name(
    tmp_path: Path,
):
    """I1 boundary case: containment is checked against reserved directory
    entries only, not against --workdir itself — an -o landing directly
    under --workdir with a name that isn't any reserved artifact (the
    ordinary <workdir>/report.json shape, the ordinary accept-a-round flow)
    must remain accepted."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    paths = run_round._reserved_workdir_paths(workdir)

    # Must not raise.
    run_round._reject_output_collision(
        workdir / "my_report.json",
        score_path=POSITIVE_CONTROL_SCORE_PATH,
        reserved_paths=paths.values(),
        reserved_directory_paths=run_round._reserved_directory_paths(paths),
    )


def test_run_round_rejects_score_under_subproc_staging_before_read(tmp_path: Path):
    """I1 full-pipeline negative case: a --workdir reused from a prior round
    that already has a subproc_staging/ tree, with score.yaml pointed at a
    file living inside it, must be refused before run_round()'s staging
    clear (`shutil.rmtree(subproc_staging_dir)`) would delete the very input
    this run is about to read — without this guard, the read would fail with
    a confusing FileNotFoundError mid-run instead of a clean preflight
    refusal."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    staging_dir = workdir / "subproc_staging"
    staging_dir.mkdir()
    score_path = staging_dir / "candidate.yaml"
    score_bytes = POSITIVE_CONTROL_SCORE_PATH.read_bytes()
    score_path.write_bytes(score_bytes)
    output_path = tmp_path / "report.json"

    with pytest.raises(run_round.ProtectedPathError):
        run_round.run_round(score_path, workdir, 0, output_path)

    # Refused before any write: the score file under subproc_staging/ must
    # survive untouched — the staging clear that would otherwise delete it
    # never got to run.
    assert score_path.read_bytes() == score_bytes
    assert not output_path.exists()


def test_run_round_rejects_output_under_subproc_staging_before_clear(tmp_path: Path):
    """I1 full-pipeline negative case: an -o target left over inside
    subproc_staging/ from a prior invocation of a reused --workdir must be
    refused before run_round()'s staging clear deletes it — otherwise a
    later failure partway through this run would lose the old report with
    no new report published in its place."""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    staging_dir = workdir / "subproc_staging"
    staging_dir.mkdir()
    output_path = staging_dir / "report.json"
    stale_report = b'{"stale": "report from a previous invocation"}'
    output_path.write_bytes(stale_report)

    with pytest.raises(run_round.ProtectedPathError):
        run_round.run_round(POSITIVE_CONTROL_SCORE_PATH, workdir, 0, output_path)

    # Refused before any write: the stale report must survive untouched.
    assert output_path.read_bytes() == stale_report


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


# --- Codex review round 17, P1: protected-tree preflight for -o (J2) -------


def test_reject_output_collision_rejects_existing_evidence_under_loop_tree(
    tmp_path: Path, monkeypatch
):
    """J2 negative case: an `-o` that resolves inside the protected L0b loop
    tree *and already exists* is refused, even though it is none of
    `prev_report`/`curr_report`/`--pareto` — mirrors `run_round.py`'s pinned
    evidence e.g. `rounds/round1/score.yaml`, `ledger.yaml`. Uses a
    monkeypatched `_LOOP_DIR` pointed at `tmp_path` (not the real repo tree)
    so this test never touches actual pin-recorded evidence."""
    monkeypatch.setattr(pareto_eval, "_LOOP_DIR", tmp_path)
    existing_evidence = tmp_path / "rounds" / "round1" / "score.yaml"
    existing_evidence.parent.mkdir(parents=True)
    existing_evidence.write_text("pinned: evidence\n", encoding="utf-8")

    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    pareto_path = tmp_path / "pareto.yaml"

    with pytest.raises(pareto_eval.ProtectedPathError, match="protected L0b loop tree"):
        pareto_eval._reject_output_collision(
            existing_evidence,
            prev_report_path=prev_path,
            curr_report_path=curr_path,
            pareto_path=pareto_path,
        )


def test_reject_output_collision_accepts_new_path_under_loop_tree(tmp_path: Path, monkeypatch):
    """J2 positive case: a brand-new path under the protected loop tree (the
    ordinary `rounds_t2_clean/roundN/pareto_vs_*.json` creation flow) is
    still accepted — only an *existing* path there is refused."""
    monkeypatch.setattr(pareto_eval, "_LOOP_DIR", tmp_path)
    new_output = tmp_path / "rounds_t2_clean" / "round6" / "pareto_vs_round5.json"
    new_output.parent.mkdir(parents=True)

    prev_path = tmp_path / "prev.json"
    curr_path = tmp_path / "curr.json"
    pareto_path = tmp_path / "pareto.yaml"

    result = pareto_eval._reject_output_collision(
        new_output,
        prev_report_path=prev_path,
        curr_report_path=curr_path,
        pareto_path=pareto_path,
    )
    assert result == new_output.resolve()


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

    eval_control_profile_bytes = run_round.EVAL_CONTROL_PROFILE_PATH.read_bytes()
    eval_score_path, eval_score_sha256 = run_round._prepare_scores(
        nonexistent_score_path, score_bytes, paths, eval_control_profile_bytes
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


# NOTE (PR #247 round 4 P2): `_publish_report_bundle` moved from "hashes
# written atomically, then report written atomically" to a two-phase staged
# design — both members are fully staged (write+flush+fsync to a tempfile)
# *before* either destination is touched, then published via two consecutive
# `os.replace` calls, report first. The three tests below are rewritten
# (not just touched up) because their failure-injection seam
# (`monkeypatch.setattr(run_round, "atomic_write_bytes", ...)`) no longer
# intercepts anything on the initial-publish path — `atomic_write_bytes` is
# now only called during rollback's restore of the report. Each test's
# *intent* (rollback restores pre-existing bytes / removes never-existed
# paths / rollback failure chains) is preserved; only the injection point
# and, where the publish-order flip changes which member rollback restores,
# the specific bytes under test change. See each docstring for detail.


def test_run_round_report_bundle_rolls_back_pre_existing_bytes_on_symbolic_fail(
    tmp_path: Path, monkeypatch
):
    """On the symbolic-fail early-return path, injecting a failure into the
    *second* publish `os.replace` (`hashes.json`) — after the report's
    `os.replace` has already made the new report visible — must roll the
    report back to its pre-call bytes. `hashes.json`'s `os.replace` never
    completes, so its pre-existing bytes survive untouched (not via
    rollback, since rollback only ever restores the report under the new
    "report first" publish order — see `_publish_report_bundle`'s
    docstring)."""
    bad_score_path = tmp_path / "bad_score.yaml"
    bad_score_path.write_text("not: a valid composition score\n", encoding="utf-8")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    output_path = tmp_path / "report.json"
    hashes_path = workdir / "hashes.json"

    # Pre-existing bytes at both bundle targets (simulating this round's
    # workdir already carrying a prior report/hashes pair) must survive a
    # failed re-publish attempt byte-for-byte.
    old_hashes_bytes = b'{"round": "old-hashes"}'
    old_report_bytes = b'{"round": "old-report"}'
    hashes_path.write_bytes(old_hashes_bytes)
    output_path.write_bytes(old_report_bytes)

    real_os_replace = run_round.os.replace

    def _fail_hashes_replace(src: str, dst: str) -> None:
        if Path(dst) == hashes_path:
            raise RuntimeError("boom: simulated failure publishing hashes.json")
        real_os_replace(src, dst)

    monkeypatch.setattr(run_round.os, "replace", _fail_hashes_replace)

    with pytest.raises(RuntimeError, match="boom"):
        run_round.run_round(bad_score_path, workdir, 0, output_path)

    # The report was published (new bytes made visible) and then rolled
    # back — this assertion is only satisfiable via a genuine restore, since
    # the intervening publish provably overwrote it with new report bytes.
    assert output_path.read_bytes() == old_report_bytes
    assert hashes_path.read_bytes() == old_hashes_bytes


def test_publish_report_bundle_removes_paths_that_did_not_exist_before(
    tmp_path: Path, monkeypatch
):
    """Unit-level counterpart to the fail-gate test above, exercising
    `_publish_report_bundle` directly: forcing the *report's* `os.replace`
    (the first publish step) to fail means `output_path` is never touched
    (POSIX rename is all-or-nothing) and the hashes `os.replace` never even
    runs — neither bundle path should exist afterward, and no rollback
    restore is attempted (there is nothing to roll back)."""
    hashes_path = tmp_path / "hashes.json"
    output_path = tmp_path / "report.json"

    real_os_replace = run_round.os.replace

    def _fail_report_replace(src: str, dst: str) -> None:
        if Path(dst) == output_path:
            raise RuntimeError("boom: simulated failure publishing the report")
        real_os_replace(src, dst)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_round.os, "replace", _fail_report_replace)
        with pytest.raises(RuntimeError, match="boom"):
            run_round._publish_report_bundle(
                output_path=output_path,
                report_bytes=b'{"new": "report"}',
                hashes_path=hashes_path,
                hashes_bytes=b'{"new": "hashes"}',
            )

    assert not hashes_path.exists()
    assert not output_path.exists()


def test_publish_report_bundle_rollback_failure_chains_original_exception(
    tmp_path: Path, monkeypatch
):
    """If the rollback itself also fails, the original publish exception
    must not be silently swallowed: `_publish_report_bundle` raises
    `ReportBundleRollbackError` chained (`__cause__`) from the rollback
    failure, and the original exception's message survives in the new
    exception's own message.

    Under the new "report first" publish order, rollback only ever restores
    the report, so triggering rollback requires the *hashes* `os.replace` to
    fail (after the report's succeeded), and making the rollback itself fail
    requires the report's `atomic_write_bytes` restore to fail."""
    hashes_path = tmp_path / "hashes.json"
    output_path = tmp_path / "report.json"
    hashes_path.write_bytes(b"old-hashes")
    output_path.write_bytes(b"old-report")

    real_os_replace = run_round.os.replace

    def _fail_hashes_replace(src: str, dst: str) -> None:
        if Path(dst) == hashes_path:
            raise RuntimeError("boom: simulated failure publishing hashes.json")
        real_os_replace(src, dst)

    real_atomic_write_bytes = run_round.atomic_write_bytes

    def _fail_report_restore(path: Path, data: bytes) -> None:
        if path == output_path and data == b"old-report":
            # Simulated failure during rollback's restore of output_path.
            raise OSError("rollback-boom: simulated failure restoring report.json")
        real_atomic_write_bytes(path, data)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_round.os, "replace", _fail_hashes_replace)
        mp.setattr(run_round, "atomic_write_bytes", _fail_report_restore)
        with pytest.raises(run_round.ReportBundleRollbackError) as exc_info:
            run_round._publish_report_bundle(
                output_path=output_path,
                report_bytes=b'{"new": "report"}',
                hashes_path=hashes_path,
                hashes_bytes=b'{"new": "hashes"}',
            )

    assert "boom: simulated failure publishing hashes.json" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, OSError)


def test_publish_report_bundle_staging_failure_leaves_existing_bundle_untouched(
    tmp_path: Path, monkeypatch
):
    """A failure during the *staging* phase (writing the hashes tempfile,
    after the report tempfile staged fine) must never touch either
    published path — not even to enter the rollback/snapshot machinery,
    since neither `os.replace` has run yet. Checks (a) existing bundle
    bytes survive byte-for-byte and (b) `os.replace` is never called at all
    for this invocation — proving the snapshot-restore branch was never
    *entered*, not merely that it happened to restore correctly."""
    hashes_path = tmp_path / "hashes.json"
    output_path = tmp_path / "report.json"
    old_hashes_bytes = b"old-hashes"
    old_report_bytes = b"old-report"
    hashes_path.write_bytes(old_hashes_bytes)
    output_path.write_bytes(old_report_bytes)

    real_stage_bytes = run_round._stage_bytes

    def _fail_hashes_stage(directory: Path, dest_name: str, data: bytes) -> Path:
        if dest_name == hashes_path.name:
            raise RuntimeError("boom: simulated failure staging hashes.json")
        return real_stage_bytes(directory, dest_name, data)

    replace_calls: list[tuple[str, str]] = []
    real_os_replace = run_round.os.replace

    def _recording_replace(src: str, dst: str) -> None:
        replace_calls.append((str(src), str(dst)))
        real_os_replace(src, dst)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_round, "_stage_bytes", _fail_hashes_stage)
        mp.setattr(run_round.os, "replace", _recording_replace)
        with pytest.raises(RuntimeError, match="boom: simulated failure staging"):
            run_round._publish_report_bundle(
                output_path=output_path,
                report_bytes=b'{"new": "report"}',
                hashes_path=hashes_path,
                hashes_bytes=b'{"new": "hashes"}',
            )

    assert hashes_path.read_bytes() == old_hashes_bytes
    assert output_path.read_bytes() == old_report_bytes
    assert replace_calls == []
    # No leftover tempfiles either — the report's tempfile, staged
    # successfully before the hashes staging failure, is cleaned up too.
    assert list(tmp_path.glob("*.tmp")) == []


def test_publish_report_bundle_publishes_report_before_hashes(tmp_path: Path, monkeypatch):
    """Publish order matters (see `_publish_report_bundle`'s docstring): the
    report must become visible via `os.replace` before `hashes.json` does,
    so a crash between the two syscalls leaves the machine-detectable "new
    report + old hashes.json" state rather than the reverse."""
    hashes_path = tmp_path / "hashes.json"
    output_path = tmp_path / "report.json"

    replace_order: list[str] = []
    real_os_replace = run_round.os.replace

    def _recording_replace(src: str, dst: str) -> None:
        replace_order.append(Path(dst).name)
        real_os_replace(src, dst)

    monkeypatch.setattr(run_round.os, "replace", _recording_replace)

    run_round._publish_report_bundle(
        output_path=output_path,
        report_bytes=b'{"new": "report"}',
        hashes_path=hashes_path,
        hashes_bytes=b'{"new": "hashes"}',
    )

    assert replace_order == [output_path.name, hashes_path.name]


# --- ledger shape validation (PR #247 Codex review round 8) -------------------


def test_ledger_t2_rounds_shape_includes_clean_rerun():
    """PR #247 レビュー 8 巡目 P1: round `5_clean` が誤って
    `post_loop_hardening.items` 配下へネストされる退行が実発生した。台帳の
    正直会計は消費者が `t2.rounds` を列挙して読む前提なので、クリーン再周回
    と off-contract 分類が正しい位置に存在することを形状として enforce する
    （YAML が парース可能なだけでは形状退行を検出できない）。"""

    ledger = yaml.safe_load((LOOP_DIR / "ledger.yaml").read_text(encoding="utf-8"))

    round_ids = [entry.get("round") for entry in ledger["t2"]["rounds"]]
    assert round_ids == [1, 2, 3, 4, 5, "5_clean"]

    # off-contract 分類: rounds 3/4/5 = coordinator 注記、5_clean = 系譜汚染
    # （PR #247 レビュー 12 巡目で追加）。
    off_contract_rounds = {e["round"] for e in ledger["t2"]["off_contract_events"]}
    assert off_contract_rounds == {3, 4, 5, "5_clean"}

    round5 = next(e for e in ledger["t2"]["rounds"] if e.get("round") == 5)
    assert "excluded" in round5["evidence_status"]

    # クリーン系列（round 2 から分岐）: 3c/4c/5c が dated + pin 付きで記録
    # され、5c が改善周回であること（クリーン操舵証拠の形状 enforce）。
    clean_branch = ledger["t2"]["clean_branch"]
    assert clean_branch["branched_from"] == 2
    clean_ids = [entry["round"] for entry in clean_branch["rounds"]]
    assert clean_ids == ["3c", "4c", "5c"]
    for entry in clean_branch["rounds"]:
        assert entry["files"]["score"]["sha256"]
        assert entry["files"]["report"]["sha256"]
    round5c = clean_branch["rounds"][-1]
    assert round5c["pareto_vs_prev"]["improved"] is True
    assert round5c["observed"] == ["intro", "chorus", "chorus", "outro"]

    assert ledger["loop_status_t2"]["status"] == "clean_success"
    # 旧名 clean_rerun_round は「5_clean = クリーン」を示唆するため改名済み
    # （16 巡目 P1）。旧キーの復活も形状違反として検出する。
    assert "clean_rerun_round" not in ledger["loop_status_t2"]
    assert ledger["loop_status_t2"]["lineage_contaminated_rerun"] == "5_clean"
    assert ledger["loop_status_t2"]["clean_branch_terminated_at"] == "5c"

    hardening_items = ledger["post_loop_hardening"]["items"]
    assert all(isinstance(item, str) for item in hardening_items)
