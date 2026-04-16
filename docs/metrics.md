# RPE Physical Metrics

## Core Metrics

| Metric | Definition | Formula |
|--------|-----------|---------|
| RMS Mean | Frame-level RMS average | `mean(librosa.feature.rms(y))` |
| Active Rate | Fraction of frames above RMS threshold | `count(rms > threshold) / total_frames` |
| Crest Factor | Peak-to-RMS ratio | `peak_amplitude / rms_mean` |
| Valley Depth | Dynamic range (P90-P10 of RMS) | `P90(rms) - P10(rms)` |
| Thickness | Sonic density composite | `w1*spectral_richness + w2*rms_norm + w3*(1-valley_norm)` |

## Spectral Metrics

| Metric | Definition |
|--------|-----------|
| Spectral Centroid | Center of spectral mass (Hz) |
| Low Ratio | Energy below 300 Hz / total |
| Mid Ratio | Energy 300-4000 Hz / total |
| High Ratio | Energy above 4000 Hz / total |
| Brightness | High / (low + mid + high) |

## Temporal Metrics

| Metric | Definition |
|--------|-----------|
| BPM | Beats per minute (librosa beat_track) |
| Key | Chroma → Krumhansl-Kessler template matching |
| Onset Density | Onsets per second |

## Stereo Metrics

| Metric | Definition |
|--------|-----------|
| Width | RMS(L-R) / RMS(L+R) |
| Correlation | Pearson correlation between L and R channels |

## Pro Baseline (config/pro_baseline.yaml)

| Metric | Pro Value |
|--------|----------|
| rms_mean | 0.298 |
| active_rate | 0.915 |
| crest_factor | 5.0 |
| valley_depth | 0.2165 |
| thickness | 2.105 |

## Scoring

RPE Score: proximity to Pro baseline, each metric [0,1], averaged.

UGHer Score (4-component, v0.2):
- `por_lexical_similarity`: token + synonym overlap of por_core (config/synonym_map.yaml)
- `grv_anchor_match`: BPM/key/duration/primary anchor alignment
- `delta_e_profile_alignment`: transition type + intensity match
- `instrumentation_context_alignment`: production notes token overlap

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
PhysicalDiff: bpm_diff, key_match, rms_diff, valley_diff, active_rate_diff, thickness_diff.
action_hints: auto-generated improvement suggestions based on diffs.
