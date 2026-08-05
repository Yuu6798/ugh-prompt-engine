"""L0-s symbolic validation gate.

Runs `svp_rpe.compose.loader.load_composition_score` against a candidate
`score.yaml` and records a deterministic pass/fail verdict as JSON — no audio
is produced here. This is the first gate of the L0-s pipeline
(`examples/l0s_spike/contract.md` §3's `symbolic_validation` block); a `fail`
result is a normal, expected outcome (not a script failure), so this always
exits 0 as long as the file itself could be opened as YAML.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from svp_rpe.compose.models import CompositionScore


def _validate(score_path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(score_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {
            "status": "fail",
            "errors": [{"where": "<file>", "message": str(exc)}],
        }
    if not isinstance(raw, dict):
        return {
            "status": "fail",
            "errors": [{"where": "<file>", "message": "composition score must be a mapping"}],
        }
    try:
        CompositionScore.model_validate(raw)
    except ValidationError as exc:
        errors = [
            {"where": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return {"status": "fail", "errors": errors}
    return {"status": "pass"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L0-s symbolic validation gate")
    parser.add_argument("score", type=Path, help="Path to score.yaml")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output validation.json")
    args = parser.parse_args(argv)

    result = _validate(args.score)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"status={result['status']} -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
