# ugh-prompt-engine

SVP (Semantic Vector Prompt) + RPE (Reverse Prompt Engineering) — UGHer ecosystem prompt infrastructure.

## Current Status

- **Current status: PoC**
- **Not yet validated as production music quality evaluator**
- Deterministic local pipeline, but score validity requires a validation dataset.

See [Validation Status](docs/validation.md) for the current validation boundary and required ground truth.

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
- [Architecture](docs/architecture.md) — Pipeline design and module overview
- [Metrics](docs/metrics.md) — Physical metric definitions and Pro baseline
- [CLI Reference](docs/cli.md) — Command usage
- [Semantic CI Product V1](docs/semantic_ci_product_v1.md) — Target SVP → Expected RPE → Diff → Repair SVP core
- [Roadmap](docs/roadmap.md) — PoC milestones (M0–M5) + Pre-prototype plan (P1–P5)
- [Goal 1 Roadmap](docs/roadmap_goal1.md) — Quantitative observation completion plan (Q0–Q5)
- [Code Semantic CI Design](docs/code_semantic_ci_design.md) — Code Edition v0.1 spec (3-state RPE, typed constraints, Python MVP plan)
- [AGENTS.md](AGENTS.md) — Claude × Codex orchestration protocol (Task Brief / Completion Summary templates)

## License

MIT
