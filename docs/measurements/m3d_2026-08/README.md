# M3d — チューニング実測記録（2026-08-09）

設計/事前登録: 本ディレクトリの [`preregistration.md`](preregistration.md)。
本 README は preregistration.md の内容には触れず、その後に実行した実測の
機械的事実のみを記録する（M2c 流儀: `docs/measurements/m2c_2026-07/README.md` 相当）。

## ファイル

| ファイル | 内容 |
|---|---|
| `run1.json` / `run2.json` | run phase の report（repeats、2 プロセス分） |
| `run_bit_identity.json` | run1 と run2 の pair 単位 bit 一致比較 |
| `verdict_tuning.json` | evaluate phase の判定（tuning 校正・凍結提案・holdout 会計） |
| `notcomparable_diagnosis.json` | `not_comparable` の理由ヒストグラム・観測ゲートパラメータ・アノテーション由来フレーズ構造と crepe 観測系列の突合 |
| `mini_cost_calibration.json` | フル規模実行前のミニコスト校正（起動コスト測定） |
| `provisioning_record.json` | 実行環境の依存パッケージ・重み・データセット取得記録（sha256 等） |
| `run_attempts.log` | run1/run2 の実行試行ログ（打ち切り・再試行・完走までの経緯） |

## 実行環境

- リモートコンテナ: 4 CPU / 15GB RAM
- 依存パッケージ: crepe 0.0.16 / tensorflow 2.21.0 / librosa 0.11.0 / numpy 2.4.6
  （`provisioning_record.json` 参照）
- crepe model-full.h5: sha256 `b6fd2758b03a8625a16fe86cd474ff0d8f30ad9a05e4bee2244e13e98664f860`
  （`provisioning_record.json.step_a_crepe_tf.crepe_weights` 参照）
- vocadito.zip: sha256 `e0d6b99d3f9c594afe5ae5c4d7bdacebe569e53b809e90b89d1c771c4f9990e3`
  （`provisioning_record.json.step_b_vocadito` 参照）
- vocadito 40 clip pin 検証（`tests/fixtures/melody_bench/m2c_external_fixtures.yaml` 対照）:
  40 clip 全一致（mismatch 0 件、`provisioning_record.json.step_b_vocadito.m2c_pin_verification` 参照）

## 手順 1: ミニコスト校正

`mini_cost_calibration.json` で warm state（モデルロード済み）の
wall_sec / duration_sec 平均を測定し、フル規模採用可否を判断した。

- warm 実測 3 件平均: wall_sec 17.8004 s（`warm_state_summary.warm_wall_sec_mean`）
- warm realtime factor 平均: 1.9294（`warm_state_summary.warm_realtime_factor_mean`）
- 初回呼び出し（モデルロード込み）のオーバーヘッド推定: 12.8389 s
  （`first_call_model_load_overhead_sec`）

この測定に基づき、フル規模（98 pair × 2 repeats + holdout run）の実行に進んだ。

## 手順 2: run × 2

2026-08-09、`m3d_pairs_manifest.yaml`（98 pair）に対し crepe_direct 経路で
run を 2 回実行した（`--pins` 指定・`pins_preflight_verified=true`、
`run1.json` / `run2.json` の `mode=run` フィールド参照）。

- 98 pair の内訳: tuning 66 件（measured）・holdout 32 件
  （`holdout_locked_until_frozen`、比較未実施）
- run1: report `started_utc=2026-08-09T04:10:18Z` / `recorded_utc=2026-08-09T05:22:33Z`
- run2: report `started_utc=2026-08-09T05:23:35Z` / `recorded_utc=2026-08-09T06:39:39Z`
- bit 一致: `run_bit_identity.json` により、実行された比較チェーン（crepe 抽出→
  表現→整列→軸類似）の bit 一致は **tuning 66/66**。holdout 32 行は
  `holdout_locked_until_frozen` ロックマーカー（`{split, status}` の 2 キーのみ、
  抽出・比較チェーン未実行）の同一性であり、こちらも 32/32 で `identical: true`
  だが tuning の実行結果とは性質が異なる（`pair_key_set_mismatch` は run1/run2
  とも空集合。是正: Codex レビュー #255 第 2 巡 N3 — 調整前は両者を合算した
  「98/98」とのみ記載していた）

運用注記: 実行基盤のバックグラウンドタスク打ち切り（≈4200 s TTL）により
run1 は attempt1〜3 が失敗（詳細は `run_attempts.log`: attempt1 は原因不明の
kill、attempt2 は container restart、attempt3 は container restart ではないが
原因不明の kill）。attempt4 でセッション分離（`setsid` デタッチ実行）に切り替え
完走した。run2 も同方式で 1 回で完走した。完了判定は実行時ログの報告文言では
なく、atomic write された report ファイル（`m3d_run1.json` / `m3d_run2.json`）の
存在と内容で行った。

## 手順 3: evaluate → verdict_tuning.json

`verdict_tuning.json` の `freeze_proposal_rejected_reason` は
`rejected_positive_not_comparable`。

- real（vocadito 実声）tuning: positive 48 件中 `not_comparable` 46 件、
  negative 12 件中 `not_comparable` 11 件
  （`material_accounting.real_voice.not_comparable_positive_count` /
  `not_comparable_negative_count`）
- `freeze_proposal` は空（`{}`）
- `holdout_locked_until_frozen: true`（holdout 32 行は未消費のままロック維持）

`margin_table` の数値（contour margin 0.2125・interval margin 0.1727・
rhythm margin 0.1078 等）は、real tuning のうち `not_comparable` を除いた
**measured 3 対**（tuning_pair_count 60 − not_comparable_positive 46 −
not_comparable_negative 11 = 3、内訳 positive 2 / negative 1）由来である。

## not_comparable の支配的理由

`notcomparable_diagnosis.json` の `1_reason_histogram` により、
`observation_gate_insufficient`（detail: `phrase_count 1 < min 2`）が
最多理由（side a 45 件・side b 48 件、うち一部は `note_count 5 < min 8` も
併記）。次点は `evidence_thresholds_uncalibrated`（4 件）・
`insufficient_overlap`（2 件、detail: `min_fraction=0.0000, floor=0.5000`）。

ゲートパラメータ（`2_gate_parameters`、M1 registry 凍結値
`tests/fixtures/melody_bench/registry.yaml` 参照）: `phrase_gap_sec=0.6` /
`min_phrase_count=2`。`phrase_count` の定義: notes を start_sec でソートし、
直前ノートの end_sec との間隔が `phrase_gap_sec` を超えたら新規フレーズ
開始（`observability.py:316-325 _phrase_count()`）。

`notcomparable_diagnosis.json` はアノテーション由来のフレーズ構造
（`3_annotation_phrase_structure_table`）と crepe 観測系列（`4_crepe_observed_series_in_report`）
の突合を含み、`min_phrase_count=2` ゲートに対して vocadito クリップの
観測フレーズ数が構造的に不足していることを示す。

## 手順 4（凍結）・手順 5（holdout）: 未実施

凍結提案が `rejected_positive_not_comparable` で拒否されたため、手順 4
（閾値凍結）は機械的に実行不可能であり、手順 5（holdout 一度きり検証）も
未実施。`holdout_locked_until_frozen: true` によりロックは維持されている
（一度きり検証権は未消費）。判定・後続方針は User 決裁待ち。

## v2 スクリーニング（2026-08-09）

規則 commit `aac2bd9`（v2 再事前登録: 観測ゲート由来の素材スクリーニング規則）
に基づき、S1/S2 スクリーニングを実測した（記録 = `screening_v2.json`）。

**S1（観測ゲート）**: 40 clip を実測し `sufficient` 13・`insufficient` 27。
`insufficient` 27 件は全件 `phrase_count` 単独理由（`insufficient_reason_histogram:
{"phrase_count": 27}`）。

**S2（変形別脱落）**: 4 変形それぞれの脱落数は `pitch_+3st` 7・`pitch_-5st` 6・
`time_x0.87` 6・`time_x1.12` 7。

**survivor**: 全変形込みで通過したのは N=3（`vocadito_1` / `vocadito_8` /
`vocadito_18`）のみ。

**分割式・停止条件**: `select_clips_v2`（N<18 規則）により survivor N=3 から
tuning=2・holdout=1 が機械的に決まるが、prereg_v2 §3 の停止条件
（tuning>=6 かつ holdout>=3）に tuning・holdout の双方が抵触し、
`scripts/build_m3d_pairs.py` が fail-closed で例外を送出することを実確認した
（部分出力なし）。

**未達事項**: manifest v2 は未生成、run×2（tuning 実測 + holdout 一度きり検証）
は未実施、v1 の holdout 一度きり検証権も未消費のまま。判定は User 決裁待ち。
