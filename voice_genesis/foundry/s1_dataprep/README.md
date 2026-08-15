# S1 データ工場 — D1 (PJS) + D2 (リツ) → DiffSinger 2 話者統合データセット

`DESIGN_S1_p2poc.md` §3「データ工場」の実装。scratchpad スパイク
（一次記録: `s1a_conversion_record.md`・`s1b_dataset_record.md`、非コミット）の
清書版で、実行者非依存の CLI として収載する。設計判断・遭遇した障害の逐語根拠は
上記 2 記録が正本であり、本 README は再現手順のみを扱う。

## 0. 入力素材の取得先と pin

**3 点とも権利者本人配布**（ライセンス精査は `jp_corpus_survey_2026-08-15.md` /
`results_f1_2/licenses/*.md` / `results_s0/s0_record_2026-08-15.md` が正）。
取得したら **sha256 を必ず照合する**（一致しなければ止まる。差し替えない）。

| # | 素材 | 取得元 | サイズ | sha256 |
|---|---|---|---|---|
| 1 | 波音リツ強連続音 Ver1.5.1（UTAU VCV voicebank、A3/F4） | `https://www.canon-voice.com/voice/r73_strong_ren0151.zip` | 189,261,648 bytes | `88c7b3efcf134945169d9cb4bf1d124e49c387ef1793391a31f56f4df66dde76` |
| 2 | PJS corpus ver1.1（歌唱コーパス 100 曲） | Google Drive `1hPHwOkSe2Vnq6hXrhVtzNskJjVMQmvN_`（`gdown` 推奨。ページ: `https://sites.google.com/site/shinnosuketakamichi/research-topics/pjs_corpus`） | 275,179,158 bytes（≈0.256 GiB） | `683c00253ee35a62d50de0375bb9d8e003a74338d4ce3495ac3f7ad096abc1ca` |
| 3 | リツ公式 DiffSinger 配布 zip（617 語彙辞書 `dsdur/dsdict.yaml` を抽出して使う） | `https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip` | 421,940,274 bytes | `5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530` |

展開後の実体パス（zip 直下のフォルダ名。パスに触れるコードはこの名前に依存する）:

```
<素材1展開先>/波音リツ強連続音Ver1.5.1/{A3,F4}/oto.ini + *.wav
<素材2展開先>/PJS_corpus_ver1.1/pjsNNN/{pjsNNN.lab, pjsNNN_song.wav, pjsNNN_speech.wav, ...}
<素材3展開先>/NamineRitsu_DiffSinger/dsdur/dsdict.yaml   # 617 grapheme エントリ
```

素材 3 は zip 全体 (403MB) を必要としない。`dsdur/dsdict.yaml` 1 ファイルの
みが D2 変換に必要（音響モデル本体 `acoustic.onnx` 等は本工程では不使用）。

**外部ツール clone**（実行前に用意する。commit pin は下記）:

| ツール | clone URL | pin commit |
|---|---|---|
| `UtaUtaUtau/nnsvs-db-converter` | `https://github.com/UtaUtaUtau/nnsvs-db-converter.git` | `185ada6`（`convert_pjs.py --converter-dir` に渡す） |
| `openvpi/DiffSinger` | `https://github.com/openvpi/DiffSinger.git` | `e2307b1`（`scripts/binarize.py` を実行するために別途必要。本ディレクトリのスクリプトは呼び出さない） |

`donor_bank_utau.py`（`voice_genesis/foundry/adapter/`）は既存実績コード
（review #262 で oto 境界計算バグ修正済み）を read-only import する。
本ディレクトリのスクリプトから移植・複製はしない。

## 1. 実行順

```bash
# 環境（既存 svp-rpe 環境に以下を追加。python 3.11 系実測）
pip install praat-parselmouth        # nnsvs-db-converter の依存
pip install -r <DiffSinger clone>/requirements.txt \
  || pip install h5py resampy tensorboardX onnxsim MonkeyType  # 個別導入でも可
# torch は CPU 版で足りる（binarize は学習を含まない）

# D2: リツ VCV → transcriptions.csv + wavs/
python voice_genesis/foundry/s1_dataprep/convert_ritsu.py \
    --voicebank-root "<素材1展開先>/波音リツ強連続音Ver1.5.1" \
    --dsdict         "<素材3展開先>/NamineRitsu_DiffSinger/dsdur/dsdict.yaml" \
    --out-dir        <出力>/ritsu_diffsinger_db

# D1: PJS → transcriptions.csv + wavs/ （nnsvs-db-converter を subprocess 起動）
python voice_genesis/foundry/s1_dataprep/convert_pjs.py \
    --pjs-root       "<素材2展開先>/PJS_corpus_ver1.1" \
    --converter-dir  <clone>/nnsvs-db-converter \
    --staging-dir    <出力>/pjs_staging
# 出力: <出力>/pjs_staging/diffsinger_db/transcriptions.csv + wavs/

# 統合: 2 話者辞書 + acoustic config 生成 + 検証
python voice_genesis/foundry/s1_dataprep/build_dataset.py \
    --ritsu-raw-dir    <出力>/ritsu_diffsinger_db \
    --pjs-raw-dir      <出力>/pjs_staging/diffsinger_db \
    --out-dict         <出力>/merged_ja_dict.txt \
    --out-config       <出力>/s1_multispeaker_acoustic_config.yaml \
    --binary-data-dir  <出力>/binary \
    --report           <出力>/build_report.json

# binarize（openvpi/DiffSinger 側。本ディレクトリの範囲外だが確認用に記載）
cd <DiffSinger clone>
python scripts/binarize.py --config <出力>/s1_multispeaker_acoustic_config.yaml
```

## 2. 所要時間実測値（CPU、本セッション実測・2026-08-15）

| ステップ | 実測時間 |
|---|---|
| `convert_ritsu.py`（456 セグメント、A3+F4） | 約 3.9 秒 |
| `convert_pjs.py`（100 曲 → 287 セグメント。nnsvs-db-converter 本体の変換自体は約 54 秒、subprocess 起動込みで約 56 秒） | 約 56 秒 |
| `build_dataset.py`（辞書生成 + config 生成 + 検証） | 1 秒未満 |
| `scripts/binarize.py`（train 733 / valid 10、統合後） | 約 43 秒（`s1b_dataset_record.md` §5.2 実測） |

## 3. 出力

```
<出力>/
├── ritsu_diffsinger_db/
│   ├── transcriptions.csv   # name,ph_seq,ph_dur （456 行）
│   ├── wavs/                # ritsu_{A3,F4}_NNN.wav （456 本、44.1kHz PCM16）
│   ├── provenance.json      # name -> 元 oto wav ファイル名 + pitch dir
│   └── d2_stats.json        # 収量統計（unmapped/sokuon/音素分布 等）
├── pjs_staging/
│   ├── pjsNNN.lab / pjsNNN.wav   # song wav のみをリネームしたシンボリックリンク
│   ├── lang.json                 # nnsvs-db-converter へ渡した言語定義
│   └── diffsinger_db/
│       ├── transcriptions.csv    # name,ph_seq,ph_dur,ph_num,note_seq,note_dur （287 行）
│       └── wavs/                 # pjsNNN_segNNN.wav （287 本、44.1kHz へ自動リサンプル）
├── merged_ja_dict.txt            # PJS(34) ∪ リツ-D2(3: dy/fy/vf) 追加 = 38 行、恒等写像
├── s1_multispeaker_acoustic_config.yaml   # binarize.py --config にそのまま渡せる
└── build_report.json             # 話者別セグメント数・音素差分・test_prefixes・検証結果
```

## 4. 動作検証の記録

収載版スクリプトを scratchpad の実素材（上記 pin 済み展開物）で一周走らせ、
D2/D1 双方の `transcriptions.csv` が scratchpad スパイク出力（`s1b_ritsu_dataset/`
`s1_pjs_diffsinger/`）と **`diff` 完全一致**（バイト単位）することを確認した。
`ritsu_diffsinger_db/wavs/*.wav` も sha256 集合が完全一致。`build_dataset.py`
の統合辞書も `s1b_dataset_record.md` の `merged_ja_dict.txt`（38 記号: 共通34
+ リツ由来 `dy`/`fy`/`vf`、PJS 由来 `cl`/`xx` は各話者専用として residual に
現れる）と集合一致し、検証（wav 欠落・ph_seq/ph_dur 長不一致・非正 duration）は
`problems: []`（PASS）。`test_prefixes` の具体的な選定値は決定論選択アルゴリズム
（`build_dataset.select_test_prefixes`: name 昇順を等分割）が生成するため
scratchpad 時点の手動指定値とは一致しないが、機能上の等価性（話者ごと 5 件・
binarize の `test_prefixes` として妥当）は満たす。

## 5. 既知の制限（引き継ぎ）

- D2（リツ）は AP（息継ぎ）を検出しない（UTAU oto.ini に相当情報源が無いため。
  統合データセットの AP は PJS 由来のみ）。
- 3 音素クラスタ（`kw`/`gw`/`vf`+グライド系、fallback table 由来）の子音内境界は
  oto の 1 境界からの均等按分近似であり、音響的な正確性は未検証。
- いずれも `s1b_dataset_record.md` §7 の申し送り事項と同一。学習後の耳判定
  （S1 §4 早期ゲート）で異常が出た場合の疑い対象として記録しておく。
