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

## 追加の手順

1. probe / checker を編集する前の版を
   `git show <commit>:<path> > snapshots/<stem>_<sha8>.py` で凍結する
2. `index.json` の `snapshots` に登録する（`sha256` は 64 桁・`measured_results`
   はその版が生んだ結果 JSON の全数）
3. live 版が未実測なら `live_unmeasured` に `reason` / `revalidation` を書く
4. 再実測して結果 JSON を作り直したら、`live_unmeasured` の該当行を消す。
   どの結果からも参照されなくなった snapshot は削除する（孤児検査が落ちる）
