# Composition E2E Needle Comparison — Midnight Signal

同一 Score を 2 つの演奏スタイルで演奏し、audit 制御盤の針位置を比較する。

- score_id: midnight-signal
- take 1: `first_take` (wav sha256 `467f5200173c…`)
- take 2: `faithful_take` (wav sha256 `4d8c83f67c1b…`)

| knob | layer | target | first_take | faithful_take | dev (first) | dev (faithful) | 針の移動 |
|---|---|---|---|---|---:|---:|---|
| bpm | physical | 128 | 99.38 | 129.2 | -28.62 | 1.2 | → target |
| key | physical | C minor | E minor | C minor | 1 | 0 | → target |
| time_signature | physical | 4/4 | 4/4 | 4/4 | 0 | 0 | = flat |
| active_rate | physical | 0.90-0.93 | 1 | 0.9301 | 0.07 | 0.0001 | → target |
| valley_depth | physical | 0.15-0.25 | 0.0043 | 0.1593 | -0.1457 | 0 | → target |
| brightness | physical | dark | 787.2 | 950.8 | 0 | 0 | = flat |
| stereo_width | physical | wide |  |  |  |  |  |
| core | semantic | introspective night drive | A continuous, dark, mid-focused sonic character | A bass-heavy, dark, grounded sonic character | 1 | 1 | = flat |
| grv | semantic | deep_house, ambient | mid-focused, dense | bass-heavy, dense | 1 | 0.5 | → target |
| delta_e | semantic | gradual build from solitude to release | sustained_energy | gradual_build | 0.7 | 0.0544 | → target |
