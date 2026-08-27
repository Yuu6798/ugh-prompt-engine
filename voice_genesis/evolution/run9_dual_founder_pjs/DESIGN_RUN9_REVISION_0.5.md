# DESIGN RUN9 — Revision 0.5

- **裁定日:** 2026-08-26
- **裁定者:** User
- **design_revision:** 0.4 → 0.5
- **裁定ソース:** [`USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt`](./USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt)
  （「RUN9 User裁定 — AF0 runtime mapping」、逐語・一字一句改変禁止。
  受領経路は口頭/チャット裁定 — session scratchpad
  `scratchpad/run9_user_adjudication_af0_mapping.md` へ Fable が記録した
  ものを repo 内収載した。同ファイルの実バイト sha256 は
  `RUN9_CONTRACT.yaml` へ情報記録として収載する——`design_doc_sha256`
  規約と同じファイル実バイト規約で、裁定文書自体は「値の転記元の証跡」
  であり RUN9 の実行前提条件そのものではないため
  `USER_ADJUDICATION_20260826_EXECUTION_PROFILE.txt` の裁定 txt sha256
  と同じ扱い）。

## 契約レベルの design_revision 昇格

裁定逐語「design_revisionを0.5へ上げ」に従い、本改訂は rev 0.2 → 0.3 →
0.4 の過去改訂と同じ手順で**契約レベルの `design_revision` を実際に
昇格する**——`RUN9_CONTRACT.yaml` トップレベル `design_revision` フィー
ルドを `"0.4"` → `"0.5"` へ、`run9_schema.DESIGN_REVISION` 定数を同じく
`"0.5"` へ、`design_revision_doc_sha256` pin を本文書
（`DESIGN_RUN9_REVISION_0.5.md`）の実バイト sha256 へ repoint する。
rev 0.4 文書（`DESIGN_RUN9_REVISION_0.4.md`）自体は無改変のまま存続し、
その sha256 は下記「系譜」節へ記録する——`design_doc_sha256`/
`design_revision_doc_sha256` の前例どおり、欄としては最新 revision の
1件のみを pin する規約は不変。旧 revision（"0.1"〜"0.4"）を宣言する
contract は design_revision 0.5 以降 fail-closed で拒否される
（`run9_schema.DESIGN_REVISION` の凍結値照合）。

〔経緯注記: 本改訂の初版は契約レベルの `design_revision` 昇格を「本 PR
のスコープ外」として据え置いていた（実装チェックリストが契約レベル昇格
を明示的に列挙していなかったため）。Fable によるレビューで、裁定逐語
「design_revisionを0.5へ上げ」は User 裁定の直接指示であり、指示書の
チェックリスト漏れが原因の据え置きは採用しないとの判定を受け、本改訂内
で契約レベル昇格を実施した。〕

## 変更種別

**NON-ARCHITECTURAL DESIGN CORRECTION**（rev 0.4 と同区分）。RUN9 の
中核仮説・実験条件（Adapter architecture / Backbone freeze / Genome
freeze / Identity freeze / Lesson budget / 学習回数 / 評価 metric /
Pareto・Gate 条件）は変更しない。変更対象は speaker map（RUN9 の3
Founder 座標と backbone speaker embedding との対応表）の runtime 合成
方式ただ1点に限られる。

## 1. 方式A（採用）

裁定逐語:

> 方式Aを採用する。
>
> RUN9の構造Genomeは従来どおり
> af0 / ritsu / user の三点Identity Domainを保持する。
>
> ただし現行RUN6 Backboneにはbyte-verifiedなAF0 speaker embeddingが
> 存在しないため、runtime renderでは実現可能な
> ritsu / user成分だけを再正規化して線形合成する。
>
> R9F-01:
>   ritsu = 0.75
>   user  = 0.25
>
> R9F-02:
>   ritsu = 1/3
>   user  = 2/3
>
> 合成はfloat32の単純加重和とし、L2正規化、摂動、
> ランダム成分、試聴後の重み調整を禁止する。

構造 Genome（`founders/R9F-0{1,2}_genome.json`、af0/ritsu/user 三点
coords）は不変のまま保持し、**runtime render 時にのみ** ritsu/user
成分を `w_ritsu = ritsu/(ritsu+user)`, `w_user = user/(ritsu+user)` へ
再正規化し、`synth = w_ritsu * ritsu_vec + w_user * user_vec` の単一
float32 加重和で speaker embedding を合成する。禁止事項は4点固定
（L2正規化・摂動・ランダム成分・試聴後の重み調整）——`inputs/speaker_
map_manifest.json` `synthesis_formula.prohibited` へ逐語収載し、
`run9_schema.validate_speaker_map_manifest()` が4件ちょうどであることを
machine 強制する。

## 2. AF0 成分の runtime 非実現と unrealized mass

裁定逐語:

> AF0成分は構造Genomeには存在するが、
> 現行runtimeでは音響的に実現されない。
> この事実とunrealized massをspeaker map manifestへ明記する。

`inputs/speaker_map_manifest.json` は `declaration_af0_not_realized`
（この事実の逐語宣言）と、両 founder の `unrealized_mass`（`coords_raw.
af0` の機械転記——R9F-01: 0.6, R9F-02: 0.1）を必須構造として持つ。
`validate_speaker_map_manifest()` は `unrealized_mass.value ==
coords_raw.af0` を fail-closed で強制する（af0 の質量が runtime 合成の
外側に「取り残されている」ことを数値としても正直に記録する——af0 の
未実現を隠蔽して ritsu/user 二成分だけが Genome の全てであるかのように
装う経路を閉じる）。

## 3. 非主張3点

裁定逐語:

> 本方式は三親音響交配の成立を意味しない。
> AF0音響形質の継承、AF0-dominant音声、
> AF0成分に起因する学習能力差を主張しない。

`declaration_af0_not_realized` は上記4文（三親音響交配の成立の否定 +
非主張3点）を逐語収載する。`validate_speaker_map_manifest()` は同フィー
ルドが「三親音響交配の成立を意味しない」「AF0音響形質の継承」
「AF0-dominant音声」「AF0成分に起因する学習能力差を主張しない」の4
マーカー文言をすべて含むことを machine 強制する——将来の repin でこの
非主張が欠落した宣言文へ差し替えられることを防ぐ。

## 4. 不変宣言

裁定逐語:

> design_revisionを0.5へ上げ、
> 発行済みFounder Genome、coords、genome_id、
> TRI_CROSSOVER/1.0は変更しない。

`inputs/speaker_map_manifest.json` `unchanged_per_adjudication` は
`["発行済み Founder Genome", "coords", "genome_id", "TRI_CROSSOVER/1.0"]`
の4項目ちょうどを収載する。実装面では `founders/R9F-0{1,2}_genome.json`
・`RUN9_CONTRACT.yaml` `founder_genome_shas`・TRI_CROSSOVER/1.0 関連
定数のいずれも本改訂で1byteも変更しない——`validate_speaker_map_
manifest()` の cross-check (b) が両 founder の `coords_raw` を
`load_pinned_founder_genome_document()` 経由で読んだ**発行済み** Genome
document の `coords` と厳密一致させることで、この不変宣言を消費時にも
機械強制する。

## 5. pin 前検証6点

裁定逐語:

> speaker map pin前に、
> 入力hash照合、384-dim float32有限性、
> 生成embeddingのbyte決定論、
> 二体embeddingの相異、
> smoke render成立、render replay決定論を検証する。
>
> smoke PASS後にspeaker map manifestのraw byte sha256を
> expected_speaker_map_shaへPINNEDする。

RUN9-L0-HARNESS-3a（本 PR）が実測した6点は
[`HARNESS3A_SPEAKER_MAP_RECORD.md`](./HARNESS3A_SPEAKER_MAP_RECORD.md)
が詳細を保持し、`inputs/speaker_map_manifest.json`
`pre_pin_verification_summary` へ全6点 `"PASS"` + `all_pass: true` として
収載済み。`validate_speaker_map_manifest()` は6点全てが逐語 `"PASS"` で
あることに加え、`byte_determinism_confirmed`/`render_replay_
determinism_confirmed`/`supply_route_verified` の個別フラグと run1/
run2 の実 sha256 一致を再計算照合する——`pre_pin_verification_summary`
だけを書き換えて PASS を騙る経路と、個別フラグだけを書き換える経路の
両方を閉じる。smoke PASS を受けて `expected_speaker_map_sha` を
`inputs/speaker_map_manifest.json` の raw byte sha256 で `RUN9_CONTRACT.
yaml` へ PINNED 化するのは本改訂の直接の成果物である。

## 6. Birth Identity Separation Gate は pin 後に別途実行

裁定逐語:

> その後、Birth Identity Separation Gateを別途実行する。
> 二体分離が成立しない場合はNOT_ESTABLISHEDとして凍結し、
> 同attempt内で重み変更または方式Bへの自動昇格を行わない。

**本 PR（RUN9-L0-HARNESS-3a）は speaker map manifest の pin までを実装
範囲とし、Birth Identity Separation Gate 自体の実行は含まない**——裁定
が「その後、...別途実行する」と明示的に時系列を分けているとおりの区切り
である。Gate 実行時に二体分離が成立しない場合は結果を `NOT_ESTABLISHED`
として凍結し、同一 attempt 内で speaker map の重み再調整（R9F-01/R9F-02
の ritsu/user 比率変更）や方式Bへの自動昇格を行ってはならない——これは
`inputs/speaker_map_manifest.json` `synthesis_formula.prohibited` の
「試聴後の重み調整」禁止と同じ規律を Gate 結果に対しても適用したもの
である。

## 7. 方式B・方式Cの扱い

裁定逐語:

> 方式Bは将来のAF0 acoustic realization用の別revision/別Runへ送る。
> 方式CはGenome座標の意味をrender層で失うため不採用とする。

方式B（AF0 acoustic realization を伴う三成分合成）は本 RUN9 attempt の
スコープ外——将来 AF0 speaker embedding が byte-verified で得られた
時点で、別の design_revision・別の Run として再検討する。方式C
（Genome 座標を render 層で別の意味へ再解釈する方式、詳細は Design Memo
検討時点の作業メモ参照）は Genome 座標の意味論を render 層で失わせる
ため不採用——本改訂ではこれ以上の詳細化を行わない（不採用の理由のみを
裁定逐語として凍結する）。

---

## design_revision 系譜（byte-pin sha256 記録）

| revision | 文書 | sha256（実バイト） |
|---|---|---|
| v0.1（正本、無改変） | `DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md` | `b1f6901c0ba8bcfcbd61170aa672c95e96a37d082fce5e3f12f245bc4faaae1e` |
| 0.2（無改変・存続） | `DESIGN_RUN9_REVISION_0.2.md` | `406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb` |
| 0.3（無改変・存続） | `DESIGN_RUN9_REVISION_0.3.md` | `b4f05cfbccb484a16a39b736086e989e1c953f295bda66970d491e4db5b94b04` |
| PoR 裁定ソース（無改変・byte-pin） | `POR_CONCEPT_ADJUDICATION_20260824.txt` | `56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007` |
| 派生設計変更メモ（無改変・byte-pin） | `DERIVED_DESIGN_CHANGES_FROM_EXTERNAL_FEEDBACK_20260825.txt` | `a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091` |
| 0.4（無改変・存続） | `DESIGN_RUN9_REVISION_0.4.md` | `7bfefcf61886062511c30df92c25e597b7a4a7745037514ed4655a623e38df07` |
| AF0 runtime mapping 裁定ソース（無改変・byte-pin） | `USER_ADJUDICATION_20260826_AF0_RUNTIME_MAPPING.txt` | `07d932da7d60e0e5abf3011040228d47e0b027514a5d0b6d2c165e71d6c65426` |
| 0.5（本文書、`design_revision_doc_sha256` が PINNED で保持する契約レベルの現行文書） | `DESIGN_RUN9_REVISION_0.5.md` | `RUN9_CONTRACT.yaml` の `design_revision_doc_sha256` が PINNED で保持する（本文書は本文書自身の sha256 を内部に書けないため実測は contract 側を正とする） |
