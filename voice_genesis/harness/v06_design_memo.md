# v0.6 改修設計メモ — 第 6 サイクル（sweep 内計器凍結）

対象: formant_scale（残存阻害 = dir 0.70）/ spectral_tilt（残存阻害 =
モデル選択率経由の結合）。v5 で判明した共通根因 = **sweep 系列の途中で
joint fit のモデル構造（1peak/2peak 選択・ピーク位置）が切り替わり、
特徴量の定義が不連続に変わる**こと。

## A. 原理: 計器凍結（frozen instrument within measurement series）

sweep は「Genome 軸に対する特徴量の微分」を測る操作である。測定系列の
途中で計器の構造的構成が変わると微分は定義できない。よって:

- 各 (axis, probe) の sweep 系列につき、**系列中央 sweep 点（index 2）で
  一度だけ** v4 の既存規則（BIC + F2/F1 妥当性ゲート）によりモデル構造を
  決定する: モデル次数（1peak/2peak）・アンカーピーク位置。
- 系列内の全 sweep 点（および §A-4 の σ_meas 反復レンダ）は、この凍結
  構造で再フィットする: 次数固定、ピーク探索はアンカー位置 ±30% の
  局所窓のみ、モデル選択は再実行しない。
- 凍結内容（chosen order / anchor peaks）を probe ごとにレポートへ記録。
- band 割当・免除表規約・gate 条件は v5 のまま（probe = 低音域 suite）。

## B. 実装と判定

- `measure_v4.py` は変更禁止。凍結対応の拡張は `measure_v6.py`（v4 の
  joint fit を import し、fixed_structure 引数付き再フィット関数を追加）
  として新設。`vt3_v6.py` で formant_scale / spectral_tilt の 2 軸のみ
  再測（breathiness / vibrato_depth は re-state）。
- 期待: formant_scale は dir 回復のみで PASS 圏（他条件は v5 で成立済み）。
  spectral_tilt はモデル選択率経由の結合が消えるため E(declared) が
  低下するはず — 従属条件（<= 0.5×E(intended)）を満たすかを実測。
- 未達が残る場合は無理に通さず記録して終了。**本サイクルが grip 完成の
  最終試行**であり、以降は結果を受け入れて試作品 1 号の統合へ移る
  （未達軸は fail-closed の open issue として受け入れ判定に正直に記載）。

## C. 成果物

- `measure_v6.py` / `vt3_v6.py`
- `results_v6/grip_report_v6.json` / `run_summary_v6.md`（gate 判定表・
  凍結構造の記録・免除表・v0.2→v6 全推移・非退行確認）/ `underspec_log_v6.md`
