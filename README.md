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

# Print to stdout
svprpe run track.wav --no-save

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
│   └── structure.py       # Segment detection
├── svp/                   # SVP generation
│   ├── models.py          # SVPBundle, MinimalSVP
│   ├── generator.py       # RPE → SVP conversion
│   ├── render_yaml.py     # YAML output
│   └── render_text.py     # Markdown output
├── eval/                  # Evaluation
│   ├── models.py          # RPEScore, UGHerScore, IntegratedScore
│   ├── scorer_rpe.py      # Physical quality scoring
│   ├── scorer_ugher.py    # Semantic consistency scoring
│   └── scorer_integrated.py  # Weighted integration
└── utils/config_loader.py # YAML config loading

config/                    # External configuration
├── pro_baseline.yaml      # Pro reference values
├── semantic_rules.yaml    # Physical → semantic rules
└── svp_templates.yaml     # SVP templates

tests/                     # pytest (49 tests)
docs/                      # Design documents
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

## License

MIT
