# ugh-prompt-engine

**Composition Score（三層楽譜）を軸に、AI 音楽生成器の制御可能性を実測する研究計器。**

## Concept

このリポジトリは AI 音楽生成器（Suno / MusicGen 等）を **"演奏者"**、
`CompositionScore`（物理層 + 意味層 + 事象層の三層構造）を **"楽譜"** とみなす。

```
楽譜 (CompositionScore) → コンパイル (prompt) → 演奏 (生成器) → 測定 (RPE 抽出) → 楽譜 (draft)
```

この往復ループを実際に回し、「どのフィールドがどの生成器で本当に効くか（grip）」
「往復させても保存されるか（roundtrip preservation）」を決定論的な計器で実測する。
狙いは独自理論ではなく、**「AI が演奏者として使える楽譜」という実用物**を作ること。
背景・思想は [Composition Score Product Brief](docs/composition_score_product_brief.md) と
[AI-Performer Score Roadmap](docs/ai_performer_score_roadmap.md) を参照。

音声 → RPE（物理特徴量 + ルールベース意味層）→ SVP（構造化プロンプト）→ 評価、という
**旧来の計測三層パイプライン**は、このループを支える計測層の基盤として現役で稼働している
（`svprpe extract` / `generate` / `evaluate` / `compare`）。API キー不要、LLM 不要、
同一入力 → 同一出力の完全決定論的パイプライン。

関連プロジェクト: [ugh-audit-core](https://github.com/Yuu6798/ugh-audit-core)

## Current Status

- **Current status: PoC**
- 楽譜ループは **Suno** と **MusicGen** の 2 生成器で「楽譜→演奏→測定→楽譜」の往復が
  閉じている（`compose` → 生成器 → `measure`/`transcribe` → `roundtrip-rep`）。
  MusicGen はローカル API のためバッチ実測が可能（n=20 まで実施済み）、
  Suno は人手 UI 生成が律速で反復数が少ない。`score-adherence` CLI は
  **決定論演奏者経路**のコンパイル保持 + 往復保存チェック（外部生成テイクの
  検査は `roundtrip-rep` が担当）
- 旧来の RPE/SVP 評価スコアは **本番の音楽品質評価器として未検証**
- 各トラックの実測範囲・既知の限界は [`docs/roadmap_goal2.md`](docs/roadmap_goal2.md)（再現実証）、
  [`docs/controllability_poc.md`](docs/controllability_poc.md)（制御性 grip）、
  [`docs/musicgen_backend.md`](docs/musicgen_backend.md)（MusicGen 実測）を参照

See [Validation Status](docs/validation.md) and [Measurement Coverage](docs/coverage.md) for the
legacy pipeline's validation boundary, measurement coverage, and required ground truth.

## Setup

```bash
pip install -e ".[dev]"
```

## Quick Start

### Score track（楽譜 → 演奏 → 測定 → 診断）

```bash
# 楽譜を外部生成器向けプロンプトへコンパイル
svprpe compose examples/composition/midnight_signal/composition_score.yaml

# (プロンプトを生成器に渡して演奏を得る — Suno UI / MusicGen ローカル推論など)

# 演奏の物理フィールドを計測 / draft スコアを起票
svprpe measure track.wav
svprpe transcribe track.wav --output draft_score.yaml

# 楽譜が宣言した tight フィールドの準拠診断（決定論演奏者経路 — 外部テイクは対象外）
svprpe score-adherence examples/composition/midnight_signal/composition_score.yaml

# 決定論演奏者での往復保存性診断 (R0) / 外部生成テイク群の反復診断 (R3)
svprpe roundtrip examples/roundtrip/synth_01_source.yaml
svprpe roundtrip-rep examples/roundtrip/synth_01_source.yaml takes_manifest.json
```

### Legacy measurement track（RPE/SVP）

```bash
# Full pipeline: extract → generate → evaluate
svprpe run track.wav --output-dir ./output

# Compare reference vs candidate
svprpe compare --reference-audio ref.wav --candidate-audio gen.wav
```

全コマンド・全オプション（`extract` / `generate` / `evaluate` / `ci-check` /
`batch` / `score-adherence` / `roundtrip-corpus` / `roundtrip-rep` /
`genre-calibrate` / `genre-audit` 等を含む）のリファレンスは
[CLI Reference](docs/cli.md) を参照。

## Project Structure

トップレベルディレクトリのみの簡略ツリー。モジュール単位の詳細は
[Architecture](docs/architecture.md) と `CLAUDE.md` の Architecture 節を参照。

```
src/svp_rpe/                # Main package (src layout)
├── cli/                    # typer CLI (svprpe command)
├── keys.py, sentinels.py   # 調ラベル一致度・共有 sentinel 値
├── io/                     # WAV/MP3 loading + optional Demucs source separation
├── rpe/                    # RPE 抽出層 (physical/semantic/structure/valley/learned)
├── melody/                 # 主旋律観測センサー M0/M1 + 比較器 M3
├── svp/                    # SVP 生成層 (RPE → SVPBundle)
├── eval/                   # 評価層 (RPE score / UGHer score / comparison)
├── compose/                # CompositionScore: models/loader/convert/fixity/renderer
├── semantic_ci/            # Target SVP → Expected RPE → Diff → Repair SVP
├── transcribe/             # 演奏音声 → CompositionScore 物理フィールド計測/起票
├── perform/                # 決定論的 CompositionScore 演奏者 (synth + performer)
├── roundtrip/              # 楽譜往復保存性診断 (R0 harness / R1 corpus / R3 repetition)
├── control/                # 制御トラック: grip 効果量 / K3 直交性行列
├── calibration/            # ジャンル/楽器語彙コーパス校正 (genre-calibrate / genre-audit)
├── arrange/                # ArrangementSpec: 決定論的 override/resolve/compile + sidecar (AR 系列)
├── recast/                 # RecastProject: 既存 sidecar への参照+実行方針ワークスペース定義
├── batch/                  # 複数ファイルの一括処理
├── config/                 # config/*.yaml のパッケージ同梱コピー
└── utils/                  # config loader・clamp・atomic write・hashing 等の共通ユーティリティ

config/                     # External configuration (src/svp_rpe/config/ と同期)
tests/                      # pytest
docs/                       # Design documents (index: docs/README.md)
examples/                   # sample_input/ + expected_output/ + composition/roundtrip/calibration fixtures
```

## Key Metrics (one-line definitions)

- **RPE score / UGHer score** — 旧来の物理/意味整合性スコア。定義は [Metrics](docs/metrics.md)
- **grip** — ある楽譜フィールドを動かすと生成器の出力が実際に動く効果量（体温計ではなくハンドル）。
  定義・実測は [Controllability PoC](docs/controllability_poc.md)
- **roundtrip preservation** — 楽譜 → 演奏 → 抽出 → draft 楽譜の往復でフィールド値が保たれるか
  の 4 値診断（`preserved` / `calibration_disagreement` / `sensor_blind` / ...）。verdict では
  なく計器。定義は [Roundtrip Preservation](docs/roundtrip_preservation.md)
- **device profile / control_profile** — 生成器ごとに「どのフィールドが tight/loose で効くか」
  を楽譜自身に持たせる自己記述。定義は [control_profile](docs/control_profile.md)

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

全 42 件の設計ドキュメント索引（カテゴリ別）は [`docs/README.md`](docs/README.md)
を参照。ここには核心 doc のみ抜粋する。

- [Architecture](docs/architecture.md) — Two-part design (measurement three-layer + score track), module overview
- [CLI Reference](docs/cli.md) — Command usage (all `svprpe` subcommands)
- [Metrics](docs/metrics.md) — Physical metric definitions and Pro baseline
- [Roadmap](docs/roadmap.md) — PoC milestones (M0–M5) + Pre-prototype plan (P1–P5)
- [Goal 1 Roadmap](docs/roadmap_goal1.md) — Quantitative observation completion plan (Q0–Q5)
- [Goal 2 Roadmap](docs/roadmap_goal2.md) — Reproduction-proof completion plan (R0–R5): bidirectional reproducibility decomposed (grip × calibration × transcription), round-trip preservation across the K/Q/T/C tracks

## License

MIT
