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
"builds-latest/0.1"`, `{"content_digest": "<64hex>"}`) to point at it. A
digest directory is **immutable**: if `<root>/builds/<content_digest>/`
already exists as a directory from a previous run, its artifacts are never
overwritten, appended to, or fully re-verified — only its one *descriptive*
file (`arrangement_bundle.json`) is read once and compared byte-for-byte
against what this invocation would have published for that filename, so a
directory that merely happens to occupy this digest path isn't blindly
trusted. If that file is missing or unreadable, the run fails (exit `1`,
`latest.json` left untouched) rather than treating the directory as a valid
prior publication. Re-running an identical build (first-publish-wins: the
existing bytes match) prints an "already published" advisory and exits `0`
with the directory untouched; because `content_digest` covers only
`derived_score.yaml` + `arrangement_diff.json`, a same-digest rebuild whose
recorded provenance differs (e.g. the same `arrangement.yaml` referenced via
a different path) is *also* left untouched but gets a distinct "provenance
differs from this invocation" advisory instead — the first publication's
provenance always wins, `latest.json` still moves to point at it. If
`<root>/builds/<content_digest>` already exists as something other than a
directory (a plain file, a symlink to a file, or a dangling symlink), the run
fails outright (exit `1`, `latest.json` left untouched) rather than treating
it as published. Re-running with different inputs that resolve to a
different `content_digest` publishes a sibling directory; older digest
directories are never pruned. This descriptive-file check is intentionally
partial (one file, single read, no rehash of the other artifacts); a full
recursive verification of a published digest directory is left to a future
`verify`-style command.

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

The capability profile's `generator` must match the derived score's resolved
backend profile key (`external` resolves to `suno`). A mismatch fails before
either artifact is published. Prompt rendering also uses that resolved
generator, so an `external` score packaged for Suno receives the same
Suno-specific prompt and section-tag behavior as `target_backend: suno`.
The resolved generator device profile is loaded once, rendered from that exact
model, and pinned in both outputs by a canonical-model-JSON SHA-256 (or an
explicit `not_found` status), so prompt provenance does not depend on an
unrecorded local-versus-packaged config choice.

Advisory mode is the default and records capability warnings. Add
`--strict-capabilities` to fail when any hard anchor is `unsupported` or
`unknown`; hard anchors on experimental but usable channels remain deliverable.
Both JSON files are constructed before publication and are published together
with rollback on failure. `compilation_report.json` (`schema_version:
"compilation-report/0.2"`) pins the exact published package bytes with
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
bug.

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
