# Architecture

## Pipeline

```
Audio (WAV/MP3) → RPE Extraction → SVP Generation → Evaluation
                  ├── Physical     ├── 5 blocks      ├── RPE Score
                  ├── Semantic     └── Minimal SVP    ├── UGHer Score (4-component)
                  └── Structure                       ├── Integrated
                                                      └── Comparison (vs external SVP)
```

## Three-Layer Design (ugh-audit-core pattern)

| Layer | ugh-audit-core | svp-rpe |
|-------|---------------|---------|
| Detection | `detect()` → Evidence | `extract()` → RPEBundle |
| Calculation | `calculate()` → State | `generate()` → SVPBundle |
| Decision | `decide()` → verdict | `evaluate()` / `compare()` → scores + action_hints |

## Modules

### io/audio_loader.py
- WAV/MP3/FLAC loading via librosa + soundfile
- Mono/stereo support, resampling
- AudioData + AudioMetadata models

### rpe/physical_features.py
- 10+ physical metrics (RMS, crest, valley, thickness, spectral, BPM, key, onset)
- All deterministic, same waveform → same output

### rpe/valley.py (v0.2 — strategy pattern)
- 3 methods: `rms_percentile`, `section_ar`, `hybrid` (default)
- ValleyDiagnostics with rms_p90/p10, ar_main/min, confidence

### rpe/structure_novelty.py (v0.2 — multi-feature)
- Combined novelty curve: RMS derivative + onset strength + spectral flux + chroma change
- Improved boundary detection vs v0.1 RMS-only

### rpe/structure_labels.py
- Heuristic section labels: Intro / Verse / Chorus / Bridge / Outro
- Based on energy profile ranking

### rpe/section_features.py
- Per-section feature vectors (RMS, active rate, spectral, onset, flux, chroma)

### rpe/semantic_rules.py
- Rule-based physical → semantic mapping
- Rules externalized in config/semantic_rules.yaml

### rpe/extractor.py
- Integrates physical + semantic + structure v2 + valley strategy → RPEBundle

### svp/generator.py
- RPEBundle → SVPBundle (5 blocks + MinimalSVP)
- Deterministic transformation

### svp/parser.py (v0.2)
- Parse external SVP files (YAML or text/markdown)
- Returns ParsedSVP for comparison

### eval/scorer_*.py
- RPE: physical quality vs Pro baseline
- UGHer: semantic consistency (4-component: por/grv/delta_e/instrumentation)
- Integrated: weighted combination

### eval/comparison.py (v0.2)
- Reference RPE vs candidate SVP comparison
- SemanticDiff + PhysicalDiff + action_hints generation
- Self and cross-comparison modes

### eval/semantic_similarity.py (v0.2)
- Token + synonym overlap for PoR similarity
- Synonym map config: config/synonym_map.yaml

### eval/anchor_matcher.py (v0.2)
- GRV anchor alignment (primary, BPM, key, duration, terms)

### eval/delta_e_alignment.py (v0.2)
- ΔE profile type + intensity matching

### batch/runner.py (v0.2)
- Multi-file batch processing
- Ranking, summary CSV/JSON, next_action.md generation

## Config Files

| File | Purpose |
|------|---------|
| config/pro_baseline.yaml | Pro reference values for RPE scoring |
| config/semantic_rules.yaml | Physical → semantic mapping rules |
| config/svp_templates.yaml | SVP generation templates |
| config/synonym_map.yaml | Synonym groups for semantic similarity |

## Known Limitations (v0.2)

- Key detection uses Krumhansl-Kessler templates (no deep learning)
- Semantic layer is heuristic rule-based, not trained
- por_similarity uses token + synonym overlap (embedding in future)
- Section labels are energy-heuristic (not ML-based)
- Time signature fixed at 4/4 (low confidence)
- Batch mode is sequential (no parallel processing)
- **Scorer / comparison は semantic v2.0 の新情報（`SemanticLabel.confidence` /
  `evidence` / `layer`）を未活用** — `eval/scorer_ugher.py` と
  `eval/comparison.py` は `por_core` / `grv_anchor` / `delta_e_profile` のみを
  参照する。PR #8 で導入された evidence-bearing 層の confidence 重み付けや
  layer ごとのスコア分離は未実装。改善余地として
  [`roadmap_goal1.md`](roadmap_goal1.md) Q4 の "未活用 / フォローアップ余地"
  節（Q4-fu1）を参照
