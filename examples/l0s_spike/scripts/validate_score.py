"""L0-s symbolic validation gate.

Runs two checks against a candidate `score.yaml`, in order, and records a
deterministic pass/fail verdict as JSON — no audio is produced here. This is
the first gate of the L0-s pipeline (`examples/l0s_spike/contract.md` §3's
`symbolic_validation` block); a `fail` result is a normal, expected outcome
(not a script failure), so this always exits 0 as long as the file itself
could be opened as YAML.

1. **L0-s public-scope check** (PR #245 Codex P2 review, two rounds):

   - **Round 1** (top-level only): `contract.md` §1 publishes exactly six
     top-level keys — `meta`, `semantic`, `physical`, `structure`,
     `rendering`, `events` — and forbids everything else, `fixity` and
     `control_profile` included (legal `CompositionScore` fields, but
     engine-internal and never author-facing under this contract).
   - **Round 2** (whole-tree, this version): the top-level-only check missed
     the same "public scope ≠ canonical" gap one level down — canonical
     `CompositionScore` accepts fields this contract never publishes at any
     depth (e.g. `semantic.lyrics_presence`) and coerces types this contract
     pins narrower (e.g. `PhysicalLayer.bpm: int | str` with a
     digit-string-to-int `field_validator`, so canonical alone would accept
     `bpm: "96"` even though `contract.md` §1 declares `bpm: int`). This
     check now walks the *entire* public schema tree from `contract.md` §1 —
     every nested object's allowed key set, every field's literal type
     (`str`/`int`, excluding `bool` even though `bool` is an `int` subclass
     in Python; `list[str]`), and the two literal enumerations the contract
     spells out (`physical.brightness`, `events.chord_progression[].root`/
     `.quality`) — verbatim.

   **Scope boundary**: this check enforces the L0-s v0 authoring contract's
   *public schema* exactly as `contract.md` §1 writes it — key sets, types,
   and the two literal enumerations it names. It is not a semantic linter:
   value plausibility beyond those enumerations (e.g. whether
   `physical.bpm` is a *musically sensible* tempo, or whether
   `structure[].bars` is positive) is out of scope here and belongs to a
   future L0a symbolic-validation-gate freeze design, not this spike-era
   runner script.

   Every violation found anywhere in the tree is reported (not just the
   first), as a single deterministic, sorted list, whether or not canonical
   validation would separately reject the same document.
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

# contract.md §1: the public schema tree an L0-s author may write, verbatim —
# key sets per level, literal types, and the two literal enumerations the
# contract names. Anything outside this tree (at any depth) is rejected
# before canonical validation runs, even where canonical `CompositionScore`
# would itself accept it (e.g. `semantic.lyrics_presence`, `bpm` as a
# digit-string).
TOP_LEVEL_KEYS = frozenset({"meta", "semantic", "physical", "structure", "rendering", "events"})
META_KEYS = frozenset({"title", "version"})
# `lyrics_presence` is a real CompositionScore.semantic field but is not
# published by contract.md §1 — deliberately absent here, so it falls out of
# SEMANTIC_KEYS and is rejected as an off-contract key like any other.
SEMANTIC_KEYS = frozenset({"core", "grv", "delta_e", "avoid"})
GRV_KEYS = frozenset({"primary", "secondary"})
DELTA_E_KEYS = frozenset({"overall"})
PHYSICAL_KEYS = frozenset(
    {
        "bpm",
        "key",
        "time_signature",
        "active_rate_target",
        "valley_depth_target",
        "brightness",
        "stereo_width",
    }
)
STRUCTURE_SECTION_KEYS = frozenset({"section", "bars", "role", "physical"})
RENDERING_KEYS = frozenset({"target_backend", "prompt_max_chars", "priority"})
EVENTS_KEYS = frozenset({"chord_progression"})
CHORD_KEYS = frozenset({"root", "quality"})

BRIGHTNESS_ENUM = frozenset({"dark", "bright", "balanced"})
CHORD_ROOT_ENUM = frozenset(
    {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"}
)
CHORD_QUALITY_ENUM = frozenset({"major", "minor"})

OFF_CONTRACT_KEY_MESSAGE = (
    "contract-forbidden key (L0-s public schema; recorded as off-contract self-edit)"
)
STR_TYPE_MESSAGE = "must be str per L0-s public schema (contract.md §1)"
INT_TYPE_MESSAGE = (
    "must be int per L0-s public schema (contract.md §1); numeric strings and "
    "TODO sentinels are rejected even though canonical CompositionScore coerces them"
)
LIST_STR_TYPE_MESSAGE = "must be a list of str per L0-s public schema (contract.md §1)"


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _extra_key_errors(prefix: str, container: Any, allowed: frozenset[str]) -> list[dict[str, str]]:
    """Off-contract keys directly under `container` (a no-op if `container`
    isn't even a mapping — a wrong-shape value is canonical validation's job
    to report, not this check's)."""
    if not isinstance(container, dict):
        return []
    extra = sorted(str(key) for key in container if key not in allowed)
    return [{"where": _join(prefix, key), "message": OFF_CONTRACT_KEY_MESSAGE} for key in extra]


def _check_str_field(where: str, container: Any, key: str) -> list[dict[str, str]]:
    if not isinstance(container, dict) or key not in container:
        return []  # missing entirely is canonical validation's "field required" to report
    if isinstance(container[key], str):
        return []
    return [{"where": where, "message": STR_TYPE_MESSAGE}]


def _check_int_field(where: str, container: Any, key: str) -> list[dict[str, str]]:
    if not isinstance(container, dict) or key not in container:
        return []
    value = container[key]
    if isinstance(value, int) and not isinstance(value, bool):
        return []
    return [{"where": where, "message": INT_TYPE_MESSAGE}]


def _check_list_str_field(where: str, container: Any, key: str) -> list[dict[str, str]]:
    if not isinstance(container, dict) or key not in container:
        return []
    value = container[key]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return []
    return [{"where": where, "message": LIST_STR_TYPE_MESSAGE}]


def _check_enum_field(
    where: str, container: Any, key: str, allowed: frozenset[str]
) -> list[dict[str, str]]:
    """Only fires once the field is already confirmed `str` (a type error from
    `_check_str_field` covers a wrong-typed value; this avoids reporting both
    for the same field)."""
    if not isinstance(container, dict) or key not in container:
        return []
    value = container[key]
    if not isinstance(value, str) or value in allowed:
        return []
    message = f"must be one of {sorted(allowed)!r} per L0-s public schema (contract.md §1)"
    return [{"where": where, "message": message}]


def _public_scope_errors(raw: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    errors += _extra_key_errors("", raw, TOP_LEVEL_KEYS)

    meta = raw.get("meta")
    errors += _extra_key_errors("meta", meta, META_KEYS)
    errors += _check_str_field("meta.title", meta, "title")
    errors += _check_str_field("meta.version", meta, "version")

    semantic = raw.get("semantic")
    errors += _extra_key_errors("semantic", semantic, SEMANTIC_KEYS)
    errors += _check_str_field("semantic.core", semantic, "core")
    errors += _check_list_str_field("semantic.avoid", semantic, "avoid")
    grv = semantic.get("grv") if isinstance(semantic, dict) else None
    errors += _extra_key_errors("semantic.grv", grv, GRV_KEYS)
    errors += _check_str_field("semantic.grv.primary", grv, "primary")
    errors += _check_str_field("semantic.grv.secondary", grv, "secondary")
    delta_e = semantic.get("delta_e") if isinstance(semantic, dict) else None
    errors += _extra_key_errors("semantic.delta_e", delta_e, DELTA_E_KEYS)
    errors += _check_str_field("semantic.delta_e.overall", delta_e, "overall")

    physical = raw.get("physical")
    errors += _extra_key_errors("physical", physical, PHYSICAL_KEYS)
    errors += _check_int_field("physical.bpm", physical, "bpm")
    physical_str_fields = (
        "key",
        "time_signature",
        "active_rate_target",
        "valley_depth_target",
        "stereo_width",
    )
    for field in physical_str_fields:
        errors += _check_str_field(f"physical.{field}", physical, field)
    errors += _check_str_field("physical.brightness", physical, "brightness")
    errors += _check_enum_field("physical.brightness", physical, "brightness", BRIGHTNESS_ENUM)

    structure = raw.get("structure")
    if isinstance(structure, list):
        for index, section in enumerate(structure):
            prefix = f"structure[{index}]"
            errors += _extra_key_errors(prefix, section, STRUCTURE_SECTION_KEYS)
            errors += _check_str_field(f"{prefix}.section", section, "section")
            errors += _check_int_field(f"{prefix}.bars", section, "bars")
            errors += _check_str_field(f"{prefix}.role", section, "role")
            errors += _check_str_field(f"{prefix}.physical", section, "physical")

    rendering = raw.get("rendering")
    errors += _extra_key_errors("rendering", rendering, RENDERING_KEYS)
    errors += _check_str_field("rendering.target_backend", rendering, "target_backend")
    errors += _check_int_field("rendering.prompt_max_chars", rendering, "prompt_max_chars")
    errors += _check_list_str_field("rendering.priority", rendering, "priority")

    events = raw.get("events")
    if events is not None:
        errors += _extra_key_errors("events", events, EVENTS_KEYS)
        chord_progression = (
            events.get("chord_progression") if isinstance(events, dict) else None
        )
        if isinstance(chord_progression, list):
            for index, chord in enumerate(chord_progression):
                prefix = f"events.chord_progression[{index}]"
                errors += _extra_key_errors(prefix, chord, CHORD_KEYS)
                errors += _check_str_field(f"{prefix}.root", chord, "root")
                errors += _check_enum_field(f"{prefix}.root", chord, "root", CHORD_ROOT_ENUM)
                errors += _check_str_field(f"{prefix}.quality", chord, "quality")
                errors += _check_enum_field(
                    f"{prefix}.quality", chord, "quality", CHORD_QUALITY_ENUM
                )

    return sorted(errors, key=lambda error: (error["where"], error["message"]))


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
