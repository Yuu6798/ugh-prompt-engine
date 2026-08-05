"""L0-s per-round measurement harness (gate 2).

Takes a `score.yaml` that already passed `validate_score.py` (gate 1) and
runs the full observation chain the L0-s contract (`contract.md` §3) reports
back to the author: R0 roundtrip + score-adherence (via the real `svprpe`
CLI) plus AR4 `observe` (via a decision-derived `performance_package.json` /
`IdentityManifest` / rendered take.wav — none of this exists as CLI input
today, so this script *is* the derivation step task.md calls for).

Concretely, per round:

1. Build `<workdir>/eval_score.yaml`: `score.yaml` with its top-level
   `control_profile` replaced by `frozen/eval_control_profile.yaml` (the
   frozen axis table — task.md's judge, not the submitted Score, decides
   which fields are `tight` for `score-adherence`).
2. `svprpe roundtrip <eval_score.yaml> --format json -o <workdir>/roundtrip.json`
3. `svprpe score-adherence <eval_score.yaml> --format json -o <workdir>/adherence.json`
4. Render `<workdir>/take.wav` via the `svp_rpe.perform` Python API (there is
   no `svprpe perform` CLI command), generate a deterministic
   `<workdir>/identity_manifest.yaml` (work_id `l0s-spike`, source pinned to
   the *original* `score.yaml` — not the control_profile-injected eval copy
   — and a single `structure` anchor pointing at
   `frozen/section_map.json`, `required: false` so `svprpe package` doesn't
   demand a preservation policy for it), `svprpe package`, then
   `svprpe observe`.
5. Fold `roundtrip.json` (key/brightness) and `observe_report.json`
   (structure) into `<workdir>/report.json` per contract.md §3 — never the
   raw instrument JSON itself (D6).
6. Record every artifact's sha256 in `<workdir>/hashes.json`.

**Output-collision guard** (PR #245 Codex P2 review, round 3): every check
below runs before this script writes anything, so a rejected invocation
leaves the filesystem untouched.

- `--workdir` must not resolve inside the protected spike tree
  (`examples/l0s_spike/`, this script's own grandparent directory) at all —
  that tree holds pin-recorded round evidence
  (`rounds/<n>/score.yaml`, `rounds/<n>/report.json`,
  `positive_control/score.yaml`, `ledger.yaml`, ...) a scratch workdir would
  silently clobber via its own internal writes (`score.yaml` copy,
  `eval_score.yaml`, `identity/`, `package/`, ...).
- `-o`/`--output` resolving *inside* that same protected tree is only
  accepted if the target path does not already exist — a brand-new
  `rounds/<n>/report.json` is the normal accept-a-round flow, but
  overwriting an existing one would silently destroy pin-recorded evidence.
  Outside the protected tree (e.g. `build/l0s/...`) it stays unrestricted,
  as before.
- `-o`/`--output` must not resolve to any path this run reads as an input
  (the `score` positional argument, or any of the frozen fixtures this
  module always reads: `frozen/section_map.json`,
  `frozen/eval_control_profile.yaml`, `frozen/arrangement.yaml`, the Suno
  capability profile).
- The `score` positional argument must not resolve to the exact path this
  script would copy it to inside `--workdir` (`<workdir>/score.yaml`) — that
  self-collision would have the input-copy step overwrite the very file it
  is reading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from svp_rpe.compose.loader import load_composition_score
from svp_rpe.perform import FAITHFUL_TAKE, perform, wav_bytes

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SPIKE_DIR = _SCRIPTS_DIR.parent
_REPO_ROOT = _SPIKE_DIR.parents[1]
_FROZEN_DIR = _SPIKE_DIR / "frozen"

SECTION_MAP_PATH = _FROZEN_DIR / "section_map.json"
EVAL_CONTROL_PROFILE_PATH = _FROZEN_DIR / "eval_control_profile.yaml"
ARRANGEMENT_PATH = _FROZEN_DIR / "arrangement.yaml"
CAPABILITY_PROFILE_PATH = _REPO_ROOT / "config" / "capability_profiles" / "suno.yaml"

# Fixed-path inputs this module always reads, regardless of round — an `-o`
# aliasing any of these would have the report-write step clobber a pipeline
# input (guard rule (c) in the module docstring above).
_FIXED_INPUT_PATHS = (
    SECTION_MAP_PATH,
    EVAL_CONTROL_PROFILE_PATH,
    ARRANGEMENT_PATH,
    CAPABILITY_PROFILE_PATH,
)

# Frozen judge (task.md judgement table) — deliberately not read back from
# the submitted score.yaml itself: the requirement is the task's fixed
# target, the "observed" side of the report is what got measured.
REQUIREMENT_KEY = "D minor"
REQUIREMENT_BRIGHTNESS = "dark"

_CLI_TIMEOUT_SECONDS = 300


class ProtectedPathError(RuntimeError):
    """A `--workdir`/`-o` path would clobber protected L0-s spike evidence or
    a pipeline input — refused before anything is written (PR #245 Codex P2
    review, round 3)."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _inside_spike_tree(path: Path) -> bool:
    return path == _SPIKE_DIR or path.is_relative_to(_SPIKE_DIR)


def _reject_workdir_inside_spike_tree(workdir: Path) -> Path:
    resolved = workdir.resolve()
    if _inside_spike_tree(resolved):
        raise ProtectedPathError(
            f"--workdir must not resolve inside the protected L0-s spike tree "
            f"{_SPIKE_DIR} (got {resolved}). That tree holds pin-recorded evidence "
            "(e.g. rounds/<n>/score.yaml, rounds/<n>/report.json, "
            "positive_control/score.yaml, ledger.yaml) that a scratch workdir's own "
            "internal writes would silently overwrite. Use a scratch directory "
            "outside the spike tree, e.g. build/l0s/<name>/."
        )
    return resolved


def _reject_score_copy_self_collision(score_path: Path, workdir: Path) -> None:
    resolved_score = score_path.resolve()
    resolved_copy_dest = (workdir / "score.yaml").resolve()
    if resolved_score == resolved_copy_dest:
        raise ProtectedPathError(
            f"score.yaml argument ({resolved_score}) resolves to the exact path "
            f"measure_round.py would copy it to inside --workdir "
            f"({resolved_copy_dest}) — refusing a self-overwriting copy. Use a "
            "--workdir that does not already contain the input under the name "
            "'score.yaml'."
        )


def _reject_output_collision(output_path: Path, *, score_path: Path) -> Path:
    resolved_output = output_path.resolve()

    protected_inputs = {score_path.resolve(), *(p.resolve() for p in _FIXED_INPUT_PATHS)}
    if resolved_output in protected_inputs:
        raise ProtectedPathError(
            f"-o/--output must not resolve to a pipeline input path (got "
            f"{resolved_output}) — writing the report there would clobber an "
            "input this run reads."
        )

    if _inside_spike_tree(resolved_output) and resolved_output.exists():
        raise ProtectedPathError(
            f"-o/--output resolves inside the protected L0-s spike tree {_SPIKE_DIR} "
            f"and already exists ({resolved_output}) — refusing to silently "
            "overwrite pin-recorded round evidence. A brand-new report.json under "
            "rounds/<n>/ is the normal accept-a-round flow; an existing one there "
            "means this round was already measured. Delete/rename it first if this "
            "is an intentional re-run, or write to a scratch path instead."
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


def _prepare_scores(score_path: Path, workdir: Path) -> tuple[Path, Path, str, str]:
    """Write the original score copy + the eval copy into `workdir`.

    Returns `(original_copy_path, eval_score_path, score_sha256, eval_score_sha256)`.
    `score_sha256` is the hash of the *submitted* `score.yaml` bytes (pinned
    as the identity manifest's `source`), distinct from `eval_score_sha256`
    (the control_profile-injected copy everything downstream measures).
    """
    score_bytes = score_path.read_bytes()
    score_sha256 = _sha256_bytes(score_bytes)

    original_copy = workdir / "score.yaml"
    original_copy.write_bytes(score_bytes)

    data = yaml.safe_load(score_bytes)
    if not isinstance(data, dict):
        raise ValueError(f"score.yaml must be a mapping: {score_path}")
    control_profile = yaml.safe_load(EVAL_CONTROL_PROFILE_PATH.read_text(encoding="utf-8"))
    data["control_profile"] = control_profile

    eval_bytes = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    eval_score_path = workdir / "eval_score.yaml"
    eval_score_path.write_bytes(eval_bytes)
    eval_score_sha256 = _sha256_bytes(eval_bytes)

    return original_copy, eval_score_path, score_sha256, eval_score_sha256


def _write_identity_manifest(
    workdir: Path, *, score_sha256: str, round_number: int
) -> tuple[Path, str]:
    """Deterministically generate `<workdir>/identity_manifest.yaml`.

    Returns `(manifest_path, manifest_sha256)`. Copies `frozen/section_map.json`
    into `<workdir>/identity/` — `IdentityManifest` artifact locators must
    resolve inside the manifest's own directory (path confinement), so the
    frozen fixture can't be referenced in place.
    """
    identity_dir = workdir / "identity"
    identity_dir.mkdir(exist_ok=True)
    section_map_bytes = SECTION_MAP_PATH.read_bytes()
    section_map_sha256 = _sha256_bytes(section_map_bytes)
    (identity_dir / "section_map.json").write_bytes(section_map_bytes)

    manifest_data: dict[str, Any] = {
        "schema_version": "identity-manifest/0.1",
        "meta": {"work_id": "l0s-spike", "version": "0.1"},
        "source": {
            "locator": "score.yaml",
            "sha256": score_sha256,
            "rights_basis": "original",
            "note": (
                f"L0-s spike round {round_number}: pins the submitted score.yaml "
                "as authored (pre control_profile injection; eval_score.yaml, the "
                "copy everything downstream measures, is a derived artifact, not "
                "the pinned source)."
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
    manifest_path = workdir / "identity_manifest.yaml"
    manifest_path.write_bytes(manifest_bytes)
    return manifest_path, _sha256_bytes(manifest_bytes)


def _axis_from_roundtrip_field(field: dict[str, Any], requirement: str) -> dict[str, Any]:
    diagnosis = field["diagnosis"]
    return {
        "requirement": requirement,
        "observed": field["transcribed_value"],
        "verdict": "preserved" if diagnosis == "preserved" else "deviated",
        "band": "not_observed" if diagnosis == "sensor_blind" else "measured",
    }


def _structure_axis(observe_report: dict[str, Any]) -> dict[str, Any]:
    canonical_sections = json.loads(SECTION_MAP_PATH.read_text(encoding="utf-8"))["sections"]
    structure_anchor = next(
        anchor for anchor in observe_report["anchors"] if anchor["anchor_id"] == "structure"
    )
    observed_sections = structure_anchor["measurements"].get("observed_sections", [])
    adherence_status = structure_anchor["adherence_status"]
    sensor_available = bool(structure_anchor["sensor"]["available"])
    return {
        "requirement": canonical_sections,
        "observed": observed_sections,
        "verdict": "exact_match" if adherence_status == "preserved" else "mismatch",
        "band": "measured" if sensor_available else "not_observed",
    }


def measure_round(
    score_path: Path, workdir: Path, round_number: int, output_path: Path
) -> dict[str, Any]:
    # Output-collision guard (module docstring) — runs first, before any
    # write, so a rejected invocation leaves the filesystem untouched.
    _reject_workdir_inside_spike_tree(workdir)
    _reject_score_copy_self_collision(score_path, workdir)
    _reject_output_collision(output_path, score_path=score_path)

    workdir.mkdir(parents=True, exist_ok=True)

    # Defensive re-check: this gate assumes a score that already passed
    # validate_score.py; fail fast with a clear message rather than a
    # confusing downstream CLI error if that assumption is violated.
    load_composition_score(score_path)

    _original_copy, eval_score_path, score_sha256, eval_score_sha256 = _prepare_scores(
        score_path, workdir
    )

    roundtrip_path = workdir / "roundtrip.json"
    _run_cli(["roundtrip", str(eval_score_path), "--format", "json", "-o", str(roundtrip_path)])

    adherence_path = workdir / "adherence.json"
    _run_cli(
        ["score-adherence", str(eval_score_path), "--format", "json", "-o", str(adherence_path)]
    )

    score = load_composition_score(eval_score_path)
    take_wav_path = workdir / "take.wav"
    take_wav_path.write_bytes(wav_bytes(perform(score, FAITHFUL_TAKE)))

    manifest_path, manifest_sha256 = _write_identity_manifest(
        workdir, score_sha256=score_sha256, round_number=round_number
    )

    package_dir = workdir / "package"
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
    package_json_path = package_dir / "performance_package.json"

    observe_report_path = workdir / "observe_report.json"
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

    roundtrip_report = json.loads(roundtrip_path.read_text(encoding="utf-8"))
    fields_by_name = {field["field"]: field for field in roundtrip_report["fields"]}
    key_axis = _axis_from_roundtrip_field(fields_by_name["key"], REQUIREMENT_KEY)
    brightness_axis = _axis_from_roundtrip_field(
        fields_by_name["brightness"], REQUIREMENT_BRIGHTNESS
    )

    observe_report = json.loads(observe_report_path.read_text(encoding="utf-8"))
    structure_axis = _structure_axis(observe_report)

    notes: list[str] = []
    for axis_name, axis in (
        ("key", key_axis),
        ("brightness", brightness_axis),
        ("structure", structure_axis),
    ):
        if axis["band"] != "measured":
            notes.append(f"{axis_name}: band={axis['band']} (not eligible for verdict trust).")

    report = {
        "round": round_number,
        "symbolic_validation": {"status": "pass"},
        "axes": {"key": key_axis, "brightness": brightness_axis, "structure": structure_axis},
        "notes": notes,
    }
    report_bytes = (
        json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    )
    output_path.write_bytes(report_bytes)
    report_sha256 = _sha256_bytes(report_bytes)

    hashes = {
        "score": score_sha256,
        "eval_score": eval_score_sha256,
        "take_wav": _sha256_file(take_wav_path),
        "manifest": manifest_sha256,
        "package": _sha256_file(package_json_path),
        "roundtrip": _sha256_file(roundtrip_path),
        "adherence": _sha256_file(adherence_path),
        "observe_report": _sha256_file(observe_report_path),
        "report": report_sha256,
    }
    (workdir / "hashes.json").write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {"report": report, "hashes": hashes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L0-s per-round measurement harness")
    parser.add_argument("score", type=Path, help="Path to a validated score.yaml")
    parser.add_argument("--workdir", type=Path, required=True, help="Scratch/output directory")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output report.json path")
    parser.add_argument(
        "--round",
        type=int,
        default=0,
        dest="round_number",
        help="Round number recorded in report.json (default 0: dry-run / positive control, "
        "not counted as spike evidence).",
    )
    args = parser.parse_args(argv)

    try:
        result = measure_round(args.score, args.workdir, args.round_number, args.output)
    except ProtectedPathError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report = result["report"]
    print(f"round={report['round']} report -> {args.output}")
    for axis_name, axis in report["axes"].items():
        print(
            f"  {axis_name}: verdict={axis['verdict']} band={axis['band']} "
            f"observed={axis['observed']!r}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
