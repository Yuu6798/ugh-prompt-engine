# ledger/

VG-E0 genome 台帳の実配置先（`DESIGN_VG_E0.md` §6）。1 個体 1 JSON ファイル・
ファイル名 = `<genome_id>.json`。`ledger.Ledger` / `bootstrap.run_bootstrap()`
が書き出す。

実台帳（創始個体・以降の世代の JSON）はこのディレクトリには**コミットしない**
（run4 checkpoint 確定・`anchors_provenance` 記入前の座標定義のみの仮個体を
恒久 git 履歴に残さないため）。ローカルでの動作確認・ブートストラップ実行は
scratchpad 等の作業ディレクトリを `--ledger-dir` に指定して行う。
