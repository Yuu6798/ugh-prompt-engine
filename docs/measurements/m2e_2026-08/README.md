# M2e-r0 — 事前登録・決裁・撤回の dated 記録（2026-08-01）

設計正本: [`docs/DESIGN_M2e_vremix_real_bed.md`](../../DESIGN_M2e_vremix_real_bed.md)
runbook: [`docs/m2e_provisioning_runbook.md`](../../m2e_provisioning_runbook.md)

本ディレクトリは M2e 帯（V-remix 実ベッド）の **段階記録**である。本ファイルは
その最初の段階 **M2e-r0**（設計 §10 の P-a）に対応し、設計 §2 が要求する User 承認・
決裁の dated 記録と、附録A が要求する撤回・是正の記録を保持する。

> **この時点で M2e の実測記録は 0 件である。**
> `m2e_accuracy_bars.yaml` は存在せず、1 バイトも書かれていない。
> `m2e_bed_fixtures.yaml` も存在しない。セル台帳 1280 のうち完了は **0**。

---

## 1. User 承認・決裁の記録（設計 §2）

| 決定 | 日付 | 内容 |
|---|---|---|
| ① | 2026-08-01 | stem 帯実測は **V-remix 方式へ全面切替** |
| ② | 2026-08-01 | **MedleyDB 申請は見送り**（結果が不審な場合のみ再検討） |
| ③ | 2026-08-01 | **melodia は deferred へ正式棚上げ**（M 系列の完了条件から除外。#222 の正規化裁定は将来必要になった時点で再開） |
| ④ | 2026-08-01 | 合成ベッド → **MUSDB18-HQ 実ベッドへの切替** |
| ⑤ | 2026-08-01 | 実測は **1 回あたり壁時計 2 時間上限**。規模は削らず**回数**で回す |

③ の反映先: [`docs/melody_observability.md`](../../melody_observability.md) §1 の
資産表 Melodia 行に「状態: deferred（2026-08-01 User 決裁）」を dated 記載した。
棚上げの解除は User 決裁のみ（設計 §13「melodia の混入」の禁止項）。

### ④ が one-way rule の対象外である理由（設計 §2 末尾・附録A）

一方向規律が禁じるのは **登録済みの有効なバーの緩和**である。④ に伴う撤回
（合成ベッド帯 `V_remix_synth_direct` / `V_remix_synth_stem`）は、

- `m2e_accuracy_bars.yaml` へ **1 行も書かれておらず**、
- M2e の実測が **0 件**

の時点での差し替えである。よってこれは「凍結の緩和」ではなく
**登録前の設計差し替え**であり、one-way rule の対象外。既存の
`m2_accuracy_bars.yaml`（M2b/M2c が使った凍結値）は**1 バイトも変更していない**。

---

## 2. 撤回・是正の記録（設計 附録A・全件）

**撤回の経緯そのものが監査対象である**（設計 §0 の規約）。ファイルの本数ではなく
**記録の完全性**でこの規律を満たす——元文書 7 本は個別に commit せず、統合正本
（設計 §0 の対照表）と本節が全件を保持する。

| ID | 対象 | 扱い | 理由の要旨 |
|---|---|---|---|
| A-1 | 改訂1: 合成ベッド帯 `V_remix_synth_*` | **撤回**（登録前） | 前提「実ベッドが調達できない」が調査で崩れた（MUSDB18-HQ が無申請・DOI つきで直接入手可）。裁定 4 件は存続（下記） |
| A-2 | 親設計の単一帯 `V_remix`（バー 0.65/0.10・2 水準） | **2 アームへ置換**（登録前） | 分離器を通す/通さないで測っているものの信頼性が根本的に違い、単一帯では混ざる |
| A-3 | 旧スクリーニング指標 `voiced_frame_ratio <= 0.10` | **退役** | 声なし素材が声あり 2 件の**間に入った**。判別器は反例 1 つで死ぬ。閾値をどこへ動かしても分離できない＝「バーが厳しすぎた」ではなく**測っている量が違った**。閾値 0.10 は導出せずに置いた推測値であり、**設計側の不備**（実行側に落ち度はない） |
| A-4 | 補正1 §8 の帯ブロック（`gate_level`/`levels` をバー block へ） | **是正** | バー block の各キーは `_BAR_THRESHOLD_RANGES`（有限数値・値域つき）で fail-closed 検証されるため文字列もリストも通らない。通すには検査を弱体化するしかない。分類の誤り（バー=judge が数値比較する閾値 / `gate_level`・`levels`=「何を測ったか」の宣言）。**閾値の値は 1 つも動いていない** |
| A-5 | 補正2 §3（総予算 12 時間からの規模逆算） | **撤回** | 「資源上限」を「科学の規模」に直結させる構造。資源が減るたび測る対象が痩せる。正しい分解は「上限は 1 回の実行にかける／規模は削らない／足りない分は回数で埋める」 |
| A-6 | 改訂2 §6-3 の縮退規則（clip を lexical 先頭 20 件へ） | **撤回** | チェックポイント再開（§8.7）で「1 セッションに収まらない」問題自体が消えた。加えて lexical 先頭切り出しは**それ自体が誤り**（id 順が歌手・出典で並んでいれば偏りをそのまま拾う）。標本削減が要るなら附録B の系統抽出 |
| A-7 | 統合正本に残っていた未指定 3 件 | **是正**（2026-08-01） | `lexical order` の照合方式未定義／`archive_sha256_local` 未算出・`canonical decode` 未定義／スペクトログラム未生成。いずれも実行側の指摘により閉じた。**妥当な選択が書かれていないこと自体が欠陥**である（次の実行者が同じ選択に到達する保証がない） |

### A-1 で存続する裁定（撤回されていない）

1. **「`V_fullstack` として押し通す」の却下理由。** `S_fullstack: {}` は「バー未定」で
   はなく「合成伴奏込みのフルスタックには判定バーを置かない」という決定である。
   別素材をその昇格バーで判定することは、閾値を動かさずに**閾値が守っていた素材の
   定義を動かす**行為で、one-way rule が禁じる緩和の変種にあたる。
   **ベッドが実音源になっても「歌声と伴奏が同一曲ではない」ため同じ理由が生きる。**
2. **見返りがないという理由。** M4 の `CALIBRATION_BOUND_ROUTES` は `{"crepe_direct"}`、
   G2 の帯は `clear_lead`。M4 が待っているのは `crepe_direct × clear_lead` の校正。
3. **runbook 必須。** 素材が変わっても「実行者は交換可能」の要件は不変。
4. **水準ラダー 4 点と「主生産物は破断曲線」。** 素材依存の設計ではないため存続。

### A-3 退役記録の未完項目（**宣言された穴**）

設計 附録A-3 は退役記録に以下の必須項目を課している。**うち生値の一覧は本リポジトリに
未収載である**（実行側の作業領域にのみ存在）。埋まったことにせず、穴として宣言する。

| 必須項目 | 状態 |
|---|---|
| 11 素材の `voiced_frame_ratio` **実数値・全件**（採否ではなく生値） | **未収載**（実行側の記録待ち） |
| 声なし素材が声あり 2 件の**間に入る**という順序そのものの明示 | 事実は本表 A-3 に記載済み。**個別の生値による裏づけは未収載** |
| 掃引の範囲と 11/11 不採用（0.5097〜0.8769）という結果 | 範囲の 2 値は設計正本から転記。**素材別内訳は未収載** |
| 測定窓（約 180 秒・**曲頭からの開始オフセット**） | **未確認**。0.0s 起点ならミックス窓の上位集合として有効。0.0s 起点でないなら、この 11 件は「旧指標に判別力がない」ことの証拠としてのみ残し、窓の議論から切り離す（**退役の結論は窓に依存しないため変わらない**） |
| 「全 50 曲は旧指標では意図的に回していない」の明示と理由 | 明示する。理由: **退役済みの計器に 2.5 時間を払わない**。生値を残す方針なので、疑う者は自力で反証できる |

**この穴は測定開始のブロッカーではない**（退役の結論は上記のとおり窓にも個別値にも
依存しない）。ただし r2 のスクリーニング記録を書く際に同じ場所へ追記し、
**空欄のまま黙って進めない**こと。

---

## 3. 本測定の前提条件（未達のもの）

| 段階 | 成果物 | 状態 |
|---|---|---|
| r0 | 設計正本 + 索引 2 行 + 本記録 + runbook | **完了** |
| r1 | ハーネス配線・条件 block 検証・`make_vremix_fixtures.py` + テスト | **完了**（下記 §3.1） |
| r2 | 全 50 曲スクリーニング（棄却事由の事前登録 → 生成 → 1 行判定まで r2 の内側で閉じる） | **完了**（[`r2_screening.md`](r2_screening.md)） |
| r3 | `m2e_bed_fixtures.yaml` / `m2e_accuracy_bars.yaml` 登録 | **完了**（下記 §3.3） |
| r4 | r2-0（`P` 決定・並列不変性ゲート・単位コスト校正・`env_digest`・lockfile） | 未実施 |
| r5 | `m2e_r2_shard_map.yaml` | 未実施 |
| r6 | 本測定（code change 厳禁） | 未実施 |
| r7 | 破断曲線 + stem アーム 4 点の dated 記録（**昇格宣言をしない**） | 未実施 |

r2 以降が未実施である理由は**実行環境の揮発**である（設計 §9.3・下記 §3.2）。
「素材が無いので着手できない」ではない——切り分けを誤ると対処も誤る。

### 3.2 状態の正しい切り分け（2026-08-01・User 指摘により是正）

当初この記録は r2 以降の未実施理由を「素材・重みの**未取得**」と書いていた。
**これは誤りである。** 正しくは「取得した資産が、コンテナ回収により**失われた**」。
両者は対処が違う:

| 誤った切り分け | 正しい切り分け |
|---|---|
| 素材が無い → 取得できるまで待つ | 資産が揮発した → **再取得を `S` に織り込んで次の回で回す** |
| r0 / r1 も素材待ちに見える | **r0 / r1 は音源ゼロで完走する**（実際に完走済み） |

根拠として User から示された事実（**本セッションの実測ではないため、数値は測定記録
として採用しない**。§11 の報告規律に従い、セル台帳が揃うまで帯の数値は出さない）:

- 実行側は既に MUSDB18-HQ test split の復号・フレーム解析・スペクトログラム描画まで
  到達していた（トラック単位の秒数、描画の下端クランプへの言及、タスク名
  「50-track pin collection」）。
- 先行して demucs による分離（3 素材の分離差）と vocadito の測定（ミックスに入らない
  割合）も実行されていた。

**この記録が残すのは事象であって数値ではない。** 失われた作業の生値は、再取得後の
r2 で改めて全 50 曲を測って記録する（§3.3-2 の「全件の実数値を記録」は変わらない）。

**資産ごとの再取得コストの実態**（設計 §9.3 の表と対応）:

| 資産 | 再取得 | 備考 |
|---|---|---|
| vocadito | **軽い** | repo に pin 済み（`m2c_external_fixtures.yaml`: zenodo 5578807 / CC BY 4.0 / zip md5 / 40 clip 全部の sha256）。取得して照合するだけ |
| demucs / crepe | **軽い** | pip |
| MUSDB18-HQ 22.7 GB | **重い（ここだけ）** | 全 50 曲が要るのは **r2 だけ**。r3 以降は採用 2 曲 × 3 stem + vocadito の数百 MB。r2 の計算自体も窓 ≈34 秒なので軽い——**重いのは取得であって計算ではない** |

**本セッションのコンテナ実測（2026-08-01）**: `demucs` / `crepe` / `torch` は
パッケージメタデータごと不在、`soundfile 0.14.0` / `librosa 0.11.0` は在、
書き込み可能領域 29 GB、Zenodo 到達可（`GET /api/records/5578807` → 200）。
すなわち r0 / r1 は成立し、r2 は再プロビジョニングを `S` に計上したうえで開始できる。

### 3.1 r1（P-b）で入った配線 — 何が code change 済みか

**r6（P-d）に code change を 1 行でも入れたらその実測は無効**（§10）。よって
実測に必要なコードは r1 で閉じる必要がある。入れたものを列挙する。

| 設計 | 実装 |
|---|---|
| §6.3 routing 追加 | `src/svp_rpe/melody/routing.py`: `INPUT_KINDS` + `_ROUTES` に **`full_mix_direct_probe` を加算**（既存 4 キーの route 列は 1 本も変更していない。テストが完全一致を固定） |
| §6.1 `_CATEGORY_SPECS` | `V_remix_real_direct`（`full_mix_direct_probe` × `crepe_direct`）と `V_remix_real_stem`（`full_mix` × `demucs_vocals_then_crepe`）を `kind: "external"` で登録 |
| §5.2 カテゴリ所有権 | `_CATEGORY_SPECS` の各行が `bars_file` を持ち、バー検証は**所有カテゴリのみ**を対象にする。所有者未指定・未登録ファイル名は import 時に fail-closed。ファイル同一性は**パス名でなく `schema_version`** から決める（測り直し子は bytes を tmp へ凍結複製して別名で渡すため） |
| §5.3 条件 block | `m2e_measurement_conditions` を**バー block の兄弟**として検証（`gate_level ∈ levels` / `levels` はラダーと順序込み完全一致 / `level_margin_db == 20.0` / 所有しないカテゴリの条件を拒否 / バーを持つカテゴリの条件欠落を拒否）。`gate_level` をバー block に入れると既存の `_BAR_THRESHOLD_RANGES` が未知キーとして拒否する（附録A-4 の是正がそのまま効く） |
| §5.1-4 共有スカラー | M2e 側で `tolerance_cents` / `est_voiced_confidence_floor` / `repeats_min` を**再宣言できない**（宣言したら拒否）。M2 側の値を参照する |
| §5.1-1/-3 | M2e バーに `one_way_rule`（明示的継承）と `provenance.derived_from`（転用元ファイル・sha256・カテゴリ）を要求 |
| §6.2 水準規律 | `--level` を run の次元として導入。row と cat_result に `level` / `ladder_index` を記録。**`level != gate_level` の run にバーを適用しない**（`status: "level_record_only"`。バーが無い帯 `diagnostic_only` とは区別する）。repeats 間で水準が食い違えば拒否 |
| §5.2 provenance | row / cat_result / verdict に、使った bars ファイルの**相対パスと sha256 の両方**を記録し、評価器が読んだ値との一致を fail-closed で要求 |
| §4 生成仕様 | `scripts/make_vremix_fixtures.py`（`build` / `screen` / `stem-sha256` / `n-max`）。seed 不使用・自由変数ゼロ |
| §9.2 canonical decode | 同スクリプトの `canonical_decode()` / `stem_sha256()`。CI 保証は commit した 64 サンプルの int16 wav に対する既知 sha256 照合 |
| §10 の段階契約 | 外部素材 pin ファイルの受理 schema 集合に `m2e-external-fixtures/0.1` を**r1 の時点で**追加。r3（P-c）・r6（P-d）が「code change なし」でいられるようにするため。未知 schema は従来どおり拒否 |

**§4.7（分離器へのチャンネル受け渡し）の流用元の明示**: 新規約は発明していない。

- ハーネスの stem アームは既存 `demucs_vocals_then_crepe` 経路をそのまま通る
  （`src/svp_rpe/melody/extractors.py` の `_prepare_waveform`: `requires_separation` の
  分岐が `isolate_vocals_with_provenance` へ委譲し、stem/weights の digest を刻む）。
- `make_vremix_fixtures.py screen` も同じ入口
  （`svp_rpe.rpe.learned.source_separation_adapter.isolate_vocals_with_provenance`）を
  使う。返り値が 2 次元のときのみモノ化する。**2026-08-01 改訂**（Codex 15/16 巡目）:
  provenance を捨てる `isolate_vocals` から切り替え、分離器の model / version /
  weights digest を `m2e_bed_fixtures.yaml` の `screening` block と照合するようにした。
  同時に、外部 stem を受け取る `--vocals-stem` は**撤去**（渡された WAV を事前登録の
  何とも結び付けられず、素材 pin も窓 pin も通ったまま採用コホートだけが変わりうる）。

**r1 で意図的にやっていないこと**:

- `m2e_accuracy_bars.yaml` / `m2e_bed_fixtures.yaml` は**作らない**（r3 の仕事。
  帯の登録はベッド確定の後でなければ `levels` の意味が確定しない）。ハーネスは
  ファイルが存在しなくても M2 側の検証に影響しない（所有カテゴリのみを見るため）。
- `V_fullstack` の配線（`_CATEGORY_SPECS` への追加）は**しない**——別件・別 PR
  （§5.2 末尾）。「事前登録済み・未配線」の帯が存在してよいという先例は維持する。
- `_DIAGNOSTIC_ONLY_CATEGORIES` は `{"S_fullstack"}` のまま変更しない。

---

## 4. この記録が主張していないこと

- 本記録は**測定結果を一切含まない**。破断曲線・RPA・見通しのいずれも書かない（§11）。
- `demucs_vocals_then_crepe` の calibrated 昇格、`V_fullstack` への昇格、**M4 G2 の解錠**
  のいずれも主張しない（§5.4・§7.2・§13）。
- MUSDB18-HQ test split 先頭 2 曲というベッド構成について、**ジャンル・編成の代表性を
  主張しない**（§7.2）。


---

## 5. M2e-r3（P-c・code change なし）— 事前登録の pin

**この時点で M2e の実測（帯のセル）は依然 0 件である。** 1280 セルのうち完了は **0**。

| ファイル | schema | sha256 |
|---|---|---|
| `tests/fixtures/melody_bench/m2e_bed_fixtures.yaml` | `m2e-bed-fixtures/0.1` | `96958d68fd83d5d5b718620b930a72acd88795e1cfaa394b60c8cfc9ddd394f3` |
| `tests/fixtures/melody_bench/m2e_accuracy_bars.yaml` | `m2e-accuracy-bars/0.1` | `7e8c068fabc4bc8167d822beeaa8806b39f6d2929b08551da8280b4389780f39` |

> `m2e_bed_fixtures.yaml` の digest は **2026-08-01 の (d) 実装訂正**（`r2_screening.md`
> §4.8）で再生成した後の値である。**バー・閾値・採用 2 件はいずれも変わっていない**
> （訂正したのは各曲の `n_drop_frames` / `dropout_sec` / `reason_d_hit`）。
> `m2e_accuracy_bars.yaml` は 1 バイトも変わっていない。

### 5.1 `m2e_bed_fixtures.yaml`（§9 / §9.1 / §9.2）

- **全 50 曲**の帰属証拠を完備した（採用 2 件だけではない）。選定の監査可能性は
  「通過 2 件」ではなく「全 50 件の記録」に載っている（§3.3-2）。
  - `expected_stem_sha256`: **150 件**（50 曲 × drums/bass/other・§9.2 canonical decode）
  - `members`: **150 件**（member path / 非圧縮サイズ / 中央ディレクトリ CRC-32 /
    `member_sha256`）
  - 各曲の `residual_db` / `n_drop_frames` / `dropout_sec` / `reason_d_hit` / `accepted`
- **`archive_sha256_local: null`** + dated 理由（§9.1 レイヤ(2) の**宣言された穴**）。
  上流 md5 の転記で埋めていない。推定値も書いていない。
- `vocals_stem_fetched: false` — `vocals.wav` は 1 バイトも取得していない。
- `screening.threshold_db: -26.0` は `derived_from: {level_margin_db: 20.0,
  hardest_level_db: -6.0}` を併記した**導出値**であり、独立した自由パラメータではない。
- **§9 の様式にない追加フィールド**として `reason_d`（(d) の凍結パラメータ）と
  各曲の `n_drop_frames` を入れた。理由: **(d) が選定に参加した**ため、これが無いと
  「`residual_db` を通った lexical 先頭 2 件がなぜ不採用か」を本ファイル単体で
  説明できない。

採用 2 件:

| rank | track | `residual_db` | `N_drop` |
|---|---|---|---|
| 1 | Angels In Amplifiers - I'm Alright | −48.288419 | 36 |
| 2 | Arise - Run Run Run | −52.255730 | 48 |

> `N_drop` は **2026-08-01 の実装訂正後**の値（`r2_screening.md` §4.8）。訂正前は
> それぞれ 72 / 51 だった。**採否は不変**（訂正は `N_drop` を減らす方向にしか効かない）。

### 5.2 `m2e_accuracy_bars.yaml`（§5.1 / §5.3）

- 別ファイル分離の理由（`bars_sha256` pin の偽陽性回避）と、**一方向規律の明示的継承**を
  冒頭コメントに書いた（§5.1-1: 暗黙の継承にしない）。機械可読な宣言として
  `one_way_rule` を置き、loader が非空文字列を要求する。
- **共有スカラーを再宣言していない**（§5.1-4）。`tolerance_cents` /
  `est_voiced_confidence_floor` / `repeats_min` は M2 側を参照する。loader が再宣言を
  fail-closed で拒否することも確認済み。
- 条件 block（`m2e_measurement_conditions`）は**バー block の兄弟**（§5.3・附録A-4）。
  `gate_level: "+12dB"` / `levels: ["+12dB","+6dB","0dB","-6dB"]` / `level_margin_db: 20.0`。
- `provenance.derived_from` に転用元
  （`m2_accuracy_bars.yaml` @ `50c83e65…` の `V_fullstack`）を記録した（§5.1-3）。

### 5.3 **バーの導出が r2 の screening 値に依存していないこと**（明示）

r2 で全 50 曲の実測値を見た後に本登録を行っている。よって「見てから決めたのではないか」
という疑いは構造的に生じうる。**依存していないはずだが書かれていない、を欠陥と見なす
規律**（§3.3.1 のときと同じ理屈）に従い、機械可読な宣言
（`provenance.bars_independent_of_screening: true` + note）と併せてここに明記する。

| バーの構成要素 | 由来 | r2 の観測値への依存 |
|---|---|---|
| `min_rpa: 0.65` | `m2_accuracy_bars.yaml` の `V_fullstack`（**2026-07-25 凍結**） | **なし**（M2e の素材が存在するより前に凍結） |
| `max_octave_gap: 0.10` | 同上 | **なし** |
| `gate_level: "+12dB"` | 設計 §5.3（素材選定より前に定義） | **なし** |
| `levels`（4 点ラダー） | 設計 §3.6（素材選定より前に定義） | **なし** |
| `level_margin_db: 20.0` | 設計 §3.4.1 の不変量 | **なし** |

**r2 の観測値から導出した閾値は 1 つも無い。** 逆向きの依存（r2 が設計値を使う）は
存在する——`screening.threshold_db: -26.0` は `level_margin_db` と `hardest_level_db`
の導出値であり、これは設計 → 実測の向きなので順序が逆転していない。

### 5.4 r3 で code change を入れていないこと

r3 は設計 §10 の表で **code change: なし**と定められている。よって:

- ハーネス（`scripts/run_melody_accuracy.py`）・routing・生成器のいずれも変更していない。
- **commit 済み記録を pin するテストも追加していない**（テストはコードであるため）。
  新ファイルの schema・所有権・条件 block・共有スカラー再宣言拒否は、**r1 で追加済みの
  テスト群がすでに機械検証している**（`load_bars` 経由）。
- 実ロード確認は行った: `load_bars(M2E_BARS_PATH)` が
  `m2e_accuracy_bars.yaml` として同一性解決に成功し、条件 block・provenance 検証を
  通過すること、および `m2_accuracy_bars.yaml` 側が無傷であることを実測した。

### 5.5 次段

**r4（M2e-r2-0）**: `P` の決定・**並列不変性ゲート**・`S` / `T_direct` / `T_stem` の校正・
`env_digest` 確定・lockfile commit。

r2 で得た申し送り（`r2_screening.md` §4.6.4）を必ず適用すること——
**`OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1` / `torch.set_num_threads(1)` の 3 点すべて**を
設定しないと demucs の stem が run 間で bit 一致せず、stem アームの repeats が
「別 model stack」として fail-closed になる。合格条件は「出力ピッチ軌跡の sha256 が
完全一致」であり、精度値の一致では不十分（§8.3）。
