# Semantic CI Report

## Verdict

- Verdict: pass
- Loss: 0.0000
- Threshold: 0.0000
- Target SVP: semantic-ci-pass-perfect
- Observed RPE: observed-pass-perfect

## Signal Diff

| Category | Signals |
|---|---|
| missing | (none) |
| preserved | 148 bpm, bass-heavy, bright, chorus lift, energetic driving dense, gradual build, wide stereo |
| over_changed | (none) |

## Metric Diff

| Metric | Expected | Observed | Tolerance | Diff | Passed |
|---|---:|---:|---:|---:|:---:|
| active_rate | 0.9 | 0.9 | 0.0 | 0.0 | yes |
| bpm | 148.0 | 148.0 | 0.0 | 0.0 | yes |

## Repair Plan

- Change budget: 3
- Preserve: 148 bpm, bass-heavy, bright, chorus lift, energetic driving dense, gradual build, wide stereo
- Restore: (none)
- Reduce: (none)
- Lock: 148 bpm, bass-heavy, bright, chorus lift, energetic driving dense, gradual build, wide stereo
- Deferred restore: (none)
- Deferred reduce: (none)

| Order | Op | Signal | Applied |
|---:|---|---|:---:|
| 1 | preserve | 148 bpm | yes |
| 2 | preserve | bass-heavy | yes |
| 3 | preserve | bright | yes |
| 4 | preserve | chorus lift | yes |
| 5 | preserve | energetic driving dense | yes |
| 6 | preserve | gradual build | yes |
| 7 | preserve | wide stereo | yes |
| 8 | lock | 148 bpm | yes |
| 9 | lock | bass-heavy | yes |
| 10 | lock | bright | yes |
| 11 | lock | chorus lift | yes |
| 12 | lock | energetic driving dense | yes |
| 13 | lock | gradual build | yes |
| 14 | lock | wide stereo | yes |

## Hash Trail

| Object | SHA-256 prefix |
|---|---|
| target_svp | af2925ebb32d |
| expected_rpe | 4b6c2c2c1709 |
| observed_rpe | b77b3fb9f778 |
| semantic_diff | fb058e0ff65b |
| repair_svp | 558443b328ed |
| final | 23789d091c03 |
