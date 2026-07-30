# M2d — 主旋律抽出の誤差モデル（crepe 経路・2026-07 実測確定）

設計: [`DESIGN_M2_extraction_accuracy.md`](DESIGN_M2_extraction_accuracy.md) §5 M2d / §7。
本書は M2b（S 帯・[`measurements/m2b_2026-07/`](measurements/m2b_2026-07/)）と
M2c（V 帯・[`measurements/m2c_2026-07/`](measurements/m2c_2026-07/)）の dated 実測から
導いた **crepe 経路の誤差モデル**であり、**M3（旋律比較器）設計書の第 1 入力**である。
数値はすべて commit 済み verdict/report に pin されている（本書は解釈のみ・新規測定なし）。

## 実測サマリ（3 帯 + 未測定 1 帯）

| 帯 | 入力 | RPA | RCA−RPA | median cent | VR | VFA | 判定 |
|---|---|---|---|---|---|---|---|
| S-direct | 合成単旋律・分離なし | 1.000 | 0.000 | 1.35 | 1.000 | 0.259 | **fail**（VFA 単独超過） |
| S-fullstack | 合成ミックス→demucs→crepe | 0.640 | 0.003 | 13.19 | 0.903 | 0.585 | diagnostic_only（分布外） |
| V-direct | 実声 40 clip・分離なし | 0.9886 | 0.0015 | 3.84 | 0.998 | 0.44 | **pass** |
| V-fullstack | 実ミックス（MedleyDB） | — | — | — | — | — | **未測定**（アクセス申請律速） |

## 誤差モデル（M3 の目盛りの根拠・4 軸）

**1. 音高中心誤差（cent）** — 有効帯域では小さく安定。合成 ~1.4 cent・実声
per-clip median 2.33–6.91 cent（中央 3.65）。mir_eval 標準の ±50 cent 許容に対し
1 桁下であり、M3 の音高一致許容幅は **±10–15 cent でも中心誤差を十分吸収**する
（50 cent は編曲差分の検出感度を落とすので M3 側で選択の余地あり）。

**2. オクターブ誤り率（RCA−RPA）** — direct 帯では実質ゼロ（集計 0.15%・
最悪 clip 2.54%）。M3 のオクターブ正規化は必須ではないが、**worst-case 2.5%/clip の
予算**として計上する。分離込み（S-fullstack）でも 0.33% と低い — 分離が壊すのは
音高でなく voicing と追跡連続性。

**3. voicing 誤検出（VFA）— 本経路の系統的弱点軸**。VR は全帯 ≈1.0（有声の
取りこぼしなし）に対し、VFA は S-direct 0.259・実声 median 0.44・**per-clip 0.13–0.76
と分散の主軸**。S-direct fail の帰属仮説（休符のリリース尾 = spec 上無声・音響上有声の
正解境界アーティファクト）は実声でも整合的に再現した。**M3 への含意（最重要）**:
比較器は抽出器の voiced/unvoiced 判定を信用してはならない。**両系列が共に有声の
フレームのみで音高比較**し、voicing 不一致をペナルティ化しない設計を既定とする
（est_voiced_confidence_floor 0.30 は凍結値・事後調整禁止）。

**4. 素材別ばらつき** — 実声 40 clip で RPA min 0.9507（バー割れゼロ）と音高は
一様に高い。ばらつきは VFA に集中（録音デバイス・歌唱密度依存と推定・M3 では
clip 単位の信頼重みに使える）。集計は per-clip 算術平均（clip 等重・MIREX 慣行）。

**計器ノイズ床**: 同一バッチ内 repeats は bit 一致、バッチ間は median cent に
~1e-3 cent の双安定（crepe TF CPU 推論由来・M2b 記録）。誤差モデルの cent スケール
（数 cent）に対し 3 桁下で無視可能。

## 帯域地図と §7 分岐判定

- **crepe_direct（clean lead / vocals stem 入力帯）**: **誤差モデル付きで校正済み**。
  音高系は合成・実声とも高精度、voicing は上記の既知バイアスとして扱う
- **demucs_vocals_then_crepe（フルミックス入力帯）**: **未校正**。S-fullstack は
  合成音色が demucs の分布外のため診断参考のみ（RPA 0.640 を経路の欠陥と読まない）。
  V-fullstack は MedleyDB 確保後に dated 再入
- §4 の「calibrated 昇格 = V_fullstack 通過」は**未達**。§7 分岐の準用として、M3 は
  **vocals stem / clean lead 入力限定で先行設計可能**（フルミックス帯への外挿は禁止・
  誤差モデル明示つき外挿の可否も M3 Brief で User 決裁）

## データ出所（すべて commit 済み・pin 整合は committed-record テストが CI 強制）

M2b: `measurements/m2b_2026-07/`（run7・n=2）/ M2c: `measurements/m2c_2026-07/`
（vocadito 40 clip・n=2・CC BY 4.0・sha256 事前登録 commit `1cbd448`）。
バーは `tests/fixtures/melody_bench/m2_accuracy_bars.yaml`（凍結・一方向）。
