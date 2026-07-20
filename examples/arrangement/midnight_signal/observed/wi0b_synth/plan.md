# WI0-b 実推論計測 事前登録

Registered at (UTC): 2026-07-20T15:15:24Z

## 目的

WI0-b = melody センサー（basic-pitch）の転写精度を、真値が既知の決定論的レンダー
（Composition Score performer 出力）に対して実測し、WI2 v0 の意味層軸として
melody を採用するかどうかを判断するための一次データを得る。

lyrics（歌詞転写）は、歌入り + 歌詞 pin 済みの音源という条件を満たす素材が
手元にないため素材律速であり、精度主張はしない。instrumental wav に対する
lyrics anchor の境界挙動（no_speech 系の挙動・転写出力の形）のみを記録する。

## 採否ルール（結果を見る前に固定・逸脱禁止）

決定論 performer による faithful レンダー（transpose 0 の最易ケース）で観測した
`pitch_lcs_ratio`:

- `pitch_lcs_ratio >= 0.8` → melody 軸を WI2 v0 採用候補とする
- `pitch_lcs_ratio < 0.8` → v0 から除外する（理由を記録する）

`interval_lcs_ratio` は副次記録とし、採否キーには使わない。

## 決定論確認手順

(a) レンダリング対象の wav を別プロセスで 2 回レンダリングし、sha256 が一致することを確認する
(b) 同一 wav に対する `svprpe observe` を 2 回実行し、出力 report のバイト列が一致することを確認する

## lyrics 境界スモーク

instrumental wav（melody 計測で使った同じ wav）への lyrics anchor 込み転写出力を
verbatim 記録する。精度主張はしない（境界挙動の記録のみ）。

## 実行規律

- コミット・push は一切しない（計測のみ。fixture 化は判読後に別途）
- すべてフォアグラウンド同期実行。timeout は Bash ツールの timeout パラメータで明示
- run_in_background / `&` / nohup は使わない
