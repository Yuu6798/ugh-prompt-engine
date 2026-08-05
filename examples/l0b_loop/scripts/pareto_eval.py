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
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

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


def evaluate(
    prev_report: dict[str, Any], curr_report: dict[str, Any], pareto_spec: dict[str, Any]
) -> dict[str, Any]:
    """Pure function: `prev_report`/`curr_report` are parsed
    `AuthoringDiffReport` JSON (dicts), `pareto_spec` is parsed
    `frozen/pareto.yaml`. Returns a deterministic result dict — see module
    docstring for the band-exclusion/improvement semantics."""
    spec_axes = set(pareto_spec["axes"])
    if spec_axes != set(_AXES):
        raise ValueError(
            f"frozen/pareto.yaml declares axes {sorted(spec_axes)!r}, expected "
            f"{sorted(_AXES)!r} — pareto_eval.py and the frozen spec have drifted apart"
        )

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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="L0b Pareto improvement evaluator")
    parser.add_argument("prev_report", type=Path, help="Path to the previous round's report.json")
    parser.add_argument("curr_report", type=Path, help="Path to the current round's report.json")
    parser.add_argument("--pareto", type=Path, required=True, help="Path to frozen/pareto.yaml")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="Optional path to write the result JSON"
    )
    args = parser.parse_args(argv)

    prev_report = json.loads(args.prev_report.read_text(encoding="utf-8"))
    curr_report = json.loads(args.curr_report.read_text(encoding="utf-8"))
    pareto_spec = yaml.safe_load(args.pareto.read_text(encoding="utf-8"))

    result = evaluate(prev_report, curr_report, pareto_spec)
    content = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_bytes(content.encode("utf-8"))
    else:
        sys.stdout.write(content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
