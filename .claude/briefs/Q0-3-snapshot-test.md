# Task Brief: Q0-3 — expected_output の snapshot テスト

## Phase
roadmap_goal1.md Q0-3

## Prerequisite
Q0-2（expected_output 生成 + hashes.txt 整備）がマージ済であること。
Q0-2 がマージ前なら、本 Brief は着手せず保留。

## Goal
Q0-2 で生成した `examples/expected_output/<song_id>/{rpe.json,svp.yaml,evaluation.json}`
を CI で snapshot 検証する pytest を追加する。決定論性が壊れた場合に CI が
即座に検出し、再生成手順を診断メッセージで提示する。

## Acceptance Criteria
- [ ] `tests/test_snapshot.py` が存在し、`pytest -q tests/test_snapshot.py` で pass
- [ ] 5 曲 × 3 ファイル = 15 件の SHA-256 比較が個別 test として走る
  （`pytest.parametrize` で song_id × file_type を展開）
- [ ] hash 不一致時のエラーメッセージに以下が含まれる:
  - 不一致のファイルパス
  - 期待される SHA-256（hashes.txt から）
  - 現状の SHA-256
  - 「`python scripts/regenerate_expected.py` で再生成し、差分が意図したものか
    確認してください」という案内
- [ ] CI 実行時間の増分が 30 秒以内（5 曲 × `extract` + `generate` + `evaluate`
  3 段で実測）
- [ ] 本 snapshot test が `examples/sample_input/` の WAV ファイル不在時に
  skip ではなく fail（明示的なエラーメッセージで欠落を通知）
- [ ] 既存テスト + snapshot で合計件数が 110 + 15 = 125 件（前後）

## Scope
- IN:
  - `tests/test_snapshot.py`（新規）
  - 必要なら `tests/conftest.py` への fixture 追加
- OUT:
  - `src/svp_rpe/**`（実装変更が必要になったら escalation）
  - `examples/expected_output/**`（Q0-2 成果物に手を入れない）
  - `scripts/regenerate_expected.py`（Q0-2 成果物に手を入れない）

## Allowed Dependencies
なし（pytest + 標準ライブラリ + 既存依存のみ）

## Implementation Hints
- ground_truth.yaml の `id` リストから song_id を取得し、parametrize
- 各 test の中で `extract_rpe_from_file` → `generate_svp` → `score_*` を直接呼び
  in-memory で生成 → JSON/YAML 文字列化 → SHA-256 算出 → hashes.txt と比較
- ファイルから読み込む実装でも可だが、Q0-2 の `scripts/regenerate_expected.py`
  と同じ生成パスを呼ぶことで重複検証になる
- YAML の dict 順や JSON の indent / 末尾改行が hash に影響するため、
  Q0-2 のシリアライズ手順を完全に再現すること（`scripts/regenerate_expected.py`
  からヘルパーを import する形が最も安全）
- パフォーマンス対策: 5 曲一括実行 fixture（`session` scope）でも可

## Required Outputs
- ブランチ名: `codex/q0-3-snapshot-test`
- PR タイトル: `test(snapshot): add hash-based snapshot tests for synth expected_output`
- 期待する変更ファイル:
  - `tests/test_snapshot.py`
  - `tests/conftest.py`（必要なら）
- 必須テスト: snapshot test 自体（15 件）

## Done When
- 上記 Acceptance Criteria が全て ✓
- CI green（`ruff check .` + `pytest -q`）
- Completion Summary に CI 実行時間の増分（実測）と pass 件数（15 件）を明記
- 不一致時の診断メッセージのサンプル出力を Completion Summary に貼付
