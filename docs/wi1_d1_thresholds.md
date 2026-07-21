# WI1 — 逸脱分布と D-1 閾値 Design Memo（structure / harmony）

日付: 2026-07-21（`date -u` 実測確認済み）
状態: WI1 完了報告 + D-1 分類規約 v0 の確定案（実配線は WI4）
起点: [`work_identity_roadmap.md`](work_identity_roadmap.md) WI1 節

## 1. 目的と位置づけ

MusicGen 無人バッチ n=20（canonical 規律）で structure / harmony の逸脱分布を
取得し、observe の `adherence_status` を現行 2 値（preserved / not_observed）から
`changed_within_policy` / `changed_outside_policy` を含む分類へ進めるための
**D-1 分類規約**（判定関数と閾値）を設計する。本 Memo は設計と根拠の正本であり、
observe への実配線は行わない（WI1 受け入れ条件どおり。配線は WI4）。

## 2. バッチ provenance（分布 fixture）

- fixture: `examples/arrangement/midnight_signal/observed/wi1_musicgen_n20/`
  （`wi1_plan.yaml` / `wi1_generation_timestamps.yaml` /
  `wi1_takes_manifest.json` / `wi1_observation_take{0..19}.json` /
  `wi1_determinism_spot_check.yaml`。wav 20 本は sha256 pin のみで非コミット
  — #196 前例）
- 条件: #193（AR4 form 実測）と同一 — `facebook/musicgen-small`
  @ `4c8334b02c6ec4e8664a91979669a501ec497792`、duration 30.0s、
  guidance_scale 3.0、performance_package は `svprpe package` で決定論再構築し
  sha256 が #193 pin と byte 一致することを生成前に検証済み
- seed 系列: `8200 + take_index`（0..19）。#191 の 8000 系 / #193 の 8100 系と
  非衝突の新規単一バッチ（交絡隔離: 既存 take の混入なし）
- canonical 条件（AGENTS.md §8）: 事前登録 `plan_confirmed_at_utc =
  2026-07-21T03:49:01Z`（全生成開始時刻に先行することを timestamps で裏付け・
  ABBA/均衡ゲート B 非適用の根拠 = 決定論 seed にドリフト機構不在を plan に記載）
  + fresh-process 決定論スポット検証 = 時間的に最遠の take0 / take19 を
  別プロセス再生成し sha256 byte 一致 **2/2**
- observe: 20/20 が provenance chain 検証（D-3）を通過。パスは相対で記録

## 3. 実測分布

### 3.1 structure（正典 C = [intro, verse, chorus, bridge]）

観測列（正規化後）は 5 パターンに完全分類された:

| 観測パターン | 本数 | take |
|---|---|---|
| [intro, outro] | 4 | 0, 9, 11, 17 |
| [intro, chorus, outro] | 8 | 3, 7, 10, 12, 13, 14, 15, 16 |
| [intro, chorus, chorus, outro] | 5 | 1, 2, 4, 5, 19 |
| [intro, chorus, bridge, chorus, outro] | 1 | 8 |
| [intro, bridge, chorus, chorus, outro] | 2 | 6, 18 |

分布ファクト:

- **verse は 20/20 で未観測**（#193 n=2 の「30s でも verse 非表現」を n=20 で確定）
- **outro 挿入は 20/20 全数、常にちょうど 1 個**（挿入ラベルは outro のみ）
- **正典順序破壊（bridge が chorus に先行）は 2/20 = 10%**
- chorus 反復は 6/20（隣接 5 + 非隣接 1）、いずれも +1 回
- `sequence_exact_match` は 0/20、`position_match_rate` は 0.2–0.5

### 3.2 harmony（正典進行長 4）

- **`full_cycles = 0` が 20/20** — 正典進行の 1 周一致は皆無
- `collapsed_match_fraction`: 17/20 が 0.0。非零 3 件は
  take5 = 1.0（ただし `collapsed_observed_length = 1` の退化 — 和音 1 個が正典
  先頭と偶然一致しただけ）、take6 = 0.25、take16 = 0.2
- take18 は `observed_length = 0`（和音イベント検出ゼロ = 観測空）

## 4. structure 逸脱の編集分解（D-1 正式定義・新設アルゴリズム）

入力: 正典列 C（ラベル重複なし）、観測列 O（observe の `observed_sections`
正規化後）。

1. **first-occurrence 列** F(O) = O から C に属する各ラベルの初出のみを順に
   取った列
2. **order 判定**: F(O) が C の（連続とは限らない）部分列でなければ
   **reorder**（AR2-3 語彙外）で語彙外確定
3. F(O) が部分列のとき、分解は**一意に**決まる（探索的整列を使わないため
   多重解なし）:
   - `omission` = |C| − |F(O)|（未観測の正典ラベル数）
   - `insertion` = O 中の非正典ラベルのトークン数
   - `repetition` = O 中の正典ラベルのトークン数 − |F(O)|（初出以降の再出現。
     隣接に限定しない）
4. **within-vocabulary** ⇔ reorder なし

### 手計算アンカー（検算照合ゲート用の固定例。i=intro, v=verse, c=chorus, b=bridge）

| 例 | O | reorder | om | ins | rep |
|---|---|---|---|---|---|
| #193 take0 | i,c,c,b,outro | なし（F=i,c,b） | 1 | 1 | 1 |
| #193 take1 | i,outro | なし（F=i） | 3 | 1 | 0 |
| Suno A2 | i,v,c,b,c,v,v,outro | なし（F=i,v,c,b=C） | 0 | 1 | 3 |
| Suno B1 | i,c,b,v | **あり**（v が b の後） | — | — | — |

## 5. D-1 分類規約 v0（structure）

`changed_within_policy` は 3 段ゲートの全通過で与える:

- **ゲート 0（観測前提）**: `observed_length > 0`。観測空はセンサー無出力で
  あり逸脱ではない → `not_observed` 維持（take18 harmony が実例）
- **ゲート 1（契約・質的）**: 編集分解が reorder を含まず、かつ現れた変形種
  （insertion / omission / repetition のうち回数 > 0 のもの）が identity
  manifest の当該 anchor に宣言された allowed transforms の部分集合であること。
  **宣言的契約が主**: 作者が repetition を許していなければ repetition 1 回でも
  outside。anchor に policy 宣言がない場合は分類せず現行どおり
  `not_observed` / `deferred` 維持（midnight_signal の form manifest は
  structure anchor が `required: false` で policy 不在 — 本バッチの分布解析は
  AR2-3 全語彙 {insertion, omission, repetition} を**参照ポリシー**として
  適用した計器特性の記述であり、この作品の契約判定ではない）
- **ゲート 2（予算・量的、v0 既定値。per-work 上書きは WI4 identity budget）**:
  - `insertion ≤ 1` — 実測全 26 観測（本バッチ 20 + #193 n=2 + Suno n=4）で
    挿入は最大 1（かつ全て outro）。ラベル制限型（framing 系のみ許可）は
    設計代替案として記録し、v0 は回数制のみ採用
  - **正典被覆 |F(O)| ≥ 2**（|C| 相対表現では coverage ≥ 0.5 @ |C|=4）—
    coverage 1/4 の 4 take は観測が [intro, outro] のみ = 正典の内容
    セクションをひとつも含まず、intro は任意の楽曲に共通する framing で
    弁別力を持たない（WI 思想 2「弁別判定」）。omission 回数分布 {1,2,3} に
    空隙はないため、この床は分布の空隙でなく弁別原理から導出し、分布は
    「床の下に退化族 [intro,outro] が実在する」ことの実証として使う
  - `repetition` は**予算なし**（語彙membership のみ）— 楽式上の反復は正規の
    形式操作で、実測も MusicGen ≤ 1 / Suno A2 = 3 と生成器依存に割れ、回数
    上限に弁別的根拠がない

`changed_outside_policy` = ゲート 0 は通過（観測あり）だがゲート 1 または 2 に
不合格。`preserved` / `not_observed` の現行意味は不変。

### n=20 への機械適用（参照ポリシー下）

- **within**: 14/20（[i,c,outro] ×8、[i,c,c,outro] ×5、[i,c,b,c,outro] ×1）
- **outside**: 6/20（reorder ×2、被覆床不合格 [i,outro] ×4）
- 退化しない分割（全数 within でも全数 outside でもない）= 閾値が分布上で
  実際に弁別している

### 中立候補入力への適用（規約確定後の機械適用）

- 実 Suno A2 → within（被覆 4/4・ins 1・rep 3・reorder なし）
- 実 Suno B1 → outside（reorder）
- #193 take0 → within（om 1 / ins 1 / rep 1・被覆 3/4）、take1 → outside（被覆床）

A2 / B1 は 2026-07-19 デモの定性判断（A2 = 許容語彙内で唯一 / B1 = 順序破壊）と
一致した。**honesty**: この 2 件の定性ラベルは設計時に既知であり、ブラインド
テストではない（設計の試金石として使用したことを開示する）。

## 6. harmony の裁定 v0

**v0 では harmony の within / outside 分類を定義しない**（事前登録済みの有効な
結論）。根拠:

- `full_cycles = 0` が 20/20 — 現行センサー（(root, quality) 正規化 +
  cycle-alignment）の分解能と本素材条件では、許容変形（chord_extensions /
  functional_substitution）と単なる不一致を分離する信号が観測されない
- 将来閾値化する場合の設計材料 2 点を記録:
  (a) `collapsed_match_fraction` 単独の閾値化は不可 — take5（長さ 1 で 1.0）の
  退化例により**長さ床の併置が必須**、(b) 観測空（take18）は逸脱でなく
  `not_observed` に送る（ゲート 0 と同型）
- harmony anchor は現行どおり `not_observed` / `deferred` を維持

## 7. WI4 配線設計（本 Memo では実装しない）

- sidecar の `ObservationAdherenceStatus` を
  `{preserved, changed_within_policy, changed_outside_policy, not_observed}` へ
  拡張。`not_observed` は「センサー欠如・観測空・policy 不在・分類未定義
  ドメイン」へ純化
- **`determination` の語彙 `{exact_match, deferred, no_sensor}` は不変**
  （WI1 受け入れ条件）。マッピング: 分類が within / outside どちらに出ても
  `determination = deferred` のまま（分類は D-1 規約による解釈層であり、
  センサー生一致の 3 値とは別軸）。validator は
  `changed_* → determination == deferred` を強制
- 分類は default off の advisory 層として配線（#128 / proxy 前例の本文不変規律）

## 8. Honesty / 限界

- 本分布は**忠実レンダー**（意図的破壊なし）の生成器自然逸脱 = この
  (work, generator, 30s) 条件の再生成ノイズ特性。予算値はノイズ床由来の
  既定値であり、弁別力の検証は WI2 の破壊 recast 行列で行う
- **30s 尺の交絡**: omission は尺切り詰めの寄与を含む（verse 0/20 は
  「MusicGen が verse を表現しない」と「30s に収まらない」の合成）。被覆床は
  本条件下の導出であり、他尺では再校正が必要
- 単一 work・単一生成器・n=20。閾値の一般化主張はしない（per-work 上書きが
  WI4 identity budget の設計対象）
- reorder 2/20 は「忠実レンダーでも 10% は語彙外に落ちる」ことを意味する —
  within 分類は品質保証ではなく契約履行の判定である
- structure ラベルは抽出器の区間ラベラー出力に依存する（センサー系統の
  限界は #192 に従う）

## 9. 検算照合ゲート（AGENTS.md §8）

- §4 の手計算アンカー 4 例と §5 の n=20 適用結果（within 14 / outside 6・
  パターン別本数）は、fixture テスト
  `tests/test_wi1_musicgen_n20_fixture.py` が committed observation JSON から
  §4 のアルゴリズムを**独立に再実装・再計算**して照合する（Memo の数表が
  機械接地される）
- 分解能要件: 本 Memo の全量は observe の既存出力（`observed_sections`
  正規化後 / harmony 集計値）のみから計算可能 — 新規センサー不要を確認済み
