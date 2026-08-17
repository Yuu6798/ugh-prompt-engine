# D3 データセット変換記録 — S3 Phase B closeout（2026-08-17）

- 位置づけ: `DESIGN_S3_backfill.md` §2 Phase B の出口記録（AC「D3 データセットが
  build_dataset ゲートを通過し実効分数が record に記録される」の充足）
- 上流の正本: 生成会計 = `d3_manifest.json`（事前登録殻）+
  `d3_manifest_results.json`（40 セル実測）/ 変換器 = `../s1_dataprep/convert_d3.py`

## 1. 変換実行

- 入力: B2 生成の 40 セル（wav 24kHz + timing CSV。ペア 40/40・orphan 0）
- コマンド: `python convert_d3.py --render-dir <40セル dir> --out-dir <dataset>`
- 出力: `transcriptions.csv` 40 行（name,ph_seq,ph_dur,ph_num,note_seq,note_dur）+
  `wavs/` 40 本（44.1kHz mono s16）
- 内訳: note 行 680 / rest(SP) 行 260

## 2. build_dataset 3 ゲート（実測）

| ゲート | 結果 |
|---|---|
| `validate_speaker` | problems = []（PASS） |
| `check_ph_dur_duration` | warnings = []（PASS） |
| `check_note_dur_consistency` | violations = []（PASS） |

**GATE RESULT: PASS (0 issue)** — 40 行全件。

## 3. 実効分数

| 指標 | 実測 |
|---|---|
| ph_dur 合計 | 1200.50 s = **20.008 分** |
| 有声実長（SP 除く） | 1135.50 s = **18.925 分** |
| SP（ブレス休符） | 65.00 s |

参照点: D1 (PJS) = 26.86 分 / D2 (リツ VCV) = 33.4 分。D3 は目標帯
（20–30 分・PJS と同オーダー）の下端。増量が必要になった場合の増設ノブは
seed 追加（マニフェスト改版=新殻の事前登録から）。

## 4. 再現手順（コーパスは非コミット・全再生成可能）

1. リツ voicebank 取得: s1_dataprep README の pin（zip `88c7b3ef…`）で検証
2. `d3_manifest.json` の 40 セル（4 score × 10 seed）を render
   （`--timing-out` 付き）→ 各 wav/csv sha256 が `d3_manifest_results.json` と
   一致することを確認（seed=11 トリップワイヤ含む）
3. `convert_d3.py` で変換 → 本記録 §2 のゲート 3 本を再実行

## 5. run 4 への引き継ぎ

- 本データセットは **spk=ritsu 側への追加**（話者別データ分離の維持。
  `DESIGN_S3_backfill.md` §2.4）
- binarize（openvpi/DiffSinger）は GPU 実行環境（クロー）側の分担
- 残る Phase B 関連 Open Question: 促音っ（§7 Q5）

## 6. pin 改訂記録（2026-08-17・SIMD レベル drift）

### 6.1 発見

pin 起源環境（AVX-512 = numpy dispatch `X86_V4`）で生成した `d3_manifest_results.json`
の wav_sha256 を AVX2 止まりホスト（`X86_V3`）で再検証したところ、
40 セル中 `d3_sustain` seed=701 のみが不一致になった。差分は int16 PCM
1,524,000 サンプル中 **1 サンプル・1 LSB**（index 989445、`-1`）のみで、
他 39 セル + tripwire 2 件（`sakura`/`umi` seed=11）+ 全 timing CSV +
全 spec は完全一致していた。原因は numpy の AVX-512（`X86_V4`）dispatch
カーネルの加算順序差（SIMD reduction の典型パターン）。詳細な反復記録・
before/after `show_config` 実測・全 40 セル表 = セッション scratchpad
`simd_pin_experiment/RESULT.md`（本 doc への転記が本節）。

### 6.2 tripwire の盲点

`sakura`/`umi` seed=11 の tripwire 照合はこのクラスの環境差を検出
**できない**（両ホストで一致したまま）。SIMD dispatch ドリフトは
tripwire を素通りし、40 セル全数照合で初めて表面化する種類の環境差である。

### 6.3 X86_V3 を正基準に採用した理由

- **AVX2 ホストでの決定論は独立に成立**（`NPY_DISABLE_CPU_FEATURES=X86_V4`
  固定で 2 独立 run + warm cache run がバイト完全同一）。
- X86_V3（AVX2 相当）は AVX-512 ホスト・AVX2 ホストの双方で到達可能な
  「最小公倍数」の dispatch レベルである（AVX2 止まりのハードでは
  そもそも `X86_V4` カーネルを選べないため、逆方向〔AVX2 ホストで
  AVX-512 出力を再現〕は原理的に不可能。片方向のみ再現可能な X86_V3 側を
  正とするほかない）。
- `NPY_DISABLE_CPU_FEATURES=X86_V4` を render 前に一律設定する運用規約を
  `S3_RUN4_RUNBOOK.md` §2.2 に追加した（受け入れゲート・silent no-op の罠
  込み）。

### 6.4 pin 値の対応表

| ファイル | フィールド | 旧値（`X86_V4` 起源） | 新値（`X86_V3` 起源） |
|---|---|---|---|
| `d3_manifest_results.json` | `d3_sustain` seed=701 `wav_sha256` / `log_output_sha256` | `88cae387a28ae0a6fb0a6a59daf7786c9b4e72ec8847be6fa3f13cdbf8200002` | `8b4f8a6095f3181d2a4cb97cf531cc915beeb65e3ceb89a02ebe4940f837fa6a` |
| `run4_dataset_pins.json` | `d3.wav_sha256["d3_sustain_seed701.wav"]`（44.1kHz 変換後） | `4cb56a3a33431764e7236a70e2eddc3d1f65ba261dc3b5a0b1cb5cba1dd2ce76` | `b37b3ee0c67a9f4a52263c0482c15865d4c3dc2abb7b74db61b687a1006cc153` |

他 39 セル・`transcriptions_csv_sha256`・`wav_total_seconds_measured`
（1200.3s）・`timing_csv_sha256` は全て pin 旧値と不変（X86_V3 環境での
`convert_d3.py` 実測により確認済み）。
