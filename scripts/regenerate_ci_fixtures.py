"""Regenerate or verify examples/semantic_ci golden snapshots."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from svp_rpe.semantic_ci import ObservedRPE, TargetSVP, run_semantic_ci

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "semantic_ci"
SCENARIOS = ("pass_perfect", "repair_degraded", "repair_budget_zero")


def render_result(target_path: Path, observed_path: Path) -> str:
    """Render semantic CI output using the same JSON shape as `svprpe ci-check`."""

    target = TargetSVP(**json.loads(target_path.read_text(encoding="utf-8")))
    observed = ObservedRPE(**json.loads(observed_path.read_text(encoding="utf-8")))
    result = run_semantic_ci(target, observed)
    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def render_scenario(name: str) -> tuple[Path, str]:
    scenario_dir = FIXTURE_DIR / name
    expected_path = scenario_dir / "expected_output.json"
    target_path = scenario_dir / "target_svp.json"
    observed_path = scenario_dir / "observed_rpe.json"
    if not target_path.is_file():
        raise FileNotFoundError(f"missing fixture: {target_path}")
    if not observed_path.is_file():
        raise FileNotFoundError(f"missing fixture: {observed_path}")
    return expected_path, render_result(target_path, observed_path)


def regenerate() -> int:
    for scenario in SCENARIOS:
        expected_path, text = render_scenario(scenario)
        expected_path.write_text(text, encoding="utf-8")
        print(f"[regenerate] wrote {expected_path.relative_to(ROOT).as_posix()}")
    return 0


def check() -> int:
    mismatches: list[str] = []
    for scenario in SCENARIOS:
        expected_path, text = render_scenario(scenario)
        if not expected_path.is_file():
            mismatches.append(f"{scenario}: expected_output.json missing")
            continue
        expected = expected_path.read_text(encoding="utf-8")
        if expected != text:
            mismatches.append(f"{scenario}: expected_output.json is out of date")

    if mismatches:
        print("[check] FAIL - semantic CI fixtures are out of sync:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        print(
            "\nRun `python scripts/regenerate_ci_fixtures.py` and review the diff.",
            file=sys.stderr,
        )
        return 1

    print(f"[check] OK - {len(SCENARIOS)} semantic CI snapshots match")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate or verify examples/semantic_ci snapshots.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify snapshots without writing files.",
    )
    args = parser.parse_args(argv)
    return check() if args.check else regenerate()


if __name__ == "__main__":
    sys.exit(main())
