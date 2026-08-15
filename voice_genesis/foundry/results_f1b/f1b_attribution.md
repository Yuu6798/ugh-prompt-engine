# F1b ドナー素材 帰属・ライセンス記録

## データセット

- **vocadito**: A dataset of solo vocals with f0, note, and lyric annotations
- 著者: Bittner, Rachel M.; Bosch, Juan José; Rubinstein, David; Meseguer-Brocal,
  Gabriel; Ewert, Sebastian（Zenodo メタデータより。詳細は原論文
  "vocadito: A dataset of solo vocals with f0, note, and lyric annotations",
  Bittner et al., 2021 を参照）
- Zenodo record: **5578807** (<https://zenodo.org/records/5578807>)
- 配布ファイル: `vocadito.zip`, size=58492257 bytes, **md5 `dea40fd18f14d899643c4ba221b33a46`**
  （本リポ `docs/m2e_provisioning_runbook.md` §3 記載の既存 pin と完全一致を確認済み）
- ローカル DL の sha256: `e0d6b99d3f9c594afe5ae5c4d7bdacebe569e53b809e90b89d1c771c4f9990e3`
- **ライセンス: CC BY 4.0**（Creative Commons Attribution 4.0 International）

## 採用クリップ

- clip ID: **vocadito_2**（singer_id `S2`, language `Spanish`, metadata
  `average_pitch=65`（MIDI 相当のおおよその指標。実測は下記））
- 実測（`pyworld.harvest`, frame_period=5ms, 44.1kHz 原音, 有声フレーム = f0>0）:
  - f0 中央値: **344.9 Hz**
  - 有声率: **0.845**
  - さくら score f0 中央値 330Hz との半音差: **+0.77 semitone**（候補中最小）
- 選定手順: metadata `average_pitch` が MIDI 64（≈330Hz）に近い候補 15 クリップを
  抽出し、各々 `pyworld.harvest` で粗く f0 推定。有声 f0 中央値が 260–400Hz に入り
  かつ有声率が高いものの中から、330Hz に最も近い `vocadito_2` を採用
  （詳細は `select_donor.py` の出力、`f1b_raw.md` に転記）
- ファイル: `Audio/vocadito_2.wav`
  - **sha256: `8dcc99c3b08a9a5800b793e3d65cccfb4464961f15cf8ccde25bd4c8b853d519`**
  - **md5: `81c978f8c22d8155633417dda7490dc5`**
  - 本リポ pin `tests/fixtures/melody_bench/m2c_external_fixtures.yaml`
    の `vocadito_2.expected_audio_sha256` と **完全一致**を確認済み
  - 44100Hz, mono, 16bit PCM, 35.208s

## Identity quarantine 注記

単一ドナー（vocadito_2, singer S2）から抽出したスペクトル包絡テンプレートは、
そのドナーの音色の**準クローン**である。設計書 §8 の identity quarantine 対象と同型の
懸念が該当する。F1b はテンプレート路線が「声に聴こえるか」を実証する暫定措置であり、
量産・配布を前提とした音色ではない（生ログに同旨を明記）。

## 出典・再頒布ポリシー

- 本セッションでの利用は音響実験目的の一時展開（scratchpad 内、リポジトリへの
  commit なし）。CC BY 4.0 の要求に従い、上記に著者・データセット名・出典 URL・
  ライセンスを明記した。
