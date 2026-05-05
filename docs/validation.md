# Validation Status

Current status: **PoC**.

This repository provides a deterministic local SVP/RPE pipeline. The metrics
below are validation signals for development and comparison. They are **not**
production music-quality labels and must not be treated as ground-truth quality
scores.

## 1. Quantitative Validation

Dataset:

- `examples/sample_input/synth_*.wav`
- Five deterministic synthetic sine-wave samples
- Ground truth: `examples/sample_input/ground_truth.yaml`
- Comparison script: `scripts/validate_against_truth.py`

Validation command:

```bash
python scripts/validate_against_truth.py
python scripts/validate_against_truth.py --json
python scripts/validate_against_truth.py --check
```

`--check` currently enforces:

- BPM octave-adjusted absolute error < 5 BPM
- key score >= 0.5
- time signature exact match
- downbeat hit-rate >= 0.8
- chord event hit-rate >= 0.75
- melody pitch accuracy >= 0.8 within +/-50 cents
- melody voicing recall >= 0.5
- section F@3s >= 0.5

## 2. Per-Song Results

| song_id | BPM est / ref / diff | BPM octave adj / diff | tempo p | key score | meter est / ref / conf | downbeat hit | chord hit | melody acc | melody recall | seg F@3s | check |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|
| synth_01_slow_pad_c_major | 117.45 / 60.00 / 57.45 | half: 58.73 / 1.27 | 0.00 | 1.00 | 4/4 / 4/4 / 0.77 | 1.00 | 1.00 | 0.99 | 0.99 | 0.73 | pass |
| synth_02_minor_pulse_a_minor | 92.29 / 90.00 / 2.29 | none: 92.29 / 2.29 | 1.00 | 1.00 | 4/4 / 4/4 / 1.00 | 0.44 | 1.00 | 1.00 | 1.00 | 0.73 | fail |
| synth_03_mid_groove_g_major | 123.05 / 120.00 / 3.05 | none: 123.05 / 3.05 | 1.00 | 1.00 | 4/4 / 4/4 / 0.55 | 1.00 | 1.00 | 1.00 | 0.99 | 0.73 | pass |
| synth_04_waltz_fsharp_minor | 136.00 / 140.00 / 4.00 | none: 136.00 / 4.00 | 1.00 | 1.00 | 3/4 / 3/4 / 1.00 | 1.00 | 1.00 | 1.00 | 0.99 | 0.73 | pass |
| synth_05_fast_bright_d_major | 172.27 / 170.00 / 2.27 | none: 172.27 / 2.27 | 1.00 | 1.00 | 4/4 / 4/4 / 0.85 | 1.00 | 1.00 | 0.99 | 0.99 | 0.73 | pass |

`BPM octave adj` reports the closest of the raw estimate, half tempo, and
double tempo. `tempo p` remains the raw `mir_eval.tempo.detection` score so the
double-tempo estimate is still visible instead of being silently corrected.

Known threshold misses:

- `synth_02_minor_pulse_a_minor`: downbeat phase drift remains.

## 3. Aggregate Results

| Metric | Result | Notes |
|---|---:|---|
| BPM raw error < 5 BPM | 4/5 | `synth_01` still reports a double-tempo raw estimate |
| BPM octave-adjusted error < 5 BPM | 5/5 | `synth_01` is explicitly modeled as half-tempo equivalent |
| Key weighted score >= 0.5 | 5/5 | All synthetic keys match |
| Time signature exact match | 5/5 | Includes one `3/4` sample |
| Downbeat hit-rate >= 0.8 | 4/5 | Q2-1 librosa fallback |
| Chord event hit-rate >= 0.75 | 5/5 | Q2-2 major/minor triad fallback |
| Melody pitch accuracy >= 0.8 | 5/5 | Q2-3 pyin over high-passed melody signal |
| Melody voicing recall >= 0.5 | 5/5 | All synthetic melody regions detected |
| Section F@3s >= 0.5 | 5/5 | Coarse section boundaries only |
| `--check` pass rate | 4/5 | Fails only the known `synth_02` downbeat phase case |

## 4. Coverage Matrix

| Area | Status | What is currently checked |
|---|---|---|
| BPM extraction | Quantitatively validated on synth with caveat | Raw diff and octave-adjusted diff are both reported; `synth_01` remains raw double-tempo but passes octave-equivalent check |
| Key detection | Quantitatively validated | `mir_eval.key.evaluate` weighted score |
| Time signature detection | Quantitatively validated | Exact match against synth ground truth |
| Downbeat detection | Partially validated | Hit-rate against synthetic downbeats; madmom deferred |
| Chord event detection | Quantitatively validated | Overlap hit-rate against synthetic chord events |
| Melody contour extraction | Quantitatively validated on synth | `librosa.pyin` pitch accuracy and voicing recall |
| Section boundaries | Partially validated | `mir_eval.segment.detection` F@0.5s / F@3s |
| Snapshot determinism | Verified | 15 output artefact hashes |
| Genre baseline scoring | Partially verified | Profile-specific scores are deterministic, not quality labels |
| RPE physical scores | Unverified | Heuristic proximity to static baselines |
| UGHer score | Unverified | Token / anchor / Delta-E heuristics |
| SVP YAML output | Deterministic | Snapshot hash checked |
| DomainProfile packaging | Verified | Local + packaged resource fallback tests |

## 5. Interpretation Rules

- `rpe_score`, `ugher_score`, and `integrated_score` are not production
  quality labels.
- High score means "close to current deterministic heuristics", not "good
  music".
- Synthetic validation proves deterministic measurement behavior, not broad
  genre coverage.
- Real-world validity still requires a labeled validation dataset.

## 6. Per-Stem Validation

Q3 source separation is implemented as an opt-in path because Demucs is too
heavy for the default CI environment. Current validation is split accordingly:

| Q3 criterion | Synthetic CI status | Real-audio / Demucs status |
|---|---|---|
| Summed-stem residual < 5% | Verified by `tests/test_stem_validation.py` using deterministic synthetic stems | Local Demucs smoke tests passed with `htdemucs` CPU: `synth_03` residual `0.034802`; external real-audio 30s MP3 excerpt residual `0.032082`; no committed real-audio stem corpus yet |
| Per-stem BPM matches full mix | Verified by `tests/test_stem_validation.py` on a pulsed synthetic stem bundle | Local Demucs smoke test on `synth_03_mid_groove_g_major` failed: drums `24.15` BPM and vocals `129.20` BPM vs full mix `120.19`; sparse stems may not yield stable BPM |

Manual real-audio check:

```bash
python scripts/validate_stem_separation.py track.wav
python scripts/validate_stem_separation.py track.wav --check
python scripts/validate_stem_separation.py track.wav --json
```

This script requires the optional Demucs dependency (`svp-rpe[separate]`) and
system `ffmpeg` / `ffprobe` on `PATH`. On Windows with TorchAudio 2.9+, use a
shared FFmpeg build so TorchCodec can load the FFmpeg DLLs. This manual check
does not turn the score into a production music-quality label.

## 7. Learned vs Deterministic (synth, Q4'-6)

**synth-only, not yet sufficient for promotion to PhysicalRPE**

The learned-output validation harness compares opt-in learned model output
against the current deterministic estimates on the synthetic sample corpus.
It is a read-only report: learned downbeats and note events remain attached
only through `RPEBundle.learned_annotations`.

Manual command:

```bash
python scripts/compare_learned_against_truth.py
python scripts/compare_learned_against_truth.py --json
python scripts/compare_learned_against_truth.py --song synth_03_mid_groove_g_major
```

| song_id | downbeat F (det) | downbeat F (learn) | downbeat winner | note onset+pitch F (det) | note onset+pitch F (learn) | note winner |
|---|---:|---:|---|---:|---:|---|
| generated by `scripts/compare_learned_against_truth.py` | n/a | n/a | n/a | n/a | n/a | n/a |

This is a schema placeholder. The script writes ignored local reports to
`examples/learned_validation/summary.json` and
`examples/learned_validation/per_song/*.json`; run the harness locally to
populate concrete values.

Because this report uses synthetic audio only, learned-model wins here are
promotion candidates, not validation of real-world music analysis quality.
Promotion still requires real-audio human ground truth before learned output
can replace or write through to `PhysicalRPE`.

## 8. Real-Audio Measurement Harness

**Measurement storage only; not yet real-audio accuracy validation.**

Real-audio files are intentionally not committed to the repository. Use a local
manifest to run the current deterministic pipeline over external WAV/MP3 files
and store ignored development artifacts under
`examples/real_audio_validation/runs/`.

Manifest template:

```yaml
schema_version: "1.0"
tracks:
  - id: local_real_audio_example
    path: "C:/path/to/your/audio.wav"
    baseline: pro
    notes: "local notes"
```

Manual command:

```bash
python scripts/measure_real_audio.py path/to/real_audio_manifest.yaml
python scripts/measure_real_audio.py path/to/real_audio_manifest.yaml --json
python scripts/measure_real_audio.py path/to/real_audio_manifest.yaml --learned
python scripts/measure_real_audio.py path/to/real_audio_manifest.yaml --separate
```

For each track, the harness writes:

- `rpe.json`
- `svp.yaml`
- `evaluation.json`
- per-run `summary.json`
- per-run `summary.md`

The generated `rpe_score`, `ugher_score`, and `integrated_score` remain
deterministic heuristic measurements. They must not be read as production
music-quality truth. Real-audio validation still requires a separate
human-annotated dataset with BPM/key/downbeat/chord/melody/section ground truth
before accuracy claims can be made.

## 9. Next Validation Work

- Q2 follow-up: replace downbeat fallback with a stronger tracker when the
  dependency story is stable.
- Q3 real-audio follow-up: add CC0 tracks with stem-level ground truth and
  record manual `validate_stem_separation.py` outputs.
- Q4'-7: evaluate learned wins against real-audio human annotations before
  proposing any promotion into `PhysicalRPE`.
- Add CC0 real-audio samples for genre and production-style coverage.
