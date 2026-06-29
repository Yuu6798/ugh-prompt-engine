# K2 — Suno grip transfer fixture

K1 が tight と判定したツマミ（`bpm` / `brightness`）が、実際の製品級生成器
**Suno** でも tight のまま転移するかを検証するための数値 fixture。

K0/K2 は **fixture-driven**: 音源生成と RPE 抽出は事前に済ませ、コミットするのは
`suno_rpe_fixture.json`（サンプルごとの数値特徴量）と `expected_grip.json`
（`scripts/measure_grip.py --json` の出力）のみ。`fixture → grip` は決定論で
`tests/test_grip.py::test_k2_suno_fixture_snapshot_bpm_and_brightness_transfer`
が回帰固定する。

## Provenance

- **生成器**: Suno（製品級・確率的・リポジトリ外）。
- **音源**: session 添付の mp3 16 本。repo にはコミットしない（content-addressed）。
  各サンプルの `audio_sha256` を fixture にインライン保全し provenance とする。
- **設計**: 2 ツマミ × 2 水準 × 4 反復 = 16 サンプル。A/B コントラストを保つため、
  対象ツマミの記述子だけを振り、key（A minor）・編成・密度は固定。
  - bpm: 90 BPM ↔ 140 BPM（音色は中立）。
  - brightness: dark ↔ bright（テンポは 110 BPM 固定）。

## 生成プロンプト（Suno 公式準拠 — Style 欄は短く、否定は Exclude 欄へ）

スタイル欄は ~200 字超で黙って切り捨てられ、否定の `Avoid:` 文は専用の
Exclude Styles 欄に分離するのが公式仕様（help.suno.com）。

```text
# bpm_low  (×4)
Instrumental synthwave, 90 BPM, A minor. Analog synth pads, four-chord loop, steady drum machine, simple bassline, lead motif. Even steady density.

# bpm_high (×4)
Instrumental synthwave, 140 BPM, A minor. Analog synth pads, four-chord loop, steady drum machine, simple bassline, lead motif. Even steady density.

# bright_dark   (×4)
Instrumental synthwave, 110 BPM, A minor. Analog synth pads, four-chord loop, drum machine, bassline. Dark warm muffled tone, soft rounded highs, no sparkle.

# bright_bright (×4)
Instrumental synthwave, 110 BPM, A minor. Analog synth pads, four-chord loop, drum machine, bassline. Bright airy crisp tone, sparkling shimmering highs, lots of treble.
```

Exclude Styles 欄（Pro/Premier・任意）: 全セル `vocals, orchestral, acoustic guitar, tempo change`、
brightness セルは反対側音色も除外（dark に `bright, treble, sparkle`／bright に `muffled, dark, lo-fi, bass-heavy`）。

## 結果サマリ

| knob | sensor | mean low | mean high | grip d | class | K1（玩具）|
|---|---|---:|---:|---:|---|---|
| bpm | bpm | 117.75 | 138.43 | 1.61 | tight | 1.61 tight |
| brightness | spectral_centroid | 2320.7 | 2686.7 | 0.86 | tight | 223.5 tight |

詳細・留保（BPM prior アトラクタ／brightness の非対称）は
`docs/controllability_poc.md` §5.2 を参照。
