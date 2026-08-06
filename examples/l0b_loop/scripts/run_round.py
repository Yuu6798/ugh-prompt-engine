"""L0b per-round runner (`docs/llm_adapter_planning.md` §4「L0b」/
`examples/l0b_loop/task.md`).

`run_round.py <score.yaml> --round N --workdir <dir> -o <report.json>` runs
the full L0b pipeline for one candidate `score.yaml` and writes an
`AuthoringDiffReport` (`src/svp_rpe/authoring/report.py`) — L0a's frozen
report normal form — to `-o`.

**T2 (`examples/l0b_loop/task_t2.md`)**: the canonical section map is the
only judge-side difference between L0B-T1 and L0B-T2 (task_t2.md's 判定器
節). `--section-map` selects it (default = T1's `frozen/section_map.json`,
so T1's behavior — CLI invocations, `run_round()`/`main()` callers that don't
pass the new parameter, and the identity manifest's structure anchor — is
byte-for-byte unchanged). Passing `frozen/section_map_t2.json` swaps the
canonical requirement `_structure_axis` reads and the fixture copied into
`identity/section_map.json`, and adds the selected map to the output-collision
guard's protected set alongside the other fixed inputs.

Pipeline (task.md's 判定器 table):

(a) **Symbolic gate**: `svprpe validate <score> --contract
    config/authoring_contract_l0.yaml -o <workdir>/validation.json` (L0a CLI,
    subprocess). Exit code `0`/`1` is a normal pass/fail record (not a script
    error — a `fail` `SymbolicValidationResult` is expected, ordinary output);
    any other exit code is an operational error and raises. On `fail`, this
    script writes an `AuthoringDiffReport` with the failing
    `symbolic_validation` and empty `axes`/`notes` (the schema's own
    provenance guard, `docs/l0a_authoring_contract.md` (d).6, requires this
    shape for a failed gate) straight to `-o` and returns — no audio is
    produced.
(b) **On pass**: runs the same observation chain
    `examples/l0s_spike/scripts/measure_round.py` uses (eval copy with the
    frozen `control_profile` injected, `svprpe roundtrip`, `svprpe
    score-adherence`, deterministic render via `svp_rpe.perform`, `svprpe
    package`, `svprpe observe`) — that script is a frozen historical artifact
    (CLAUDE.md 凍結成果物ポリシー) so its logic is transcribed here rather
    than imported, reading L0b's own `frozen/` copies (pinned separately —
    `ledger.yaml`) instead of `examples/l0s_spike/frozen/`.
(c) **Boundary-second live wiring** (task.md's central new requirement over
    L0-s): `svprpe observe`'s structure anchor `measurements` carries a
    *label* sequence only (`observed_sections`), no timing — `svp_rpe.
    arrange.observe._observe_structure` derives that label list from
    `RPEBundle.physical.structure` (a list of `svp_rpe.rpe.models.
    SectionMarker`, which *does* carry `start_sec`/`end_sec`) but discards
    the timing before it reaches the anchor's `measurements` dict. Rather
    than changing `arrange/observe.py`'s schema (out of scope per the design
    brief — the existing API already exposes what's needed), this script
    calls `svp_rpe.rpe.extractor.extract_rpe_from_file` directly on the same
    `take.wav` bytes `svprpe observe` measured, and zips its
    `RPEBundle.physical.structure` markers (same deterministic extraction,
    same audio, same order/count as observe's own internal call) against the
    normalized label list `svprpe observe` already computed, to populate
    `AuthoringDiffReport.axes["structure"].observed_sections`.
(d) **Verdicts**: `key`/`brightness` = `preserved` iff `band == "measured"`
    (diagnosis != `sensor_blind`) *and* the observed value matches this
    task's frozen requirement (`REQUIREMENT_KEY` via
    `svp_rpe.keys.keys_enharmonically_equal`, `REQUIREMENT_BRIGHTNESS` via
    plain string equality) — mirrors `measure_round.py`'s
    compare-to-requirement fix (not diagnosis-self-consistency). `structure`
    = `exact_match` iff the AR4 sensor's own `sequence_exact_match`
    (`adherence_status == "preserved"`) says so.

**Output-collision guard**: mirrors `measure_round.py`'s guard rules exactly
(same rationale — a rejected invocation must leave the filesystem untouched),
scoped to `examples/l0b_loop/` as the protected tree instead of
`examples/l0s_spike/`. `_reserved_workdir_paths` is the single source of
truth both the preflight guard and the actual read/write steps derive from.

**Reserved-path symlink-escape guard** (Codex review round 5, P1): a reused
scratch `--workdir` is the normal path (task.md's iterate-on-rounds flow) —
but if one of `_reserved_workdir_paths`' reserved names (`score.yaml`,
`identity/`, etc.) has been replaced with a *symlink*, this run's own writes
would follow it and could truncate/overwrite whatever it points at, up to
and including pin-recorded evidence outside `--workdir` entirely (e.g.
`examples/l0b_loop/`'s own `frozen/`). `_reject_escaping_reserved_paths()`
checks every reserved path (and each existing intermediate path component
between `--workdir` and it) for exactly this before any write happens: a
symlink anywhere in that chain is refused outright, and — as a second,
independent check — any existing component whose `resolve()` lands outside
the resolved `--workdir` is refused too, even if nothing in the chain is
literally a symlink (e.g. a component only reachable via a symlinked
ancestor of `--workdir` itself). Nonexistent components pass (the ordinary
first-run shape, where `run_round()` creates every reserved path fresh); an
ordinary reuse of leftover real files/dirs from a prior round also passes
unchanged — nothing about a normal `run_round()` invocation ever creates a
symlink under a reserved name, so this guard only ever fires against a
tampered/adversarial workdir.

**Atomic reserved-path writes** (Codex review round 6, P1): every reserved
workdir path this script itself writes directly (`score.yaml`,
`eval_score.yaml`, `take.wav`, `identity/section_map.json`,
`identity_manifest.yaml` — everything `_publish_report_bundle` does not
already cover) now goes through `svp_rpe.utils.atomic_io.atomic_write_bytes`
(tempfile in the same directory + `os.replace`) instead of a plain
`Path.write_bytes`. This closes a hazard the symlink-escape guard above
cannot: a reused `--workdir` whose reserved name is a *hard* link (not a
symlink) to some other pin-recorded file shares that file's inode, so a
plain in-place `write_bytes` would truncate and overwrite the *other* file's
content too, in-place, through the shared inode — `Path.is_symlink()` never
sees a hard link (it is, by construction, indistinguishable from an
ordinary file at the filesystem level), so no lexical guard can catch this
case. `os.replace` sidesteps the whole problem structurally rather than
detecting it: it unlinks the reserved name from whatever inode it
previously pointed at (shared or not) and rebinds the name to a brand-new
inode holding this run's freshly staged bytes, so the old inode — and
whatever *other* path still points at it — is left completely untouched.
Normal-path output bytes are unchanged by this switch (same content, same
final path) — the T1/T2 slow smoke tests below (`test_positive_control_
round_trip_reproduces_pinned_report` / `..._t2_...`) are the byte-for-byte
proof.

**Preflight judge-input drift check** (`_reject_judge_input_drift`, F5,
Codex review round 6, P1): the guards above all police *where* this run
writes; this one polices whether the fixed inputs this run *reads* are
still the ones L0b's evidence is pinned against. `_FIXED_INPUT_SHA256` pins
the sha256 of every fixed judge-side input `run_round()` reads (the four
inputs read on every invocation, plus whichever `--section-map` this
invocation selected), machine-derived from the same files
`examples/l0b_loop/ledger.yaml`'s `pinned_inputs`/`t2.pinned_inputs` already
pin (same values, same source files — this table exists only so
`run_round.py` itself can check byte fidelity at run time, not to introduce
a second, independent source of truth). Runs after every other preflight
guard and before `workdir.mkdir()` — a mismatch means the judge itself has
drifted (accidental edit, wrong checkout, a stale copy), so the run is
refused before writing anything rather than silently producing an
evidence-shaped report measured against a different judge than the one this
experiment's evidence assumes. If an engine change legitimately updates one
of these files, `_FIXED_INPUT_SHA256` must be updated to match in the same
change — this table's whole purpose is to *detect* drift, not to freeze the
files in place forever.

**G1 — unpinned `--section-map` rejection** (`_reject_unpinned_section_map`,
Codex review round 7, P1): before F5's hash check even runs,
`--section-map` must resolve to one of exactly the two pinned section maps
(`SECTION_MAP_PATH` T1 / `SECTION_MAP_T2_PATH` T2). Without this guard, a
`--section-map` pointing anywhere else fell outside
`_selected_fixed_inputs`'s two-entry selection and F5's hash-fidelity loop
silently skipped it entirely — the structure axis would then be judged
against an arbitrary, unverified section map with no drift protection at
all (a run against a pin-external judge input is itself an unpinned judge).
Runs immediately before `_reject_judge_input_drift`, so two-path membership
is enforced unconditionally, independent of and prior to the hash check.

**G2 — judge-input snapshot-feed** (Codex review round 7, P1): closes a
verify-then-reread TOCTOU F5's hash check left open — F5 verified each
fixed input's bytes *at preflight time*, but every downstream consumer
(the `svprpe validate --contract` subprocess, `svprpe package`'s
`arrangement`/`--capability-profile` subprocess arguments, `_prepare_scores`'s
`control_profile` injection, `_write_identity_manifest`/`_structure_axis`'s
section-map requirement) used to re-read the same source paths later in the
run, arbitrarily long after the preflight check passed. `_reject_judge_input_drift`
now returns the exact bytes it read and hashed for each fixed input
(`dict[label, bytes]`); `run_round()` threads those same verified bytes to
every consumer instead of letting any of them re-open the source path.
Consumers that are themselves subprocess CLI invocations (`validate`'s
`--contract`, `package`'s positional arrangement arg and
`--capability-profile`) cannot take bytes directly — for those,
`run_round()` stages the verified bytes into reserved workdir copies under
`<workdir>/judge_inputs/` (`atomic_write_bytes`, so publish is atomic same
as every other reserved-path write) and points the subprocess at the copy
instead of the original config/frozen path. `eval_control_profile` and the
section map are consumed in-process (not via a subprocess file argument) and
are fed the verified bytes directly, no physical copy needed. The three new
`judge_inputs/*` copy paths are ordinary entries in `_reserved_workdir_paths`,
so the existing output-collision guard and reserved-path symlink-escape
guard protect them the same way they protect every other reserved workdir
artifact, with no separate guard logic required. Normal-path final artifacts
(`report.json`/`take.wav`/`eval_score.yaml`/`hashes.json`) are unchanged
byte-for-byte by this change — the T1/T2 slow smoke tests below prove it —
since the verified bytes are, by F5's own hash check, identical to what a
fresh re-read would have produced on an undrifted judge; only the *read
path* changes, not the content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

import yaml

from svp_rpe.authoring.report import (
    AuthoringDiffReport,
    AuthoringNote,
    AxisReport,
    ObservedSection,
    dump_json_bytes,
)
from svp_rpe.authoring.validate import SymbolicValidationResult
from svp_rpe.compose.loader import load_composition_score
from svp_rpe.keys import keys_enharmonically_equal
from svp_rpe.perform import FAITHFUL_TAKE, perform, wav_bytes
from svp_rpe.rpe.extractor import extract_rpe_from_file
from svp_rpe.utils.atomic_io import atomic_write_bytes

_SCRIPTS_DIR = Path(__file__).resolve().parent
_LOOP_DIR = _SCRIPTS_DIR.parent
_REPO_ROOT = _LOOP_DIR.parents[1]
_FROZEN_DIR = _LOOP_DIR / "frozen"

SECTION_MAP_PATH = _FROZEN_DIR / "section_map.json"
SECTION_MAP_T2_PATH = _FROZEN_DIR / "section_map_t2.json"
EVAL_CONTROL_PROFILE_PATH = _FROZEN_DIR / "eval_control_profile.yaml"
ARRANGEMENT_PATH = _FROZEN_DIR / "arrangement.yaml"
CAPABILITY_PROFILE_PATH = _REPO_ROOT / "config" / "capability_profiles" / "suno.yaml"
CONTRACT_PATH = _REPO_ROOT / "config" / "authoring_contract_l0.yaml"

# Fixed-path inputs this module always reads, regardless of round — an `-o`
# aliasing any of these would have a write step clobber a pipeline input
# (mirrors measure_round.py's `_FIXED_INPUT_PATHS`). `SECTION_MAP_PATH` is the
# default (T1); `_fixed_input_paths()` substitutes the selected
# `--section-map` in its place so the guard always protects the map actually
# in use for a given run, not just the default.
_FIXED_INPUT_PATHS = (
    SECTION_MAP_PATH,
    EVAL_CONTROL_PROFILE_PATH,
    ARRANGEMENT_PATH,
    CAPABILITY_PROFILE_PATH,
    CONTRACT_PATH,
)


def _fixed_input_paths(section_map_path: Path) -> tuple[Path, ...]:
    return (
        section_map_path,
        EVAL_CONTROL_PROFILE_PATH,
        ARRANGEMENT_PATH,
        CAPABILITY_PROFILE_PATH,
        CONTRACT_PATH,
    )


# F5 (Codex review round 6, P1): frozen judge-side input byte-fidelity pins
# — see module docstring's "Preflight judge-input drift check" section for
# the full rationale. Machine-derived (sha256 of each file's current bytes,
# not hand-transcribed) and identical in value to
# `examples/l0b_loop/ledger.yaml`'s `pinned_inputs`/`t2.pinned_inputs`
# entries for the same six files (same source files, same digest — this
# table exists only so `run_round.py` itself can check byte fidelity at run
# time, not to introduce a second, independent source of truth). If an
# engine change legitimately updates one of these files, this table must be
# synced to match in the same change — drift detection is the entire point,
# not a hard freeze enforced by this code.
_FIXED_INPUT_SHA256: dict[str, tuple[Path, str]] = {
    "section_map": (
        SECTION_MAP_PATH,
        "a7d4330c06137117474f19ab3fae27204e7df9a00deac72cba9a0299296a83fd",
    ),
    "section_map_t2": (
        SECTION_MAP_T2_PATH,
        "7add33ba142174c5e2ee002d39af9abb2ebdc65858aed2b05564f9e8ce00d830",
    ),
    "eval_control_profile": (
        EVAL_CONTROL_PROFILE_PATH,
        "a5eb1847a54032c49b865a8a6834bde597c5d3ddcdf8784d05d3cce3b7e76c1e",
    ),
    "arrangement": (
        ARRANGEMENT_PATH,
        "efa0d6db0e288ad74c330a719b28e6db224c4545b852bdbbf62bc7adeda96e5a",
    ),
    "suno_capability_profile": (
        CAPABILITY_PROFILE_PATH,
        "343f3e84bb7e2bb4f4c945195659b3823318bf14c306774278a33ee7d116592b",
    ),
    "authoring_contract_l0": (
        CONTRACT_PATH,
        "f34479d9683667c343179fdeef435ea02f59fb843ff62870be815511a281f8c6",
    ),
}

_SECTION_MAP_LABELS = ("section_map", "section_map_t2")


def _selected_fixed_inputs(section_map_path: Path) -> dict[str, Path]:
    """The fixed judge-side inputs a `run_round()` invocation actually
    reads, keyed the same way as `_FIXED_INPUT_SHA256`: always the four
    non-section-map entries, plus whichever of `_FIXED_INPUT_SHA256`'s two
    section-map entries `section_map_path` resolves to (T1's default or a
    `--section-map` override) — the other section-map slot is never read
    this run and is therefore excluded. A `section_map_path` that resolves
    to neither pinned section map (a path outside this table's two frozen
    entries) is likewise excluded — this table only pins the two frozen
    maps this experiment currently knows about."""
    selected: dict[str, Path] = {
        label: path
        for label, (path, _sha256) in _FIXED_INPUT_SHA256.items()
        if label not in _SECTION_MAP_LABELS
    }
    resolved_section_map_path = section_map_path.resolve()
    for label in _SECTION_MAP_LABELS:
        pinned_path, _sha256 = _FIXED_INPUT_SHA256[label]
        if pinned_path.resolve() == resolved_section_map_path:
            selected[label] = pinned_path
            break
    return selected


_PINNED_SECTION_MAP_PATHS: tuple[Path, ...] = (SECTION_MAP_PATH, SECTION_MAP_T2_PATH)


def _reject_unpinned_section_map(section_map_path: Path) -> None:
    """G1 preflight guard (Codex review round 7, P1) — see module
    docstring's "G1 — unpinned `--section-map` rejection" section for the
    full rationale. `--section-map` must resolve to exactly one of the two
    pinned section maps (`SECTION_MAP_PATH` T1 / `SECTION_MAP_T2_PATH` T2);
    anything else is refused with `JudgeInputDriftError` (a
    `ProtectedPathError` — same family `main()`'s existing
    `except ProtectedPathError` already catches) before F5's hash check
    below ever runs, so a `--section-map` outside the two pinned paths can
    never silently fall through `_selected_fixed_inputs`'s selection and
    skip drift protection entirely."""
    resolved = section_map_path.resolve()
    pinned_resolved = {path.resolve() for path in _PINNED_SECTION_MAP_PATHS}
    if resolved not in pinned_resolved:
        raise JudgeInputDriftError(
            f"--section-map ({resolved}) is not one of the pinned section maps L0b's "
            f"judge is defined against ({SECTION_MAP_PATH} for T1, {SECTION_MAP_T2_PATH} "
            "for T2) — refusing to run the structure axis against an unpinned section "
            "map: running against a pin-external judge input is itself an unpinned "
            "judge, with no drift protection at all. Pass "
            "--section-map frozen/section_map.json (T1, the default) or "
            "frozen/section_map_t2.json (T2)."
        )


def _reject_judge_input_drift(section_map_path: Path) -> dict[str, bytes]:
    """Preflight byte-fidelity check (F5) — see module docstring's
    "Preflight judge-input drift check" section for the full rationale.
    Raises `JudgeInputDriftError` (a `ProtectedPathError` — refused before
    any write, same family as this module's other preflight guards) naming
    the drifted input, its actual sha256, and the pinned one it no longer
    matches.

    Returns the exact bytes read (and verified) for each fixed input, keyed
    the same way as `_selected_fixed_inputs` (G2 snapshot-feed, Codex
    review round 7, P1 — see module docstring's "G2 — judge-input
    snapshot-feed" section). `run_round()` threads these bytes to every
    downstream consumer instead of letting any of them re-read the source
    path later, closing the verify-then-reread TOCTOU a fresh re-read would
    otherwise leave open."""
    verified: dict[str, bytes] = {}
    for label, path in _selected_fixed_inputs(section_map_path).items():
        data = path.read_bytes()
        actual_sha256 = _sha256_bytes(data)
        expected_sha256 = _FIXED_INPUT_SHA256[label][1]
        if actual_sha256 != expected_sha256:
            raise JudgeInputDriftError(
                f"judge input drift: {label} ({path}) sha256 {actual_sha256} != "
                f"pinned {expected_sha256} — this fixed judge-side input no longer "
                "matches the frozen judge L0b's evidence is pinned against "
                "(_FIXED_INPUT_SHA256 in run_round.py); refusing to run rather than "
                "silently measuring a round against a different judge than the one "
                "this experiment's evidence assumes. If this input was legitimately "
                "updated, _FIXED_INPUT_SHA256 must be synced to match."
            )
        verified[label] = data
    return verified


# Frozen judge (task.md judgement table) — deliberately not read back from the
# submitted score.yaml itself: the requirement is the task's fixed target,
# the "observed" side of the report is what got measured.
REQUIREMENT_KEY = "D minor"
REQUIREMENT_BRIGHTNESS = "dark"

_CLI_TIMEOUT_SECONDS = 300


class ProtectedPathError(RuntimeError):
    """A `--workdir`/`-o` path would clobber protected L0b evidence or a
    pipeline input — refused before anything is written."""


class JudgeInputDriftError(ProtectedPathError):
    """A fixed judge-side input's on-disk bytes no longer match its pinned
    sha256 (`_FIXED_INPUT_SHA256`) — refused before `workdir.mkdir()` or any
    other write (F5, module docstring's "Preflight judge-input drift check"
    section). A `ProtectedPathError` subclass so `main()`'s existing
    `except ProtectedPathError` handling catches it unchanged."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _inside_loop_tree(path: Path) -> bool:
    return path == _LOOP_DIR or path.is_relative_to(_LOOP_DIR)


def _reserved_workdir_paths(workdir: Path) -> dict[str, Path]:
    """Single source of truth for every path this run writes to (or reads
    back from) inside `workdir`. Both the preflight collision guard and the
    actual read/write steps in `run_round()` derive their workdir-relative
    paths from this same mapping (mirrors `measure_round.py`'s
    `_reserved_workdir_paths`). Excludes the report `-o` target itself:
    that's the one workdir-relative artifact this run is meant to create
    fresh, not a reserved one.
    """
    identity_dir = workdir / "identity"
    package_dir = workdir / "package"
    judge_inputs_dir = workdir / "judge_inputs"
    return {
        "score_copy": workdir / "score.yaml",
        "validation": workdir / "validation.json",
        "eval_score": workdir / "eval_score.yaml",
        "roundtrip": workdir / "roundtrip.json",
        "adherence": workdir / "adherence.json",
        "take_wav": workdir / "take.wav",
        "identity_dir": identity_dir,
        "identity_section_map": identity_dir / "section_map.json",
        "identity_manifest": workdir / "identity_manifest.yaml",
        "package_dir": package_dir,
        "package_json": package_dir / "performance_package.json",
        "package_compilation_report": package_dir / "compilation_report.json",
        "observe_report": workdir / "observe_report.json",
        "hashes": workdir / "hashes.json",
        # G2 snapshot-feed (Codex review round 7, P1): reserved workdir
        # copies of the fixed judge-side inputs a subprocess CLI reads by
        # path — staged from `_reject_judge_input_drift`'s already-verified
        # bytes so the subprocess reads exactly what preflight checked,
        # never a fresh (and therefore TOCTOU-able) re-read of the original
        # config/frozen path. Ordinary entries in this mapping, so the
        # output-collision guard and the reserved-path symlink-escape guard
        # protect them automatically.
        "judge_inputs_dir": judge_inputs_dir,
        "judge_contract": judge_inputs_dir / "authoring_contract_l0.yaml",
        "judge_arrangement": judge_inputs_dir / "arrangement.yaml",
        "judge_capability_profile": judge_inputs_dir / "capability_profile.yaml",
    }


def _reject_workdir_inside_loop_tree(workdir: Path) -> Path:
    resolved = workdir.resolve()
    if _inside_loop_tree(resolved):
        raise ProtectedPathError(
            f"--workdir must not resolve inside the protected L0b loop tree "
            f"{_LOOP_DIR} (got {resolved}). That tree holds pin-recorded evidence "
            "(e.g. positive_control/report.json, ledger.yaml) that a scratch workdir's "
            "own internal writes would silently overwrite. Use a scratch directory "
            "outside the loop tree, e.g. build/l0b/<name>/."
        )
    return resolved


def _reject_score_copy_self_collision(score_path: Path, reserved_paths: dict[str, Path]) -> None:
    resolved_score = score_path.resolve()
    resolved_copy_dest = reserved_paths["score_copy"].resolve()
    if resolved_score == resolved_copy_dest:
        raise ProtectedPathError(
            f"score.yaml argument ({resolved_score}) resolves to the exact path "
            f"run_round.py would copy it to inside --workdir ({resolved_copy_dest}) — "
            "refusing a self-overwriting copy. Use a --workdir that does not already "
            "contain the input under the name 'score.yaml'."
        )


def _reject_escaping_reserved_paths(workdir: Path, reserved_paths: dict[str, Path]) -> None:
    """Rejects a `--workdir` whose reserved path names (`_reserved_workdir_paths`'
    values — `score.yaml`, `identity/`, etc.) have been replaced with a symlink,
    or otherwise resolve outside `workdir` — see module docstring's "Reserved-path
    symlink-escape guard" section for the full rationale. For every reserved
    path, walks the chain of path components from `workdir` down to it
    (inclusive) and, for each component that already exists on disk:

    (a) refuses if the component itself is a symlink (`Path.is_symlink()` —
        true even for a *broken* symlink, which `Path.exists()` alone would
        miss, since a broken symlink's target does not exist but the link
        itself does); and
    (b) refuses if the component's `resolve()`d location is not `workdir`
        itself or a path under it — a second, independent check for the case
        where nothing in this specific chain is itself a symlink but an
        ancestor of `workdir` is (so `workdir.resolve()` already differs from
        `workdir`, and every "ordinary" descendant would silently resolve
        outside the workdir the caller thinks it is writing into).

    A component that does not exist yet is skipped (nothing to escape
    through) — this is the ordinary first-run shape, where `run_round()`
    itself creates every reserved path fresh, and the ordinary reused-workdir
    shape (leftover real files/dirs from a prior round) passes unchanged."""
    resolved_workdir = workdir.resolve()
    for label, reserved_path in reserved_paths.items():
        try:
            relative = reserved_path.relative_to(workdir)
        except ValueError:
            # Every entry `_reserved_workdir_paths` produces is built as
            # `workdir / ...`, so this should be unreachable — skip rather
            # than raise on a path this guard was never meant to police.
            continue
        cumulative = workdir
        for part in relative.parts:
            cumulative = cumulative / part
            if cumulative.is_symlink():
                raise ProtectedPathError(
                    f"reserved workdir path {label!r} ({reserved_path}) has a symlink "
                    f"at {cumulative} — refusing to read/write through a reserved name "
                    "that has been replaced with a symlink (it could redirect this "
                    "run's writes outside --workdir, including onto pin-recorded "
                    "evidence)."
                )
            if cumulative.exists():
                resolved_cumulative = cumulative.resolve()
                if resolved_cumulative != resolved_workdir and not resolved_cumulative.is_relative_to(
                    resolved_workdir
                ):
                    raise ProtectedPathError(
                        f"reserved workdir path {label!r} ({reserved_path}) resolves to "
                        f"{resolved_cumulative}, outside --workdir ({resolved_workdir}) — "
                        "refusing a read/write that would escape the workdir."
                    )


def _reject_output_collision(
    output_path: Path,
    *,
    score_path: Path,
    reserved_paths: Iterable[Path],
    fixed_input_paths: Iterable[Path] = _FIXED_INPUT_PATHS,
) -> Path:
    resolved_output = output_path.resolve()

    protected = {score_path.resolve(), *(p.resolve() for p in fixed_input_paths)}
    protected |= {p.resolve() for p in reserved_paths}
    if resolved_output in protected:
        raise ProtectedPathError(
            f"-o/--output must not resolve to a pipeline input path or a reserved "
            f"workdir artifact path (got {resolved_output}) — writing the report "
            "there would clobber an input this run reads, or a generated artifact "
            "this run writes and later hashes into hashes.json."
        )

    if _inside_loop_tree(resolved_output) and resolved_output.exists():
        raise ProtectedPathError(
            f"-o/--output resolves inside the protected L0b loop tree {_LOOP_DIR} "
            f"and already exists ({resolved_output}) — refusing to silently "
            "overwrite pin-recorded evidence. A brand-new report.json is the normal "
            "accept-a-round flow; an existing one there means this round was already "
            "measured. Delete/rename it first if this is an intentional re-run, or "
            "write to a scratch path instead."
        )
    return resolved_output


def _run_cli(args: list[str]) -> None:
    result = subprocess.run(
        ["svprpe", *args],
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"svprpe {' '.join(args)} failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


def _run_validate_cli(score_path: Path, output_path: Path, contract_path: Path) -> None:
    """Runs the L0a symbolic gate. Exit `0` (pass) and `1` (fail record) are
    both normal outcomes — `svprpe validate` always writes `output_path` in
    either case (`src/svp_rpe/cli/validate_cmd.py`'s docstring: exit codes
    `0`/`1` bracket the *result*, `2` is reserved for an operational error
    that never reaches the point of computing/writing a result at all).

    `contract_path` is the reserved workdir copy (`paths["judge_contract"]`)
    of `CONTRACT_PATH`'s already-verified bytes (G2 snapshot-feed), not
    `CONTRACT_PATH` itself — `run_round()` passes the copy so this
    subprocess reads exactly the bytes preflight verified."""
    result = subprocess.run(
        ["svprpe", "validate", str(score_path), "--contract", str(contract_path), "-o", str(output_path)],
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"svprpe validate {score_path} --contract {contract_path} failed with an "
            f"operational error (exit {result.returncode}), not a pass/fail result\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


def _prepare_scores(
    score_path: Path,
    score_bytes: bytes,
    paths: dict[str, Path],
    eval_control_profile_bytes: bytes,
) -> tuple[Path, str]:
    """Writes the eval copy (`control_profile` injected from the frozen axis
    table) into `workdir`, from the already-read `score_bytes` snapshot —
    the caller (`run_round()`) reads `score.yaml` exactly once per run and
    passes those same bytes here (and into the `score_copy` write, the
    symbolic gate, and `hashes["score"]`); this function does not re-read
    `score_path` from disk itself (`score_path` is kept only to name the
    file in the `ValueError` message below). `eval_control_profile_bytes` is
    likewise `_reject_judge_input_drift`'s already-verified snapshot of
    `EVAL_CONTROL_PROFILE_PATH` (G2 snapshot-feed) — this function does not
    re-read that path itself either. Returns `(eval_score_path,
    eval_score_sha256)` — the submitted score's own hash
    (`hashes["score"]`/the identity manifest's `source.sha256`) is derived
    by the caller from the same `score_bytes`, not re-derived here."""
    data = yaml.safe_load(score_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"score.yaml must be a mapping: {score_path}")
    control_profile = yaml.safe_load(eval_control_profile_bytes.decode("utf-8"))
    data["control_profile"] = control_profile

    eval_bytes = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    eval_score_path = paths["eval_score"]
    atomic_write_bytes(eval_score_path, eval_bytes)
    eval_score_sha256 = _sha256_bytes(eval_bytes)

    return eval_score_path, eval_score_sha256


def _write_identity_manifest(
    paths: dict[str, Path],
    *,
    score_sha256: str,
    round_number: int,
    section_map_bytes: bytes,
) -> tuple[Path, str]:
    """Deterministically generates `<workdir>/identity_manifest.yaml`.
    Copies `section_map_bytes` (T1: `frozen/section_map.json`'s bytes, T2:
    `frozen/section_map_t2.json`'s bytes — read exactly once by
    `run_round()` and shared with `_structure_axis`'s requirement
    resolution, not re-read here) into `<workdir>/identity/` —
    `IdentityManifest` artifact locators must resolve inside the manifest's
    own directory (path confinement), so the frozen fixture can't be
    referenced in place."""
    identity_dir = paths["identity_dir"]
    identity_dir.mkdir(exist_ok=True)
    section_map_sha256 = _sha256_bytes(section_map_bytes)
    atomic_write_bytes(paths["identity_section_map"], section_map_bytes)

    manifest_data: dict[str, Any] = {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": "l0b-loop", "version": "0.1"},
        "source": {
            "locator": "score.yaml",
            "sha256": score_sha256,
            "rights_basis": "original",
            "note": (
                f"L0b loop round {round_number}: pins the submitted score.yaml as "
                "authored (pre control_profile injection; eval_score.yaml, the copy "
                "everything downstream measures, is a derived artifact, not the "
                "pinned source)."
            ),
        },
        "anchors": [
            {
                "id": "structure",
                "domain": "structure",
                "artifact": "identity/section_map.json",
                "artifact_type": "section_map",
                "media_type": "application/json",
                "format_version": "section-map/0.1",
                "sha256": section_map_sha256,
                # required: false — frozen/arrangement.yaml is a passthrough
                # spec with no identity_anchors preservation policy;
                # build_preservation_contract only demands a policy entry
                # for required=True anchors (arrange/contract.py).
                "required": False,
            }
        ],
    }
    manifest_bytes = yaml.safe_dump(
        manifest_data, sort_keys=False, allow_unicode=True
    ).encode("utf-8")
    manifest_path = paths["identity_manifest"]
    atomic_write_bytes(manifest_path, manifest_bytes)
    return manifest_path, _sha256_bytes(manifest_bytes)


def _band_from_diagnosis(diagnosis: str) -> str:
    """`diagnosis` decides *band* only: `sensor_blind` means the value was
    never measured, so it's ineligible for a verdict regardless of what it
    happens to equal. Every other diagnosis (`preserved`/`knob_dead`/
    `calibration_disagreement`) means a working sensor produced a real
    observed value — `measured` — whether or not that value satisfies this
    task's frozen requirement is a separate question `_key_axis`/
    `_brightness_axis` answer themselves (mirrors
    `measure_round.py`'s `_band_from_diagnosis`)."""
    return "not_observed" if diagnosis == "sensor_blind" else "measured"


def _key_axis(field: dict[str, Any]) -> AxisReport:
    band = _band_from_diagnosis(field["diagnosis"])
    observed = field["transcribed_value"]
    matches = band == "measured" and keys_enharmonically_equal(REQUIREMENT_KEY, observed)
    return AxisReport(
        requirement=REQUIREMENT_KEY,
        observed=observed,
        verdict="preserved" if matches else "deviated",
        band=band,
    )


def _brightness_axis(field: dict[str, Any]) -> AxisReport:
    band = _band_from_diagnosis(field["diagnosis"])
    observed = field["transcribed_value"]
    matches = band == "measured" and observed == REQUIREMENT_BRIGHTNESS
    return AxisReport(
        requirement=REQUIREMENT_BRIGHTNESS,
        observed=observed,
        verdict="preserved" if matches else "deviated",
        band=band,
    )


def _structure_axis(
    observe_report: dict[str, Any],
    take_wav_path: Path,
    *,
    section_map_path: Path,
    section_map_bytes: Optional[bytes] = None,
) -> tuple[AxisReport, Optional[float]]:
    """Builds the `structure` axis, including the boundary-second live wire
    (module docstring (c)): `svprpe observe`'s structure anchor
    `measurements["observed_sections"]` is a normalized *label* list with no
    timing, so this function separately calls `extract_rpe_from_file` on the
    same `take.wav` to recover `RPEBundle.physical.structure`'s
    `SectionMarker`s (`label`/`start_sec`/`end_sec`) and zips them against
    the label list by index — both derive from the same deterministic
    extraction over the same audio bytes, so they line up 1:1.

    `section_map_path` selects the canonical requirement (T1: `["intro",
    "chorus", "outro"]`, T2: `["intro", "chorus", "chorus", "outro"]`).
    `section_map_bytes`, when given, is the already-read bytes of
    `section_map_path` — `run_round()` reads the section map exactly once
    per run and passes those bytes here (shared with
    `_write_identity_manifest`) instead of this function re-reading the
    file itself. Callers that omit it (e.g. unit tests that only care about
    the requirement resolution) keep the previous read-from-`section_map_path`
    behavior unchanged.

    Returns `(axis_report, position_match_rate)` — the latter feeds the
    report's `notes` (the only whitelisted `AuthoringNote.kind`).
    """
    raw_section_map = (
        section_map_bytes if section_map_bytes is not None else section_map_path.read_bytes()
    )
    canonical_sections = json.loads(raw_section_map)["sections"]
    structure_anchor = next(
        anchor for anchor in observe_report["anchors"] if anchor["anchor_id"] == "structure"
    )
    measurements = structure_anchor["measurements"]
    observed_sections_labels = measurements.get("observed_sections", [])
    adherence_status = structure_anchor["adherence_status"]
    sensor_available = bool(structure_anchor["sensor"]["available"])
    band = "measured" if sensor_available else "not_observed"
    verdict = "exact_match" if adherence_status == "preserved" else "mismatch"
    position_match_rate = measurements.get("position_match_rate")

    observed_sections: Optional[list[ObservedSection]] = None
    if band == "measured":
        bundle = extract_rpe_from_file(str(take_wav_path))
        markers = bundle.physical.structure
        if len(markers) != len(observed_sections_labels):
            raise RuntimeError(
                "boundary-second derivation mismatch: extract_rpe_from_file(take.wav) "
                f"produced {len(markers)} structure marker(s) but svprpe observe's "
                f"structure anchor measurements list {len(observed_sections_labels)} "
                "observed_sections label(s) for the same audio — both calls should be "
                "deterministic over the same take.wav bytes and therefore agree."
            )
        observed_sections = [
            ObservedSection(
                label=label, start_seconds=marker.start_sec, end_seconds=marker.end_sec
            )
            for label, marker in zip(observed_sections_labels, markers)
        ]

    axis_report = AxisReport(
        requirement=canonical_sections,
        observed=observed_sections_labels,
        verdict=verdict,
        band=band,
        observed_sections=observed_sections,
    )
    return axis_report, position_match_rate


def _build_failed_gate_report(
    round_number: int, symbolic_validation: SymbolicValidationResult
) -> AuthoringDiffReport:
    return AuthoringDiffReport(
        round=round_number,
        symbolic_validation=symbolic_validation,
        axes={},
        notes=[],
    )


class ReportBundleRollbackError(RuntimeError):
    """The report/hashes bundle publish (`_publish_report_bundle`) failed
    *and* the rollback that followed it also failed — the original publish
    exception is chained via `__cause__` so it is never silently lost, but
    the filesystem state for `hashes.json`/`-o` may now be inconsistent
    (neither the old nor the new bundle)."""


def _stage_bytes(directory: Path, dest_name: str, data: bytes) -> Path:
    """Staging-phase primitive for `_publish_report_bundle`: writes `data`
    into a tempfile inside `directory` (the eventual destination's own
    parent directory, so the later `os.replace` stays on one filesystem —
    same placement convention as `svp_rpe.utils.atomic_io.atomic_write_bytes`)
    and durably flushes it (`write` + `flush` + `os.fsync`) *without*
    reading or touching `dest_name`'s actual destination path at all. Returns
    the tempfile path; the caller decides whether to `os.replace` it into
    place or delete it — this function's own failure handling only covers
    the tempfile it itself just created (best-effort cleanup) and re-raises
    unchanged, so a staging failure never has a chance to touch a published
    path."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f"{dest_name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        _best_effort_unlink(tmp_path)
        raise
    return tmp_path


def _best_effort_unlink(path: Optional[Path]) -> None:
    """Deletes `path` if given, swallowing any `OSError` (mirrors
    `atomic_write_bytes`'s own best-effort tempfile cleanup on its failure
    path — a secondary cleanup error must never mask the primary exception
    already in flight)."""
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _publish_report_bundle(
    *, output_path: Path, report_bytes: bytes, hashes_path: Path, hashes_bytes: bytes
) -> None:
    """Publishes the report (`-o`) and `hashes.json` as a single visible
    unit, in two phases, closing the provenance-mismatch window a plain
    "write each file atomically, one after the other" ordering leaves open:
    that only protects against a crash leaving *no* report (a legibly
    incomplete run) — but when `-o` names an existing path *outside* the
    protected loop tree (the output-collision guard only refuses an
    existing path *inside* `_LOOP_DIR`), a failure *while writing* one of
    the two files could leave a half-written or stale file at its
    destination while the other file already names/matches the *new*
    content — an inconsistent pair with no crash-only explanation.

    **Staging phase**: both `report_bytes` and `hashes_bytes` are written
    completely (`_stage_bytes`: write + flush + fsync) to tempfiles beside
    their own destinations *before either destination is touched at all* —
    neither `output_path` nor `hashes_path` is read or written during
    staging. Any failure here (staging the report, or staging hashes after
    the report staged fine) cleans up only the tempfile(s) already created
    and re-raises the original exception unchanged; the publish/rollback
    machinery below never runs, so the previously published bundle (if any)
    is provably untouched.

    **Publish phase**: once both members are fully staged,
    `os.replace(report_tmp, output_path)` then
    `os.replace(hashes_tmp, hashes_path)`, in that order — evidence body
    (the report) becomes visible before its provenance record
    (`hashes.json`). This ordering choice matters only for *which*
    inconsistency a crash between the two syscalls produces: "new report +
    old hashes.json" is machine-detectable (hashes.json's recorded `report`
    sha256 will not match the now-visible new report's actual sha256, so a
    reader can tell the pair is stale); the reverse ordering would instead
    risk "new hashes.json (naming a report hash) + old report" with no
    equivalent tell from hashes.json's contents alone.

    **Rollback**: if the report's `os.replace` itself fails, `output_path`
    was never touched (POSIX rename is all-or-nothing) — both tempfiles are
    discarded and the original exception re-raises with no rollback needed.
    If the report's `os.replace` *succeeds* but the hashes `os.replace`
    subsequently fails, `output_path` now holds the *new* report while
    `hashes_path` is untouched (the failed replace changed nothing) — the
    report alone is rolled back to its pre-call snapshot (restore the
    snapshotted bytes via `atomic_write_bytes`, or delete `output_path` if
    it did not exist before this call) and the original exception re-raises.
    A failure during that rollback restore does not swallow the original
    exception: it is re-raised wrapped in `ReportBundleRollbackError`,
    chained via `raise ... from rollback_exc`, so both the publish failure
    and the rollback failure stay visible.

    **Boundary declaration**: full atomicity across this two-file layout is
    not achievable with POSIX `rename` alone — `os.replace` is atomic only
    per file, not across a pair. Staging both members fully before either
    `os.replace` removes the partial-write hazard entirely; what remains is
    the residual crash window *between* the two `os.replace` syscalls
    themselves, which cannot be closed further with this file layout. The
    one inconsistency that window can still produce ("new report + old
    hashes.json") is machine-detectable by recomputing the report's sha256
    and comparing it against `hashes.json`'s recorded `report` field.
    """
    report_tmp = _stage_bytes(output_path.parent, output_path.name, report_bytes)
    try:
        hashes_tmp = _stage_bytes(hashes_path.parent, hashes_path.name, hashes_bytes)
    except BaseException:
        _best_effort_unlink(report_tmp)
        raise

    # Snapshot the report's pre-call bytes (`None` if it did not exist yet)
    # before the first `os.replace` — the only bundle member rollback can
    # ever need to restore, since the report is published first.
    previous_report = output_path.read_bytes() if output_path.exists() else None

    try:
        os.replace(report_tmp, output_path)
    except BaseException:
        _best_effort_unlink(report_tmp)
        _best_effort_unlink(hashes_tmp)
        raise

    try:
        os.replace(hashes_tmp, hashes_path)
    except BaseException as exc:
        try:
            if previous_report is None:
                output_path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(output_path, previous_report)
        except BaseException as rollback_exc:
            raise ReportBundleRollbackError(
                f"report bundle publish to {output_path} failed ({exc!r}) and the "
                f"rollback that followed also failed ({rollback_exc!r}) — filesystem "
                f"state for {hashes_path} and {output_path} may be inconsistent"
            ) from rollback_exc
        _best_effort_unlink(hashes_tmp)
        raise


def run_round(
    score_path: Path,
    workdir: Path,
    round_number: int,
    output_path: Path,
    *,
    section_map_path: Path = SECTION_MAP_PATH,
) -> dict[str, Any]:
    # `paths` is the single source of truth every workdir-relative read/write
    # below derives from — the same mapping the preflight guard checks `-o`
    # against, so the two cannot drift apart.
    paths = _reserved_workdir_paths(workdir)

    # Output-collision guard — runs first, before any write, so a rejected
    # invocation leaves the filesystem untouched. `fixed_input_paths` swaps in
    # the selected `section_map_path` so the guard protects the map actually
    # read by this run (T1's default or T2's), not just T1's.
    _reject_workdir_inside_loop_tree(workdir)
    _reject_score_copy_self_collision(score_path, paths)
    _reject_escaping_reserved_paths(workdir, paths)
    _reject_output_collision(
        output_path,
        score_path=score_path,
        reserved_paths=paths.values(),
        fixed_input_paths=_fixed_input_paths(section_map_path),
    )
    # G1 preflight (Codex review round 7, P1): --section-map must be one of
    # the two pinned maps, unconditionally, before F5's hash check below
    # even considers it (module docstring's "G1 — unpinned --section-map
    # rejection" section).
    _reject_unpinned_section_map(section_map_path)
    # F5 preflight: the fixed judge-side inputs this run is about to read
    # must still match the judge L0b's evidence is pinned against (module
    # docstring's "Preflight judge-input drift check" section) — after every
    # other preflight guard, before workdir.mkdir() or any write. Also
    # captures the verified bytes themselves (G2 snapshot-feed, module
    # docstring's "G2 — judge-input snapshot-feed" section) so every
    # downstream consumer below reads from this same in-memory snapshot
    # instead of re-reading the source paths later.
    verified_inputs = _reject_judge_input_drift(section_map_path)

    workdir.mkdir(parents=True, exist_ok=True)

    # G2 snapshot-feed: stage the verified bytes of every fixed judge-side
    # input a subprocess CLI reads as its own file argument into reserved
    # workdir copies, so those subprocesses read exactly the bytes this
    # preflight check verified rather than re-reading the original
    # config/frozen path later in the run. `eval_control_profile` and the
    # section map are consumed in-process (not via a subprocess file
    # argument) and are fed directly from `verified_inputs` below, with no
    # physical copy needed.
    atomic_write_bytes(paths["judge_contract"], verified_inputs["authoring_contract_l0"])
    atomic_write_bytes(paths["judge_arrangement"], verified_inputs["arrangement"])
    atomic_write_bytes(
        paths["judge_capability_profile"], verified_inputs["suno_capability_profile"]
    )

    # Single input snapshot: `score.yaml` is read from disk exactly once per
    # run, right here — the resulting `score_bytes` (not a second read of
    # `score_path`) drives every downstream step that needs the submitted
    # score's content: the `score_copy` write below, the symbolic gate (run
    # against that copy, not `score_path`, so validation and everything
    # else agree on identical bytes), `hashes["score"]`,
    # `load_composition_score`, and `_prepare_scores`. This closes a TOCTOU
    # window where `score_path` could change between the old code's several
    # independent reads of it.
    score_bytes = score_path.read_bytes()
    score_sha256 = _sha256_bytes(score_bytes)
    score_copy_path = paths["score_copy"]
    atomic_write_bytes(score_copy_path, score_bytes)

    # (a) Symbolic gate — validated against the just-written copy (the same
    # bytes snapshot), not `score_path` itself.
    validation_path = paths["validation"]
    _run_validate_cli(score_copy_path, validation_path, paths["judge_contract"])
    validation_data = json.loads(validation_path.read_text(encoding="utf-8"))
    symbolic_validation = SymbolicValidationResult.model_validate(validation_data)

    hashes: dict[str, str] = {
        "score": score_sha256,
        "validation": _sha256_file(validation_path),
    }

    if symbolic_validation.status == "fail":
        report = _build_failed_gate_report(round_number, symbolic_validation)
        report_bytes = dump_json_bytes(report)
        hashes["report"] = _sha256_bytes(report_bytes)
        # Bundle publish (hashes.json + report, in that order, snapshot +
        # rollback on failure) — see `_publish_report_bundle`'s docstring
        # for the provenance-mismatch window this closes over the prior
        # "hashes first, report atomic second" ordering alone.
        hashes_bytes = (
            json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        _publish_report_bundle(
            output_path=output_path,
            report_bytes=report_bytes,
            hashes_path=paths["hashes"],
            hashes_bytes=hashes_bytes,
        )
        return {"report": report, "hashes": hashes}

    # Defensive re-check: the gate above already ran canonical validation
    # against `score_copy_path`, so this should never fail — fail fast with
    # a clear message rather than a confusing downstream CLI error if that
    # assumption is ever violated. Reads the same copy/bytes snapshot the
    # gate validated, not a fresh read of `score_path`.
    load_composition_score(score_copy_path)

    # (b) Full observation chain (measure_round.py's pipeline, transcribed).
    eval_score_path, eval_score_sha256 = _prepare_scores(
        score_path, score_bytes, paths, verified_inputs["eval_control_profile"]
    )
    hashes["eval_score"] = eval_score_sha256

    roundtrip_path = paths["roundtrip"]
    _run_cli(["roundtrip", str(eval_score_path), "--format", "json", "-o", str(roundtrip_path)])
    hashes["roundtrip"] = _sha256_file(roundtrip_path)

    adherence_path = paths["adherence"]
    _run_cli(
        ["score-adherence", str(eval_score_path), "--format", "json", "-o", str(adherence_path)]
    )
    hashes["adherence"] = _sha256_file(adherence_path)

    score = load_composition_score(eval_score_path)
    take_wav_path = paths["take_wav"]
    atomic_write_bytes(take_wav_path, wav_bytes(perform(score, FAITHFUL_TAKE)))
    hashes["take_wav"] = _sha256_file(take_wav_path)

    # Single section-map snapshot: G1 guarantees `section_map_path` resolves
    # to exactly one of the two pinned maps, so `verified_inputs` carries it
    # under exactly one of these two labels — reuse those already-verified
    # bytes (G2 snapshot-feed) rather than re-reading `section_map_path`
    # here, shared with both the identity manifest's
    # `identity/section_map.json` copy and `_structure_axis`'s requirement
    # resolution below.
    section_map_label = "section_map" if "section_map" in verified_inputs else "section_map_t2"
    section_map_bytes = verified_inputs[section_map_label]

    manifest_path, manifest_sha256 = _write_identity_manifest(
        paths,
        score_sha256=score_sha256,
        round_number=round_number,
        section_map_bytes=section_map_bytes,
    )
    hashes["manifest"] = manifest_sha256

    package_dir = paths["package_dir"]
    package_dir.mkdir(exist_ok=True)
    _run_cli(
        [
            "package",
            str(eval_score_path),
            str(manifest_path),
            # G2 snapshot-feed: the reserved workdir copies of
            # `ARRANGEMENT_PATH`/`CAPABILITY_PROFILE_PATH`'s already-verified
            # bytes, not the original config/frozen paths themselves.
            str(paths["judge_arrangement"]),
            "--capability-profile",
            str(paths["judge_capability_profile"]),
            "--output-dir",
            str(package_dir),
        ]
    )
    package_json_path = paths["package_json"]
    hashes["package"] = _sha256_file(package_json_path)

    observe_report_path = paths["observe_report"]
    _run_cli(
        [
            "observe",
            str(package_json_path),
            str(take_wav_path),
            "--manifest",
            str(manifest_path),
            "-o",
            str(observe_report_path),
        ]
    )
    hashes["observe_report"] = _sha256_file(observe_report_path)

    roundtrip_report = json.loads(roundtrip_path.read_text(encoding="utf-8"))
    fields_by_name = {field["field"]: field for field in roundtrip_report["fields"]}
    key_axis = _key_axis(fields_by_name["key"])
    brightness_axis = _brightness_axis(fields_by_name["brightness"])

    observe_report = json.loads(observe_report_path.read_text(encoding="utf-8"))
    structure_axis, position_match_rate = _structure_axis(
        observe_report,
        take_wav_path,
        section_map_path=section_map_path,
        section_map_bytes=section_map_bytes,
    )

    notes: list[AuthoringNote] = []
    if position_match_rate is not None:
        notes.append(AuthoringNote(kind="position_match_rate", value=position_match_rate))

    report = AuthoringDiffReport(
        round=round_number,
        symbolic_validation=symbolic_validation,
        axes={"key": key_axis, "brightness": brightness_axis, "structure": structure_axis},
        notes=notes,
    )
    report_bytes = dump_json_bytes(report)
    hashes["report"] = _sha256_bytes(report_bytes)

    # Bundle publish (same helper/rationale as the failed-gate branch above).
    hashes_bytes = (
        json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _publish_report_bundle(
        output_path=output_path,
        report_bytes=report_bytes,
        hashes_path=paths["hashes"],
        hashes_bytes=hashes_bytes,
    )

    return {"report": report, "hashes": hashes}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="L0b per-round runner")
    parser.add_argument("score", type=Path, help="Path to a candidate score.yaml")
    parser.add_argument("--workdir", type=Path, required=True, help="Scratch/output directory")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output report.json path")
    parser.add_argument(
        "--round",
        type=int,
        default=0,
        dest="round_number",
        help="Round number recorded in report.json (default 0: dry-run / positive control, "
        "not counted as L0b evidence).",
    )
    parser.add_argument(
        "--section-map",
        type=Path,
        default=SECTION_MAP_PATH,
        dest="section_map_path",
        help="Canonical section-map JSON (section-map/0.1) the structure axis is judged "
        "against (default: frozen/section_map.json, T1's 3-section map). Pass "
        "frozen/section_map_t2.json for T2's 4-section map.",
    )
    args = parser.parse_args(argv)

    try:
        result = run_round(
            args.score,
            args.workdir,
            args.round_number,
            args.output,
            section_map_path=args.section_map_path,
        )
    except ProtectedPathError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report: AuthoringDiffReport = result["report"]
    print(f"round={report.round} symbolic_validation.status={report.symbolic_validation.status} -> {args.output}")
    for axis_name, axis in report.axes.items():
        print(f"  {axis_name}: verdict={axis.verdict} band={axis.band} observed={axis.observed!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
