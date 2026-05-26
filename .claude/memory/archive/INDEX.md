# Archive Index

30 日以上経過したセッションログの格納先。
元テキストをそのまま保持し、情報損失ゼロで移動する。

## 格納ルール

- ディレクトリ構造: `archive/YYYY-MM/YYYY-MM-DD.md`
- `_index.md` の該当エントリは 1 行要約 + アーカイブパスに短縮する
- STATUS.md の "recently merged" は最新 5 件のみ保持、溢れ分は `STATUS_MERGED_LOG.md` に移動

## 格納済みファイル

（まだアーカイブ対象なし — 最古のセッションログは 2026-04-25 で、移行は次回 wrap-up で実施）
