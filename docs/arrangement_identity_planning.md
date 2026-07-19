# Arrangement Identity Track Planning

**Status**: AR0 計画文書。AR1、AR2-1/2/3、AR3-1/2 は実装済み。AR2-3 は
**2026-07-19 に解凍・同日実装完了**（AR2-3 Design Memo (Fable, 2026-07-19): section-map/0.2 の
stable ID、`section_ref` 解決規則、structure 変形語彙 3 語追加）、AR4 は計器配線済み
（`svprpe observe` + `ObservationReport` sidecar。harmony + structure domain を
実測、判定閾値は未定・実 Suno 生成物での観測は未実施）。

> この文書は、元の未コミット AR0 ドラフトが checkout 内に残っていなかったため、
> 2026-07-15 のユーザー承認に基づき、マージ済み PR #175–#181、現行コード、
> `.claude/memory/2026-07-14.md` の実装記録から再構成した。失われたドラフトの
> 文言を復元したものではなく、確認できる設計契約だけを正典化する。

## 1. 目的

Composition PoC の C5「Layer Manipulator」を、元作品として残す要素を明示しながら
編曲できる経路として具体化する。Base `CompositionScore` に別入力の編曲指定を適用し、
派生 Score、変更差分、provenance、identity artifact の保持要求、生成器への配送可否、
生成後の観測を段階ごとに分離して扱う。

このトラックの中心課題は、「編曲指定を受理できること」と「同じ作品として保持された
こと」を同一視しないことである。要求、配送、制御、観測を別の記録として残し、観測前の
段階で聴覚的同一性を宣言しない。

## 2. マイルストーン

| 段階 | 完了条件 | 現状 |
|---|---|---|
| Score-level preservation (M1) | 1 つの Base Score から異なる Derived Score を決定論的に作り、`semantic.core` と `physical.key` の保持を Score と差分で確認する | #177 で完了 |
| Artifact delivery preservation | identity artifact と保持契約を生成器の実入力チャネルへ配送し、配送不能を明示状態として記録する | AR3-2 compiler 実装済み。実 backend 縦切り E2E は未完了 |
| Observed musical identity preservation | 生成後成果物を anchor ごとのセンサーと adherence 指標で比較し、観測結果を記録する | AR4: harmony センサー実配線・決定論 synth 実測のみ（実 Suno 生成物は未観測） |

M1 の正典表現は、**「意味核（`semantic.core`）とキー（`physical.key`）を保持した
Score-level identity preservation demo」**である。これは聴覚的同一性、メロディ保持、
歌詞保持を実証したものではない。Artifact delivery と生成後比較が成立する前に
「同じ曲として保持できた」と判定してはならない。

## 3. データフロー

```text
Base CompositionScore + ArrangementSpec
  -> deterministic resolver
  -> Derived CompositionScore + field-level diff + provenance bundle

IdentityManifest + ArrangementSpec.preservation
  -> PreservationContract

PreservationContract + InputCapabilityProfile
  -> PerformancePackage / CompilationReport       (AR3-2)
  -> backend generation
  -> observed artifact + anchor adherence report  (AR4)
```

各段の出力は次段への入力であり、前段の成功を後段の成功へ読み替えない。

## 4. 設計判断

### D1: canonical `CompositionScore` は変更せず sidecar-first とする

編曲指定、identity artifact、保持契約、生成器 capability は canonical
`CompositionScore` に追加しない。`ArrangementSpec`、`IdentityManifest`、
`PreservationContract`、`InputCapabilityProfile` を独立モデルとして持つ。
Score は作品記述、sidecar は編曲セッション固有の要求・証跡・配送情報を担う。

この分離は #175 の resolver と #178 の `IdentityManifest` で実装済みであり、
`src/svp_rpe/arrange/identity.py` は `CompositionScore` を直接 import しない。

### D7: hard anchor の暗黙テキスト代替を禁止する

生成器が symbolic melody や MIDI を受け取れない場合、hard anchor を
`"keep the same melody"` のような prompt 文言へ暗黙に降格しない。配送不能は
`unsupported` または `unknown` として記録し、strict モードでは失敗、advisory
モードでは警告付き package とする。代替は、将来の明示的 fallback policy が指定された
場合に限る。

これは artifact を渡した事実と、曖昧な自然言語で保持を依頼した事実を混同しないための
境界である。D7 は AR3-2 の `PerformancePackage` compiler で実装済みである。

### マージ済み実装で固定された制約

| 制約 | 内容 |
|---|---|
| 4 軸分離 | fixity（採譜確定）、preservation（編曲可否）、capability（入力チャネル存在）、adherence（生成後観測）を別モデルで扱う |
| 保持モード | `hard` は変更禁止、`elastic` は列挙した変形のみ許可、`free` は列挙による制約を課さない |
| capability と grip の分離 | `supported` は入力チャネルの存在だけを表し、保持精度や制御性を保証しない |
| 推測補完禁止 | 省略 policy を free、未掲載 channel を supported/unsupported、欠落 hash や provenance を推定しない |
| 決定論 | タイムスタンプ・絶対パス・output directory を成果物へ埋め込まず、同一入力から byte-identical な成果物を作る |
| 永続成果物の安全 | loader と compiler は [`AGENTS.md`](../AGENTS.md) §8 の Persistent Artifact Safety Gate に従う |

## 5. フェーズ

### AR0: 計画と境界の固定

本書で目的、マイルストーン、D1/D7、フェーズ境界を正典化する。実装済みコードの説明を
後付けで拡張するのではなく、今後の AR3-2/AR4 が越えてはならない境界を固定する。

### AR1: 決定論的な Score-level 編曲

- **AR1-1 (#175)**: `ArrangementSpec` と `resolve_arrangement`。明示 allowlist 上の
  部分 override、hard/elastic/free、source 非改変、安定順 diff を実装。
- **AR1-2 (#176)**: `svprpe arrange`、derived Score、bundle、diff、入力 hash を含む
  provenance。全構築後公開と byte-identical 出力を実装。
- **AR1-3 (#177)**: 1 Base Score から EDM/Jazz の 2 variant を生成する committed
  fixture。M1 の Score-level identity preservation を実証。

AR1 は Score の保持だけを扱い、外部 artifact の配送や生成後音声の同一性を扱わない。

### AR2: identity artifact と保持契約

- **AR2-1 (#178, #181)**: hash 検証付き `IdentityManifest` sidecar。
  `artifact_type`、`media_type`、`format_version` で形式を明示し、source/anchor の
  path と sha256 を検証する。
- **AR2-2 (#179, #181)**: anchor ごとの `hard` / `elastic` / `free` と許容変形から
  `PreservationContract` を構築する。省略 anchor を推測で補完しない。
- **AR2-3 (実装済み、2026-07-19、AR2-3 Design Memo (Fable))**: structure anchor の
  stable ID と section policy。
  実 form artifact を用いる AR4 の結果が得られるまで保留する。
  **2026-07-19 解凍判断（AR4 実生成物 n=2 到着後）: 保留継続**。今回の実観測は
  harmony domain のみで、form/structure anchor は manifest に存在せず、12 秒
  クリップは form を表現できないため、保留条件（実 form artifact での AR4 結果）は
  未充足。ただし律速の性質は「人手生成待ち」から次の 2 点へ変質した:
  (a) structure センサーの observe 配線（`rpe/structure.py` / `structure_labels.py`
  の既存資産を流用可能）、(b) form が存在する長尺 artifact の用意（MusicGen は
  30s 上限のため相性要検討・Suno なら人手律速）。(a) は Claude 完結可能な先行
  タスクであり、AR2-3 の解凍は (a)+(b) 充足後に再判断する。
  **2026-07-19 再判断（同日の form 実測 n=2 到着後・上記保留継続判断の後刻）: 解凍**。(a) は #192、(b) は MusicGen 30s × structure
  anchor 入り manifest の実測（`observed/musicgen_form/`、n=2）で充足 — 保留条件
  「実 form artifact での AR4 結果」は文言どおり取得された。実データは AR2-3 の
  設計空間を直接照らす: 挿入（正典に無い outro の観測）・欠落（verse は 2 take
  とも一度も観測されず）・重複（chorus×2）・長さ振れ（正典 4 に対し観測 5 / 2）が
  実生成の常態であり、section policy は厳密一致以外の許容カテゴリ（挿入・欠落・
  並び替えの扱い）を、stable ID は不安定な観測系列への対応付けを、それぞれ前提に
  設計する必要がある。留保: n=2・MusicGen 30s のみ・Suno 未観測。本解凍は
  **設計着手の解凍**であって form 制御性の主張ではない。
  **2026-07-19 実装完了（AR2-3 Design Memo, Fable — 本節がその規範仕様の反映先）**: 解凍直後、同日中に設計から
  実装まで完了した。実装内容は 3 点:
  1. **section-map/0.2**（`arrange/section_map.py`、`identity.py`/`observe.py`
     双方が依存する第三モジュール — `observe.py` が `identity.py` を import する
     既存の一方向依存を壊さないための切り出し）: 既存 `section-map/0.1`（id 概念
     なしの順序付きラベル列。committed fixture
     `examples/arrangement/midnight_signal/identity/section_map.json` が pin
     している形式で、無変更のまま維持する）と並置で、各セクションが artifact
     内で一意な安定 `id` + `label` を持つ `section-map/0.2` を追加した
     （未知キー・空 `sections`・id 重複・空 `id`/`label` は fail-closed）。
     `is_structure_sensor_anchor`（`observe.py`）は 0.1/0.2 いずれの
     `format_version` 宣言も structure センサーへ配線するよう拡張し、宣言
     `format_version` と artifact 内 `schema_version` の不一致は各 format 専用
     parser の Literal 検証により自然に fail-closed になる。0.2 anchor の
     observe 実行では、既存の label 系 measurements（`canonical_sections` /
     `sequence_exact_match` 等、0.1 と完全に同一のロジック）に加えて
     `canonical_section_ids`（artifact 記載順の生 id 列）を記録するが、
     D-1 の恒等判定（`sequence_exact_match`）は 0.1 と同様 label 系列のみを
     根拠とし、id は一切使わない。0.1 anchor の observe measurements は
     1 bit も変わらない（`canonical_section_ids` キー自体が存在しない）。
  2. **`section_ref` 解決**（`identity.py`）: manifest 内に
     `format_version == "section-map/0.2"` の structure anchor が 1 件以上
     あれば、非 `None` の `section_ref` はそれら anchor の（複数あれば合併した）
     section id のいずれかに解決されなければならず、解決できなければ
     `IdentityManifestError`（dangling reference、fail-fast）。合併した id が
     複数の 0.2 anchor から重複宣言されている場合も同様に fail-fast。0.2
     structure anchor が manifest に無い場合、`section_ref` は AR2-2 以前と
     同じく opaque な文字列のまま検証しない（既存 manifest の後方互換 —
     `test_note_and_section_ref_are_optional_and_preserved` は無変更で pass）。
     stable id は section-map artifact 自身が持ち、`IdentityAnchor` 側には
     複製しない（sidecar-first: id は「1 artifact 内の部分」の識別子）。
  3. **form 変形語彙 3 語**（`models.py` の `AllowedTransformation` +
     `contract.py` の `DOMAIN_ALLOWED_TRANSFORMS["structure"]`）:
     `section_insertion` / `section_omission` / `section_repetition` を追加した。
     上記の MusicGen 30s form 実測（挿入=正典外 outro の観測・欠落=verse が
     2 take とも未観測・反復=chorus×2）に由来する語彙であり、推測補完ではない。
     既存の `intro_extension`（intro 延長という特定の楽曲的意図）・
     `instrumental_break`（間奏挿入という特定の楽曲的意図）とは意図の粒度が
     異なる、より一般的な form レベルのカテゴリとして共存する — 前者を後者の
     別名やコード語彙上位互換として削除しない。3 層検証（`AnchorPreservation`
     の mode/allow 整合、`build_preservation_contract`/`ContractAnchor` の
     domain-vocab cross-validation、`PackageAnchorStatus` への転記）は
     `AllowedTransformation` Literal と `DOMAIN_ALLOWED_TRANSFORMS` の更新のみで
     自動追従し、他 domain（例: melody）へのこの 3 語の指定は既存の
     domain-vocab 検証により引き続き拒否される。

  閾値分類（挿入/欠落/反復のうちどれが「保存範囲内」かの判定基準）は本実装の
  範囲外のまま — AR2-3 が供給したのは計器（section-map/0.2 + `section_ref`
  解決）と契約語彙（3 変形カテゴリ）であり、判定条件は実測を積んでから別
  Design Memo が固定する（D-1 の原則を AR2-3 にも適用）。

AR2 の完了は「何を残したいか」と「どの変形を許すか」を機械可読にしたことを意味し、
生成器へ渡せたことは意味しない。

### AR3: backend capability と artifact delivery

- **AR3-1 (#180, #181)**: `InputCapabilityProfile`。`style_prompt`、`lyrics_text`、
  `section_tags`、`reference_audio`、`symbolic_melody`、`midi` の support 状態を
  `supported` / `experimental` / `unsupported` / `unknown` で記録する。
- **AR3-2 (implemented)**: `PreservationContract` と capability profile から
  `PerformancePackage` と `CompilationReport` を構築する。anchor ごとに
  requested / deliverable / controllable / observed を分離し、strict/advisory と
  D7 の暗黙代替禁止を実装した。compile 時点の control は `unknown`、observation は
  `not_observed` に固定し、配送可否を保持実績へ読み替えない。

Artifact delivery preservation の完了には、少なくとも 1 backend へ hard melody
artifact を実配送し、package と backend invocation の双方で同一 artifact/hash を
確認する縦切り E2E が必要である。

2026-07-17: 1 曲 (`midnight_signal`) x 1 編曲 (`edm.identity.arrangement.yaml`) x
1 生成器 (suno standard) x 実在 artifact (lyrics / melody) で
IdentityManifest -> PreservationContract -> InputCapabilityProfile ->
PerformancePackage を実 CLI で通す縦切り E2E fixture + テストを追加した
（`tests/test_e2e_vertical_slice.py`）。hard melody anchor は committed
suno profile へ配送不能であり（`symbolic_melody: unsupported`）、E2E はそれを
成功偽装せず requested=hard / delivery=unsupported / control=unknown /
observation=not_observed の 4 状態として記録することを実証した。同一 package 内で
hard x supported な lyrics anchor が delivery=delivered となる対照も同時に固定し、
delivery≠preservation の語彙分離を可視化した。配送成功や聴覚的同一性は主張しない。
`compilation_report.json` は `invocation_provenance.compiler.git_commit` を含み
コミット毎に変わるため committed byte-pin の対象外とし（フィールドレベル検証のみ）、
committed byte-pin は `performance_package.json` のみとした。

### AR4: 生成後の作品同一性観測

生成された音声または記号成果物を anchor ごとに比較し、adherence を観測する。
`requested=hard` や `delivery=supported` だけから `observed=preserved` を導出しない。
センサーが存在しない場合は `not_observed`、有効帯域外なら sensor limitation として
記録する。

最初の縦切りは 1 曲 × 1 編曲 × 1 生成器 × 実在 artifact 1 件とし、manifest、contract、
capability profile、performance package、生成物、observation report を同じ provenance
chain で結ぶ。聴覚的同一性の判定条件と閾値は、その artifact とセンサーの実測を得てから
別 Design Memo で固定する。

2026-07-17: 計器を配線した（`svprpe observe` + `ObservationReport` sidecar,
`observation-report/0.1`）。実配線したのは harmony domain のみ:
`identity_manifest.yaml` に harmony anchor（`chord_sequence_json`。正典進行は
`perform/performer.py` の C minor 既定進行 `MINOR_PROGRESSION` と一致させた
`identity/chord_progression.json`）を追加し、生成音声を `extract_rpe_from_file`
（依存ゼロの `compute_chord_events`）で実測する。measurements は生の frame 単位
指標（`chord_sequence_match_rate` / `repeated_chord_sequence_match_rate`。
透明性のため残すが恒等判定には使わない）に加え、繰り返しを織り込んだ collapsed
cycle-alignment 系列（`canonical_length` / `observed_length` /
`collapsed_observed_length` / `matched_cycle_prefix_length` /
`collapsed_match_fraction` / `unmatched_tail_length` / `unmatched_tail_head`）
を記録する。**`adherence_status` の恒等判定（D-1）はこの cycle-alignment
prefix が collapsed 列全体に一致するかどうかで行う**（frame 単位の生 match_rate
は判定に使わない — 「作品の和声的同一性 = 繰り返される正典進行」という計器
意味論への修正、2026-07-17 round 2）。lyrics/melody は optional extra 依存の
ためセンサー本体を配線せず `available: false` + reason のみ記録する（他
domain も同様に `no_sensor`）。`adherence_status` / `determination` は 3 分岐
のみ（no_sensor / preserved+exact_match / not_observed+deferred）—
**閾値判定は書いていない**。observe は package を書き換えず、provenance
chain（manifest sha256 / anchor artifact hash / package・音声 sha256）を
測定前に検証し、切れていれば exit 1 とする。

決定論 synth (`perform` + `FAITHFUL_TAKE`) による `expected/edm/derived_score.yaml`
E2E fixture の実測: `collapsed_observed_length=10`、
`matched_cycle_prefix_length=7`（正典 4 コード進行の 2 cycle 分＝スコアの
非ドローン区間 2 つ（verse/chorus）と符合）、残り 3 エントリが不一致の tail。
**解釈**（report 自体は事実のみを記録し、この解釈は含めない）:
この tail はおそらくドローンのみの intro/bridge 区間に由来する — 和声進行を
意図しない裸のルート音を chroma テンプレート検出器がそれでも何らかの
major/minor ラベルへ強制分類するため。
「観測できた」と言えるのは決定論 synth を実測したこの harmony 系列のみであり、
**実 Suno 生成物での観測は未実施**。判定条件・閾値は引き続き未定（実測を積んで
から別 Design Memo で固定）。詳細: [`cli.md`](cli.md) の `svprpe observe` 節。

2026-07-19: 上記の「実 Suno 生成物は未観測」を、**MusicGen ローカル生成の実観測**で
初めて埋めた（Design Memo AR4 実観測バッチ、#171/#136 と同じ MusicGen ローカル
決定論路線）。`midnight_signal` の EDM 編曲 + identity anchor（lyrics/melody/harmony
hard）+ `config/capability_profiles/musicgen.yaml` で `svprpe package` した
performance package（`prompt.text` は非空を確認済み、`svprpe verify` の実データ初
適用は 16 checks 全 pass）から、`facebook/musicgen-small@4c8334b0…`・CPU・
guidance_scale=3.0・12s・seed 8000/8001 で n=2 take を実生成した
（`scripts/collect_ar4_observation.py`）。決定論スポット検証（各 take を別プロセスで
再生成）は 2/2 一致。両 take とも `svprpe observe` の D-3 provenance chain 検証を
通過し（exit 0）、harmony anchor は実測されたが `not_observed`/`deferred`（collapsed
observed sequence が正典進行の 1 cycle も一致しない — take0 は
`matched_cycle_prefix_length=0`/`full_cycles=0`、take1 も同様）、lyrics/melody は
従来どおり `not_observed`/`no_sensor`。**実 Suno 生成物での観測は依然未実施のまま**。
事前登録（`ar4_plan.yaml` の `plan_confirmed_at_utc`）は生成タイムスタンプ
（`ar4_generation_timestamps.yaml`）に先行する。成果物一式:
`examples/arrangement/midnight_signal/observed/musicgen/`
（`ar4_plan.yaml` / `ar4_takes_manifest.json` /
`ar4_observation_take{0,1}.json` / `ar4_generation_timestamps.yaml` /
`ar4_determinism_spot_check.yaml`。WAV 自体は DD-A によりコミット対象外）。

2026-07-19: AR2-3 解凍条件 (a)（structure センサーの observe 配線）を充足した
（Design Memo `design_memo_structure_sensor.md`）。harmony と完全に同型の
「計器・verdict なし・D-1 3 分岐」設計で `domain == "structure" and
artifact_type == "section_map"` にのみ実配線する
（`is_structure_sensor_anchor`。それ以外の artifact_type を持つ structure
anchor は harmony 同様 no_sensor のまま）。正典は `section-map/0.1` artifact
（`sections`: 非空の文字列リスト、順序が正典。未知キー・型不正・未知
schema_version は fail-closed）、観測は `PhysicalRPE.structure`
（`SectionMarker.label` 列、extract で常に populate 済みのため新規抽出コード
不要）。両側を正規化（lowercase 化 + 末尾数字 strip。それ以外の変換はしない）
した上で先頭から位置整合させ、一致数 / max(正典長, 観測長) を
`position_match_rate` として記録する。**D-1 の恒等判定は正規化後の列が長さ・
順序とも完全一致するか（`sequence_exact_match`）のみで行う** —
`position_match_rate` は透明性のための生値であり閾値判定には使わない。harmony
の cycle-alignment のような繰り返し折り畳みは行わない（structure には
「繰り返す正典進行」に相当する周期構造の前提が無いため）。決定論 synth
（`perform` + `FAITHFUL_TAKE`）による `expected/edm/derived_score.yaml` E2E
実測（合成 manifest。`examples/arrangement/midnight_signal/` 本体は不変）:
実抽出器のセクションラベルは `Intro/Chorus/Bridge/Verse/Chorus/Verse2/Outro`
（大文字・繰り返し区間の自動連番付き）で、正典を正規化後の観測列と一致する
`intro/chorus/bridge/verse/chorus/verse/outro` に設定した結果
`sequence_exact_match=True` / `preserved`/`exact_match` に到達し、正規化規則が
実データに対して機能することを実証した。**AR2-3 の解凍は残る条件 (b)（form が
存在する長尺 artifact の用意）待ちのまま** — structure センサーの配線自体は
identity anchor の stable ID・section policy の意味論には一切触れておらず
（`IdentityAnchor.section_ref` は引き続き opaque 文字列）、AR2-3 本体（structure
anchor の stable ID / section policy 確定）とは独立。詳細:
[`cli.md`](cli.md) の `svprpe observe` 節。

2026-07-19: AR2-3 解凍条件 (b)（form が存在する長尺 artifact の用意）を
MusicGen 30s で実測した（Design Memo `design_memo_ar4_form.md`）。
`identity_manifest.form.yaml`（既存 `identity_manifest.yaml` + structure anchor
1 件。既存 manifest は不変）+ `identity/section_map.json`
（正典セクション名は `composition_score.yaml` の `structure` 欄
`[intro, verse, chorus, bridge]` からの機械転記）で package した performance
package から、`facebook/musicgen-small@4c8334b0…`・CPU・guidance_scale=3.0・
**30s**・seed 8100/8101 で n=2 take を実生成した
（決定論スポット検証 2/2 一致、`svprpe verify` 16/16 pass）。両 take とも
`svprpe observe` の D-3 provenance chain 検証を通過し（exit 0）、structure
anchor は実測されたが `not_observed`/`deferred` のまま — take0 は
observed raw sections `[Intro, Chorus, Chorus, Bridge, Outro]`
（正規化後 `position_match_rate=0.6`）、take1 は `[Intro, Outro]`
（正規化後 `position_match_rate=0.25`）で、いずれも正典 4 セクション
`[intro, verse, chorus, bridge]` と `sequence_exact_match=False`。harmony も
両 take とも `full_cycles=0`（0 cycle 一致）。**判定の事前登録どおり
preserved を成功条件にしていない**: この結果自体が
「MusicGen 30s は正典 verse を一度も表現せず、30s クリップでも section 系列を
完全再現しない」という律速確定の実測材料であり、条件 (b) の充足として記録する
（preserved の獲得ではなく、計器で実データを取得できたことが成果— D-1 の
事前登録どおり）。AR2-3 の解凍可否そのものの判断はこの実測データをもって
別途行う（本バッチのスコープ外）。成果物一式:
`examples/arrangement/midnight_signal/observed/musicgen_form/`
（`ar4f_plan.yaml` / `ar4f_takes_manifest.json` /
`ar4f_observation_take{0,1}.json` / `ar4f_generation_timestamps.yaml` /
`ar4f_determinism_spot_check.yaml`。WAV は DD-A によりコミット対象外）。

## 6. 実装状況

| Phase | 主成果物 | 状態 |
|---|---|---|
| AR0 | 本計画文書 | 完了 |
| AR1 | resolver / CLI / bundle / diff / EDM-Jazz fixture | 完了 (#175–#177) |
| AR2-1/2 | IdentityManifest / PreservationContract | 完了 (#178, #179, #181) |
| AR2-3 | structure anchor policy | **完了（2026-07-19、AR2-3 Design Memo (Fable)）**: section-map/0.2（stable id 付き section map、0.1 と並置）+ `section_ref` 解決（0.2 anchor 存在時のみ fail-fast 検証、それ以外は opaque 後方互換）+ structure 変形語彙 3 語追加（section_insertion/section_omission/section_repetition、実 form 実測由来）。閾値分類は範囲外のまま |
| AR3-1 | InputCapabilityProfile | 完了 (#180, #181) |
| AR3-2 | PerformancePackage compiler + 縦切り E2E fixture | 実装済み（E2E fixture 追加 2026-07-17） |
| AR4 | generated-output identity observation | 計器配線済み。harmony + structure を実測（decisive synth E2E + 2026-07-19 MusicGen 12s n=2 + 同日 MusicGen 30s form n=2）。判定閾値は未定、実 Suno 生成物は未観測 |

2026-07-17: bundle/report に `content_digest`（内容指紋）と、
`CompilationReport` 限定の `invocation_provenance.compiler`（実行環境の監査記録、
digest からは除外）を追加し、`arrange` / `package` に opt-in の不変
`--builds-root` 出力先を配線した。AR4 の生成後観測に先立つ、証跡・再現性の
基盤整備であり、配送実証や聴覚的同一性の主張ではない。

2026-07-17: `InputCapabilityProfile`（`input-capability/0.2`）に
`generator_variant`（必須・経路の識別子）/ `model_version` / `interface`
（任意・捏造禁止）を追加し、`PerformancePackage`（`0.2`）/
`CompilationReport`（`0.3`）へ転記する経路区別の語彙とスキーマを導入した
（詳細: [`cli.md`](cli.md) の `svprpe package` 節）。既存 2 profile
（suno / musicgen）はいずれも `generator_variant: "standard"` のまま —
remix/cover/reference-audio 等の変種 profile はリポジトリ内に実在する
committed evidence が無いため未追加であり、実測 profile の拡充ではない。
grip（`control_profile` / `DeviceProfile`）は本 PR の対象外のまま。

## 7. 非目標

- canonical `CompositionScore` への identity/capability/adherence 欄の追加
- hard policy を生成器出力保証とみなすこと
- unsupported artifact の prompt 文言への暗黙変換
- M1 fixture から聴覚的同一性やメロディ保持を主張すること
- 実測前に AR4 のセンサー、閾値、verdict を発明すること

## 8. 関連資料

- [`docs/composition_poc_planning.md`](composition_poc_planning.md) — C5 の上位計画
- [`docs/control_profile.md`](control_profile.md) — grip と backend compile の既存契約
- [`docs/roundtrip_preservation.md`](roundtrip_preservation.md) — Score 往復保存診断
- [`docs/lyrics_semantic_anchor.md`](lyrics_semantic_anchor.md) — 歌詞 anchor の観測上の限界
- [`AGENTS.md`](../AGENTS.md) §8 — Persistent Artifact Safety Gate
