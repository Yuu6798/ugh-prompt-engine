# attempt1（fail-closed）— dated 失敗記録

初回実測（2026-07-25、checkout `a9dd478` / generator `b7a133dc…`）の記録。demucs 分離の
非決定論（`shifts` 既定 1）により `stem_sha256` が run 間で不一致となり、評価器が
fail-closed で verdict を出さず停止した。原因は PR #221（`shifts=0` 決定論化）で修正され、
親ディレクトリの再実測（verdict: go）に置き換えられた。

**このディレクトリのファイルは当時の記録そのままで、一切編集していない**（記録の
手編集禁止の規律による）。そのため:

- `evaluate_go_bar_fail_closed.txt` 内のコマンドはアーカイブ前のパス
  （`docs/measurements/m1real_2026-07/m1real_run1.json` = 当時この位置にあった本
  ディレクトリの run）を指す。現在そのパスには再実測の run があるため、コマンドを
  そのままコピーしても本ファイルの失敗は再現しない
- 再現には当時の checkout が必要: `git checkout a9dd478` 上で、本ディレクトリの
  run×2 を当時のパスに置いて評価器を実行する（現行評価器は旧 generator digest
  `b7a133dc…` を stale として弾くため、現 checkout では再現不能）
- 差分の生データは `route_provenance_diff.txt`（read-only 生成）
