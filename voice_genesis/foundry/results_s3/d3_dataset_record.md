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
