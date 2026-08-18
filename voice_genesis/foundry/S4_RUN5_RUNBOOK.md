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
3. **rclone conf（サービスアカウント方式・推奨）**: 無人 Pod からの
   読み書きにはサービスアカウント（SA）が最も簡単で、アクセス範囲が
   「SA に共有したフォルダだけ」に自然に閉じる:
   - Google Cloud Console → 新規プロジェクト（既存でも可）→
     「API とサービス」で **Google Drive API を有効化**
   - 「IAM と管理 → サービスアカウント」で SA を作成 → 「鍵」タブから
     **JSON キー**を作成しダウンロード
   - 成果物フォルダを SA のメールアドレス（`...@....iam.gserviceaccount.com`）
     に**編集者として共有**（この共有が実質のスコープ制限 — SA は他の
     ファイルに一切届かない）
   - rclone.conf を組み立てて base64 化:

     ```ini
     [run5drive]
     type = drive
     scope = drive
     service_account_credentials = <JSON キーの中身を 1 行で>
     ```

     ```bash
     base64 -w0 rclone.conf   # 出力を RUN5_RCLONE_CONF_B64 へ
     ```
   - JSON キー・conf・base64 はリポジトリ/チャット記録に残さない運用が
     望ましい（漏洩時の被害面は共有した 1 フォルダに限定されるが、
     キーのローテーションは SA の鍵削除で即時にできる）

## 2. Pod 作成（RunPod REST API）

イメージ・スペックは run 4 実績（`runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`・
RTX 3090 Community $0.22/h・containerDiskInGb 60）を踏襲する。起動コマンドには
[`scripts/run5_pod_entry.sh`](scripts/run5_pod_entry.sh) 冒頭コメントの 1 行
（raw.githubusercontent の pin コミット URL から entry を取得して実行）を注入する。

予算ガード: cap **$8**（DESIGN_S4 §3.4）。スクリプト内 wall-clock 上限 24h
（$0.22/h × 24h ≈ $5.3）。スクリプト自体が死ぬ場合の課金露出は Q8 として設計受容
済み — 第 2 防衛線は §3 のポーリングから の API stop。

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
として Drive へ退避される。

## 5. 成果物（Drive 退避レイアウト）

| 区分 | ファイル |
|---|---|
| heartbeat | `<stage>.status.json` 群（exit code 込み） |
| データ束 pin | `assembly_manifest.json` / `run4_config_datasets.yaml.normalized.yaml` / `dict.txt` |
| 学習 config pin | `run5_config_phase_a.yaml` / `run5_config_phase_b.yaml` / `run5_training_manifest.json` |
| checkpoint | phase A 5K + phase B の 5K/10K/20K/40K（節目毎に push 済み） |
| ログ | `config.yaml`（train.py スナップショット）・`*.log`・TensorBoard events |

s4 record（s3_record 様式）への転記は run 5 完了後に本セッション側で行う —
**run 4 で未転記残となった 4 項目（checkpoint sha / 学習 log・TB sha /
wav 生成コマンド対応表 / 費用）は run 5 では同時転記で完了させる**
（DESIGN_S4 §5 AC）。

## 6. 判定材料 ①〜④（Pod 完走後・ローカル CPU）

判定材料の合成は Pod 上では行わない（学習に vocoder は不要）。回収した
checkpoint に対しローカルで `gate_synth.py run` / `gate_synth_run4.py`
（`--speaker d3synth` で合成教師声の立ちも聴ける — DESIGN_S4 §4）/
`forge_triangle.py`（④ = VG-E1 第 0 世代）を実行する。手順は
`S3_RUN4_RUNBOOK.md` §5 と同一（checkpoint 差し替えのみ）。
