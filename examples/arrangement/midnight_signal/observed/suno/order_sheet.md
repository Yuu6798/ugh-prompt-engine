# Plan C: Suno A/B real-commission demo — order sheet

Work: `midnight-signal` (EDM variant, `edm.identity.suno.arrangement.yaml`).
Purpose: first-ever AR4 observation against a **real Suno generation**, plus an
exploratory A/B on whether the `section_tags` channel (Suno Lyrics-field
section-tag script) helps form/structure preservation. Dogfoods #194's
section-map/0.2 stable id + `section_ref` resolution + the new elastic
structure vocab (`section_insertion` / `section_repetition`).

All sidecar inputs, the compiled package, and this order sheet live under
`scratchpad/suno_ar4_demo/` (nothing committed to the repo — see the
"provenance" section at the end for exact file paths and hashes).

---

## Pre-registration block

- **Registered at (UTC, real clock read via `date -u`)**: `2026-07-19T14:31:23Z`
- **Status: exploratory, non-canonical.** This is **not** a canonical AR4
  measurement batch in the sense of the existing MusicGen batches
  (`docs/arrangement_identity_planning.md` §5). Specifically:
  - No ABBA counter-balancing is applied (n=2 per cell, generated in whatever
    order the human operator finds convenient — order is only logged as a
    memo, not controlled).
  - No balance gate between cells.
  - No independent timestamp-evidence chain for "prompt confirmed before
    generation" (unlike the MusicGen batches' `plan_confirmed_at_utc` <
    `ar4_generation_timestamps.yaml` ordering) — the UTC timestamp above is
    this document's own authoring time, recorded for the record, not proof of
    a machine-verified sequence.
  - This is an **n=2×2 initial observation**. No effect-size claim is made or
    implied by this order or its eventual results.
- **Pre-registered determination (do not deviate after seeing results):**
  - Run `svprpe observe` (template at the bottom of this file) against each
    returned take and record its output verbatim.
  - `adherence_status`/`determination` follow the existing 3-branch instrument
    semantics (`no_sensor` / `preserved`+`exact_match` / `not_observed`+
    `deferred`) — see `docs/cli.md`'s `svprpe observe` section. **`preserved`
    is not a success condition for this demo.** A `not_observed`/`deferred`
    result for every take is registered in advance as an equally valid,
    reportable outcome.
  - Any A vs. B comparison is **descriptive statistics only** (e.g. "structure
    `position_match_rate` for A1/A2 vs. B1/B2", or "harmony
    `matched_cycle_prefix_length` for A vs. B") — no significance test, no
    "A wins" verdict. n=2 per cell cannot support one.

---

## What's being tested

Two cells, same style/semantic/physical prompt content in both, differing
only in whether the Suno **Lyrics** field carries the section-tag script
compiled from `structure` (`section_tags`, channel `section_tags`, capability
`experimental` per `config/capability_profiles/suno.yaml` — see "Package
result" below). Both cells are **fully instrumental** — no sung lyrics in
either — so any observed difference cannot be attributed to a lyrics/vocal
confound (`docs/lyrics_semantic_anchor.md`'s known vocal-as-anchor confound).

- **Cell A ("tags")**: Style prompt + section-tag script pasted into Lyrics
  field. Instrumental toggle ON.
- **Cell B ("no tags")**: Style prompt only, Lyrics field empty. Instrumental
  toggle ON.

---

## Cell A — tags (generate ×2: `suno_A1`, `suno_A2`)

**Suno settings**: Custom mode ON. Instrumental: **ON**. Style/Lyrics fields
below. If the Suno UI surfaces a model version anywhere (e.g. a "v4.5" badge),
please note it in your return message — the repo's `config/capability_profiles/
suno.yaml` currently records `model_version: null` because Suno does not
publish a stable pin, so any UI-visible label is new information worth
capturing.

Style field (paste as-is):

```text
132 BPM. Brightness bright. Introspective night drive atmosphere. melodic_edm / progressive_house track. C minor. 4/4 time. Wide stereo. Active rate 0.90-0.93. Valley depth 0.15-0.25.
```

Lyrics field (paste as-is — this is a section-tag script, not sung lyrics;
Instrumental stays ON):

```text
[Intro: low density, sub bass only]
[Verse: sparse drums, short phrases, clear rests]
[Chorus: full energy, wide stereo, focused layers]
[Bridge: no kick, no bass, minimal texture]
```

Exclude Styles field, if present in the UI (paste as-is):

```text
bright festival EDM, comic vocal delivery
```

Generate **2 independent takes** with this exact Style + Lyrics + Exclude
Styles content, Instrumental ON, Custom mode. Do not edit between takes.

---

## Cell B — no tags (generate ×2: `suno_B1`, `suno_B2`)

**Suno settings**: Custom mode ON. Instrumental: **ON**. Lyrics field:
**empty**. Same Style and Exclude Styles fields as Cell A.

Style field (identical to Cell A, paste as-is):

```text
132 BPM. Brightness bright. Introspective night drive atmosphere. melodic_edm / progressive_house track. C minor. 4/4 time. Wide stereo. Active rate 0.90-0.93. Valley depth 0.15-0.25.
```

Lyrics field: **leave empty** (do not paste anything; rely on the
Instrumental toggle, not an "[Instrumental]" tag, so Cell B has zero Lyrics
field content and Cell A's only difference is the tag script).

Exclude Styles field, if present in the UI (paste as-is, identical to Cell A):

```text
bright festival EDM, comic vocal delivery
```

Generate **2 independent takes** with this exact Style content, Lyrics field
empty, Instrumental ON, Custom mode. Do not edit between takes.

---

## Return instructions

Please return 4 audio files (mp3 or wav, either is fine):

| Filename | Cell | Notes |
|---|---|---|
| `suno_A1` | A (tags) | 1st Cell A take |
| `suno_A2` | A (tags) | 2nd Cell A take |
| `suno_B1` | B (no tags) | 1st Cell B take |
| `suno_B2` | B (no tags) | 2nd Cell B take |

Also include a short memo (a few lines is fine) noting:

- The order you actually generated them in (e.g. "A1, A2, B1, B2" or
  interleaved — whatever happened; this is not controlled, just logged).
- Any Suno model-version indicator visible in the UI, if any.
- Anything Suno's UI reported back to you as unusual (regenerated/rejected
  content, a warning about the Lyrics field, etc.).

---

## Acceptance: observe command template (run after audio arrives)

For **each** of the 4 returned files, from this demo directory (or with paths
adjusted accordingly), run:

```bash
svprpe observe \
  scratchpad/suno_ar4_demo/package/performance_package.json \
  <path/to/suno_A1.wav> \
  --manifest scratchpad/suno_ar4_demo/inputs/identity_manifest.demo.yaml \
  -o scratchpad/suno_ar4_demo/observed/suno_A1_observation.json
```

(repeat for `suno_A2`, `suno_B1`, `suno_B2`, each with its own `-o` output
path). `observe` re-verifies the D-3 provenance chain (manifest sha256 vs.
`package.inputs.identity_manifest.sha256`, every manifest anchor's artifact
hash vs. the file on disk, and the package's own schema) before measuring
anything, and exits `1` without measuring if any link is broken — a clean
exit `0` for all 4 takes is itself part of the record.

Two sensors are wired against this manifest: **harmony** (anchor `harmony`,
`chord_sequence_json`) and **structure** (anchor `structure`,
`section_map`/`section-map/0.2`) — this demo is the **first exercise of the
structure sensor against a section-map/0.2 anchor** (previous structure-sensor
observations, e.g. `examples/arrangement/midnight_signal/observed/
musicgen_form/`, used `section-map/0.1`). `lyrics` and `melody` anchors will
report `not_observed`/`no_sensor` regardless of outcome (no sensor wired in
this repo for those domains yet) — that is expected and not a failure of this
demo.

Record all 4 `observation_report.json` files verbatim (do not summarize away
the raw `measurements` block) — per the pre-registration above, whatever comes
back is the result, including an all-`not_observed` outcome.

---

## Provenance — sidecar inputs and package (this demo, not committed)

All paths below are relative to `scratchpad/suno_ar4_demo/`:

- `inputs/composition_score.yaml` — byte-identical copy of
  `examples/arrangement/midnight_signal/composition_score.yaml`
  (sha256 `37854f54b42a1c4d424f357148d3d10f347e238ec72a42d1248bea2203f97d0b`,
  confirmed equal to the repo original before use).
- `inputs/identity/{lyrics.txt,melody_notes.json,chord_progression.json}` —
  byte-identical copies of the same-named files under
  `examples/arrangement/midnight_signal/identity/` (hashes confirmed equal to
  the repo originals; unchanged from `identity_manifest.yaml`'s existing pins).
- `inputs/identity/section_map.v2.json` — new section-map/0.2 artifact (sha256
  `e0b1d7b9667d78b566b517a2389aaedb80da062419ad87a8386cc9c0d0eee951`), ids
  `s1-intro`/`s2-verse`/`s3-chorus`/`s4-bridge`, labels mechanically
  transcribed from `composition_score.yaml`'s `structure` section names.
- `inputs/identity_manifest.demo.yaml` — lyrics/melody/harmony anchors
  unchanged from the repo's `identity_manifest.yaml`, plus one new `structure`
  anchor (`required: false`) pointing at `section_map.v2.json`. The `melody`
  anchor carries `section_ref: "s3-chorus"` (sha256 of this manifest:
  `f96c5f0b4296d18242d8aa3df48ef1d4c040337c79ac8224df61cb34af774aa4`).
- `inputs/edm.identity.suno.arrangement.yaml` — same EDM target as the repo's
  `edm.identity.arrangement.yaml` (melodic_edm/progressive_house, bpm 132,
  bright, lyrics/melody/harmony hard), plus one new `identity_anchors.structure`
  policy: `mode: elastic`, `allow: [section_repetition, section_insertion]`
  (omission and reordering are deliberately **not** in the allow-list — not
  declared within-policy for this demo).
- `arrange_out/` — `svprpe arrange` output (`derived_score.yaml`,
  `arrangement_bundle.json`, `arrangement_diff.json`).
- `package/performance_package.json`, `package/compilation_report.json` —
  `svprpe package` output against `config/capability_profiles/suno.yaml`.
- `prompt.json` — `svprpe compose arrange_out/derived_score.yaml --format json`
  output (see the caller's report for a note on why this alone does not carry
  `section_tags`; the package's `prompt.text`/`prompt.section_tags` is the
  authoritative source used above).

Exact commands run (all foreground, no background/`&`/nohup):

```bash
svprpe arrange examples/arrangement/midnight_signal/composition_score.yaml \
  scratchpad/suno_ar4_demo/inputs/edm.identity.suno.arrangement.yaml \
  --output-dir scratchpad/suno_ar4_demo/arrange_out

svprpe package examples/arrangement/midnight_signal/composition_score.yaml \
  scratchpad/suno_ar4_demo/inputs/identity_manifest.demo.yaml \
  scratchpad/suno_ar4_demo/inputs/edm.identity.suno.arrangement.yaml \
  --capability-profile config/capability_profiles/suno.yaml \
  --output-dir scratchpad/suno_ar4_demo/package

svprpe verify scratchpad/suno_ar4_demo/package/performance_package.json \
  --manifest scratchpad/suno_ar4_demo/inputs/identity_manifest.demo.yaml
```

`verify` result: **38/38 checks passed, exit 0** (V1 package load, V2
compilation report cross-check, V3 identity manifest chain, V4
`channel_artifacts` — both the `lyrics_text`/`lyrics` and the new
`section_tags`/`structure` channel entries passed every V4 sub-check).

`structure` anchor delivery status in `performance_package.json`'s
`anchor_statuses`: `requested_mode: elastic`, `delivery.channel:
"section_tags"`, `delivery.status: "experimental"` — **not** `"delivered"`.
This is expected, not a demo bug: `config/capability_profiles/suno.yaml`
declares `section_tags: {support: experimental}`, and
`ARTIFACT_TYPE_CHANNEL["section_map"] == "section_tags"` maps the structure
anchor onto that channel; `_delivery_status` passes `experimental` through
unchanged (only `"supported"` maps to `"delivered"`). `experimental` still
qualifies as deliverable enough to appear in `channel_artifacts` (the package
compiler treats `delivered` and `experimental` alike for inclusion), which is
why the section-tag script above is present in `prompt.section_tags` at all —
but the package's own bookkeeping is honest that this channel's reliability is
unproven, matching `docs/control_profile.md`'s existing `section_tags:
experimental` evidence trail.

`section_ref` resolution: the `melody` anchor's `section_ref: "s3-chorus"`
resolved successfully against the `structure` anchor's section-map/0.2 ids —
`svprpe package`'s clean exit (and `svprpe verify`'s clean exit, which
reparses the same manifest independently) is the proof; a dangling reference
would have raised `IdentityManifestError` and aborted both commands before any
output was written.
