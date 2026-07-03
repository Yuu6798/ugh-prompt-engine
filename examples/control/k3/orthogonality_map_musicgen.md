# K3 orthogonality matrix

- fixture: `k3_2b_musicgen_matrix_rpe_features`
- generator: `musicgen-small`
- repetitions: 8

## Effect-size matrix

| knob \ sensor | bpm | key_match_baseline | spectral_centroid | active_rate | valley_depth | brightness_band_ratio† |
|---|---|---|---|---|---|---|
| bpm | **0.851** * | -0.0958 | 0.127 | 0.085 | 0.242 | -0.334 |
| key | 0.307 | **0.14** | 0.441 | 0.0237 | -0.2 | 0.431 |
| brightness | -0.234 | 0.178 | **1.26** * | -0.764 | 0.596 | 0.849 ⚠ * |
| active_rate_target | -0.614 | -0.848 ⚠ | -0.215 | **0.15** | 0.721 | -0.556 |
| valley_depth_target | 0.0625 | 0.0276 | 0.44 | -0.656 | **0.499** | -0.413 |

* = ノイズ天井超え（既知 dead 行の経験的ヌル分布 max |d| を上回る）

## DCI

### Disentanglement (per knob)

| knob | disentanglement |
|---|---:|
| bpm | 0.6712 |
| key | 0.5793 |
| brightness | 0.2258 |
| active_rate_target | 0.196 |
| valley_depth_target | 0.3264 |

- overall disentanglement: 0.3225

### Completeness / effect_size_gap (per sensor)

| sensor | completeness | effect_size_gap |
|---|---:|---:|
| bpm | 0.2145 | 0.2788 |
| key_match_baseline | 1 | 1 |
| spectral_centroid | 0.2666 | 0.6498 |
| active_rate | 0.5711 | 0.141 |
| valley_depth | 0.1788 | 0.1736 |

- overall completeness: 0.3551
- mean effect_size_gap: 0.4486

## Noise ceiling

- ceiling: 0.8481
- known dead knobs: active_rate_target, valley_depth_target
- null cell count: 12

### Resolved cells

| knob | sensor | effect | margin |
|---|---|---:|---:|
| brightness | spectral_centroid | 1.26 | 1.485 |
| bpm | bpm | 0.851 | 1.003 |
| brightness | brightness_band_ratio | 0.849 | 1.001 |

## Summary

- diagonal: tight=2 loose=1 dead=2
- interference (off-diagonal): clean=8 weak=15 strong=2
- resolution: resolved=3 unresolved=15 no_ceiling=0
- dead knobs: none
- untouched sensors: none
