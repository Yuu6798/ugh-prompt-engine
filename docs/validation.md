# Validation Status

Current status: **PoC**.

This repository provides a deterministic local SVP/RPE pipeline, but the scoring outputs are **not yet validated** as ground-truth measures of production music quality. Treat scores as diagnostic heuristics for development and comparison experiments, not as objective judgments of musical quality.

## Current Evidence

| Area | Status | What is currently checked | Ground truth needed next |
|---|---|---|---|
| BPM extraction | Partially verified | Deterministic extraction on synthetic and fixture audio | Human/DAW-labeled BPM set across genres and tempos |
| Key detection | Unverified | Deterministic key field generation only | Labeled key/mode dataset with ambiguous-key annotations |
| Structure labels | Unverified | Deterministic section output and non-empty labels | Human-labeled section boundaries and role labels |
| RPE physical scores | Unverified | Heuristic proximity to static baseline values | Calibrated reference set with expert mix-quality labels |
| UGHer score | Unverified | Token/anchor/Delta-E consistency heuristics | Human-rated semantic preservation pairs |
| SVP YAML output | Verified for determinism | Stable `rpe.json`, `svp.yaml`, and `evaluation.json` hashes for same synthetic input | Broader fixture corpus and release snapshots |
| DomainProfile packaging | Verified for packaging path | Local config fallback plus packaged resource fallback tests | Wheel install smoke test in clean environment |

## Interpretation Rules

- Do not treat `rpe_score`, `ugher_score`, or `integrated_score` as truth labels for production music quality.
- A higher score currently means "closer to the implemented heuristics," not "better music."
- The pipeline is deterministic for a fixed input and environment, but metric validity requires validation datasets.
- Before using this as an evaluator, build a ground-truth corpus with labeled BPM, key, structure, semantic preservation, and human quality ratings.

## Next Validation Work

1. Build a small audio validation set with known BPM/key and human-annotated section boundaries.
2. Compare extracted BPM/key/structure against that ground truth and publish error rates.
3. Collect paired reference/candidate outputs with human semantic-preservation ratings.
4. Calibrate `rpe_score`, `ugher_score`, and `integrated_score` against those labels.
5. Add snapshot fixtures for representative audio cases after the validation set is stable.
