# checker_at_measurement (battery_v2)

BR-D1 v2 再測の登録時点で `check_token_ban.py`（sha256 =
`98f13bc0af4c0a8c5bf9297c0397ab756bcc3acded2c91ca3ff9ca4565e53ee8`）を
凍結したコピー。squash マージで git 系譜が切れても
`sha256_at_measurement` の実体照合を可能にする保全措置であり、実行対象では
ない。現行実装は `../../scripts/check_token_ban.py`。br_d1 は
`token_ban: false` のため本再測では未使用だが、v1 台帳との dual-pin 構造
対称性のために転記する。

登録時点（2026-08-07、commit 187c57e）で `sha256_at_measurement` ==
`sha256_current` として pin し、実測（2 系列 × 3 周回、実測完了 commit
1f52f70）は完了済み——br_d1 は `token_ban: false` のため実測で本 checker が
実行されることはなかったが、本コピーは実測期間中の実装実体の保全として
そのまま有効である。以後 `../../scripts/check_token_ban.py` に正当な変更が
入った場合は `sha256_current` のみが更新され、本コピーと
`sha256_at_measurement` は不変のまま維持される（dual-pin 遷移——
ledger_l0br_v2.yaml `constraint_checker.note` 参照）。
