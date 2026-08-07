# composer_at_measurement (battery_v2)

BR-D1 v2 再測の登録時点で `compose_payload.py`（sha256 =
`b5be530096f076da18e765bbce52ea369331cf1a127a2b29c7c6df4c15c8b233`）を
凍結したコピー。squash マージで git 系譜が切れても
`sha256_at_measurement` の実体照合を可能にする保全措置であり、実行対象では
ない。現行実装は `../../scripts/compose_payload.py`。

v1 バッテリー（`../../battery/composer_at_measurement/`）とは異なり、本
再測は登録時点で `sha256_at_measurement` == `sha256_current` から開始する
（測定がまだ実行されていないため——ledger_l0br_v2.yaml
`payload_composition.composer.note` 参照）。
