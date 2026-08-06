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
proof. (This closed the hazard for every reserved-path write *this module*
makes directly; the subprocess CLI outputs `-o`-write into other reserved
names of their own — see "H1" below, which closes that remaining gap the
same way.)

**Subprocess-output staged atomic publish (H1)** (Codex review round 9,
P1): the guarantee above covers only the paths this module writes to
*directly* (its own `atomic_write_bytes` calls). The single-file CLI
subprocess outputs this module also collects at a reserved workdir path via
`-o` (`validation.json`, `roundtrip.json`, `adherence.json`,
`observe_report.json`) and `svprpe package`'s `--output-dir` directory
output were still exposed to exactly the hard-link hazard the section above
closes for this module's own writes: at least one of those CLI
implementations (`src/svp_rpe/cli/roundtrip_cmd.py`'s `roundtrip`/
`score-adherence` commands) writes its `-o` target in place
(`Path(output).write_text(...)`), so on a reused `--workdir` whose reserved
name under that path is a *hard* link to some other pin-recorded file, that
in-place write truncates and overwrites the other file's content too,
through the shared inode — the same hazard class, just reached through a
subprocess instead of this module's own `write_bytes`/`write_text` call.
`src/`'s CLI implementations are out of scope for this fix (this module's
own task brief), so the fix lives entirely on this module's side of the
subprocess boundary: every one of these five outputs is redirected to a
dedicated staging path under `<workdir>/subproc_staging/` (a fresh,
reserved sub-tree this module clears and recreates at the top of every
`run_round()` call, so a stale staged file from a prior invocation of a
reused `--workdir` can never leak into this run's publish step) instead of
the real reserved path, and only *after* the subprocess exits does this
module read the staged bytes back and publish them to the real reserved
path via `atomic_write_bytes` (`_run_cli_staged`, `_run_validate_cli`,
`_publish_staged_directory`) — the same `os.replace` convention as every
other reserved-path write in this module, applied one extra hop downstream
of the subprocess. `svprpe package --output-dir` publishes into
`<workdir>/subproc_staging/package/`, and every file found there afterward
(`performance_package.json`/`compilation_report.json` today, and any other
file a future package output might add) is published individually the same
way into `paths["package_dir"]`. The staging paths themselves are ordinary
entries in `_reserved_workdir_paths`, so the existing output-collision
guard and reserved-path symlink-escape guard protect them the same way they
protect every other reserved workdir artifact. Hash computation and every
downstream read (`_key_axis`/`_brightness_axis`/`_structure_axis`,
`hashes.json`) still happen against the real reserved paths exactly as
before — only the write path changed, not what gets hashed or read back —
so normal-path final artifacts are unchanged byte-for-byte by this change
(the T1/T2 slow smoke tests below prove it). With this in place, every
subprocess output this module collects goes through the same
staged-then-published pattern this module's own direct writes already
use — 平文 write 経路の全数終端 (the plain-write path is now closed for
every reserved-path writer in this pipeline, not just this module's own
direct writes).

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

**I1 — reserved-directory containment for score/`-o`** (Codex review round
11, P1): the guards above (`_reject_score_copy_self_collision`,
`_reject_output_collision`) originally checked `score_path`/`-o` against the
reserved paths by *exact equality* only. That leaves a gap H1's
`subproc_staging/` clear opens: a `score_path` (or a pre-existing `-o`
target) living *inside* a reserved directory — e.g. `<workdir>/
subproc_staging/candidate.yaml` — is not equal to any single reserved path,
so the old checks let it through, and `run_round()`'s `shutil.rmtree` of
`subproc_staging/` (which runs after preflight, before `score_path` is ever
read) deletes it before this run reads it — for `score_path`, that turns a
clean preflight refusal into a confusing `FileNotFoundError` mid-run; for a
pre-existing `-o` under the same tree, the old report is deleted with
nothing published in its place if this run then fails downstream. Both
guards now also check *containment*: `score_path`/`-o` must not resolve to
a path inside any of `_RESERVED_DIRECTORY_KEYS`' directory trees
(`identity/`, `package/`, `judge_inputs/`, `subproc_staging/`,
`subproc_staging/package/`), in addition to the existing exact-equality
check. Containment is intentionally *not* checked against `--workdir`
itself — an `-o`/score living directly under `--workdir` but outside every
reserved subtree (the ordinary `<workdir>/report.json` shape) remains
accepted, exactly as before.

**J1 — subprocess bound to this interpreter's own engine** (`_svprpe_cmd`,
Codex review round 17, P1): every `subprocess.run` invocation in this module
used to shell out to `["svprpe", *args]` — a bare console-script name
resolved via `PATH` at subprocess-launch time, not necessarily the same
`svp_rpe` install this script itself (`sys.executable`) imports in-process
elsewhere (`load_composition_score`, `extract_rpe_from_file`, `perform`,
...). If `PATH` resolves to a *different* environment's stale `svprpe`
console script, that subprocess runs different engine code than the one this
run is pinned against, while still writing a `-o` output that looks like an
ordinary canonical report — an unpinned-engine result masquerading as a
pinned one. `_svprpe_cmd(args)` returns `[sys.executable, "-m",
"svp_rpe.cli", *args]` instead: `python -m svp_rpe.cli` runs this repo's
`[project.scripts]` `svprpe = "svp_rpe.cli:app"` entry point's own `app`
(via `src/svp_rpe/cli/__main__.py`) against exactly the interpreter running
this script, closing the PATH-resolution gap structurally rather than
trusting `PATH` to agree. `_run_cli`/`_run_validate_cli` (and therefore
`_run_cli_staged`, which calls `_run_cli`) all route through this one
helper. Output is unchanged byte-for-byte by this switch — same engine, same
arguments, same bytes out — the T1/T2 slow smoke tests below
(`test_positive_control_round_trip_reproduces_pinned_report` /
`..._t2_...`) are the byte-for-byte proof.

**K1 — import engine checkout containment** (`_reject_engine_outside_checkout`,
Codex review round 19, P1): J1 above bound the *subprocess* CLI path to this
interpreter's own engine; this guard closes the matching gap on the
*in-process* import path. `import svp_rpe` (this module's own top-level
import, resolved via `sys.path` at interpreter start rather than at
subprocess-launch time) could resolve to a different checkout's editable
install, a stale wheel, or a site-packages copy shadowing this repo's own
package — every in-process call this script makes (`load_composition_score`,
`extract_rpe_from_file`, `perform`, ...) would then run different engine
bytes than the ones this checkout's evidence is pinned against, while still
writing a `-o` report that looks like an ordinary canonical result: the
imported engine is not this checkout's pinned source, so reports would be
attributed to wrong engine bytes. `_reject_engine_outside_checkout` resolves
`svp_rpe.__file__` and verifies it falls inside `_SRC_ROOT`
(`_REPO_ROOT / "src"`, reusing this module's existing repo-root derivation);
a mismatch raises `EngineCheckoutContainmentError` naming the actual
resolved `svp_rpe.__file__` and the expected `src/` prefix. Runs first in
`run_round()`, before every other preflight guard and before any write —
a wrong-engine import would already make every subsequent guard's own
attribution suspect, so this check gates the guard chain itself rather than
sitting alongside it.

**Boundary declaration**: containment binds the engine's *attribution* to
"this checkout's `src/`" only. Individual source-byte identity is
`engine_state`'s job (git attestation: `git diff --stat <base>..HEAD --
src/` empty) — this guard deliberately does not hash-verify the engine's
full source tree (that would duplicate git's own role). This guard's scope
is narrower and purely structural: it blocks an import-resolution
mix-up (wrong checkout entirely), not an edit within the right one.

**L1 — pre-publish judge-input copy re-verification**
(`_reject_judge_input_copy_drift`, Codex review round 23, P1): G2 above
closed the *source*-side TOCTOU (a downstream consumer re-reading
`config`/`frozen` after preflight verified it) by staging preflight-verified
bytes into `<workdir>/judge_inputs/*` copies once, up front. It left the
*copy*-side open: `judge_inputs/*` is an ordinary mutable workdir file for
the rest of the run, ordinarily protected by the output-collision guard only
against *this run's own* writes, not against a second process with write
access to `--workdir` overwriting a copy after G2 stages it — if that
happens, the CLI subprocesses that already consumed the original copy are
unaffected, but a report published afterward is effectively vouching for
judge bytes that no longer match what is actually on disk under
`judge_inputs/`. `_reject_judge_input_copy_drift(paths, verified_inputs)`
re-reads every `judge_inputs/*` copy `_JUDGE_INPUT_COPY_LABELS` names and
re-hashes it against the `verified_inputs` bytes G2 staged it from,
immediately before each of `run_round()`'s two `_publish_report_bundle`
call sites; any mismatch or missing copy raises `JudgeInputCopyDriftError`
(a `JudgeInputDriftError`/`ProtectedPathError` subclass) naming the drifted
copy, and the publish that would have attributed a report to it never
happens. Applied at *both* publish sites unconditionally: G2 stages all
three copies before the symbolic-fail branch's own early-return check
(`if symbolic_validation.status == "fail"`) is reached, so that branch is
not exempt from the drift window this guard closes, even though it is the
cheaper of the two branches to run. This is re-verification, not
immutable-by-construction protection — see `JudgeInputCopyDriftError`'s
docstring for the boundary this leaves open (a window between G2's write
and this check still exists; it is merely much shorter than "for the rest
of the run").

**M1 — pre-publish recorded-artifact re-verification** (`_reject_recorded_
artifact_drift`, PR #247 Codex review round 25, P1, family terminus): L1
above closed drift for the three `judge_inputs/*` *input* copies. It left
every *generated* artifact this run itself hashes into `hashes.json` open to
the identical hazard: `take.wav`, `eval_score.yaml`, `validation.json`,
`roundtrip.json`, `adherence.json`, the identity manifest, the package pair,
and `observe_report.json` are all recorded (hashed into `hashes.json`) at
generation time and then consumed by a *later* step (a subsequent
subprocess, or nothing further at all) — nothing makes any of them
read-only afterward, so a second process with write access to `--workdir`
could rewrite one after this run hashed it (round 23's `judge_inputs/`
finding, applied to this run's own outputs instead of its snapshotted
inputs). A report published afterward would then vouch for artifact bytes
that no longer match what is actually on disk, with no record that the
consuming step (or a human reading `hashes.json` post hoc) ever saw the
tampered version. `_reject_recorded_artifact_drift(paths, hashes)` is a
single unified gate (not one bespoke check per artifact): it walks every key
`hashes` actually holds at call time, resolves each to its reserved workdir
path via the explicit `_RECORDED_ARTIFACT_PATH_KEYS` table, re-reads and
re-hashes that path, and compares against the digest already recorded in
`hashes`. `score` and `report` are the only two keys deliberately excluded
(`_RECORDED_ARTIFACT_EXCLUDED_KEYS`, each for a distinct reason — see that
constant's own comment) — every other key `hashes` can hold today has a
path-table entry. A key present in neither table is *not* silently skipped:
it raises `RecordedArtifactDriftError` naming the unrecognized key, so a
future artifact added to `hashes` without a matching table entry fails
closed instead of quietly slipping past this guard. Called immediately
alongside `_reject_judge_input_copy_drift`, at both of `run_round()`'s
`_publish_report_bundle` call sites (the symbolic-fail branch, where
`hashes` holds only `score`/`validation`/`report`, and the full-pipeline
success branch, where it holds all of the above) — a generic
"verify every key that's actually present" implementation naturally covers
both call sites with the same function, no branch-specific variant needed.
With L1 (judge-side inputs) and this guard (this run's own generated
artifacts) both in place immediately before publish, every byte this run
consumed or produced and later attests to via `hashes.json` — other than
`score` (an external input already byte-snapshotted at the top of the run)
and `report` (not yet written to disk at the point this guard runs, since
`_publish_report_bundle` writes it together with `hashes.json` right after
this check passes) — is re-verified against its recorded digest at the
publish boundary. This closes the family of "hash it, consume it, but never
look at it again before vouching for it" gaps 23 and 25 both surfaced;
re-verification (not immutable-by-construction protection, the same
boundary L1 already documents) is the terminus for this family.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

import yaml

import svp_rpe
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
# K1 (Codex review round 19, P1): this checkout's own `src/` tree — the
# containment boundary `_reject_engine_outside_checkout` binds `import
# svp_rpe`'s resolved `svp_rpe.__file__` against. Reuses the same
# `_REPO_ROOT` derivation every other path constant in this module already
# relies on.
_SRC_ROOT = _REPO_ROOT / "src"

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


class JudgeInputCopyDriftError(JudgeInputDriftError):
    """L1 pre-publish guard (PR #247 Codex review round 23, P1): a staged
    `judge_inputs/*` copy (G2 snapshot-feed, module docstring's "G2" section)
    no longer matches the bytes `_reject_judge_input_drift` verified and
    `run_round()` staged it from. G2 closed the verify-then-*re-read*
    TOCTOU (a downstream consumer re-opening the original config/frozen
    path), but every `judge_inputs/*` copy is itself an ordinary mutable
    workdir file for the rest of the run — nothing makes it read-only — so a
    second process with write access to `--workdir` could still overwrite a
    copy between G2's staging write and this run's eventual report publish,
    and the report would then be attributed to pinned judge bytes that no
    longer match what is actually sitting in `judge_inputs/`.
    `_reject_judge_input_copy_drift` re-reads and re-hashes every staged
    copy immediately before `_publish_report_bundle` publishes a report, and
    raises this error (fail-closed — the publish is refused, not
    best-effort-warned) on any mismatch or missing copy. This is
    re-verification, not immutable-by-construction protection: it shrinks
    the unnoticed-drift window to "immediately before publish" rather than
    closing it structurally (an OS-level read-only bind mount or permission
    change on `judge_inputs/` would be the structural fix; out of scope for
    this lightweight guard). A `JudgeInputDriftError` subclass so `main()`'s
    existing `except ProtectedPathError` handling catches it unchanged."""


class RecordedArtifactDriftError(JudgeInputDriftError):
    """M1 pre-publish guard (PR #247 Codex review round 25, P1, family
    terminus): a generated artifact this run already hashed into `hashes`
    (`take.wav`, `eval_score.yaml`, `validation.json`, `roundtrip.json`,
    `adherence.json`, the identity manifest, the package pair, or
    `observe_report.json`) no longer matches the sha256 recorded for it —
    either the on-disk bytes changed after this run hashed and consumed them
    (a second process with write access to `--workdir` rewrote it), or the
    file is missing outright. Also raised when `hashes` carries a key this
    guard has no path-table entry for (neither `_RECORDED_ARTIFACT_PATH_KEYS`
    nor `_RECORDED_ARTIFACT_EXCLUDED_KEYS`) — an unrecognized key fails
    closed rather than being silently skipped, so a future artifact added to
    `hashes` without updating this guard's tables is itself caught, not
    quietly left unverified. Module docstring's "M1" section has the full
    rationale. A `JudgeInputDriftError`/`ProtectedPathError` subclass so
    `main()`'s existing `except ProtectedPathError` handling catches it
    unchanged."""


class EngineCheckoutContainmentError(ProtectedPathError):
    """`import svp_rpe` resolved to a module outside this checkout's `src/`
    tree (K1, module docstring's "K1" section) — a different checkout's
    editable install, a stale wheel, or a site-packages copy is shadowing
    this repo's own `svp_rpe` package on `sys.path`. Refused before any
    write; a `ProtectedPathError` subclass so `main()`'s existing `except
    ProtectedPathError` handling catches it unchanged."""


def _reject_engine_outside_checkout(engine_file: Optional[str] = None) -> None:
    """K1 preflight guard — see module docstring's "K1 — import engine
    checkout containment" section for the full rationale. Verifies the
    imported `svp_rpe` package's `__file__` resolves inside `_SRC_ROOT`
    (`_REPO_ROOT / "src"`, this checkout's own source tree). `engine_file`
    defaults to the real `svp_rpe.__file__`; parameterized (rather than
    reading the module attribute unconditionally) so tests can substitute an
    out-of-tree path without needing a second real install.

    Deliberately does not hash-verify the engine's source bytes — that is
    `engine_state`'s job (git attestation), not this guard's; see the module
    docstring's boundary declaration. This check only rejects an
    import-resolution mix-up: `svp_rpe` resolving to the wrong checkout
    entirely, not an edit within the right one."""
    resolved = Path(engine_file if engine_file is not None else svp_rpe.__file__).resolve()
    expected_root = _SRC_ROOT.resolve()
    if not resolved.is_relative_to(expected_root):
        raise EngineCheckoutContainmentError(
            "imported engine is not this checkout's pinned source — reports would be "
            f"attributed to wrong engine bytes. svp_rpe.__file__ resolved to {resolved}, "
            f"expected to be inside {expected_root} (this checkout's src/ tree). This "
            "means a different checkout's editable install, a stale wheel, or a "
            "site-packages copy of svp_rpe is shadowing this repo's own package on "
            "sys.path — refusing to run rather than silently producing a report "
            "attributed to the wrong engine. Reinstall this checkout editable "
            "(`pip install -e .`) or fix sys.path/PYTHONPATH so `import svp_rpe` "
            "resolves inside this checkout's src/ tree."
        )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


# L1 (PR #247 Codex review round 23, P1): the `_reserved_workdir_paths` key
# for each `judge_inputs/*` copy G2 stages unconditionally in `run_round()`
# (before either the symbolic-fail early-return branch or the full-pipeline
# success branch is reached — see `JudgeInputCopyDriftError`'s docstring),
# mapped to the `verified_inputs` label (`_reject_judge_input_drift`'s
# return value) it was staged from. `eval_control_profile` and the section
# map are consumed in-process from `verified_inputs` directly (module
# docstring's "G2" section) with no physical `judge_inputs/` copy, so they
# have no entry here — there is no on-disk copy for
# `_reject_judge_input_copy_drift` to re-check for those two.
_JUDGE_INPUT_COPY_LABELS: dict[str, str] = {
    "judge_contract": "authoring_contract_l0",
    "judge_arrangement": "arrangement",
    "judge_capability_profile": "suno_capability_profile",
}


def _reject_judge_input_copy_drift(
    paths: dict[str, Path], verified_inputs: dict[str, bytes]
) -> None:
    """L1 pre-publish guard (PR #247 Codex review round 23, P1) — see
    `JudgeInputCopyDriftError`'s docstring for the full rationale. Called
    immediately before every `_publish_report_bundle` invocation in
    `run_round()`. Re-reads each `judge_inputs/*` copy named in
    `_JUDGE_INPUT_COPY_LABELS` and compares its current on-disk sha256
    against the sha256 of the `verified_inputs` bytes it was staged from at
    the top of this run; a mismatch (rewritten) or a `FileNotFoundError`
    (deleted) both raise `JudgeInputCopyDriftError` naming the copy, its
    path, and — for a mismatch — both digests, fail-closed before the report
    bundle is published.

    Applied unconditionally to both `run_round()` publish sites (the
    symbolic-fail early-return branch and the full-pipeline success branch):
    G2 stages all three copies before the symbolic gate ever runs, so the
    fail branch's report is reached only *after* the copies already exist on
    disk — it is not, in this module's current control flow, exempt from
    the drift window this guard closes."""
    for path_key, label in _JUDGE_INPUT_COPY_LABELS.items():
        copy_path = paths[path_key]
        expected_bytes = verified_inputs[label]
        expected_sha256 = _sha256_bytes(expected_bytes)
        try:
            actual_bytes = copy_path.read_bytes()
        except FileNotFoundError:
            raise JudgeInputCopyDriftError(
                "judge input copy was mutated mid-run — refusing to publish a report "
                f"attributed to pinned judge bytes: the {label} copy ({copy_path}) is "
                f"missing (expected sha256 {expected_sha256}, staged at run start from "
                "preflight-verified bytes)."
            ) from None
        actual_sha256 = _sha256_bytes(actual_bytes)
        if actual_sha256 != expected_sha256:
            raise JudgeInputCopyDriftError(
                "judge input copy was mutated mid-run — refusing to publish a report "
                f"attributed to pinned judge bytes: the {label} copy ({copy_path}) sha256"
                f" is {actual_sha256}, expected {expected_sha256} (the bytes staged into "
                "this copy at run start from preflight-verified input) — something "
                "rewrote it before this run's report bundle was published."
            )


# M1 (PR #247 Codex review round 25, P1): `hashes` key -> `_reserved_
# workdir_paths` key, for every generated artifact this run hashes into
# `hashes.json` and later attests to via a published report. Explicit table
# (not a naming convention/heuristic) so a mismatch between a `hashes` key
# and its on-disk path is a deliberate declaration, not an inferred guess —
# mirrors `_JUDGE_INPUT_COPY_LABELS`'s existing style. Every `hashes` key
# `run_round()` currently sets is covered here or in
# `_RECORDED_ARTIFACT_EXCLUDED_KEYS` below; `test_l0b_scripts.py` asserts
# that coverage stays exhaustive as a regression guard.
_RECORDED_ARTIFACT_PATH_KEYS: dict[str, str] = {
    "validation": "validation",
    "eval_score": "eval_score",
    "roundtrip": "roundtrip",
    "adherence": "adherence",
    "take_wav": "take_wav",
    "manifest": "identity_manifest",
    "package": "package_json",
    "package_compilation_report": "package_compilation_report",
    "observe_report": "observe_report",
}

# `hashes` keys deliberately excluded from `_reject_recorded_artifact_drift`
# re-verification — each for a distinct, concrete reason (not a blanket
# bypass):
_RECORDED_ARTIFACT_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {
        # External input: `hashes["score"]` is the sha256 of `score_bytes`,
        # the single snapshot read of the submitted score.yaml at the top of
        # `run_round()` (see that read's own comment). `score_copy` (the
        # on-disk `<workdir>/score.yaml`) is a passthrough copy of those same
        # bytes, not an independently generated artifact this guard needs to
        # re-derive-and-compare — re-hashing it would only re-verify the same
        # snapshot `score_bytes` already is, already covered by every
        # downstream step reading `score_bytes` in-memory rather than
        # re-reading the copy.
        "score",
        # Not yet on disk at the point this guard runs: `hashes["report"]` is
        # computed from `report_bytes` in memory, but `_publish_report_bundle`
        # writes `report_bytes` to `-o` and `hashes` (as `hashes.json`)
        # together, immediately *after* this guard passes — there is nothing
        # at `output_path` yet to re-read and re-hash.
        "report",
    }
)


def _reject_recorded_artifact_drift(paths: dict[str, Path], hashes: dict[str, str]) -> None:
    """M1 pre-publish guard (PR #247 Codex review round 25, P1, family
    terminus) — see `RecordedArtifactDriftError`'s docstring and the module
    docstring's "M1" section for the full rationale. Called immediately
    alongside `_reject_judge_input_copy_drift`, right before each of
    `run_round()`'s two `_publish_report_bundle` call sites.

    Walks every key actually present in `hashes` (not a fixed list) so both
    call sites — the symbolic-fail branch's small `hashes`
    (`score`/`validation`/`report`) and the full-pipeline success branch's
    full `hashes` — are covered by the same implementation. For each key:

    - a key in `_RECORDED_ARTIFACT_EXCLUDED_KEYS` is skipped (declared, not
      inferred — see that constant's comment for why each one is safe to
      skip);
    - a key in `_RECORDED_ARTIFACT_PATH_KEYS` is resolved to its reserved
      workdir path, re-read, re-hashed, and compared against the digest
      already recorded in `hashes`; a mismatch or a missing file raises
      `RecordedArtifactDriftError` naming the key, its path, and (for a
      mismatch) both digests;
    - a key in *neither* table raises `RecordedArtifactDriftError` naming the
      unrecognized key — fail-closed, so a future artifact added to `hashes`
      without a matching table entry is caught here rather than silently
      skipped."""
    for key, recorded_sha256 in hashes.items():
        if key in _RECORDED_ARTIFACT_EXCLUDED_KEYS:
            continue
        try:
            path_key = _RECORDED_ARTIFACT_PATH_KEYS[key]
        except KeyError:
            raise RecordedArtifactDriftError(
                f"hashes.json key {key!r} is not in _RECORDED_ARTIFACT_PATH_KEYS or "
                "_RECORDED_ARTIFACT_EXCLUDED_KEYS — refusing to publish rather than "
                "silently skip pre-publish re-verification for a recorded artifact "
                "this guard doesn't know how to check. Add it to one of those two "
                "tables in run_round.py."
            ) from None
        artifact_path = paths[path_key]
        try:
            actual_bytes = artifact_path.read_bytes()
        except FileNotFoundError:
            raise RecordedArtifactDriftError(
                "recorded artifact drift — refusing to publish a report attributed "
                f"to bytes that no longer exist: the {key} artifact ({artifact_path}) "
                f"is missing (expected sha256 {recorded_sha256}, recorded in "
                "hashes.json when this run generated and hashed it)."
            ) from None
        actual_sha256 = _sha256_bytes(actual_bytes)
        if actual_sha256 != recorded_sha256:
            raise RecordedArtifactDriftError(
                "recorded artifact drift — refusing to publish a report attributed "
                f"to stale bytes: the {key} artifact ({artifact_path}) sha256 is now "
                f"{actual_sha256}, expected {recorded_sha256} (recorded in "
                "hashes.json when this run generated and hashed it) — something "
                "rewrote it after it was hashed and consumed, before this run's "
                "report bundle was published."
            )


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
    # H1 (Codex review round 9, P1): staging paths every subprocess CLI
    # output is redirected to (via `-o`/`--output-dir`) before this module
    # republishes it to the real reserved path below via
    # `atomic_write_bytes` — see module docstring's "H1" section. Ordinary
    # entries in this mapping, so the output-collision guard and the
    # reserved-path symlink-escape guard protect them automatically, the
    # same way they already protect the G2 `judge_inputs/*` copies.
    subproc_staging_dir = workdir / "subproc_staging"
    subproc_staging_package_dir = subproc_staging_dir / "package"
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
        # H1 staging paths (see comment above) — one per subprocess `-o`
        # single-file output, plus a directory for `svprpe package
        # --output-dir`'s multi-file output.
        "subproc_staging_dir": subproc_staging_dir,
        "subproc_staging_validation": subproc_staging_dir / "validation.json",
        "subproc_staging_roundtrip": subproc_staging_dir / "roundtrip.json",
        "subproc_staging_adherence": subproc_staging_dir / "adherence.json",
        "subproc_staging_observe_report": subproc_staging_dir / "observe_report.json",
        "subproc_staging_package_dir": subproc_staging_package_dir,
    }


# I1 (Codex review round 11, P1): the subset of `_reserved_workdir_paths`'
# entries that are reserved as *directories* — their entire subtree is
# reserved, not just the literal path. `score`/`-o` containment guards below
# use this (not the full mapping) because only these entries' descendants
# are dangerous: `run_round()` clears and recreates `subproc_staging/` (and
# `subproc_staging/package/`) via `shutil.rmtree` before ever reading
# `score_path`, and `identity/`/`package/`/`judge_inputs/` are likewise
# written-into wholesale rather than as single files. A file-type reserved
# entry (e.g. `score.yaml`, `validation.json`) has no meaningful
# "descendant" — colliding with one of those is already an exact-equality
# case the existing checks catch directly.
_RESERVED_DIRECTORY_KEYS: tuple[str, ...] = (
    "identity_dir",
    "package_dir",
    "judge_inputs_dir",
    "subproc_staging_dir",
    "subproc_staging_package_dir",
)


def _reserved_directory_paths(
    reserved_paths: dict[str, Path],
) -> tuple[tuple[str, Path], ...]:
    """`(label, path)` pairs for `_RESERVED_DIRECTORY_KEYS`' entries present
    in `reserved_paths` — the containment roots the score/`-o` guards below
    check against. Keeping the label alongside the path lets a rejection
    message name exactly which reserved directory the offending path falls
    under."""
    return tuple(
        (label, reserved_paths[label]) for label in _RESERVED_DIRECTORY_KEYS if label in reserved_paths
    )


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
    """Rejects a `score_path` that either resolves to the exact path of any
    reserved workdir artifact (originally just `score_copy` — generalized to
    every entry in `reserved_paths`, e.g. a `score.yaml` argument that
    happens to alias `paths["roundtrip"]`) or lives *inside* one of the
    reserved directory trees (`identity/`, `package/`, `subproc_staging/`,
    etc. — I1, Codex review round 11, P1).

    The containment case matters because `run_round()` clears and recreates
    `subproc_staging/` (`shutil.rmtree`) before ever reading `score_path` —
    a `score.yaml` living at e.g. `<workdir>/subproc_staging/candidate.yaml`
    would be deleted by that clear before this run reads it at all, turning
    what should be a clean preflight refusal into a confusing
    `FileNotFoundError` mid-run. This guard fires before `workdir.mkdir()`
    or any write, same as the exact-equality case."""
    resolved_score = score_path.resolve()
    for label, reserved_path in reserved_paths.items():
        resolved_reserved = reserved_path.resolve()
        if resolved_score == resolved_reserved:
            raise ProtectedPathError(
                f"score.yaml argument ({resolved_score}) resolves to the exact path of "
                f"reserved workdir artifact {label!r} ({resolved_reserved}) — refusing: "
                "this run's own writes to that reserved path (e.g. copying score.yaml "
                "into --workdir, or a later pipeline step writing that artifact) would "
                "clobber the input or produce a self-overwriting copy. Use a --workdir/"
                "score combination that does not alias a reserved artifact."
            )
    for label, reserved_dir in _reserved_directory_paths(reserved_paths):
        resolved_dir = reserved_dir.resolve()
        if resolved_score.is_relative_to(resolved_dir):
            raise ProtectedPathError(
                f"score.yaml argument ({resolved_score}) is inside reserved workdir "
                f"directory {label!r} ({resolved_dir}) — refusing: run_round() clears "
                "and recreates that directory before this run ever reads score.yaml, "
                "which would delete the input out from under this run before it's read. "
                "Use a --workdir/score combination where the score does not live under "
                "a reserved directory."
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
    reserved_directory_paths: Iterable[tuple[str, Path]] = (),
) -> Path:
    """`reserved_directory_paths` (I1, Codex review round 11, P1 — labeled
    `(label, path)` pairs, typically `_reserved_directory_paths(paths)`)
    extends the original exact-equality check with *containment*: an `-o`
    landing anywhere inside a reserved directory tree (`identity/`,
    `package/`, `subproc_staging/`, etc.) is refused too, not just an `-o`
    equal to one of those directories' own path. This matters because
    `run_round()` clears and recreates `subproc_staging/` at the top of
    every run — an `-o` an earlier round left there would be deleted by that
    clear before this run's own publish step, silently losing whatever
    report was at that path if this run then fails partway through.
    Defaults to `()` (no containment check) so existing callers that only
    care about the original exact-equality behavior are unaffected.
    Containment against `--workdir` itself is deliberately not checked here
    — an `-o` inside `--workdir` but outside every reserved subtree (e.g.
    `<workdir>/report.json`) is the ordinary, legitimate case."""
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

    for label, reserved_dir in reserved_directory_paths:
        resolved_dir = reserved_dir.resolve()
        if resolved_output.is_relative_to(resolved_dir):
            raise ProtectedPathError(
                f"-o/--output ({resolved_output}) is inside reserved workdir directory "
                f"{label!r} ({resolved_dir}) — refusing: run_round() clears and "
                "recreates that directory at the top of every run, which would delete "
                "an existing -o target there before this run's own publish step — a "
                "prior report at that path would be lost, with no new report written in "
                "its place, if this run then fails. Use a --workdir/-o combination where "
                "the output path does not live under a reserved directory."
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


# Codex review round 17, P1: `svprpe` (a bare console-script name resolved
# via `PATH`) can pick up a stale script installed for a *different*
# interpreter/environment than `sys.executable`, silently running a
# different `svp_rpe` engine than the one this run is pinned against while
# still producing a canonical-looking `-o` report. `_svprpe_cmd` binds every
# subprocess CLI invocation in this module to *this* interpreter's own
# installed package instead (`svp_rpe.cli:app`, this repo's
# `[project.scripts]` entry point — `python -m svp_rpe.cli` runs the same
# `app` via `src/svp_rpe/cli/__main__.py`), so the subprocess always runs the
# exact engine this script itself imports in-process elsewhere in this
# module, never an unpinned PATH lookup.
_SVPRPE_MODULE = "svp_rpe.cli"


def _svprpe_cmd(args: list[str]) -> list[str]:
    return [sys.executable, "-m", _SVPRPE_MODULE, *args]


def _run_cli(args: list[str]) -> None:
    result = subprocess.run(
        _svprpe_cmd(args),
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"svprpe {' '.join(args)} failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


def _run_validate_cli(
    score_path: Path, staging_path: Path, dest_path: Path, contract_path: Path
) -> None:
    """Runs the L0a symbolic gate. Exit `0` (pass) and `1` (fail record) are
    both normal outcomes — `svprpe validate` always writes `staging_path` in
    either case (`src/svp_rpe/cli/validate_cmd.py`'s docstring: exit codes
    `0`/`1` bracket the *result*, `2` is reserved for an operational error
    that never reaches the point of computing/writing a result at all).

    `contract_path` is the reserved workdir copy (`paths["judge_contract"]`)
    of `CONTRACT_PATH`'s already-verified bytes (G2 snapshot-feed), not
    `CONTRACT_PATH` itself — `run_round()` passes the copy so this
    subprocess reads exactly the bytes preflight verified.

    `-o` is pointed at `staging_path` (a path under
    `<workdir>/subproc_staging/`), not `dest_path` (the real reserved
    `validation.json`) directly — H1 (module docstring's "H1" section): once
    the subprocess exits with a normal pass/fail exit code, the staged bytes
    are read back and republished to `dest_path` via `atomic_write_bytes`,
    so a reused `--workdir` whose `validation.json` slot is a hard link into
    other pin-recorded evidence is never written to in place.

    Invoked via `_svprpe_cmd` (Codex review round 17, P1) — bound to this
    interpreter's own `svp_rpe.cli` rather than a `PATH`-resolved `svprpe`
    console script (see `_svprpe_cmd`'s docstring comment above)."""
    result = subprocess.run(
        _svprpe_cmd(
            [
                "validate",
                str(score_path),
                "--contract",
                str(contract_path),
                "-o",
                str(staging_path),
            ]
        ),
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
    atomic_write_bytes(dest_path, staging_path.read_bytes())


def _run_cli_staged(args: list[str], staging_path: Path, dest_path: Path) -> None:
    """Runs `svprpe <args>` (whose trailing `-o <staging_path>` the caller
    has already included, pointed at a path under
    `<workdir>/subproc_staging/`) via `_run_cli`, then republishes the
    staged bytes to `dest_path` via `atomic_write_bytes` — H1 (module
    docstring's "H1" section): closes the same hard-link-shared-inode hazard
    for a subprocess's own `-o` write that `atomic_write_bytes` already
    closes for this module's direct reserved-path writes (Codex review round
    6, P1)."""
    _run_cli(args)
    atomic_write_bytes(dest_path, staging_path.read_bytes())


def _publish_staged_directory(staging_dir: Path, dest_dir: Path) -> None:
    """Publishes every file `svprpe package --output-dir staging_dir`
    produced into `dest_dir`, individually via `atomic_write_bytes` — the
    directory-output counterpart of `_run_cli_staged` above (H1, module
    docstring's "H1" section). Walks `staging_dir` recursively (not just the
    two files currently known — `performance_package.json`/
    `compilation_report.json`) so any file a future `package` output adds is
    published too, preserving its path relative to `staging_dir` under
    `dest_dir`. `atomic_write_bytes` creates any needed parent directories
    itself, so nested output is handled without extra bookkeeping here."""
    for path in sorted(staging_dir.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(staging_dir)
        atomic_write_bytes(dest_dir / relative, path.read_bytes())


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
    # K1 preflight (Codex review round 19, P1): import engine checkout
    # containment — runs before every other guard below and before any
    # write (module docstring's "K1" section). A wrong-engine `import
    # svp_rpe` would already make every subsequent guard's own attribution
    # suspect, so this gates the guard chain itself rather than sitting
    # alongside it.
    _reject_engine_outside_checkout()

    # `paths` is the single source of truth every workdir-relative read/write
    # below derives from — the same mapping the preflight guard checks `-o`
    # against, so the two cannot drift apart.
    paths = _reserved_workdir_paths(workdir)

    # Output-collision guard — runs first, before any write, so a rejected
    # invocation leaves the filesystem untouched. `fixed_input_paths` swaps in
    # the selected `section_map_path` so the guard protects the map actually
    # read by this run (T1's default or T2's), not just T1's.
    # `reserved_directory_paths` (I1, Codex review round 11, P1) extends both
    # the score and `-o` checks below from exact-equality to containment: a
    # score.yaml/-o living *inside* a reserved directory (e.g.
    # `subproc_staging/`, cleared via `rmtree` further down before score.yaml
    # is ever read) is refused the same as one aliasing a reserved path
    # exactly.
    _reject_workdir_inside_loop_tree(workdir)
    _reject_score_copy_self_collision(score_path, paths)
    _reject_escaping_reserved_paths(workdir, paths)
    _reject_output_collision(
        output_path,
        score_path=score_path,
        reserved_paths=paths.values(),
        fixed_input_paths=_fixed_input_paths(section_map_path),
        reserved_directory_paths=_reserved_directory_paths(paths),
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

    # H1 (Codex review round 9, P1): fresh staging scratch space for every
    # subprocess CLI output this run collects via `-o`/`--output-dir`
    # (module docstring's "H1" section) — cleared and recreated at the top
    # of *every* run, not just a workdir's first, so a reused `--workdir`
    # never lets a stale staged file from a previous invocation leak into
    # this run's publish step. Safe to `rmtree` unconditionally: the
    # symlink-escape guard above already confirmed neither
    # `subproc_staging_dir` nor `subproc_staging_package_dir` is itself a
    # symlink and both resolve inside `workdir` before this point (and
    # `shutil.rmtree` never follows a symlink it finds *inside* the tree
    # either — it unlinks the link entry itself rather than recursing
    # through it, so a symlink planted deeper inside by a prior run cannot
    # make this delete anything outside the staging tree).
    staging_dir = paths["subproc_staging_dir"]
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    paths["subproc_staging_package_dir"].mkdir(parents=True)

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
    # bytes snapshot), not `score_path` itself. `-o` is staged (H1) before
    # being republished to the reserved `validation_path`.
    validation_path = paths["validation"]
    _run_validate_cli(
        score_copy_path, paths["subproc_staging_validation"], validation_path, paths["judge_contract"]
    )
    # Single-snapshot read: hash and parse the SAME bytes (PR #247 Codex
    # review round 24 — a split read lets a concurrent writer change the
    # parsed values after hashing, publishing verdicts hashes.json does not
    # certify). Same rule applied to roundtrip.json / observe_report.json.
    validation_bytes = validation_path.read_bytes()
    validation_data = json.loads(validation_bytes)
    symbolic_validation = SymbolicValidationResult.model_validate(validation_data)

    hashes: dict[str, str] = {
        "score": score_sha256,
        "validation": _sha256_bytes(validation_bytes),
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
        # L1 pre-publish guard (PR #247 Codex review round 23, P1): the G2
        # snapshot-feed writes above already staged all three
        # `judge_inputs/*` copies unconditionally before this branch is
        # reached, so this branch is not exempt from the mid-run drift
        # window `_reject_judge_input_copy_drift` closes — see
        # `JudgeInputCopyDriftError`'s docstring.
        _reject_judge_input_copy_drift(paths, verified_inputs)
        # M1 pre-publish guard (PR #247 Codex review round 25, P1, family
        # terminus): re-verifies every generated artifact this branch's
        # (small) `hashes` already recorded — here, just `validation.json` —
        # still matches what it was hashed as. See `RecordedArtifactDriftError`'s
        # docstring / module docstring's "M1" section.
        _reject_recorded_artifact_drift(paths, hashes)
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

    # `-o` for both of these is staged (H1) before being republished to the
    # reserved `roundtrip_path`/`adherence_path` — `roundtrip_cmd.py` writes
    # its `-o` target in place (`Path(output).write_text(...)`), the exact
    # hazard H1 closes (module docstring's "H1" section).
    roundtrip_path = paths["roundtrip"]
    roundtrip_staging_path = paths["subproc_staging_roundtrip"]
    _run_cli_staged(
        ["roundtrip", str(eval_score_path), "--format", "json", "-o", str(roundtrip_staging_path)],
        roundtrip_staging_path,
        roundtrip_path,
    )
    roundtrip_bytes = roundtrip_path.read_bytes()
    hashes["roundtrip"] = _sha256_bytes(roundtrip_bytes)

    adherence_path = paths["adherence"]
    adherence_staging_path = paths["subproc_staging_adherence"]
    _run_cli_staged(
        [
            "score-adherence",
            str(eval_score_path),
            "--format",
            "json",
            "-o",
            str(adherence_staging_path),
        ],
        adherence_staging_path,
        adherence_path,
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

    # `--output-dir` is staged (H1) into `subproc_staging_package_dir`, then
    # every file it produced is individually republished into the reserved
    # `package_dir` (module docstring's "H1" section).
    package_dir = paths["package_dir"]
    package_dir.mkdir(exist_ok=True)
    package_staging_dir = paths["subproc_staging_package_dir"]
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
            str(package_staging_dir),
        ]
    )
    _publish_staged_directory(package_staging_dir, package_dir)
    package_json_path = paths["package_json"]
    hashes["package"] = _sha256_file(package_json_path)
    # `svprpe package` always emits compilation_report.json alongside the
    # package — record it too so the run's hash manifest covers the whole
    # emitted pair (PR #247 Codex review round 20: an unhashed sibling could
    # carry provenance claims outside the attestation surface).
    hashes["package_compilation_report"] = _sha256_file(paths["package_compilation_report"])

    # `-o` is staged (H1) before being republished to the reserved
    # `observe_report_path` (module docstring's "H1" section).
    observe_report_path = paths["observe_report"]
    observe_report_staging_path = paths["subproc_staging_observe_report"]
    _run_cli_staged(
        [
            "observe",
            str(package_json_path),
            str(take_wav_path),
            "--manifest",
            str(manifest_path),
            "-o",
            str(observe_report_staging_path),
        ],
        observe_report_staging_path,
        observe_report_path,
    )
    observe_report_bytes = observe_report_path.read_bytes()
    hashes["observe_report"] = _sha256_bytes(observe_report_bytes)

    roundtrip_report = json.loads(roundtrip_bytes)
    fields_by_name = {field["field"]: field for field in roundtrip_report["fields"]}
    key_axis = _key_axis(fields_by_name["key"])
    brightness_axis = _brightness_axis(fields_by_name["brightness"])

    observe_report = json.loads(observe_report_bytes)
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
    # L1 pre-publish guard (PR #247 Codex review round 23, P1) — see
    # `JudgeInputCopyDriftError`'s docstring. `judge_arrangement`/
    # `judge_capability_profile` were last read by the `package` subprocess
    # call above, arbitrarily long before this point in wall-clock time —
    # the longest of this run's drift windows, and exactly what this guard
    # exists to catch just before the report attributing to those bytes
    # becomes visible.
    _reject_judge_input_copy_drift(paths, verified_inputs)
    # M1 pre-publish guard (PR #247 Codex review round 25, P1, family
    # terminus): re-verifies every generated artifact this run hashed —
    # take.wav, eval_score.yaml, validation.json, roundtrip.json,
    # adherence.json, the identity manifest, the package pair, and
    # observe_report.json — against the digest recorded in `hashes` at
    # generation time, immediately before this run's report is published.
    # See `RecordedArtifactDriftError`'s docstring / module docstring's "M1"
    # section.
    _reject_recorded_artifact_drift(paths, hashes)
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
