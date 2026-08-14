# Underspecification Log v5 — v0.5 改修設計メモ（第 5 サイクル）実装

`v05_design_memo.md`（§A〜C）を実装する過程で決定できなかった箇所、および
実装中に発見した新たな知見を記録する。

| # | 対応メモ§ | 欠落内容 | 自分が置いた仮定 |
|---|---|---|---|
| 1 | §A | 低音域 suite の MIDI 番号が「G2,C3,E3,G3,C4」という音名のみで具体化されていない。 | 標準 MIDI 番号（C4=60 基準）で G2=43, C3=48, E3=52, G3=55, C4=60 とした（f0=98.0/130.8/164.8/196.0/261.6Hz、memo記載の「f0 98-262Hz」と一致）。 |
| 2 | §C | 「新規 measure 実装は不要のはず」との予測に反し実際に不要だったかどうかの確認手順。 | `measure_v4.py` を無改変・無変更のまま `vt3_v5.py` から呼び出すのみで実装完了した。`measure_v5.py` は作成していない（memo の想定どおり、追加実装は不要だった）。 |
| 3 | §A / §B | band 宣言・免除表の出力先（gate 判定と別データ構造にするか統合するか）が明記されていない。 | `grip_report_v5.json` トップレベルに `band_assignment`（band・probes・rationale）を独立したセクションとして持たせ、各軸の結果オブジェクトにも `band` / `band_rationale` / `out_of_band_probes` を重複して埋め込み、レポート単体で band 宣言と根拠が読み取れるようにした。 |

## 実装過程で発見した新たな知見（band 変更の効果と限界）

### formant_scale: band 変更は大きく前進させたが、新しい阻害要因を露出させた

- no_exemption grip: v4 の 1.738 → v5 (低音域) の 2.826 へ改善（+1.09pt）。
- **免除表が今回初めて「eligible=True」に到達**: 宣言 side (`source_tilt`)
  の符号一致 5/5（100%）、`E(source_tilt)=1.787 <= 0.5*E(formant_centroid)
  =2.525` を満たし、`grip_declared=5.051 >= 3.0` も達成。
- **しかし `direction_consistency=0.70` が 0.90 の gate 閾値を下回り、
  免除適用後もなお FINAL FAIL**。これは v4 までの実行では表面化していな
  かった **新しい阻害要因**であり、band 変更で初めて可視化された:
  低音域 probe 5 点のうち、G2（全 sweep 点で 2 ピークモデル採用）は
  完全に単調（4/4 一致）だが、C3/E3/G3/C4 は sweep 点ごとに 1 ピーク/
  2 ピークモデルが切り替わり（`fit_mode_matrix` 参照）、その都度
  formant_centroid の定義（単独ピークの log2 vs 2 ピーク幾何平均の log2）
  が不連続に変わるため、sweep 軌跡が非単調になる（3/4, 3/4, 2/4, 2/4）。
  合算 direction_consistency = 14/20 = 0.70。
- **この知見は「アルゴリズムを変えない」という本サイクルの制約と直接
  衝突する**: 根本対策（sweep 全体で probe ごとに固定のモデル次数を使う
  等）は joint fit の呼び出し方自体の変更を要し、memo §B
  「レンダラ・推定器・joint fit（v4の安全機構込み）は無変更で使用」の
  範囲外と判断し、本サイクルでは実施しなかった。次サイクルへの申し送り
  事項として記録する。

### spectral_tilt: band 変更の効果はほぼ無かった

- no_exemption grip: v4 の 0.574 → v5 の 0.528 とほぼ横ばい（微減）。
- 免除表: 宣言 side (`formant_centroid`) の符号一致は 5/5 に改善した
  （v4 は 4/5 だった）が、`E(declared)=18.38` は `E(intended)=9.71` 自身
  よりなお大きく、`0.5*E(intended)=4.86` を大幅に超過（比較不成立は
  v4 とほぼ同水準: v4 は E(declared)=18.24 vs E(intended)=10.47）。
- これは band（probe の音域）を変えても **spectral_tilt →
  formant_centroid の結合強度そのものは変わらない**ことを示す。この
  結合は「倍音サンプリング不足」ではなく、「tilt を変えるとケプストラム
  包絡由来の初期ピーク検出・モデル選択（1peak/2peak の切替率）自体が
  系統的に変わる」という、joint fit のモデル選択ロジックに内在する
  結合であり、band 割当では原理的に解消できないことが本サイクルで
  裏付けられた（v04 underspec_log の仮説を追認する形になった）。
