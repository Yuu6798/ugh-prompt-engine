# Roundtrip Preservation (R0)

R0 measures whether physical CompositionScore knobs survive this deterministic path:

```text
CompositionScore -> perform(FAITHFUL_TAKE) -> WAV -> extract_rpe_from_file()
  -> draft_score() -> CompositionScore' -> diagnose_roundtrip()
```

The report is a descriptive instrument panel. It does not emit verdict, pass/fail,
or loss keys. A loss of preservation is a calibration or controllability signal,
not a product-quality judgment.

## Diagnosis Labels

| label | meaning |
|---|---|
| `preserved` | source and transcribed score field match under the field comparator |
| `sensor_blind` | the transcription side is TODO/null/outside a calibrated sensor band |
| `knob_dead` | the field differs and K1 classifies that knob as dead |
| `calibration_disagreement` | K1 says the knob is tight, but a working sensor disagrees |

The implementation reads a packaged copy of `examples/control/k1/expected_grip.json`.
A regression test asserts that the packaged resource and canonical fixture stay
JSON-equivalent, so grip values are not hardcoded in the roundtrip diagnosis.

## Snapshot Summary

Snapshot fixtures live in `examples/roundtrip/*_roundtrip.json`.

| source | preserved | sensor_blind | knob_dead | calibration_disagreement |
|---|---|---|---|---|
| `synth_01_roundtrip_source` | bpm, key, brightness | time_signature, stereo_width | active_rate_target, valley_depth_target | - |
| `synth_02_roundtrip_source` | bpm, key, brightness | time_signature, stereo_width | active_rate_target, valley_depth_target | - |
| `synth_03_roundtrip_source` | bpm, key, brightness | time_signature, stereo_width | active_rate_target, valley_depth_target | - |
| `synth_04_roundtrip_source` | key | time_signature, stereo_width | active_rate_target, valley_depth_target | bpm, brightness |
| `synth_05_roundtrip_source` | bpm, key | time_signature, stereo_width | active_rate_target, valley_depth_target | brightness |

## K1 Cross-Check

- `key` is tight in K1 and is preserved in all five deterministic roundtrips.
- `bpm` is tight in K1 and is preserved when the roundtrip delta is within the
  Q1-3 tolerance (`|delta| <= 5 BPM`). `synth_04` still surfaces
  `calibration_disagreement` because its measured delta is far outside that
  tolerance. This is the known tempo-calibration risk tracked in Q1-3/R2: the
  harness can surface the mismatch, but should not tune the BPM estimator inside
  R0.
  **Per-field bpm trust (R2-3 closeout, 2026-06-18):** bpm is *excluded* from the
  trusted reproduction knobs on the probabilistic (R3) path. The R2 detector
  series (#82–#86) only partially mitigates the faster-side (reported-too-slow)
  collapse post-hoc, and the ÷2 (reported-too-fast / doubling) direction is
  unrecoverable inside the extractor — it passes at high confidence and
  `bpm_octave_ambiguous=False` (e.g. the original `synth_01` sample WAV: true 60,
  detected 117.45, confidence 0.877). Only the corpus screener, which has stated
  truth, diagnoses ÷2 via low-prior recovery. So bpm is honest-but-untrusted: in
  R0 it is still measured and surfaces `preserved` / `calibration_disagreement`
  as above, but it is **not claimed as a faithful, locked reproduction target**.
  The transcribe trust gate already encodes this by emitting
  `TODO(transcribe): bpm undetected` (unlocked) whenever the half-fold flag fires.
  See roadmap_goal2.md 完成定義 §4.
- `brightness` uses the canonical spectral-centroid sensor. Dark targets are
  preserved in the first three examples; bright targets surface
  `calibration_disagreement` in the last two because the deterministic performer
  does not push the measured centroid into the bright band strongly enough.
  This is a calibration signal for R4, not a reason to rewrite the R0 harness.
- `active_rate_target` and `valley_depth_target` are dead in K1 and diagnose as
  `knob_dead` in all five examples.
- `stereo_width` remains `sensor_blind` because T1 has no calibrated stereo band
  and the deterministic synth path is mono.
- `time_signature` remains `sensor_blind` in these snapshots because T1 marks
  zero-confidence signatures as TODO instead of pretending they were measured.

## R5 Fixity And Schema Admission

R5 adds `CompositionScore.fixity` as an optional physical-field lock map:

```yaml
fixity:
  bpm: locked
  key: locked
  time_signature: unlocked
  active_rate_target: locked
  valley_depth_target: locked
  brightness: locked
  stereo_width: unlocked
```

The explicit map is limited to the seven `PhysicalLayer` fields, must include all
seven keys, and must match the current physical value state. `locked` means the
field was populated by a measured or authored value; `unlocked` means the field
remains a `TODO(transcribe):` sentinel and needs human authoring or sensor
calibration. T1 draft transcription writes this block explicitly. Authored scores
may omit it; `field_fixity(score)` then derives the same shape from TODO
sentinels, which makes older scores behave as locked unless they already contain
transcribe TODO values.

This is separate from `semantic_ci.lock`: fixity describes measured physical
fields in CompositionScore, while semantic CI locks describe repair constraints
for semantic signals.

New CompositionScore or PhysicalLayer fields must pass a schema-admission review
before becoming canonical. The Design Memo must state:

- whether the new field can be `locked` by measurement or only authored as
  `unlocked`;
- the roundtrip evidence already measured for that field, or the concrete
  measurement plan and fixture path that will produce it;
- how the field will be represented in future R0/R5 preservation reports if it
  cannot yet be measured.

## Follow-Up Routing

- R2/Q1-3: tempo estimator calibration, including low/half-tempo attractors.
  **R2 closed (2026-06-18):** bpm is excluded from the R3 trusted reproduction
  knobs (faster-side mitigation is post-hoc; the ÷2 doubling direction is
  extractor-unrecoverable, screener-only). The remaining
  `BPM_CONFIDENCE_CV_SCALE` real-audio calibration is **closed (2026-06-22, #92):**
  the Drive-backed loader materialized 7 real tracks and confirmed `CV_SCALE=5.0`
  unchanged — the 3 within-±5BPM tracks hold the Q1-3 contract (confidence
  0.83–0.90 > 0.7) on real audio, while the 4 incorrect-BPM tracks (1 octave-half +
  3 off) still read 0.80–0.85 (regularity-only), reaffirming the exclusion rather
  than reopening it. See
  roadmap_goal2.md 完成定義 §4 / R2-3.
- R4: brightness performer/sensor calibration for the bright band.
- T-series calibration: time-signature confidence and stereo-width banding.
