# F1b テンプレート路線スパイク — 生ログ

## 結果表

ドナー: vocadito_2（実歌唱, CC BY 4.0, f0中央値345.31Hz@実測）。
グルーコード: `glue_template.py` 280行。決定論（乱数不使用）。
ドナー転写距離（donor f0中央値 vs さくらscore f0中央値, semitone）: **+1.011**
（3合成出力とも同一ドナー・同一score駆動のため共通）。

| 出力 | b0_500 | b1k_3k | b3k_5k | b5k_8k | HNR(dB) | グルー行数 | ドナー転写距離(semitone) |
|---|---|---|---|---|---|---|---|
| f1b_template_neutral.wav | 0.139 | 0.027 | 0.0036 | 0.0002 | 10.36 | 280 | +1.011 |
| f1b_template_dark.wav | 0.245 | 0.012 | 0.0007 | 0.00004 | 10.33 | 280 | +1.011 |
| f1b_template_bright_breathy.wav | 0.136 | 0.039 | 0.0040 | 0.0005 | 9.30 | 280 | +1.011 |
| f1b_donor_excerpt.wav（参照, 実声） | 0.124 | 0.383 | 0.0106 | 0.0011 | 9.62 | — | 0（自己参照） |

**最重要所見**: donor の `b1k_3k`=0.383 に対し合成3種は 0.012–0.039 と一桁以上低い。
HNR は合成(9.3–10.4dB)とdonor(9.6dB)でほぼ同水準 — 有声性・周期性そのものは
テンプレートが再現できている一方、1k–3kHz 帯のフォルマントエネルギーが痩せている。
F1a の律速（手設計包絡は声に聴こえない）をテンプレートが破れたかは耳判定が必要だが、
定量指標上は「有声さ(HNR)は改善、中域フォルマント密度は依然ドナーに届いていない」
という部分的改善として記録する。formant_scale 変形（dark/bright）は狙い通り
b0_500 とb1k_3k の配分を移動させており、Genome 変形としては機能している。

## 0. 環境確認

- 2026-08-15T03:04:17Z セッション開始
- python3.11.15
- pyworld / soundfile / numpy / scipy / librosa: すべて import 成功（追加 pip 不要）
- HTTPS_PROXY=http://127.0.0.1:44863 経由でネットワーク到達確認済み

## Part 1 — ドナー素材

### 1.1 リポジトリ内既存記録の確認

`grep -rn "vocadito" docs/ tests/ --include=*.md --include=*.py -l` がヒットしたファイル群を確認。
実データそのものはリポジトリに同梱されていない（pin YAML のみ:
`tests/fixtures/melody_bench/m2c_external_fixtures.yaml`）。

filesystem 全体を検索（`find / -iname "*vocadito*"`）した結果、pytest 一時ディレクトリ
（`/tmp/pytest-of-root/...`、合成テスト fixture）以外に実データは存在しない。
→ Zenodo から新規取得する。

### 1.2 Zenodo record 特定

```
$ curl -sSL "https://zenodo.org/api/records/?q=vocadito&size=5"
```
exit=0

結果: record id **5578807**, タイトル "vocadito: A dataset of solo vocals with f0, note,
and lyric Annotations", ファイル `vocadito.zip` size=58492257 bytes (~55.8MB),
md5 `dea40fd18f14d899643c4ba221b33a46`。

本リポの `docs/m2e_provisioning_runbook.md` §3 に記載された既存 pin
（Zenodo record 5578807・CC BY 4.0・`vocadito.zip` md5 `dea40fd18f14d899643c4ba221b33a46`）
と **完全一致**。サイズは 1GB 超えなし、DL 続行。

### 1.3 DL・展開

```
$ curl -sSL --max-time 590 -o vocadito.zip "https://zenodo.org/records/5578807/files/vocadito.zip?download=1"
```
exit=0, http_code=200, size=58492257, elapsed=46s

```
$ md5sum vocadito.zip    -> dea40fd18f14d899643c4ba221b33a46  (pin と一致)
$ sha256sum vocadito.zip -> e0d6b99d3f9c594afe5ae5c4d7bdacebe569e53b809e90b89d1c771c4f9990e3
$ unzip -q vocadito.zip -d vocadito   -> exit=0
```

展開結果: `vocadito/Audio/*.wav` (40 clip, real) + `vocadito/__MACOSX/...` (junk, ignore) +
`vocadito/Annotations/{F0,Notes,Lyrics}/` + `vocadito_metadata.csv`。

### 1.4 ドナー選定

`vocadito_metadata.csv` の `average_pitch`（MIDI 相当の粗い指標）が MIDI64（≈330Hz）に
近い候補 15 クリップを選び、`select_donor.py`（pyworld.harvest, frame_period=5ms）で実測。

```
$ python3 select_donor.py    (elapsed=104s)
track_id=  2 sr=44100 dur=35.2s f0_median=344.9Hz voicing_rate=0.845 semitone_vs_330=+0.77
track_id=  6 sr=44100 dur=23.8s f0_median=266.8Hz voicing_rate=0.811 semitone_vs_330=-3.68
track_id=  7 sr=44100 dur=19.5s f0_median=296.0Hz voicing_rate=0.856 semitone_vs_330=-1.88
track_id=  9 sr=44100 dur=20.4s f0_median=293.4Hz voicing_rate=0.781 semitone_vs_330=-2.03
track_id= 15 sr=44100 dur=20.3s f0_median=250.4Hz voicing_rate=0.826 semitone_vs_330=-4.78
track_id= 16 sr=44100 dur=17.3s f0_median=244.2Hz voicing_rate=0.830 semitone_vs_330=-5.21
track_id= 22 sr=44100 dur=18.3s f0_median=242.2Hz voicing_rate=0.766 semitone_vs_330=-5.36
track_id= 24 sr=44100 dur=14.8s f0_median=228.8Hz voicing_rate=0.903 semitone_vs_330=-6.34
track_id= 25 sr=44100 dur=14.7s f0_median=262.2Hz voicing_rate=0.877 semitone_vs_330=-3.98
track_id= 30 sr=44100 dur=13.0s f0_median=203.3Hz voicing_rate=0.837 semitone_vs_330=-8.38
track_id= 34 sr=44100 dur=24.9s f0_median=217.5Hz voicing_rate=0.955 semitone_vs_330=-7.22
track_id= 37 sr=44100 dur=38.7s f0_median=287.4Hz voicing_rate=0.853 semitone_vs_330=-2.40
track_id= 38 sr=44100 dur=27.8s f0_median=273.9Hz voicing_rate=0.758 semitone_vs_330=-3.22
track_id= 39 sr=44100 dur=31.7s f0_median=274.8Hz voicing_rate=0.866 semitone_vs_330=-3.17
track_id= 40 sr=44100 dur=18.6s f0_median=273.6Hz voicing_rate=0.872 semitone_vs_330=-3.24
```

採用: **vocadito_2**（f0 中央値 344.9Hz が 260–400Hz レンジ内で最も 330Hz に近く、
有声率 0.845 も良好）。sha256 `8dcc99c3b08a9a5800b793e3d65cccfb4464961f15cf8ccde25bd4c8b853d519`
が本リポ pin `tests/fixtures/melody_bench/m2c_external_fixtures.yaml` の
`vocadito_2.expected_audio_sha256` と完全一致（既存 pin の正しさを再確認）。
帰属詳細は `f1b_attribution.md`。

## Part 2 — テンプレート銀行の構築

**設計判断（音素非区別）**: テンプレート索引はピッチ（semitone ビン）のみで行い、
音素ラベルは一切使わない。出力は音素を跨いで同一のスペクトル包絡（=単一母音相当、
「ラで歌う」に近い音色）になる。F1b の問い「テンプレートで声に聴こえるか」に対して
歌詞明瞭度は評価対象外のため、これで進める。

`f1a_control.npz` は既に scratchpad に存在（`/tmp/.../foundry_f1a/f1a_control.npz`、
`glue_control.py` 実行済み・再生成不要のため今回はコピー・再実行なし）。

## Part 3 — 合成

`OUT/glue_template.py`（280 行）で Part2+Part3 を一括実装。

```
$ python3 glue_template.py    (elapsed=35s)
donor loaded: 844993 samples @ 24000Hz (35.208s), resampled from 44100Hz via resample_poly(80/147)
wrote f1b_donor_excerpt.wav: 240000 samples (10.000s)
template bank: 30 semitone bins populated (voiced frames=5863/7042)
bin frame counts: 37:4, 38:48, 39:59, 40:31, 41:19, 42:14, 43:11, 44:2, 45:5, 46:1, 48:1,
  50:3, 51:4, 53:1, 55:1, 56:7, 57:30, 58:23, 59:34, 60:562, 61:109, 62:301, 63:335,
  64:1048, 65:1300, 66:201, 67:1368, 68:338, 69:1, 70:2
sparse bins (<3 frames): [44, 46, 48, 53, 55, 69, 70]
control loaded: total_samples=590000 (24.583s) sr=24000, voiced_frac=0.9492
control f0 median (voiced) = 325.71 Hz
target frames: n_frames=4917 n_unvoiced_carry=250 n_extended_lookup=0
target bins used (post-extension): 57:662, 58:2, 59:3, 60:1, 61:2, 62:1318, 63:20,
  64:1323, 65:995, 66:5, 67:5, 68:5, 69:326
wrote f1b_template_neutral.wav: 590040 samples (24.585s), peak-normalized to 0.6
wrote f1b_template_dark.wav: 590040 samples (24.585s), formant_scale=0.94 + tilt=-2dB/oct, peak-norm 0.6
wrote f1b_template_bright_breathy.wav: 590040 samples (24.585s), formant_scale=1.06 + ap+0.15(clip), peak-norm 0.6
donor transcription distance: donor f0 median=345.31Hz vs control f0 median=325.71Hz -> 1.011 semitone
=== exit=0 elapsed=35s ===
```

観測: score が要求する目標ビンは 57–69（さくら音域が概ねドナーの実演域と重なる）に
すべて収まり、**`n_extended_lookup=0`** — 拡張検索（隣接ビン借用）は一度も発生しなかった。
ドナー(vocadito_2)の実声域とさくら score のピッチ要求がよく重なっていたため、
疎ビン問題は今回は顕在化しなかった。ただし無声/無音フレーム 250 件（4917 中）は
直前有声テンプレートの carry-forward で埋めている（設計判断。ビン境界を跨がない
簡便策だが、有声→無声の遷移で本来のスペクトル減衰と異なる形状が残る副作用があり得る
— 後述 sanity check では聴感評価は行っていない=波形統計のみ確認）。

sanity check（NaN・peak）:
```
f1b_template_neutral.wav        24000Hz 590040samp peak=0.6000 nan=False rms=0.1282
f1b_template_dark.wav           24000Hz 590040samp peak=0.6000 nan=False rms=0.1772
f1b_template_bright_breathy.wav 24000Hz 590040samp peak=0.6000 nan=False rms=0.1367
f1b_donor_excerpt.wav           24000Hz 240000samp peak=0.7920 nan=False rms=0.1184
```
異常値・NaN なし。決定論: 乱数不使用（median/畳み込み/線形補間のみ、seed 概念なし）。

ドナー転写距離（f0 中央値の半音差）: donor 345.31Hz vs さくら score 325.71Hz
→ **+1.011 semitone**（Part1 の候補選定時の粗い推定 +0.77 semitone とは、44.1kHz直接分析
 vs 24kHz resample後分析・母集団フレーム数の違いにより若干の差。いずれも 1 半音未満で
 「かなり近い」という結論自体は変わらない）。

## Part 4 — 参照素材と計測

### 4.1 ドナー抜粋

`f1b_donor_excerpt.wav`（24kHz, 冒頭10秒, 240000 samples）は Part3 の
`glue_template.py` 内で donor 24kHz 版から直接切り出し・書き出し済み（Part3 ログ参照）。

### 4.2 帯域計測

```
$ python3 voice_genesis/foundry/scripts/measure_bands.py \
    f1b_template_neutral.wav f1b_template_dark.wav f1b_template_bright_breathy.wav \
    f1b_donor_excerpt.wav --out f1b_bands.json
```
exit=0, elapsed=3s

生 JSON は `f1b_bands.json`（sha256 込み、4件）。

**観測（正直な記録）**: donor 抜粋の `b1k_3k`（0.383）が合成3種（0.012–0.039）より
桁違いに高い。ドナー実声は 1k–3kHz 帯にかなりのエネルギーを持つのに対し、
テンプレート合成音はこの帯域が明らかに痩せている。CheapTrick 包絡の median 集約
（有声フレームの中央値を取る）と 250 フレームの unvoiced carry-forward が
高域エネルギーを均してしまっている可能性、または amp envelope 乗算（振幅包絡は
score 側の粗い正弦波状エンベロープであり、donor 自体の子音バーストなど非定常成分を
持たない）が寄与している可能性がある。**これは F1a で判明した「手設計包絡は声に
聴こえない」という律速をテンプレートが完全には破れていない可能性を示す定量的傍証**
（採否判定はしない。耳判定のための素材として記録するのみ）。

## Round 2（NN フレーム選択）

**設計判定の背景**: Round 1 の半音ビン median プーリングは、母音ごとに位置が
異なる F2 帯のフォルマントを打ち消し、`b1k_3k` を実ドナー 0.383 → 0.012–0.039 に
潰したと推定された。median 集約をやめ、ドナーの有声フレーム列そのもの
（`f0_d[i]`, `sp_d[i]`, `ap_d[i]`）を保持し、ターゲット各フレームに対して
**貪欲連続性選択**（直前選択の i+1 が 1 半音以内ならそれを採用、だめなら全有声
フレームから距離最小へジャンプ、同率は index 最小=決定論）で実フレームを直接
引く方式に差し替えた。

実装: `OUT/glue_template_nn.py`（271 行、`glue_template.py` から改変）。
テンプレートバンク構築（semitone ビン median 集約）を廃し、donor の
`pw.harvest` / `pw.cheaptrick` / `pw.d4c` 出力フレーム列をそのまま保持する
`analyze_donor()` に置換。選択ロジックは `select_nn_sequence()`。無声ターゲット
フレームの carry-forward・時間方向 3 フレーム移動平均・Genome 変形（freq_warp /
spectral_tilt / ap 底上げ）は Round 1 と同一演算子を再利用。

```
$ python3 glue_template_nn.py    (timeout=180000, elapsed<10s)
donor loaded: 844993 samples @ 24000Hz (35.208s), resampled from 44100Hz via resample_poly(80/147)
donor analyzed: n_frames=7042 voiced_frames=5863 (no binning/pooling — raw frame sequence retained)
control loaded: total_samples=590000 (24.583s) sr=24000, voiced_frac=0.9492
control f0 median (voiced) = 325.71 Hz
target frames: n_frames=4917 n_unvoiced_carry=250 n_initial=1 n_continuity=4201 n_jump=465 (continuity_threshold=1.0 semitone)
jump rate among voiced target frames = 0.0996 (465/4667)
wrote f1b_nn_neutral.wav: 590040 samples (24.585s), peak-normalized to 0.6
wrote f1b_nn_dark.wav: 590040 samples (24.585s), formant_scale=0.94 + tilt=-2dB/oct, peak-norm 0.6
wrote f1b_nn_bright_breathy.wav: 590040 samples (24.585s), formant_scale=1.06 + ap+0.15(clip), peak-norm 0.6
donor transcription distance: donor f0 median=345.31Hz vs control f0 median=325.71Hz -> 1.011 semitone
=== exit=0 ===
```

sanity check（NaN・peak）:
```
f1b_nn_neutral.wav                  24000Hz 590040samp peak=0.6000 nan=False rms=0.0931
f1b_nn_dark.wav                     24000Hz 590040samp peak=0.6000 nan=False rms=0.1005
f1b_nn_bright_breathy.wav           24000Hz 590040samp peak=0.6000 nan=False rms=0.0907
```
異常値・NaN なし。決定論: 乱数不使用（連続性チェック・argmin・移動平均・線形補間のみ）。
`np.argmin` の同率タイブレークは index 昇順（既定動作）で確定するため再現性は
Round 1 と同水準。

### 帯域計測（`OUT/f1b_bands_nn.json`）

```
$ python3 voice_genesis/foundry/scripts/measure_bands.py \
    f1b_nn_neutral.wav f1b_nn_dark.wav f1b_nn_bright_breathy.wav \
    f1b_donor_excerpt.wav --out f1b_bands_nn.json
exit=0
```

| 出力 | b0_500 | b1k_3k | b3k_5k | b5k_8k | HNR(dB) | b1k_3k比(対ドナー0.383) | ジャンプ回数 | グルー行数 |
|---|---|---|---|---|---|---|---|---|
| f1b_nn_neutral.wav | 0.1196 | **0.0663** | 0.0076 | 0.0003 | 10.06 | ×0.173 | 465 | 271 |
| f1b_nn_dark.wav | 0.2087 | 0.0314 | 0.0008 | 0.0001 | 10.08 | ×0.082 | 465 | 271 |
| f1b_nn_bright_breathy.wav | 0.1302 | 0.0647 | 0.0088 | 0.0005 | 9.08 | ×0.169 | 465 | 271 |
| f1b_donor_excerpt.wav（参照, 実声, 再掲） | 0.1244 | 0.3832 | 0.0106 | 0.0011 | 9.62 | ×1.000（自己参照） | — | — |

Round 1 との比較（`b1k_3k`, 同一 neutral/dark/bright_breathy 順）:

| 系統 | neutral | dark | bright_breathy |
|---|---|---|---|
| Round 1 (median プーリング) | 0.027 | 0.012 | 0.039 |
| Round 2 (NN フレーム選択) | 0.0663 | 0.0314 | 0.0647 |
| 倍率 (R2/R1) | ×2.46 | ×2.62 | ×1.66 |

**所見（正直な記録・判定はしない）**:

- `b1k_3k` は 3 出力とも Round 1 比で 1.7–2.6 倍に改善した。median プーリングが
  F2 帯フォルマントを打ち消していたという設計判定の仮説と方向は整合する。
- ただし **成功基準の目安（>0.1）には 3 出力とも未到達**（0.031–0.066、ドナーの
  17–8% 水準）。ドナー同オーダーへの回復は部分的に留まる。
- ジャンプ回数 465/4667（有声ターゲットフレームの 9.96%）。約 9 割は連続性選択
  （i+1 継続）で賄えており、貪欲連続性は概ね機能している。ジャンプは主にドナー
  実演の音域（bin 57–69 台、Round 1 ログ参照）とターゲット score の要求ピッチが
  乖離する箇所、および無声区間直後（`last_sel` 凍結からの再開）で発生している
  と推定されるが、ジャンプ位置と `b1k_3k` 不足の直接的因果は本ラウンドでは
  分離していない。
- `n_unvoiced_carry=250` は Round 1 と完全一致（同一 control npz・同一無声判定の
  ため当然）。carry-forward による高域エネルギー平滑化の寄与は Round 1/2 で
  変わらず残っており、`b1k_3k` 不足の残存要因の一つとして依然疑いが残る
  （本ラウンドでは carry-forward 自体には手を入れていない）。
- HNR は Round 1（9.3–10.4dB）とほぼ同水準（9.08–10.08dB）で、有声性の質は
  維持されたまま `b1k_3k` のみ改善した — median プーリングが主犯という設計判定
  の見立てと矛盾しない結果。

## Round 3 前診断（設計側）

- D1: ドナー抜粋の WORLD 往復（自身の f0/sp/ap）は b1k_3k 0.427 を保つ = 配管は無実
- D2: f0 を 325.71Hz 定数に差し替えても 0.409 = 定数 f0 も無実
- D3b: 抜粋の 260–400Hz 有声フレームの包絡比 (1k-3k)/(500-1k) median 0.346 = 素材も健全
- glue の sp 処理（パワー領域・tilt 10^(dB/10)・interp warp）は目視で健全
- 残る主容疑 H1: **NN 選択がフルクリップ（5863 フレーム）から引いた結果、325Hz 近傍の
  暗い区間（ハミング/閉母音）に選択が集中**した

## Round 3（明るさフロア付き NN 選択）

### 手順1: H1 の定量確認（`analyze_h1.py`）

`glue_template_nn.py` の `analyze_donor()` / `select_nn_sequence()` をそのまま
呼び出し、選択結果 `sel_idx_seq` を再現（Round 2 と同一乱数不使用ロジックのため
決定論的に同一選択が得られる）。env_ratio = Σsp[1k-3k]/Σsp[500Hz-1k]（CheapTrick
フレームのビン和、n_bins=513 @ sr=24000）を各分布で計算。

```
$ python3 analyze_h1.py
n_bins=513 sr=24000
[full-clip voiced] {n=5863, median=0.2362, p10=0.0250, p90=3.8869}
[260-400Hz voiced] {n=4951, median=0.1878, p10=0.0227, p90=2.2529}
select stats: n_frames=4917 n_unvoiced_carry=250 n_initial=1 n_continuity=4201 n_jump=465
n_selected_instances(with multiplicity)=4667 n_unique_donor_frames_used=1270
[NN-selected, with multiplicity] {n=4667, median=0.1107, p10=0.0140, p90=6.5057}
[NN-selected, unique donor frames] {n=1270, median=0.0810, p10=0.0131, p90=1.1250}
=== exit=0 ===
```

**選択フレーム集合の env_ratio 分布**: median 0.1107（多重度あり, 出力フレーム
単位）は、フルクリップ有声フレーム median 0.2362 の **約 47%**、260–400Hz 帯
median 0.1878 の **約 59%** に留まる。unique ドナーフレーム基準（1270/5863 =
21.7% のみが実際に使われている）では median 0.0810 とさらに低い — NN 選択が
ドナー全体のごく一部の、しかも相対的に暗いフレーム群を繰り返し再利用している
ことを示す。

**時間帯集中（1 秒ビン, 多重度重み付け, 上位 5）**:

| 秒レンジ | 選択回数 | 全体比 | f0 中央値(Hz) | env_ratio 中央値 |
|---|---|---|---|---|
| 12–13s | 864 | 18.51% | 293.85 | 0.0697 |
| 17–18s | 661 | 14.16% | 222.78 | 4.3385 |
| 7–8s | 606 | 12.98% | 329.81 | 0.0374 |
| 15–16s | 488 | 10.46% | 348.32 | 0.0470 |
| 2–3s | 388 | 8.31% | 438.11 | 49.9039 |

上位 5 区間中 3 区間（12–13s / 7–8s / 15–16s、合計 41.95%）は f0 が 293–348Hz
（さくら score の要求域とほぼ重なる）でありながら env_ratio が 0.037–0.070 と
フルクリップ median の 1/3–1/6 という顕著な暗さを示す。逆に 17–18s と 2–3s は
env_ratio が 4.3・49.9 と極端に明るい外れ値区間で、選択の二極化（暗い主要区間 +
少数の極端に明るいスパイク）が見える。**H1 は定量的に支持される**: NN 選択は
ドナー全体の分布よりも有意に暗い部分集合に偏り、特に score の主要ピッチ域
（293–348Hz）で暗いフレームへの集中が強い。

### 手順2: 明るさフロア付き NN 選択（`glue_template_nn_v2.py`）

各ドナー有声フレームの env_ratio を前計算し、候補集合を
「env_ratio ≥ フルクリップ有声フレームの p40 値」に制限した上で、Round 2 と
同じ貪欲連続性 NN（i+1 優先・±1半音・外れたら argmin |Δlog2 f0|・決定論
tie-break）を適用。フロア候補が空の場合はフロアなし全有声候補へフォールバック
（回数記録、0 件だった）。

```
$ python3 glue_template_nn_v2.py
donor loaded: 844993 samples @ 24000Hz (35.208s), resampled from 44100Hz via resample_poly(80/147)
donor analyzed: n_frames=7042 voiced_frames=5863 (no binning/pooling — raw frame sequence retained)
control loaded: total_samples=590000 (24.583s) sr=24000, voiced_frac=0.9492
control f0 median (voiced) = 325.71 Hz
floor: percentile=40.0 val=0.1425 n_floor_candidates=3518/5863 (0.600)
target frames: n_frames=4917 n_unvoiced_carry=250 n_initial=1 n_continuity=3778 n_jump=888 n_floor_empty_fallback=0 (continuity_threshold=1.0 semitone)
jump rate among voiced target frames = 0.1903 (888/4667)
wrote f1b_nnv2_neutral.wav: 590040 samples (24.585s), peak-normalized to 0.6
wrote f1b_nnv2_bright_breathy.wav: 590040 samples (24.585s), formant_scale=1.06 + ap+0.15(clip), peak-norm 0.6
donor transcription distance: donor f0 median=345.31Hz vs control f0 median=325.71Hz -> 1.011 semitone
=== exit=0 ===
```

フロア p40 = env_ratio 0.1425（3518/5863 = 60.0% のフレームが候補として残存）。
フロア導入によりジャンプ率が Round 2 の 9.96%（465/4667）から **19.03%
（888/4667）** へほぼ倍増（continuity 継続先 i+1 がフロアを外れて弾かれる
ケースが増えた分、当然の副作用）。フロア候補が空になったフレームは 0 件
（p40 という緩めの閾値では候補が常に十分数残る）。

### 帯域計測（`f1b_bands_nnv2.json`）

```
$ python3 voice_genesis/foundry/scripts/measure_bands.py \
    f1b_nnv2_neutral.wav f1b_nnv2_bright_breathy.wav f1b_donor_excerpt.wav \
    --out f1b_bands_nnv2.json
exit=0
```

| 出力 | b0_500 | b500_1k | b1k_3k | b3k_5k | HNR(dB) | b1k_3k比(対ドナー0.3832) | フォールバック回数 | ジャンプ回数 |
|---|---|---|---|---|---|---|---|---|
| f1b_nnv2_neutral.wav | 0.0956 | 0.6073 | **0.2858** | 0.0104 | 9.11 | ×0.746 | 0 | 888 |
| f1b_nnv2_bright_breathy.wav | 0.1085 | 0.6516 | **0.2240** | 0.0143 | 8.09 | ×0.585 | 0 | 888 |
| f1b_donor_excerpt.wav（参照, 実声, 再掲） | 0.1244 | 0.4805（参考） | 0.3832 | 0.0106 | 9.62 | ×1.000（自己参照） | — | — |

Round 1/2 との比較（`b1k_3k`, neutral / bright_breathy）:

| 系統 | neutral | bright_breathy |
|---|---|---|
| Round 1 (median プーリング) | 0.027 | 0.039 |
| Round 2 (NN, フロアなし) | 0.0663 | 0.0647 |
| Round 3 (NN, 明るさフロア p40) | **0.2858** | **0.2240** |
| 倍率 (R3/R2) | ×4.31 | ×3.46 |
| 倍率 (R3/R1) | ×10.6 | ×5.74 |

**所見（正直な記録・判定はしない）**:

- `b1k_3k` は Round 2 比で neutral 4.31 倍・bright_breathy 3.46 倍に跳躍し、
  Round1 開始時の成功基準の目安（>0.1）を **両出力とも突破**（0.224–0.286）。
  ドナー抜粋 0.383 に対しては neutral 74.6%・bright_breathy 58.5% まで接近した。
- H1（NN 選択が暗い区間に偏っていた）を修正するだけでここまで改善したことから、
  Round 1→2→3 を通じて律速要因は「集約方式（median pooling）→選択の代表性
  （暗い区間偏重）」の順に特定・解消されたと解釈できる。glue 配管・f0・素材
  （D1/D2/D3b）はいずれも無実という設計側診断と整合する。
- 副作用: ジャンプ率が 9.96%→19.03% に倍増した。ジャンプはフレーム間の
  スペクトル不連続（音色のワブル・ぶつ切れ感）を生みうる操作のため、明るさ
  改善とワブルリスクのトレードオフが発生している可能性がある（本ラウンドでは
  聴感評価は行っていない=波形統計のみ）。
- HNR は neutral 9.11dB（ドナー 9.62dB とほぼ同水準）、bright_breathy 8.09dB
  （ap 底上げによる意図的な効果、Round1/2 と同傾向）で、有声性の質は
  b1k_3k 改善と両立している。
- b500_1k がドナー 0.4805 に対し合成 2 種は 0.607–0.652 とやや過多（分母帯域自体も
  フロア選択でやや変化した可能性）。b1k_3k/b500_1k の絶対配分がドナーと完全一致した
  わけではなく、「大幅改善したが完全再現ではない」という位置づけで記録する。
- **H1 裁定: 支持（反証されず）**。選択済み sp_seq を直接 synthesize に渡す
  ablation（手順5）は、H1 が定量的に支持され修正で b1k_3k が閾値を突破したため
  実施不要と判断し、打ち切る。
