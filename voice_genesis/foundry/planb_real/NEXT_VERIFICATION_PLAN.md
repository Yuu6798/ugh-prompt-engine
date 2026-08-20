# Genome PoC 核心 6 項目 — 到達状況

- User 裁定（2026-08-20）: 本タスクの核心は次の 6 項目。**これ以外は測らないし直さない**
- 記録: [`REAL_CORPUS_POC_RECORD.md`](REAL_CORPUS_POC_RECORD.md) /
  スコアカード: [`results/core_six_scorecard.json`](results/core_six_scorecard.json)

---

## 到達状況 — 6/6 達成

| # | 核心 | 判定 | 根拠 |
|---|---|---|---|
| 1 | R0–R4 が Ritsu 系 Identity として残るか | **達成** | 耳 Q1=yes「R 系はみな同じ歌い手に聞こえる」/ G6 PASS（全 8 ペア × 全 5 段で出力は PJS より Ritsu に近い）/ G4 PASS |
| 2 | PJS 由来 Performance を入れると挙動が変わるか | **達成** | 4 軸が可動。probe ノートの F0 標準偏差 2.07 → 22.43 Hz、利得 52.6 dB p2p、split MAE 9.73 → 0.90 ms |
| 3 | その違いを人間が知覚できるか | **達成** | 耳 Q2=yes「最後の『り』で音程の揺れ方に違いがある」 |
| 4 | 単に PJS の声質へ寄っただけではないか | **達成** | 耳 Q4=no「声は寄っていない」/ G4 PASS（donor テクスチャへの単調接近なし） |
| 5 | どの Performance 成分を入れたか追跡できるか | **達成** | 尺を固定した対で意図側を直接比較 → 漏れゼロ |
| 6 | 同条件で再現できるか | **達成** | G1 PASS（8 ペア × R0–R4 のサンプル列 sha256 が再計算で一致） |

---

## 核心 5 の詰め（本日確定）

G3（介入の直交性）が FAIL していたが、**介入が漏れているのか軸が交絡しているのか**が
未確認だった。両者は意味が違う。

そこで **意図側（合成器が置いた値）** を直接比較した。ただし尺を変えると、
コア保存型 warp（頭尾は実尺・母音コアのみ伸縮）により
「正規化時間上の同じ位置」が原音の別の場所を指すため、
尺が変わる rung は他成分と比較できない（実測: R2 で energy が 17.5 dB 動くが、
gain を掛けたからではなく参照点がずれたため）。

よって **尺を固定した対** で測った:

| 検査 | f0 [cent] | duration [frame] | energy [dB] | release [dB] | 漏れ |
|---|---|---|---|---|---|
| f0 のみ | 1.8〜39.7 | **0** | **0.0** | **0.0** | **なし** |
| energy のみ | **0.0** | **0** | 12.0 | 3.8〜12.0 | **なし**（release 窓も動くが gain は unit 全体に掛かるので想定内） |
| release のみ | **0.0** | **0** | **0.0** | 0〜32.7 | **なし** |

尺そのものは frame 比較が原理的に成立しない。gain / release 演算の**非適用**は
`pb_compose.compose` のコード経路（`toggles.energy` / `toggles.release` が False の
とき当該演算に入らない）と `pr_gates` の tripwire（合成器が実際に読んだ属性集合を実測）
で担保する。

**結論: 成分は追跡できる。G3 の FAIL は結果側（再解析値）の軸交絡であって
介入の漏れではない。** 登録ゲート G3 の判定は凍結どおり書き換えない。

生データ = [`results/attribution_intent_vs_result.json`](results/attribution_intent_vs_result.json)

---

## 残す注記（核心の判定は変えない）

- 核心 3 の標本は薄い（判定者 1 名・probe 1 ペア・介入は 1 音節のみ）。
  **判定は yes で確定**しており、これは精度の注記であって未達項目ではない
- 登録ゲートのうち G3 / G7 / G-ear は FAIL のまま。ただし
  - G3 = 上記のとおり軸の交絡（核心 5 は別途達成）
  - G7 / G-ear Q3 = terminal /ri/ の改善。**これは VoiceGenesis / run 8 の未解決**
    であって本タスクの核心ではない

## スコープ外（測らない・直さない）

- terminal /ri/ 破綻の解決、および run 7 checkpoint の取得・測定
- 原音忠実度・雑音軸・`AP_SCALE` の最適化
- 介入範囲の拡張（note → phrase）、`R0n` の追加、TRF 軸の再定義

## run 8 へ渡す所見（本タスクの副産物・こちらでは追わない）

事前登録の TRF 主軸 `nasal_gain_db` を Ritsu **原音**へ当てると terminal /ri/ で
+5.654 / +5.853 dB を示す（R0 とほぼ同値）。この軸は「モデルの破綻」ではなく
「人間の歌唱に元からある終端の性質」を読む。`failure_present = 絶対値 >= 1.0 dB`
という門は自然な人間の歌唱を「破綻あり」と判定する。
run 8 で TRF 軸を登録するなら、絶対値ではなく**基準からの逸脱**として定義することを勧める。

生データ = [`results/anchor_reference_check.json`](results/anchor_reference_check.json)
