# tests/discipline

セッション運用の規律ルール（CLAUDE.md § Session Memory / wrap-up skill /
AGENTS.md §8）を CI 失敗に変換する実行可能チェック群。semantic-ci-code の
`tests/discipline/` から移植・本リポジトリのフォーマットに適応済み。

- `test_status_md_phase_single_paragraph.py`: `.claude/memory/STATUS.md` の
  `## Phase` は単一の正準段落を維持する。
- `test_status_md_next_queue_no_completed.py`: 完了/マージ済み item を
  `## Next-Issue Queue` に残置してはならない（`## Recently Merged` へ移動）。
- `test_index_md_entry_compactness.py`: `.claude/memory/_index.md` の各
  エントリは 500 文字以内（詳細は dated session log へ）。
- `test_claude_md_line_cap.py`: `CLAUDE.md` は 400 行以内（always-loaded
  policy のため。reference detail は docs/ / skill にポインタ化）。
- `test_readme_line_cap.py`: `README.md` は 350 行以内（hard limit。入口情報に
  限定し、詳細は docs/README.md 索引経由の docs/*.md へ）。

各テストは実ファイル検査に加えて `fixtures/` の違反サンプルに対する
self-test を持ち、パーサ自体の劣化（違反を検出できなくなる drift）を防ぐ。

実行（wrap-up skill step 8 の pre-push gate と同一コマンド）:

```bash
python -m pytest tests/discipline/ -q
```

注: 本リポジトリの dev extras に pytest-cov は含まれないため
`--no-cov` は付けない（unrecognized argument でエラーになる）。
