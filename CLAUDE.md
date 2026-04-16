# CLAUDE.md — ugh-prompt-engine (svp-rpe)

## Project Overview

UGHer エコシステムのプロンプト基盤。
音楽ファイル（WAV/MP3）から RPE を抽出し、決定論的に SVP を生成し、
UGHer 系 + RPE 系の二系統評価を行うローカル完結型ツール。

- **RPE (Reverse Prompt Engineering)**: 音声 → 物理特徴量 + 意味層
- **SVP (Semantic Vector Prompt)**: RPE → 構造化プロンプト生成
- **Eval**: UGHer 系 + RPE 系スコアリング

API キー不要、LLM 不要、同一入力 → 同一出力の完全決定論的パイプライン。

関連: [ugh-audit-core](https://github.com/Yuu6798/ugh-audit-core)
実装プラン: [svp_rpe_implementation_plan.md](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/svp_rpe_implementation_plan.md)

## Tech Stack

- **Language**: Python 3.11+
- **Build**: setuptools (pyproject.toml, src layout)
- **Lint**: ruff (line-length=100, target py311)
- **Test**: pytest
- **CI**: GitHub Actions (Python 3.10/3.11/3.12)
- **Audio**: librosa + soundfile
- **Models**: Pydantic v2
- **CLI**: typer + rich
- **Config**: YAML (PyYAML)
- **License**: MIT

## Architecture

```
src/svp_rpe/
├── __init__.py
├── cli.py                 # typer CLI (svprpe command)
├── io/
│   ├── __init__.py
│   └── audio_loader.py    # WAV/MP3 loading + AudioMetadata
├── rpe/
│   ├── __init__.py
│   ├── models.py          # PhysicalRPE, SemanticRPE, RPEBundle
│   ├── extractor.py       # RPE integrated pipeline
│   ├── physical_features.py  # librosa-based features
│   ├── semantic_rules.py  # rule-based semantic generation
│   └── structure.py       # segment division
├── svp/
│   ├── __init__.py
│   ├── models.py          # SVPBundle, MinimalSVP
│   ├── generator.py       # RPE → SVP conversion
│   ├── templates.py       # template definitions
│   ├── render_yaml.py     # YAML output
│   └── render_text.py     # Markdown/TXT output
├── eval/
│   ├── __init__.py
│   ├── models.py          # RPEScore, UGHerScore, IntegratedScore
│   ├── scorer_rpe.py      # RPE physical scoring
│   ├── scorer_ugher.py    # UGHer semantic scoring
│   └── scorer_integrated.py  # weighted integration
└── utils/
    ├── __init__.py
    └── config_loader.py   # YAML config loading

config/
├── pro_baseline.yaml      # RPE Pro baseline values
├── semantic_rules.yaml    # physical → semantic mapping rules
└── svp_templates.yaml     # SVP generation templates

tests/                     # pytest
docs/                      # design documents
```

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest -q --tb=short
svprpe --help
```

## Coding Conventions

- ruff 準拠 (line-length=100)
- 型ヒント必須
- `from __future__ import annotations` を全モジュール先頭に記述
- Pydantic BaseModel for data structures (frozen-like via schema_version)
- 値クランプ: `max(0.0, min(1.0, value))` で [0, 1] 正規化
- Optional + confidence pattern for uncertain values

## Git Workflow

- `main` — 安定版。直接 push 禁止（PR 必須）
- `claude/*` — 作業ブランチ
- Conventional Commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`

### Pull Request 必須ルール

**リモートへの変更反映は必ず PR を経由すること。**
main への直接 push は禁止。作業ブランチで commit → push → PR 作成 → マージの流れを守る。

```bash
# 1. 作業ブランチで開発
git checkout -b claude/<topic>

# 2. commit + push
git push -u origin claude/<topic>

# 3. PR 作成
gh pr create --title "..." --body "..."
```

## ugh-audit-core パターン対応

| ugh-audit-core | svp-rpe | 役割 |
|---|---|---|
| `detect()` → Evidence | `extract()` → RPEBundle | 入力からの事実抽出 |
| `calculate()` → State | `generate()` → SVPBundle | 事実 → 設計図 |
| `decide()` → verdict | `evaluate()` → scores | 評価・判定 |
| frozen dataclass | Pydantic BaseModel | 不変データ構造 |
| YAML registry | config/*.yaml | 外部化設定 |
