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

## 13. 成果物完全性の境界宣言（2026-08-21・User 裁定）

レビュー 3 巡で「確定した記録・commit した約束を、後から来る操作が黙って
書き換えられる」型の指摘を 11 件採用したが、**塞ぐたびに隣の経路が出る**
状態になった。実害基準で線を引き、以降はこの線で採否する。

### 採用し続けるもの

| 型 | 判定基準 |
|---|---|
| **A. 誤判定** | **正直な入力**から誤った verdict / S4 gate が出る |
| **B. データ破壊** | **正常な操作**で確定済みの成果物が壊れる・消える |
| **C. 下流汚染** | S4 等の下流が誤った事実（`perceptually_established` 等）を受け取る |

### 終端宣言するもの

> **`results/` に書き込み権限を持つ主体が、意図的に成果物を偽造する経路への
> 追加防御は、以降 scope 外とする。**

**理由**: その主体は `blind_manifest.json` や `answer_key.private.json` を書ける
のと同じ権限で、**`s35_results.json` を直接書ける**。入力側の検査をいくら重ねても
最終成果物の偽造は止まらないので、これは防御ではなく装飾になる。手を入れるほど
「守っているつもりの範囲」が実態より広く見える分、かえって有害。

**この残余を実際に受け止めている仕組み**（コード側ではない）:

- **commitment を回答前に push 済み**（`7ffb568` = Stage 1 出題前）。git 履歴と
  GitHub の push 時刻は `results/` への write 権限では書き換えられない
- **記録そのものが commit・レビュー対象**。差し替えれば diff に出る
- **判定は 1 人の聴取に依存する実験**であり、そもそも聴取者の自己申告を
  暗号で保証する設計ではない（§13 の out-of-scope 観測に既出）

### 既採用分の扱い

第 3 巡の「manifest の stage 束縛」は本境界では (D) 側だが、実装済み・テスト済み・
無害なので**そのまま残す**（撤去コストの方が高い）。以降の同型指摘は本節を引いて
見送る。

### 全数掃討して終端したファミリー（2026-08-21・第 8 巡）

`AGENTS.md` §3「同型穴はファミリー単位で全数掃討して終端宣言」に従い、
以下 2 ファミリーは**個別指摘ではなく全数**を塞いだ。以降、同型の指摘は
本節を引いて見送る（新しい**別型**の経路なら §13 の A/B/C 基準で採否する）。

| ファミリー | 掃討内容 | 固定テスト |
|---|---|---|
| **壊れた JSON で BLOCKED が出せない** | S3.5 が読む JSON を**全数** `prep.json_object()` 経由にし、根が object でなければ `S35Stop`。対象 = S3 正本 / blind manifest / private key / 回答 / 既存 key_reveal。`load_finalized()` のみ非 dict を `None` 扱い | `test_no_json_loads_outside_the_shared_gate` が `json.loads` の出現箇所を helper と `load_finalized` の 2 つに固定 |
| **Stage 1 失敗で private key が孤児化** | key 書き込みを temp + `chmod 0600` + `os.replace` の原子操作にし、**書き込みを含む** Stage 1 全体を 1 つの巻き戻し scope に入れる。失敗時は呼び出し前の状態へ復元 | `test_private_key_write_failure_leaves_nothing` / `test_stage1_failure_leaves_no_orphan_private_key` |

### 第 8 巡の終端宣言は範囲不足だった（2026-08-21・第 9 巡で訂正）

第 8 巡で「壊れた JSON で BLOCKED が出せない」を全数掃討したと宣言したが、
**掃討したのは JSON の根と器の形だけ**で、`answers` の**各値の型**は見て
いなかった。`["A"]` / `{"answer":"A"}` は `check_complete()` の集合判定に
届いて unhashable の `TypeError` になり、同じく BLOCKED 記録が出せない。
器と中身は同じ穴の別の深さであって別ファミリーではないので、第 9 巡で
値の型検査を足して掃討を完了させた（`test_non_string_answer_value_is_blocked`）。

**終端宣言は「実際に全数を潰したこと」が前提であり、宣言したこと自体を
根拠に同型指摘を見送ってはならない。** 宣言の範囲を超えた同型指摘が来たら、
宣言が不足していたと認めて掃討を完了させる。

### commitment の第三者検証（2026-08-21・第 9 巡）

`key_reveal.json` は `key_sha256 == key_commitment` を主張するだけで、
`canonical_bytes` に含まれる `salt_hex` / `s3_results_sha256` を載せていな
かった。private key は恒久 gitignore なので、**公開物だけでは commitment を
再計算できず**、`commitment_verified: true` が検証不能な自己申告になる。
commitment 方式の目的は第三者検証なので、これは目的未達にあたる。

対応として reveal に `key_preimage`（private key の全鍵）を載せた。
開示後なので秘匿価値はゼロ（全正解は `trials` で既に公開済み）。
`test_reveal_alone_reproduces_the_commitment` が、private key を一切参照せず
reveal だけから `sha256(canonical_bytes(key_preimage)) == key_commitment` を
再計算できることを検査する。

**本 session の確定済み記録には遡及適用しない。** 適用すると
`key_reveal_sha256` が変わり、`conflicting_with_finalized()` が正しく拒否する
（第 9 巡で実測: 差分は `key_reveal_sha256` の 1 フィールドのみ、verdict・
`answers_sha256`・`key_commitment`・`blind_manifest_sha256` は全一致）。
コード自身が出す `required_action` が「確定済み session の結果はそのまま残す。
やり直すなら新規に事前登録した別 session として実施する」である以上、
**成果物を良くするためであっても確定記録を書き換えない。**

本 session の commitment を検証する手段は現時点で以下に限られる（開示）:

- `7ffb568`（Stage 1 出題前）で push 済みの `blind_manifest.json` に
  `key_commitment` が入っており、GitHub の push 時刻は `results/` の write
  権限では書き換えられない
- 原像の再計算には手元の `answer_key.private.json` が要る。これは
  第 9 巡で実測し `sha256(canonical_bytes(key)) == d4fadda8…` = 回答前に
  push した値と一致することを確認した。ただし**第三者はこれを追試できない**
  ので、本 session に限りこの一致は attestation であって proof ではない

### 第 10 巡 = bot レビュー上限。未対応 1 件を User へ渡す（2026-08-21）

`CLAUDE.md`「bot レビュー対応の運用」の**上限 10 ラウンドに到達**した。
第 10 巡の 3 件のうち 2 件（manifest 入れ子形状 / publish の改行翻訳）は
採用済み。残る 1 件は**私の裁量では決められない**ので境界宣言として渡す。

**未対応 = 確定済み `key_reveal.json` に `key_preimage` が無いこと。**

指摘は正しい。本 session の `key_reveal.json` は `commitment_verified: true`
と書いているが、clean checkout の第三者はその digest を再計算できない
（`salt_hex` / `s3_results_sha256` が公開物に無い）。第 9 巡で実装した
`key_preimage` は**次の session から**効き、本 session には効かない。

**私が決めなかった理由**: 解決策が 2 つあり、どちらも「実験記録の意味」を
変えるため、実験の所有者の判断が要る。

| 案 | 内容 | 代償 |
|---|---|---|
| **A. 新 session として再実施** | protocol どおり事前登録し直して回す | 聴取をやり直す。本記録はそのまま残る |
| **B. 原像を別ファイルで併載** | 凍結物に触れず `key_preimage` を sidecar として公開 | `results/.gitignore` の「blind key は回答後も commit しない」と衝突する（このルール自体は本 session で私が書いたもの） |
| **C. 現状維持** | §13 の開示のみ | 本 session の commitment は attestation 止まり |

**現状は C。** どれを採るかは User の判断とし、勝手に A/B へ動かさない。
確定記録を私の判断で書き換えないという §13 の線は、成果物を良くする方向でも
維持する（第 9 巡で同じ判断をしている）。

### 形状ゲートの全数掃討を完了した（2026-08-21・第 11 巡）

**第 8 巡・第 10 巡の 2 度、範囲不足のまま「掃討完了」と宣言した。** 8 巡は
根と器だけ、10 巡は manifest の入れ子だけで、いずれも「同型穴を全数潰した」
とは言えない状態で終端宣言を書いた。第 11 巡で残り全部を一度に潰す。

S3.5 が外部から読む JSON は 5 つ。それぞれ **根 / 器 / 値 / 入れ子**の
4 階層すべてにゲートを置いた。棚卸しは
`test_every_external_json_has_a_shape_gate` が固定する。

| ファイル | 根 | 入れ子の絞り点 |
|---|---|---|
| `s3_results.json` | `json_object()` | `gate_s3()` が `overall` / `reproducibility` |
| `blind_manifest.json` | `json_object()` | `manifest_stage_block()` が stages / block / trial_ids / audio_sha256 |
| `answer_key.private.json` | `json_object()` | `load_private_key()` が `trials` / `plans`（絞り点なので下流 8 箇所を一度に守る） |
| `answers_stage*.json` | `json_object()` | `load_answers()` が器と**各値の型** |
| `key_reveal.json`（既存） | `json_object()` | `require_mapping()` が `revealed_after_answers_sha256` |

**教訓（今後の終端宣言に適用する）**: 「1 ファイル分を直したので同型は終わり」
は終端宣言として無効。**同じ形の参照が他のファイル・他の階層に残っていないか
grep で数え、残数 0 を示してから**宣言する。示せないなら宣言しない。

### 見送るときの手順

指摘が (A)(B)(C) のどれにも当たらないと判断したら、**resolve せず**本節への
リンクを付けて残置する（`AGENTS.md` §3-3）。**新しい具体経路が (A)(B)(C) に
該当するなら、巡数に関わらず採用する** — 本宣言は実害基準を上書きしない。

## 14. 範囲外の観測（記録のみ・修正しない）

`s35_report.OUT_OF_SCOPE_OBSERVATIONS` に載せて記録にだけ残す。

- **X が A/B と byte-identical なので、聴取者が 3 ファイルを sha256 で
  突き合わせれば聴かずに正答できる。** commitment が守るのは「実験者が回答後に
  正解を変えないこと」であって聴取者の自己申告ではない
- **1 gene あたり 2 問なので、偶然の一致でも 1/4 で `PERCEPTIBLE_CANDIDATE` に
  到達する。** 本 Gate は統計的証明ではなく S4 へ進むための最小確認である
  （指示の「統計的証明は目的にしない」に沿った設計上の帰結であって、欠陥ではない）
