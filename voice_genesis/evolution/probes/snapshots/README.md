# probes/snapshots — 実測を生んだ版の凍結

`vgl0_*` の結果 JSON は `pins.probe_script.sha256` / `checker_script.sha256` で
**判定を出したコード**を束縛する。`tests/test_vgl0_probe_result_pins.py` は
その sha が repo 内の実体と一致することを検査し、「probe を編集したのに結果を
再生成していない」fixture drift を落とす。

問題は、**再実測が repo 外の資産に律速される**ことだった。実測入力は run 6 の
40K ONNX（checkpoint 由来・数百 MB）と canon/vocoder 一式で、いずれも repo に
入らない。この状態で「編集したら必ず再実測」を唯一の逃げ道にすると、
**probe の欠陥修正そのものが不可能**になる（7 巡目の 3 件がこれに当たった）。

そこで結果を生んだ版をここへバイト同一で凍結する:

- 結果 JSON が pin する sha は、**live の実装** または **本ディレクトリの
  snapshot** のどちらかで必ず実体に解決できる（テストが強制）
- snapshot は `index.json` に登録する。登録漏れ・孤児・sha 不一致はテストで落ちる
- live 実装が **どの結果からも pin されていない**場合、`index.json` の
  `live_unmeasured` に理由と再検証条件を書くことを強制する
  = 「今の実装は未実測である」ことが黙って隠れない

**この仕組みは再実測の免除ではない**。免除しているのは「再実測できるまで
コードを直せない」という詰みだけで、`live_unmeasured` に載っている限り
live 実装の実測証拠は無い。結果を根拠に新しい主張を立てるときは、その版で
測り直すこと。

## 時限措置としての枠（User 承認 2026-08-20・PR #291）

**これは恒久パターンではない。** 採用の条件は 3 つで、いずれも `index.json` の
`policy` に宣言され、テストが宣言の存在と内容を検査する:

1. **期限** — run 6 の 40K ONNX + canon + vocoder が再用意でき次第、live 実装で
   正本を再生成する。凍結はその時点で不要になる
2. **1 スクリプト 1 世代** — 2 世代目を作りたくなったという事実自体が
   「再実測を構造的に先送りしている」合図。凍結ではなく再実測を選ぶ
   （台帳検査が 2 世代目を弾く）
3. **置換計画** — CI が履歴を取得できるようになったら（`actions/checkout` の
   `fetch-depth` を深くできたら）、凍結コピーを廃して **pin された sha を
   named commit の git blob と照合する方式**へ置換する。重複バイトがゼロになる

### 出口は機械強制（運用者の善意に依存しない）

再実測すると、次の連鎖で凍結物が**自動的に退場を強制される**:

```
再実測 → 正本が live sha を pin
      → snapshot の measured_results 帰属が崩れる（帰属検査が落ちる）
      → 名前を外す → measured_results が空 → 「1 件も登録されていない」で落ちる
      → 行ごと削除 → ディレクトリと index の突き合わせで実体も削除
```

つまり「消し忘れて残り続ける」経路が無い。放置すると CI が赤くなる。

### 既知のコスト（`policy.known_costs` に機械可読で保持）

- 検査の比重が「実測の健全さ」から「台帳の整合」へ寄る
- **本 PR のレビュー指摘 18 件のうち約 8 件は本機構自体の穴だった** — 直した
  欠陥より多くを持ち込んだ側面がある
- 凍結コピーは lint 対象外・リファクタ対象外
- 未実測の live 実装が正本に居座る（`live_unmeasured` で可視化するが、
  ラベルは読み飛ばされうる）

## 追加の手順

1. probe / checker を編集する前の版を
   `git show <commit>:<path> > snapshots/<stem>_<sha8>.py` で凍結する
2. `index.json` の `snapshots` に登録する（`sha256` は 64 桁・`measured_results`
   はその版が生んだ結果 JSON の全数）
3. live 版が未実測なら `live_unmeasured` に `reason` / `revalidation` を書く
4. 再実測して結果 JSON を作り直したら、`live_unmeasured` の該当行を消す。
   どの結果からも参照されなくなった snapshot は削除する（孤児検査が落ちる）
