# ugh-prompt-engine

SVP (Semantic Vector Prompt) + RPE (Reverse Prompt Engineering) — UGHer ecosystem prompt infrastructure.

## Overview

音楽ファイル（WAV/MP3）から RPE を抽出し、決定論的に SVP を生成し、
UGHer 系 + RPE 系の二系統評価を行うローカル完結型ツール。

- **RPE**: 音声波形から物理特徴量 + ルールベース意味層を抽出
- **SVP**: RPE から構造化プロンプトを決定論的に生成
- **Eval**: Pro 基準値 (RPE) + 意味的整合性 (UGHer) の統合スコアリング

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

- [Architecture](docs/architecture.md) — Pipeline design and module overview
- [Metrics](docs/metrics.md) — Physical metric definitions and Pro baseline
- [CLI Reference](docs/cli.md) — Command usage
- [Roadmap](docs/roadmap.md) — PoC milestones (M0–M5) + Pre-prototype plan (P1–P5)

## License

MIT
