# S7 provisioning runbook — run 8 の資産を実行可能な machine へ再配置する

- 日付: 2026-08-21
- 目的: **「別の提出物を探す」のではなく、run 8 の残りを実行できる machine へ
  資産を再配置する**（User 指示 2026-08-21）。実行者非依存の手順として書く。
- 前提: 資産は全て **pin 済みの sha256 で照合できる**。照合に落ちたものは使わない。
- 正本: pin 値の一次ソースは Drive `run7/` の
  `assembly_manifest.json`（sha256 `4e5614d5218657a8f5f6ca2827c52c929581416021f56c054dee754cef1ad99c`）と
  `run_execution_manifest.json`（`run_id = s6_run7`・`repo_commit 7df3a5fe5e34129218d5f3f0cc33ce332eebfff3`）。
  両者の連鎖は 2026-08-21 に実測照合済み（`s7_ledger_inputs.json`）。

## 0. 環境契約（run 7 実測値・`run_execution_manifest.json` より）

```
python                     3.11.10
render numeric stack       numpy==2.4.6 scipy==1.17.1 pyworld==0.3.5 soundfile==0.14.0
binarize numeric stack     numpy 1.26.4 / scipy 1.17.1   ← render と別（混同しない）
ANALYSIS_STACK_PIN         numba==0.66.0 librosa==0.11.0 pyloudnorm==0.2.0
ffmpeg                     n6.1.2 static（libavformat 60.16.100）
SIMD gate                  NPY_DISABLE_CPU_FEATURES=X86_V4（X86_V3 まで許可）
GPU（run7 実績）           NVIDIA GeForce RTX 3090（**run 8-0 の CPU レンダには不要**）
```

B-1 校正は `ANALYSIS_STACK_PIN` を **fail-closed で検査**する
（`s7_b1_calibration.verify_analysis_stack`）。宣言と違う版では測らない。

## 1. 資産表（取得元と pin）

| 資産 | 取得元 | pin (sha256) | 用途 |
|---|---|---|---|
| ritsu voicebank zip | `https://www.canon-voice.com/voice/r73_strong_ren0151.zip` | `88c7b3efcf134945169d9cb4bf1d124e49c387ef1793391a31f56f4df66dde76` | ritsu CSV 再生成 |
| NamineRitsu_DiffSinger zip | `https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip` | `5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530` | `dsdur/dsdict.yaml`（617 語彙） |
| PJS corpus zip | Drive file id `1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_` | `683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca` | pjs CSV 再生成 |
| nnsvs-db-converter | `https://github.com/UtaUtaUtau/nnsvs-db-converter.git` | （run7 は commit 未 pin — 取得時の commit を記帳する） | pjs 変換 |
| **run7 Phase-B 40K checkpoint** | Drive file id `1LY_Qckwo4zTZmaTxq8EmESEoOKjQlVFW` | `518df090a8154e61f28b529f731418f4f97d47c3b56d1326d354e6be4629fa93` | P-ANCHOR / 校正レンダ |
| vocoder pc_nsf_hifigan 2025.02 | openvpi/vocoders release | archive `9d98ba73727f2abb75172cf8249d75182237e8472fc3b6ed09c721ae8b0e83c6` / model ckpt `d6dd28909d2a1a2dcf74b3e3aa0b82b48695b87979fdf41561940aeecd85c67f` | 波形合成 |
| dict.txt | Drive file id `1m6Dx-22HtT-iUtqpphrI63CmELl7LXev` | `633ee9667b4f1079aff4cb1ac66cdce407d02226beb372474bf88ef0c7fedbe4` | 音素辞書（44 記号） |
| assembly_manifest.json | Drive file id `1vs1UDdSlOCGLsQEblkDJ1NLXpbEFeKe6` | `4e5614d5218657a8f5f6ca2827c52c929581416021f56c054dee754cef1ad99c` | CSV pin の正本 |
| run4_config_datasets.yaml.normalized.yaml | Drive file id `16Q4car7o2qISwXgQp_wJMSgZMjY8ug5y` | `d9d8a9cf4e1400ac2200caf313855beb0f717a238128ff6e212945cb5e31b526` | assemble 設定 |
| ffmpeg static | BtbN/FFmpeg-Builds autobuild-2024-09-30 | archive `16ab3dbb56495f17428e5404959e7127f10a561293d867cba0393b2d33bb08c3` / bin `1aabfcf5351c09f23de483052ccd804c8a0f4a4d0409667b400202edac3ce65d` | 44.1kHz 変換 |

**取得経路の制約**: Drive 資産は MCP 経由だと base64 が context を超えるため、
**Drive を直接マウント / `rclone` / ブラウザ取得できる machine で行う**。
公開 URL（canon-voice.com / GitHub release）は HTTP で直接取得できる。

## 1b. 本セッションで実際に確保・照合できた資産（2026-08-21 実測）

| 資産 | サイズ | pin 照合 |
|---|---|---|
| ritsu voicebank zip | 189,261,648 B | **一致** `88c7b3ef…` |
| NamineRitsu_DiffSinger zip | 421,940,274 B | **一致** `5c7b8c32…` |
| PJS corpus zip | 275,179,158 B | **一致** `683c0025…` |
| nnsvs-db-converter | — | clone commit `185ada6`（run7 は commit 未 pin） |
| amitaro staged wav × 324 | 117.7 MB | **324/324 一致**（run7 `staged_source_sha256` 全数） |
| ffmpeg static n6.1.2 | 106,454,476 B | **一致** tarball `16ab3dbb…` / bin `1aabfcf5…` / `libavformat 60. 16.100` |
| **run7 Phase-B 40K checkpoint** | 556,022,498 B | **一致** `518df090…` |
| vocoder pc_nsf_hifigan 2025.02 | — | **一致** zip `9d98ba73…` / model.ckpt `d6dd2890…` |
| run4_prep.tar（user CSV 実体） | 914,688,000 B | tar sha `e6a75c18…`・中の CSV が **一致** `fc3a760c…` |

**取得経路の実測**: Drive の非公開見えするファイルも
`https://drive.usercontent.google.com/download?id=<ID>&export=download&confirm=t`
で直接取得できた（MCP の base64 経路は context 上限に当たるため使わない）。
`amitaro_sources` の 324 件は `cmdlogs/datasets_amitaro-sources-lsjson.log`
（rclone lsjson・file id 付き）を正本として ID を回収した。

**追加で必要だった依存**（run7 では未 pin のもの）:
`pyworld==0.3.5`（ritsu / adapter 経路）・`praat-parselmouth 0.4.7`（`db_converter.py`）・
`pyloudnorm==0.2.0`（`ANALYSIS_STACK_PIN` どおり）。

## 2. 実行順（User 指示 2026-08-21）

```
資産再配置（本 runbook §1）
   ↓
CSV SHA verification（§3）
   ↓
STEP 4  target_exposure_ledger 実集計（§4）
   ↓
user / amitaro の P-ANCHOR blind classification（§5）
   ↓
STEP 5  real-render calibration set の事前登録 amendment（§6）
   ↓
STEP 6  calibration render（§7）
   ↓
measurement spec 1.0 freeze（§8）
```

## 3. CSV: 回収 → 無ければ決定論再生成 → SHA 照合

canonical pin（`results_s7/s7_ledger_inputs.json` に記帳済み）:

| 話者 | rows | sha256 |
|---|---|---|
| ritsu | 456 | `fb01a936fa2435204b57958bf2611ae5b05b538f1b79b2a3919a0280efd13a5c` |
| pjs | 287 | `4e96a4f63d51186ff05b4595219e90ace28a18ef4367bc5932ae29b2ae1952ef` |
| user | 15 | `fc3a760c4c45c37760b8a846f90672acf7db39bc43ff1b3a02908fe9fd7deb3b` |
| amitaro | 299 | `08d67e817110bf965254b48ffe72b787bddfd58ad7ec53d06bd74c081ed3e366` |

手順:

1. **実体回収を最優先**。Pod / Drive に残っていればそれを使う
   （2026-08-21 時点の Drive 検索では未発見 = `s7_ledger_inputs.json` の `retrieval_status`）
2. 無ければ run7 と同じ入力・同じ converter で**決定論再生成**:

```bash
# ritsu（公開 zip 2 本だけで完結。GPU 不要）
python voice_genesis/foundry/s1_dataprep/convert_ritsu.py \
  --voicebank-root <展開先>/波音リツ強連続音Ver1.5.1 \
  --dsdict <展開先>/NamineRitsu_DiffSinger/dsdur/dsdict.yaml \
  --out-dir <作業先>/ritsu_diffsinger_db

# pjs（PJS corpus zip + nnsvs-db-converter）
python voice_genesis/foundry/s1_dataprep/convert_pjs.py \
  --pjs-root <展開先>/PJS_corpus_ver1.1 \
  --converter-dir <展開先>/nnsvs-db-converter \
  --staging-dir <作業先>/pjs_staging
```

3. `sha256sum <out>/transcriptions.csv` を上表と照合
4. **完全一致したものだけ** STEP 4 へ渡す（台帳側が `--inputs-pin` で
   自動照合し、不一致・未 pin・row_count 不一致は `InputPinMismatch` で停止）
5. 不一致なら BLOCKED。**推測した CSV を使わない**

**再生成が pin と一致しない場合に疑う順序**（run7 と本手順の差分）:
python patch 版 / soundfile 版 / repo commit（run7 = `7df3a5fe`）/ 展開時の
ファイル名エンコーディング（cp437 化けの既知事例あり）/ ロケール。

## 4. STEP 4 — 台帳の実集計

```bash
python voice_genesis/foundry/run8/s7_ledger.py \
  --speaker ritsu:VCV:<path>/ritsu/transcriptions.csv \
  --speaker pjs:real_song:<path>/pjs/transcriptions.csv \
  --speaker user:real_song:<path>/user/transcriptions.csv \
  --speaker amitaro:speech:<path>/amitaro/transcriptions.csv \
  --breaking ritsu --non-breaking pjs \
  --out voice_genesis/foundry/results_s7/target_exposure_ledger.json
```

- `--breaking` / `--non-breaking` は **`s7_speaker_classification.json` で
  証拠ベースに確定した分のみ**渡す（現時点 = ritsu / pjs）。user / amitaro は
  §5 の blind 判定が終わるまで渡さない（未分類のままで集計は走る）
- `--inputs-pin` は既定で `s7_ledger_inputs.json` を読む
- 出力は JSON + 人間可読の表（`--table-out`。既定は `--out` と同じ幹の `.md`）

## 5. P-ANCHOR blind classification（user / amitaro・最大 3 問）

- **過去成果物の探索は終了**（User 指示）。run7 Phase-B 40K checkpoint
  （Drive `1LY_Qckwo4zTZmaTxq8EmESEoOKjQlVFW` / sha `518df090…`）で**新規 CPU レンダ**する
- vocoder は公式 release から再取得し、archive / model ckpt の sha を run7 pin と照合
- 提示条件は `s7_speaker_classification.json` の `blind_classification_spec`
  （run7 P-ANCHOR 同条件・`break` / `ok` / `unsure`・回答凍結後に pin・
  UNSURE は unclassified のまま）
- 判定後に `s7_speaker_classification.json` を更新し、台帳を再実行して
  H-TTD を確定する（user が real_song の比較対なので、ここが H-TTD の要）

## 6. STEP 5 — 実レンダ校正セットの事前登録（amendment）

**必ず「生成する前に」pin する**（§12-0-C2 と同じ順序規律）。

- `s7_b1_calibration_set.json` の `amendments` に追記して改訂する:
  何を足すか / なぜ足すか / 本番 360 セルとの分離をどう担保するか
- 要件（User 裁定 1）:
  - 本番 360 セルとは**完全分離**
  - run5/6/7 の good/bad ラベル・P-ANCHOR 結果・本番セルを選定に使わない
  - **同じ DiffSinger / vocoder 経路**を通した校正音のみ
  - 現行 13 合成刺激との**対応が追える**形（条件名・終端種別・尺ラダー・
    gain 対・無音を 1:1 で写像できるように設計する）
- 校正専用 label-free render であって「本番 360 セルを見る」ことではない

## 7. STEP 6 — 校正レンダと B-1 再校正（2026-08-21 実施済み）

事前登録 pin（`d4ee992`）の**後に**生成した。所要 ≈ 60 s（11 レンダ）+ B-1 数分。

```bash
# 7-1. 校正専用 real-render セットを生成（本番 360 セルとは完全分離）
python voice_genesis/foundry/run8/s7_calib_render.py \
  --canon-model-dir  $S7/extracted/ds/NamineRitsu_DiffSinger \
  --vocoder-dir      $S7/materials/vocoder_onnx \
  --acoustic-onnx    $S7/out/onnx_export/s6_run7_acoustic.onnx \
  --acoustic-dsconfig $S7/out/onnx_export/dsconfig.yaml \
  --acoustic-phonemes-json $S7/out/onnx_export/s6_run7_acoustic.phonemes.json \
  --canon-phonemes-txt $S7/extracted/ds/NamineRitsu_DiffSinger/phonemes.txt \
  --speaker ritsu --speaker-emb $S7/out/onnx_export/s6_run7_acoustic.ritsu.emb \
  --ckpt $S7/materials/model_ckpt_steps_40000.ckpt \
  --canon-zip $S7/materials/NamineRitsu_DiffSinger.zip \
  --vocoder-container $S7/materials/nsf_hifigan.oudep \
  --out-dir $S7/out/b1_real_render \
  --manifest-out $S7/out/b1_real_render/s7_b1_real_render_manifest.json

# 7-2. 実レンダ校正で B-1 を回す（合成 spec は上書きしない）
python voice_genesis/foundry/run8/s7_b1_calibration.py \
  --real-render $S7/out/b1_real_render/s7_b1_real_render_manifest.json \
  --out $S7/out/b1_real_render/trf_measurement_spec_realrender.json

# 7-3. 合成校正の spec（比較用）を再生成する場合
python voice_genesis/foundry/run8/s7_b1_calibration.py \
  --out voice_genesis/foundry/results_s7/trf_measurement_spec.json
```

- `verify_analysis_stack()` が `ANALYSIS_STACK_PIN` を fail-closed で照合する
- 独立プロセス反復（`cross_process_reproducibility`）は必須。スキップすると
  どの軸も frozen にならない
- 容器 pin（ckpt / canon zip / vocoder 容器）は**レンダ前**に照合する（不一致なら回さない）
- 結果 = **0/4 軸 frozen・`1.0-rc2` / `blocked`**。原因と裁定要求は
  [`s7_b1_real_render_record.md`](s7_b1_real_render_record.md)

## 8. STEP 7 — `measurement spec 1.0` の凍結条件

- 4 軸それぞれで 6 つの hard requirement を実レンダ校正音で評価する
- **候補選別が実際に働いたか**を記帳する（合成刺激では 12/12・3/3 が通過し、
  選別が働かなかった = `1.0-rc1` に留めた理由の 1 つ）
- 通れば `spec_version = 1.0` / `freeze_status = frozen` へ更新し、
  §12-0-D の PR-2 開始 Gate を再確認する（9 項目）

## 9. 本セッションで実施した分（2026-08-21）

- Drive から `assembly_manifest.json` / `run_execution_manifest.json` を取得し、
  4 話者 CSV pin と連鎖を実測照合（§3 の表はその実測値）
- `s7_ledger_inputs.json` を新設して pin し、台帳へ fail-closed 照合ゲートを実装
- ritsu の公開素材 2 本の取得を開始（本 runbook §1 の 1・2 行目）。結果は
  `s7_record_2026-08-21.md` に追記する
