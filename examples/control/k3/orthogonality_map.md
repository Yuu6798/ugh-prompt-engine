# K3 orthogonality matrix

- fixture: `k3_synth_performer_matrix_rpe_features`
- generator: `synth_performer_c4`
- repetitions: 5

## Effect-size matrix

| knob \ sensor | bpm | key_match_baseline | spectral_centroid | active_rate | valley_depth | onset_density† |
|---|---|---|---|---|---|---|
| bpm | **16.4** * | 0 | -11.6 ⚠ * | 4.52 ⚠ * | -1.64 ⚠ | 3.72 ⚠ * |
| key | -0.632 | **-999** * | -1.77 ⚠ | 0.642 | 0.841 ⚠ | 0.386 |
| brightness | 0.4 | 0 | **160** * | 1.66 ⚠ | 0.894 ⚠ | 3.37 ⚠ * |
| active_rate_target | -2.53 ⚠ | 0 | 0.0885 | **0.552** | 0.398 | -0.483 |
| valley_depth_target | 0 | 0 | 0.0655 | 0.828 ⚠ | **-1.03** | -0.134 |

* = ノイズ天井超え（既知 dead 行の経験的ヌル分布 max |d| を上回る）

## DCI

### Disentanglement (per knob)

| knob | disentanglement |
|---|---:|
| bpm | 0.2467 |
| key | 0.4087 |
| brightness | 0.5312 |
| active_rate_target | 0.5206 |
| valley_depth_target | 0.5729 |

- overall disentanglement: 0.3751

### Completeness / effect_size_gap (per sensor)

| sensor | completeness | effect_size_gap |
|---|---:|---:|
| bpm | 0.5124 | 0.747 |
| key_match_baseline | 1 | 1 |
| spectral_centroid | 0.4292 | 0 |
| active_rate | 0.2146 | 0.6336 |
| valley_depth | 0.05492 | 0.3753 |

- overall completeness: 0.4854
- mean effect_size_gap: 0.5512

## Noise ceiling

- ceiling: 2.53
- known dead knobs: active_rate_target, valley_depth_target
- null cell count: 12

### Resolved cells

| knob | sensor | effect | margin |
|---|---|---:|---:|
| key | key_match_baseline | -999 | 394.9 |
| brightness | spectral_centroid | 160 | 63.42 |
| bpm | bpm | 16.4 | 6.491 |
| bpm | spectral_centroid | -11.6 | 4.588 |
| bpm | active_rate | 4.52 | 1.786 |
| bpm | onset_density | 3.72 | 1.47 |
| brightness | onset_density | 3.37 | 1.332 |

## Summary

- diagonal: tight=3 loose=1 dead=1
- interference (off-diagonal): clean=8 weak=6 strong=11
- resolution: resolved=7 unresolved=11 no_ceiling=0
- dead knobs: none
- untouched sensors: none
