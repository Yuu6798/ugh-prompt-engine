# S4 run 5 GPU 実行 runbook（RunPod API 無人ブートストラップ）

設計正本: [`DESIGN_S4_run5.md`](DESIGN_S4_run5.md) §3「実行契約」。本書は手順のみを
記述し、閾値・予算上限（cap $8）・学習規模（40K）・単一介入の定義は設計側が凍結する
（本書と設計が食い違ったら設計が勝つ — `S3_RUN4_RUNBOOK.md` と同じ規律）。

run 4 との違い: **クロー非経由**（2026-08-17 User 決定事項）。SSH 対話・稼働中の
介入は一切行わない。Pod 作成時に起動スクリプトを注入し、
clone → 4 ゲート → 素材照合 → 再生成 → pin 照合 → 学習（2 フェーズ）→
Google Drive 退避 → 自動停止を `scripts/run5_bootstrap.py` が単一実行で完走する。

---

## 0. 起動前必須の先行タスク — **転記完了（2026-08-18）**

[`results_s3/run5_material_pins.json`](results_s3/run5_material_pins.json) の
PENDING 2 件は User が一次記録から回収した値で転記済み。**来歴の強度が異なる**
点に注意（同ファイル `provenance` 欄が正本）:

| 素材 | 来歴 | 転記時の独立検証（2026-08-18） |
|---|---|---|
| `ffmpeg_static`（BtbN n6.1.2・autobuild-2024-09-30-15-36） | **強** — 当方実測が起源（40/40 バイト再現検証の正本 = scratchpad ffmpeg_static_verify/RESULT.md・実体再検証済み） | URL から独立 DL し tarball sha256 / size / bin/ffmpeg sha256 / libavformat 60.16.100 の 4 点一致を再実測 |
| `vocoder_pc_nsf_hifigan`（openvpi pc-nsf-hifigan 2025.02） | **中** — クロー報告値（run 4 学習開始報告の逐語）。run 4 が本配置物で 40K 完走した間接実証のみ | URL から独立 DL し zip sha256 / model.ckpt sha256 の一致を再実測（「run 4 Pod 上の実バイト列」との同一性はクロー報告値経由の推定のまま） |

bootstrap preflight は引き続き null pin を fail-closed で拒否する（null へ戻す
退行は `tests/test_run5_bootstrap.py::test_committed_material_pins_file_is_fully_pinned`
が検出する）。なお DESIGN_S4 §3.2 の「nsf_hifigan.onnx」表記は run 4 実績の
一次記録（pc_nsf_hifigan zip + model.ckpt）と食い違っていたため、pins 表側を
正とする（学習側 vocoder = pc_nsf_hifigan ckpt / 判定材料合成のローカル ONNX
vocoder = S1 以来のローカル資産、の 2 系統が別物という整理）。

---

## 1. 事前に用意するもの（Pod 作成時に環境変数で注入）

| 環境変数 | 内容 | 注意 |
|---|---|---|
| `RUN5_PIN_COMMIT` | run 5 実行コードの pin コミット SHA（§2 のコード変更マージ後の main コミット） | プレースホルダのまま起動しない |
| `RUN5_RCLONE_CONF_B64` | rclone.conf の base64。リモート名は `run5drive` | **成果物専用フォルダに権限を限定したスコープ**（Drive 全域トークン不可 — DESIGN_S4 §3.3。Pod 側侵害時の被害面を成果物フォルダに閉じる）。作り方は §1.1 |
| `RUN5_DRIVE_FOLDER_ID` | 成果物フォルダの Google Drive フォルダ ID（URL の `folders/` 以降） | 退避先であり、user 宅録原本の入力元も兼ねる（§1.1） |
| `RUN5_USER_SOURCES_URL`（任意） | user 宅録原本アーカイブの直リンク（`uc?export=download&id=` 形式実証済み） | **省略時が既定**（2026-08-18 User 裁定・案 A）: 成果物フォルダ内 `user_sources/` から rclone 取得。この変数は代替経路としてのみ使う |

`RUNPOD_POD_ID` は RunPod が自動注入する（self-stop 用）。

### 1.1 成果物フォルダの準備（既定経路・案 A）

1. Google Drive に**成果物フォルダ**を 1 つ作る（**共有設定は非公開のまま** —
   リンク共有にしない）。URL `https://drive.google.com/drive/folders/<ID>` の
   `<ID>` が `RUN5_DRIVE_FOLDER_ID`
2. その直下に **`user_sources/`** という名前のサブフォルダを作り、
   「音楽サンプル」の原本 17 本をコピーする。**ファイル名はそのままでよい**
   （Drive のコピーが付ける「〜 のコピー」も可・重複コピー混入も可）—
   bootstrap は中身の sha256 で台帳 `source_sha256` と 17/17 照合して
   ファイルを特定する（名前非依存。台帳の `source_filename` は intake
   正規化名で Drive 表示名とは元々一致しない）。台帳のどれかの sha に
   一致するファイルが 1 本でも見つからなければ fail-closed
3. **rclone conf（トークンの権限範囲に注意）**: リモート名は `run5drive`。
   下記いずれの方式でも、bootstrap は `--drive-root-folder-id
   <RUN5_DRIVE_FOLDER_ID>` で成果物フォルダを起点に読み書きする。

   **重要（セキュリティの実態）**: `scope = drive` のトークン自体は
   「そのアカウント／SA の Drive 全域」への読み書き権を持つ。被害面を
   成果物フォルダ 1 つに閉じ込めているのは **その主体に他のフォルダを
   共有していないこと**であって、`scope` 値ではない。したがって:
   - 使う主体には**成果物フォルダ以外を共有しない**（既存アカウントの
     rclone トークンを流用しない — 全 Drive が露出する）
   - conf に `root_folder_id` を書けば、そのトークンが起点を超えて辿るのを
     防ぐ多層防御になる（`--drive-root-folder-id` フラグと二重でも無害）

   **方式 A: サービスアカウント（SA）**
   - Google Cloud Console →「API とサービス」で **Google Drive API を有効化**
   - 「IAM と管理 → サービスアカウント」で SA 作成 →「鍵」タブから **JSON キー**
   - 成果物フォルダを SA のメール（`...@....iam.gserviceaccount.com`）に
     **編集者として共有**（SA の Drive は空なので、この 1 フォルダ以外は
     存在しない = 実質のスコープ制限）
   - ※ 組織ポリシー `iam.disableServiceAccountKeyCreation` が JSON キー作成を
     ブロックする環境では方式 B を使う（2026-08-18 実地でこのブロックに遭遇）

   ```ini
   [run5drive]
   type = drive
   scope = drive
   root_folder_id = <RUN5_DRIVE_FOLDER_ID>
   service_account_credentials = <JSON キーの中身を 1 行で>
   ```

   **方式 B: 専用 Google アカウント + OAuth トークン（GCP 回避・実地採用）**
   - run 5 専用の新規 Gmail を作る（**Drive は空のまま使う** — これが被害面の
     封じ込め）。成果物フォルダをこの新アカウントに**編集者として共有**
   - 端末（PC or Termux）で `rclone authorize "drive"` →
     **必ず新アカウントでログイン**して承認 → 出力の JSON トークンを控える

   ```ini
   [run5drive]
   type = drive
   scope = drive
   root_folder_id = <RUN5_DRIVE_FOLDER_ID>
   token = <rclone authorize が出力した {"access_token":...} を 1 行で>
   ```

   いずれも base64 化して注入:

   ```bash
   base64 -w0 rclone.conf   # 出力を RUN5_RCLONE_CONF_B64 へ
   ```
   - JSON キー・トークン・conf・base64 はリポジトリ／チャット記録に残さない
     運用が望ましい（トークン失効は SA の鍵削除、または新アカウントの
     「セキュリティ → サードパーティのアクセス」で rclone を取り消して即時）

## 2. Pod 作成（RunPod REST API）

イメージ・スペックは run 4 実績（`runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`・
RTX 3090 Community $0.22/h・containerDiskInGb 60）を踏襲する。起動コマンドには
[`scripts/run5_pod_entry.sh`](scripts/run5_pod_entry.sh) 冒頭コメントの 1 行
（raw.githubusercontent の pin コミット URL から entry を取得して実行）を注入する。

予算ガード: cap **$8**（DESIGN_S4 §3.4）。スクリプト内 wall-clock 上限 24h
（$0.22/h × 24h ≈ $5.3）。スクリプト自体が死ぬ場合の課金露出は Q8 として設計受容
済み — 第 2 防衛線は §3 のポーリングから の API stop。

### 2.1 素材取得経路の注意（2026-08-18 実地）

- **PJS corpus は認証済み Drive API で取る**（`rclone backend copyid`・注入済み
  トークン）。匿名 DL（gdown / `uc?export=download`）は Google の per-file 上限に
  達すると HTML の "Quota exceeded" を返して失敗する — run 5 の二度目の起動が
  これで停止した（1 度目は上限到達前で成功しており、**再現性の無い停止**として
  現れる点に注意）。認証済み経路で sha256 一致を実測確認済み
- **user_sources は Drive の file ID 指定で 1 本ずつ取る**（`lsjson` で列挙 →
  `backend copyid`）。Drive は同一フォルダに**同じ表示名のファイルを複数**
  持てるため、名前ベースの `rclone copy` は 2 本目以降を
  `Duplicate object found in source - ignoring` として黙って落とす — run 5
  三度目の起動は 17 本中 10 本しか Pod に届かず fail-closed した。
  **注意: `rclone hashsum`（サーバ側 hash 一覧）では 17/17 見えるため、
  コピー経路を通さない事前検証ではこの穴を発見できない**
- 外部コマンドの出力は `<work>/cmdlogs/<stage>.log` に残り、失敗時は末尾が
  `failure.status.json` の detail に載る + ログ本体も salvage で Drive へ退避
  される（無人 Pod は人が入れないため、証跡はその場で残すしかない）

## 3. 監視（RunPod API ポーリング + Drive heartbeat）

- **進捗の正は Drive の heartbeat**（`<stage>.status.json` — 段階・status・UTC）。
  RunPod API は Pod 内ファイルを読めないため、API ポーリングは Pod 生死・課金時間の
  確認に限定する（DESIGN_S4 §3.1。報告文でなく成果物で完了判定する）
- ステージ順: `preflight → gates → materials → datasets → assemble → binarize →
  train_phase_a → train_phase_b → salvage → self_stop`（`run5_bootstrap.py --plan`
  で印字可能）
- 学習中は 5K 節目毎に `train_phase_*_step_<N>.status.json` + 当該 checkpoint が
  Drive へ push される（NaN 検知時は status=nan で即退避・停止）
- **異常時**: `failure.status.json` が push され、確保済み成果物を salvage push した
  うえで自己停止する。Pod が stop されずに残っている場合のみ、ポーリング側から
  `POST /pods/{podId}/stop` を打つ（第 2 防衛線）

## 4. 学習 2 フェーズ（run 3 レシピの機械化）

`run5_bootstrap.py` は assemble が生成した live config（無編集・
assembly_manifest に pin 済み）から 2 つの phase config を導出する:

1. phase A = スクラッチ 0→5K（`finetune_enabled: false`）
2. phase B = finetune 機構再適用（optimizer 新品・`finetune_ckpt_path` = phase A の
   5K checkpoint）で 0→40K

いずれも bf16-mixed / lr 0.0002 / clip_grad_norm 1.0 を付与（runbook §4 の
手動移植 4 項目の無人版。キー名が s1_record の文章記述由来で実 YAML 一次照合
未達という限界も継承 — 初回実測でキー名不一致が判明したら学習が即落ちるため
fail-closed 側に倒れる）。2 config の sha256 は `run5_training_manifest.json`
として Drive へ退避される。**キー名一次照合の限界は run 5 実走で解消済み**
（train.py の config スナップショットに同キー・同値が採録 — s4_record §4）。

### 4.1 依存の固定（lock + runtime gate の二重保証・2026-08-19 外部レビュー P3）

- 確定 pin の正本 = `scripts/requirements_run5_pod.lock`（render/gates 環境の
  数値スタック 4 点。bootstrap の `NUMERIC_STACK_PIN`〔gate1 が毎回検査〕との
  同期はテストが固定）
- 実行時解決が残る範囲（`pip install -e ".[dev]"` / praat-parselmouth /
  DiffSinger requirements）は、bootstrap が **`pip freeze` を gates 段と
  binarize 段の cmdlog として捕獲・salvage** する。run 5 は freeze 未捕獲の
  ため lock は部分 pin — **run 6 の実測 freeze から lock を完全化する**
  （lock ファイル内の PENDING 注記が解消条件）

## 5. 成果物（Drive 退避レイアウト）

2026-08-19 bootstrap 改修（外部レビュー P1/P2 採用）後のレイアウト。
**run 5 実走時はフラット配置**だった（当時の実配置と手動保全の経緯 =
s4_record §5.6）— 以下は run 6 以降に適用される:

| 区分 | Drive 上の配置 |
|---|---|
| heartbeat | 直下の `<stage>.status.json` 群（exit code 込み。`preflight_manifest_diff` = 前回 manifest との比較 info を含む） |
| 実行正本（機械可読） | 直下の `run_execution_manifest.json`（schema `run-execution-manifest/0.1`: pod/commit/環境版数/素材・データ・checkpoint・TB hash/stage_status/salvage 会計/failure_history。次回 preflight が前回走行と比較する） |
| データ束 pin | 直下の `assembly_manifest.json` / `run4_config_datasets.yaml.normalized.yaml` / `dict.txt` / `run5_training_manifest.json` |
| phase 別成果物 | `phase_a/` と `phase_b/` 配下に `checkpoints/`（節目 ckpt — milestone push・salvage とも同一規則）・`config/`（導出 config + train.py スナップショット `config.yaml`）・`logs/`（**`train.log` = 学習生 stdout/stderr**・TensorBoard events・その他ログ） |
| コマンドログ | `cmdlogs/` 配下（各ステージの外部コマンド逐語出力 + `gates_pip-freeze` / `binarize_pip-freeze` = 依存 lock 完全化の材料） |

- **同名衝突の罠（2026-08-19 実走で発見 → 同日 bootstrap 改修で根治済み）**:
  旧実装は「ファイル名のみ」で Drive ルートへ push したため、phase A/B の
  `model_ckpt_steps_5000.ckpt` と `config.yaml` が後勝ち上書きになった。
  run 5 では phase B の 5K push 前に監視側から Drive サーバーサイド copyto で
  phase A 5K を `phase_a_model_ckpt_steps_5000.ckpt` へ手動保全した（正しさは
  `run5_training_manifest.json` の `phase_a_5k_ckpt_sha256` 一致で機械検証 —
  s4_record §5.6）。現行実装は上表の phase 別 namespace により同 basename でも
  独立保存され（回帰テストで固定）、**手動保全は不要**
- 学習の生 stdout は run 5 では退避されなかった（当時の train.py は
  コンソール直結 — s4_record §2 の正直会計）。現行実装は
  `phase_*/logs/train.log` として記録・salvage し、学習失敗時は末尾が
  failure heartbeat の detail に添付される（証跡 4 系統 = checkpoint /
  TensorBoard / heartbeat / train log）

s4 record（s3_record 様式）への転記は run 5 完了後に本セッション側で行う —
**run 4 で未転記残となった 4 項目（checkpoint sha / 学習 log・TB sha /
wav 生成コマンド対応表 / 費用）は run 5 では同時転記で完了させる**
（DESIGN_S4 §5 AC）。**→ 転記済み（2026-08-19）**: 正本 =
[`results_s4/s4_record_2026-08-19.md`](results_s4/s4_record_2026-08-19.md)
（項目 3 = wav 生成コマンド対応表のみ判定材料生成待ち・様式凍結済み）。

## 6. 判定材料 ①〜④（Pod 完走後・ローカル CPU）

判定材料の合成は Pod 上では行わない（学習に vocoder は不要）。回収した
checkpoint に対しローカルで `gate_synth.py run` / `gate_synth_run4.py`
（`--speaker d3synth` で合成教師声の立ちも聴ける — DESIGN_S4 §4）/
`forge_triangle.py`（④ = VG-E1 第 0 世代）を実行する。手順は
`S3_RUN4_RUNBOOK.md` §5 と同一（checkpoint 差し替えのみ）。

## 7. 実地障害の資産化ループ（運用ルール・2026-08-19 外部レビュー採用で正式化）

GPU 実走で fail-closed（または手動回避を要する事象）が発生したら、次の
ループを**必ず一周させてから**次の run へ進む。**「手動回避だけで次 run へ
進む」ことを禁止する** — 回避は走行を救うが、根治・fixture・テストが無い
回避は同じ障害を次 run へ持ち越す。

```text
fail-closed 発生
  → 原因ログ確保（cmdlog / failure heartbeat / train.log — 証跡はその場に残る設計）
  → 根本原因修正（PR）
  → 実測 fixture 追加（実出力の逐語を fixture 化 — 例: ffmpeg -version の桁揃え出力）
  → 回帰テスト追加（同型障害の再導入を機械検出）
  → runbook 更新（罠の記録 = §2.1 / §5 の様式）
  → 次 run へ反映（pin コミット更新）
```

run 5 の実績 = このループの実例 4 件: ffmpeg 版検査（#271・逐語 fixture +
回帰テスト）/ PJS quota（#272・認証済み経路化）/ Drive 同名重複（#274・
ID fetch + 回帰テスト）/ **phase 別 namespace 分離（2026-08-19 改修 —
run 5 中の copyto 手動保全を「回避のまま終わらせず」根治 + 回帰テスト化
した事例。s4_record §5.6）**。
