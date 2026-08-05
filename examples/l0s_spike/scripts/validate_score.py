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

   - **Round 6** (this version): key/time_signature format, added after an
     empirical crash-family sweep (PR #245 Codex P2 review round 6). Round 2
     confirmed every *type*, but `contract.md` §1 also spells out a literal
     *format* for `physical.key` (`"<A-G>[#|b] <major|minor>"`, e.g.
     `"D minor"`) and `physical.time_signature` (`"4/4"`-style) that a
     type-only check (`str`) doesn't enforce — any str value passed through.
     A score authored with a format contract.md never sanctions (`"D
     dorian"`, `"H minor"`, `"waltz"`) still passed this gate, and
     `measure_round.py`'s later pipeline crashed with an *unstructured*
     traceback instead of a `{where, message}` gate error:
     `svp_rpe.perform.performer.parse_key` raises `ValueError` for a
     non-`major`/`minor` key, and `perform()` raises `ValueError` from
     `int(score.physical.time_signature.split("/", 1)[0])` for a
     non-`"N/M"` time signature (both confirmed by direct measurement — a
     `"D dorian"`/`"H minor"`/`"waltz"` Score reaches `measure_round.py`'s
     `svprpe roundtrip` subprocess call and crashes it with exit 1 and a raw
     traceback, never producing a report). This round adds format
     validation for exactly those two fields, empirically confirmed to
     crash — `active_rate_target`/`valley_depth_target` were also swept as
     crash-family candidates and confirmed *not* to crash (unused by
     `perform()`; `roundtrip`'s `values_match`/`parse_numeric_range` treats
     a malformed range string as "no numeric range" and falls back to a
     graceful string-equality comparison), so their format is deliberately
     left unvalidated here — see **Scope boundary** below.

   **Scope boundary**: this check enforces the L0-s v0 authoring contract's
   *public schema* exactly as `contract.md` §1 writes it — key sets, types,
   the two literal enumerations it names, and (round 6, narrowly) the two
   field *formats* empirically confirmed to crash `measure_round.py`'s
   pipeline downstream rather than fail gracefully
   (`physical.key`/`physical.time_signature`). It is not a general semantic
   linter: value plausibility beyond those — including
   `active_rate_target`/`valley_depth_target`'s contract-declared
   `"<lower>-<upper>"` range format (confirmed non-crashing, see round 6
   note above), whether `physical.bpm` is a *musically sensible* tempo, or
   whether `structure[].bars` is positive — is out of scope here. The
   crash/non-crash line is not principled from first read of the contract
   alone; it was determined empirically per field and belongs to a future
   `svprpe validate` / L0a symbolic-validation-gate freeze design to make
   exhaustive, not this spike-era runner script.

   Every violation found anywhere in the tree is reported (not just the
   first), as a single deterministic, sorted list, whether or not canonical
   validation would separately reject the same document.
2. **Canonical schema validation**
   (`svp_rpe.compose.loader.load_composition_score`'s underlying model).

Both checks always run — a public-scope failure does not short-circuit
canonical validation — and `status` is `fail` if either produced an error;
`errors` concatenates public-scope errors (sorted) followed by canonical
validation errors (in the model's own deterministic `loc` order).

**Output-collision guard** (PR #245 Codex P2 review, round 3 — mirrors the
same rules `measure_round.py` enforces for its own `-o`, since both scripts
write into the same protected spike tree): before anything is written,
`-o`/`--output` is rejected if it resolves to the `score` positional
argument itself (an input this run reads), or if it resolves inside the
protected spike tree (`examples/l0s_spike/`, this script's own grandparent
directory) *and* already exists there — pin-recorded `validation.json`
files under `rounds/<n>/` must never be silently overwritten. A brand-new
file inside the protected tree is still the normal accept-a-round flow and
stays allowed; outside the tree (e.g. `build/l0s/...`) output stays
unrestricted, as before.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from svp_rpe.compose.models import CompositionScore

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SPIKE_DIR = _SCRIPTS_DIR.parent

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

# Round 6 (PR #245 Codex P2 review): contract.md §1's literal format for the
# two fields empirically confirmed to crash measure_round.py's downstream
# pipeline (svp_rpe.perform.performer.parse_key / the int(time_signature
# split) call) rather than fail gracefully — see the module docstring's
# round-6 note for the crash-family sweep this is scoped to.
KEY_FORMAT_PATTERN = re.compile(r"^[A-Ga-g][#b]? (major|minor)$")
TIME_SIGNATURE_FORMAT_PATTERN = re.compile(r"^[0-9]+/[0-9]+$")

OFF_CONTRACT_KEY_MESSAGE = (
    "contract-forbidden key (L0-s public schema; recorded as off-contract self-edit)"
)
STR_TYPE_MESSAGE = "must be str per L0-s public schema (contract.md §1)"
INT_TYPE_MESSAGE = (
    "must be int per L0-s public schema (contract.md §1); numeric strings and "
    "TODO sentinels are rejected even though canonical CompositionScore coerces them"
)
LIST_STR_TYPE_MESSAGE = "must be a list of str per L0-s public schema (contract.md §1)"
KEY_FORMAT_MESSAGE = (
    'must match the contract key format "<A-G>[#|b] <major|minor>" '
    '(e.g. "D minor", "F# major") per L0-s public schema (contract.md §1) — '
    "confirmed to crash measure_round.py's pipeline (parse_key) otherwise"
)
TIME_SIGNATURE_FORMAT_MESSAGE = (
    'must match the contract time signature format "<N>/<M>" (e.g. "4/4") per '
    "L0-s public schema (contract.md §1) — confirmed to crash "
    "measure_round.py's pipeline (int() on the beats-per-bar component) otherwise"
)


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


def _check_format_field(
    where: str, container: Any, key: str, pattern: re.Pattern[str], message: str
) -> list[dict[str, str]]:
    """Only fires once the field is already confirmed `str` (same
    non-duplication rule as `_check_enum_field`). Round 6: the crash-family
    fields (`physical.key`/`physical.time_signature`) empirically confirmed
    to reach an unstructured downstream crash under a merely str-typed but
    off-format value — see module docstring."""
    if not isinstance(container, dict) or key not in container:
        return []
    value = container[key]
    if not isinstance(value, str) or pattern.fullmatch(value) is not None:
        return []
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
    errors += _check_format_field(
        "physical.key", physical, "key", KEY_FORMAT_PATTERN, KEY_FORMAT_MESSAGE
    )
    errors += _check_format_field(
        "physical.time_signature",
        physical,
        "time_signature",
        TIME_SIGNATURE_FORMAT_PATTERN,
        TIME_SIGNATURE_FORMAT_MESSAGE,
    )

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


class ProtectedPathError(RuntimeError):
    """`-o` would clobber a pipeline input or protected L0-s spike evidence —
    refused before anything is written (PR #245 Codex P2 review, round 3)."""


def _reject_output_collision(output_path: Path, *, score_path: Path) -> Path:
    resolved_output = output_path.resolve()
    resolved_score = score_path.resolve()

    if resolved_output == resolved_score:
        raise ProtectedPathError(
            f"-o/--output must not resolve to the score.yaml input path itself "
            f"(got {resolved_output}) — writing the report there would clobber "
            "the input this run reads."
        )

    inside_spike_tree = resolved_output == _SPIKE_DIR or resolved_output.is_relative_to(
        _SPIKE_DIR
    )
    if inside_spike_tree and resolved_output.exists():
        raise ProtectedPathError(
            f"-o/--output resolves inside the protected L0-s spike tree {_SPIKE_DIR} "
            f"and already exists ({resolved_output}) — refusing to silently "
            "overwrite pin-recorded round evidence. A brand-new validation.json "
            "under rounds/<n>/ is the normal accept-a-round flow; an existing one "
            "there means this round was already validated. Delete/rename it first "
            "if this is an intentional re-run, or write to a scratch path instead."
        )
    return resolved_output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L0-s symbolic validation gate")
    parser.add_argument("score", type=Path, help="Path to score.yaml")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output validation.json")
    args = parser.parse_args(argv)

    try:
        _reject_output_collision(args.output, score_path=args.score)
    except ProtectedPathError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    result = _validate(args.score)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"status={result['status']} -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
