# Recast Phase 0 — メロディ類似度スパイク（ゲート判定）

日付: 2026-07-22（`date -u` 実測確認済み）
状態: Phase 0 完了報告。ゲート **不成立**（recast PR4 の hard anchor から melody を除外）
起点: Recast Workspace 指示書 Phase 0

## 1. 目的

recast 製品層（編曲制作フロー）が謳う「主旋律 preserved」保証に必要な
メロディ保存センサーのゲートスパイク。既存素材（`PhysicalRPE.melody_contour`
= librosa.pyin ベース、`LearnedAudioAnnotations.note_events` = basic_pitch）で
移調・テンポ不変の類似度指標が成立するかを判定する。

## 2. 方法

ベーススコア 3 本 × スタイル 3 種（計 9 テイク）を決定論演奏者（`perform`）で
合成し、メロディ輪郭からノート系列 → 類似度 2 指標を計算した。

- **ベーススコア**:
  - S1 = `examples/composition/midnight_signal/composition_score.yaml`（既存 fixture）
  - S2 / S3 = スクリプト内で決定論的に構築した無関係曲級の派生曲
    （chord_progression / structure_bars / key / bpm を変えた合成スコア）
- **スタイル**: `base`（無変形）/ `transposed_up`（transpose+3・bpm_bias+25）/
  `transposed_down`（transpose−4・bpm_bias−20）。乱数源は `PerformanceStyle.seed` のみ
- **パイプライン**: `perform` で合成 → `compute_melody_contour`（pyin）でメロディ輪郭抽出
  → voicing ≥ 0.5 のフレームのみ採用 → Hz→MIDI 変換 → median filter（kernel=5）
  → 半音ラン（≥3 フレーム連続）でノート化 → 音程列に差分（オクターブ折返し
  `[-6, +6]`）→ DTW（折返し距離・パス長正規化）+ LCS 比の 2 指標で類似度を算出
- 判定は行わない生データダンプ（分類・閾値は本スパイクの範囲外）

### 再現レシピ

```bash
python scripts/spike_melody_similarity.py --out examples/recast/melody_spike_2026-07-22.json
```

全入力は committed（スクリプト本体 + `examples/composition/midnight_signal/composition_score.yaml`。
S2/S3 はスクリプト内で決定論的に構築されるため追加 fixture 不要）。

- `scripts/spike_melody_similarity.py` sha256:
  `f97105593a334e220afddc322982e415b88d1a58a4a01cd015cea93fe2e3d444`
- `examples/recast/melody_spike_2026-07-22.json` sha256:
  `12ae62ca08bbb0801fa628943e6feeec21b11dd05c90ecdade059233f887df52`
- 決定論: 同一コマンドを 2 回実行し出力 JSON が byte-identical であることを実測済み

## 3. 結果（生数値）

ノート列長は 1–4（きわめて短い）。特に S1:base は voicing フレームから抽出できた
ノートが 1 音のみで、音程列（interval）が 0 個 = 類似度計算が成立しない。
9 テイク・全 24 ペア中 11 ペアが `insufficient_notes_for_intervals` /
`similarity_skipped_empty_intervals` で skip（うち S1 が絡むペアが大半）。

計算が成立した 14 ペアの類似度分布:

- 同曲変形ペア（4 件）: `sim_dtw` = {0.75, 0.3333, 0.3333, 0.1667}、
  `sim_lcs` = {0.6667, 0.6667, 0.5, 0.0}
- 異曲ペア（10 件）: `sim_dtw` = 0.1667–0.8（最大 0.8 = `S2:base` × `S1:transposed_down`）

**異曲ペアの最大値 0.8 が同曲ペアの最大値 0.75 を上回り、分布が完全に重複している**
（同曲=保存されているべき／異曲=別物であるべき、という区別を指標が付けられていない）。

### 全 14 ペア（生データ、JSON より転記）

| a | b | category | sim_dtw | sim_lcs |
|---|---|---|---|---|
| S2:base | S2:transposed_up | same_song_variant | 0.75 | 0.6667 |
| S2:base | S2:transposed_down | same_song_variant | 0.3333 | 0.6667 |
| S3:base | S3:transposed_up | same_song_variant | 0.1667 | 0.0 |
| S3:base | S3:transposed_down | same_song_variant | 0.3333 | 0.5 |
| S2:base | S1:transposed_up | cross_song | 0.5 | 0.3333 |
| S2:base | S1:transposed_down | cross_song | **0.8** | 0.6667 |
| S2:base | S3:base | cross_song | 0.2 | 0.0 |
| S2:base | S3:transposed_up | cross_song | 0.75 | 0.6667 |
| S2:base | S3:transposed_down | cross_song | 0.2727 | 0.0 |
| S3:base | S1:transposed_up | cross_song | 0.1667 | 0.0 |
| S3:base | S1:transposed_down | cross_song | 0.2143 | 0.0 |
| S3:base | S2:base | cross_song | 0.2 | 0.0 |
| S3:base | S2:transposed_up | cross_song | 0.1667 | 0.0 |
| S3:base | S2:transposed_down | cross_song | 0.2222 | 0.0 |

## 4. ゲート判定: 不成立

原因はセンサー段の縮退である。合成和音パッド音源に対し pyin が旋律的な
ノート系列をほぼ返さない（S1:base は 1 音のみ、他テイクも 2–4 音）ため、
そもそも類似度アルゴリズムに渡す入力系列が成立していない。DTW/LCS という
アルゴリズム選択の優劣以前の問題であり、指標の改善では解決しない。

既往実測と整合する:

- WI0-b（#199）: melody 実推論が sim 0.6 < 閾値 0.8 で WI2 v0 から除外
  （メロディ抽出とボーカル/伴奏分離層の欠如が既知の弱点）
- WI2（#201）: melody 軸が非弁別（生成物の同一性判定でも melody は機能していない）

## 5. 帰結（Recast Workspace 指示書 §2 ゲート条項適用）

recast PR4 の縦切り hard anchor は melody を採用せず、以下に差し替える:

- **core-progression**: `chord_progression` + `chord_sequence_json` /
  harmony センサー（コード進行の事象レベル一致率）
- **structure**: `section_map` + structure センサー（セクション構成の一致率）

melody は recast 初版において **`not_observable` を正式ステータス**として扱う
（`observe` の既存 D-1 `no_sensor` / `not_observed` 経路をそのまま使う）。
**melody preserved の判定は行わない**——分類できないものを「保存されている」と
偽って報告することは避ける。

### WI 系への再入条件

ボーカル分離層（Demucs 等）+ 単旋律素材（ボーカルあり曲・ボーカル stem 抽出後）
での再スパイクが必要。合成和音パッドではなく単旋律ソースで pyin/basic_pitch が
機能するかを再検証してから melody センサーの再検討を行う。

## 6. 限界（honesty）

本判定の有効帯域は決定論シンセ和音音源（`perform` の合成器出力）に限られる。
実歌唱音源・実演奏音源での成立可能性は本スパイクでは未検証。ただし製品ゲート
としては「今測れないものを保証の柱にしない」という判断に十分な情報である。

## 7. fixture

- `scripts/spike_melody_similarity.py`（スパイクスクリプト本体）
- `examples/recast/melody_spike_2026-07-22.json`（実測生データ、決定論 byte 一致 2 回確認済み）
