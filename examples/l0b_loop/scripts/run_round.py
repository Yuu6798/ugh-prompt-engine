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

# Frozen judge (task.md judgement table) — deliberately not read back from the
# submitted score.yaml itself: the requirement is the task's fixed target,
# the "observed" side of the report is what got measured.
REQUIREMENT_KEY = "D minor"
REQUIREMENT_BRIGHTNESS = "dark"

_CLI_TIMEOUT_SECONDS = 300


class ProtectedPathError(RuntimeError):
    """A `--workdir`/`-o` path would clobber protected L0b evidence or a
    pipeline input — refused before anything is written."""


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


def _run_validate_cli(score_path: Path, output_path: Path) -> None:
    """Runs the L0a symbolic gate. Exit `0` (pass) and `1` (fail record) are
    both normal outcomes — `svprpe validate` always writes `output_path` in
    either case (`src/svp_rpe/cli/validate_cmd.py`'s docstring: exit codes
    `0`/`1` bracket the *result*, `2` is reserved for an operational error
    that never reaches the point of computing/writing a result at all)."""
    result = subprocess.run(
        ["svprpe", "validate", str(score_path), "--contract", str(CONTRACT_PATH), "-o", str(output_path)],
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"svprpe validate {score_path} --contract {CONTRACT_PATH} failed with an "
            f"operational error (exit {result.returncode}), not a pass/fail result\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


def _prepare_scores(
    score_path: Path, score_bytes: bytes, paths: dict[str, Path]
) -> tuple[Path, str]:
    """Writes the eval copy (`control_profile` injected from the frozen axis
    table) into `workdir`, from the already-read `score_bytes` snapshot —
    the caller (`run_round()`) reads `score.yaml` exactly once per run and
    passes those same bytes here (and into the `score_copy` write, the
    symbolic gate, and `hashes["score"]`); this function does not re-read
    `score_path` from disk itself (`score_path` is kept only to name the
    file in the `ValueError` message below). Returns `(eval_score_path,
    eval_score_sha256)` — the submitted score's own hash
    (`hashes["score"]`/the identity manifest's `source.sha256`) is derived
    by the caller from the same `score_bytes`, not re-derived here."""
    data = yaml.safe_load(score_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"score.yaml must be a mapping: {score_path}")
    control_profile = yaml.safe_load(EVAL_CONTROL_PROFILE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = control_profile

    eval_bytes = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    eval_score_path = paths["eval_score"]
    eval_score_path.write_bytes(eval_bytes)
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
    paths["identity_section_map"].write_bytes(section_map_bytes)

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
    manifest_path.write_bytes(manifest_bytes)
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

    workdir.mkdir(parents=True, exist_ok=True)

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
    score_copy_path.write_bytes(score_bytes)

    # (a) Symbolic gate — validated against the just-written copy (the same
    # bytes snapshot), not `score_path` itself.
    validation_path = paths["validation"]
    _run_validate_cli(score_copy_path, validation_path)
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
    eval_score_path, eval_score_sha256 = _prepare_scores(score_path, score_bytes, paths)
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
    take_wav_path.write_bytes(wav_bytes(perform(score, FAITHFUL_TAKE)))
    hashes["take_wav"] = _sha256_file(take_wav_path)

    # Single section-map snapshot: read once and shared with both the
    # identity manifest's `identity/section_map.json` copy and
    # `_structure_axis`'s requirement resolution below (instead of each of
    # those two call sites independently reading `section_map_path` off
    # disk).
    section_map_bytes = section_map_path.read_bytes()

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
            str(ARRANGEMENT_PATH),
            "--capability-profile",
            str(CAPABILITY_PROFILE_PATH),
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
