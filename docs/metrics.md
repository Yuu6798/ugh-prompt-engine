# RPE Physical Metrics

## Core Metrics

| Metric | Definition | Formula |
|--------|-----------|---------|
| RMS Mean | Frame-level RMS average | `mean(librosa.feature.rms(y))` |
| Active Rate | Fraction of frames above RMS threshold | `count(rms > threshold) / total_frames` |
| Crest Factor | Peak-to-RMS ratio | `peak_amplitude / rms_mean` |
| Valley Depth | Dynamic range (P90-P10 of RMS) | `P90(rms) - P10(rms)` |
| Thickness | Sonic density composite | `w1*spectral_richness + w2*rms_norm + w3*(1-valley_norm)` |
| Dynamic Range (dB) | Frame RMS P95/P10 ratio in dB | `20 * log10(P95(rms) / P10(rms))`, both floored at 1e-3 |

`dynamic_range_db` is a lightweight cross-song descriptor of loudness
variation. It is **not** EBU R128 LRA — proper LRA requires K-weighted
short-term loudness with absolute and relative gating. The `_db` suffix is
intentional to prevent confusion with LRA values.

## Track-Level Dynamics Summary

`PhysicalRPE.dynamics_summary` aggregates the multi-feature novelty curve
(`compute_novelty_curve`: RMS + onset + spectral flux + chroma change) into
five descriptive numbers suitable for cross-song comparison.

| Field | Definition |
|-------|-----------|
| `peak_novelty` | Maximum value of the novelty curve |
| `mean_novelty` | Mean across the curve |
| `std_novelty` | Standard deviation across the curve |
| `event_count` | Local maxima above `mean + 0.5*std` |
| `temporal_balance` | First-half mean / whole-track mean. >1 = front-loaded, <1 = back-loaded, ≈1 = balanced |

Returns `None` for tracks ≤5s (the novelty curve is not computed at that
scale). The novelty curve itself is reused from the existing structure
detection path, so the added cost is only the aggregation step.

## Spectral Metrics

| Metric | Definition |
|--------|-----------|
| Spectral Centroid | Center of spectral mass (Hz)。**dark/bright 定性ラベルの正規センサー**（`semantic_rules.yaml`: dark ≤ 1200 Hz / bright ≥ 2500 Hz、中間はニュートラル） |
| Low Ratio | Energy below 300 Hz / total |
| Mid Ratio | Energy 300-4000 Hz / total |
| High Ratio | Energy above 4000 Hz / total |
| Brightness (band ratio) | High / (low + mid + high)。**legacy**: HF の乏しい素材で 0 に張り付き盲目になるため、dark/bright 判定には使わない（K1 grip 測定で確認、[`controllability_poc.md`](controllability_poc.md) §5.1） |

### Magnitude Spectral Bands (Q1-5)

`PhysicalRPE.spectral_bands` is an additive seven-band spectrum computed from
STFT magnitude `|S|`, not power `|S|^2`. The existing
`PhysicalRPE.spectral_profile.low_ratio/mid_ratio/high_ratio` is intentionally
unchanged for backward compatibility with semantic rules and historical reports.

| Field | Band (Hz) | Weighting |
|---|---:|---|
| `sub_bass` | 20-60 | `sum(|S| in band) / sum(|S| in reported bands)` |
| `bass` | 60-250 | `sum(|S| in band) / sum(|S| in reported bands)` |
| `low_mid` | 250-500 | `sum(|S| in band) / sum(|S| in reported bands)` |
| `mid` | 500-2000 | `sum(|S| in band) / sum(|S| in reported bands)` |
| `high_mid` | 2000-4000 | `sum(|S| in band) / sum(|S| in reported bands)` |
| `presence` | 4000-6000 | `sum(|S| in band) / sum(|S| in reported bands)` |
| `brilliance` | 6000-20000 | `sum(|S| in band) / sum(|S| in reported bands)` |

Band upper bounds are clipped to the input Nyquist frequency. With the current
default loader target sample rate of 22050 Hz, `brilliance` therefore reports
6000-11025 Hz; callers that need the full 11-20 kHz region must extract from
audio loaded at a sufficient sample rate. The denominator is the union of the
reported/clipped band masks, so magnitude below 20 Hz or above 20 kHz does not
dilute the seven serialized ratios.

Why this is separate: the legacy three-band profile accumulates power
(`|S|^2`). That makes it stable for existing rule thresholds but can overweight
low-frequency fundamentals and under-report broadband high-frequency content.
The magnitude seven-band profile tracks the same weighting family as spectral
centroid, so it is the preferred descriptive spectrum when inspecting brightness
distribution. Do not reinterpret legacy `high_ratio` as a brightness sensor;
centroid remains the semantic dark/bright sensor, and `spectral_bands` is the
additive diagnostic distribution.

## Temporal Metrics

| Metric | Definition |
|--------|-----------|
| BPM | Beats per minute (librosa beat_track). Faster-tempo collapse (×2 octave + 3:2 sub-octave) is flagged in `PhysicalRPE.bpm_octave_ambiguous` / `bpm_candidates` and corrected to the recovered tempo — see below |
| Tempo Stability Std | Standard deviation of frame-wise tempo estimates (`librosa.feature.rhythm.tempo(..., aggregate=None)`) |
| Time Signature | Beat-level onset strength autocorrelation over supported meters (`3/4`, `4/4`, `6/8`) |
| Downbeat Times | Beat-strength phase pick over the inferred meter (`PhysicalRPE.downbeat_times`) |
| Chord Events | Major/minor triad template match over chroma (`PhysicalRPE.chord_events`) |
| Melody Contour | `librosa.pyin` monophonic pitch contour (`PhysicalRPE.melody_contour`) |
| Key | Chroma → Krumhansl-Kessler template matching |
| Onset Density | Onsets per second |

## HPSS Metrics

`PhysicalRPE.harmonic_ratio` and `PhysicalRPE.percussive_ratio` are additive
descriptors derived from deterministic librosa HPSS
(`librosa.decompose.hpss` over STFT magnitude, avoiding waveform reconstruction).
Each component is measured by spectrogram energy after separation and normalized
so the pair sums to approximately 1.0. Pure sustained tones should be
harmonic-dominant; transient-heavy material should increase the percussive share.
These fields do not alter semantic rules or scoring thresholds in Q1-5.

### BPM Faster-tempo Collapse Detection (R2-2)

`detect_bpm_octave_ambiguity()` flags the **faster-tempo collapse** where a faster
true tempo is reported slower. The `octave` naming is kept for schema stability,
but two collapse families are covered:

- **octave halving** (R2-2a/2b): true `~2×bpm` reported at `bpm`
  (roundtrip_case_studies.md §4: true 175 BPM near 89).
- **3:2 sub-octave collapse** (R2-2d): true `~1.47×bpm` reported at `bpm` — the
  "117.45 attractor" (R1 screen: true ~172 reported at 117.45). librosa's tempo
  prior (centred ~120) picks the nearer BPM-grid point, so the true 172 grid loses.
  This is *not* a clean ÷2, so an exactly-2× model cannot recover it.

It scans the onset-strength autocorrelation across a faster-side lag neighborhood
`BPM_OCTAVE_NEIGHBORHOOD` = (1.4, 2.2)× `bpm` for the dominant overlap-normalized
peak (not a single fixed ×2 lag — grid quantization lands the real pulse off a
clean octave, e.g. 1.93×), and compares it against the detected-tempo lag:

- Ordinary subdivided music has comparable subdivision energy (overlap-normalized
  ratio ≈ 1.0; the Q1-3 synth fixtures sit at ≤ 1.0), so it is **not** flagged.
  Each lag's autocorrelation is normalized by its overlap count so the ratio is
  not biased upward on short clips.
- A genuine collapse makes the faster tempo the real beat, so its autocorrelation
  **dominates** (ratio ≥ `BPM_OCTAVE_RATIO_THRESHOLD` = 1.15). The ratio threshold
  is what discriminates a collapse (faster beat dominates) from a correctly
  detected tempo whose subdivisions are merely *comparable* — the Q1-3 fixtures,
  including the 3/4 waltz `synth_04`, stay ≈ 1.0 and unflagged under the widened
  window.

When flagged, `bpm_candidates` lists the plausible tempi (sorted) and the
extractor (R2-2c) **corrects** the reported `bpm` to the recovered faster tempo =
`max(candidates)`; the original collapsed reading survives as `min(candidates)`.
`bpm_confidence` is still capped at `BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP` = 0.5 and
the flag stays set, so the transcribe trust gate (`score_draft._bpm_untrusted`)
treats the corrected value as sensor-blind — it transcribes to `TODO(transcribe):
bpm undetected` (unlocked) rather than a faithful, locked value. The boolean flag,
not the cap alone, is what enforces this.

Scope: only the faster ("reported too slow") direction. The opposite ÷2
("reported too fast") direction is **not** added to the extractor: autocorrelation
magnitude cannot separate a true doubled report from ordinary double-period
support, beat-phase alternation produced false evidence on the synth fixtures, and
a low prior alone also collapses correctly detected tracks. R2-2e therefore handles
÷2 only in the corpus screener, where stated truth is available:
`bpm_doubling_prior_recovery` / `bpm_doubling_prior_recoverable` compare the raw
default BPM against a low-prior (`start_bpm=50`) re-estimate to distinguish
extractor doubling from generator unfaithfulness. The post-hoc detector remains a
*partial mitigation* for faster-side collapse; the principled fix for the attractor
is the tempo prior itself (adaptive / higher `start_bpm`), a separate
higher-regression task (roundtrip_corpus_screen.md).

#### BPM prior-disagreement detection (R2-2f)

`detect_bpm_prior_disagreement()` automates the corpus screener's high-prior
diagnostic (`scripts/screen_corpus.py`, `start_bpm=180`) as an extractor-level
detector, closing the gap R2-2's autocorrelation scan leaves for the 3:2
sub-octave family: the halving cases (detected ~117.45 / true ~172.3) do not
clear `BPM_OCTAVE_RATIO_THRESHOLD`, so `detect_bpm_octave_ambiguity` stays
unflagged for them (roundtrip_corpus_screen.md finding #6).

It re-runs `compute_bpm(y, sr, start_bpm=BPM_PRIOR_PROBE_START_BPM)` (180.0,
same as the screener's `HIGH_PRIOR_START_BPM`) to get a high-prior estimate H,
and compares it against the already-detected default-prior `bpm` D via the
ratio `r = H/D`:

- `r` within `BPM_PRIOR_AGREEMENT_TOLERANCE` (0.04) of 1.0 → the prior agrees,
  not a disagreement.
- `r` inside `BPM_PRIOR_DISAGREEMENT_WINDOW` = (1.35, 1.6) → fires. This is a
  deliberately narrow band: `scratchpad/bpm_prior_decision_table.yaml`
  (mechanically extracted repo records) shows the recoverable 3:2 halving
  family clustering tightly at r ≈ 1.467-1.475 (so_what_run / wafu_jungle_174 /
  astral_trigger / rock_05_punk), while every recorded correctly-detected track
  either agrees (r ≈ 1.0) or overshoots into the doubling-artifact band
  (r ≈ 2.0, e.g. yaoyorozu_shinwa 2.0773 — a correct slow track that a naive
  high-prior tie-break would falsely double). No correctly-detected track in
  the repo's records falls inside (1.35, 1.6).
- Any other ratio (≈ 2.0 doubling-artifact band, r < 1, or the unobserved
  1.6-2.0 gap) → does not fire. The window is not generalized beyond the
  observed cluster.

The probe runs **only when R2-2 (`detect_bpm_octave_ambiguity`) did not already
flag ambiguity** — the two detectors never fire on the same track, so there is
no double correction. When R2-2f fires, the extractor corrects `bpm` to
`max(candidates)` (same precedent as R2-2c), caps `bpm_confidence` at
`BPM_OCTAVE_AMBIGUOUS_CONFIDENCE_CAP`, and sets **both**
`bpm_octave_ambiguous=True` (shared trust-gate semantics — the transcribe trust
gate `score_draft._bpm_untrusted` keys off this flag, so it closes with zero
extra wiring) and `bpm_prior_disagreement=True` (provenance: which detector
fired). `bpm_candidates` is populated the same way as R2-2c.

Scope: this detector only automates the observed 3:2 band. It does not attempt
an sr ensemble, a low-prior doubling auto-correction, or flag-only handling of
unobserved ratio bands (1.6-1.8 etc.) — see roundtrip_corpus_screen.md finding
#6 for the reasoning. The screener (`scripts/screen_corpus.py`) still calls the
raw `compute_bpm` directly for its own diagnostics and is unaffected by this
detector.

### Time Signature Detection (Q1-2)

`compute_time_signature()` estimates meter without learned models:

1. Detect beats with `librosa.beat.beat_track`.
2. Sample normalized onset strength around each beat.
3. Compute autocorrelation over the beat-strength sequence.
4. Emit `3/4` when lag-3 clearly dominates nearby duple/quadruple lags.
5. Emit `6/8` when lag-6 dominates lag-3 while lag-3 remains strong.
6. Fall back to `4/4` with low confidence when beat evidence is insufficient.

The current validation set contains four `4/4` synth samples and one `3/4`
waltz sample. `6/8` support is covered by a synthetic beat-strength unit test;
an audio-level 6/8 fixture is deferred until the sample set is expanded.

### Downbeat Detection (Q2-1)

`compute_downbeat_times()` estimates downbeats without learned models:

1. Detect beats with `librosa.beat.beat_track`.
2. Sample normalized onset strength around each beat.
3. Parse the inferred meter numerator (`3/4` → 3, `4/4` → 4, `6/8` → 6).
4. Choose the strongest beat-strength phase within each bar.
5. Emit `PhysicalRPE.downbeat_times` as sorted seconds.

This is a lightweight deterministic fallback. The roadmap target remains
madmom-backed downbeat tracking, but `madmom==0.16.1` does not currently build
cleanly in the Python 3.11 environment without extra native/Cython setup. The
fallback gives a reviewable Q2-1 baseline while keeping installation stable.

### Chord Event Detection (Q2-2)

`compute_chord_events()` estimates coarse harmonic blocks without new
dependencies:

1. Compute `librosa.feature.chroma_cqt`.
2. Match each chroma frame against normalized major/minor triad templates.
3. Merge consecutive frames with the same chord.
4. Drop events shorter than 0.75s.
5. Emit `ChordEvent(chord, root, quality, start_sec, end_sec, confidence)`.

The detector is intentionally limited to major/minor triads. It is a
deterministic validation baseline for the synthetic I/IV/V-style samples, not a
production chord recognizer.

### Melody Contour Extraction (Q2-3)

`compute_melody_contour()` estimates a monophonic pitch track without adding new
dependencies:

1. High-pass the input at 300 Hz to reduce bass/chord root dominance.
2. Run `librosa.pyin` with C2-C7 bounds and hop length 2048.
3. Store frame-aligned `times`, `frequencies_hz`, and `voicing` probabilities.
4. Emit `None` for silence or tracks with no meaningful voicing evidence.

The current validation target is synthetic, clearly voiced melody regions:
pitch accuracy within +/-50 cents and voicing recall over the melody ground
truth intervals. This is not a production vocal transcription system.

## Stereo Metrics

| Metric | Definition |
|--------|-----------|
| Width | RMS(L-R) / RMS(L+R) |
| Correlation | Pearson correlation between L and R channels |

## Baseline Profiles (Q1-4)

`score_rpe()` compares physical metrics against a named baseline profile.
The default is `pro`, preserving the original single-baseline behavior.

| Profile | Config | Intended use |
|---|---|---|
| `pro` | `config/pro_baseline.yaml` | General commercial mastering baseline |
| `loud_pop` | `config/loud_pop_baseline.yaml` | Loud pop / rock with high RMS and lower crest factor |
| `acoustic` | `config/acoustic_baseline.yaml` | Acoustic / jazz with lower RMS and wider dynamics |
| `edm` | `config/edm_baseline.yaml` | Electronic / dance mixes with dense low-end and stronger section contrast |

| Metric | pro | loud_pop | acoustic | edm |
|--------|---:|---:|---:|---:|
| rms_mean | 0.298 | 0.38 | 0.15 | 0.35 |
| active_rate | 0.915 | 0.95 | 0.75 | 0.92 |
| crest_factor | 5.0 | 3.5 | 8.0 | 4.0 |
| valley_depth | 0.2165 | 0.15 | 0.25 | 0.35 |
| thickness | 2.105 | 2.5 | 1.5 | 2.8 |

These values are initial calibration anchors, not production-quality truth.
Select explicitly via `score_rpe(phys, baseline="edm")` or CLI `--baseline edm`.

## Scoring

RPE Score: proximity to Pro baseline, each metric [0,1], averaged.

UGHer Score (`UGHerScore`, 4-component, weighted; see `eval/scorer_ugher.py`):
- `por_similarity` (weight 0.3): token overlap between `por_core` and the SVP prompt
- `grv_consistency` (weight 0.3): primary anchor / BPM / key reflected in the SVP
- `delta_e_assessment` (weight 0.2): transition type preserved in `evaluation_criteria`
- `physical_accuracy` (weight 0.2): generated `physical_checks` count / 4 (clamped to [0,1])

Integrated: `w_ugher * ugher + w_rpe * rpe` (default 50/50).

## Valley Depth Methods (v0.2)

| Method | Formula | Use Case |
|--------|---------|----------|
| `rms_percentile` | P90(RMS) - P10(RMS) | Frame-level dynamic range |
| `section_ar` | AR_main - AR_min across sections | Section-level contrast |
| `hybrid` (default) | 0.5 * rms_percentile + 0.5 * section_ar | Balanced estimate |

ValleyDiagnostics output: rms_p90, rms_p10, ar_main, ar_min, chorus_sections, lowest_section, confidence.

## Comparison Metrics (v0.2)

SemanticDiff: por_lexical_similarity, grv_anchor_match, delta_e_profile_alignment, instrumentation_context_alignment.
PhysicalDiff: bpm_diff, key_match, rms_diff, valley_diff, active_rate_diff, thickness_diff, spectral_centroid_diff.
action_hints: auto-generated improvement suggestions based on diffs.

## Metrics v2 (level-invariant)

The legacy `active_rate` (RMS > fixed 0.01 threshold) and `valley_depth`
(`rms_percentile`/`section_ar`/`hybrid`, all built on absolute RMS
differences) are **level-dependent**: a loud/produced master saturates
`active_rate` to 1.0, and `valley_depth` scales down ~linearly when the whole
track is attenuated (empirically ×0.25 at -12 dB). Synthetic-signal testing
also showed the two formulas conflate four distinct concepts (silence,
density, dynamics, transient punch) into two numbers, so neither separates
Pro from AI-generated masters. `crest_factor` (peak/RMS) was the one legacy
metric already correct — it is level-invariant and was the only metric that
separated a Pro corpus (crest ≈ 5.1) from an AI corpus (crest ≈ 4.2).

Metrics v2 (`rpe/physical_features.py`) gates every measurement against the
track's own robust peak (`P99.99(|y|)`, not an absolute threshold) and
expresses dynamics in dB, keeping the four concepts separate:

| Metric | Field | Formula | Concept |
|---|---|---|---|
| Silence rate | `PhysicalRPE.silence_rate` | `mean(frame_rms <= peak*10^(-40/20))` | is it actually silent here? |
| Active rate v2 | `PhysicalRPE.active_rate_v2` | `1 - silence_rate` | level-invariant `active_rate` |
| Fullness | `PhysicalRPE.fullness` | `mean(frame_rms >= P95(non-silent) * 10^(-12/20))` | wall-of-sound density |
| Valley (dB) | `PhysicalRPE.valley_db` | `20*log10(P95(non-silent)/P10(non-silent))` | level-invariant dynamic range |
| Valley (0-1) | `PhysicalRPE.valley_norm` | `clamp(valley_db / 30)` | 0-1 normalized (30 dB = dramatic) |
| Crest (robust) | `PhysicalRPE.crest_factor_robust` | `P99.99(\|y\|) / rms` | transient punch (Pro/AI separator) |

Frame RMS uses `librosa.feature.rms(frame_length=2048, hop_length=512)`
throughout. All fields are `Optional`, always populated by the extractor
regardless of `valley_depth_method`, and `schema_version` stays `"1.0"`
(purely additive, existing JSON deserializes unchanged).

`valley_depth_method` now accepts `"v2"` (**default**, `valley_depth` =
`valley_norm`), `"legacy_hybrid"` (alias of `"hybrid"`, identical value —
the explicit opt-out name), plus the unchanged `rms_percentile`/
`section_ar`/`hybrid`. `ValleyDiagnostics.v2_db_value`/`v2_norm_value` are
always populated alongside the three legacy `*_value` fields. `--valley-method
legacy_hybrid` reproduces pre-v2 behavior exactly.

`compare_physical_diff` scores valley/active-rate on the v2 fields
(`valley_db_diff`/12 dB, `active_rate_v2_diff`/0.3) when both sides of a
comparison carry them, falling back to the legacy `valley_diff`/0.3 and
`active_rate_diff`/0.3 scoring for pre-v2 RPE JSON. Action hints likewise
switch to a crest-driven hint ("トランジェントの張り不足") once v2 data is
present, since crest — not valley/AR — is the metric that actually tracks
Pro/AI quality (Metrics_v2_spec.md §4-5); the legacy "Bridge/Verse 低密度" /
"breakdown 挿入" hints remain for legacy-only comparisons.

Baseline config (`config/*_baseline.yaml`) `active_rate_ideal` and
`valley_depth_pro` are frozen (values unchanged) pending an n=20 v2
re-baseline; see the `DEPRECATED` header comment in each file.
