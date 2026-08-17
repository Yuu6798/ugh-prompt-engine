# 第 3 話者（User）データセット変換記録 — S3 Phase C closeout（2026-08-17）

- 位置づけ: `DESIGN_S3_backfill.md` §3 Phase C の出口記録（AC「convert_user.py が動き、
  User 音源の実効分数・音素被覆が record に記録される」の充足）
- 上流の正本: 台帳 = `../recording_kit/user_donor_ledger.json`（17 本・User 確認済み
  カード対応）/ 変換器 = `../s1_dataprep/convert_user.py`

## 1. 音素化の接地（C1→C2 の設計転換）

C1 はトイ音素化器 `phoneme_jp` を使用し T2 10 枚が全除外となったが、
**訓練の符号化に実際に使われる正本辞書**（リツ公式 DiffSinger 配布
`dsdur/dsdict.yaml`・617 エントリ・zip sha256 `5c7b8c32…` = S1_GPU_RUNBOOK 素材 3 の
pin と一致・runbook §377 で従来運用を確認）を取得・pin 照合し、C2 で T2 の音素化を
辞書グラフェム lookup（最長一致・っ→`cl`・カタカナ単独形ッ/ンはひらがな表記へ
正規化）に置換した。**辞書に実在する記号のみ emit** の原則を維持。

## 2. 変換結果（17 本の内訳）

| tier | 採用 | 除外 | 除外理由（exclusions.json に全記録） |
|---|---|---|---|
| T0 (UC-001/002) | 2 | 0 | — |
| T1 (UC-003〜007) | 5 | 0 | — |
| T2 (UC-008〜017) | 8 | 2 | UC-010: ヴ系グラフェムが正本辞書に非対応（代用禁止）/ UC-009: 実収録の有声ラン数 2 < 期待フレーズ数 4 で併合規則が適用不能（fail-closed） |

計 **15/17 採用**。アラインメント手法は tier 別
（T1=RMS 3 段 + f0 中央値 MIDI / T2=有声ラン×モーラ比例 + 最小ギャップ併合規則 /
T0=フレーズギャップ×スコア拍配分）— 精密アラインメントではない旨を明記
（AC は「spk_embed が立つ」であり第 3 頂点の方向弁別が判定点）。

## 3. 実効分数・音素被覆（実測）

| 指標 | 実測 |
|---|---|
| ph_dur 合計 | 233.395 s = **3.890 分** |
| 有声実長（SP 除く） | 171.88 s = **2.865 分** |
| 音素被覆 | **33 種**（C1 の 15 種 → cl・外来語 A/I/E/O・b/d/z/p 系・ch/j/sh/ts/ky/gy が追加） |

設計想定（5–6 分）より薄いが、想定どおり「単独で強いアンカー」は要求しない。
不足時の増収ノブ = UC-009 の再録（フレーズ間で息継ぎを入れる指定）+ T3 任意カード。

## 4. 検証（実測）

- build_dataset 3 ゲート（validate_speaker / check_ph_dur_duration /
  check_note_dur_consistency）: 実 15 本出力で **0 issue**
- 決定論: 2 回実行で transcriptions.csv / wavs / exclusions.json バイト一致
- **T0/T1 の 7 本は C1 出力とバイト完全一致**（音素化置換の影響が T2 に閉じている
  ことの回帰実証 + dsdict 内容に対する不変性の単体テスト）
- ruff clean / 新テスト 42 本 + 絞り込み 697 passed

## 5. run 4 への引き継ぎ

- 本データセットは **spk=user の第 3 話者**として投入（話者別データ分離）
- User 音源の権利 = 本人の声を本人が使う（recording_kit/README §8）
- UC-009/010 の素材自体は台帳に記帳済みで失われない（将来の辞書拡張・再録で回収可能）
