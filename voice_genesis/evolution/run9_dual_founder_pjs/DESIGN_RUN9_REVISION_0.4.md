# DESIGN RUN9 — Revision 0.4

- **裁定日:** 2026-08-25
- **裁定者:** User
- **design_revision:** 0.3 → 0.4
- **裁定ソース:** [`EXTERNAL_REVIEW_AQUEST_20260825.txt`](./EXTERNAL_REVIEW_AQUEST_20260825.txt)
  （「RUN9 外部指摘反映による変更点 / External Review: AQUEST 山崎信英氏 /
  Revision Proposal: RUN9 design_revision 0.2」、**byte-pin 不変**。
  sha256 = `a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091`
  — `RUN9_CONTRACT.yaml` の `external_review_aquest_sha256`
  相当欄は本改訂では新設しない。byte-pin の一次証跡は本ファイル自身の
  「系譜」節と `tests/test_run9_contract.py` の byte-pin テストが保持する）
  + 2026-08-25 User 追加裁定「確認メモ / RUN9 用語整理」（口頭/チャット
  裁定、原文ファイルなし — 本文書 §7 に逐語収載する）。

## 系譜（番号注記）

外部レビュー原文は自称 **「Revision Proposal: RUN9 design_revision 0.2」**
（原文4行目）だが、リポジトリ上は `design_revision` が既に 0.2 → 0.3 まで
発行・マージ済みである（[`DESIGN_RUN9_REVISION_0.2.md`](./DESIGN_RUN9_REVISION_0.2.md)
→ [`DESIGN_RUN9_REVISION_0.3.md`](./DESIGN_RUN9_REVISION_0.3.md)）。同じ番号を
外部レビューの内容で上書きすると、マージ済み rev 0.2/0.3 が固定した pin
（`design_doc_sha256` / `design_revision_doc_sha256` /
`backbone_checkpoint_sha` / `metric_space_sha` 等）の版管理上の意味が曖昧に
なる。したがって **本編入は rev 0.4 として発行する**（意味上は外部レビュー
の言う「0.2」に相当する内容である）。

これは [`DESIGN_RUN9_REVISION_0.3.md`](./DESIGN_RUN9_REVISION_0.3.md) 冒頭
「番号注記」が確立した前例（PoR メモ「RUN9 v0.2 PoR整理・設計裁定メモ」も
自称 "v0.2 design revision input" だったが rev 0.3 として発行した）と**同型
の系譜処理**である。マージ済み rev 0.2/0.3 文書は**無改変のまま存続**し、
その内容（ControlProfile 方式・三経路分離・書込境界・結果分類・機械的校正
の定義等）は、本 rev 0.4 と矛盾しない限りそのまま有効であり続ける。v0.1
本文・PoR メモ・外部レビュー原文もすべて同様に無改変・byte-pin 不変のまま。

旧 revision（"0.1"〜"0.3"）を宣言する contract は design_revision 0.4 以降
fail-closed で拒否される（`run9_schema.DESIGN_REVISION` の凍結値照合）—
これは意図どおりの拒否であり、実装バグではない。

## 変更種別

**NON-ARCHITECTURAL DESIGN CORRECTION**（外部レビュー原文「推奨正典変更」
節の逐語区分）。中核仮説は維持する。実験条件（Adapter architecture /
Backbone freeze / Genome freeze / Identity freeze / Lesson budget / 学習
回数 / 評価 metric / Pareto・Gate 条件）は**変更 7 のとおり不変**（外部
レビュー原文「変更7: RUN9本体の実験条件は変更しない」）。変更対象は
provenance / rights / terminology / Teacher・Voice・Performance の概念分離
のみ — 下記 §7 の User 追加裁定によりこの対象範囲はさらに絞られる
（terminology は「置換」ではなく「非所有注記の付加」へ緩和）。

## CASE A の適用

外部レビュー「変更8: 現RUN9の処理方針」が定める3分岐のうち、RUN9 は
**CASE A（まだ Lesson Freeze / 本学習開始前）** に該当する。RUN9 Phase 3
時点の現在地（`README.md` 実行順マップ）は step 0〜3 の部分 pin 段階に
留まり、`education_technique_lesson_manifest_sha` / `learning_recipe_sha`
はいずれも PENDING（Lesson Freeze 未実施）。CASE A の指示（「→
design_revision へ更新 → provenance schema を追加 → Rights Gate 再確認 →
RUN9 続行」）に従い、本改訂は provenance schema の追加（§3）・R9-G1 の
拡張（§4）を行った上で RUN9 を継続する。CASE B/C（本学習開始済み）の分岐
は適用しない。

## 中心問題の再定義

外部レビュー「RUN9への理論的影響」節の逐語再定義を rev 0.4 の中心問題として
採用する:

> 「Teacherの声を学習する」ではなく、**「権利来歴が明確な Performance
> Source から Identity 非依存の Performance Trait のみを抽出し、Target
> Voice Identity を保持したまま移送できるか」**。

外部指摘はさらに、v0.1/rev 0.3 が既に採用している「Voice Identity ≠
Performance」という分離だけでなく、

```
Voice Source ≠ Performance Source ≠ Performance Author
```

という三者分離が必要であることを明確化する。この三者分離は RUN9 の中核
仮説（H1〜H6、v0.1 §16、byte-pin 不変）を否定せず、その provenance
前提を精密化するものである。

---

## 変更1・2 — Teacher 概念の分解 + rights_manifest の4層分離

外部レビュー「変更1: Teacher概念を分解」「変更2: rights_manifestを4層へ
分離」を、[`inputs/rights_manifest.json`](./inputs/rights_manifest.json)
の再編として実装する。詳細は同ファイル自体を正とし、要点のみここに記す。

### 原則3式（外部レビュー原文、逐語）

```
Teacher ≠ Voice Identity Owner
Teacher ≠ Performance Author
Voice Source ≠ Performance Source
```

この3式は `inputs/rights_manifest.json` トップレベルの `principles` 欄に
逐語収載する。

### 4層構造への再編

`inputs/rights_manifest.json` を次の4層へ再編する（外部レビュー原文の
`rights_manifest:` 雛形を機械可読キーへ写す）:

| 外部レビューの層 | `rights_manifest.json` のキー | 内容 |
|---|---|---|
| `voice_identity_rights` | `voice_identity_rights` | Voice / VoiceBank / speaker identity の権利。**既存 User donor 17件（UC-001..017）・usage_grants・rights_class/consent_status/attestation を内容無改変のまま本層へ格納**（`schema: run9-user-donor-rights/1.0` の意味論は不変 — `verify_rights_manifest_against_ledger()` は本層の `entries`/`usage_grants` を従来どおり検証対象とする） |
| `performance_rights` | `performance_rights` | 歌唱・調声・演奏表現・UST 等の権利/許諾。新設 `performance_source`（`id: PJS`, `role: EXTERNAL_PERFORMANCE_SOURCE`）+ `provenance.performance_author` を格納 |
| `composition_rights` | `composition_rights` | 楽曲・旋律・歌詞等。`provenance.composition`（composer/lyricist）を格納 |
| `recording_master_rights` | `recording_master_rights` | 使用する生成音声・録音物自体の権利。`provenance.voice_source` + `provenance.synthesis` + ライセンス実測値（PJS corpus は CC BY-SA 4.0 — 出典は
  `voice_genesis/foundry/results_f1_2/licenses/pjs_terms_snapshot.md`）を格納 |

**Hard Gate**（外部レビュー原文、逐語）: 4層すべてについて provenance /
permission を確定する。

**禁止**（外部レビュー原文、逐語）: 「VoiceBank利用許諾 → Performance利用
許諾も得た」と自動的に解釈すること。この禁止文は `rights_manifest.json`
トップレベルの `auto_interpretation_prohibited` 欄に逐語収載する。

### provenance の実値充填（捏造禁止 — 出典 grep 結果のみ）

repo 内の PJS 記録を機械検証し、確認できた値のみ出典参照付きで
`rights_manifest.json` `performance_rights.provenance` /
`composition_rights.provenance` / `recording_master_rights.provenance` へ
充填した。不明な項目は `<PENDING_USER_ATTESTATION>` のまま残す（値の
捏造はしない）。

| provenance フィールド | 充填できた値 | 出典 | 状態 |
|---|---|---|---|
| `voice_source.owner` | PJS corpus（"phoneme-balanced Japanese singing voice corpus"）著作権者 = corpus 全体として CC BY-SA 4.0 licensed | `voice_genesis/foundry/results_f1_2/licenses/pjs_terms_snapshot.md`（論文 Koguchi & Takamichi, arXiv:2006.02959 の逐語引用） | 機械検証済み（法人/個人名の disclosure は原典に無し） |
| `voice_source.source_id` | `PJS_corpus_ver1.1` | `voice_genesis/foundry/s1_dataprep/README.md` 素材2 / `voice_genesis/foundry/adapter/presets/pjs_neutral.json` `donor.corpus_name` | 機械検証済み |
| `performance_author.performer` | PJS corpus の歌唱者個人名 | — | `<PENDING_USER_ATTESTATION>`（原典・repo いずれにも個人名の記載なし。歌唱と録音が単一の自然録音であり、UTAU 型の別途調声者は存在しないと推定されるが、この推定自体を pin 化はしない） |
| `performance_author.performance_editor` | 該当なし（自然録音、UTAU 型の別調声レイヤーなし） | `pjs_terms_snapshot.md`（論文は "singing voice corpus" と明記、synthesis 由来ではない） | 機械検証済み（`not_applicable` として明記。placeholder ではなく構造的に存在しない旨を区別する） |
| `synthesis.engine` / `synthesis.voicebank` | 該当なし（PJS は自然録音コーパスであり、VoiceGenesis 側の合成エンジン/voicebank の入力ではない — RUN9 が PJS から取得するのは Performance Trait のみで音声合成物ではない） | `pjs_terms_snapshot.md` | 機械検証済み（`not_applicable`） |
| `composition.composer` / `composition.lyricist` | — | — | `<PENDING_USER_ATTESTATION>`（PJS corpus のトラック別作曲者/作詞者クレジットは repo 内に記録なし） |
| ライセンス（`recording_master_rights.license`） | **CC BY-SA 4.0**（コーパス全体、研究・商用いずれも利用可。派生物の頒布・公開時は同一ライセンス継承 + attribution 必須） | `pjs_terms_snapshot.md`（論文 Abstract/Conclusion 逐語引用） | 機械検証済み |

`performance_rights.rights_class` / `consent_status` は、上記の未確定項目
（`performance_author.performer` の個人名）が残る間は `voice_identity_rights`
と同様 `PENDING_USER_ATTESTATION` とする。RUN9 が内部使用するのは PJS の
raw audio（PRACTICE 枝、rights-clean curriculum として v0.1 §13.2 が要求
する要件の一部）と、そこから抽出した Performance Trait（EDUCATION 枝）で
あり、ライセンス自体（CC BY-SA 4.0、研究利用可）は明確だが、**「歌唱者
個人の attest」という外部レビューが要求する主体特定は本改訂だけでは完結
しない** — 未解決のまま正直に PENDING とする（捏造禁止規律）。

### attest 対象の更新（a 裁定の反映）

`voice_identity_rights`（旧ファイル全体、User donor 17件）の attest 対象は
**4層構造への再編後も同一内容**であり、User attest は次段（`anchor_hashes.user`
の pin 実施時）で行う——本改訂は構造再編のみで、User donor 側の
`rights_class`/`consent_status`/`attestation.attested` の値そのものは
変更しない（依然 `PENDING_USER_ATTESTATION` / `false`）。これが User 裁定
「aとbを承認」の **a**（rights manifest attest は新4層構造に対して次段で
確定）の実装である。

---

## 変更3・6 — 「歌い方」の定義修正 + LessonRecord 標準仕様

外部レビュー「変更3: 『歌い方』の定義を修正」「変更6: LessonRecord標準仕様
へ追加」を `run9_schema.py` の run-local 定数として実装する。

### Performance Trait 語彙（変更3、9項目）

`run9_schema.PERFORMANCE_TRAIT_VOCAB`（外部レビュー原文の逐語9項目）:
`relative_F0` / `duration_ratio` / `onset_offset` / `energy_envelope` /
`vibrato` / `phrase_dynamics` / `attack_behavior` / `release_behavior` /
`articulation_timing`。

旧定義（「PJSの歌い方を移植する」）を新定義（「PJS由来Performance Source
から抽出した Performance Residual / Performance Trait を移植する」）へ
読み替える — v0.1 §11「PJS Performance Lesson」の Lesson 構成要素（F0_lesson
/ Duration_lesson / Energy_lesson / End_lesson 等、v0.1 本文・byte-pin
不変）は、この Performance Trait 語彙の RUN9 固有の初期実装例として引き続き
有効。

### Identity 除外 Trait 語彙（変更3+変更6の統合、7項目）

`run9_schema.IDENTITY_EXCLUDED_TRAIT_VOCAB` は、外部レビュー変更3の除外
6項目（speaker embedding / timbre identity / formant identity / spectral
identity / Voice Genome / source-specific identity representation）と
変更6の除外4項目（speaker_embedding / timbre_embedding / formant_profile /
identity_vector）を統合した正準リストである。重複概念（speaker embedding
系・timbre 系・formant 系の3組）は統一名 + 別名注記で吸収し、7項目へ収束
する:

| 正準名 | 吸収した別名 | 由来 |
|---|---|---|
| `speaker_embedding` | "speaker embedding"（変更3） | 変更3+変更6 |
| `timbre_identity` | "timbre identity"（変更3）、`timbre_embedding`（変更6） | 変更3+変更6 |
| `formant_identity` | "formant identity"（変更3）、`formant_profile`（変更6） | 変更3+変更6 |
| `spectral_identity` | "spectral identity" | 変更3のみ |
| `voice_genome` | "Voice Genome" | 変更3のみ |
| `source_specific_identity_representation` | "source-specific identity representation" | 変更3のみ |
| `identity_vector` | — | 変更6のみ（RUN9 の Identity 座標・genome coordinate 一般を指す汎用項目。変更3の個別表現群とは別軸） |

既存 `EDUCATION_FORBIDDEN_INPUTS`（rev 0.3、7項目: `pjs_speaker_embedding` /
`pjs_identity_coordinate` / `pjs_voice_quality_latent` /
`formant_inheritance_target` / `spectral_envelope_identity_replication` /
`founder_identity_replacement_parameter` /
`learner_pjs_raw_audio_direct_reference`）との関係を明記する: 両者は
**別の層**である。`IDENTITY_EXCLUDED_TRAIT_VOCAB` は「Performance Trait
として扱ってはならない特徴クラスの一般的な正準分類」（LessonRecord が
`explicitly_excluded_identity_traits` として宣言する対象）であり、
`EDUCATION_FORBIDDEN_INPUTS`/`PRACTICE_FORBIDDEN_INPUTS` は「RUN9・PJS
固有の、特定の入力チャネルとして渡してはならない具体的禁止項目の列挙」
である。前者は特徴の分類学、後者は運用上の入力境界—重なる概念
（speaker embedding・formant 系統）を持つが、片方を変更しても他方の
語彙は自動的に変わらない（別の凍結対象）。

### LessonRecord 標準仕様（変更6）

`run9_schema.py` に schema `run9-lesson-record/1.0`
（`SCHEMA_LESSON_RECORD`）+ `LESSON_RECORD_REQUIRED_KEYS` +
`validate_lesson_record()` を新設し、外部レビュー原文の LessonRecord
雛形（`lesson_id` / `performance_source` / `voice_source` /
`performance_author` / `composition_source` / `recording_source` /
`extracted_traits` / `explicitly_excluded_identity_traits` /
`rights_manifest` / `provenance_manifest`）を機械可読な最低要件へ写す。

- `extracted_traits` は `PERFORMANCE_TRAIT_VOCAB` の部分集合でなければ
  ならない。外部レビュー原文の変更6が使う5つの略記名
  （`relative_F0` / `duration` / `timing` / `dynamics` / `articulation`）
  は `run9_schema.LESSON_RECORD_TRAIT_ALIASES` により正準名へ解決する
  （`duration`→`duration_ratio`、`timing`→`onset_offset`、
  `dynamics`→`energy_envelope`、`articulation`→`articulation_timing`、
  `relative_F0`→`relative_F0` 恒等）。
- `explicitly_excluded_identity_traits` は `IDENTITY_EXCLUDED_TRAIT_VOCAB`
  の7項目を**完全含有**しなければならない（部分集合ではなく上位集合
  ——外部レビュー原文の4項目例示は最低限の一部であり、正準7項目すべてを
  満たすことを rev 0.4 で要求する）。
- manifest 実体の build（実際の Lesson 抽出処理）は VG-L0 学習ハーネス
  実装待ちの machine-dependent 作業であり、本改訂は validator + 閉集合 +
  型のみを事前配線する（既存 `validate_practice_split_manifest()`/
  `validate_education_lesson_manifest()` と同じ「骨組み凍結」パターン）。

---

## 変更5 — R9-G1 の拡張

v0.1 §19「R9-G1 INPUT_FREEZE_AND_RIGHTS」（byte-pin 不変、意味「AF0 /
Ritsu / User / PJS / Backbone / code / dataset / config / metric の hash
と権利来歴が揃う」）を、rev 0.2 が Adapter→ControlProfile へ行ったのと
同じ**読み替え方式**（v0.1 本文は一切書き換えない — §対応マップによる
写像）で拡張する。

- **意味名の追加**: `run9_schema.R9_G1_SEMANTIC_NAME =
  "RIGHTS_AND_PROVENANCE_GATE"`（外部レビュー原文の逐語）。
  `run9_schema.R9_G1_LEGACY_NAME = "INPUT_FREEZE_AND_RIGHTS"`
  （v0.1 §19 原文名、不変）と対で保持する — v0.1 側の gate ID（`R9-G1`）
  自体は変更しない、意味名の追加読み替えのみ。
- **PASS 条件8項目の凍結**: `run9_schema.R9_G1_PASS_CONDITIONS`
  （外部レビュー原文の逐語8項目を機械可読 id へ写した凍結 tuple）:
  `VOICE_SOURCE_IDENTIFIED` / `VOICE_USAGE_TERMS_CONFIRMED` /
  `PERFORMANCE_AUTHOR_IDENTIFIED` / `PERFORMANCE_USAGE_TERMS_CONFIRMED` /
  `COMPOSITION_RIGHTS_CONFIRMED` / `RECORDING_MASTER_RIGHTS_CONFIRMED` /
  `TEACHER_SOURCE_VS_VOICE_IDENTITY_SOURCE_DISTINGUISHED` /
  `NO_UNKNOWN_RIGHTS_HOLDER`。
- **FAIL 語彙の追加**: `run9_schema.GATE_FAIL_RIGHTS_PROVENANCE_UNRESOLVED
  = "RIGHTS_PROVENANCE_UNRESOLVED"`（外部レビュー原文の逐語）。既存
  `FAILURE_CLASSES`（3分類、rev 0.3 改訂E）とは別軸のゲート単位 FAIL
  値であり、`FAILURE_CLASSES` を置き換えない（gate 個別の FAIL vocabulary
  という性質が異なる — R9-G1 が不成立の attempt は、原因次第で
  `FAILURE_CLASSES` のいずれか（例: rights manifest が届いていない実装
  ミスなら `IMPLEMENTATION_FAILURE`、権利自体が構造的に確定不能なら
  `DESIGN_FAILURE`）にも分類され得る、独立した2層の語彙）。
- **境界宣言は不変**: PR #316 第4巡の層分離規約（gate は構造述語、pin
  値の実物照合は R9-G1 tooling の職務）を rev 0.4 でも維持する
  （`run9_schema.py` 3711行付近の既存コメント）。本改訂が追加する
  `r9_g1_pass_conditions_declared()` は、8条件 id の**宣言集合**が
  `R9_G1_PASS_CONDITIONS` を完全に満たすかどうかの構造判定のみを行い、
  各条件の実体照合（例えば「Voice Source が実際に特定されているか」の
  中身の正しさ）はこれまでどおり R9-G1 tooling（machine-dependent、
  VG-L0 実装時）の職務のまま変更しない。

---

## 変更4 — Common Performance Lesson

外部レビュー「変更4: Common Teacher Transfer名称の見直し」の新候補3案
（Common Performance Lesson / Common Performance Transfer / Performance
Residual Transfer）のうち、**Common Performance Lesson**（第1候補）を
採用する。

**理由**（User 裁定、逐語）: 教育枝の lesson manifest 経由という実態と
一致し、Transfer は既存枝名 `TRANSFER_TECHNIQUE`/`INHERIT_TRAIT` と衝突
しやすい。

**適用範囲**: v0.1 §14「H2 — Common Teacher Transfer」（byte-pin 不変、
hypothesis 見出しラベル）は書き換えない。rev 0.4 以降、RUN9 の可変
artifact・schema・ドキュメントが H2 に言及する際は「Common Performance
Lesson（旧称: v0.1 §14 の Common Teacher Transfer。名称のみの改訂——
仮説内容・機械判定条件は不変）」の形で旧名注記付きで参照する。現時点
（Phase 3、hypothesis_algebra_sha は PENDING）では H2 の機械可読な
実装対象自体がまだ存在しないため、本改訂はこの参照規約のみを確立し、
実装は `hypothesis_algebra_sha` の pin 化時（VG-L0 ハーネス実装後）に
本規約へ従う。

---

## 変更7・8 — 実験条件不変の確認 + CASE A 続行

§「変更種別」「CASE Aの適用」で既述のとおり、実験条件（Adapter
architecture / Backbone freeze / Genome freeze / Identity freeze /
Lesson budget / 学習回数 / 評価 metric / Pareto・Gate 条件）は本改訂で
一切変更しない。`run9_controlprofile.py` の書込境界機構
（`BRANCH_WRITABLE_PARTITIONS`）・`identity_metric_space.json` の
`calibration`（freeze_threshold/validity_gates/decision_rule）・
`RUN9_CONTRACT.yaml` の `interventions`（edges/control_branch/テイク数
pin）はいずれも無改訂。変更対象は provenance / rights / terminology /
概念分離のみ（§7 の User 追加裁定によりさらに terminology は「非所有
注記の付加」へ限定される）。

---

## User 裁定 a/b の記録（2026-08-25）

裁定原文: 「aとbを承認」（2026-08-25、本タスクの依頼メッセージに逐語
記載。原文ファイルなし——この一文自体が裁定の全文）。

- **a**: rights manifest attest は新4層構造に対して次段で確定する
  （§「変更1・2」「attest 対象の更新」節が実装）。
- **b**: `render_code_commit`（`inputs/backbone_runtime_bundle.json`、
  `openvpi/DiffSinger @ e2307b1080b00f3999702ce9017cfd75c7f862fe`）の
  status を `INFERRED_UNCONFIRMED` から **`USER_ATTESTED`** へ昇格する
  （User attestation による確定——Codex bot レビュー PR #316 第1巡指摘
  が要求していた「直接記録の発掘、または User attestation」のうち
  後者の経路）。これにより `RUN9_CONTRACT.yaml` の
  `backbone_runtime_bundle_sha` の pin 解禁条件（bundle 内 PENDING 解消）
  が満たされ、本改訂で **PENDING → PINNED** へ昇格する。実測手順・
  昇格後の値は `inputs/backbone_runtime_bundle.json` 自体と
  `RUN9_CONTRACT.yaml` の当該欄コメントを正とする。

**読み違いの veto 可能性**: この一文裁定の解釈（a/b の対応関係、b の
「User attestation」という解釈）が User の意図と異なっていた場合、PR
レビューで veto 可能——他の Fable 設計判定枠（例: rev 0.3 の
`metric_space_sha` pin）と同じ「マージ前に User が差し戻せる」運用を
本裁定の解釈にも適用する。

---

## §7 User 追加裁定（確認メモ / RUN9 用語整理、2026-08-25）

本改訂の実装着手後、User から以下の確認メモ（口頭/チャット裁定、原文
ファイルなし）が追加で入り、実装項目5（terminology 掃討）の指示が
緩和された。以下、裁定内容を逐語収載する。

> 【設計修正 — User 追加裁定 2026-08-25「確認メモ / RUN9 用語整理」による】
>
> 実装項目5（terminology 掃討）の指示を以下のとおり緩和・修正する。
> 他項目は不変。
>
> ## 用語整理（User 裁定、rev 0.4 doc へ逐語収載すること）
>
> - Voice Identity = 声そのものの個体性
> - Performance = 歌い方 / 歌唱表現（「歌い方」と「歌唱表現」の両語は
>   区別しない）
> - Performance Source = Performance を取得した音声・データ
> - Performance Author = その Performance を作成した主体
> - Performance Residual = Performance から抽出された RUN9 内部の
>   数値表現（F0, timing, duration, dynamics, vibrato 等）
> - **Teacher = RUN9 既存の運用上の呼称として維持可能。ただし「Voice
>   所有者」を意味しない**
>
> ## 指示の変更点
>
> 1. **teacher 語の全面置換はしない**。可変 artifact 中の「teacher」
>    出現は原則維持してよい。ただし各出現箇所（または各ファイルの定義
>    部1箇所）に「Teacher は運用上の呼称であり Voice 所有者・Voice
>    Identity Owner を意味しない（Voice Source ≠ Performance Source ≠
>    Performance Author の分離は rev 0.4 / rights_manifest provenance
>    を正とする）」旨の注記または定義参照が付くようにする
> 2. `RUN9_CONTRACT.yaml` の teacher_reference 相当欄: 置換でなく、
>    `performance_source` ブロック（id=PJS / role=EXTERNAL_PERFORMANCE_SOURCE）
>    を**追加**し、既存 teacher 表記には上記の非所有注記を付ける（既存
>    pin 値・構造の破壊を避ける）
> 3. `identity_metric_space.json` confuser_control の「teacher」文言:
>    削除せず維持してよいが、上記の非所有注記（または rights_manifest
>    provenance への参照）を role 節に追記する。文言追記による repin は
>    指示どおり実施
> 4. 「Common Teacher Transfer」→「Common Performance Lesson」の改名は
>    **維持**（外部レビュー変更4の明示推奨を User が採用済み。旧名注記付き）
> 5. validator に「teacher 語の再出現拒否」チェックを入れる予定だったなら
>    **入れない**。代わりに（可能な範囲で）「teacher 語を含む定義節に
>    非所有注記が存在する」ことの検証に置き換えるか、検証自体を見送る
> 6. rev 0.4 doc に本用語整理を「User 裁定（確認メモ）」として逐語で
>    収載し、変更1/4の適用がこの裁定で緩和された旨の系譜を記す

### 実装への反映

- **`RUN9_CONTRACT.yaml`**: teacher_reference 相当欄は元々存在しない
  （rev 0.3 で `interventions` 構造へ既に移行済み）。本裁定の指示2に
  従い、新設 `performance_source` ブロック（`id: PJS`,
  `role: EXTERNAL_PERFORMANCE_SOURCE`）を**追加**し、
  `teacher_terminology_note` 欄に非所有注記を格納する。
- **`inputs/identity_metric_space.json`**: `confuser_control.pjs_reference_definition`
  等の「PJS は teacher であり」を含む文言は**削除しない**。role 節へ
  非所有注記（Voice Source ≠ Performance Source ≠ Performance Author の
  分離は rev 0.4 / `rights_manifest.json` の provenance を正とする旨）を
  追記する。追記に伴う正規形 sha256 の repin は実施する
  （`metric_space_sha` の repin 履歴 — `domains/identity_domain_run9_v1.json`
  参照）。
- **`README.md` / 他の可変 doc**: 「teacher」の literal な出現は
  `verify donor and teacher rights / manifests`（v0.1 §22 step 2 の直接
  引用）等、いずれも frozen 文書の逐語引用であり、RUN9 独自の用語選択
  ではないため無改訂。「Common Teacher Transfer」の literal な出現は
  frozen 文書（v0.1 §14 見出しラベル・外部レビュー原文）のみであり、
  これも無改訂（§「変更4」の旧名注記付き参照規約が可変 artifact 側の
  対応）。
- **validator**: 「teacher 語の再出現拒否」チェックは実装しない（指示5）。
  代替として、`tests/test_run9_contract.py` に
  `identity_metric_space.json` が「teacher」という語と非所有注記の両方を
  含むことを確認する軽量テストを追加する（専用の run9_schema.py
  validator 関数としては実装しない——「検証自体を見送る」選択肢を、
  もっとも軽量な形で部分的に採用した）。

---

## 8変更 × repo artifact 写像表

| 外部レビューの変更 | 適用先 artifact | 本文書の節 |
|---|---|---|
| 変更1（Teacher概念の分解） | `inputs/rights_manifest.json`（`principles`/`performance_rights.provenance`）+ `RUN9_CONTRACT.yaml`（`performance_source`、§7 裁定により追加のみ） | §「変更1・2」§7 |
| 変更2（rights_manifest 4層分離） | `inputs/rights_manifest.json` 全体再編 | §「変更1・2」 |
| 変更3（「歌い方」定義修正） | `run9_schema.py` `PERFORMANCE_TRAIT_VOCAB`/`IDENTITY_EXCLUDED_TRAIT_VOCAB` | §「変更3・6」 |
| 変更4（Common Teacher Transfer 名称） | 参照規約のみ（v0.1 §14 は無改変、Common Performance Lesson を rev 0.4 以降の呼称として採用） | §「変更4」 |
| 変更5（Source Provenance Gate = R9-G1拡張） | `run9_schema.py` `R9_G1_SEMANTIC_NAME`/`R9_G1_PASS_CONDITIONS`/`GATE_FAIL_RIGHTS_PROVENANCE_UNRESOLVED` | §「変更5」 |
| 変更6（LessonRecord標準仕様） | `run9_schema.py` `SCHEMA_LESSON_RECORD`/`LESSON_RECORD_REQUIRED_KEYS`/`validate_lesson_record()` | §「変更3・6」 |
| 変更7（実験条件不変） | 無改訂の確認（`RUN9_CONTRACT.yaml` `interventions`・`identity_metric_space.json` `calibration`・`branch_write_policy.json` は無変更） | §「変更7・8」 |
| 変更8（CASE A 続行） | 本文書全体の適用方針 | §「CASE Aの適用」 |

## design_revision 系譜（byte-pin sha256 記録）

| revision | 文書 | sha256（実バイト） |
|---|---|---|
| v0.1（正本、無改変） | `DESIGN_RUN9_TRI_DONOR_DUAL_FOUNDER_PJS_LEARNING_v0.1.md` | `b1f6901c0ba8bcfcbd61170aa672c95e96a37d082fce5e3f12f245bc4faaae1e` |
| 0.2（無改変・存続） | `DESIGN_RUN9_REVISION_0.2.md` | `406098e2ac62065855b7e4086fce769a2956b64606594ad83b63b527a23ad4fb` |
| 0.3（無改変・存続） | `DESIGN_RUN9_REVISION_0.3.md` | `b4f05cfbccb484a16a39b736086e989e1c953f295bda66970d491e4db5b94b04` |
| PoR 裁定ソース（無改変・byte-pin） | `POR_CONCEPT_ADJUDICATION_20260824.txt` | `56b66fd8df943fbfa98767f2ea481c0ba2a68c26916832e08517379408d97007` |
| 外部レビュー原文（無改変・byte-pin） | `EXTERNAL_REVIEW_AQUEST_20260825.txt` | `a148b4410a7d741b404ada69a6e459679e8dcb01c876fd71ac116c3e0fffb091` |
| 0.4（本文書） | `DESIGN_RUN9_REVISION_0.4.md` | `RUN9_CONTRACT.yaml` の `design_revision_doc_sha256` が PINNED で保持する（本文書は本文書自身の sha256 を内部に書けないため実測は contract 側を正とする） |
