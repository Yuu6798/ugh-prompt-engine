"""L0b Pareto improvement evaluator (`frozen/pareto.yaml`, `task.md` 正本 §5).

`pareto_eval.py <prev_report.json> <curr_report.json> --pareto
<pareto.yaml>` computes the frozen axis-wise distances for two
`AuthoringDiffReport` JSON files (`src/svp_rpe/authoring/report.py`'s normal
form, as `scripts/run_round.py` writes) and reports whether `curr` is a
Pareto improvement over `prev`, per `frozen/pareto.yaml`'s
`improvement_rule`/`tie_rule`/`band_rule`. Pure function (`evaluate()`) +
deterministic JSON output — no side effects beyond stdout / `-o`.

Distance definitions (mirror `frozen/pareto.yaml`, the single source of
truth — this module does not re-derive them from anywhere else):

- `key`/`brightness`: `0` if `axes[<name>].verdict == "preserved"` else `1`
  (`_binary_from_verdict`) — the verdict itself already encodes the
  requirement/observed comparison (`AuthoringDiffReport`'s own
  `model_validator`s enforce that a `preserved` verdict only appears when
  the values actually match), so this does not re-implement that
  comparison.
- `structure`: token-level Levenshtein edit distance between
  `axes["structure"].requirement` and `.observed` (both label lists),
  comparing tokens via `str.casefold()` equality (`_levenshtein`).

Band rule (D5 / 正本 §5 保守側原則): if either report's axis has `band !=
"measured"` (or the axis is entirely absent — e.g. a `symbolic_validation:
fail` report carries no `axes` at all), that axis is excluded from the
distance computation and the *whole round pair* is reported as
`improved: false`, `band_excluded: true` — an out-of-band/absent axis's
distance is not trustworthy evidence in either direction, so a round pair
with any such axis is never counted as improvement evidence (never silently
downgraded to a "tie").

Verdict/value consistency gate (`evaluate()`, Codex review round 5, P1):
`AuthoringDiffReport` (`src/svp_rpe/authoring/report.py`) *deliberately*
leaves a failure-side verdict's (`deviated`/`mismatch`) `requirement`/
`observed` unconstrained — L0a's design lets a failing axis report its
actual observed value honestly, without the schema forcing it to also
prove non-equality (see that module's own docstring, "成功側のみ強制・
失敗側は正直な報告の自由"). This module, however, treats `verdict` as the
distance's ground truth (`_binary_from_verdict`: `preserved` -> 0, anything
else -> 1) rather than re-deriving the comparison from `requirement`/
`observed` itself — so a `key`/`brightness` axis report that says
`verdict="deviated"` while its own `requirement`/`observed` actually agree
is internally contradictory, and silently trusting its `verdict` over its
own values would let such a report manufacture a false `deviated ->
preserved` "improvement" (`prev` distance 1, `curr` distance 0) purely from
an unenforced verdict/value mismatch, with no real change in what was
measured. `_reject_contradictory_deviated_verdict()` re-derives the same
comparison `AuthoringDiffReport`'s `preserved` validator already trusts
(`keys_enharmonically_equal` for `key`, plain string equality for
`brightness`) and raises `ValueError` when a `band == "measured"`,
`verdict == "deviated"` axis's `requirement`/`observed` are both `str` and
equal under that comparison — this is a local consistency check on this
evaluator's own inputs, not a re-implementation of `AuthoringDiffReport`'s
comparison logic in general (it only ever runs the comparison in the one
direction needed to catch this specific contradiction). It runs for both
`prev_report` and `curr_report`, for every axis of theirs, before any
distance is computed. Non-`str` `requirement`/`observed` (e.g. `None`, the
`_axis()` test-helper default) are skipped — nothing to compare — as are
axes with `band != "measured"`: those axes are never used in the distance
computation in the first place (`_axis_distance` is only called when both
sides' `band == "measured"`), so a non-`measured`-band axis's verdict/value
relationship cannot manufacture false distance evidence and is out of this
gate's scope. `structure` is excluded from this gate entirely — its
distance (`_levenshtein`) is computed directly from `requirement`/
`observed`, never from `verdict`, so `verdict` cannot desync from the
values used for its distance the way it can for `key`/`brightness`.

Input validation (`main()` only — Codex review #5, P1): `prev_report`/
`curr_report` are validated against `AuthoringDiffReport`
(`src/svp_rpe/authoring/report.py`) before `evaluate()` runs, so a
malformed or internally-inconsistent report.json (wrong `Verdict`/`Band`
vocabulary, a `preserved` verdict paired with a non-`measured` band, etc.)
fails fast with a clear message and non-zero exit instead of `evaluate()`
silently computing a distance over untrustworthy input. `evaluate()` itself
stays a pure function of plain dicts — the validated model is discarded
after the check (`_load_report()` returns the original parsed dict
unchanged), so this gate cannot perturb the deterministic JSON `evaluate()`
already produced (a pin'd `pareto` result JSON stays byte-identical to
before this gate existed).

Output-collision guard (`main()` only — Codex review #5, P2): `-o`'s
resolved path is refused, before anything is written, if it matches
`prev_report`/`curr_report`/`--pareto` (mirrors `run_round.py`'s
`ProtectedPathError`/`_reject_output_collision` style — a rejected
invocation must leave the filesystem untouched).

Spec/implementation contract check (`evaluate()`, Codex review round 2, P2):
`evaluate()` previously only checked the *axis key set* of `pareto_spec`
against `_AXES` — it never checked that the spec's declared `schema_version`,
per-axis `distance`/`order`, or the presence of the three prose rule fields
actually match what this module hardcodes (`_binary_from_verdict`/
`_levenshtein`, "lower distance wins", `improvement_rule`/`tie_rule`/
`band_rule`). `_validate_pareto_spec_contract()` now enforces that
agreement up front, before any distance is computed, so a spec that has
drifted from the implementation fails fast with a clear `ValueError`
instead of `evaluate()` silently computing a result under a stale label.
Boundary (deliberately not enforced): `improvement_rule`/`tie_rule`/
`band_rule` are checked for byte-for-byte agreement against
frozen/pareto.yaml's current canonical wording via a pinned sha256 digest
per field (`_PROSE_RULE_SHA256` / `_require_canonical_prose_rule` — D2,
Codex review round 3, P2), but their prose semantics are still never parsed
or reinterpreted here; `evaluate()`'s own control flow below remains the
sole source of truth for what those rules actually mean — the pin only
detects wording drift, it does not derive behavior from the prose. `main()`
catches this `ValueError` the same way it already catches `_load_report()`'s
validation errors (stderr + exit 1).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from svp_rpe.authoring.report import AuthoringDiffReport
from svp_rpe.keys import keys_enharmonically_equal
from svp_rpe.utils.atomic_io import atomic_write_bytes

_AXES = ("key", "brightness", "structure")


def _levenshtein(a: list[str], b: list[str]) -> int:
    """Token-level Levenshtein edit distance (unit-cost insert/delete/
    substitute), tokens compared via `str.casefold()` equality
    (`frozen/pareto.yaml`'s `axes.structure.distance_definition`)."""
    a_norm = [token.casefold() for token in a]
    b_norm = [token.casefold() for token in b]
    n, m = len(a_norm), len(b_norm)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if a_norm[i - 1] == b_norm[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    return dp[n][m]


def _binary_from_verdict(verdict: Optional[str]) -> int:
    return 0 if verdict == "preserved" else 1


def _require_str_tokens(axis_name: str, side: str, tokens: list[Any]) -> None:
    """D1 (PR #247 Codex review round 3, P2): `_axis_distance` used to coerce
    every element via `str(item)` before comparing — silently accepting
    `requirement=[1, 2]` or `observed=[None]` and comparing their `str()`
    forms instead of failing loudly on a malformed structure axis. This
    enforces that every element of `tokens` (`axes[axis_name][side]`) is
    already a `str` (`AuthoringDiffReport`'s own `requirement`/`observed`
    fields are typed `Any` — see `src/svp_rpe/authoring/report.py` — so
    `pareto_eval.py` cannot assume element type without checking itself),
    raising a `ValueError` that names the axis, the side (`requirement` vs
    `observed`), the offending index, and the violating value/type."""
    for index, item in enumerate(tokens):
        if not isinstance(item, str):
            raise ValueError(
                f"axes[{axis_name!r}].{side}[{index}] must be a str token, got "
                f"{item!r} ({type(item).__name__}) — pareto_eval.py no longer "
                "coerces non-str tokens via str(); a structure axis's "
                "requirement/observed must be a list of string labels."
            )


def _axis_distance(axis_name: str, axis: dict[str, Any]) -> int:
    if axis_name == "structure":
        requirement = axis.get("requirement")
        observed = axis.get("observed")
        if not isinstance(requirement, list) or not isinstance(observed, list):
            raise ValueError(
                "axes['structure'].requirement/observed must both be lists to compute "
                f"the Levenshtein distance, got requirement={requirement!r} "
                f"observed={observed!r}"
            )
        _require_str_tokens(axis_name, "requirement", requirement)
        _require_str_tokens(axis_name, "observed", observed)
        return _levenshtein(requirement, observed)
    return _binary_from_verdict(axis.get("verdict"))


def _axis_band(axis: Optional[dict[str, Any]]) -> Optional[str]:
    if axis is None:
        return None
    return axis.get("band")


_CONTRADICTION_COMPARATORS = {
    "key": keys_enharmonically_equal,
    "brightness": lambda requirement, observed: requirement == observed,
}


def _reject_contradictory_deviated_verdict(
    report_label: str, axis_name: str, axis: Optional[dict[str, Any]]
) -> None:
    """Consistency gate — see module docstring's "Verdict/value consistency
    gate" section for the full rationale/scope. Only `key`/`brightness` are
    checked (`_CONTRADICTION_COMPARATORS`); `structure` never reaches this
    function (its distance never reads `verdict`, see module docstring).
    Only fires for `band == "measured"` + `verdict == "deviated"` axes whose
    `requirement`/`observed` are both `str` — anything else (missing axis,
    other band, other verdict, non-str values) is silently skipped, not an
    error in itself."""
    if axis is None:
        return
    comparator = _CONTRADICTION_COMPARATORS.get(axis_name)
    if comparator is None:
        return
    if axis.get("band") != "measured" or axis.get("verdict") != "deviated":
        return
    requirement = axis.get("requirement")
    observed = axis.get("observed")
    if not (isinstance(requirement, str) and isinstance(observed, str)):
        return
    if comparator(requirement, observed):
        raise ValueError(
            f"{report_label}.axes[{axis_name!r}] is a contradictory report: "
            f"verdict='deviated' with band='measured', but requirement={requirement!r} "
            f"and observed={observed!r} actually match "
            f"({'enharmonically ' if axis_name == 'key' else ''}equal) — "
            "AuthoringDiffReport deliberately leaves a deviated axis's requirement/"
            "observed unconstrained (L0a's failure-side honesty design), so this "
            "evaluator-local check rejects a report whose own values contradict its "
            "verdict instead of silently trusting 'deviated' as the distance's ground "
            "truth (which would otherwise manufacture a false deviated->preserved "
            "improvement)."
        )


_REQUIRED_SCHEMA_VERSION = "l0b-pareto/1.0"
_BINARY_AXES = ("key", "brightness")
_STRUCTURE_DISTANCE = "levenshtein_casefold"
_BINARY_DISTANCE = "binary_from_verdict"
_REQUIRED_ORDER = "lower_is_better"
_PROSE_RULE_KEYS = ("improvement_rule", "tie_rule", "band_rule")


# D2 (PR #247 Codex review round 3, P2): the three prose rule fields used to
# be checked only for *presence* (non-empty string) — a spec whose prose had
# silently drifted from frozen/pareto.yaml's frozen wording (e.g. a copy/
# paste error, or a manually-edited `--pareto` file) still passed. These
# sha256 hex digests pin the *exact* current canonical wording of
# frozen/pareto.yaml's improvement_rule/tie_rule/band_rule, so that any given
# spec's rule text is checked for byte-for-byte agreement, not just
# non-emptiness. frozen/pareto.yaml remains the single source of truth for
# what the prose says (per the module docstring's "Boundary" note below,
# this module's control flow — not the prose — is what `evaluate()` actually
# executes); this pin exists only to *detect* drift between the two, the
# same drift-detection style as L0a's trusted_axes table pin/drift-test
# (`docs/l0a_authoring_contract.md`). Computed by loading the current
# frozen/pareto.yaml with `yaml.safe_load` and hashing the three resulting
# strings directly — not hand-transcribed, since YAML `>-` folded block
# scalars normalize internal line breaks to single spaces and strip the
# trailing newline in a way that is easy to get wrong by hand.
_PROSE_RULE_SHA256: dict[str, str] = {
    "improvement_rule": "527fa17af1d1f3aba1e63465bc7323a5ef2e2e3848f26c589b475411d77d005a",
    "tie_rule": "866ff29f1d396b7475a85651acba129cbf74d5c528adf6878263b726960c29aa",
    "band_rule": "88fd578e232a7435eca9083e3bf8a7a65beec21197594439060fc0c6b970e38e",
}


def _require_canonical_prose_rule(spec: dict[str, Any], key: str) -> None:
    value = spec.get(key)
    if not isinstance(value, str):
        raise ValueError(
            f"frozen/pareto.yaml's {key!r} must be a string, got {value!r}"
        )
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    expected = _PROSE_RULE_SHA256[key]
    if digest != expected:
        raise ValueError(
            f"frozen/pareto.yaml's {key!r} rule text drifted from the implemented "
            f"contract (sha256 {digest} != pinned {expected}) — frozen/pareto.yaml "
            "is the single source of truth for this prose (its own header forbids "
            "changing it after L0B-T1 started); pareto_eval.py pins the exact "
            "wording only to detect drift (same style as L0a trusted_axes' "
            "drift-test), never to reinterpret its meaning."
        )


def _validate_axis_contract(spec_axes: dict[str, Any], axis_name: str, *, distance: str) -> None:
    axis_spec = spec_axes.get(axis_name)
    if not isinstance(axis_spec, dict):
        raise ValueError(
            f"frozen/pareto.yaml's axes[{axis_name!r}] must be a mapping, got {axis_spec!r}"
        )
    if axis_spec.get("distance") != distance:
        raise ValueError(
            f"frozen/pareto.yaml's axes[{axis_name!r}].distance must be {distance!r}, got "
            f"{axis_spec.get('distance')!r} — pareto_eval.py's distance implementation and "
            "the frozen spec have drifted apart"
        )
    if axis_spec.get("order") != _REQUIRED_ORDER:
        raise ValueError(
            f"frozen/pareto.yaml's axes[{axis_name!r}].order must be {_REQUIRED_ORDER!r}, "
            f"got {axis_spec.get('order')!r} — pareto_eval.py's distance comparisons "
            "(lower distance always wins, hardcoded) and the frozen spec have drifted apart"
        )


def _validate_pareto_spec_contract(pareto_spec: dict[str, Any]) -> dict[str, Any]:
    """Enforces that `pareto_spec` (parsed `frozen/pareto.yaml`) matches what
    this module actually implements — see module docstring's "Spec/
    implementation contract check" section for what is and is not checked.
    Returns the validated `axes` mapping (a plain `dict`) for the caller's
    convenience; raises `ValueError` on any mismatch."""
    schema_version = pareto_spec.get("schema_version")
    if schema_version != _REQUIRED_SCHEMA_VERSION:
        raise ValueError(
            f"frozen/pareto.yaml's schema_version must be {_REQUIRED_SCHEMA_VERSION!r}, "
            f"got {schema_version!r}"
        )

    spec_axes = pareto_spec.get("axes")
    if not isinstance(spec_axes, dict) or set(spec_axes) != set(_AXES):
        got = sorted(spec_axes) if isinstance(spec_axes, dict) else spec_axes
        raise ValueError(
            f"frozen/pareto.yaml declares axes {got!r}, expected "
            f"{sorted(_AXES)!r} — pareto_eval.py and the frozen spec have drifted apart"
        )

    for axis_name in _BINARY_AXES:
        _validate_axis_contract(spec_axes, axis_name, distance=_BINARY_DISTANCE)
    _validate_axis_contract(spec_axes, "structure", distance=_STRUCTURE_DISTANCE)

    for rule_key in _PROSE_RULE_KEYS:
        _require_canonical_prose_rule(pareto_spec, rule_key)

    return spec_axes


def evaluate(
    prev_report: dict[str, Any], curr_report: dict[str, Any], pareto_spec: dict[str, Any]
) -> dict[str, Any]:
    """Pure function: `prev_report`/`curr_report` are parsed
    `AuthoringDiffReport` JSON (dicts), `pareto_spec` is parsed
    `frozen/pareto.yaml`. Validates `pareto_spec` against the implementation
    contract this module hardcodes (`_validate_pareto_spec_contract` — axis
    key set, per-axis distance/order, prose rule text pinned by sha256)
    before computing
    anything. Returns a deterministic result dict — see module docstring
    for the band-exclusion/improvement semantics."""
    _validate_pareto_spec_contract(pareto_spec)

    prev_axes = prev_report.get("axes") or {}
    curr_axes = curr_report.get("axes") or {}

    per_axis: dict[str, Any] = {}
    excluded_axes: list[str] = []

    for axis_name in _AXES:
        prev_axis = prev_axes.get(axis_name)
        curr_axis = curr_axes.get(axis_name)
        # Consistency gate — before any distance is computed (module
        # docstring's "Verdict/value consistency gate" section).
        _reject_contradictory_deviated_verdict("prev_report", axis_name, prev_axis)
        _reject_contradictory_deviated_verdict("curr_report", axis_name, curr_axis)
        prev_band = _axis_band(prev_axis)
        curr_band = _axis_band(curr_axis)
        axis_measured = (
            prev_axis is not None
            and curr_axis is not None
            and prev_band == "measured"
            and curr_band == "measured"
        )

        entry: dict[str, Any] = {
            "prev_band": prev_band,
            "curr_band": curr_band,
            "measured": axis_measured,
        }
        if axis_measured:
            prev_distance = _axis_distance(axis_name, prev_axis)
            curr_distance = _axis_distance(axis_name, curr_axis)
            entry["prev_distance"] = prev_distance
            entry["curr_distance"] = curr_distance
            entry["delta"] = curr_distance - prev_distance
            entry["regressed"] = curr_distance > prev_distance
            entry["strictly_improved"] = curr_distance < prev_distance
        else:
            excluded_axes.append(axis_name)
        per_axis[axis_name] = entry

    band_excluded = bool(excluded_axes)
    if band_excluded:
        improved = False
    else:
        regressed_any = any(per_axis[axis_name]["regressed"] for axis_name in _AXES)
        strictly_improved_any = any(
            per_axis[axis_name]["strictly_improved"] for axis_name in _AXES
        )
        improved = (not regressed_any) and strictly_improved_any

    return {
        "improved": improved,
        "band_excluded": band_excluded,
        "excluded_axes": sorted(excluded_axes),
        "per_axis": per_axis,
    }


class ProtectedPathError(RuntimeError):
    """`-o/--output` would clobber one of this run's own inputs
    (`prev_report`/`curr_report`/`--pareto`) — refused before anything is
    written (style mirrors `run_round.py`'s `ProtectedPathError`)."""


def _load_report(path: Path) -> dict[str, Any]:
    """Parses `path` as JSON and validates it against `AuthoringDiffReport`
    (fail-fast on a malformed/internally-inconsistent report) — the
    validated model is discarded after the check; the *original* parsed
    dict is returned unchanged, so `evaluate()`'s deterministic output does
    not depend on anything pydantic's model construction/serialization
    might normalize differently (byte-for-byte compatible with the pre-gate
    behavior for any report that passes validation)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        AuthoringDiffReport.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"{path}: not a valid AuthoringDiffReport: {exc}") from exc
    return data


def _reject_output_collision(
    output_path: Path, *, prev_report_path: Path, curr_report_path: Path, pareto_path: Path
) -> Path:
    resolved_output = output_path.resolve()
    protected = {
        prev_report_path.resolve(),
        curr_report_path.resolve(),
        pareto_path.resolve(),
    }
    if resolved_output in protected:
        raise ProtectedPathError(
            f"-o/--output must not resolve to prev_report/curr_report/--pareto "
            f"(got {resolved_output}) — writing the result there would clobber "
            "an input this run reads."
        )
    return resolved_output


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="L0b Pareto improvement evaluator")
    parser.add_argument("prev_report", type=Path, help="Path to the previous round's report.json")
    parser.add_argument("curr_report", type=Path, help="Path to the current round's report.json")
    parser.add_argument("--pareto", type=Path, required=True, help="Path to frozen/pareto.yaml")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="Optional path to write the result JSON"
    )
    args = parser.parse_args(argv)

    try:
        if args.output is not None:
            _reject_output_collision(
                args.output,
                prev_report_path=args.prev_report,
                curr_report_path=args.curr_report,
                pareto_path=args.pareto,
            )
        prev_report = _load_report(args.prev_report)
        curr_report = _load_report(args.curr_report)
        pareto_spec = yaml.safe_load(args.pareto.read_text(encoding="utf-8"))
        # `evaluate()`'s own `_validate_pareto_spec_contract` call raises
        # `ValueError` on a spec/implementation drift (C2) — caught here the
        # same way `_load_report()`'s validation errors already are, before
        # anything is written.
        result = evaluate(prev_report, curr_report, pareto_spec)
    except (ProtectedPathError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    content = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        # D3 (PR #247 Codex review round 3, P2): publish via the same
        # atomic_write_bytes `run_round.py` uses (tempfile in the same
        # directory + os.replace) instead of a plain `write_bytes` — a crash
        # partway through the write can no longer leave a truncated/partial
        # result at `-o`. stdout output is unaffected (no partial-write
        # hazard there).
        atomic_write_bytes(args.output, content.encode("utf-8"))
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
