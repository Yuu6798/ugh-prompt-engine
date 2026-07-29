# M2c — V 帯実測記録（vocadito V-direct・2026-07-29）

設計: [`docs/DESIGN_M2_extraction_accuracy.md`](../../DESIGN_M2_extraction_accuracy.md) §5 M2c（および V 帯ネイティブタイムライン評価の追記 §2）。
ハーネス: `scripts/run_melody_accuracy.py`（run/evaluate 二相）。

本ディレクトリは **M2c-2**（V_direct・vocadito 40 clip・実声・分離なし）の
確定実測 dated 記録である。事前登録した外部素材 pin（`m2c_external_fixtures.yaml`,
commit `1cbd448`）に対し、その後に実測を実行した。

## ファイル

| ファイル | 内容 |
|---|---|
| `run1.json` / `run2.json` | run phase の report（repeats n=2） |
| `verdict.json` | evaluate phase の判定（凍結 bars の機械適用 + 測り直し検証） |
| `run1_stdout.txt` / `run2_stdout.txt` / `evaluate_stdout.txt` | 実行時 stdout/stderr（TF import 時警告を含む・無編集） |

**命名が M2b（`m2b_run1.json` 等の `m2b_` prefix）と異なる理由**: `verdict.json` の
`report_pins[].path_name` が実行時点の出力ファイル名 `run1.json` / `run2.json` を
そのまま指しており、これは report の bytes ではなく `path_name` 文字列も込みで
pin されている。commit 時にリネームすると `path_name` が指す先が存在しなくなり
pin 整合が壊れるため、本記録では**リネームせず** run 実行時のファイル名のまま
配置する。

pin 整合は `tests/test_m2c_committed_record.py` が CI で強制する
（report_pins の sha256 照合・凍結 bars/external fixtures の digest 照合・判定固定）。

## ライセンス関門の記録

- **データセット**: vocadito（Zenodo record 5578807）
- **API 応答原文**（`GET https://zenodo.org/api/records/5578807`、確認日
  2026-07-29）: `"license": {"id": "cc-by-4.0"}`, `"access_right": "open"`
- **DOI**: 10.5281/zenodo.5578807
- **vocadito.zip** md5 = `dea40fd18f14d899643c4ba221b33a46`（Zenodo 掲載値と照合済み）、
  sha256 = `e0d6b99d3f9c594afe5ae5c4d7bdacebe569e53b809e90b89d1c771c4f9990e3`
- アーカイブ内に単体 LICENSE ファイルは同梱されず、ライセンスは Zenodo deposit
  メタデータのみで宣言される。`$V/license_readme.txt`（実行時に保存した引用原本）
  より要点転記:
  - `Annotations/README.txt` 全文: F0 アノテーション（timestamp, f0 Hz・0.0=無声）、
    歌詞（楽節ごとの空白区切りテキスト）、ノートアノテーション（開始時刻・音高 Hz・
    継続時間、annotator A1/A2 の 2 系列）の列定義
  - creators: Bittner, Pasalo, Bosch, Meseguer Brocal, Rubinstein（Spotify / IRCAM）

**波形・注釈は commit しない**。外部配置 + `tests/fixtures/melody_bench/m2c_external_fixtures.yaml`
への 40 clip audio/annotation sha256 の事前登録（commit `1cbd448`）をもって
「実測前凍結」を担保し、実測はその事前登録の**後**に実行した。

## 判定（`m2_accuracy_bars.yaml` 凍結バーの機械適用）

| 帯 | RPA | RCA | octave_gap | median cent | VR | VFA | 判定 |
|---|---|---|---|---|---|---|---|
| V_direct (`crepe_direct`) | **0.9886** | 0.9901 | **0.0015** | 3.84 | 0.998 | 0.44 | **pass**（RPA ≥ 0.80・octave_gap ≤ 0.05） |

- V_direct に VFA バーは設定されていない（M2b S-direct のみが対象）。
- 各 run 内 repeats n=2 は bit 一致（`repeats_bit_identical: true`）。
- 40 clip 集計値（上表）は per-clip 40 件の重み付き集計（フレーム数加重）。

## per-clip 分布（40 clips・M2d 誤差モデルの一次入力）

| 指標 | min | median | max | バー超過 |
|---|---|---|---|---|
| RPA (raw_pitch_accuracy) | 0.9507 | 0.9915 | 0.9997 | 0 件（バー 0.80） |
| octave_gap | 0.0000 | 0.0000 | 0.0254 | 0 件（バー 0.05） |
| median cent error | 2.33 | 3.65 | 6.91 | （バーなし・参考値） |
| VFA (voicing_false_alarm) | 0.1344 | 0.4413 | 0.7558 | （バーなし・**分散の主軸**） |

- 最低 RPA clip: `vocadito_23` (0.9507), `vocadito_2` (0.9619), `vocadito_21` (0.9620)
- 40 clip 中バー割れ（RPA/octave_gap の個別超過）は 0 件で、集計値の pass 判定は
  個々の clip でも一貫して支持される。

## 計器知見

「音高はほぼ完璧・voicing 抑制が弱点」という M2b S-direct の特性（VFA 0.259 が
単独超過因子）が、実声（vocadito・分離なし）でも再現された。実声集計 VFA
中央値 0.44 は S-direct の合成音声（0.259）より更に高く、voicing は
`crepe_direct` 経路の系統的弱点軸であることが実声側でも確認された。M2d 誤差
モデルの中心テーマとして持ち越す。

## V_fullstack: 未測定

M2c-2 では V_fullstack（vocals 分離込み経路）は実行していない。想定素材
MedleyDB はアクセス申請ゲートがあり User 律速のため、設計 §3
「確保できない帯は未測定と記録する」の方針に従い、本記録では正直に
**未測定**として扱う（V_direct のみの記録）。

## 注釈のタイムライン評価について

vocadito の F0 アノテーションはネイティブ（元 hop）タイムラインのまま評価した
（リサンプルなし）。実測 hop は 256/44100 ≈ 5.805ms（設計 §2 の M2c 追記を参照）。

## 実行環境・provenance（要約。全 pin は JSON 本体を参照）

- 実行環境は M2b 記録と同一: crepe 0.0.16 / mir_eval 0.8.2 / CPU 4 コア・GPU なし
  （`numeric_runtime_config.cpu_count` / `sched_affinity_count` = 4）。
- 実行時間: run1 ~24 分（23m56s）、run2 ~24 分（23m36s）、evaluate（測り直し 2 回込み）
  47m6s。
- `external_fixtures_sha256` = `91b08852dabe3584de289c5ad5d9aafd7a40c8d3c2e14b2dbd8f599acc03b92f`
  （`tests/fixtures/melody_bench/m2c_external_fixtures.yaml` の bytes と一致・
  commit `1cbd448` で事前登録済み）。
- `external_manifest_sha256` = `9afb5f4e030a0ffd11f113247177690c7e225c054fd0acf5c0d0765b08a7151c`
  （run 実行時に渡した `--external-manifest`、40 clip の audio/annotation パス列挙。
  マニフェスト自体は外部素材参照のため本ディレクトリには commit しない）。

pin 整合は `tests/test_m2c_committed_record.py` が CI で強制する
（verdict bytes の凍結 digest・report_pins ↔ committed run1.json/run2.json の
sha256 照合・凍結 external fixtures/bars の digest 照合・判定固定・
external_manifest_sha256 の相互一致）。

## マージ方式の要件（事前登録 ancestry の保存）

本記録の事前登録証跡は「fixtures 登録 commit `1cbd448` が実測記録 commit より先に
HEAD ancestry に存在する」という **2 commit 構造**に依存する（verdict の
`registration_attestation.first_commit` がこれを指す）。**本 PR を squash マージすると
この構造が崩れ、attestation の主張が main 履歴上で検証不能になる**ため、マージは
merge commit 方式（リポジトリ従来方式）で行うこと。検証コマンド:
`git merge-base --is-ancestor 1cbd448 <merge後のHEAD>`（exit 0 が正）。
squash された場合は計器の再検証（evaluate の git 履歴 attestation）が fail-closed で
検出する — 静かに受理されることはない。
