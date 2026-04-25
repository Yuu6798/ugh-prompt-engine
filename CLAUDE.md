# CLAUDE.md — ugh-prompt-engine (svp-rpe)

このファイルは Claude Code / Claude Agent SDK がこのリポジトリで作業する際の
普遍的な運用ポリシーをまとめる。リポジトリ固有の設計詳細は
`docs/<topic>.md` と各 `README.md` に分離する。

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

## Advisor Strategy（モデル運用方針）

- **メインエージェント**: Opus（設計判断・レビュー・品質ゲート）
- **サブエージェント**: Sonnet 固定（探索・実装・定型タスク）

Agent ツールで spawn する際は必ず `model: "sonnet"` を指定すること。

```python
# 正しい例
Agent({"model": "sonnet", "subagent_type": "Explore", "prompt": "..."})

# NG — model 省略すると Opus で動き、コスト効率が下がる
Agent({"subagent_type": "Explore", "prompt": "..."})
```

## Session Memory（永続記憶ワークフロー）

セッション間の記憶喪失を防ぐため、`.claude/memory/` にセッションサマリーを蓄積する。

### 起動時ルール

1. セッション開始時に `.claude/memory/_index.md` を読み、過去の決定事項・コンテキストを把握する
2. 直近 3 件のサマリーファイルは必要に応じて詳細を参照する
3. 過去の設計判断に関する質問には、サマリーを確認してから回答する

### 終了時ルール（自動トリガー）

ユーザーがセッション終了を示す発言をしたら、**確認なしで即座に `/wrap-up` を実行する**。

**トリガーフレーズ**（文脈付きの終了意図を検出。汎用トークン単体では発火しない）:
- 「今日はここまで」「今日は終わり」「今日はおわり」
- 「セッション終了」「セッション閉じて」
- 「また明日」「また今度」「お疲れ様」「お疲れさま」
- 「done for today」「that's all」
- 手動: `/wrap-up`

**実行内容:**
- 会話の振り返りサマリーを `.claude/memory/YYYY-MM-DD.md` に保存
- `_index.md` に 1 行サマリーを追記
- CLAUDE.md への更新候補があればユーザーに提案

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

## ドキュメント管理ポリシー

**CLAUDE.md はリポジトリ横断の普遍的内容のみ記述する (目標: 400 行以内)。**

新機能・新仕様を追加する際のドキュメント作成ルール:

1. **機能・仕様の詳細は `docs/<topic>.md` を新規作成して記述する**
   - 設計思想、計算式、パラメータ、検証結果、使用例など
   - CLAUDE.md に詳細を追加してはならない
2. **CLAUDE.md への追記は最小限に留める**
   - ファイル配置の一覧に 1 行
   - 設計ドキュメント索引表に 1 行（新 doc へのリンク）
   - それ以外の詳細は追加しない
3. **既存の task-specific 内容を見つけたら対応する `docs/` に移管する**
   - CLAUDE.md が肥大化していないか定期的に精査する

**判断基準**:
- **普遍的 (CLAUDE.md に残す)**: 開発環境、コーディング規約、git workflow、
  ファイル配置の一覧、ドキュメント索引 — どの作業者・どの機能でも参照する内容
- **task-specific (`docs/` に分離)**: 1 コンポーネントの実装詳細、1 指標の校正結果、
  1 機能の API スキーマ、1 実験の検証データ — 特定タスクの深掘り情報

## README 管理ポリシー

**README.md は入口情報に限定し、再膨張を防ぐ (目標: 300 行以内、hard limit: 350 行)。**

README の運用ルール:

1. **単一 section が 30 行を超えたら `docs/<topic>.md` へ抽出する**
   - README にはリンク + 2-3 行の要約のみ残す
2. **新規 docs を作成したら索引を 2 箇所更新する**
   - README の「設計ドキュメント」表に 1 行追加
   - CLAUDE.md の設計ドキュメント索引表に 1 行追加
3. **README と docs の責務を混ぜない**
   - README: 5 分で全体像を掴む入口情報、コンセプト図、クイックスタート、
     主要指標の一行定義、設計 docs への索引
   - docs: 仕様詳細、検証データ、1 コンポーネントの仕様詳細、
     トラブルシューティング事例、実装 recipe

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest -q --tb=short
svprpe --help
```

## Coding Conventions

### Style

- ruff 準拠 (line-length=100)
- 型ヒント必須: `Optional`, `List`, `Dict` を使用
- `from __future__ import annotations` を全モジュール先頭に記述
- docstring / コメントは日本語 OK
- float 表示は小数点 3–4 桁に丸める

### Patterns

- **Frozen dataclass / pydantic model**: 値オブジェクトは不変で定義する
- **フォールバックチェーン**: import 時に try/except でフラグ設定、実行時に分岐
- **値のクランプ**: 正規化が必要な float 値は `max(lo, min(hi, value))` で範囲内に収める
- **タイムスタンプ**: UTC, ISO 8601 形式で保存
- **Optional + confidence pattern**: 不確実性のある値は Optional + confidence で表現

### Error Handling

- 明示的な例外送出は避け、フォールバックチェーンで吸収する
- オプショナル依存の import は `try/except ModuleNotFoundError` でモジュール名を
  確認してからフラグ設定（transitive 依存エラーは fail-fast）
- リソース（DB 接続・ファイル・ネットワーク）はコンテキストマネージャで管理する

### Testing

- テストファイル: `tests/test_*.py`
- `tmp_path` でファイルシステムを分離
- ヘルパーファクトリでオブジェクト生成（モック不使用を推奨）
- `pytest.approx()` で float 比較

## Git Workflow

### Branches

- `main` — 安定版。直接 push しない（例外: `.claude/memory/` の運用ログは直接 commit 可）
- `claude/*` — Claude Code が実装する作業ブランチ
- `codex/*` — Codex が実装する作業ブランチ

### Commit Messages

- Conventional Commits 形式: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`
- 日本語メッセージ可

### Pull Request

**コード・ドキュメント変更は必ず Pull Request で実施する**。`main` への直接 push は
原則禁止（唯一の例外は Branches 節に記載した `.claude/memory/` 運用ログ）。
PR はリンク発行で作成する（`gh pr create` は使わない）。

```bash
# 1. ブランチを push
git push -u origin <branch-name>

# 2. PR リンクを提示
# https://github.com/Yuu6798/ugh-prompt-engine/compare/main...<branch-name>?expand=1
```

## CI 基本方針

- Push / PR で lint（`ruff check .`）+ test（`pytest -q --tb=short`）が通ることを必須とする
- CI 通過 = lint clean + 全テスト pass
- CI 固有のワークフロー詳細は `.github/workflows/*.yml` と `docs/` に記述する

## ugh-audit-core パターン対応

| ugh-audit-core | svp-rpe | 役割 |
|---|---|---|
| `detect()` → Evidence | `extract()` → RPEBundle | 入力からの事実抽出 |
| `calculate()` → State | `generate()` → SVPBundle | 事実 → 設計図 |
| `decide()` → verdict | `evaluate()` → scores | 評価・判定 |
| frozen dataclass | Pydantic BaseModel | 不変データ構造 |
| YAML registry | config/*.yaml | 外部化設定 |
