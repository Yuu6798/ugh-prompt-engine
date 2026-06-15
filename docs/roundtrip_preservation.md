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

The implementation reads `examples/control/k1/expected_grip.json`; grip values are
not hardcoded in the roundtrip diagnosis.

## Snapshot Summary

Snapshot fixtures live in `examples/roundtrip/*_roundtrip.json`.

| source | preserved | sensor_blind | knob_dead | calibration_disagreement |
|---|---|---|---|---|
| `synth_01_roundtrip_source` | bpm, key, brightness | time_signature, stereo_width | active_rate_target, valley_depth_target | - |
| `synth_02_roundtrip_source` | key, brightness | time_signature, stereo_width | active_rate_target, valley_depth_target | bpm |
| `synth_03_roundtrip_source` | key, brightness | time_signature, stereo_width | active_rate_target, valley_depth_target | bpm |
| `synth_04_roundtrip_source` | key | time_signature, stereo_width | active_rate_target, valley_depth_target | bpm, brightness |
| `synth_05_roundtrip_source` | key | time_signature, stereo_width | active_rate_target, valley_depth_target | bpm, brightness |

## K1 Cross-Check

- `key` is tight in K1 and is preserved in all five deterministic roundtrips.
- `bpm` is tight in K1, but four roundtrips surface
  `calibration_disagreement`. This matches the known tempo-calibration risk
  tracked in Q1-3/R2: the harness can surface the mismatch, but should not tune
  the BPM estimator inside R0.
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

## Follow-Up Routing

- R2/Q1-3: tempo estimator calibration, including low/half-tempo attractors.
- R4: brightness performer/sensor calibration for the bright band.
- T-series calibration: time-signature confidence and stereo-width banding.
