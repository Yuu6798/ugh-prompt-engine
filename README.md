# ugh-prompt-engine

SVP (Semantic Vector Prompt) + RPE (Reverse Prompt Engineering) — UGHer ecosystem prompt infrastructure.

## Overview

UGHer エコシステムのプロンプト基盤。AI 回答の意味的誠実性を監査する [ugh-audit-core](https://github.com/Yuu6798/ugh-audit-core) と連携し、プロンプト構造化 (SVP) と逆プロンプト工学 (RPE) を提供する。

### SVP (Semantic Vector Prompt)

プロンプトを意味ベクトル空間で構造化し、応答の意味的制御を可能にする。

### RPE (Reverse Prompt Engineering)

モデル出力からプロンプト構造を逆推定し、意味的対応関係を分析する。

## Setup

```bash
pip install -e ".[dev]"
```

## Development

```bash
# Lint
ruff check .

# Test
pytest -q --tb=short
```

## License

MIT
