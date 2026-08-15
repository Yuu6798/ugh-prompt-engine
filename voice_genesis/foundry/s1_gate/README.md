# S1 早期打ち切りゲート受け皿 — CPU 合成 (`gate_synth.py`)

`S1_GPU_RUNBOOK.md` §5.2–5.4（GPU 学習の 5K/10K/20K checkpoint 節目に対する
CPU 合成・耳判定）の実装。波音リツ DiffSinger CPU 直接推論スパイク
（`s0_probe_record.md`。実行日 2026-08-15、scratchpad 完結・非コミット、フル配線
sha256 決定論確認済み）の一般化・清書版で、実行者非依存の CLI として本ディレクトリに
収載する。設計判断・遭遇した障害の逐語根拠は `s0_probe_record.md` /
`s1_gate_synth_record.md`（いずれも scratchpad、非コミット）が一次記録であり、本
README は再現手順とモデル pin のみを扱う。

## 0. モデル実体の取得先と pin（非コミット）

**onnx/zip 本体はリポジトリへコミットしない**（数百 MB 級・ライセンス上も非商用
留保のため。詳細は `results_s0/s0_record_2026-08-15.md` §3「ライセンス会計」参照）。
取得したら **sha256 を必ず照合する**（一致しなければ止まる。差し替えない）。

| # | 素材 | 取得元 | sha256 |
|---|---|---|---|
| 1 | リツ公式 DiffSinger 配布 zip（`linguistic.onnx`/`dsdur/dur.onnx`/`dspitch/pitch.onnx`/`acoustic.onnx`/`phonemes.txt`/`dsconfig.yaml` を含む） | `https://www.canon-voice.com/voice/NamineRitsu_DiffSinger.zip` | `5c7b8c328180ea2971f71d89b3a675b2adfc91772664ae28cbb5915385f42530` |
| 2 | vocoder `nsf_hifigan.oudep`（実体は zip。展開すると `nsf_hifigan.onnx` 55MB + `oudep.yaml` + `NOTICE*.txt`） | `https://github.com/xunmengshe/OpenUtau/releases/download/0.0.0.0/nsf_hifigan.oudep` | `e22f84009804da2e5916e7a2000f4c30278148796376e49368ec5ff8f9f58830` |

展開後の実体パス（`--canon-model-dir` / `--vocoder-dir` に渡すパス）:

```
<素材1展開先>/NamineRitsu_DiffSinger/{linguistic.onnx, phonemes.txt, dsconfig.yaml,
                                       dsdur/dur.onnx, dspitch/pitch.onnx, acoustic.onnx}
<素材2展開先(.oudep を unzip)>/nsf_hifigan.onnx
```

**ライセンス留保**（`results_s0/s0_record_2026-08-15.md` §3 が正）: vocoder
（`nsf_hifigan`, OpenVPI Team 提供）は **CC BY-NC-SA 4.0**（非商用・継承・NOTICE
同梱義務）。フルパイプライン（linguistic→dur→pitch→acoustic→vocoder）を通した
最終出力音声はこの vocoder を経由するため、**成果物 WAV は当面非商用スコープ限定**
と扱う。商用化は S1 以降の別途設計判断（P2 vocoder 自前学習 or 商用可 vocoder
差し替え）に委ねる。

`--diffsinger-repo`（`openvpi/DiffSinger` clone, pin: `e2307b1`）は
`s1_dataprep/README.md` §0 の外部ツール表と同一 pin。`mapping-check` サブコマンド
と `run`（`--skip-export` を外す本番経路）が `scripts/export.py` /
`utils/phoneme_utils.py` を利用するために必要。

## 1. 使い方

3 サブコマンドの用法・オプションは `gate_synth.py` 冒頭 docstring（`--help` でも
同一内容）を参照。要約:

- `run`: export（任意）→ さくら/うみ合成 → WAV + sha256/RMS 記録。既定
  `--tokens own` は fail-closed（`*.phonemes.json` が無いとエラー終了。canon
  符号化への暗黙フォールバックはしない）。
- `mapping-check`: 自前音素語彙 <-> canon 617/46 語彙の対応表を検証（欠落音素の
  列挙）。

`score.py`/`score_umi.py` の所在は既定でこのスクリプトから見た
`voice_genesis/singer/`（`--singer-dir` で上書き可）。

## 2. S0 互換再検証（本収載時に再実施・2026-08-15）

収載後、本ディレクトリの `gate_synth.py` を上記 pin 済み実素材で実行し、S0 の
出力と bit-identical であることを再確認した:

```bash
python gate_synth.py run --skip-export --tokens canon \
    --acoustic-dir   <素材1展開先>/NamineRitsu_DiffSinger \
    --canon-model-dir <素材1展開先>/NamineRitsu_DiffSinger \
    --vocoder-dir     <素材2展開先(unzip 後)> \
    --out-dir out_compat --song sakura --notes-limit 6
```

| 項目 | 値 |
|---|---|
| acoustic token encoding | `canon (--tokens canon, explicit S0-compat verification mode)` |
| wav sha256（本収載版 `s1_gate/gate_synth.py`） | `42f459d5ec27b4b4b036a7e6415a93beabf5549e1943324a94fa88eb1f119b98` |
| S0 (`s0_sakura_probe.wav`, `s0_probe_record.md`) sha256 | `42f459d5ec27b4b4b036a7e6415a93beabf5549e1943324a94fa88eb1f119b98` |
| **判定** | **完全一致**（ハードコードパス排除・argparse 化・ディレクトリ移設による回帰なしを確認） |

## 3. 既知の残リスク（`s1_gate_synth_record.md` §4 から引き継ぎ）

- `unmapped_own` の非空チェックは `run` に未実装（`mapping-check` を実 ckpt の
  `acoustic.phonemes.json` に対しても別途走らせ、`unmapped_own_count` を確認する
  運用を 5K 到達時の耳判定前に徹底する）。
- acoustic 差し替え自体（実 ckpt での動作）は本 README 執筆時点で未検証（GPU 学習
  が前提のため、S1 GPU 実行以降のスコープ）。
