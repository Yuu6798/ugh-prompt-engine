# K3 orthogonality matrix

- fixture: `k3_2a_suno_mini_matrix_rpe_features`
- generator: `suno`
- repetitions: 4

## Effect-size matrix

| knob \ sensor | bpm | spectral_centroid | active_rate† | valley_depth† | brightness_band_ratio† |
|---|---|---|---|---|---|
| bpm | **1.61** | 2.33 ⚠ | -0.959 ⚠ | -0.124 | 1.55 ⚠ |
| brightness | -0.34 | **0.863** | -1.39 ⚠ | 1.2 ⚠ | 0.804 ⚠ |

* = ノイズ天井超え（既知 dead 行の経験的ヌル分布 max |d| を上回る）

## DCI

### Disentanglement (per knob)

| knob | disentanglement |
|---|---:|
| bpm | 0.02392 |
| brightness | 0.1412 |

- overall disentanglement: 0.05137

### Completeness / effect_size_gap (per sensor)

| sensor | completeness | effect_size_gap |
|---|---:|---:|
| bpm | 0.3329 | 0.7892 |
| spectral_centroid | 0.1577 | 0.629 |

- overall completeness: 0.2242
- mean effect_size_gap: 0.7091

## Noise ceiling

- ceiling: none — 既知 dead 行なし＝全セル unresolved（計器は有意性を主張できない）
- known dead knobs: none
- null cell count: 0

### Resolved cells

(none)

## Summary

- diagonal: tight=2 loose=0 dead=0
- interference (off-diagonal): clean=1 weak=1 strong=6
- resolution: resolved=0 unresolved=0 no_ceiling=10
- dead knobs: none
- untouched sensors: none
