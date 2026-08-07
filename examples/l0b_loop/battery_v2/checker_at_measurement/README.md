# checker_at_measurement (battery_v2)

BR-D1 v2 再測の登録時点で `check_token_ban.py`（sha256 =
`98f13bc0af4c0a8c5bf9297c0397ab756bcc3acded2c91ca3ff9ca4565e53ee8`）を
凍結したコピー。squash マージで git 系譜が切れても
`sha256_at_measurement` の実体照合を可能にする保全措置であり、実行対象では
ない。現行実装は `../../scripts/check_token_ban.py`。br_d1 は
`token_ban: false` のため本再測では未使用だが、v1 台帳との dual-pin 構造
対称性のために転記する。

v1 バッテリー（`../../battery/checker_at_measurement/`）とは異なり、本
再測は登録時点で `sha256_at_measurement` == `sha256_current` から開始する
（測定がまだ実行されていないため——ledger_l0br_v2.yaml
`constraint_checker.note` 参照）。
