# Arrangement Identity Track Planning

**Status**: AR0 計画文書。AR1、AR2-1/2、AR3-1 は実装済み。AR2-3 は保留、
AR3-2 と AR4 は未実装。

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
| Artifact delivery preservation | identity artifact と保持契約を生成器の実入力チャネルへ配送し、配送不能を明示状態として記録する | AR3-2 未実装 |
| Observed musical identity preservation | 生成後成果物を anchor ごとのセンサーと adherence 指標で比較し、観測結果を記録する | AR4 未実装 |

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
境界である。D7 の実装先は AR3-2 とし、現時点では計画上の必須契約である。

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
- **AR2-3 (deferred)**: structure anchor の stable ID と section policy。
  実 form artifact を用いる AR4 の結果が得られるまで保留する。

AR2 の完了は「何を残したいか」と「どの変形を許すか」を機械可読にしたことを意味し、
生成器へ渡せたことは意味しない。

### AR3: backend capability と artifact delivery

- **AR3-1 (#180, #181)**: `InputCapabilityProfile`。`style_prompt`、`lyrics_text`、
  `section_tags`、`reference_audio`、`symbolic_melody`、`midi` の support 状態を
  `supported` / `experimental` / `unsupported` / `unknown` で記録する。
- **AR3-2 (next)**: `PreservationContract` と capability profile から
  `PerformancePackage` と `CompilationReport` を構築する。anchor ごとに
  requested / deliverable / controllable / observed を分離し、strict/advisory と
  D7 の暗黙代替禁止を実装する。

Artifact delivery preservation の完了には、少なくとも 1 backend へ hard melody
artifact を実配送し、package と backend invocation の双方で同一 artifact/hash を
確認する縦切り E2E が必要である。

### AR4: 生成後の作品同一性観測

生成された音声または記号成果物を anchor ごとに比較し、adherence を観測する。
`requested=hard` や `delivery=supported` だけから `observed=preserved` を導出しない。
センサーが存在しない場合は `not_observed`、有効帯域外なら sensor limitation として
記録する。

最初の縦切りは 1 曲 × 1 編曲 × 1 生成器 × 実在 artifact 1 件とし、manifest、contract、
capability profile、performance package、生成物、observation report を同じ provenance
chain で結ぶ。聴覚的同一性の判定条件と閾値は、その artifact とセンサーの実測を得てから
別 Design Memo で固定する。

## 6. 実装状況

| Phase | 主成果物 | 状態 |
|---|---|---|
| AR0 | 本計画文書 | 完了 |
| AR1 | resolver / CLI / bundle / diff / EDM-Jazz fixture | 完了 (#175–#177) |
| AR2-1/2 | IdentityManifest / PreservationContract | 完了 (#178, #179, #181) |
| AR2-3 | structure anchor policy | 保留 |
| AR3-1 | InputCapabilityProfile | 完了 (#180, #181) |
| AR3-2 | PerformancePackage compiler | 未実装 |
| AR4 | generated-output identity observation | 未実装 |

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
