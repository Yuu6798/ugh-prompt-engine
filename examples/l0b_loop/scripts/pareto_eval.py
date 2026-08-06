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
`band_rule` are checked only for *presence* (non-empty string) — their
prose semantics are not parsed or reinterpreted here; `evaluate()`'s own
control flow below remains the sole source of truth for what those rules
actually mean. `main()` catches this `ValueError` the same way it already
catches `_load_report()`'s validation errors (stderr + exit 1).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from svp_rpe.authoring.report import AuthoringDiffReport

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
        return _levenshtein([str(item) for item in requirement], [str(item) for item in observed])
    return _binary_from_verdict(axis.get("verdict"))


def _axis_band(axis: Optional[dict[str, Any]]) -> Optional[str]:
    if axis is None:
        return None
    return axis.get("band")


_REQUIRED_SCHEMA_VERSION = "l0b-pareto/1.0"
_BINARY_AXES = ("key", "brightness")
_STRUCTURE_DISTANCE = "levenshtein_casefold"
_BINARY_DISTANCE = "binary_from_verdict"
_REQUIRED_ORDER = "lower_is_better"
_PROSE_RULE_KEYS = ("improvement_rule", "tie_rule", "band_rule")


def _require_nonempty_str(spec: dict[str, Any], key: str) -> None:
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"frozen/pareto.yaml's {key!r} must be a non-empty string, got {value!r} — "
            "pareto_eval.py enforces only that a prose rule is present here; the "
            "meaning of what it says is the implementation's responsibility, not "
            "parsed or reinterpreted by this check."
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
        _require_nonempty_str(pareto_spec, rule_key)

    return spec_axes


def evaluate(
    prev_report: dict[str, Any], curr_report: dict[str, Any], pareto_spec: dict[str, Any]
) -> dict[str, Any]:
    """Pure function: `prev_report`/`curr_report` are parsed
    `AuthoringDiffReport` JSON (dicts), `pareto_spec` is parsed
    `frozen/pareto.yaml`. Validates `pareto_spec` against the implementation
    contract this module hardcodes (`_validate_pareto_spec_contract` — axis
    key set, per-axis distance/order, prose rule presence) before computing
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
        args.output.write_bytes(content.encode("utf-8"))
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
