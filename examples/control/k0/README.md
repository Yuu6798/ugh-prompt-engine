# K0 — 方法実証用 fixture（MusicGen 実測ではない）

`musicgen_rpe_fixture.json` は grip 効果量パイプライン（`fixture → scripts/measure_grip.py`）
の配線を検証するための**手作りの合成 fixture**であり、実際に MusicGen を走らせて
得た数値ではない。`generator: "musicgen_fixture"` はこの手作り性を示すラベルで、
実生成器の名前空間（`musicgen-small` 等）とは区別している。

MusicGen の**実測**フィクスチャは `examples/control/k2_musicgen/`（Codex PR #136）に
ある。K0 と K2 型は同じ K2 スキーマ（`fixture.json` + `expected_grip.json`）を
共有するが、K0 はパイプライン自体の疎通確認、K2 型は実測 grip 転移の検証という
役割の違いがある。
