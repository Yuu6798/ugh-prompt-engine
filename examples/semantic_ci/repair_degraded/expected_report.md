# Semantic CI Report

## Verdict

- Verdict: repair
- Loss: 0.5894
- Target SVP: semantic-ci-repair-degraded
- Observed RPE: observed-repair-degraded

## Signal Diff

| Category | Signals |
|---|---|
| missing | bright, chorus lift, energetic driving dense, gradual build, wide stereo |
| preserved | 148 bpm, bass-heavy |
| over_changed | dark ambient, unexpected pad |

## Metric Diff

| Metric | Expected | Observed | Tolerance | Diff | Passed |
|---|---:|---:|---:|---:|:---:|
| active_rate | 0.9 | 0.4 | 0.0 | 0.5 | no |
| bpm | 148.0 | 132.0 | 0.0 | 16.0 | no |

## Repair Plan

- Change budget: 3
- Preserve: 148 bpm, bass-heavy, chorus lift, energetic driving dense
- Restore: bright, chorus lift, energetic driving dense
- Reduce: (none)
- Lock: 148 bpm, bass-heavy
- Deferred restore: gradual build, wide stereo
- Deferred reduce: dark ambient, unexpected pad

| Order | Op | Signal | Applied |
|---:|---|---|:---:|
| 1 | preserve | 148 bpm | yes |
| 2 | preserve | bass-heavy | yes |
| 3 | preserve | chorus lift | yes |
| 4 | preserve | energetic driving dense | yes |
| 5 | restore | bright | yes |
| 6 | restore | chorus lift | yes |
| 7 | restore | energetic driving dense | yes |
| 8 | lock | 148 bpm | yes |
| 9 | lock | bass-heavy | yes |
| 10 | restore | gradual build | no |
| 11 | restore | wide stereo | no |
| 12 | reduce | dark ambient | no |
| 13 | reduce | unexpected pad | no |

## Hash Trail

| Object | SHA-256 prefix |
|---|---|
| target_svp | 464d6db707cb |
| expected_rpe | 3e77d54c9ebe |
| observed_rpe | 71d6a0e6a483 |
| semantic_diff | 062d8bb81883 |
| repair_svp | 36517f41bbc1 |
| final | aa264950a0b7 |
