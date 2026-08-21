# Genome Architecture S3.5 — 実装ノート（人間負担軽量版）

**正本 = User 改訂指示「S3.5 修正指示 — 人間負担軽量版」（2026-08-21）。**
旧 `Perceptual Gene Gate Design Specification v1.0` の 32 問 ABX 案は**廃止**した。

**この文書は設計をしない。** 指示の各項がどのコードに落ちたかを示すだけ。
契約から逸脱する判断が必要になった場合は実装せず `status = BLOCKED` で停止する。

---

## 1. S3.5 が答える問い

> S3 で機械的に成立した 4 gene について、**人間が実際に差を聞き分けられるか**。

**統計的証明は目的にしない。** 耳判定はただ 1 つ —
**「X は A と B のどちらと同じですか？」**（`A` / `B` / `UNSURE`）。
自然さ・好み・品質・語尾破綻・改善度は質問しない。

S3.5 は S3 を再裁定しない。`S4_NOT_READY` は S3 FAIL を意味しない。

## 2. 旧 v1 から削除したもの

指示 12 のとおり、以下は**コードにも定数にも残していない**（テストが不在を検査）:

```text
4 pair × 2 repeat / 8 trial per gene / 32 trial total /
7-of-8 閾値 / binomial 確率による合格判定
```

## 3. 2 段階方式

| | 内容 | 問数 |
|---|---|---|
| **Stage 1** | F0 / Duration / Energy / Release 各 1 問（4 gene × 1 pair） | 4 |
| **Stage 2** | Stage 1 に**正解した gene だけ**・Stage 1 と別 pair かつ別 probe_kind | 最大 4 |

**人間の回答負担は最大 8 問**（`MAX_TOTAL_TRIALS = 8`）。

各問は `A` = B0 / `B` = gene-only（配置は blind key で決まる）、`X` は A か B。

## 4. pair 選択 — 人間は選ばない

`s35_spec.selection_hash` =
`SHA256("voicegenesis-s35-v1" + s3_results_sha256 + gene + pair_key)`。

- Stage 1 = hash 最小の pair
- Stage 2 = **Stage 1 と別 pair かつ別 probe_kind** のうち hash 最小

効果量・聞きやすさ・音質は**入力に含めない**（cherry-pick 禁止）。
候補は S3 で `SUPPORTED` の pair のみ。同じ S3 正本なら常に同じ選択になる。

### 別 context を「優先」ではなく「要件」にした理由（開示）

指示 3 は「異なる probe_kind を**優先**」だが、指示 5 は
「Stage 2 用の別 context が存在しない → `NOT_EVALUABLE_S35`」と定めており、
指示 7 の主張上限も「**異なる 2 文脈で**識別できた」まで。
3 つを整合させると、別 context は実質**要件**になる。
実装は要件として扱い、別 context が無い gene は `NOT_EVALUABLE_S35` にする
（同 context で代替しない）。

### Stage 2 の pair は Stage 1 の回答前に確定する

`prepare_stage1` が **両 stage の pair と正解を同時に決めて key に含め、
commitment を取る**。配布は Stage 1 のみ。これで「Stage 1 の結果を見てから
Stage 2 の pair を選ぶ」ができない（テストが commitment 不変を検査）。

## 5. gene 判定

```text
Stage 1 正解 AND Stage 2 正解        -> PERCEPTIBLE_CANDIDATE
どちらか不正解 / UNSURE               -> NOT_ESTABLISHED
Stage 2 用の別 context が存在しない    -> NOT_EVALUABLE_S35
blind 破壊 / SHA 不一致 / 回答欠落     -> INVALID
```

`UNSURE` は正答に数えない。**4 状態以外を作らない。**

## 6. S4 進行条件

```text
PERCEPTIBLE_CANDIDATE >= 2  -> S4_READY
それ未満                     -> S4_NOT_READY
```

## 7. 主張上限

> **少なくとも 2 つの Performance gene について、異なる 2 文脈で
> 単独介入差を人間が識別できた。**

まで。**統計的有意差・知覚閾値・完全独立は主張しない。**
「自然」「高品質」「改善」「歌唱技能を獲得」「Genome Architecture 完成」も言わない。

## 8. 音源

S3 canonical WAV のみ使用。**再生成禁止**。
normalize / gain / trim / fade / denoise / resample も禁止。
A/B/X は元 WAV の **byte copy** で、コピー前後の SHA 一致を検査する
（変わっていれば BLOCKED）。配布前に S3 正本記録との SHA 一致も確認する。

Energy gene が対象に入るため、**S3.5 側で音量正規化しない**。
再生側の自動ラウドネス正規化・EQ・空間オーディオはオフ、A/B/X 間で音量を触らない。

## 9. blind

| ファイル | 扱い |
|---|---|
| `results/answer_key.private.json` | **local-only**・`0600`・**commit しない** |
| `results/blind_manifest.json` | 公開可。`protocol_version` / `s3_results_sha256` / stage ごとの `trial_ids` と `audio_sha256` / `key_commitment` **だけ** |
| `results/key_reveal.json` | **全回答の凍結後にのみ**生成 |

- salt は 256-bit CSPRNG
- `key_commitment` = `SHA256(canonical_bytes(private key))`。正規形 bytes を
  そのままファイルに書くのでファイル SHA と一致する
- 配布名は `S1T01_A.wav` 形式のみ。gene 名・pair 名を含めない
- 各問終了時に正誤を表示しない。stage 単位でまとめて採点する
- UI に answer key を埋め込まない（trial_id だけ）
- Stage 2 の trial_id は配布するまで manifest に載せない（未配布の問題数を見せない）

manifest の非漏洩はテストで**構造ごと固定**して検査する
（sha256 hex に `f0` が偶然含まれるため部分文字列走査では判定できない）。

## 10. 実装フロー

```text
S3 正本確認 → Stage 1 の 4 問生成
                ↓ STOP（User 回答）
Stage 1 採点 → 正解 gene のみ Stage 2 生成
                ↓ STOP（User 回答）
最終判定 → S4_READY / S4_NOT_READY
                ↓ STOP
```

```bash
# Stage 1 生成
python voice_genesis/foundry/genome_s35/s35_prepare.py

# Stage 1 採点 → 進行 gene の確認
python voice_genesis/foundry/genome_s35/s35_score.py

# Stage 2 生成（進行 gene を指定）
python -c "import sys; sys.path.insert(0,'voice_genesis/foundry/genome_s35'); \
import s35_prepare as p, s35_score as s, s35_spec as sp; \
print(p.prepare_stage2(s.advancing_from_stage1(s.score_stage(sp.STAGE1))))"

# 最終記録
python voice_genesis/foundry/genome_s35/s35_report.py
```

終了コード: `0` = S4_READY / `1` = S4_NOT_READY / `3` = BLOCKED。

**User 回答前に採点フェーズへ進まない。S3.5 完了後に S4 へ自動で進まない。**

## 11. モジュール

```text
genome_s35/
├── DESIGN_GENOME_S35.md   この文書
├── s35_spec.py            凍結定数・verdict・pair 選択（I/O と音響処理を置かない）
├── s35_prepare.py         S3 検証 → 2 段階 plan → key/commitment → stage pack
├── s35_score.py           stage 採点 → 進行判定 → 最終 verdict → key reveal
├── s35_report.py          results/s35_results.json + results/S3_5_RECORD.md
├── tests/test_genome_s35.py
└── results/.gitignore     WAV・private key・回答を commit しない
```

`genome_s3` / `planb_real` / `planb` は read-only。S3 の問題は S3 側へ戻す。

## 12. 停止規則

以下で即停止し、`status` / `reason` / `affected_gene` / `required_action` **だけ**を出す。
合理的推測で突破しない。

S3 正本が PASS でない / S3 正本 SHA を固定できない / canonical WAV 欠落 /
WAV SHA 不一致 / Stage 1 を作れる gene が無い / private key が既に露出 /
回答ファイルが部分的・語彙外・stage 不一致 / Stage 1 通過 gene があるのに
Stage 2 pack が無い / trial 総数が 8 を超える。

## 13. 範囲外の観測（記録のみ・修正しない）

`s35_report.OUT_OF_SCOPE_OBSERVATIONS` に載せて記録にだけ残す。

- **X が A/B と byte-identical なので、聴取者が 3 ファイルを sha256 で
  突き合わせれば聴かずに正答できる。** commitment が守るのは「実験者が回答後に
  正解を変えないこと」であって聴取者の自己申告ではない
- **1 gene あたり 2 問なので、偶然の一致でも 1/4 で `PERCEPTIBLE_CANDIDATE` に
  到達する。** 本 Gate は統計的証明ではなく S4 へ進むための最小確認である
  （指示の「統計的証明は目的にしない」に沿った設計上の帰結であって、欠陥ではない）
