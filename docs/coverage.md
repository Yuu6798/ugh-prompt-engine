# Measurement Coverage

Status: **PoC -- validated on deterministic synthetic data only**.

This page states what the current SVP/RPE pipeline can measure, what it can
only measure with important caveats, and what it cannot measure. Raw validation
numbers are maintained in [validation.md](validation.md); metric definitions are
maintained in [metrics.md](metrics.md).

The central interpretation rule is simple: `rpe_score`, `ugher_score`, and
`integrated_score` are deterministic heuristic signals. They are not production
music-quality truth.

## Can Measure

These signals are implemented and have either synthetic ground-truth validation
or deterministic regression coverage.

| Area | Current method | Current evidence | Known limitation |
|---|---|---|---|
| Audio loading metadata | `librosa` / `soundfile` load path | WAV/MP3 loader tests and sample fixture tests | Unsupported decode errors still depend on backend behavior |
| Duration / sample rate / channels | Loader metadata | Ground-truth sample metadata tests | Resampling behavior follows loader configuration |
| BPM / tempo | `librosa.beat.beat_track` plus confidence calibration | 4/5 synth samples within 5 BPM | `synth_01` slow-pad octave error remains |
| Key / mode | Chroma against Krumhansl-Kessler templates | 5/5 synth samples pass weighted key score | Enharmonic spelling and modulation are not modeled |
| Time signature | Beat-strength autocorrelation for `3/4`, `4/4`, `6/8` | 5/5 synth samples for `3/4` and `4/4`; `6/8` unit-tested | `6/8` has no audio-level fixture yet |
| Downbeat times | Deterministic beat-strength phase fallback | 4/5 synth samples meet hit-rate threshold | `synth_02` phase drift remains; madmom is deferred |
| Chord events | Major/minor triad templates over chroma | 5/5 synth samples meet chord hit-rate threshold | Triads only; no inversions, sevenths, extensions, or modulations |
| Melody contour | `librosa.pyin` after high-pass filtering | 5/5 synth samples meet pitch accuracy and voicing recall thresholds | Monophonic contour only; polyphony collapses to a dominant estimate |
| Section boundaries | Multi-feature novelty curve and boundary picking | 5/5 synth samples pass coarse F@3s threshold | Fine-grained boundary timing is not validated |
| RMS mean / peak / crest factor | Direct waveform statistics | Unit and snapshot coverage | Baseline interpretation is heuristic |
| Active rate | Fraction of RMS frames over threshold | Unit and snapshot coverage | Threshold is fixed; not genre-adaptive |
| Valley depth | `rms_percentile`, `section_ar`, or `hybrid` | Unit and snapshot coverage | Human-perceived arrangement contrast is only approximated |
| Thickness | Composite of spectral richness, RMS, and inverse valley | Unit and snapshot coverage | Not a standardized perceptual density metric |
| Spectral centroid / band ratios / brightness | STFT energy summaries | Unit and snapshot coverage | Mapping from spectrum to labels is rule-based |
| LUFS integrated loudness / true peak | `pyloudnorm` plus true-peak oversampling | Unit test against a 1 kHz / -20 dBFS ITU reference signal | Not validated on a broad reference-track suite |
| Stereo width / correlation | Channel difference/sum and correlation | Unit and snapshot coverage | Mono input has no stereo profile |
| Onset density | `librosa.onset.onset_detect` per second | Unit and snapshot coverage | Sensitive to onset detector configuration |
| Snapshot determinism | Hashes over RPE JSON, SVP YAML, and evaluation JSON | 15 expected-output hashes | Proves determinism, not real-world validity |

## Partially Measurable

These features exist, but their interpretation should stay conservative.

| Area | Current status | Missing validation |
|---|---|---|
| Per-stem RPE | `--separate` opt-in Demucs adapter emits vocals, drums, bass, and other `PhysicalRPE` entries | No stem-level ground-truth corpus; no per-stem BPM/key/brightness validation yet |
| Per-stem scoring | `score_rpe()` emits nested `stem_scores` with vocal/drum/bass/other baseline mapping | Baseline values are initial anchors, not validated against separated real stems |
| Genre baseline scoring | `pro`, `loud_pop`, `acoustic`, and `edm` profiles are selectable | Profile values are hand-calibrated anchors, not genre consensus truth |
| External SVP comparison | `compare` and `evaluate --svp` compute semantic and physical diffs | Diff thresholds are heuristic and not calibrated against human review labels |
| Semantic labels | `SemanticLabel` includes `label`, `layer`, `confidence`, `evidence`, and `source_rule` | Rule confidence is engineering calibration, not learned probability |
| `semantic_hypothesis` labels | Low-confidence rule hypotheses are separated from perceptual/structural labels | They should not be treated as factual mood or intent labels |
| UGHer score | Token overlap, anchor match, Delta-E alignment, and context alignment | No labeled dataset tying score to perceived prompt or production quality |
| Integrated score | Weighted combination of UGHer score and RPE score | No validation that the weighted total predicts production quality |
| Demucs runtime | Optional dependency behind `--separate` | Runtime and quality vary by hardware, model, and audio material |

## Cannot Measure

The current pipeline does not provide these capabilities.

- Production music quality as a ground-truth label.
- Commercial release readiness or mastering quality.
- Vocal quality, intelligibility, pronunciation, or lyric correctness.
- Human emotional response, mood, or intent beyond low-confidence rule labels.
- Automatic genre classification. Users choose a baseline; the system does not infer genre.
- Production technique identification such as compressor settings, reverb type, or mastering chain.
- Polyphonic transcription or multi-voice melody tracking.
- Robust real-world chord recognition beyond coarse major/minor triads.
- Music similarity search, retrieval, or embedding-based recommendation.
- Learned acoustic descriptors such as danceability, arousal, valence, or aggressiveness.
- Cross-domain SVP validity outside the current music/audio implementation.

## Interpretation Rules

- High `rpe_score` means close to current physical baselines, not "good music".
- High `ugher_score` means close to current semantic comparison heuristics, not
  "good prompt design" in a human-validated sense.
- High `integrated_score` means both heuristic families agree; it is still not
  a production-quality truth label.
- Synthetic validation proves deterministic measurement behavior on controlled
  samples. It does not prove accuracy on real recordings.
- `SemanticLabel.layer == "semantic_hypothesis"` is an uncertain rule inference.
  It must not be presented as a measured fact.
- Baseline profiles are relative calibration anchors. They should be selected
  explicitly and reported with results.

## Validation Dataset

The quantitative validation set currently contains five deterministic
sine-wave-based WAV files in `examples/sample_input/`:

- BPM targets: 60, 90, 120, 140, 170
- Keys: C major, A minor, G major, F# minor, D major
- Time signatures: four `4/4` samples and one `3/4` sample
- Ground truth: `examples/sample_input/ground_truth.yaml`

As of the current validation report, 3/5 samples pass all enforced thresholds.
Known failures are documented:

- `synth_01_slow_pad_c_major`: BPM octave error
- `synth_02_minor_pulse_a_minor`: downbeat phase drift

Real-audio CC0 validation and stem-level ground truth remain future work.
