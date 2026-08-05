# L0a 著述契約 v1 — スキーマ公開範囲 + 記号検証ゲート + report 正規形の凍結

**Status**: 実装済み（本 PR）。正本 = [`llm_adapter_planning.md`](llm_adapter_planning.md)
§4「L0a」。材料 = [`l0s_spike_record.md`](l0s_spike_record.md) §4 の L0a 材料 6 件
（優先順）と `examples/l0s_spike/scripts/validate_score.py`（L0-s 9 巡硬化済みの
記号検証ゲート、凍結済み歴史的成果物のため無変更）。

本文書は L0-s 契約 v0（`examples/l0s_spike/contract.md`）を base に、L0-s の
観測記録が示した契約欠陥 5 件を編入した v1 である。実装物（spec YAML /
`svprpe validate` / 信頼軸表 / report スキーマ）は音声処理ゼロ。

## (a) スキーマ公開範囲 — spec YAML が正

L0-s v0 は契約文書（Markdown）にスキーマを手書きしていたが、実装（記号検証
ゲート）とドリフトしうる二重管理だった。L0a では公開スキーマ範囲の正本を
宣言的 YAML spec 1 本に統合する:

- **正 = [`config/authoring_contract_l0.yaml`](../config/authoring_contract_l0.yaml)**
  （`src/svp_rpe/config/` に同梱コピー同期）。`schema_version:
  "authoring-contract/1.0"`。全階層の許可キー木・型狭窄・列挙・リテラル・
  形式正規表現を宣言する——`examples/l0s_spike/scripts/validate_score.py`
  がハードコードしていた制約の逐語データ化（対応関係は spec YAML 冒頭の
  provenance コメントに記載）。
- ロード: `src/svp_rpe/authoring/contract.py:load_authoring_contract()`
  （pydantic モデル、spec 自体も `extra="forbid"` で検証——タイプミスで
  壊れた spec を黙って無視しない）。
- 著者向けの読み物としての体裁（フィールドの意味・演奏への反映——L0-s
  contract.md §0/§2 相当）は今後この文書に編入する場合、spec YAML の内容と
  矛盾しないことを spec YAML 側の provenance コメントで確認できるようにする
  こと。

## (b) 著述ガイド v1 — 計器分解能・可行域の開示（D5 拡張）

L0-s 観測記録 §3.3-2「計器の分解能・有効帯域の非開示」への対応。契約は軸の
**値**だけでなく計器の**分解能・可行域**も著者に開示する:

- **構造センサー**（AR4 `svprpe observe` structure domain）:
  - 境界検出は音響駆動（宣言したセクション名の転記ではない）。隣接
    セクションのエネルギー・音色対比が明確なほど境界は検出されやすい。
  - 最小セクション間隔は概ね 5 秒。極端に短い曲は 1 セクションへ縮退する。
  - 長い持続区間は内部ダイナミクスの変化で過分割されうる。
  - **可行窓は狭い**（L0-s 実測、`l0s_spike_record.md` §2 周回台帳）:
    中間セクション実長 12.3 秒は分割された。6.2 秒は吸収された（縮退側）。
    陽性対照の 7.5 秒は 3 分割ちょうどで成立した。この 2 点（12.3s
    分割・6.2s 吸収）は可行窓が 7.5s 近辺の狭い帯であることを示す唯一の
    実測点であり、正確な閾値ではない——新しい課題を組む際は自前の陽性
    対照（正本 §4.1）で可行性を確認すること。
- この開示は契約の**著述ガイド**（読み物）の一部であり、記号検証ゲートは
  強制しない（構造の可行性は音響ヒューリスティックであり pydantic 型検証の
  対象外）。

### 数値下限のクラッシュ族実測（PR #246 Codex P2 review 2 巡目）

L0-s 6 巡目（`physical.key`/`physical.time_signature` の記法）と同型の
「契約妥当 × 下流ハードクラッシュ」族。`svp_rpe.perform.performer.perform()`
を `FAITHFUL_TAKE` スタイルで直接実行し、契約の型チェックは通るが
`perform()` を未捕捉例外で落とす非正整数値を実測で確定した:

| 実測対象 | 結果 | 例外 |
|---|---|---|
| `physical.bpm: 0` | **クラッシュ** | `ZeroDivisionError`（`60.0 / bpm`） |
| `physical.bpm: -60` | **クラッシュ** | `ValueError: zero-size array to reduction operation maximum`（負の `bar_sec` → 空配列 → `np.max` が空配列に失敗） |
| `structure[].bars: 0` | **クラッシュ** | 同上 `ValueError`（`section_len` が 0 になり `t` が空配列） |
| `structure[].bars: -4` | **クラッシュ** | 同上 `ValueError` |
| `physical.time_signature: "0/4"`（分子 0） | **クラッシュ** | 同上 `ValueError`（`beats_per_bar=0` → `bar_sec=0`） |
| `physical.time_signature: "4/0"`（分母 0） | 非クラッシュ | — （`perform()` は `time_signature.split("/", 1)[0]` で分子のみ読み、分母はこのリポジトリのどこでも解析されない） |

ゲート化: `physical.bpm`/`structure[].bars` に `FieldSpec.min: 1` を追加し、
新規 kind `range` の違反として報告する（下記 (c) の kind 表に追加）。
`physical.time_signature` の形式正規表現を `^[1-9][0-9]*/[1-9][0-9]*$`
へ狭窄した——**境界宣言**: 分母 0 の排除はクラッシュ実測に基づく判断では
なく（実測は非クラッシュ）、意味のない時刻記法を防御的に閉じる形式強化に
すぎない。`bpm`/`bars`/分子 0 の 3 者と実測根拠の性質が異なることを
ここに明記する（L0-s の「クラッシュ確認済みのみゲート、非クラッシュは
境界宣言」という規律を、分母 0 についても嘘なく保つため）。

歴史的 evidence（陽性対照 + L0-s rounds 1–5、いずれも `bpm`/`bars` 正整数・
`time_signature: "4/4"`）は本ゲート追加後も全件 `pass` のまま
（`tests/test_validate_cli.py`/手動再検証で確認）。

### 空リストのクラッシュ族実測（PR #246 Codex P2 review 4 巡目 B）

`structure: []`（空リスト）を同じ手順（`perform()` を `FAITHFUL_TAKE` で
直接実行）で実測した:

| 実測対象 | 結果 | 例外 |
|---|---|---|
| `structure: []` | **クラッシュ** | `ValueError: perform() requires at least one structure section`（`perform()` 冒頭の明示的ガード節） |
| `events.chord_progression: []` | 非クラッシュ | — （`perform()` はキーに応じた既定進行へフォールバックする。省略時と同じ経路） |

`structure: []` は `_public_scope_errors` の per-element ループが 0 回で
素通りし、canonical `CompositionScore` 側にも `structure` の最小要素数
制約が無いため、`svprpe validate` が偽 `pass` を返した後に `perform()` が
クラッシュしていた（`svprpe validate` 単体では検出不能だった欠陥）。

ゲート化: `ObjectSpec.min_items` を新設し `structure_section`（`structure`
リストの各要素を記述する唯一の `ObjectSpec`）へ `min_items: 1` を付与、
`range` kind の違反として報告する（コンテナのサイズ制約は
`FieldSpec.min` の値域下限と同種の「許容域を下回る」違反という判断——
kind を新設せず既存 `range` を再利用し語彙を増やさない）。
**境界宣言**: `events.chord_progression: []` は非クラッシュと確認済み
のため引き続きゲート対象外——`structure` とは異なり、こちらは省略時と
同じフォールバック経路を通る契約 v0/v1 の既定挙動どおりであり、クラッシュ
族には属さない。

歴史的 evidence（陽性対照 + L0-s rounds 1–5、いずれも `structure` 3 要素
以上）は本ゲート追加後も全件 `pass` のまま。

## (c) エラープロトコル — kind 語彙と where 粒度

`svprpe validate`（`src/svp_rpe/authoring/validate.py`）が返すエラーは
`{where, message, kind}` の決定論ソート済みリスト:

| kind | 意味 |
|---|---|
| `public_scope` | spec の許可キー集合に無いキー（契約非掲載。canonical 側では合法な場合もある） |
| `type` | spec が宣言する型（`str`/`int`（bool 除外）/`list[str]`）に一致しない |
| `enum` | spec が宣言する列挙値のいずれでもない |
| `literal` | spec が宣言する単一リテラル値と一致しない |
| `format` | spec が宣言する正規表現形式に一致しない（`physical.key`/`physical.time_signature` のみ） |
| `range` | spec が宣言する値域下限 (`FieldSpec.min` — `physical.bpm`/`structure[].bars`) またはコンテナの最小要素数 (`ObjectSpec.min_items` — `structure`) を下回る。PR #246 crash-family 実測、上記 (b) 節 |
| `canonical` | canonical `CompositionScore` 検証（pydantic）が拒否 |

`where` の粒度はセクション+フィールド（例 `physical.bpm`、
`structure[0].bars`、`events.chord_progression[0].root`）。コンテナ全体の
制約（`structure` の `min_items`）は要素インデックスを持たないため
`where: "structure"`（インデックス無し）で報告する。公開範囲チェック
失敗は canonical 検証を短絡しない——両方常に走り、`errors` は公開範囲
エラー（`(where, message)` でソート済み）に続けて canonical エラー
（pydantic の `loc` 順）を連結する。

**`status`×`errors` の整合（PR #246 Codex P2 review 4 巡目 A）**:
`SymbolicValidationResult`（`svprpe validate` の結果、および
`AuthoringDiffReport.symbolic_validation` に埋め込まれる同型）は
`status="pass"` に `errors` が付随する組、`status="fail"` なのに `errors`
が空/欠落の組——どちらの矛盾組も spec ロード時（`model_validator`）に
`ValidationError` で fail-closed に拒否する。これにより、このスキーマを
消費するあらゆるコード（将来の L0b 閉ループも含む）が「`status` だけ見て
`errors` を信頼しない」経路を作れない。歴史的 `validation.json`/
`report.json`（いずれも `status: "pass"` かつ `errors` キー自体を持たない）
はこの制約下でも問題なく parse できることを確認済み。

## (d) report 正規形 — JSON 統一・境界秒・notes 白リスト

正 = `src/svp_rpe/authoring/report.py:AuthoringDiffReport`
（`frozen=True, extra="forbid"`）。L0-s 観測記録 §3.3 の 3 点を編入:

1. **境界時刻**（観測①）: `AxisReport.observed_sections` に
   `{label, start_seconds, end_seconds}` のリストをスキーマとして追加。
   **境界宣言**: この PR ではどの生産器も populate しない（配線は L0b）。
   `start_seconds`/`end_seconds` は `>= 0`（PR #246 Codex P2 review 7 巡目
   B）かつ `end_seconds > start_seconds`（ゼロ長・逆転区間を拒否）——
   計器が返す実測境界秒が負値・逆転になるのは計器/生産器側のバグであり、
   著者向け報告としては構成不能な壊れた区間として fail-closed に扱う。
2. **計器の分解能・可行域**（観測②）: 上記 (b) に編入（スキーマ変更なし）。
3. **notes 白リスト**（観測③）: `AuthoringNote.kind` を
   `Literal["position_match_rate"]` に制限する。新しい kind を追加する際は
   この `Literal` と `report.py` モジュール docstring の一覧を両方更新する
   こと。`value` は `kind` ごとの値検証（`_NOTE_VALUE_VALIDATORS`、PR #246
   Codex P2 review 8 巡目 A）で検証する——従来 `value: Any` で無検証
   だった。`position_match_rate` は有限数（`bool` 除外、`math.isfinite`）
   かつ `0.0 <= value <= 1.0`（`svp_rpe.arrange.observe` の
   `measurements["position_match_rate"]` と同じ「比率」という値域）。
   新規 kind を白リストへ追加する際は `_NOTE_VALUE_VALIDATORS` へ対応する
   検証関数を**同時に**登録すること（欠けている kind は構築時に
   `ValidationError` で fail-closed になる——白リストに kind だけ足して
   値検証を忘れる片手落ちを防ぐ）。
4. **verdict 語彙の凍結 + 軸整合**（PR #246 Codex P2 review 7 巡目 A）:
   `AxisReport.verdict` は `Verdict = Literal["preserved", "deviated",
   "exact_match", "mismatch"]` に固定する（従来は任意 `str` を受理して
   いた）。さらに `AuthoringDiffReport` が既知軸名（`key`/`brightness` は
   `preserved`/`deviated`、`structure` は `exact_match`/`mismatch`）ごとに
   verdict の部分集合を強制する——`key` 軸に `exact_match` のような他軸の
   verdict を書くと、`Verdict` 全体としては合法でも軸整合違反として拒否
   される。**境界宣言**: `_AXIS_VERDICTS` に無い軸名（将来 L0b が追加する
   軸）は `Verdict` の全語彙をそのまま許容する——この report スキーマは
   軸集合そのものを固定しない、という上記 (e) の方針と整合させるため。
5. **verdict×band の整合（D5 の成功会計除外規則）**（PR #246 Codex P2
   review 8 巡目 B）: 成功側 verdict（`preserved`/`exact_match`）は
   `band == "measured"` のときのみ許容する（`AxisReport` 単体の
   `model_validator`）。`out_of_band`/`not_observed` な数値・ラベルは
   修正の根拠に使ってはならないという D5（正本 §5 の帯域注釈規律）を、
   report スキーマ自身が成功宣言に対して強制する——`out_of_band` の
   `preserved` のような「帯域外なのに成功を主張する」矛盾組は構成不能。
   失敗側 verdict（`deviated`/`mismatch`）は非 `measured` band でも許容
   したまま（「未確認だが成功も主張していない」正直な報告は制約しない）。
6. **`symbolic_validation.status`×`axes`×`notes` の provenance 整合**
   （PR #246 Codex P2 review 10 巡目 + 12 巡目、4〜5 巡目の
   `SymbolicValidationResult` 内 `status`×`errors` 整合と同族）:
   `status == "fail"` の報告は `axes` と `notes` の両方が空でなければ
   ならない。正本 §3 のフロー（`[2] 記号検証ゲート` を通過した Score
   だけが `[3] 実行と計測` へ進む）上、記号検証に落ちた Score は決して
   計測されない——`axes` に測定済みらしき値が乗っている、または `notes`
   に測定由来の参考値（現行の白リスト `position_match_rate` は構造観測器
   由来の実測値）が乗っている `status: fail` の報告は、いずれもフローの
   因果関係と矛盾する provenance であり構成不能にする（12 巡目: 10 巡目
   の `axes` 限定ガードでは `notes` 経由で同じ矛盾がすり抜ける同族の
   残り穴だった。`AuthoringDiffReport` の `model_validator`）。
   `status == "pass"` では `axes`/`notes` の空/非空いずれも許容する。
   **終端宣言**: このガードは `AuthoringNoteKind` の現行白リストが全
   kind 観測器由来の測定 provenance である前提に依存する——将来、著者の
   意図表明など非測定系 kind を白リストへ追加する場合は、この検証を
   「全 notes 空必須」から「kind ごとに測定系/非測定系を区別する」形へ
   再設計する必要がある（現時点で非測定系 kind は存在しないため、その
   区別を先回りして作らない）。
7. **`observed_sections` の軸限定**（PR #246 Codex P2 review 13 巡目、
   4 の verdict×軸整合と同族の軸不整合）: `AxisReport.observed_sections`
   は structure 軸の境界時刻としてスキーマ設計されたフィールド（上記
   (d) 冒頭 1 参照）だが、`AxisReport` 単体は軸名を知らないため軸不整合
   な組み合わせ（例 `axes.key.observed_sections`）を構成できてしまう。
   `AuthoringDiffReport` が軸名 `"structure"` のときのみこのフィールドの
   非 None/非空値を許容し、それ以外（`key`/`brightness`、および未知軸）
   では拒否する。**4 との線引き**: 4 の verdict 語彙は未知軸を許容する
   （汎用語彙は将来軸でも意味を持ちうるため）が、`observed_sections` は
   構造軸固有のスキーマであり未知軸がこれを名乗る根拠がない——将来 L0b
   が境界時刻を持つ新しい軸を追加する場合は、このホワイトリスト
   （`_OBSERVED_SECTIONS_AXES`）へ軸名を明示的に追加すること。

JSON 直列化はバイト決定論（`report.py:dump_json_bytes` —
`sort_keys=True` + 末尾改行 + UTF-8 encode 済みバイト列を構築し、
`svprpe validate -o` はその同一バイト列を
`svp_rpe.utils.atomic_io.atomic_write_bytes`（tempfile + `os.replace`、
PR #246 Codex P2 review 8 巡目 C）経由で publish する——部分書き込みが
完全な報告として観測されることを防ぐ。`write_text` のプラットフォーム
依存改行変換を避ける規約自体は `validate_score.py`/`measure_round.py`
と同じ。`exclude_none=True` で歴史的 `report.json`（`examples/l0s_spike/
rounds/round{1..5}/report.json`）とスキーマ後方互換——`errors`/
`observed_sections` を省略した形をそのまま parse できる。5 本とも
`key`/`brightness: preserved`・`structure: mismatch`（いずれも
`band: measured`）で上記 4/5 の軸整合・verdict×band 整合を満たすことを
確認済み）。

## (e) 信頼軸表 — 凍結 YAML への参照 + 導出規則

正 = [`config/authoring_trusted_axes_l0.yaml`](../config/authoring_trusted_axes_l0.yaml)
（`schema_version: "authoring-trusted-axes/1.0"`）。手書きリストの陳腐化を
防ぐため、`src/svp_rpe/authoring/trusted_axes.py:derive_trusted_axes()` が
出典計器から機械導出した結果を凍結したもの
（`tests/test_trusted_axes.py` が再導出との一致を enforce）。

導出規則（詳細 = `trusted_axes.py` モジュール docstring）:

- 物理軸: 出典 = R0 `ROUNDTRIP_FIELDS`（`svp_rpe.roundtrip.diagnose`）。K1
  grip map の `classification == "dead"`（`active_rate_target`/
  `valley_depth_target` — knob_dead）と、`diagnose.py` の恒常
  sensor_blind（`stereo_width`/`time_signature`）を除外。残る
  `bpm`/`key`/`brightness` に `source_instrument` を付与。`brightness` は
  さらに `band_restriction: {trusted_values: [dark]}`（bright 帯は演奏者の
  押し込み不足が正本 §5 で実測済み）。
- 構造軸: 出典 = AR4 `svp_rpe.arrange.observe` structure domain
  （`svprpe observe`、実配線済み）。
- 出典を示せない軸は表に載せない（正本 §5「暗黙の例外を作らない」）。

**境界宣言（bpm）**: `bpm` はこの表に出典計器の構造上載るが、正本 §5 は
BPM 抽出器の octave/halving 誤検出を理由に「事前登録課題の BPM が信頼帯内と
確認できた場合のみ算入」という**実験ごとの動的判定**を別途要求する。この
凍結表は「出典計器が存在するか」という静的事実のみを表し、個別実験での
band 判定（`measured`/`out_of_band`/`not_observed`）は L0b が D5 規則に
従って都度決める——本表への `bpm` の掲載はその動的判定を代替しない。

`brightness` の `band_restriction` は表内フィールドとして明記される一方、
この境界宣言が docstring/本文にしかないと、表だけを読む監査者が `bpm` を
無条件信頼と誤読しうる（Fable レビュー指摘、非対称の是正）。そのため `bpm`
エントリには `runtime_gate` フィールドとして上記の境界宣言を 1 文で表内にも
複製する（決定論な固定文字列——実験ごとに変わる値そのものではなく、判定
規則への参照）。`key`/`brightness`/`structure` に `runtime_gate` はない
（`brightness` は `band_restriction` が同種の役割を表内で既に果たす）。

## (f) 凍結・pin 規則

正本 D1（アダプター=決定論の国境）の pin 既定則をこの契約にも適用する:
実験を開始する時点で、使用する `config/authoring_contract_l0.yaml` と
`config/authoring_trusted_axes_l0.yaml` の content hash（sha256）を実験台帳
（L0-s の `ledger.yaml` に相当する形式）へ pin する。周回間の spec 変更は
禁止（LLM 可視入力が変わると収束 evidence が比較不能になる、正本 §3 と
同じ規則）。spec を変更する必要が生じたら別の実験として最初から記録し直す。

## 関連

- [`llm_adapter_planning.md`](llm_adapter_planning.md) — 正本（D1-D7, §3, §4, §5）
- [`l0s_spike_record.md`](l0s_spike_record.md) — 本契約 v1 の観測材料
- [`cli.md`](cli.md) — `svprpe validate` コマンドリファレンス
- `examples/l0s_spike/contract.md` — L0-s 契約 v0（凍結済み、本文書の base）
