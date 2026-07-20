# Plan C demo — raw harmony/structure measurements (verbatim transcription)

Verbatim `harmony` and `structure` anchor blocks (sensor + measurements +
adherence_status + determination + note) from each of the 4
`observation_report.json` files, transcribed without editing per the order
sheet's acceptance instructions. `lyrics` and `melody` anchors are omitted
here (both `not_observed`/`no_sensor` in all 4 reports, as expected — see the
full JSON files for their verbatim blocks too).

All 4: `schema_version: "observation-report/0.1"`, `work_id: "midnight-signal"`,
`package_sha256: "792f350deb85e0c94384d98f96391c1c635a9ded7afec68af4c317ad0bdbc160"`.

---

## suno_A1 (Cell A — tags)

`generated_artifact`: `{"path": "suno_A1.mp3", "sha256": "25621c50f3e51132626e561aa21017ce727bab594f309b0643b9d01b92708772"}`

### harmony

```json
{
  "anchor_id": "harmony",
  "domain": "harmony",
  "sensor": {
    "name": "chord_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "chord_sequence_match_rate": 0.0526,
    "repeated_chord_sequence_match_rate": 0.0,
    "canonical_length": 4,
    "observed_length": 38,
    "collapsed_observed_length": 20,
    "matched_cycle_prefix_length": 1,
    "collapsed_match_fraction": 0.05,
    "unmatched_tail_length": 19,
    "unmatched_tail_head": [
      ["G", "minor"],
      ["G#", "major"],
      ["C", "minor"],
      ["G", "minor"],
      ["G#", "minor"],
      ["G#", "major"],
      ["G", "minor"],
      ["G", "major"]
    ],
    "full_cycles": 0
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "collapsed observed prefix matches 0 full canonical cycle(s); 19 trailing entries fall outside the canonical alternation. changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo (D-1)."
}
```

### structure

```json
{
  "anchor_id": "structure",
  "domain": "structure",
  "sensor": {
    "name": "section_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "canonical_sections": ["intro", "verse", "chorus", "bridge"],
    "canonical_length": 4,
    "observed_sections": ["intro", "verse", "verse", "verse", "bridge", "chorus", "chorus", "outro"],
    "observed_sections_raw": ["Intro", "Verse", "Verse2", "Verse3", "Bridge", "Chorus", "Chorus", "Outro"],
    "observed_length": 8,
    "position_match_rate": 0.25,
    "sequence_exact_match": false,
    "canonical_section_ids": ["s1-intro", "s2-verse", "s3-chorus", "s4-bridge"]
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "normalized canonical and observed section sequences do not match exactly (changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo, D-1)."
}
```

---

## suno_A2 (Cell A — tags)

`generated_artifact`: `{"path": "suno_A2.mp3", "sha256": "cc19d9bbd3c3fe8f508ed1b1a37a8db606e39eef7d47329cca2b8a4fe7c1e1bf"}`

### harmony

```json
{
  "anchor_id": "harmony",
  "domain": "harmony",
  "sensor": {
    "name": "chord_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "chord_sequence_match_rate": 0.0,
    "repeated_chord_sequence_match_rate": 0.0,
    "canonical_length": 4,
    "observed_length": 33,
    "collapsed_observed_length": 31,
    "matched_cycle_prefix_length": 0,
    "collapsed_match_fraction": 0.0,
    "unmatched_tail_length": 31,
    "unmatched_tail_head": [
      ["G#", "major"],
      ["A#", "major"],
      ["C", "minor"],
      ["G", "minor"],
      ["G#", "major"],
      ["A#", "major"],
      ["C", "minor"],
      ["G#", "major"]
    ],
    "full_cycles": 0
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "collapsed observed prefix matches 0 full canonical cycle(s); 31 trailing entries fall outside the canonical alternation. changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo (D-1)."
}
```

### structure

```json
{
  "anchor_id": "structure",
  "domain": "structure",
  "sensor": {
    "name": "section_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "canonical_sections": ["intro", "verse", "chorus", "bridge"],
    "canonical_length": 4,
    "observed_sections": ["intro", "verse", "chorus", "bridge", "chorus", "verse", "verse", "outro"],
    "observed_sections_raw": ["Intro", "Verse", "Chorus", "Bridge", "Chorus", "Verse2", "Verse3", "Outro"],
    "observed_length": 8,
    "position_match_rate": 0.5,
    "sequence_exact_match": false,
    "canonical_section_ids": ["s1-intro", "s2-verse", "s3-chorus", "s4-bridge"]
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "normalized canonical and observed section sequences do not match exactly (changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo, D-1)."
}
```

---

## suno_B1 (Cell B — no tags)

`generated_artifact`: `{"path": "suno_B1.mp3", "sha256": "2c2930f07ae1bbe394a83c1ebfb308e792fba1eb0c8ca89ebd42115a910ebd1f"}`

### harmony

```json
{
  "anchor_id": "harmony",
  "domain": "harmony",
  "sensor": {
    "name": "chord_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "chord_sequence_match_rate": 0.0833,
    "repeated_chord_sequence_match_rate": 0.0,
    "canonical_length": 4,
    "observed_length": 12,
    "collapsed_observed_length": 10,
    "matched_cycle_prefix_length": 1,
    "collapsed_match_fraction": 0.1,
    "unmatched_tail_length": 9,
    "unmatched_tail_head": [
      ["D#", "major"],
      ["G#", "major"],
      ["D#", "major"],
      ["A#", "major"],
      ["G#", "major"],
      ["D#", "major"],
      ["C#", "minor"],
      ["G#", "major"]
    ],
    "full_cycles": 0
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "collapsed observed prefix matches 0 full canonical cycle(s); 9 trailing entries fall outside the canonical alternation. changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo (D-1)."
}
```

### structure

```json
{
  "anchor_id": "structure",
  "domain": "structure",
  "sensor": {
    "name": "section_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "canonical_sections": ["intro", "verse", "chorus", "bridge"],
    "canonical_length": 4,
    "observed_sections": ["intro", "chorus", "bridge", "verse", "verse", "verse", "chorus", "outro"],
    "observed_sections_raw": ["Intro", "Chorus", "Bridge", "Verse", "Verse2", "Verse3", "Chorus", "Outro"],
    "observed_length": 8,
    "position_match_rate": 0.125,
    "sequence_exact_match": false,
    "canonical_section_ids": ["s1-intro", "s2-verse", "s3-chorus", "s4-bridge"]
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "normalized canonical and observed section sequences do not match exactly (changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo, D-1)."
}
```

---

## suno_B2 (Cell B — no tags)

`generated_artifact`: `{"path": "suno_B2.mp3", "sha256": "115b9da6570014054ea8d88c781de83e40d73c7d479b5afd4d613496b0e88cb3"}`

### harmony

```json
{
  "anchor_id": "harmony",
  "domain": "harmony",
  "sensor": {
    "name": "chord_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "chord_sequence_match_rate": 0.1,
    "repeated_chord_sequence_match_rate": 0.0,
    "canonical_length": 4,
    "observed_length": 20,
    "collapsed_observed_length": 19,
    "matched_cycle_prefix_length": 2,
    "collapsed_match_fraction": 0.1053,
    "unmatched_tail_length": 17,
    "unmatched_tail_head": [
      ["D#", "major"],
      ["A#", "major"],
      ["C", "minor"],
      ["G#", "major"],
      ["A#", "major"],
      ["C", "minor"],
      ["G#", "major"],
      ["D#", "major"]
    ],
    "full_cycles": 0
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "collapsed observed prefix matches 0 full canonical cycle(s); 17 trailing entries fall outside the canonical alternation. changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo (D-1)."
}
```

### structure

```json
{
  "anchor_id": "structure",
  "domain": "structure",
  "sensor": {
    "name": "section_sequence_match",
    "available": true,
    "reason": null
  },
  "measurements": {
    "canonical_sections": ["intro", "verse", "chorus", "bridge"],
    "canonical_length": 4,
    "observed_sections": ["intro", "verse", "verse", "bridge", "chorus", "verse", "chorus", "outro"],
    "observed_sections_raw": ["Intro", "Verse", "Verse2", "Bridge", "Chorus", "Verse3", "Chorus", "Outro"],
    "observed_length": 8,
    "position_match_rate": 0.375,
    "sequence_exact_match": false,
    "canonical_section_ids": ["s1-intro", "s2-verse", "s3-chorus", "s4-bridge"]
  },
  "adherence_status": "not_observed",
  "determination": "deferred",
  "note": "normalized canonical and observed section sequences do not match exactly (changed_within_policy/changed_outside_policy classification is out of scope for this instrument and deferred to a future threshold Design Memo, D-1)."
}
```

---

## Descriptive cross-take table (no significance test — n=2 per cell, per pre-registration)

| take | cell | chord_sequence_match_rate | matched_cycle_prefix_length | full_cycles | position_match_rate | sequence_exact_match |
|---|---|---|---|---|---|---|
| suno_A1 | A (tags) | 0.0526 | 1 | 0 | 0.25 | false |
| suno_A2 | A (tags) | 0.0 | 0 | 0 | 0.5 | false |
| suno_B1 | B (no tags) | 0.0833 | 1 | 0 | 0.125 | false |
| suno_B2 | B (no tags) | 0.1 | 2 | 0 | 0.375 | false |

All 4 takes: `adherence_status: "not_observed"`, `determination: "deferred"` on
both harmony and structure anchors — a `not_observed`/`deferred` outcome for
every take, which the order sheet's pre-registration block registered in
advance as an equally valid, reportable result. `sequence_exact_match: false`
in all 4; no take reproduced the canonical 4-chord / 4-section pattern
exactly. All 4 canonical_length values match the manifest's 4-chord /
4-section canonical target; all 4 report 0 `full_cycles`.
