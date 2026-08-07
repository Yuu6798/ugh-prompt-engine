# composer_at_measurement (battery_v2)

BR-D1 v2 再測の登録時点で `compose_payload.py`（sha256 =
`b5be530096f076da18e765bbce52ea369331cf1a127a2b29c7c6df4c15c8b233`）を
凍結したコピー。squash マージで git 系譜が切れても
`sha256_at_measurement` の実体照合を可能にする保全措置であり、実行対象では
ない。現行実装は `../../scripts/compose_payload.py`。

登録時点（2026-08-07、commit 187c57e）で `sha256_at_measurement` ==
`sha256_current` として pin し、実測（2 系列 × 3 周回、実測完了 commit
1f52f70）は**この凍結実体と同一 sha の composer で全周回を実行した**——
本コピーは事前登録スナップショットであると同時に、実測時に実行された
実装そのものの保全である（全周回の payload 再組成拘束テスト
`test_ledger_l0br_v2_series_runs_payloads_recompose_via_frozen_measurement_composer`
が本コピーを通して enforce）。以後 `../../scripts/compose_payload.py` に
正当な変更が入った場合は `sha256_current` のみが更新され、本コピーと
`sha256_at_measurement` は不変のまま維持される（dual-pin 遷移——
ledger_l0br_v2.yaml `payload_composition.composer.note` 参照）。
