# S1 GPU 実行 runbook（実行者非依存）

設計: [`DESIGN_S1_p2poc.md`](DESIGN_S1_p2poc.md)（統合正本・§4「GPU 実行」が本書の
要求元）。データ準備部分の詳細手順は
[`s1_dataprep/README.md`](s1_dataprep/README.md)（入力素材 pin・実行順・動作検証記録）。

**規律**（`docs/m2e_provisioning_runbook.md` の流儀を踏襲）: 本書は手順のみを記述
する。**閾値・予算上限・学習規模を本書で決めない**（それらは `DESIGN_S1_p2poc.md`
§4・§6 が凍結する）。本書と設計が食い違ったら**設計が勝つ**。

**前提**: 本 runbook は **User が GPU インスタンスを実行し、判定材料
（checkpoint・ログ・費用実測）を本セッション（Claude 実行環境）へ持ち帰る**
運用を前提に書く。GPU 実行そのもの・費用発生を伴う判断は User 側の決裁事項
であり、本セッションは (a) 事前のデータ準備、(b) User が持ち帰った成果物の
検分・ONNX export・CPU 合成・耳判定の受け皿、を担当する。

---

## 0. 前提と予算ガード

- **想定インスタンス**: Vast.ai または RunPod の RTX 4090 スポット、Ubuntu +
  CUDA（イメージのプリインストール CUDA 版に torch を合わせる。§2 参照）。
- **予算ガード（`DESIGN_S1_p2poc.md` §4 と同一の凍結値）**:
  - 時給レンジ: 4090 スポット **$0.3–0.5/h**
  - 40K steps の実時間見積り: **6–24h = $2–12**（一次事例未発見のためレンジ。
    早期ゲートでの下振れ判定を優先する）
  - **上限 $15 で打ち切り。** 上限到達が見えたら残り steps を諦めて直近の
    checkpoint で早期ゲート判定へ回す（§6）。上限を動かす判断は User のみ。
- 本書のどの手順も、`DESIGN_S1_p2poc.md` が commit された後にのみ実行してよい
  （User 承認待ちのまま GPU を起動しない）。

---

## 1. 環境構築（GPU インスタンス側）

```bash
# CUDA 版を先に確認してから torch を合わせる（イメージ既定の CUDA と
# 不一致だと動かない。イメージのタグに CUDA 版が書かれていることが多い）
nvidia-smi
python -V   # 3.11 系を推奨（データ準備側の実測環境と揃える）

git clone https://github.com/openvpi/DiffSinger.git
cd DiffSinger
git checkout e2307b1   # データ準備の動作検証で使った commit と同じ pin。
                        # 別 commit を使う場合は理由と commit hash を記録に残す
pip install -r requirements.txt
# torch は CUDA 版へ差し替える（requirements.txt の torch>=2.4.0 制約を満たす
# CUDA ビルドを、nvidia-smi で確認した CUDA 版に合わせて選ぶ。例:
#   pip install torch --index-url https://download.pytorch.org/whl/cu121
# 具体的な index URL はインスタンスの CUDA 版に応じて読み替える）
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

---

## 2. 素材取得（README の pin 再掲）

`s1_dataprep/README.md` §0 の pin と完全に同一。GPU インスタンス側にも同じ
3 点を取得し、**sha256 を必ず照合する**（一致しなければ止まる。差し替えない）:

| # | 素材 | 取得元 | sha256 |
|---|---|---|---|
| 1 | 波音リツ強連続音 Ver1.5.1 | `https://www.canon-voice.com/voice/r73_strong_ren0151.zip` | `88c7b3efcf134945169d9cb4bf1d124e49c387ef1793391a31f56f4df66dde76` |
| 2 | PJS corpus ver1.1 | Google Drive `1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_`（`gdown`） | `683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca` |
| 3 | リツ公式 DiffSinger 配布 zip（`dsdur/dsdict.yaml` のみ使用） | `https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip` | `5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530` |

```bash
curl -L -o r73_strong_ren0151.zip "https://www.canon-voice.com/voice/r73_strong_ren0151.zip"
sha256sum r73_strong_ren0151.zip   # 88c7b3ef...df66dde76 と照合

pip install --no-cache-dir gdown
gdown "https://drive.google.com/uc?id=1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_" -O PJS_corpus_ver1.1.zip
sha256sum PJS_corpus_ver1.1.zip   # 683c0025...096abc1ca と照合

curl -L -o NamineRitsu_DiffSinger.zip "https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip"
sha256sum NamineRitsu_DiffSinger.zip   # 5c7b8c32...385f42530 と照合

unzip r73_strong_ren0151.zip -d ritsu_extracted
unzip PJS_corpus_ver1.1.zip -d pjs_extracted
unzip NamineRitsu_DiffSinger.zip -d ritsu_diffsinger_extracted
```

---

## 3. 本リポ clone → s1_dataprep 実行 → binarize

```bash
git clone <本リポジトリの URL> ugh-prompt-engine
cd ugh-prompt-engine
git checkout claude/voicegenesis-dev-continue-rfwf3p   # または該当ブランチ/タグ
pip install -e ".[dev]"
pip install praat-parselmouth

git clone https://github.com/UtaUtaUtau/nnsvs-db-converter.git
cd nnsvs-db-converter && git checkout 185ada6 && cd ..

OUT=~/s1_data
python voice_genesis/foundry/s1_dataprep/convert_ritsu.py \
    --voicebank-root "<素材1展開先>/波音リツ強連続音Ver1.5.1" \
    --dsdict         "<素材3展開先>/NamineRitsu_DiffSinger/dsdur/dsdict.yaml" \
    --out-dir        "$OUT/ritsu_diffsinger_db"

python voice_genesis/foundry/s1_dataprep/convert_pjs.py \
    --pjs-root       "<素材2展開先>/PJS_corpus_ver1.1" \
    --converter-dir  ./nnsvs-db-converter \
    --staging-dir    "$OUT/pjs_staging"

python voice_genesis/foundry/s1_dataprep/build_dataset.py \
    --ritsu-raw-dir    "$OUT/ritsu_diffsinger_db" \
    --pjs-raw-dir      "$OUT/pjs_staging/diffsinger_db" \
    --out-dict         "$OUT/merged_ja_dict.txt" \
    --out-config       "$OUT/s1_multispeaker_acoustic_config.yaml" \
    --binary-data-dir  "$OUT/binary" \
    --report           "$OUT/build_report.json"
# "validation OK" を確認する（"validation FAILED" のまま先へ進まない）

cd DiffSinger
python scripts/binarize.py --config "$OUT/s1_multispeaker_acoustic_config.yaml"
```

**照合値（binarize 完走後、必ず確認する。ズレたら data prep 側の差分を疑い、
先へ進まない）**:

```
train 733 件（ritsu 451 + pjs 282）
valid  10 件（ritsu 5 + pjs 5）
train total duration: 4180.71s
valid total duration: 57.72s
合計 duration: 4238.43s
エラー・Traceback 0 件
```

> **照合値の訂正（2026-08-16）**: 初版に記載した train 4171.08s / valid 67.35s は
> 中間版 CSV 由来の陳腐化した値だった（S1 実行時に RunPod 実測とズレて発覚。
> 収載版スクリプトの再実行では上記の値になることを本環境でも確認済み）。件数・
> 合計 duration・音素分布が一致し duration の差が train/valid 間で相殺する場合、
> 差分は valid 選定のみであり学習品質への実害はない。valid 選定の内容非依存な
> 位置ベース設計は follow-up で安定化予定（`s1_split_nondeterminism` 調査参照）。

（`s1b_dataset_record.md` §5.2 の実測値と同一。データ準備コード自体は
`s1_dataprep/README.md` §4 で scratchpad 実測との byte-diff 一致を別途確認済み
のため、この照合はデータ取得・環境差異の検知が主目的）

---

## 4. acoustic 学習 config（2 話者・40K steps）

`build_dataset.py` が生成した `s1_multispeaker_acoustic_config.yaml`
（§3）をベースに、学習規模に関わるフィールドを追記する。**追記対象はこの
config だけ**（`configs/acoustic.yaml` 等の DiffSinger 側デフォルトは変更
しない）。

```yaml
# $OUT/s1_multispeaker_acoustic_config.yaml の末尾に追記
max_updates: 40000
val_check_interval: 5000    # 既定 4000 だと 5K/10K/20K の節目に checkpoint が
                              # 乗らない。早期ゲート節目 (5K/10K/20K) と一致させる
num_ckpt_keep: 10            # 既定 5 だと 40K/5K=8 個の非 permanent checkpoint を
                              # 保持しきれず、早期の 5K/10K が学習後半で prune
                              # される（`permanent_ckpt_start` 既定 60000 は
                              # 40K 総 steps では発火しないため無効）。
                              # 8 個全てを学習完了まで残せる余裕を持たせる
```

学習実行（`exp_name` は checkpoint 保存先 `checkpoints/<exp_name>/` になる。
中断・再起動後は同じコマンドを再実行すれば直近の checkpoint から自動再開
する。明示的にやり直したい場合のみ `--reset` を付ける）:

```bash
cd DiffSinger   # base_config: configs/acoustic.yaml が相対パスのため必須
python scripts/train.py \
    --config "$OUT/s1_multispeaker_acoustic_config.yaml" \
    --exp_name s1_ritsu_pjs_acoustic_v1
```

checkpoint は `checkpoints/s1_ritsu_pjs_acoustic_v1/model_ckpt_steps_<N>.ckpt`
として `val_check_interval` ごと（= 5000 steps ごと）に生成される。

---

## 5. 早期打ち切りゲート（5K / 10K / 20K）

`DESIGN_S1_p2poc.md` §4: 「5K 時点で発声が立っていなければ設定見直しで打ち切り」。
**5K/10K/20K の各節目で学習を止めずに**（バックグラウンドで走らせたまま）
checkpoint だけを回収し、判定材料が揃うまで学習を継続してよい。

### 5.1 回収（GPU インスタンス側 → 本セッションへ）

各節目（5000 / 10000 / 20000 steps）で、`checkpoints/<exp_name>/` 配下の
**その step の ckpt ファイル + `config.yaml`（train.py がスナップショット
保存する学習時 config。export.py が読む）** を回収する。

```bash
# GPU インスタンス側（例。実際の転送手段は Vast.ai/RunPod の SSH 接続情報に従う）
STEP=5000
scp -P <port> \
    root@<host>:~/DiffSinger/checkpoints/s1_ritsu_pjs_acoustic_v1/model_ckpt_steps_${STEP}.ckpt \
    root@<host>:~/DiffSinger/checkpoints/s1_ritsu_pjs_acoustic_v1/config.yaml \
    ./checkpoints_gate_${STEP}/
```

**成果物一覧（1 節目あたり）**:

| ファイル | 用途 |
|---|---|
| `model_ckpt_steps_<N>.ckpt` | 学習重み本体（export.py の入力） |
| `config.yaml`（`checkpoints/<exp_name>/` 直下、train.py 自動保存） | export.py が `--exp` 解決に使う。無いと export できない |

回収の都度、**費用実測を記録する**（§7）。

### 5.2 ONNX export（本セッション側で実行、CPU で足りる）

```bash
cd DiffSinger   # ↑ §1 と同じ commit の clone を本セッション側にも用意しておく
mkdir -p checkpoints/s1_ritsu_pjs_acoustic_v1
cp <回収した ckpt/config.yaml> checkpoints/s1_ritsu_pjs_acoustic_v1/

python scripts/export.py acoustic \
    --exp s1_ritsu_pjs_acoustic_v1 \
    --ckpt <STEP> \
    --out  <出力先>/onnx_gate_<STEP>/
```

出力: `<出力先>/onnx_gate_<STEP>/acoustic.onnx`（+ 付随ファイル）。

### 5.3 CPU 合成（既存 S0 推論チェーンへの差し替え）

`s0_probe_record.md` の CPU 推論チェーン（`linguistic.onnx -> dur.onnx ->
linguistic.onnx(2回目) -> pitch.onnx -> acoustic.onnx -> vocoder`）を再利用
する。**linguistic/dur/pitch/vocoder はカノン氏配布のまま**（`DESIGN_S1_p2poc.md`
§2 のスコープどおり）、**`acoustic.onnx` だけを §5.2 の export 成果物へ
差し替える**。

**既知の相互運用性要件（要確認・未検証。実装前に必ず踏む）**: `acoustic.onnx`
の入力 `tokens`（int64 音素 ID 列）は、**自前学習に使った語彙
（`merged_ja_dict.txt` から binarize が生成する `dictionary-ja.txt` を元に
`export.py acoustic` が書き出す `<model_name>.phonemes.json`）の ID 空間**
であり、カノン配布 `linguistic.onnx`/`dur.onnx`/`pitch.onnx` が使う `tokens`
（カノン公式 617/46 語彙の ID 空間）とは**別物**である（実測: `acoustic.onnx`
の入力は `tokens`/`durations`/`f0`/`depth`/`speedup` のみで、`linguistic.onnx`
の連続値出力 `encoder_out` は経由しない。`onnx_io_dump.txt` 参照）。
したがって CPU 合成スクリプトでは:

1. `linguistic.onnx`/`dur.onnx`/`pitch.onnx` 呼び出し用の `tokens` は
   **従来どおりカノン公式辞書で符号化**する（変更しない）。
2. `acoustic.onnx` へ渡す `tokens` は、**同じ音素文字列列**（ph_seq）を
   **自前 `<model_name>.phonemes.json`（`export.py acoustic` が
   `deployment/exporters/acoustic_exporter.py:_export_phonemes` で
   `acoustic.onnx` と同じ出力ディレクトリに書き出す、`phone_to_id` の flat
   JSON dict。`utils/phoneme_utils.py:PhonemeDictionary.dump` の出力形式で、
   改行区切りの plain-text `phonemes.txt` ではない — 実装時に精読して判明した
   実体。canon 配布物の `phonemes.txt` はカノン側が別途変換・同梱したもので、
   export.py の標準出力形式ではない）で再符号化**したものを使う。
3. `durations`（フレーム長）は `dur.onnx` が出した値をそのまま流用してよい
   （音素の並び順が同一であれば ID 空間に依存しない）。

この差し替えを外すと、クラッシュはしないが**誤った音素へ着地した音声**が
出力される（サイレントな不整合のため、実装時に見落とさないこと）。

### 5.4 耳判定

`さくら`（`voice_genesis/singer/score.py: build_sakura_score()`）・`うみ`
（`voice_genesis/singer/score_umi.py: build_umi_score()`）の 2 曲を合成し、
**S0 と同一の軸**で User 判定を仰ぐ:

- 日本語
- 滑らかさ
- 歌声
- ノイズ

5K 時点で「発声が立っていない」（無音・ノイズのみ・音素崩壊）と判定された
場合は、10K/20K を待たずに設定見直し（学習率・config・データ量）を検討する
（`DESIGN_S1_p2poc.md` §4）。

---

## 6. 中断・再開・費用記録の作法

- **中断**: GPU インスタンスを落としても学習ジョブそのものは失われる前提で
  扱う（スポットインスタンスは横取りされうる）。**直近の checkpoint は
  `val_check_interval` ごとに自動保存済み**（§4）なので、インスタンス再作成
  後に同じ `--exp_name` で `scripts/train.py` を再実行すれば直近 checkpoint
  から自動再開する（`checkpoints/<exp_name>/` を退避してから再作成インスタンス
  へ戻す運用にする。ローカルディスクだけに置いたまま揮発させない）。
- **再開時の確認**: 再開直後のログで `resuming from checkpoint` 相当の表示と
  再開 step 番号を確認する。0 step から再学習していないかを必ず見る。
- **費用記録**: 各節目（起動・5K/10K/20K 回収・中断・終了）で下記を記録する
  （`docs/m2e_provisioning_runbook.md` の「完了判定は報告文でなく成果物で
  行う」規律と同じ: 経過時間の自己申告ではなく、インスタンス課金ダッシュ
  ボードの実測額を使う）。

  | 時刻 (UTC) | イベント | 累積経過時間 | 累積費用実測 | 備考 |
  |---|---|---|---|---|
  | | インスタンス起動 | 0h | $0 | インスタンス種別・時間単価を記録 |
  | | 5K 到達・回収 | | | |
  | | 10K 到達・回収 | | | |
  | | 20K 到達・回収 | | | |
  | | 40K 到達 or 打ち切り | | | 打ち切り理由（予算上限 / 耳判定 NG / 完走） |

- **$15 上限到達が見えたら**（残り時間の課金見込みで超過が確実なら）、
  直近の checkpoint を回収したうえで学習を止める。上限を超えて継続する
  かどうかは User の決裁事項であり、本書のデフォルトは「止める」。
- 記録先: `voice_genesis/foundry/results_s1/s1_record_<date>.md`
  （`DESIGN_S1_p2poc.md` §6 Acceptance Criteria の出口記録と同一ファイル。
  費用実測表・耳判定逐語・Open Questions をここへ集約する）。
