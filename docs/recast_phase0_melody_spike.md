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
python scripts/spike_melody_similarity.py --out examples/recast/melody_spike_2026-07-22.json \
  --dump-modules examples/recast/melody_spike_2026-07-22.modules.json
```

`--dump-modules`（optional）は次の 2 段で測定コード manifest を書き出す
副作用専用フラグ（測定結果 JSON には一切影響しない）:
(1) **svp_rpe を一切 import する前**に `src/svp_rpe/` 配下の全 `.py` ファイルを
glob → read_bytes → sha256 して事前ハッシュ表を作る（TOCTOU をさらに前倒しで
排除——旧版は「トップレベル import 完了直後」にスナップショットしていたが、
import 実行そのものとスナップショット取得の間に working tree が書き換わる
余地が残っていた。スクリプト側で svp_rpe の import を
`_import_svp_rpe_symbols()` 関数へ遅延させ、この事前ハッシュを import 実行
より前に完了させる構成に変更した）。(2) その後で import し、`run_spike()`
の実行を `sys.setprofile` の call イベントでラップして、実行中に実際に
呼ばれた関数の `co_filename` が指す `svp_rpe` 配下ファイル（= 実行時消費
granularity）を集める。測定完了後、「(1) の事前ハッシュ表」と「(2) の消費
ファイル集合」の**交差**を manifest として書き出す。消費されたファイルが
事前ハッシュ表に存在しない場合（= 事前ハッシュ採取後に新規作成されたファイルが
実行中に呼ばれた場合）は manifest を書かず非ゼロ exit で fail-closed する。

全入力は committed（スクリプト本体 + `examples/composition/midnight_signal/composition_score.yaml`。
S2/S3 はスクリプト内で決定論的に構築されるため追加 fixture 不要）。

- `scripts/spike_melody_similarity.py` sha256:
  `ab5ec86955e87ae6c62c3814ce747360eb53ad221643fca45b64a0bb5fac7bc2`
- `examples/composition/midnight_signal/composition_score.yaml`（S1 の入力）sha256:
  `37854f54b42a1c4d424f357148d3d10f347e238ec72a42d1248bea2203f97d0b`
- `examples/recast/melody_spike_2026-07-22.json` sha256:
  `12ae62ca08bbb0801fa628943e6feeec21b11dd05c90ecdade059233f887df52`
- `examples/recast/melody_spike_2026-07-22.modules.json`（測定コード manifest、下記参照）sha256:
  `c607d5b07444abc0f13dee0295ff390c4749192d9112c39b99243b947023ab26`
- S2/S3 はスクリプト内で S1 から決定論的に派生するため、S1 の pin +
  スクリプトの pin で全 9 テイクの入力系列が固定される
- 呼び出しグラフ上、本レシピはこれ以外に YAML config を読まない
  （`load_composition_score` は指定パスのみを読み、`compute_melody_contour` /
  `perform` は config 非依存であることを実装確認済み）
- 決定論: 同一コマンドを 2 回実行し出力 JSON が byte-identical であることを実測済み。
  上記スクリプト pin の更新（svp_rpe の import を関数へ遅延させ、`--dump-modules`
  の事前ハッシュ採取を import 実行より前に完了させる構成へ変更）後も、`--out`
  の測定結果 JSON が更新前と byte-identical であることを実行して確認済み
  （import 順序の並べ替え・スクリプト変更が測定に無影響であることの実測裏付け。
  manifest 自体の内容（7 モジュール・全 hash）も不変であることを確認済み）
- **測定コードの pin は実行時消費 granularity の manifest**（手動列挙は含まない）:
  `--dump-modules` を付けてスパイクをフル実行し、上記 (1)(2) の交差
  （= 測定を実行するために実際に呼ばれたモジュールのみ）を
  `examples/recast/melody_spike_2026-07-22.modules.json`
  （module 名 → {path, sha256} の canonical JSON、7 モジュール:
  `compose.loader` / `compose.models` / `perform.performer` / `perform.synth` /
  `rpe.models` / `rpe.physical_features`（= 直接 import 6 本全て）+
  `utils.clamp`（`physical_features` から実際に呼ばれる transitive 依存））
  として committed fixture 化した。ロードされただけで一度も呼ばれない
  package `__init__` 副作用 export（`compose.convert` / `compose.device_profile` /
  `compose.fixity` / `compose.prompt_renderer` / `semantic_ci` 系列 / `eval` 系列
  など、旧版で 29 モジュールまで膨らんでいた原因）は manifest から除外される。
  直接 import 対象（`from svp_rpe.* import` 6 行）はスクリプト自身のソースから
  正規表現で機械抽出し、手動列挙を経由しない（手動 4 本列挙 → 手動 6 本列挙 →
  importlib 閉包の事前列挙 → import 完了直後のスナップショット →
  import 前の事前ハッシュ×実行時消費トレースの交差、と 5 段階の是正を経て、
  TOCTOU 安全かつ「呼ばれもしない副作用 export」を含まない granularity に
  終端化。AGENTS.md §8 項目 1・8-A 項目 1 準拠）
- **不変条件（bytes 採取の順序）**: bytes 採取は svp_rpe の import 実行前に
  行う（pin される bytes = import 機構が実際に読む bytes）。実行中（pre-hash
  取得後から測定終了まで）に working tree の該当ファイルを書き換えないことが
  再現の前提であり、これは §「再現の前提」に明記した「pin 表の全ファイルが
  working tree と一致していること」という条件の帰結として既に成立している
  （実行前後で内容が変わらないことを前提にしている以上、実行中に変更しない
  ことも同じ前提の一部である）
- **閉ループ論証**: 上記の不変条件（bytes 採取は import 前）により「実行に
  使われた bytes」と「manifest に pin される bytes」が常に一致する。この
  manifest は実行時消費 granularity（実測時に実際に呼ばれた関数を含むモジュール
  のみ）である。実行経路（呼び出しグラフ）に新規消費が追加されるのは、上記の
  pin 済みファイル（スクリプト自身、または manifest が列挙するモジュールの
  いずれか）を編集したときに限られ、その編集自体が対応する hash pin の
  アラーム（`tests/test_recast_spike_provenance.py`）を踏んで赤くする。
  赤くなった場合の唯一の是正経路は「manifest を再生成する新しい dated
  再実測」であり、その再実測が改めて事前ハッシュ×消費トレースを取り直すため、
  列挙は自己完結して閉じている（手動更新で pin だけを合わせて実体との乖離を
  放置する経路は存在しない）
- `tests/test_recast_spike_provenance.py` は、manifest が列挙する全ファイルの
  存在 + working tree との sha256 一致、および直接 import 6 モジュールが
  manifest に含まれていること（⊆ 検算）を機械検証する
- **再現の前提**: pin 表の全ファイル（データ 4 件 + `.modules.json` が指す
  7 モジュールの `.py` 実体）が working tree と一致していること、かつ
  実行環境が下記「実測環境（attestation）」表と一致していること
  （§「実測環境」参照）。特定 commit の checkout そのものは前提としない
  （squash 等でオブジェクトが祖先から外れても pin 表・manifest の記法自体は
  影響を受けない）。実測を実行した commit `1248186` は、squash 等の非参照文脈
  では祖先関係を主張できない実測時 tree の **attestation（記録）** として残す
  （AGENTS.md §8 項目 6 準拠）
- **効果**: manifest が列挙するモジュールのいずれか 1 つでも将来変更されると
  `tests/test_recast_spike_provenance.py` が赤くなり、本メモの数値を無言で
  差し替える（silent stale）ことができず、新しい日付の再実測として
  別途記録する（dated log 規律）ことが強制される

#### 実測環境（attestation）

`importlib.metadata.version` で実測（2026-07-22 `--dump-modules` 実行時点）。
列挙基準は**実行経路の第一者コードが直接呼ぶサードパーティ実装**（audio スタック
だけでなく score parse 経路の PyYAML/pydantic も含む）。本メモの byte 一致再現の
主張は**下記バージョンと一致する環境内**に限定される——異バージョン環境（依存
更新後を含む）での再実行は本メモの検証範囲外であり、差異が出ても「壊れた」では
なく新しい日付の再実測として別途記録する対象になる（lockfile 導入自体は
リポジトリ全体の依存管理方針の話であり本メモの射程外）。

| package | version |
|---|---|
| python | 3.11.15 |
| numpy | 2.4.6 |
| scipy | 1.17.1 |
| librosa | 0.11.0 |
| soundfile | 0.14.0 |
| numba | 0.66.0 |
| pyyaml | 6.0.1 |
| pydantic | 2.13.4 |
| pydantic-core | 2.46.4 |

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
- `examples/recast/melody_spike_2026-07-22.modules.json`（測定コード実行時消費
  manifest、7 モジュール）
