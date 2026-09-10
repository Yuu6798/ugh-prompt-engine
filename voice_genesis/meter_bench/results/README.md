# meter_bench/results

`python -m voice_genesis.meter_bench --meter <METER>` が書く現況表。meter ごと
1 ファイル、**最新のみ** commit する（履歴は git が持つ）。

- `claimable` は定数 `false`。**PASS は校正証拠ではない** — 「確定 campaign に
  計算資源を使ってよい」の一点だけを意味する。
- `verdict` = その meter の候補のうち少なくとも 1 つが全 case を通ったか。
- FAIL の JSON も隠さず commit する（リセット設計 v0 P1/P3/P4 の指示）。
