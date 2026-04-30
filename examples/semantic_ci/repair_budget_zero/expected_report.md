# Semantic CI Report

## Verdict

- Verdict: repair
- Loss: 0.8608
- Target SVP: semantic-ci-repair-budget-zero
- Observed RPE: observed-repair-budget-zero

## Signal Diff

| Category | Signals |
|---|---|
| missing | 148 bpm, bass-heavy, bright, chorus lift, energetic driving dense, gradual build, wide stereo |
| preserved | (none) |
| over_changed | unexpected pad |

## Metric Diff

| Metric | Expected | Observed | Tolerance | Diff | Passed |
|---|---:|---:|---:|---:|:---:|
| active_rate | 0.9 | 0.4 | 0.0 | 0.5 | no |
| bpm | 148.0 | 132.0 | 0.0 | 16.0 | no |

## Repair Plan

- Change budget: 0
- Preserve: chorus lift, energetic driving dense
- Restore: (none)
- Reduce: (none)
- Lock: 148 bpm
- Deferred restore: 148 bpm, bass-heavy, bright, chorus lift, energetic driving dense, gradual build, wide stereo
- Deferred reduce: unexpected pad

| Order | Op | Signal | Applied |
|---:|---|---|:---:|
| 1 | preserve | chorus lift | yes |
| 2 | preserve | energetic driving dense | yes |
| 3 | lock | 148 bpm | yes |
| 4 | restore | 148 bpm | no |
| 5 | restore | bass-heavy | no |
| 6 | restore | bright | no |
| 7 | restore | chorus lift | no |
| 8 | restore | energetic driving dense | no |
| 9 | restore | gradual build | no |
| 10 | restore | wide stereo | no |
| 11 | reduce | unexpected pad | no |

## Hash Trail

| Object | SHA-256 prefix |
|---|---|
| target_svp | 4e7c2478c72f |
| expected_rpe | 832dfb22899d |
| observed_rpe | cae84775d25e |
| semantic_diff | ec6d78348ce6 |
| repair_svp | b6cc8bffdbc6 |
| final | 9bd933fb36fe |
