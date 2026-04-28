# Task Brief: Q0-2 — synth サンプル 5 曲の expected_output 生成

## Phase
roadmap_goal1.md Q0-2

## Goal
`examples/sample_input/` の synth WAV 5 曲（PR #9 で追加済）から `svprpe run` を
実行し、各曲の RPE / SVP / evaluation 結果を `examples/expected_output/<song_id>/`
に保存する。再生成スクリプトと SHA-256 ハッシュ一覧を併置し、Q0-3 の snapshot
test 基盤を整える。

## Acceptance Criteria
- [ ] `examples/expected_output/<song_id>/` ディレクトリが 5 曲分存在
- [ ] 各ディレクトリに `rpe.json` / `svp.yaml` / `evaluation.json` の 3 ファイルが揃う
- [ ] `examples/expected_output/hashes.txt` に各ファイルの SHA-256 が列挙されている
  （形式: `<sha256>  <relative_path>` で `sha256sum --check` 互換）
- [ ] `scripts/regenerate_expected.py` が手動実行で全 expected_output を再生成し、
  hashes.txt と完全一致する（決定論性の検証）
- [ ] 同スクリプトに `--check` フラグがあり、現状ファイルと再生成結果の hash 比較で
  pass/fail を返す（exit code 0/1）
- [ ] `examples/expected_output/README.md` で構造と再生成手順を 1 画面で説明
- [ ] 既存テスト 110 件が引き続き pass

## Scope
- IN:
  - `examples/expected_output/**/*.{json,yaml,txt,md}` (新規)
  - `scripts/regenerate_expected.py` (新規)
- OUT:
  - `src/svp_rpe/**` （実装変更が必要になったら escalation）
  - `examples/sample_input/**` （PR #9 の成果物に手を入れない）
  - `pyproject.toml`（依存追加禁止）

## Allowed Dependencies
なし（標準ライブラリ + 既存依存のみ）

## Implementation Hints
- 既存の `svprpe run <audio> --output-dir <dir>` が
  `rpe.json` / `svp.yaml` / `evaluation.json` を生成する
  （`src/svp_rpe/cli.py:200` 参照）
- ground_truth.yaml の `id` フィールドをディレクトリ名に流用する
  （5 曲: `synth_01_slow_pad_c_major` 〜 `synth_05_fast_bright_d_major`）
- `scripts/regenerate_expected.py` は subprocess ではなく、
  `extract_rpe_from_file` / `generate_svp` / `score_*` を直接呼ぶ実装が
  決定論性デバッグ時に追跡しやすい
- `--check` モードでは hashes.txt を読んで SHA-256 を再計算 → 不一致を diff 表示
- pipeline は `--valley-method hybrid`（CLI default）固定で生成
- expected_output が決定論的でない（実行ごとに hash が変わる）場合は escalation
  → 原因を特定して報告（タイムスタンプ混入 / 浮動小数点の非決定性 / dict 順 等）

## Required Outputs
- ブランチ名: `codex/q0-2-expected-output`
- PR タイトル: `feat(examples): add expected_output reference for synth samples`
- 期待する変更ファイル:
  - `examples/expected_output/synth_*/rpe.json`
  - `examples/expected_output/synth_*/svp.yaml`
  - `examples/expected_output/synth_*/evaluation.json`
  - `examples/expected_output/hashes.txt`
  - `examples/expected_output/README.md`
  - `scripts/regenerate_expected.py`
- 必須テスト: 本 Brief では追加不要（Q0-3 で snapshot test を別 Brief 化）

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- PR 本文が Completion Summary 規約に準拠し、5 曲 × 3 ファイル = 15 個の
  SHA-256 を抜粋掲載（最低 3 個、典型例として）
- `python scripts/regenerate_expected.py --check` がローカルで pass することを
  Completion Summary に明記
