# CLI Reference

## Installation

```bash
pip install -e ".[dev]"
# Include Demucs when using --separate:
pip install -e ".[dev,separate]"
# Lyrics transcription (--lyrics / lyrics-adherence; bundles Demucs for the
# default vocals-separation path):
pip install -e ".[dev,lyrics]"
```

## Commands

### `svprpe extract <audio>`

Extract RPE (physical + semantic) from an audio file.

```bash
svprpe extract track.wav -o rpe.json
svprpe extract track.wav --valley-method rms_percentile -o rpe.json
svprpe extract track.wav --separate --separation-model htdemucs_ft -o rpe.json
svprpe extract track.wav --clap-semantic -o rpe.json
svprpe extract track.wav --clap-sections -o rpe.json
svprpe extract track.wav --lyrics -o rpe.json
svprpe extract track.wav --lyrics --lyrics-no-separate -o rpe.json
```

`--separate` is opt-in because Demucs is slow and requires the optional
`svp-rpe[separate]` dependency. When enabled, the emitted RPE includes
`physical.stem_rpe` for vocals, drums, bass, and other.

`--clap-semantic` is opt-in and requires the optional `svp-rpe[semantic-embed]`
dependency. It reads the source audio's semantic content with CLAP against a
fixed battery of named semantic axes (`config/semantic_probe_axes.yaml`) and
attaches per-axis `contrast_fit` readings to `learned_annotations.semantic_axes`
— isolated from `PhysicalRPE` / `SemanticRPE`. `--clap-sections` reads the same
axis battery per structural section instead (the "emotional arc"; superset of
`--clap-semantic`), attaching `learned_annotations.semantic_axis_sections`. See
[Semantic Sensor: CLAP](semantic_sensor_clap.md).

`--lyrics` is opt-in and requires the optional `svp-rpe[lyrics]` dependency
(faster-whisper + demucs — the extra bundles Demucs because the default path
isolates vocals first, so `pip install -e ".[lyrics]"` alone stands up the
default separation-included path). It transcribes the source audio's lyrics —
isolating vocals via Demucs first by default (`--lyrics-no-separate`
transcribes the full mix instead, the demucs-free opt-out) — and attaches the
result to `learned_annotations.lyrics_transcription`.
Combine with `--clap-semantic` / `--clap-sections` to populate both sensors on
the same `learned_annotations` record. See
[Lyrics Transcription Sensor](lyrics_transcription_sensor.md).

### `svprpe generate <rpe.json>`

Generate SVP from RPE JSON.

```bash
svprpe generate rpe.json --output-dir ./output --format yaml
svprpe generate rpe.json --format text
```

### `svprpe compose <composition_score.yaml>`

Render a Composition Score YAML into a prompt for an external generator.

```bash
svprpe compose score.yaml
svprpe compose score.yaml --format json -o prompt.json
svprpe compose score.yaml --max-chars 2000
```

`--max-chars` overrides `rendering.prompt_max_chars`. Default `--format` is
`text`; `json` emits the structured `ExternalPromptAdapter` payload.

`text` format keeps stdout as pure paste-ready prompt text; non-empty
`negative_tags` are surfaced on **stderr** as one line labelled with the
backend's negative channel (suno/external = `exclude_styles`, musicgen =
`negative_prompt`) so exclusions are never silently lost on
`omit_body_negative` backends. Machine consumers should use `--format json`
(canonical; carries `negative_tags` in the payload).

For the `suno` backend, `structure` prose is not compiled into the prompt
body (measured dead in K2-seg batch 3); it is instead compiled into
`section_tags`, a section-tag script for Suno's Lyrics field. In `text`
format, a non-empty `section_tags` is surfaced on **stderr** the same way as
`negative_tags`; `--format json` carries it as a `section_tags` field, but
the key itself is **omitted** from the JSON payload when the backend does
not emit it or `structure` is empty (`GeneratedPrompt.serialize_without_empty_optionals`
drops `section_tags` when it is `None`, mirroring how `SemanticLayer.lyrics_presence`
is omitted when unset; `negative_tags` by contrast is always present as a
list). Machine consumers should treat a missing
`section_tags` key as "no tags", not check for `null`.

### `svprpe arrange <composition_score.yaml> <arrangement.yaml> (--output-dir <dir> | --builds-root <dir>)`

Resolve an `ArrangementSpec` (AR1-1: `src/svp_rpe/arrange/`) against a base
Composition Score and write the derived score plus a provenance bundle:

```bash
svprpe arrange composition_score.yaml arrangement.yaml --output-dir ./out
```

Writes exactly three files under `--output-dir`:

- `derived_score.yaml` — the resolved `CompositionScore` (loader-valid, reloadable
  via `load_composition_score`)
- `arrangement_bundle.json` — `schema_version: "arrangement-bundle/0.2"`,
  `arrangement_id`, `source_score`/`arrangement_spec` (`path` + SHA-256 of the raw
  input bytes), `changes`, `outputs` (each a `{path, sha256}` object — the SHA-256
  is computed from the exact bytes published for that file), and
  `content_digest`/`content_digest_basis` (see below)
- `arrangement_diff.json` — `schema_version: "arrangement-diff/0.1"`,
  `arrangement_id`, and the same `changes` as the bundle

All three artifacts are constructed in memory before any file is written, so a
hard-preservation conflict, an unknown preservation path, invalid YAML, a
missing input file, or a final `CompositionScore` validation failure leaves
`--output-dir` without partial output (exit code `1`, one-line error on
stderr). Given the same inputs and arguments, repeated runs produce
byte-identical output regardless of `--output-dir`. The derived score can be
fed straight back into `svprpe compose`.

`content_digest` is `sha256(canonical_json({artifact_name: sha256_hex, ...}))`
over `outputs` only (the bundle cannot hash itself, same reasoning as
`package_sha256` below); `content_digest_basis` names that definition
(`"sha256-of-canonical-json-artifact-name-to-sha256/v1"`) so a consumer can
tell which hashing convention produced it. It intentionally carries no
execution-environment information (see `--builds-root` and `package`'s
`invocation_provenance` below).

`--output-dir` and `--builds-root` are mutually exclusive; exactly one is
required (both given or both omitted exits `2`). `--builds-root <root>`
publishes the same three artifacts to `<root>/builds/<content_digest>/`
instead, and updates `<root>/latest.json` (`schema_version:
"builds-latest/0.1"`, `{"content_digest": "<64hex>"}`) to point at it. Before
anything is written, a preflight rejects `<root>/latest.json` unless it is
either absent or a plain, non-symlink file (exit `1`, nothing under
`builds/` touched) — the one path this scheme ever overwrites must not be a
directory or a symlink — and likewise rejects `<root>/builds` itself if it
is a symlink (any target, including a real directory), since every digest
directory lives under it; `<root>` (the `--builds-root` argument) is a
caller-supplied path like `--output-dir` and is not subject to this check —
a symlinked `builds_root` publishes normally. A digest directory is **immutable**: if `<root>/builds/<content_digest>/`
already exists as a directory from a previous run, its artifacts are never
overwritten, appended to, or repaired — but before `latest.json` may be moved
onto it (before the directory is "blessed"), its one *descriptive* file
(`arrangement_bundle.json`) is read once and compared byte-for-byte against
what this invocation would have published for that filename, so a directory
that merely happens to occupy this digest path isn't blindly trusted. If
that file is missing or unreadable, the run fails (exit `1`, `latest.json`
left untouched) rather than treating the directory as a valid prior
publication. Re-running an identical build (first-publish-wins: the existing
bytes match) prints an "already published" advisory and exits `0` with the
directory untouched; because `content_digest` covers only
`derived_score.yaml` + `arrangement_diff.json`, a same-digest rebuild whose
recorded provenance differs (e.g. the same `arrangement.yaml` referenced via
a different path) is *also* left untouched but gets a distinct "provenance
differs from this invocation" advisory instead — the first publication's
provenance always wins, `latest.json` still moves to point at it. A byte
difference is only accepted as provenance drift after the existing descriptor
proves self-consistent: it must parse as JSON, declare this directory's
current `schema_version` and `content_digest`, and recompute to that same
digest from its own recorded output hashes — otherwise the run fails (exit
`1`, `latest.json` untouched). Either way — byte-identical or provenance
drift accepted after the four descriptor checks — blessing the directory
also means every content artifact the descriptor declares
(`derived_score.yaml` + `arrangement_diff.json`) is confirmed to exist and
hash to its declared value; a descriptor whose bytes are pristine (or
otherwise self-consistent) does not by itself prove the directory around it
is still intact, so this check runs even on the byte-identical fast path. If
`<root>/builds/<content_digest>` already exists as something other than a
*real, non-symlink* directory (a plain file, any symlink — including one
pointing at an otherwise-valid directory — or a dangling symlink), the run
fails outright (exit `1`, `latest.json` left untouched) rather than treating
it as published. The descriptor and every declared content artifact are held
to the same rule one level down: each is rejected outright if it is a
symlink (regardless of what it resolves to — even a symlink to an
otherwise byte-identical file) or any non-regular-file entity, *before* it
is ever read. A byte difference in the descriptor is only accepted as
provenance drift after it proves self-consistent: it must parse as JSON,
declare this directory's current `schema_version`, **pass full schema
validation** (nested shapes, `sha256`/`schema_version` patterns, and unknown
top-level keys — `arrange`'s bundle validates against a reader-only
`ArrangementBundleDescriptor` model built for exactly this check;
`package`'s report reuses `CompilationReport` itself), declare this
directory's `content_digest`, recompute to that same digest from its own
recorded output hashes, *and* differ from this invocation's own descriptor
in only a small whitelist of top-level fields (`source_score` /
`arrangement_spec` for `arrange`'s bundle) — every other field
(`arrangement_id`, `changes`, `outputs`, `content_digest_basis`, etc.) must
match exactly, since fields like `arrangement_id` and `changes` aren't part
of `content_digest` at all and a tampered value there must not be waved
through as mere provenance just because the digest still happens to match.
A whitelisted field being present doesn't exempt it from schema validation
either: a legitimately-drifting `inputs` or `invocation_provenance` must
still be the right shape, not merely a differing value of any type. Any of
these checks failing fails the run (exit `1`, `latest.json` untouched).
Re-running with different
inputs that resolve to a different `content_digest` publishes a sibling
directory; older digest directories are never pruned. Blessing therefore
means "descriptor schema-valid and self-consistent (including its
provenance-only fields) + every declared output present, non-symlinked, and
hash-matching" — a digest
directory `latest.json` points at is a complete publication matching its own
bookkeeping, though this is still not a full audit (it never rehashes
undeclared files or recurses beyond what the descriptor lists, and nothing in
the directory is ever rewritten); an independent, fully recursive audit is
left to a future `verify`-style command.

If a first publish writes its artifacts successfully but the trailing
`latest.json` update itself then fails, the freshly published digest
directory is **not** rolled back — it is already a complete, valid
publication, and deleting it would violate the immutability contract for no
benefit. The command still fails on that run, but the digest directory
self-heals `latest.json` on its own: a subsequent identical invocation finds
it via the already-published no-op path and retries the pointer update, with
no separate repair step.

### `svprpe package <score.yaml> <identity.yaml> <arrangement.yaml> --capability-profile <profile.yaml> (--output-dir <dir> | --builds-root <dir>)`

Compile a `PreservationContract` inline, match every requested identity anchor
against the generator's `InputCapabilityProfile`, and write the deterministic
handoff artifacts:

```bash
svprpe package composition_score.yaml identity.yaml arrangement.yaml \
  --capability-profile config/capability_profiles/suno.yaml \
  --output-dir ./package-out
```

The command writes exactly `performance_package.json` and
`compilation_report.json`. The package keeps requested policy, delivery,
control, and observation as separate states. Only `delivered` or
`experimental` anchors appear in `channel_artifacts`; compile-time control is
`unknown` and observation is `not_observed`. Elastic anchor statuses retain
their `allow` list and optional `tolerance_profile` for downstream policy-aware
observation. Unsupported MIDI or symbolic
melody anchors are recorded as unsupported rather than rewritten into prompt
instructions. Each artifact path is explicitly relative to the identity
manifest directory. Its `artifact_base` records a concrete locator from the
performance-package directory to that manifest directory, so consumers resolve
`package_dir / artifact_base.locator / artifact`. The locator is relative and
portable within the retained directory layout; artifacts are referenced and
hash-pinned rather than copied into `--output-dir`.

A committed, real end-to-end example lives under
`examples/arrangement/midnight_signal/`: `identity_manifest.yaml` (lyrics +
melody anchors), `edm.identity.arrangement.yaml` (the EDM variant with both
anchors declared `hard`), and the resulting
`expected/e2e_edm/performance_package.json` byte-pin (see
`tests/test_e2e_vertical_slice.py`). It pins the honest outcome for a `suno`
`standard` profile: the `melody` anchor is `requested_mode: hard` but
`delivery.status: unsupported` (no `symbolic_melody` channel exists), while
the `lyrics` anchor is `hard` and `delivered` — the same package records both
states side by side rather than implying delivery from a `hard` request.

The capability profile's `generator` must match the derived score's resolved
backend profile key (`external` resolves to `suno`). A mismatch fails before
either artifact is published. Prompt rendering also uses that resolved
generator, so an `external` score packaged for Suno receives the same
Suno-specific prompt and section-tag behavior as `target_backend: suno`.
The resolved generator device profile is loaded once, rendered from that exact
model, and pinned in both outputs by a canonical-model-JSON SHA-256 (or an
explicit `not_found` status), so prompt provenance does not depend on an
unrecorded local-versus-packaged config choice.

**`generator_variant`** (`InputCapabilityProfile`, `schema_version:
"input-capability/0.2"`, required, non-empty): identifies the *route* a
profile describes — e.g. Suno's standard text/lyrics/tags flow vs. a future
remix/cover/reference-audio route, or MusicGen's standard text-to-music flow
vs. a future melody-conditioning route. The unit is one (`generator`,
`generator_variant`) pair per profile file; both committed profiles
(`config/capability_profiles/{suno,musicgen}.yaml`) currently declare
`generator_variant: "standard"` — no variant-specific profile exists yet
(committed evidence for one, e.g. a cover-route profile, is not yet in the
repo). `generator_variant` is a free string, not a closed enum: adding a new
route does not require another schema bump. It is copied verbatim from the
profile onto both `performance_package.json` and `compilation_report.json`
(a handoff and its report describe the same route as the profile that
compiled them); there is no cross-check against `device_profile`, which
carries no variant of its own. **Grip stays variant-unaware**: `control_profile`
/ `DeviceProfile` (`config/device_profiles/`) are not extended with
`generator_variant` by this schema — a variant-scoped grip mechanism remains
the deferred "model-scope" work noted in
[`control_profile.md`](control_profile.md).

`model_version` and `interface` (both `Optional[str]`, default `None`) record,
only when independently confirmed from a repository record, which concrete
model build and access surface a profile's `input_channels` measurements
describe (e.g. `interface: "web-ui"` for Suno's user-driven browser flow,
`model_version: "facebook/musicgen-small@<revision>"` and `interface:
"local-transformers"` for MusicGen's local `transformers` pipeline). Neither
field is fabricated when unconfirmed — Suno does not publish a model
version/pin, so its profile leaves `model_version: null` with an explanatory
note rather than guessing.

Advisory mode is the default and records capability warnings. Add
`--strict-capabilities` to fail when any hard anchor is `unsupported` or
`unknown`; hard anchors on experimental but usable channels remain deliverable.
Both JSON files are constructed before publication and are published together
with rollback on failure. `compilation_report.json` (`schema_version:
"compilation-report/0.3"`) pins the exact published package bytes with
`package_sha256`; neither output contains timestamps, absolute paths, or the
output directory.

`compilation_report.json` also carries `content_digest` /
`content_digest_basis` (same definition as `arrange`'s bundle:
`sha256(canonical_json({"performance_package.json": package_sha256}))`, basis
`"sha256-of-canonical-json-artifact-name-to-sha256/v1"`) and a separate
`invocation_provenance.compiler` block —
`{"package_version": "<installed svp-rpe version or null>", "git_commit":
"<40hex checkout HEAD or null>"}`. The split is deliberate: `content_digest`
answers "does this content reproduce" (same inputs + same compiler behavior
→ same digest) and is used as the `--builds-root` publish key;
`invocation_provenance` answers "what environment produced this run" and is
excluded from the digest so it never causes a rebuild to publish under a
different directory. Both fields degrade to `null` rather than being
fabricated (installed/wheel environment with no dist-info, no `git`, not a
checkout, or any detection failure) — this also means `git_commit` cannot
distinguish a dirty working tree from a clean checkout at that commit.

`--output-dir` and `--builds-root` are mutually exclusive; exactly one is
required (both given or both omitted exits `2`). `--builds-root <root>`
publishes `performance_package.json` + `compilation_report.json` to
`<root>/builds/<content_digest>/` and updates `<root>/latest.json`
(`schema_version: "builds-latest/0.1"`) to point at it, with the same
immutable-digest-directory / mutable-`latest.json` contract described for
`arrange` above — here the one *descriptive* file read back for the
byte-for-byte no-op check is `compilation_report.json` (first-publish-wins:
an already-published digest directory is left untouched; only `latest.json`
moves, with an "already published" advisory on exit `0`, or a "provenance
differs from this invocation" advisory if this run's `compilation_report.json`
bytes differ from the one already on disk for that digest — `content_digest`
covers only `performance_package.json`, so `invocation_provenance` drift alone
lands here). Because re-running with a stale checkout's inputs after an
implementation change still reproduces the same `content_digest`, publishing
never mutates a digest directory's `invocation_provenance` to match a newer
compiler run — that is the immutability contract's direct consequence, not a
bug. Blessing an existing digest directory with `latest.json` here also
verifies `performance_package.json` itself is present, non-symlinked, and
hashes to what `compilation_report.json` declares, on the same terms as
`arrange`'s `derived_score.yaml` + `arrangement_diff.json` check above; the
provenance-only whitelist for `compilation_report.json` is `inputs`,
`invocation_provenance`, and `mode` — every other top-level field (`work_id`,
`generator`, `package_sha256`, `content_digest`, `warnings`, etc.) must match
exactly even when the descriptor bytes otherwise differ. `warnings` is
deliberately *not* whitelisted: every warning `build_performance_package`
emits is a pure function of facts already baked into
`performance_package.json` bytes (channel support / anchor delivery status),
so a matching `package_sha256` implies matching `warnings` — device-profile
advisories are a separate stderr-only channel that never lands in
`warnings` (#128) — so a `warnings` difference here is a tamper signal, not
invocation drift. `inputs`, by contrast, stays whitelisted: it records
input-file byte hashes, the same category of invocation provenance
`invocation_provenance.compiler.git_commit` already is, and requiring it to
match exactly would reject legitimate re-runs where a parse-equivalent but
differently formatted input produces the same package under a different
input-file hash.

Because `--builds-root`'s locator computation stands in for the not-yet-known
`content_digest` with a reserved, same-depth placeholder directory
(`<root>/builds/<64 zeros>/`; see the `artifact_base.locator` note above), an
identity-manifest artifact that happens to actually live inside that reserved
subtree is rejected before publication (exit `1`) — the locator computed
against the placeholder would not describe where the artifact ends up
relative to the real (differently-named) digest directory. A real, already
published digest directory is not similarly reserved and is never rejected.
The placeholder path itself is also rejected outright if it is a symlink
(exit `1`, before compilation even starts) — resolving it would compute the
locator relative to wherever the symlink actually points rather than the
reserved placeholder path; a real directory sitting at the placeholder path
is fine (same depth, so the locator is still correct) and is not rejected.

### `svprpe observe <package.json> <audio> --manifest <identity_manifest.yaml> -o <report.json>`

Record post-generation anchor observations against a generated artifact (AR4). Like
`roundtrip` / `score-adherence` / `lyrics-adherence` / `audit`, this is a descriptive
instrument and intentionally does not emit a pass/fail verdict:

```bash
svprpe observe build/performance_package.json generated_track.wav \
  --manifest examples/arrangement/midnight_signal/identity_manifest.yaml \
  -o observation_report.json
```

Before measuring anything, `observe` verifies the provenance chain (D-3 in the AR4
Design Memo) and exits `1` without measuring if any link is broken:

1. the `--manifest` file's sha256 must equal `package.inputs.identity_manifest.sha256`
2. every manifest anchor's artifact hash must match the file on disk (via the same
   `load_identity_manifest` loader `package` uses — reused here, not reimplemented)
3. `package.json` itself must pass `PerformancePackage` schema validation

Only then does it read the audio once (shared across every anchor) and record, in
`observation_report.json` (schema `observation-report/0.1`):

- `package_sha256` / `generated_artifact.{path,sha256}` — provenance of what was compared.
  `generated_artifact.path` is recorded verbatim as the `<audio>` argument string, not
  resolved to an absolute path — if the report is a committed provenance artifact (as
  the AR4 fixtures under `examples/arrangement/*/observed/` are), invoke `observe` with
  a stable relative `<audio>` path (e.g. run from the audio's own directory and pass
  just its filename) so the report stays byte-reproducible across machines/checkouts
  (Codex PR #191 round 2 review: an absolute scratch path leaked into a committed
  fixture this way).
- one `AnchorObservation` per manifest anchor, each with:
  - `sensor.{name,available,reason}` — which sensor ran (or why it didn't)
  - `measurements` — raw sensor output only, no derived judgment
  - `adherence_status` / `determination` — see below

**`adherence_status`/`determination` follow exactly 3 branches (Design Memo D-1),
and no others**: this PR fixes *what was measured*, not *what counts as
preserved beyond exact identity* — that threshold judgment is deferred to a
future Design Memo.

| Case | `adherence_status` | `determination` |
|---|---|---|
| No sensor wired for the anchor's domain (lyrics/melody/rhythm/structure/motif in this PR) | `not_observed` | `no_sensor` |
| Sensor ran and measured an exact identity match (harmony: the collapsed observed sequence matches the canonical progression's cycle alternation all the way through, no leftover tail) | `preserved` | `exact_match` |
| Sensor ran but did not match exactly | `not_observed` | `deferred` |

The only sensor wired in this PR is **harmony**: it extracts the generated audio with
`extract_rpe_from_file` (the same dependency-free `compute_chord_events` chroma-template
detector R4 uses) and normalizes `PhysicalRPE.chord_events` to `(root, quality)` pairs.
Because the deterministic performer plays the canonical progression once per
chord-playing section (so the honest comparison is against *repetitions* of the
progression, not a single pass), the instrument records two families of measurement:

- **raw, frame-level** (`chord_sequence_match_rate` / `repeated_chord_sequence_match_rate`)
  — position-aligned comparisons against the raw `chord_events` list. Kept for
  transparency, but structurally low-signal here: `chord_events` has one entry per
  detected chord-frame run (irregular lengths), so a straight position-by-position
  compare drifts out of phase almost immediately even for a musically faithful
  performance. **Not used for the D-1 identity gate.**
- **collapsed cycle-alignment** (`canonical_length`, `observed_length`,
  `collapsed_observed_length`, `matched_cycle_prefix_length`, `full_cycles`,
  `collapsed_match_fraction`, `unmatched_tail_length`, `unmatched_tail_head`) —
  the raw `chord_events` sequence is collapsed (adjacent identical entries merged
  into one), then matched from the start against the canonical progression's
  infinite repeating alternation (itself collapsed across the cycle boundary,
  e.g. a 4-chord progression whose first and last chord are identical collapses
  2 cycles to 7 entries, not 8). `matched_cycle_prefix_length` is how much of the
  collapsed observed sequence matches continuously before the first divergence,
  and `full_cycles` is how many full passes through the canonical progression
  that prefix represents. **This is the D-1 identity gate**: `preserved` only
  when `collapsed_observed_length > 0`, `matched_cycle_prefix_length` equals it
  exactly (no leftover tail), *and* `full_cycles >= 1` — a prefix match alone
  isn't enough, since a drone/truncated output can collapse to a proper prefix
  of the canonical progression (e.g. a single chord) and match it exactly
  without ever completing one full cycle. `full_cycles` is always recorded in
  `measurements` (on both branches), and `note` always states the fact the gate
  relied on — "matches the canonical alternation exactly (N full cycle(s))" on
  `preserved`, or the number of full cycles matched and the length of the
  unmatched tail on `deferred` — a plain fact about the two sequences, not an
  interpretation of *why* they diverge.

Concretely, the deterministic `expected/edm/derived_score.yaml` E2E fixture measures
`collapsed_observed_length: 10`, `matched_cycle_prefix_length: 7`, `full_cycles: 2`
— the collapsed chord sequence recovers 2 full canonical cycles (matching the
score's 2 non-drone, chord-playing sections) before a 3-entry tail diverges.
*Interpretation* (not recorded in the report, which states facts only): the
3-entry tail most likely comes from the drone-only intro/bridge sections, where
the chroma-template detector still emits a (arbitrary-looking) major/minor
label for a bare root tone that was never meant to carry a chord progression —
see [`arrangement_identity_planning.md`](arrangement_identity_planning.md) AR4.

lyrics/melody anchors are recorded as `available: false` with a `reason` (they need
the optional `lyrics` / `basic-pitch` extras — not wired here); their future
connection points are `eval/lyrics_match.py` / `rpe/learned/lyrics_adapter.py` and
`rpe/learned/basic_pitch_adapter.py`.

`observation_report.json` is a re-observable sidecar, not an immutable build artifact:
re-running `observe` against the same `-o` path overwrites it (unlike
`performance_package.json`'s byte-pin/builds-root immutability contract).

### `svprpe verify <package.json> --manifest <identity_manifest.yaml>`

Exhaustively check a single `PerformancePackage`'s own internal consistency —
read-only, writes nothing, ever:

```bash
svprpe verify build/performance_package.json \
  --manifest examples/arrangement/midnight_signal/identity_manifest.yaml
```

The first argument accepts either the `performance_package.json` path directly or
its containing directory. Where `observe` stops at *identifying* the observation
target (manifest sha256 / `work_id` / `anchor_statuses` id set — PR #187 review
round 16), `verify` picks up exactly there and checks everything else a `package` +
its sibling `compilation_report.json` + the `--manifest` chain declare about
themselves, in four groups:

1. **V1 — package load**: not a symlink/non-regular file, reads, and validates
   against `PerformancePackage`'s schema (which already enforces
   `channel_artifacts` cross-references via `_validate_delivery_references` — not
   re-checked here).
2. **V2 — compilation report**: the co-located `compilation_report.json` is
   present, valid, and its `work_id` / `generator` / `generator_variant` /
   `inputs` / `package_sha256` / `content_digest` all agree with the package (the
   digest is independently recomputed via `arrange.bundle.compute_content_digest`,
   not merely read back).
3. **V3 — identity manifest chain**: the `--manifest` file sha256-pins to
   `package.inputs.identity_manifest.sha256`, parses and hash-verifies every
   source/anchor artifact it declares (via the same
   `parse_identity_manifest_with_artifacts` `observe` uses), and its `work_id` /
   anchor id set agree with the package.
4. **V4 — channel_artifacts**: every entry's `artifact_base.locator` resolves
   (relative to the package directory) to an existing directory — legitimately
   *outside* the package directory, `".."` being the normal shape, so no
   confinement applies there — that must itself resolve to the same directory
   as the supplied `--manifest`'s own parent directory (Codex review round 2,
   PR #190: an existing directory alone is not enough — a hash-matching
   artifact copy planted anywhere else, with the package/report hashes
   recomputed to match, would otherwise sail through every other V4 check).
   Its `artifact` also resolves confined under that base directory
   (`arrange.pathsafe.resolve_confined`), its bytes hash to the declared
   `artifact_sha256`, and its `anchor_id` is one the manifest actually
   declares — and, once that anchor_id is found, its `artifact` /
   `artifact_sha256` / `artifact_type` / `media_type` / `format_version` are
   each compared field-by-field against the same-id manifest anchor (Codex
   review round 1, PR #190: an id-set-only check let a reference be
   retargeted to a different file under `artifact_base` whose bytes still
   matched the package-local hash).

Every group's checks are collected in full before anything is printed — a single
failure never hides the rest (exit `1` if *any* check across all groups fails).
The only exception is a **structural** load failure — the package file itself
failing to parse/validate (V1), or the manifest failing to parse/validate
against `IdentityManifest`'s schema (V3; artifact hash mismatches are ordinary
collected failures, not structural) — which aborts every check that
depends on the missing object (V1 failing skips V2-V4 entirely; V3 failing skips
only V4).

Out of scope, left to follow-up work: a recursive audit of an entire
`--builds-root` tree (a distinct, larger surface than a single package — the
no-op-publish blessing check `arrange`/`package` already do at L209 above is
scoped to one digest directory, not this); cross-checking against an
`ObservationReport` sidecar (AR2-3 depends on structure-anchor policy that
hasn't landed); repairing anything found broken; and any musical/perceptual
verdict — `verify` only ever reports structural pass/fail.

### `svprpe measure <audio>`

Measure the seven required `CompositionScore.physical` fields from one audio file.
Use this as a transcription aid: each output field includes the sensor name, raw
measurement, score-facing value, unit, and calibration notes.

```bash
svprpe measure track.wav
svprpe measure track.wav --fields bpm,key,brightness
svprpe measure track.wav --output measurement.json
```

When `--output` is set, the command writes deterministic JSON. Without
`--output`, it prints a compact Rich table for inspection.

### `svprpe transcribe <audio>`

Create a loader-valid draft `CompositionScore` YAML from one audio file. The
physical layer is filled from `svprpe measure` sensors. Author-facing semantic
and prose fields are intentionally left as `TODO(transcribe): ...` sentinels so
they are easy to find and edit.

```bash
svprpe transcribe track.wav
svprpe transcribe track.wav --output draft_score.yaml
svprpe transcribe track.wav --clap-semantic --output draft_score.yaml
```

The command is deterministic for the same extracted RPE. It is a drafting aid,
not an automatic final composition brief.

`--clap-semantic` (opt-in, requires the `svp-rpe[semantic-embed]` extra) prepends
the CLAP semantic-axis readings of the source audio as a YAML comment block above
the draft. It is **advisory instrument context for authoring** the blank
`semantic.*` fields — it does not fill them (those stay `TODO(transcribe): ...`
per DD-D). The comment block keeps the draft loader-valid. See
[Semantic Sensor: CLAP](semantic_sensor_clap.md).

### `svprpe roundtrip <composition_score.yaml>`

Run the deterministic R0 preservation harness:

```text
CompositionScore -> perform(FAITHFUL_TAKE) -> RPE extraction -> draft score -> diagnosis
```

```bash
svprpe roundtrip examples/roundtrip/synth_01_source.yaml
svprpe roundtrip examples/roundtrip/synth_01_source.yaml --format json
svprpe roundtrip examples/roundtrip/synth_01_source.yaml --format json -o roundtrip.json
```

The output is a field-by-field descriptive report: source value, transcribed
value, diagnosis, grip, and sensor. It intentionally does not emit verdict,
pass/fail, or loss keys.

### `svprpe score-adherence <composition_score.yaml>`

Judge whether the score's `control_profile`-**tight** fields are honored, both at
compile time (PR1.5: tight fields are not dropped from the prompt) and through the
deterministic roundtrip (preserved vs. not):

```text
tight fields (per resolved backend) -> compile (ExternalPromptAdapter) + roundtrip diagnosis
```

```bash
svprpe score-adherence examples/composition/midnight_signal/composition_score.yaml
svprpe score-adherence examples/composition/midnight_signal/composition_score.yaml --format json
```

The output is a per-field table (`compiled_kept`, `roundtrip` diagnosis, `preserved`)
plus tight/kept/preserved counts. Like `roundtrip`, it is a descriptive instrument and
intentionally does not emit a global verdict or pass/fail key. See
[`control_profile.md`](control_profile.md).

### `svprpe lyrics-adherence <audio> --expected <lyrics.txt>`

Check whether generated audio sings the ordered expected lyrics — the output-side
counterpart to `extract --lyrics`:

```bash
svprpe lyrics-adherence generated_track.wav --expected lyrics.txt
svprpe lyrics-adherence generated_track.wav --expected lyrics.txt -o report.yaml
svprpe lyrics-adherence generated_track.wav --expected lyrics.txt --lyrics-no-separate
```

Transcribes `audio` with faster-whisper (requires the `svp-rpe[lyrics]` extra) and
reports, per expected line (one per line in the `--expected` text file), the best
char-level similarity ratio against the transcription plus an `overall_similarity`.
The terminal table also carries an `out_of_order` column (a textual `yes` marker on
lines whose char-offset cursor regressed), and `order_ratio` is printed alongside
`overall_similarity` — so order problems are visible interactively, not only in the
`-o` YAML report. Like `roundtrip` / `score-adherence` / `audit`, this is a
descriptive instrument and intentionally does not emit a pass/fail verdict. See
[Lyrics Transcription Sensor](lyrics_transcription_sensor.md).

### `svprpe roundtrip-corpus <manifest.yaml>`

Run or replay the R1 roundtrip corpus manifest. Records with a local
repo-relative audio file and matching SHA-256 are regenerated through
audio -> RPE -> draft Score. Records without resolvable audio are replayed as
observation logs.

```bash
svprpe roundtrip-corpus examples/roundtrip/corpus/manifest.yaml
svprpe roundtrip-corpus examples/roundtrip/corpus/manifest.yaml --format json
svprpe roundtrip-corpus examples/roundtrip/corpus/manifest.yaml --format json -o corpus.json
```

The output compares only fields whose manifest `send_form` is `numeric_knob`.
It is a descriptive corpus table and intentionally does not emit verdict,
pass/fail, or loss keys.

### `svprpe roundtrip-rep <composition_score.yaml> <takes_manifest.json>`

Run the R3 stochastic performer repetition harness (R3-1/R3-2/R3-3,
[`roadmap_goal2.md`](roadmap_goal2.md)): measure roundtrip preservation across
a batch of `n` independently generated takes of the same score.

```text
CompositionScore + N takes -> per-take RPE extraction -> draft score -> diagnosis
  -> per-field repetition rate (R3-2) -> "closest take" selection (R3-3)
```

```bash
svprpe roundtrip-rep examples/roundtrip/synth_01_source.yaml takes_manifest.json
svprpe roundtrip-rep examples/roundtrip/synth_01_source.yaml takes_manifest.json --format json
svprpe roundtrip-rep examples/roundtrip/synth_01_source.yaml takes_manifest.json --audio-dir ./takes
```

`takes_manifest.json` follows the schema written by
`scripts/collect_musicgen_takes.py perform` (`samples[]` with `audio_path` /
`audio_sha256`); `--audio-dir` defaults to the manifest's parent directory.
Each sample's audio is re-hashed and checked against the manifest's pinned
`audio_sha256` (fail-fast on mismatch); samples marked `excluded` are
skipped. The output reports, per field, how many of the `n` takes preserved
the authored value (`preserved_rate`, `diagnosis_counts`) and a mechanical
`selection` of the take that matches the most fields
(`basis="preserved_field_count"`). Like `roundtrip` / `roundtrip-corpus`, this
is a descriptive instrument — `selected_take_id` identifies the closest take
to the score, not a quality judgment, and the output intentionally does not
emit verdict, pass/fail, or loss keys. See
[`musicgen_backend.md`](musicgen_backend.md) §6.

### `svprpe evaluate --audio <audio> [--svp <svp.yaml>]`

Evaluate audio. Without `--svp`: self-evaluate. With `--svp`: compare against external SVP.

```bash
# Self-evaluation
svprpe evaluate --audio track.wav -o evaluation.json
svprpe evaluate --audio track.wav --baseline edm -o evaluation.json
svprpe evaluate --audio track.wav --separate -o evaluation.json

# Compare against external SVP
svprpe evaluate --audio track.wav --svp design.yaml -o evaluation.json
```

Output includes `action_hints` when `--svp` is provided.

### `svprpe compare`

Compare reference audio against candidate audio/SVP.

```bash
# Reference audio vs candidate SVP
svprpe compare --reference-audio ref.wav --candidate-svp candidate.yaml

# Reference audio vs candidate audio
svprpe compare --reference-audio ref.wav --candidate-audio gen.wav

# With reference SVP
svprpe compare --reference-audio ref.wav --candidate-audio gen.wav --reference-svp ref.yaml
```

Output: `semantic_diff`, `physical_diff`, `action_hints`, `overall_score`.

`--separate` is intentionally **not** supported here because the comparison
engine does not consume `PhysicalRPE.stem_rpe`. Use `evaluate --separate` or
`run --separate` for per-stem analysis.

### `svprpe ci-check <target_svp.json> <observed_rpe.json>`

Run the deterministic semantic CI fixture loop.

```bash
svprpe ci-check target_svp.json observed_rpe.json
svprpe ci-check target_svp.json observed_rpe.json -o semantic_ci_result.json
svprpe ci-check target_svp.json observed_rpe.json --format markdown -o semantic_ci_report.md
svprpe ci-check target_svp.json observed_rpe.json --threshold 0.15
svprpe ci-check examples/semantic_ci/pass_perfect/target_svp.json \
  examples/semantic_ci/pass_perfect/observed_rpe.json
```

Output includes `expected_rpe`, `semantic_diff`, `repair_svp`, `repaired_svp`, and
`roundtrip_log`. Use `--format markdown` for a human-readable report with verdict,
signal diff, metric diff, repair plan, and hash trail. The command exits with code
`1` when the final verdict is `repair`, so it can be used as a CI gate. Use
`--threshold` to treat loss values less than or equal to the threshold as `pass`.

### `svprpe audit <composition_score.yaml> <rpe_or_audio>`

Render a composition control-panel audit from a Composition Score and an
extracted `RPEBundle` JSON or an audio file. JSON fixtures are the deterministic
test path; an audio input runs the extractor as a one-shot convenience
front-end.

```bash
svprpe audit score.yaml observed_rpe.json
svprpe audit score.yaml track.wav --valley-method hybrid
svprpe audit score.yaml observed_rpe.json --format json -o audit.json
```

Default `--format` is `text`; `json` emits the structured audit report. The
report is descriptive and intentionally does not emit verdict, pass/fail, or
loss keys.

### `svprpe run <audio>`

Run full pipeline: extract → generate → evaluate.

```bash
svprpe run track.wav --output-dir ./output
svprpe run track.wav --no-save
svprpe run track.wav --valley-method section_ar --output-dir ./output
svprpe run track.wav --baseline acoustic --output-dir ./output
svprpe run track.wav --separate --output-dir ./output
```

### `svprpe batch <dir>`

Batch process multiple audio files.

```bash
# Evaluate all audio files in directory
svprpe batch ./audio_files --output-dir ./batch_out
svprpe batch ./audio_files --baseline loud_pop --output-dir ./batch_out
svprpe batch ./audio_files --separate --output-dir ./batch_out

# Compare each audio against SVP candidates
svprpe batch ./audio_files --svp-dir ./svp_candidates --mode compare --output-dir ./batch_out
```

Outputs: `ranking.json`, `summary.csv`, `summary.json`, `next_action.md`.

### `svprpe genre-calibrate <manifest.yaml>`

Analyze a genre-labeled calibration corpus manifest (`examples/calibration/genre/manifest.yaml`
shape) and report per-genre feature statistics, pair separability, and threshold candidates.
Deterministic; emits no verdict.

```bash
svprpe genre-calibrate examples/calibration/genre/manifest.yaml
svprpe genre-calibrate examples/calibration/genre/manifest.yaml --format json -o report.json
```

Samples with `excluded: true` or unresolvable audio/measured data are reported under
`excluded_samples` with a reason instead of silently dropped. See
[`genre_calibration_planning.md`](genre_calibration_planning.md).

### `svprpe genre-audit <manifest.yaml>`

Apply the current production genre rules (`semantic_rules.yaml` `cultural_context` /
`instrumentation`) to a labeled manifest and report a confusion table plus per-sample
predictions. This is a descriptive audit, not a scorer: it does not compute accuracy or
pass/fail, only a `mismatch` marker against known expected-context pairs.

```bash
svprpe genre-audit examples/calibration/genre/manifest.yaml
svprpe genre-audit examples/calibration/genre/manifest.yaml --format json -o audit.json
```

## Global Options

| Option | Description |
|--------|-------------|
| `--output` / `-o` | Output file path |
| `--output-dir` | Output directory (creates if needed) |
| `--fields` | Comma-separated `CompositionScore.physical` fields for `measure` |
| `--format` | Output format. `generate`: `yaml` (default) or `text`; `ci-check`: `json` (default) or `markdown`; `roundtrip` / `roundtrip-corpus` / `roundtrip-rep` / `compose` / `audit` / `score-adherence` / `genre-calibrate` / `genre-audit`: `text` (default) or `json` |
| `--max-chars` | Override `rendering.prompt_max_chars` (`compose` only) |
| `--threshold` | Semantic CI pass threshold from `0.0` to `1.0` (`ci-check` only) |
| `--no-save` | Print output to stdout instead of saving |
| `--valley-method` | Valley depth method: `hybrid` (default), `rms_percentile`, `section_ar` |
| `--baseline` | RPE baseline profile: `pro`, `loud_pop`, `acoustic`, or `edm` |
| `--separate` | Enable opt-in Demucs source separation (`extract` / `evaluate` / `run` / `batch` only — not `compare`) |
| `--separation-model` | Demucs model name used with `--separate` / `--lyrics` (default: `htdemucs_ft`) |
| `--separation-device` | Demucs inference device used with `--separate` / `--lyrics` (default: `cpu`) |
| `--lyrics` | Enable opt-in faster-whisper lyrics transcription (`extract` only; requires `svp-rpe[lyrics]`) |
| `--lyrics-model` | faster-whisper model size used with `--lyrics` / `lyrics-adherence` (default: `small`) |
| `--lyrics-no-separate` | Transcribe the full mix instead of isolating vocals via Demucs first |
| `--expected` | Path to a text file of expected lyric lines, one per line (`lyrics-adherence` only) |
| `--svp` | External SVP file for comparison |
| `--svp-dir` | Directory with SVP candidates (batch mode) |
| `--mode` | Batch mode: `evaluate` (default) or `compare` |
| `--help` | Show help |
