# ugh-prompt-engine

SVP (Semantic Vector Prompt) + RPE (Reverse Prompt Engineering) — UGHer ecosystem prompt infrastructure.

## Current Status

- **Current status: PoC**
- **Not yet validated as production music quality evaluator**
- Deterministic local pipeline, but score validity requires a validation dataset.

See [Validation Status](docs/validation.md) and [Measurement Coverage](docs/coverage.md) for the current validation boundary, measurement coverage, and required ground truth.

## Overview

音楽ファイル（WAV/MP3）から RPE を抽出し、決定論的に SVP を生成し、
UGHer 系 + RPE 系の二系統評価を行うローカル完結型ツール。

- **RPE**: 音声波形から物理特徴量 + ルールベース意味層を抽出
- **SVP**: RPE から構造化プロンプトを決定論的に生成
- **Eval**: Pro 基準値 (RPE) + 意味的整合性 (UGHer) の統合スコアリング
- **Semantic CI**: Target SVP から Expected RPE を生成し、fixture と比較して修復SVPを返す

API キー不要、LLM 不要、同一入力 → 同一出力の完全決定論的パイプライン。

関連プロジェクト: [ugh-audit-core](https://github.com/Yuu6798/ugh-audit-core)

## Setup

```bash
pip install -e ".[dev]"
```

## Quick Start

```bash
# Full pipeline: extract → generate → evaluate
svprpe run track.wav --output-dir ./output

# Individual steps
svprpe extract track.wav -o rpe.json
svprpe generate rpe.json --format yaml
svprpe evaluate --audio track.wav

# Compare against external SVP
svprpe evaluate --audio track.wav --svp design.yaml

# Compare reference vs candidate
svprpe compare --reference-audio ref.wav --candidate-audio gen.wav

# Deterministic semantic CI fixture check
svprpe ci-check examples/semantic_ci/pass_perfect/target_svp.json \
  examples/semantic_ci/pass_perfect/observed_rpe.json
svprpe ci-check examples/semantic_ci/repair_degraded/target_svp.json \
  examples/semantic_ci/repair_degraded/observed_rpe.json --format markdown \
  -o semantic_ci_report.md
svprpe ci-check examples/semantic_ci/repair_degraded/target_svp.json \
  examples/semantic_ci/repair_degraded/observed_rpe.json --threshold 0.6

# Batch processing
svprpe batch ./audio_files --svp-dir ./designs --mode compare --output-dir ./results

# Valley method selection
svprpe run track.wav --valley-method section_ar

# Help
svprpe --help
```

## Project Structure

```
src/svp_rpe/               # Main package (src layout)
├── cli.py                 # typer CLI (svprpe command)
├── io/audio_loader.py     # WAV/MP3 loading
├── rpe/                   # RPE extraction
│   ├── models.py          # PhysicalRPE, SemanticRPE, RPEBundle
│   ├── extractor.py       # Integrated pipeline
│   ├── physical_features.py  # librosa-based features
│   ├── semantic_rules.py  # Rule-based mapping
│   ├── structure.py       # Segment detection
│   ├── structure_labels.py    # Section labeling
│   ├── structure_novelty.py   # Novelty curve detection
│   ├── section_features.py    # Per-section feature vectors
│   └── valley.py          # Valley depth strategies
├── svp/                   # SVP generation
│   ├── models.py          # SVPBundle, MinimalSVP
│   ├── generator.py       # RPE → SVP conversion
│   ├── parser.py          # External SVP loader (compare)
│   ├── templates.py       # Template definitions
│   ├── render_yaml.py     # YAML output
│   └── render_text.py     # Markdown output
├── eval/                  # Evaluation
│   ├── models.py          # RPEScore, UGHerScore, IntegratedScore
│   ├── scorer_rpe.py      # Physical quality scoring
│   ├── scorer_ugher.py    # Semantic consistency scoring
│   ├── scorer_integrated.py  # Weighted integration
│   ├── anchor_matcher.py     # GRV anchor alignment
│   ├── comparison.py         # compare command core
│   ├── delta_e_alignment.py  # ΔE profile matching
│   ├── diff_models.py        # diff data structures
│   └── semantic_similarity.py # Token + synonym overlap
├── batch/                 # Batch processing
│   ├── runner.py          # batch command core
│   ├── discovery.py       # Input file discovery
│   └── report.py          # Report rendering
└── utils/config_loader.py # YAML config loading

|-- perform/               # Deterministic CompositionScore performer
|-- roundtrip/             # Roundtrip preservation diagnostics

config/                    # External configuration
├── pro_baseline.yaml      # Pro reference values
├── semantic_rules.yaml    # Physical → semantic rules
├── svp_templates.yaml     # SVP templates
└── synonym_map.yaml       # Synonym groups (UGHer scorer)

tests/                     # pytest
docs/                      # Design documents
examples/                  # sample_input/ + expected_output/
```

## Development

```bash
# Lint
ruff check .

# Test
pytest -q --tb=short

# CLI help
svprpe --help
```

## Documentation

- [Validation Status](docs/validation.md) — PoC label, unvalidated metrics, and required ground truth
- [Measurement Coverage](docs/coverage.md) — What the pipeline can measure, partially measure, and cannot measure
- [Architecture](docs/architecture.md) — Pipeline design and module overview
- [Metrics](docs/metrics.md) — Physical metric definitions and Pro baseline
- [Migration Notes](docs/migration.md) — RPE schema migration: SemanticRPE 1.0→2.0 (por_surface to evidence-bearing SemanticLabel), fail-fast policy, regeneration steps
- [CLI Reference](docs/cli.md) — Command usage
- [Semantic CI Product V1](docs/semantic_ci_product_v1.md) — Target SVP → Expected RPE → Diff → Repair SVP core
- [Roadmap](docs/roadmap.md) — PoC milestones (M0–M5) + Pre-prototype plan (P1–P5)
- [Goal 1 Roadmap](docs/roadmap_goal1.md) — Quantitative observation completion plan (Q0–Q5)
- [Goal 2 Roadmap](docs/roadmap_goal2.md) — Reproduction-proof completion plan (R0–R5): bidirectional reproducibility decomposed (grip × calibration × transcription), round-trip preservation across the K/Q/T/C tracks
- [Learned Models Policy](docs/learned_models_policy.md) — Adopt/reject/hold policy for learned audio-annotation models (Q4'): isolation into LearnedAudioAnnotations, no mixing into rule evidence, OSS license constraints
- [AI Music DAW Vision](docs/ai_music_daw_vision.md) — Extension track: SVP as "MIDI for AI music" standard, survivor性 framework, score-vs-performance separation, PoC (1) integration into Q0
- [Composition Score Product Brief](docs/composition_score_product_brief.md) — Composition Score product definition: three-layer composition language, canonical schema, MVP scope, PoC 1–5 roadmap
- [Composition PoC Planning](docs/composition_poc_planning.md) — Implementation plan for Composition Score: C1–C6 phases, design decisions
- [Composition PoC Report](docs/composition_poc_report.md) — C4 end-to-end demo results (deterministic path): synth performer, two-take needle comparison, sensor findings
- [Controllability PoC Planning](docs/controllability_poc.md) — Control track PoC (K-series): parameters as control knobs not eval values, grip effect-size definition, K0 minimal method-proof → K2 Suno transfer
- [Score-centric Planning](docs/score_centric_planning.md) — Score-first reorganization: bidirectional reproducibility principle, transcription track (T-series, T0–T2), Q-series redefined as instrument calibration, semantic-layer sensors as future scope
- [Round-trip Case Studies](docs/roundtrip_case_studies.md) — Real-Suno round-trip/controllability results log and R1 corpus manifest path: instrument effective-band, physical-fixed/semantic-swapped A/B, bidirectional test success (BPM caveat), BPM 89.1 attractor suspicion
- [Roundtrip Preservation](docs/roundtrip_preservation.md) - R0 deterministic score -> performance -> transcription preservation diagnostics and K1 cross-check
- [Metamorphic Probe](docs/metamorphic_probe.md) — Sweeps render_sample × real extract to auto-verify grip/calibration/orthogonality/determinism (Hypothesis): centroid=tight grip, high-band brightness=blind sensor, bpm octave error unflagged by R2-2a
- [R1 Corpus Screen](docs/roundtrip_corpus_screen.md) — Real Suno screen + A/B/C control experiment: fast tracks' low BPM is extractor halving, not Suno infidelity (start_bpm=180 recovers ~172); the "attractor" is prior × BPM-grid selection; breakbeat hypothesis falsified; R2-2a misses grid-quantized 1.93× halvings
- [AGENTS.md](AGENTS.md) — Claude × Codex orchestration protocol (Task Brief / Completion Summary templates)

- [Roundtrip Preservation](docs/roundtrip_preservation.md) - R0 deterministic score -> performance -> transcription preservation diagnostics and K1 cross-check

## License

MIT
