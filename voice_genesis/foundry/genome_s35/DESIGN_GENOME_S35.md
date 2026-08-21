# Genome Architecture S3.5 — 実装ノート

`VoiceGenesis Genome Architecture S3.5 — Perceptual Gene Gate Design Specification v1.0`
（User 起草・凍結）と `Claude Execution Instruction v1.0` の実装対応表。

**この文書は設計をしない。** 各節がどのコードに落ちたかを示すだけ。
契約から逸脱する判断が必要になった場合は実装せず `status = BLOCKED` で停止する（§25）。

---

## 1. S3.5 が答える問い（§0）

> **B0 と gene-only 出力の違いを、人間がブラインドで識別できるか？**

耳判定はただ 1 つ — **X は A と B のどちらと同じか**。
自然さ・良し悪し・好み・品質改善・語尾破綻・ノイズ量・原音への近さは
**測らない**（§2）。自由記述は Gate に使わない。

S3.5 は S3 を再裁定しない。`S4_NOT_READY` は S3 FAIL を意味しない（§14）。

## 2. 入力 — S3 正本を read-only で消費（§3, §4）

| 項目 | 値の出所 |
|---|---|
| 判定・pair 集合 | `genome_s3/results/s3_results.json` |
| 音声 | `genome_s3/results/wav/<pair>/<cond>.wav`（B0 / F / D / E / R のみ） |
| pin | `s3_results_sha256` = **parse したのと同じ bytes** の digest |

事前 Gate（`s35_prepare.gate_s3`）: `overall == PASS` / `supported_gene_count >= 2` /
`reproducibility` 記録が存在。各 WAV は `actual_sha256 == 記録 SHA` を確認する
（`verify_audio`）。**不一致・欠落は BLOCKED で、S3.5 側では再生成しない。**

`genome_s3` / `planb_real` / `planb` は read-only（§18）。S3 の問題は S3 側へ戻す。

## 3. 対象 gene と pair 選択（§5, §6）

候補は S3 で `SUPPORTED` の gene、pair も S3 で `SUPPORTED` のもののみ。
`UNSUPPORTED` / `NOT_EVALUABLE` の pair は知覚試験へ昇格させない。

`s35_spec.selection_hash` = `SHA256("voicegenesis-s35-v1" + s3_results_sha256 + gene + pair_key)`。
**効果量・metric 値・聞きやすさは入力に含めない** ので、結果を見てからの
入れ替えができない。同じ S3 正本なら同じ選択になる。

`s35_spec.select_pairs` の手順（§6.1 そのまま）:

1. `selection_hash` 昇順に並べる
2. 異なる `probe_kind` から 1 件ずつ取り、**最低 2 context** を確保
3. 残りを全候補の hash 順で埋める
4. 合計 4 distinct pair

満たせない gene は `NOT_EVALUABLE_S35`（**閾値も pair 数も緩めない**）。

### §6.1 手順 2 の読み方（開示）

「まず異なる `probe_kind` から 1 件ずつ、最低 2 context を確保」は
(A)「`MIN_CONTEXTS`（=2）に達した時点で打ち切る」とも
(B)「存在する全 `probe_kind` から 1 件ずつ取る」とも読める。
**実装は (A)** — `最低2` が確保対象であり、`MIN_CONTEXTS` が凍結定数だから。

**今回の S3 正本では両解釈の選択結果が 4 gene すべてで完全一致**するため、
この読み方の差は本走行の pair 選択に影響しない（実測で確認済み）。

## 4. ABX pack（§7, §8, §10）→ `s35_prepare.py`

- 1 gene = 4 pair × 2 trial = **8 trial**。4 gene で最大 32 trial
- 同じ pair の 2 trial は **trial 1: X=B0 / trial 2: X=gene**、A/B 配置は反転
- どちらの極性で始めるか・出す順は **blind salt から導出**（`_bit` / `_order_key`）
- **gene を跨いで全 trial をシャッフル**する（gene ごとに固めない）
- 配布ファイル名は `T001_A.wav` / `T001_B.wav` / `T001_X.wav` のみ。
  gene 名・pair_key・条件名を含めない
- A/B/X は元 WAV の **byte copy**。コピー前後の SHA 一致を確認し、
  変わっていれば BLOCKED（正規化・変換の混入検出）
- **禁止**: rerender / normalize / trim / fade / gain / resample / denoise /
  `AP_SCALE` 変更 / WORLD 変更 / gene metric 再計算

## 5. blind key と事前 commitment（§9）

| ファイル | 扱い |
|---|---|
| `results/answer_key.private.json` | **local-only**・`0600`・回答前も回答後も commit しない |
| `results/blind_manifest.json` | 公開可。`protocol_version` / `s3_results_sha256` / `trial_ids` / `audio_sha256` / `key_commitment` **だけ** |
| `results/key_reveal.json` | **回答凍結後にのみ**生成 |

- salt は 256-bit CSPRNG（`secrets.token_bytes(32)`）
- `key_commitment` = `SHA256(canonical_bytes(answer_key.private.json))`。
  正規形（`sort_keys` + 最小 separator + UTF-8）の bytes を**そのままファイルに書く**ので、
  ファイルの SHA と canonical bytes の SHA が一致する
- commitment 不一致 → 全 gene `INVALID`（回答後の正解変更を禁止する装置）
- 既存 private key があるときは上書きせず BLOCKED（blind の破壊にあたる）

blind manifest に gene 名・pair_key・A/B の正体・X 正解は入れない。
テストは部分文字列走査ではなく**構造ごと固定**して検査する
（sha256 hex に `f0` が偶然含まれるため）。

## 6. 聴取 UI（§9 実行指示 / §11）

`results/abx/index.html` は最小構成 — Trial ID / A・B・X 再生 / `A`・`B`・`UNSURE` のみ。
音質評価欄・「自然」「改善」「好み」の設問は作らない。正誤は途中表示しない。
**answer key を JS へ埋め込まない**（trial_id のみ）。1 クリップ最大
`MAX_REPLAYS_PER_CLIP`（=3）回。UI が負担なら
`results/abx/answer_sheet.template.json` + `results/abx/audio/` だけでもよい。

Energy gene を知覚対象に含むため **S3.5 側で音量正規化しない**。
再生側の自動ラウドネス正規化・EQ・空間オーディオはオフを推奨（§11）。

## 7. 採点（§12, §13, §14）→ `s35_score.py`

**回答凍結後だけ実行する。** 採点前に正解を表示しない。

- 回答は `A` / `B` / `UNSURE` のみ。**`UNSURE` は正答に数えない**
- 部分回答・語彙外・manifest に無い trial・schema 不正はすべて BLOCKED（§25）
- gene verdict は 4 状態のみ:

```text
commitment 不一致 / audio 不一致 / trial 数 != 8   -> INVALID
4 pair でない / context < 2                        -> NOT_EVALUABLE_S35
correct >= 7                                       -> PERCEPTIBLE
それ以外                                            -> NOT_ESTABLISHED
```

- S4 Gate: `PERCEPTIBLE genes >= 2` → `S4_READY`、未満は `S4_NOT_READY`

`7/8` はランダム回答での到達確率が 9/256 ≈ 3.52%（§13）。
これは心理物理統計の証明ではなく、**S4 へ進むための事前登録された工学的受入基準**（§15）。

## 8. 出力（§22, §23）→ `s35_report.py`

`results/s35_results.json`（§23 schema）と `results/S3_5_RECORD.md` を
**束として原子的に**公開する（temp へ書き切ってから rename、途中失敗は巻き戻し）。

記録する pin: `s3_results_sha256` / `protocol_version` / `blind_manifest_sha256` /
`answers_sha256` / `key_commitment` / `key_reveal_sha256` / 選択 pair と context /
gene ごとの `correct/8` と verdict / `S4_READY | S4_NOT_READY`。

raw WAV は commit しない（`results/.gitignore`）。private key も commit しない。

## 9. 実行フェーズ（実行指示 §14）

```text
Phase A  S3 input validation
Phase B  pair selection
Phase C  blind ABX pack 生成
         ↓ STOP — User へ聴取 pack を渡す
Phase D  answers freeze
Phase E  key reveal + scoring
Phase F  S3.5 record
         ↓ STOP
```

```bash
# Phase A–C
python voice_genesis/foundry/genome_s35/s35_prepare.py

# Phase D–F（User 回答が返ってから）
python voice_genesis/foundry/genome_s35/s35_report.py results/answers.json
```

終了コード: `0` = S4_READY / `1` = S4_NOT_READY / `3` = BLOCKED。

**User 回答前に採点フェーズへ進まない。S3.5 完了後に S4 へ自動で進まない。**

## 10. 停止規則（§25）

以下で即停止し、`status` / `reason` / `affected_gene` / `required_action` **だけ**を出す。
合理的推測で突破しない。

S3 正本が PASS でない / S3 正本 SHA を固定できない / canonical WAV 欠落 /
WAV SHA 不一致 / SUPPORTED pair が 4 未満 / context が 2 未満 /
blind key が既に露出 / 回答ファイルが部分的 / 回答凍結後に変更 /
protocol の変更が必要。

## 11. 範囲外（実行指示 §12, §16 / 設計書 §16, §27）

**やらない**: 新 metric 追加 / 閾値変更 / pair cherry-pick / gene 追加 /
dose 変更 / S3.5 内部での S3 rerun / 品質修正 / ノイズ修正 / 語尾修正 /
WORLD 修正 / `AP_SCALE` 調整 / 回答後の pair 交換 / 6/8 への緩和 / S4 実装開始。

再試験禁止（§16）: 結果を見たあとに pair を入れ替える・聞こえやすい pair だけ使う・
dose を増やす・音量を補正する・再生回数を増やす・閾値を 6/8 へ下げる、は禁止。
必要なら **`S3.5-v2` を新規事前登録して別実験**として行い、v1 結果は残す。

主張禁止（§27）: S3.5 PASS でも「gene を人間が意味分類できる」「4 gene が知覚上
完全独立」「自然」「高品質」「改善」「歌唱技能を獲得」「Genome Architecture 完成」
とは言わない。

## 12. 範囲外の観測（記録のみ・修正しない）

実行指示 §16「別の問題を見つけても記録だけにする」に従い、実装では手を付けない。

- **X が A / B と byte-identical（§7）なので、聴取者が 3 ファイルを sha256 で
  突き合わせれば聴かずに正答できる。** commitment 方式が守るのは
  「実験者が回答後に正解を変えないこと」であって、聴取者の自己申告ではない。
  プロトコル変更は §12 / §16 で禁止のため、`s35_report.OUT_OF_SCOPE_OBSERVATIONS`
  に載せて記録にのみ残す。
