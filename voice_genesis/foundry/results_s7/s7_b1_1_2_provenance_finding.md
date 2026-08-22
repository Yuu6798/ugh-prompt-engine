# 所見: 1.2 校正音源の親バッチが事前登録の pin と違う（2026-08-22 / PR #303 レビュー由来）

**発見の経緯**: PR #303 の自動レビューが「1.2 manifest が事前登録へ結び付いていない
（schema・事前登録 sha・条件集合の厳密一致・重複 ID を見ていない）」と指摘した。
1.0 の `real_render_source` と同じ検査を `source_12` へ実装したところ、
**4 検査のうち 3 つは通り、親 manifest の lineage 検査だけが落ちた**。
仮説の指摘ではなく、**既にコミット済みの成果物に実在する食い違い**である。

## 1. 事実

| | sha256 | 実体 |
|---|---|---|
| 1.2 事前登録が宣言する親 (`inherits.real_render_manifest`) | `bde66f3f…` | `results_s7/s7_b1_real_render_manifest.json` = **v2** バッチ |
| 1.2 manifest が実際に継承した親 (`extends.sha256`) | `36139bcd…` | `/home/user/s7work/out/b1_real_render_v3/…` = **v3** バッチ |

時系列（すべて 2026-08-22 UTC）:

```
06:14  コンテナ再構築からの復旧後、ONNX を再 export して v3 バッチをレンダ
07:57  1.2 を事前登録（親として **repo にコミット済みの v2 manifest** の sha を pin）
08:02  1.2 manifest を生成（`--manifest` に **ディスク上の v3** を渡した）
08:10  1.2 を測定 → 3/4 軸凍結・overall BLOCKED
```

v2 と v3 の差は **ONNX 再 export のノイズだけ**である（`peak_raw` / `rms_raw` が
1e-8 桁で相違、`samples_sha256` は 14 条件中 13 で不一致）。これは
[`s7_reproducibility_finding.md`](s7_reproducibility_finding.md) が既に記録した
「`samples_sha256` は再 export を跨がない / `reference_output` の再測定は ε 内で再現する」
という pin 意味論の階層そのものである。

## 2. 何が壊れていて、何が壊れていないか

**壊れている**: 宣言された由来と、測定された由来が**別物**である。
1.2 の事前登録は v2 を親と名乗っているが、測ったのは v3 由来の刺激である。
「事前登録に書いてある材料で測った」とは言えない。

**壊れていない**: 音そのものと、そこから出た判定。

- 差は 1e-8 桁であり、1.2 の各軸の ε（ms 軸 5–10 ms / 比軸 0.033）を大きく下回る
- 1.2 の結論（`terminal_mel_persistence` = UNAVAILABLE / voicing 3 軸 = 本番分解能成立 /
  overall = BLOCKED）は、この差では動かない
- Gate 1 = UNDETERMINED は**別の理由**（`calibration_scored` 10 < 12、陽性対照が
  §5-0 で除外された群にある）で決まっており、本所見とは独立である

## 3. 取った処置

1. `source_12` に 1.0 と同じ 4 段照合を実装した（schema / 1.2 事前登録 sha /
   親 manifest lineage / 条件集合の厳密一致 + 重複 ID 拒否）。**fail-closed のまま置く**
2. **事前登録は書き換えない**。`s7_b1_calibration_set_1_2.json` の `inherits` を
   v3 の sha へ直せば検査は通るが、それは**成果物に合うよう事前登録を後付けで
   合わせる**行為であり、B-1 が守ってきた順序規律（pin してから測る）を壊す
3. **1.2 の凍結値も書き換えない**（User 裁定 2026-08-22 (α)/(I)）
4. 結果として、**この検査は履歴上の 1.2 manifest に対しては落ちる**。それが正しい
   ふるまいである — 「v3 で測った値を v2 の pin で名乗る」ことを機械が拒否している

## 4. 再入するときの条件

1.2 を再測定する必要が生じた場合（Run 8 は CLOSED なので現時点では**無い**）:

- v3 バッチを親とする**新しい事前登録**を切る（1.2 を書き換えるのではなく）
- または v2 バッチを復元して測り直す（`provision.sh` + 再 export では v2 の
  `samples_sha256` は再現しないので、事実上は前者しか取れない）

## 5. 同型の 2 件目: `s7_0b_results.json` が現在の群 JSON から再生成できない

PR #303 レビュー第 2 巡で集計器に不変条件検査を入れたとき、ついでに
「コミット済みの 10 群から再集計したら同じ物が出るか」を実測した。**出なかった。**

| | 一致 |
|---|---|
| voicing 3 軸（`excess_tail_voiced_ms` / `release_after_score_boundary_ms` / `tail_f0_persistence`） | **max\|Δ\| = 0（360 セル全数）** |
| `terminal_mel_persistence` | max\|Δ\| = 1.43e-04（ε = 0.0387 の約 1/270） |
| `acoustic`（HNR / vowel drift） | max\|Δ\| = 2.7e-05 〜 1.9e-03 |
| `gate_eligible_groups` / `h0_summary` / `epsilon` / `in_gate` / `ringing_status` / `degenerate_axis` | **全一致** |
| `wav_sha256` / `samples_sha256` / `group_file_sha256` | 不一致 |

原因は §1 と同じで、コミット済みの `s7_0b_results.json` は**コンテナ消失前**の群
ファイルから集計したもの、コミット済みの `probe_0b_groups/*.json` は**復旧後の
再レンダ**である。**判定に使う量はすべて一致する**（Gate 会計・縮退判定・ε・H0 は
1 つも動かない）が、成果物どうしのバイト単位の系譜は繋がっていない。

これも書き換えない。`s7_0b_results.json` を再集計して上書きすれば sha は揃うが、
それは「凍結後に成果物を作り直す」ことであり、Run 8 = CLOSED の裁定に反する。

## 6. 一般化（次の系列への申し送り）

事前登録に外部成果物の sha を書くときは、**repo にコミットされたコピー**ではなく
**測定に渡す実体**を pin する。両者が同じである保証は無い（再 export・再レンダ・
コンテナ再構築のたびに割れる）。今回は「repo にある方を pin した」ことが原因である。

さらに一般に、**再生成できない成果物（ONNX・レンダ WAV・集計 JSON）は、生成した
その場で由来を記録しなければ後から言えない**。PR #303 第 2 巡で入れた
`s7_export_manifest.py`（ckpt → ONNX の束縛）と `validate_group_document`
（群 JSON の不変条件）は、この 2 件が示した穴を将来側で塞ぐためのものである。
**過去の run 8 成果物についてはこの記録が存在しない** — ONNX が pin 済み
checkpoint 由来であることは、機械照合可能な artifact ではなくセッション履歴に
よってしか裏付けられない。これは正直に書いておく。
