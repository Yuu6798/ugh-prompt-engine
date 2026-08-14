# Underspecification Log v6 — v0.6 改修設計メモ（第 6 サイクル・最終）実装

`v06_design_memo.md`（§A〜C）を実装する過程で決定できなかった箇所を記録する。

| # | 対応メモ§ | 欠落内容 | 自分が置いた仮定 |
|---|---|---|---|
| 1 | §A | 「アンカー ±30% の局所窓」が座標降下の反復ごとに現在位置基準（複利的に動きうる）か、凍結アンカー自体を基準に毎回固定するかが明記されていない。 | **凍結アンカーそのものを毎回の探索中心として固定**した（`measure_v4._run_coordinate_descent` の `search_center=peaks[i][0]`（現在位置基準、複利的にドリフトしうる）をそのまま流用せず、`measure_v6.py` に独自の座標降下ループを実装し `measure_v4._fit_single_peak` を `search_center=<凍結アンカー>` で毎回呼び出す）。理由: 「計器凍結」の趣旨が「構造を固定して測定間で比較可能にする」ことにあるため、探索窓自体が反復ごとに移動しうる設計は凍結の趣旨に反すると判断した。 |
| 2 | §A | 凍結構造決定時の σ_meas 反復（3反復）レンダも凍結構造を使うと明記されているが、凍結構造自体を決定する「中央 sweep 点のレンダ」自体は sigma_meas の 3 反復とは別（sigma_meas 反復は全て frozen 構造使用、構造決定用のレンダは 1 回のみ・非反復）という理解でよいかが曖昧。 | 中央 sweep 点のレンダは 1 回のみ行い（seed オフセットなし、genome 標準のまま）、そこから凍結構造を決定した。σ_meas の 3 反復（seed オフセット付き）は全てその凍結構造で再フィットする（構造決定そのものには使わない）。 |
| 3 | §B | 凍結構造をレポートへ記録する形式（フィールド名等）が指定されていない。 | probe ごとに `n_peaks` / `anchor_peaks_hz` / 決定に使った v4 の `fit_mode`（`source_fit_mode_at_mid`）/ 決定時の残差RMS・使用倍音数を `frozen_structures_per_probe` として `grip_report_v6.json` に記録した。 |

## 実測結果の要点（memo の予測との対比）

### spectral_tilt: 予測どおり改善し PASS に到達

memo §B の予測「spectral_tilt はモデル選択率経由の結合が消えるため
E(declared) が低下するはず」は**的中した**。v5 では dominant_side が
`formant_centroid`（E=18.38、intended自身より大きい）だったが、凍結後は
dominant_side が `periodicity`（E=1.11程度、後述）に入れ替わり、
grip=4.357 で**免除表を使わずに no_exemption gate を直接通過**した。
凍結によりモデル選択（1peak/2peak切替）由来のノイズが完全に除去され、
spectral_tilt 軸の本質的な結合（tilt→formant_centroidの直接的な物理結合）
がそもそも見かけほど大きくなかったことが判明した——v4/v5 で観測された
巨大な E(formant_centroid) は主にモデル選択の不連続性由来のアーティ
ファクトであり、真の物理結合ではなかったことを示す。

### formant_scale: 予測に反し direction_consistency がさらに悪化

memo §B の予測「formant_scale は dir 回復のみで PASS 圏（他条件は v5 で
成立済み）」は**外れた**。v5 の dir=0.70 → v6 の dir=**0.60** へむしろ
悪化した。免除表の数値条件（grip_declared=4.354>=3.0, sign 5/5,
E(declared)=1.762<=2.177）は全て満たすが、direction_consistency の
90% 要件だけがなお未達（かつ悪化）のため FINAL FAIL のまま。

診断: モデル選択の不連続性（v5 で特定した根因）は凍結により完全に除去
された（`fit_mode_distribution` が probe ごとに sweep 全体で単一値に
固定されていることを確認済み）。にもかかわらず direction_consistency が
改善しなかったことから、**formant_scale 軸の非単調性の主要因はモデル
選択の切替ではなく、固定モデル構造のもとでの純粋な測定ノイズ（±30%局所
窓内でのピーク位置推定自体のばらつき）である**と結論づけた。5 probe中
G2（2ピークモデルで sweep 全域を通した唯一の probe）は比較的安定して
いたが、C3/E3/G3/C4（いずれも 1peak フォールバック）で sweep 途中の
符号反転が複数箇所観測された。これは v0.4/v0.5 を通じて追いかけてきた
「モデル切替」問題とは異なる、より基礎的な計器分解能の限界であり、本
メモが想定した対策（凍結）の対象外の現象である。

## 結論（design memo v06 §B: 本サイクルが最終試行）

memo の指示どおり、これ以上のレンダラ・推定器・joint fit の変更は行わず、
実測結果をそのまま受け入れて終了する。formant_scale は fail-closed の
open issue として `run_summary_v6.md` に正直に記録する。
