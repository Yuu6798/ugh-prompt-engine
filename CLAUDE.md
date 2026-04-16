# CLAUDE.md — ugh-prompt-engine

## Project Overview

UGHer エコシステムのプロンプト基盤。
SVP (Semantic Vector Prompt) と RPE (Reverse Prompt Engineering) を統合し、
AI 回答の意味的誠実性を支えるプロンプト構造化・逆推定を提供する。

- **SVP**: プロンプトを意味ベクトル空間で構造化
- **RPE**: モデル出力からプロンプト構造を逆推定

関連プロジェクト: [ugh-audit-core](https://github.com/Yuu6798/ugh-audit-core)

## Tech Stack

- **Language**: Python 3.10+
- **Build**: setuptools (pyproject.toml)
- **Lint**: ruff (line-length=100, target py310)
- **Test**: pytest
- **CI**: GitHub Actions (Python 3.10/3.11/3.12)
- **License**: MIT

## Architecture

```
svp/                  # Semantic Vector Prompt
├── __init__.py
├── encoder.py        # prompt → semantic vector encoding
└── decoder.py        # semantic vector → structured prompt

rpe/                  # Reverse Prompt Engineering
├── __init__.py
├── extractor.py      # output → prompt structure extraction
└── analyzer.py       # prompt-output correspondence analysis

tests/                # pytest
docs/                 # design documents
```

## Commands

```bash
# Lint
ruff check .

# Lint with auto-fix
ruff check --fix .

# Test
pytest -q --tb=short
```

## Coding Conventions

- ruff 準拠 (line-length=100)
- 型ヒント必須
- `from __future__ import annotations` を全モジュール先頭に記述
- docstring / コメントは日本語 OK
- float 表示は小数点 3-4 桁に丸める

## Git Workflow

- `main` — 安定版
- `claude/*` — 作業ブランチ
- Conventional Commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
