# Recast Phase 0 — メロディ類似度スパイク（ゲート判定）

日付: 2026-07-22（`date -u` 実測確認済み）
状態: Phase 0 完了報告。ゲート **不成立**（recast PR4 の hard anchor から melody を除外）
起点: Recast Workspace 指示書 Phase 0

## 1. 目的

recast 製品層（編曲制作フロー）が謳う「主旋律 preserved」保証に必要な
メロディ保存センサーのゲートスパイク。既存素材（`PhysicalRPE.melody_contour`
= librosa.pyin ベース）で移調・テンポ不変の類似度指標が成立するかを判定する。
**本スパイクの実測範囲は `melody_contour`（pyin）経路のみ**であり、
`LearnedAudioAnnotations.note_events`（basic_pitch）経路は本スパイクでは
実行していない（除外根拠は §4 参照）。

## 2. 方法

ベーススコア 3 本 × スタイル 3 種（計 9 テイク）を決定論演奏者（`perform`）で
合成し、`compute_melody_contour`（pyin）でメロディ輪郭 → ノート系列 →
類似度 2 指標を計算した。basic_pitch（`note_events`）経路は本スパイクでは
実行していない。

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
- `examples/composition/midnight_signal/composition_score.yaml`（S1 の入力）sha256:
  `37854f54b42a1c4d424f357148d3d10f347e238ec72a42d1248bea2203f97d0b`
- `examples/recast/melody_spike_2026-07-22.json` sha256:
  `12ae62ca08bbb0801fa628943e6feeec21b11dd05c90ecdade059233f887df52`
- `examples/recast/melody_spike_2026-07-22.modules.json`（測定コード manifest、下記参照）sha256:
  `057e8023f8f9e093a4448e8af3bf8a1905a9a5a6ac33cc91e56eddd1edabadb5`
- S2/S3 はスクリプト内で S1 から決定論的に派生するため、S1 の pin +
  スクリプトの pin で全 9 テイクの入力系列が固定される
- 呼び出しグラフ上、本レシピはこれ以外に YAML config を読まない
  （`load_composition_score` は指定パスのみを読み、`compute_melody_contour` /
  `perform` は config 非依存であることを実装確認済み）
- 決定論: 同一コマンドを 2 回実行し出力 JSON が byte-identical であることを実測済み
- **測定コードの pin は first-party 推移閉包 manifest**（手動列挙は含まない）:
  モジュール列挙はインタープリタの実 import 機構による機械列挙で行う——
  スクリプトが `from svp_rpe... import ...` で直接 import する 6 モジュール
  （`compose.loader` / `compose.models` / `perform.performer` / `perform.synth` /
  `rpe.models` / `rpe.physical_features`）を `importlib.import_module` した後、
  `sys.modules` を走査して `svp_rpe` パッケージ配下のロード済みモジュール全て
  （= 実行時に実際に必要となった推移閉包）を集め、各モジュールの `__file__` を
  sha256 する。生成手順は使い捨てスクリプトで実行し、結果を
  `examples/recast/melody_spike_2026-07-22.modules.json`（module 名 → {path, sha256}
  の canonical JSON、29 モジュール）として committed fixture 化した。直接 import 6
  本の対象は正規表現 `^from (svp_rpe\.[\w.]+) import` でスクリプト自身から機械
  抽出し、手動列挙を経由しない
  （手動 4 本列挙 → 手動 6 本列挙と 2 度漏れた反省を踏まえ、列挙自体を機械化して
  終端化。AGENTS.md §8 項目 1 準拠）。
  `tests/test_recast_spike_provenance.py` が同じ手順をテスト内で再実行して
  閉包を再計算し、`.modules.json` と**両方向**（欠落・余剰とも）で突合する
  ため、transitive 依存の増減・変更も自動検出される
- **再現の前提**: pin 表の全ファイル（データ 4 件 + `.modules.json` が指す
  29 モジュールの `.py` 実体）が working tree と一致していること。特定
  commit の checkout そのものは前提としない（squash 等でオブジェクトが
  祖先から外れても pin 表・manifest の記法自体は影響を受けない）。実測を
  実行した commit `1248186` は、squash 等の非参照文脈では祖先関係を
  主張できない実測時 tree の **attestation（記録）** として残す
  （AGENTS.md §8 項目 6 準拠）
- **効果**: 推移閉包中のいずれか 1 モジュールでも将来変更されると
  `tests/test_recast_spike_provenance.py` の閉包検証テストが赤くなり、本メモの
  数値を無言で差し替える（silent stale）ことができず、新しい日付の再実測として
  別途記録する（dated log 規律）ことが強制される

## 3. 結果（生数値）

ノート列長は 1–4（きわめて短い）。特に S1:base は voicing フレームから抽出できた
ノートが 1 音のみで、音程列（interval）が 0 個 = 類似度計算が成立しない。
9 テイク・全 24 ペア中 14 ペアで類似度が算出でき、10 ペアが
`similarity_skipped_empty_intervals` で skip した（skip は全て S1:base の
1 音縮退に起因）。これとは別勘定で、take レベルの skip 記録が 1 件
（`S1:base` の `insufficient_notes_for_intervals` — ペアでなくテイク単位の
記録）が JSON に含まれる。

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

`note_events`（basic_pitch）経路は本スパイクでは実行していないが、除外根拠は
以下の既往実測にある（追試ではなく既存結果の参照）:

- WI0-b（#199）: melody 実推論が sim 0.6 < 事前登録閾値 0.8 で WI2 v0 から除外
  （メロディ抽出とボーカル/伴奏分離層の欠如が既知の弱点）
- WI2（#201）: melody 軸が非弁別（生成物の同一性判定でも melody は機能していない）

すなわち本スパイクの pyin 経路実測（本セクション冒頭）と、#199/#201 による
note_events 経路の既往不成立が、それぞれ独立に melody センサーのゲートを
不成立にしている。

## 5. 帰結（Recast Workspace 指示書 §2 ゲート条項適用）

recast PR4 の縦切り hard anchor は melody を採用せず、以下に差し替える:

- **core-progression**: `chord_progression` + `chord_sequence_json` /
  harmony センサー（コード進行の事象レベル一致率）
- **structure**: `section_map` + structure センサー（セクション構成の一致率）

melody は recast 初版において **`not_observed`**（`observe` 既存スキーマの
`ObservationAdherenceStatus`）として扱う。新語彙は導入せず、既存 D-1 の
determination `no_sensor` 経路をそのまま使う。
**melody preserved の判定は行わない**——分類できないものを「保存されている」と
偽って報告することは避ける。この帰結は pyin 経路（本スパイク実測）と
note_events 経路（#199/#201 既往実測）が**それぞれ独立に**不成立であることに
基づくため、上記の射程訂正後も変わらない。

### WI 系への再入条件

ボーカル分離層（Demucs 等）+ 単旋律素材（ボーカルあり曲・ボーカル stem 抽出後）
での再スパイクが必要。合成和音パッドではなく単旋律ソースで pyin/basic_pitch が
機能するかを再検証してから melody センサーの再検討を行う。再入時は本スパイク
（pyin/`melody_contour`）の再測に加え、note_events（basic_pitch）経路の再測も
対象に含める。

## 6. 限界（honesty）

本判定の有効帯域は決定論シンセ和音音源（`perform` の合成器出力）に限られる。
実歌唱音源・実演奏音源での成立可能性は本スパイクでは未検証。ただし製品ゲート
としては「今測れないものを保証の柱にしない」という判断に十分な情報である。

## 7. fixture

- `scripts/spike_melody_similarity.py`（スパイクスクリプト本体）
- `examples/recast/melody_spike_2026-07-22.json`（実測生データ、決定論 byte 一致 2 回確認済み）
- `examples/recast/melody_spike_2026-07-22.modules.json`（測定コード first-party
  推移閉包 manifest、29 モジュール）
