"""L0b per-round runner (`docs/llm_adapter_planning.md` §4「L0b」/
`examples/l0b_loop/task.md`).

`run_round.py <score.yaml> --round N --workdir <dir> -o <report.json>` runs
the full L0b pipeline for one candidate `score.yaml` and writes an
`AuthoringDiffReport` (`src/svp_rpe/authoring/report.py`) — L0a's frozen
report normal form — to `-o`.

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
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
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
# (mirrors measure_round.py's `_FIXED_INPUT_PATHS`).
_FIXED_INPUT_PATHS = (
    SECTION_MAP_PATH,
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


def _reject_output_collision(
    output_path: Path, *, score_path: Path, reserved_paths: Iterable[Path]
) -> Path:
    resolved_output = output_path.resolve()

    protected = {score_path.resolve(), *(p.resolve() for p in _FIXED_INPUT_PATHS)}
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


def _prepare_scores(score_path: Path, paths: dict[str, Path]) -> tuple[Path, str, str]:
    """Writes the eval copy (`control_profile` injected from the frozen axis
    table) into `workdir`. Returns `(eval_score_path, score_sha256,
    eval_score_sha256)` — `score_sha256` hashes the *submitted* `score.yaml`
    bytes (pinned as the identity manifest's `source`), distinct from
    `eval_score_sha256` (the copy everything downstream measures)."""
    score_bytes = score_path.read_bytes()
    score_sha256 = _sha256_bytes(score_bytes)

    original_copy = paths["score_copy"]
    original_copy.write_bytes(score_bytes)

    data = yaml.safe_load(score_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"score.yaml must be a mapping: {score_path}")
    control_profile = yaml.safe_load(EVAL_CONTROL_PROFILE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = control_profile

    eval_bytes = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    eval_score_path = paths["eval_score"]
    eval_score_path.write_bytes(eval_bytes)
    eval_score_sha256 = _sha256_bytes(eval_bytes)

    return eval_score_path, score_sha256, eval_score_sha256


def _write_identity_manifest(
    paths: dict[str, Path], *, score_sha256: str, round_number: int
) -> tuple[Path, str]:
    """Deterministically generates `<workdir>/identity_manifest.yaml`.
    Copies `frozen/section_map.json` into `<workdir>/identity/` —
    `IdentityManifest` artifact locators must resolve inside the manifest's
    own directory (path confinement), so the frozen fixture can't be
    referenced in place."""
    identity_dir = paths["identity_dir"]
    identity_dir.mkdir(exist_ok=True)
    section_map_bytes = SECTION_MAP_PATH.read_bytes()
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
    observe_report: dict[str, Any], take_wav_path: Path
) -> tuple[AxisReport, Optional[float]]:
    """Builds the `structure` axis, including the boundary-second live wire
    (module docstring (c)): `svprpe observe`'s structure anchor
    `measurements["observed_sections"]` is a normalized *label* list with no
    timing, so this function separately calls `extract_rpe_from_file` on the
    same `take.wav` to recover `RPEBundle.physical.structure`'s
    `SectionMarker`s (`label`/`start_sec`/`end_sec`) and zips them against
    the label list by index — both derive from the same deterministic
    extraction over the same audio bytes, so they line up 1:1.

    Returns `(axis_report, position_match_rate)` — the latter feeds the
    report's `notes` (the only whitelisted `AuthoringNote.kind`).
    """
    canonical_sections = json.loads(SECTION_MAP_PATH.read_text(encoding="utf-8"))["sections"]
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


def run_round(
    score_path: Path, workdir: Path, round_number: int, output_path: Path
) -> dict[str, Any]:
    # `paths` is the single source of truth every workdir-relative read/write
    # below derives from — the same mapping the preflight guard checks `-o`
    # against, so the two cannot drift apart.
    paths = _reserved_workdir_paths(workdir)

    # Output-collision guard — runs first, before any write, so a rejected
    # invocation leaves the filesystem untouched.
    _reject_workdir_inside_loop_tree(workdir)
    _reject_score_copy_self_collision(score_path, paths)
    _reject_output_collision(output_path, score_path=score_path, reserved_paths=paths.values())

    workdir.mkdir(parents=True, exist_ok=True)

    # (a) Symbolic gate.
    validation_path = paths["validation"]
    _run_validate_cli(score_path, validation_path)
    validation_data = json.loads(validation_path.read_text(encoding="utf-8"))
    symbolic_validation = SymbolicValidationResult.model_validate(validation_data)

    hashes: dict[str, str] = {
        "score": _sha256_file(score_path),
        "validation": _sha256_file(validation_path),
    }

    if symbolic_validation.status == "fail":
        report = _build_failed_gate_report(round_number, symbolic_validation)
        report_bytes = dump_json_bytes(report)
        output_path.write_bytes(report_bytes)
        hashes["report"] = _sha256_bytes(report_bytes)
        paths["hashes"].write_text(
            json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"report": report, "hashes": hashes}

    # Defensive re-check: the gate above already ran canonical validation,
    # so this should never fail — fail fast with a clear message rather than
    # a confusing downstream CLI error if that assumption is ever violated.
    load_composition_score(score_path)

    # (b) Full observation chain (measure_round.py's pipeline, transcribed).
    eval_score_path, score_sha256, eval_score_sha256 = _prepare_scores(score_path, paths)
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

    manifest_path, manifest_sha256 = _write_identity_manifest(
        paths, score_sha256=score_sha256, round_number=round_number
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
    structure_axis, position_match_rate = _structure_axis(observe_report, take_wav_path)

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
    output_path.write_bytes(report_bytes)
    hashes["report"] = _sha256_bytes(report_bytes)

    paths["hashes"].write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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
    args = parser.parse_args(argv)

    try:
        result = run_round(args.score, args.workdir, args.round_number, args.output)
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
