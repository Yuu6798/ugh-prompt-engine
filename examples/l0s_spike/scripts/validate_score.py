"""L0-s symbolic validation gate.

Runs two checks against a candidate `score.yaml`, in order, and records a
deterministic pass/fail verdict as JSON — no audio is produced here. This is
the first gate of the L0-s pipeline (`examples/l0s_spike/contract.md` §3's
`symbolic_validation` block); a `fail` result is a normal, expected outcome
(not a script failure), so this always exits 0 as long as the file itself
could be opened as YAML.

1. **L0-s public-scope check** (PR #245 Codex P2 review): the authoring
   contract (`contract.md` §1) publishes exactly six top-level keys — `meta`,
   `semantic`, `physical`, `structure`, `rendering`, `events` — and
   explicitly forbids everything else, `fixity` and `control_profile`
   included (those exist in the canonical `CompositionScore` schema but are
   engine-internal, injected downstream by `measure_round.py`, never
   author-facing). Before this check was added, an author score carrying a
   contract-forbidden key still passed `CompositionScore.model_validate`
   unchanged (canonical schema accepts `fixity`/`control_profile` as
   optional fields) — i.e. a contract violation the symbolic gate was
   supposed to catch silently slipped through as `status: pass`, never
   landing in `off_contract_events`. Every top-level key outside the
   allowlist is reported (not just the first) in a single deterministic,
   sorted list, whether or not canonical validation would separately reject
   it.
2. **Canonical schema validation**
   (`svp_rpe.compose.loader.load_composition_score`'s underlying model).

Both checks always run — a public-scope failure does not short-circuit
canonical validation — and `status` is `fail` if either produced an error;
`errors` concatenates public-scope errors (sorted) followed by canonical
validation errors (in the model's own deterministic `loc` order).
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

# contract.md §1: the only top-level keys an L0-s author may write. Anything
# else — including `fixity` and `control_profile`, which are legal
# CompositionScore fields but outside this contract's public range — is
# rejected before canonical validation runs.
PUBLIC_SCOPE_KEYS = frozenset({"meta", "semantic", "physical", "structure", "rendering", "events"})

OFF_CONTRACT_KEY_MESSAGE = (
    "contract-forbidden key (L0-s public schema; recorded as off-contract self-edit)"
)


def _public_scope_errors(raw: dict[str, Any]) -> list[dict[str, str]]:
    off_contract_keys = sorted(str(key) for key in raw if key not in PUBLIC_SCOPE_KEYS)
    return [{"where": key, "message": OFF_CONTRACT_KEY_MESSAGE} for key in off_contract_keys]


def _canonical_validation_errors(raw: dict[str, Any]) -> list[dict[str, str]]:
    try:
        CompositionScore.model_validate(raw)
    except ValidationError as exc:
        return [
            {"where": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
    return []


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

    errors = _public_scope_errors(raw) + _canonical_validation_errors(raw)
    if errors:
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
