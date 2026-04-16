# Architecture

## Pipeline

```
Audio (WAV/MP3) → RPE Extraction → SVP Generation → Evaluation
                  ├── Physical     ├── 5 blocks      ├── RPE Score
                  └── Semantic     └── Minimal SVP    ├── UGHer Score
                                                      └── Integrated
```

## Three-Layer Design (ugh-audit-core pattern)

| Layer | ugh-audit-core | svp-rpe |
|-------|---------------|---------|
| Detection | `detect()` → Evidence | `extract()` → RPEBundle |
| Calculation | `calculate()` → State | `generate()` → SVPBundle |
| Decision | `decide()` → verdict | `evaluate()` → scores |

## Modules

### io/audio_loader.py
- WAV/MP3/FLAC loading via librosa + soundfile
- Mono/stereo support, resampling
- AudioData + AudioMetadata models

### rpe/physical_features.py
- 10+ physical metrics (RMS, crest, valley, thickness, spectral, BPM, key, onset)
- All deterministic, same waveform → same output

### rpe/structure.py
- RMS/onset-based segment detection
- Guarantees at least 1 section

### rpe/semantic_rules.py
- Rule-based physical → semantic mapping
- Rules externalized in config/semantic_rules.yaml

### rpe/extractor.py
- Integrates physical + semantic → RPEBundle

### svp/generator.py
- RPEBundle → SVPBundle (5 blocks + MinimalSVP)
- Deterministic transformation

### eval/scorer_*.py
- RPE: physical quality vs Pro baseline
- UGHer: semantic consistency (token overlap MVP)
- Integrated: weighted combination

## Config Files

| File | Purpose |
|------|---------|
| config/pro_baseline.yaml | Pro reference values for RPE scoring |
| config/semantic_rules.yaml | Physical → semantic mapping rules |
| config/svp_templates.yaml | SVP generation templates |

## Known Limitations (v0.1)

- Key detection uses Krumhansl-Kessler templates (no deep learning)
- Semantic layer is heuristic rule-based, not trained
- por_similarity uses token overlap (embedding in future)
- Structure detection is basic RMS/onset (no deep segmentation)
- Time signature fixed at 4/4 (low confidence)
